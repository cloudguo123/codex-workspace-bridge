"""Command-line entrypoint. Mutation is explicit; dry-run is the default."""
import argparse
import json
from pathlib import Path
import platform
import shutil
import sqlite3
import sys
import tomllib
from . import __version__
from .core import (Refused, inventory, make_plan, bundle, compare, restore, load,
                   save, digest, repair_git_links)
from . import profile

def doctor(home):
    h=Path(home).expanduser().resolve()
    config=h/'config.toml'
    names=[]; config_issue=None
    try:
        if config.is_file(): names=sorted(tomllib.loads(config.read_text()).get('mcp_servers',{}))
    except (OSError,ValueError): config_issue='config unreadable/invalid; contents not emitted'
    return {'version':__version__,'os':platform.system(),'machine':platform.machine(),'python':platform.python_version(),
            'git_available':shutil.which('git') is not None,'codex_home':str(h),
            'databases':{n:(h/n).is_file() for n in profile.DATABASES},
            'mcp_server_names':names,'config_issue':config_issue,
            'skill_names':sorted(p.parent.name for p in (h/'skills').glob('*/SKILL.md')),
            'plugin_directories':sorted(p.name for p in (h/'plugins').iterdir()) if (h/'plugins').is_dir() else [],
            'credential_values_emitted':False,
            'note':'Only MCP names are emitted. This is not a credential or plugin authentication migration.'}

def dashboard(inv_path,out):
    inv=load(inv_path)
    if inv.get('schema')!='workbridge.inventory/v1': raise Refused('Unsupported inventory')
    template=Path(__file__).with_name('dashboard.html').read_text()
    raw=json.dumps({'inventory':inv,'plan':make_plan(inv)},ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
    with Path(out).open('x',encoding='utf-8') as f: f.write(template.replace('/*PAYLOAD*/',raw))
    return {'dashboard':str(Path(out).resolve()),'network_requests':0}

def main(argv=None):
    p=argparse.ArgumentParser(prog='workbridge',description='Local workspace and Codex continuity migration. All bundles are private.')
    p.add_argument('--version',action='version',version=__version__)
    s=p.add_subparsers(dest='cmd',required=True)
    a=s.add_parser('doctor'); a.add_argument('--codex-home',default=str(Path.home()/'.codex'))
    a=s.add_parser('scan'); a.add_argument('--root',action='append',required=True); a.add_argument('--depth',type=int,default=1); a.add_argument('--out',required=True)
    a=s.add_parser('plan'); a.add_argument('inventory'); a.add_argument('--out',required=True)
    a=s.add_parser('dashboard'); a.add_argument('inventory'); a.add_argument('--out',required=True)
    a=s.add_parser('bundle'); a.add_argument('plan'); a.add_argument('--out',required=True)
    for command in ('diff','restore'):
        a=s.add_parser(command); a.add_argument('bundle'); a.add_argument('--ssd'); a.add_argument('--hdd'); a.add_argument('--expect'); a.add_argument('--out')
        if command=='restore': a.add_argument('--apply',action='store_true')
    a=s.add_parser('git-links'); a.add_argument('mapping'); a.add_argument('--apply',action='store_true')
    a=s.add_parser('profile-snapshot'); a.add_argument('--codex-home',required=True); a.add_argument('--out',required=True); a.add_argument('--app-stopped',action='store_true')
    a=s.add_parser('profile-prepare'); a.add_argument('snapshot'); a.add_argument('--target',required=True); a.add_argument('--out',required=True); a.add_argument('--mapping'); a.add_argument('--expect'); a.add_argument('--app-stopped',action='store_true')
    a=s.add_parser('profile-commit'); a.add_argument('stage'); a.add_argument('--expect',required=True); a.add_argument('--backup-root',required=True); a.add_argument('--app-stopped',action='store_true')
    a=s.add_parser('profile-rollback'); a.add_argument('stage'); a.add_argument('--app-stopped',action='store_true')
    args=p.parse_args(argv)
    try:
        if args.cmd=='doctor': result=doctor(args.codex_home)
        elif args.cmd=='scan':
            if not 0<=args.depth<=8: raise Refused('Depth must be 0..8')
            result=inventory(args.root,args.depth); save(args.out,result); result={'inventory':str(Path(args.out).resolve()),'projects':len(result['projects'])}
        elif args.cmd=='plan': save(args.out,make_plan(load(args.inventory))); result={'plan':str(Path(args.out).resolve())}
        elif args.cmd=='dashboard': result=dashboard(args.inventory,args.out)
        elif args.cmd=='bundle': result=bundle(load(args.plan),args.out)
        elif args.cmd in ('diff','restore'):
            result=restore(args.bundle,args.ssd,args.hdd,args.expect,args.apply) if args.cmd=='restore' else compare(args.bundle,args.ssd,args.hdd,args.expect)
            if args.out: save(args.out,result)
        elif args.cmd=='git-links': result=repair_git_links(load(args.mapping),args.apply)
        elif args.cmd=='profile-snapshot': result=profile.snapshot(args.codex_home,args.out,args.app_stopped)
        elif args.cmd=='profile-prepare': result=profile.prepare(args.snapshot,args.target,args.out,load(args.mapping) if args.mapping else None,args.app_stopped,args.expect)
        elif args.cmd=='profile-commit': result=profile.commit(args.stage,args.expect,args.backup_root,args.app_stopped)
        else: result=profile.rollback(args.stage,args.app_stopped)
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return 0
    except (Refused,OSError,ValueError,sqlite3.Error) as e:
        print(json.dumps({'status':'refused','reason':str(e),'source_deleted':False},ensure_ascii=False),file=sys.stderr)
        raise SystemExit(2)
