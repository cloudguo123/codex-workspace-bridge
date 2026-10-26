"""Offline, additive Codex profile adapter. Versioned local storage is experimental."""
from __future__ import annotations
from contextlib import contextmanager, closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import uuid
from .core import Refused, digest, load, save, newdir, tree, safe_target, nested_outputs, stable_file

DATABASES = {
    'state_5.sqlite': {'threads','thread_dynamic_tools','thread_spawn_edges','projects','project_roots','thread_sections','thread_attachments'},
    'thread_history_1.sqlite': {'thread_turns','thread_items','thread_history_projection_state','thread_realtime_items'},
    'goals_1.sqlite': {'thread_goals'},
}
DIRECTORIES = ('sessions','archived_sessions','generated_images')
UI_KEYS = {'local-projects','thread-project-assignments','app-server-project-id-by-legacy-project-id-by-host','project-order'}
PATH_KEYS = {'cwd','rollout_path','path','root','working_directory','image_path','file_path','image_url','local_path'}
STATE_PATH_COLUMNS = {'threads': {'cwd','rollout_path','agent_path'}, 'project_roots': {'path'}}
GOAL_COLUMNS = {'thread_id','goal_id','objective','status','token_budget','tokens_used','time_used_seconds','created_at_ms','updated_at_ms'}

def qi(s): return '"'+s.replace('"','""')+'"'

@contextmanager
def connect(path, mode='ro'):
    c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode='+mode,uri=True,timeout=5)
    c.execute('PRAGMA trusted_schema=OFF')
    try:
        yield c
    finally:
        c.close()

def schema(c):
    return c.execute("select type,name,sql from sqlite_schema where name not like 'sqlite_%' order by type,name").fetchall()

def check_db(c):
    if [x[0] for x in c.execute('pragma quick_check')]!=['ok']: raise Refused('SQLite integrity check failed')
    if any(x[0] in ('trigger','view') or 'VIRTUAL TABLE' in (x[2] or '').upper() for x in schema(c)):
        raise Refused('Unsupported SQLite triggers/views/virtual tables')

def snapshot_db(src,dst):
    with connect(src) as a, closing(sqlite3.connect(dst)) as b:
        a.backup(b); check_db(b)
    os.chmod(dst,0o600)

def cold_guard(home, attested):
    if not attested: raise Refused('Stop the target App/app-server first, then pass --app-stopped')
    if Path(home).resolve()==(Path.home()/'.codex').resolve():
        r=subprocess.run(['ps','-axo','command='],capture_output=True,text=True,check=True)
        if any(line.lstrip().startswith(('/Applications/ChatGPT.app/','/Applications/Codex.app/')) or ('app-server' in line and Path(line.strip().split()[0]).name=='codex') for line in r.stdout.splitlines() if line.strip()):
            raise Refused('The live App/app-server is running')

def relocate(value,mapping):
    if not isinstance(value,str): return value
    for a,b in sorted(mapping.items(),key=lambda x:-len(x[0])):
        if value==a or value.startswith(a+'/'): return b+value[len(a):]
    return value

def typed_json(value,mapping):
    if isinstance(value,list): return [typed_json(x,mapping) for x in value]
    if isinstance(value,dict):
        return {k:relocate(v,mapping) if k in PATH_KEYS and isinstance(v,str) else typed_json(v,mapping) for k,v in value.items()}
    return value  # Never replace prose, commands, prompts or model context strings.

def sanitize_db(path,name):
    with connect(path,'rw') as c:
        tables=[x[0] for x in c.execute("select name from sqlite_schema where type='table' and name not like 'sqlite_%'")]
        allowed=DATABASES[name] | {'_sqlx_migrations'}
        for t in tables:
            if t not in allowed: c.execute('delete from '+qi(t))
        if name=='goals_1.sqlite':
            cols={x[1] for x in c.execute('pragma table_info(thread_goals)')}
            if cols != GOAL_COLUMNS: raise Refused('Unsupported Goal schema')
            c.execute("update thread_goals set status='paused'")
        if name=='state_5.sqlite':
            cols={x[1] for x in c.execute('pragma table_info(threads)')}
            if not {'id','cwd','rollout_path'}<=cols: raise Refused('Unsupported thread schema')
            # Imported chats require fresh approvals. No inherited permission grants.
            if 'daybreak_enabled' in cols: c.execute('update threads set daybreak_enabled=0')
            if 'approval_mode' in cols: c.execute("update threads set approval_mode='untrusted'")
            if 'sandbox_policy' in cols: c.execute('update threads set sandbox_policy=?', ('{"type":"read-only"}',))
        check_db(c)
        c.commit()
        # Remove deleted enrollment/auth remnants from free pages of exported copies.
        c.execute('vacuum')


def snapshot(home,out,app_stopped=False):
    home=Path(home).expanduser().resolve(); nested_outputs(out,[home]); cold_guard(home,app_stopped)
    if home.is_symlink() or not home.is_dir(): raise Refused('Missing profile')
    dest=newdir(out); data=dest/'data'; data.mkdir(mode=0o700)
    original_goals=[]
    for name in DATABASES:
        src=home/name
        if not src.is_file() or src.is_symlink(): raise Refused('All three supported state/history/Goal stores are required: '+name)
        if name=='goals_1.sqlite':
            with connect(src) as c:
                c.row_factory=sqlite3.Row
                original_goals=[dict(x) for x in c.execute('select * from thread_goals')]
        snapshot_db(src,data/name); sanitize_db(data/name,name)
    for directory in DIRECTORIES:
        src=home/directory
        if not src.exists(): continue
        for row in tree(src):
            if row['kind']=='symlink': raise Refused('Profile links require manual review')
            target=data/directory/row['path']; target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            if row['kind']=='directory': target.mkdir(exist_ok=True,mode=0o700)
            else:
                sha,_=stable_file(src/row['path'])
                shutil.copyfile(src/row['path'],target); os.chmod(target,0o600)
                if digest(target)!=sha or stable_file(src/row['path'])[0]!=sha: raise Refused('Profile changed during cold snapshot')
    ui=home/'.codex-global-state.json'
    if ui.is_file() and not ui.is_symlink(): save(data/ui.name,{k:v for k,v in load(ui).items() if k in UI_KEYS})
    save(dest/'original-goals.json',original_goals)
    files={p.relative_to(dest).as_posix():digest(p) for p in dest.rglob('*') if p.is_file()}
    save(dest/'manifest.json',{'schema':'workbridge.profile/v1','source_home':str(home),'files':files,
        'excluded':['auth','remote enrollments','queues','logs','automations','MCP credentials','plugin credentials','other unlisted profile state'],
        'goals_held_paused':True,'permissions_reset':True,
        'limits':['External attachments/images and assets outside this profile are not bundled automatically.','Skills/plugins/configs require separate inventory and reinstall; no credentials are copied.','App storage adapter is community-maintained and not an official import API.']})
    return {'snapshot':str(dest),'manifest_sha256':digest(dest/'manifest.json'),'goal_count':len(original_goals)}

def allowed_profile_file(rel):
    return rel in DATABASES or rel == '.codex-global-state.json' or any(rel.startswith(d + '/') for d in DIRECTORIES)

def verify_snapshot(folder,expected=None):
    p=Path(folder).resolve(); m=load(p/'manifest.json')
    if expected and digest(p/'manifest.json')!=expected: raise Refused('Profile manifest changed')
    if m.get('schema')!='workbridge.profile/v1': raise Refused('Unsupported profile snapshot')
    for rel,sha in m['files'].items():
        if rel != 'original-goals.json' and (not rel.startswith('data/') or not allowed_profile_file(rel[5:])):
            raise Refused('Unexpected profile artifact')
        f=safe_target(p,rel)
        if f.is_symlink() or not f.is_file() or digest(f)!=sha: raise Refused('Profile payload corrupt: '+rel)
    required={'data/'+x for x in DATABASES}
    if not required<=set(m['files']): raise Refused('Incomplete profile store set')
    return p,m

def additive(a,b):
    if isinstance(a,dict) and isinstance(b,dict):
        result=dict(a)
        for k,v in b.items(): result[k]=additive(result[k],v) if k in result else v
        return result
    if isinstance(a,list) and isinstance(b,list): return a+[x for x in b if x not in a]
    if a!=b: raise Refused('Existing target UI value conflicts')
    return a

def row_value(row,table,name,mapping):
    d=dict(row)
    for k in STATE_PATH_COLUMNS.get(table,set()):
        if k in d: d[k]=relocate(d[k],mapping)
    for k in ('item_json','payload','metadata'):
        if isinstance(d.get(k),str):
            try:
                raw=json.loads(d[k]); changed=typed_json(raw,mapping)
                if changed!=raw: d[k]=json.dumps(changed,ensure_ascii=False,separators=(',',':'))
            except (ValueError,TypeError): pass
    if table=='thread_goals': d['status']='paused'
    return d

def merge_database(src,target,name,mapping):
    with connect(src) as a,connect(target,'rw') as b:
        check_db(a); check_db(b)
        if schema(a)!=schema(b): raise Refused('Schema mismatch: '+name)
        if a.execute("select name from sqlite_schema where name='_sqlx_migrations'").fetchone():
            if a.execute('select * from _sqlx_migrations order by version').fetchall()!=b.execute('select * from _sqlx_migrations order by version').fetchall():
                raise Refused('Migration history mismatch: '+name)
        a.row_factory=b.row_factory=sqlite3.Row
        stats={}
        for table in DATABASES[name]:
            cols=list(a.execute('pragma table_info('+qi(table)+')'))
            if not cols: continue
            names=[x[1] for x in cols]; pk=[x[1] for x in sorted(cols,key=lambda x:x[5]) if x[5]]
            if not pk: raise Refused('Unsupported table without primary key: '+table)
            query='insert into '+qi(table)+' ('+','.join(map(qi,names))+') values ('+','.join('?' for _ in names)+')'
            count=0
            for row in a.execute('select * from '+qi(table)):
                d=row_value(row,table,name,mapping)
                old=b.execute('select * from '+qi(table)+' where '+' and '.join(qi(k)+'=?' for k in pk),[d[k] for k in pk]).fetchone()
                if old is not None:
                    # Target is authoritative, including its Goal status/counters.
                    if dict(old)!=d: raise Refused('Existing target row conflict in '+table+'; prepare aborted')
                else:
                    b.execute(query,[d[k] for k in names]); count+=1
            stats[table]=count
        check_db(b)
        if b.execute('pragma foreign_key_check').fetchall(): raise Refused('Foreign key check failed')
        b.commit()
        return stats

def fingerprints(home):
    names=[*DATABASES,'.codex-global-state.json']
    for n in DATABASES: names += [n+'-wal',n+'-shm']
    return {n:digest(Path(home)/n) if (Path(home)/n).is_file() else None for n in names}

def prepare(snapshot_dir,target,out,mapping=None,app_stopped=False,expected=None):
    src,m=verify_snapshot(snapshot_dir,expected); target=Path(target).expanduser().resolve()
    cold_guard(target,app_stopped); nested_outputs(out,[src,target])
    if not target.is_dir() or target.is_symlink(): raise Refused('Target profile must exist')
    for name in DATABASES:
        if not (target/name).is_file() or (target/name).is_symlink(): raise Refused('Target store missing')
        wal=target/(name+'-wal')
        if wal.exists() and wal.stat().st_size: raise Refused('Target WAL is nonempty; cleanly stop/checkpoint with App tools first')
    stage=newdir(out); before=stage/'before'; ready=stage/'ready'; before.mkdir(mode=0o700); ready.mkdir(mode=0o700)
    before_fps=fingerprints(target)
    mapping=dict(mapping or {}); mapping[m['source_home']]=str(target)
    if any(not Path(a).is_absolute() or not Path(b).is_absolute() for a,b in mapping.items()): raise Refused('Mappings require absolute roots')
    stats={}
    for name in DATABASES:
        shutil.copyfile(target/name,before/name); shutil.copyfile(target/name,ready/name)
        os.chmod(before/name,0o600); os.chmod(ready/name,0o600)
        stats[name]=merge_database(src/'data'/name,ready/name,name,mapping)
    # Imported Goal bindings must exist in merged state, including child threads.
    with connect(ready/'state_5.sqlite') as state, connect(ready/'goals_1.sqlite') as goals:
        ids={x[0] for x in state.execute('select id from threads')}
        if any(x[0] not in ids for x in goals.execute('select thread_id from thread_goals')): raise Refused('Orphan Goal binding')
    ui='.codex-global-state.json'
    if (src/'data'/ui).exists():
        source_ui=typed_json(load(src/'data'/ui),mapping)
        # UI path keys are host/version dependent. No blanket home/text replacement.
        target_ui=load(target/ui) if (target/ui).is_file() else {}
        if (target/ui).exists(): shutil.copyfile(target/ui,before/ui); os.chmod(before/ui,0o600)
        save(ready/ui,additive(target_ui,source_ui))
    additions=[]
    for rel in m['files']:
        if not rel.startswith('data/'): continue
        rel=rel[5:]
        if rel in DATABASES or rel==ui: continue
        p=safe_target(target,rel); f=src/'data'/rel
        if p.exists() or p.is_symlink():
            if p.is_symlink() or not p.is_file() or digest(p)!=digest(f): raise Refused('Existing target file conflict: '+rel)
        else:
            dest=safe_target(ready,rel); dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700); shutil.copyfile(f,dest); os.chmod(dest,0o600)
            additions.append(rel)
    # Rollout references must resolve at commit. No dangling raw-context references.
    with connect(src/'data'/'state_5.sqlite') as original:
        imported_ids = {x[0] for x in original.execute('select id from threads')}
    with connect(ready/'state_5.sqlite') as state:
        for ident, cwd, path in state.execute('select id,cwd,rollout_path from threads'):
            if ident not in imported_ids: continue
            if not cwd or not Path(cwd).is_dir(): raise Refused('Project workspace missing; restore projects and provide a root mapping first')
            if not path: continue
            pp=Path(path)
            if not pp.is_absolute() or not pp.is_relative_to(target): raise Refused('Unmapped rollout path')
            if not pp.is_file() and not (ready/pp.relative_to(target)).is_file(): raise Refused('Missing rollout segment')
    if fingerprints(target)!=before_fps: raise Refused('Target changed during prepare')
    ready_files={p.relative_to(ready).as_posix():digest(p) for p in ready.rglob('*') if p.is_file()}
    save(stage/'plan.json',{'schema':'workbridge.profile-commit/v1','target':str(target),'before':before_fps,'ready':ready_files,'additions':additions,'database_additions':stats,'mapping':mapping,'gui_verified':False})
    return {'stage':str(stage),'plan_sha256':digest(stage/'plan.json'),'database_additions':stats,'status':'prepared_live_unchanged'}

def commit(stage,expected,backup_root,app_stopped=False):
    stage=Path(stage).resolve(); plan=load(stage/'plan.json'); target=Path(plan['target'])
    if not expected or digest(stage/'plan.json')!=expected: raise Refused('Commit requires the reviewed plan SHA256')
    if plan.get('schema')!='workbridge.profile-commit/v1': raise Refused('Unsupported plan')
    cold_guard(target,app_stopped)
    if fingerprints(target)!=plan['before']: raise Refused('Target drift; prepare again')
    # Prepared bytes are also pinned. Never trust writable staging blindly.
    for rel,sha in plan['ready'].items():
        if not allowed_profile_file(rel): raise Refused('Unexpected prepared file')
        p=safe_target(stage/'ready',rel)
        if p.is_symlink() or not p.is_file() or digest(p)!=sha: raise Refused('Prepared payload changed')
        if rel not in DATABASES and rel!='.codex-global-state.json' and rel not in plan['additions']: raise Refused('Unexpected replacement')
    for rel,sha in plan['before'].items():
        if sha is not None and rel in [*DATABASES, '.codex-global-state.json']:
            before_file = safe_target(stage/'before', rel)
            if not before_file.is_file() or before_file.is_symlink() or digest(before_file) != sha:
                raise Refused('Prepared backup before-image changed')
    nested_outputs(backup_root,[target,stage])
    backup=newdir(Path(backup_root)/('workbridge-'+uuid.uuid4().hex))
    # A second location is required; filesystem distinction is reported, not assumed physical independence.
    for p in (stage/'before').iterdir():
        q=backup/p.name; shutil.copyfile(p,q); os.chmod(q,0o600)
        if digest(q)!=digest(p): raise Refused('Backup readback failed')
    save(backup/'commit-plan.json',plan)
    save(stage/'commit-intent.json',{'plan_sha256':expected,'backup':str(backup),'target':str(target)})
    journal=[]
    try:
        for rel,sha in plan['ready'].items():
            p=safe_target(target,rel); src=stage/'ready'/rel
            replace=rel in DATABASES or rel=='.codex-global-state.json'
            if not replace and (p.exists() or p.is_symlink()): raise Refused('Target file appeared during commit')
            p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            # Journal before each write; recovery can identify before/after digests.
            item={'path':rel,'after':sha,'before':plan['before'].get(rel),'replacement':replace}
            save(stage/('write-'+str(len(journal))+'.json'),item); journal.append(item)
            if replace:
                temp=p.with_name(p.name+'.workbridge-'+uuid.uuid4().hex)
                shutil.copyfile(src,temp); os.chmod(temp,0o600); os.replace(temp,p)
            else:
                with src.open('rb') as a,p.open('xb') as b: shutil.copyfileobj(a,b)
                os.chmod(p,0o600)
            if digest(p)!=sha: raise Refused('Commit readback failed')
        # Empty cold sidecars only. Keep them in backups rather than carrying stale SHM.
        for name in DATABASES:
            for suffix in ('-wal','-shm'):
                p=target/(name+suffix)
                if p.exists():
                    if suffix=='-wal' and p.stat().st_size: raise Refused('A writer restarted during commit')
                    shutil.copyfile(p,backup/p.name); p.unlink()
        result={'status':'committed_gui_acceptance_required','backup':str(backup),'different_filesystems':backup.stat().st_dev!=target.stat().st_dev,'written':len(journal),'goals_started':0,'gui_verified':False}
        save(stage/'receipt.json',result); return result
    except BaseException:
        # No blind rollback over a user's concurrent edits. Keep a durable recovery journal.
        rollback(stage,app_stopped=app_stopped)
        raise

def rollback(stage,app_stopped=False):
    stage=Path(stage).resolve(); intent=load(stage/'commit-intent.json'); plan=load(stage/'plan.json'); target=Path(plan['target'])
    if digest(stage/'plan.json')!=intent['plan_sha256']: raise Refused('Recovery plan changed')
    cold_guard(target,app_stopped)
    writes=[load(p) for p in sorted(stage.glob('write-*.json'),key=lambda p:int(p.stem.split('-')[1]))]
    # Preflight all rows before changing any: edited data requires manual recovery.
    for item in writes:
        p=safe_target(target,item['path'])
        if p.is_symlink(): raise Refused('Recovery target became a link')
        actual=digest(p) if p.is_file() else None
        if actual not in (item['before'],item['after']): raise Refused('Target edited since commit; keep both copies for manual recovery')
        if item['before'] is not None:
            b=stage/'before'/item['path']
            if not b.is_file() or digest(b)!=item['before']: raise Refused('Recovery before-image changed')
    quarantine=stage/('recovered-after-'+uuid.uuid4().hex); quarantine.mkdir(mode=0o700)
    for item in reversed(writes):
        p=safe_target(target,item['path'])
        if not p.is_file() or digest(p)!=item['after']: continue
        q=quarantine/item['path']; q.parent.mkdir(parents=True,exist_ok=True,mode=0o700); os.replace(p,q)
        if item['before'] is not None: shutil.copyfile(stage/'before'/item['path'],p); os.chmod(p,0o600)
    return {'status':'rolled_back_after_files_retained','quarantine':str(quarantine)}
