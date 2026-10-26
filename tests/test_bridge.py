import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from workbridge import core, profile
from workbridge.cli import dashboard


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try: return super().__exit__(*args)
        finally: self.close()

def db(path): return sqlite3.connect(path, factory=ClosingConnection)

class Fixture(unittest.TestCase):
    def setUp(self): self.tmp=tempfile.TemporaryDirectory(); self.r=Path(self.tmp.name).resolve()
    def tearDown(self): self.tmp.cleanup()
    def project(self):
        p=self.r/'projects'/'sample';p.mkdir(parents=True);(p/'package.json').write_text('{}');(p/'a.txt').write_text('hello');(p/'.env').write_text('PRIVATE=synthetic')
        (p/'node_modules').mkdir();(p/'node_modules'/'skip').write_text('cache');return p
    def packaged(self):
        p=self.project();plan=core.make_plan(core.inventory([p],0));plan['projects'][0]['destination']='ssd'
        return p,core.bundle(plan,self.r/'bundle'),plan

class FilesTests(Fixture):
    def test_non_git_risk_and_size(self):
        p=self.project();i=core.inventory([p],0)['projects'][0];self.assertFalse(i['git']['present']);self.assertGreater(i['logical_bytes'],0);self.assertGreater(i['rebuildable_bytes'],0)
    def test_output_never_in_source(self):
        p=self.project();plan=core.make_plan(core.inventory([p],0));plan['projects'][0]['destination']='ssd'
        with self.assertRaises(core.Refused):core.bundle(plan,p/'out')
    def test_selection_required(self):
        with self.assertRaises(core.Refused):core.bundle(core.make_plan(core.inventory([self.project()],0)),self.r/'out')
    def test_roundtrip_and_idempotence(self):
        p,r,_=self.packaged();out=self.r/'target';res=core.restore(r['bundle'],out,expected=r['manifest_sha256'],apply=True);dest=Path(next(iter(res['targets'].values())))
        self.assertEqual((dest/'a.txt').read_text(),'hello');self.assertFalse((dest/'.env').exists());self.assertFalse((dest/'node_modules').exists())
        again=core.restore(r['bundle'],out,expected=r['manifest_sha256'],apply=True);self.assertTrue(all(x['status']=='same' for x in again['changes']))
    def test_conflict_preserves_entire_target(self):
        _,r,_=self.packaged();out=self.r/'target';res=core.restore(r['bundle'],out,expected=r['manifest_sha256'],apply=True);dest=Path(next(iter(res['targets'].values())))
        (dest/'a.txt').write_text('target edit');(dest/'package.json').unlink()
        with self.assertRaises(core.Refused):core.restore(r['bundle'],out,expected=r['manifest_sha256'],apply=True)
        self.assertEqual((dest/'a.txt').read_text(),'target edit');self.assertFalse((dest/'package.json').exists())
    def test_dry_run_no_target(self):
        _,r,_=self.packaged();core.restore(r['bundle'],self.r/'target');self.assertFalse((self.r/'target').exists())
    def test_manifest_pin(self):
        _,r,_=self.packaged()
        with self.assertRaises(core.Refused):core.restore(r['bundle'],self.r/'target',expected='0'*64,apply=True)
    def test_corrupt_blob_refused(self):
        _,r,_=self.packaged();next((Path(r['bundle'])/'blobs').iterdir()).write_text('bad')
        with self.assertRaises(core.Refused):core.compare(r['bundle'],self.r/'target')
    def test_traversal_refused(self):
        for p in ('../secret','/etc/passwd','a/../../b','a\\b','a//b','a/./b'):
            with self.assertRaises(core.Refused):core.safe_relative(p)
    def test_symlink_parent_refused(self):
        (self.r/'real').mkdir();os.symlink(self.r/'real',self.r/'link')
        with self.assertRaises(core.Refused):core.safe_target(self.r/'link','a')
    def test_link_not_followed(self):
        p=self.project();os.symlink('/not/copied',p/'link');rows=core.tree(p);self.assertEqual(next(x for x in rows if x['path']=='link')['target'],'/not/copied')
    def test_dashboard_injection_is_data(self):
        inv=core.inventory([self.project()],0);inv['projects'][0]['name']='</script><script>alert(1)</script>'
        core.save(self.r/'inv.json',inv);dashboard(self.r/'inv.json',self.r/'dashboard.html');html=(self.r/'dashboard.html').read_text()
        self.assertNotIn('</script><script>alert',html);self.assertIn('\\u003c/script',html)
    def test_source_drift_refused(self):
        p=self.project();plan=core.make_plan(core.inventory([p],0));plan['projects'][0]['destination']='ssd';real=core.git_info;calls=[]
        def changed(root):
            calls.append(1)
            if len(calls)>1:(p/'new').write_text('race')
            return real(root)
        with patch.object(core,'git_info',side_effect=changed):
            with self.assertRaises(core.Refused):core.bundle(plan,self.r/'out')
    def test_bundle_link_parent_refused(self):
        _,r,_=self.packaged();p=Path(r['bundle'])/'manifest.json';m=core.load(p)
        m['units'][0]['entries'] += [{'path':'link','kind':'symlink','mode':511,'mtime_ns':0,'target':'/tmp'}, {'path':'link/file','kind':'directory','mode':448,'mtime_ns':0}]
        p.write_text(json.dumps(m))
        with self.assertRaises(core.Refused):core.compare(r['bundle'],self.r/'target')
    @unittest.skipUnless(shutil.which('git'),'git needed')
    def test_git_status_does_not_execute_filters(self):
        p=self.project();subprocess.run(['git','init',str(p)],capture_output=True,check=True)
        subprocess.run(['git','-C',str(p),'add','a.txt'],check=True)
        subprocess.run(['git','-C',str(p),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','fixture'],check=True,capture_output=True)
        marker=self.r/'FILTER_EXECUTED';(p/'.gitattributes').write_text('a.txt filter=probe\n')
        subprocess.run(['git','-C',str(p),'config','filter.probe.clean','touch '+str(marker)],check=True)
        (p/'a.txt').write_text('changed');core.git_info(p);self.assertFalse(marker.exists())
    @unittest.skipUnless(shutil.which('git'),'git needed')
    def test_git_unborn_and_linked_worktree(self):
        p=self.r/'main';p.mkdir();subprocess.run(['git','init',str(p)],check=True,capture_output=True);self.assertTrue(core.git_info(p)['unborn']);(p/'a').write_text('tracked')
        subprocess.run(['git','-C',str(p),'add','.'],check=True);subprocess.run(['git','-C',str(p),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-m','fixture'],check=True,capture_output=True)
        wt=self.r/'linked';subprocess.run(['git','-C',str(p),'worktree','add','-b','feature',str(wt)],check=True,capture_output=True)
        plan=core.make_plan(core.inventory([p,wt],0))
        for x in plan['projects']:x['destination']='ssd'
        b=core.bundle(plan,self.r/'pack');res=core.restore(b['bundle'],self.r/'ssd',expected=b['manifest_sha256'],apply=True);mapping=res['git_link_map'];core.repair_git_links(mapping,True)
        for dest in mapping.values():self.assertEqual(core.git_info(Path(dest))['head'],core.git_info(p)['head'])

# Synthetic schemas only; never use user databases in tests.
def make_profile(p,ident='source',goals=True):
    p.mkdir(parents=True)
    (p.parent/'project').mkdir(exist_ok=True)
    with db(p/'state_5.sqlite') as c:
        c.executescript('create table threads(id text primary key,cwd text,rollout_path text,approval_mode text,sandbox_policy text,daybreak_enabled integer);create table remote_control_enrollments(token text primary key);')
        c.execute('insert into threads values(?,?,?,?,?,?)',(ident,str(p.parent/'project'),str(p/'sessions'/f'{ident}.jsonl'),'never','{"type":"danger-full-access"}',1));c.execute("insert into remote_control_enrollments values('SYNTHETIC_ENROLLMENT_SECRET')")
    with db(p/'thread_history_1.sqlite') as c:
        c.execute('create table thread_items(thread_id text,turn_id text,item_id text,item_json text,primary key(thread_id,turn_id,item_id))');c.execute('insert into thread_items values(?,?,?,?)',(ident,'turn','item',json.dumps({'text':'Original command /old/home stays unchanged'})))
    with db(p/'goals_1.sqlite') as c:
        c.executescript('create table thread_goals(thread_id text primary key,goal_id text,objective text,status text,token_budget integer,tokens_used integer,time_used_seconds integer,created_at_ms integer,updated_at_ms integer);create table thread_goal_continuation_deferrals(thread_id text primary key);')
        if goals:c.execute('insert into thread_goals values(?,?,?,?,?,?,?,?,?)',(ident,'goal-'+ident,'Synthetic objective','active',10000,1234,456,1,2))
    (p/'sessions').mkdir();(p/'sessions'/f'{ident}.jsonl').write_text('{"text":"synthetic rollout"}\n');(p/'auth.json').write_text('NEVER_EXPORT_AUTH');(p/'queue_1.sqlite').write_text('DO_NOT_EXPORT_QUEUE');return p

class ProfileTests(Fixture):
    def setup_stage(self):
        src=make_profile(self.r/'source');target=make_profile(self.r/'target','existing');snap=profile.snapshot(src,self.r/'snapshot',True);stage=profile.prepare(snap['snapshot'],target,self.r/'stage',app_stopped=True,expected=snap['manifest_sha256']);return src,target,snap,stage
    def test_snapshot_excludes_auth_queue_enrollment(self):
        snap=profile.snapshot(make_profile(self.r/'source'),self.r/'snapshot',True);p=Path(snap['snapshot']);self.assertFalse((p/'data'/'auth.json').exists());self.assertFalse((p/'data'/'queue_1.sqlite').exists())
        with db(p/'data'/'state_5.sqlite') as c:self.assertEqual(c.execute('select count(*) from remote_control_enrollments').fetchone()[0],0)
        self.assertNotIn(b'SYNTHETIC_ENROLLMENT_SECRET',(p/'data'/'state_5.sqlite').read_bytes())
    def test_original_goal_preserved_restore_paused(self):
        src=make_profile(self.r/'source');snap=profile.snapshot(src,self.r/'snapshot',True);p=Path(snap['snapshot']);self.assertEqual(core.load(p/'original-goals.json')[0]['status'],'active')
        with db(p/'data'/'goals_1.sqlite') as c:self.assertEqual(c.execute('select status,tokens_used,time_used_seconds from thread_goals').fetchone(),('paused',1234,456))
        with db(src/'goals_1.sqlite') as c:self.assertEqual(c.execute('select status from thread_goals').fetchone()[0],'active')
    def test_requires_cold_attestation(self):
        with self.assertRaises(core.Refused):profile.snapshot(make_profile(self.r/'source'),self.r/'out')
    def test_prepare_commit_and_rollback(self):
        _,target,_,s=self.setup_stage();before=(target/'state_5.sqlite').read_bytes();self.assertFalse((target/'sessions'/'source.jsonl').exists());receipt=profile.commit(s['stage'],s['plan_sha256'],self.r/'backup',True);self.assertEqual(receipt['goals_started'],0)
        with db(target/'goals_1.sqlite') as c:self.assertEqual(dict(c.execute('select thread_id,status from thread_goals')),{'existing':'active','source':'paused'})
        with db(target/'state_5.sqlite') as c:
            self.assertEqual(c.execute('select token from remote_control_enrollments').fetchone()[0],'SYNTHETIC_ENROLLMENT_SECRET');self.assertEqual(c.execute("select approval_mode,daybreak_enabled from threads where id='source'").fetchone(),('untrusted',0))
        profile.rollback(s['stage'],True);self.assertEqual((target/'state_5.sqlite').read_bytes(),before);self.assertFalse((target/'sessions'/'source.jsonl').exists())
    def test_staging_tamper_refused(self):
        _,target,_,s=self.setup_stage();before=profile.fingerprints(target);(Path(s['stage'])/'ready'/'sessions'/'source.jsonl').write_text('tampered')
        with self.assertRaises(core.Refused):profile.commit(s['stage'],s['plan_sha256'],self.r/'backup',True)
        self.assertEqual(profile.fingerprints(target),before)
    def test_before_image_tamper_refused(self):
        _,target,_,s=self.setup_stage();before=profile.fingerprints(target);(Path(s['stage'])/'before'/'state_5.sqlite').write_text('bad backup')
        with self.assertRaises(core.Refused):profile.commit(s['stage'],s['plan_sha256'],self.r/'backup',True)
        self.assertEqual(profile.fingerprints(target),before)
    def test_snapshot_auth_injection_refused(self):
        src=make_profile(self.r/'source');snap=profile.snapshot(src,self.r/'snap',True);p=Path(snap['snapshot']);(p/'data'/'auth.json').write_text('fake')
        m=core.load(p/'manifest.json');m['files']['data/auth.json']=core.digest(p/'data'/'auth.json');(p/'manifest.json').write_text(json.dumps(m))
        with self.assertRaises(core.Refused):profile.verify_snapshot(p)
    def test_target_drift_refused(self):
        _,target,_,s=self.setup_stage();(target/'state_5.sqlite').write_bytes((target/'state_5.sqlite').read_bytes()+b'changed')
        with self.assertRaises(core.Refused):profile.commit(s['stage'],s['plan_sha256'],self.r/'backup',True)
    def test_existing_row_conflict_no_target_write(self):
        src=make_profile(self.r/'source');target=make_profile(self.r/'target','source');before=profile.fingerprints(target);snap=profile.snapshot(src,self.r/'snap',True)
        with self.assertRaises(core.Refused):profile.prepare(snap['snapshot'],target,self.r/'stage',app_stopped=True)
        self.assertEqual(profile.fingerprints(target),before)
    def test_schema_mismatch(self):
        src=make_profile(self.r/'source');target=make_profile(self.r/'target','existing')
        with db(target/'goals_1.sqlite') as c:c.execute('alter table thread_goals add column unknown text')
        snap=profile.snapshot(src,self.r/'snap',True)
        with self.assertRaises(core.Refused):profile.prepare(snap['snapshot'],target,self.r/'stage',app_stopped=True)
    def test_orphan_goal(self):
        src=make_profile(self.r/'source');target=make_profile(self.r/'target','existing')
        with db(src/'goals_1.sqlite') as c:c.execute("update thread_goals set thread_id='missing'")
        snap=profile.snapshot(src,self.r/'snap',True)
        with self.assertRaises(core.Refused):profile.prepare(snap['snapshot'],target,self.r/'stage',app_stopped=True)
    def test_missing_rollout(self):
        src=make_profile(self.r/'source');target=make_profile(self.r/'target','existing');(src/'sessions'/'source.jsonl').unlink();snap=profile.snapshot(src,self.r/'snap',True)
        with self.assertRaises(core.Refused):profile.prepare(snap['snapshot'],target,self.r/'stage',app_stopped=True)
    def test_preserve_prose(self):
        item={'cwd':'/old/home/project','text':'run /old/home/file','nested':{'image_path':'/old/home/a.png'}};mapped=profile.typed_json(item,{'/old/home':'/new/home'});self.assertEqual(mapped['text'],item['text']);self.assertEqual(mapped['cwd'],'/new/home/project')
    def test_rollback_preserves_post_commit_edit(self):
        _,target,_,s=self.setup_stage();profile.commit(s['stage'],s['plan_sha256'],self.r/'backup',True);(target/'sessions'/'source.jsonl').write_text('user edit')
        with self.assertRaises(core.Refused):profile.rollback(s['stage'],True)
        self.assertEqual((target/'sessions'/'source.jsonl').read_text(),'user edit')
    def test_injected_failure_retains_recovery_journal(self):
        _,target,_,s=self.setup_stage();before=(target/'state_5.sqlite').read_bytes();real=profile.digest
        def fail(p):
            if Path(p)==target/'goals_1.sqlite' and (Path(s['stage'])/'write-1.json').exists():raise OSError('synthetic disk error')
            return real(p)
        with patch.object(profile,'digest',side_effect=fail):
            with self.assertRaises(OSError):profile.commit(s['stage'],s['plan_sha256'],self.r/'backup',True)
        self.assertTrue((Path(s['stage'])/'commit-intent.json').exists());profile.rollback(s['stage'],True);self.assertEqual((target/'state_5.sqlite').read_bytes(),before)

if __name__=='__main__':unittest.main()
