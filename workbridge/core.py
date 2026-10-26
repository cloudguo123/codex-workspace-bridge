"""Deterministic local filesystem operations. No shell, network or source writes."""
from __future__ import annotations
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import stat
import subprocess
import unicodedata
import uuid

class Refused(RuntimeError):
    pass

REBUILDABLE = {"node_modules", ".venv", "venv", "__pycache__", ".next", ".cache", "target"}
SECRET_NAMES = {"auth.json", ".env", "id_rsa", "id_ed25519", "credentials.json"}
MARKERS = {"pyproject.toml", "package.json", "Cargo.toml", "go.mod", "requirements.txt", ".git"}

def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def save(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Artifacts are write-once. A repeated command needs a new destination.
    with p.open("x", encoding="utf-8") as f:
        os.chmod(p, 0o600)
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())

def newdir(path):
    p = Path(path).expanduser().absolute()
    p.mkdir(parents=True, mode=0o700, exist_ok=False)
    return p.resolve()

def safe_relative(value):
    p = PurePosixPath(value)
    if not value or p.is_absolute() or ".." in p.parts or "\\" in value or "\0" in value or str(p) != value:
        raise Refused(f"Unsafe relative path: {value!r}")
    return p

def under(path, root):
    return Path(path).is_relative_to(Path(root))

def nested_outputs(output, roots):
    for root in roots:
        if under(Path(output).resolve(), Path(root).resolve()):
            raise Refused("Output must be outside every source root")

def stable_file(path):
    p = Path(path)
    before = p.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise Refused(f"File type changed: {p}")
    sha = digest(p)
    after = p.lstat()
    attrs = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, a) != getattr(after, a) for a in attrs):
        raise Refused(f"File changed during hashing: {p}")
    return sha, after

def tree(root, exclude=()):
    """No symlink traversal; detect Unicode/case collisions for default macOS disks."""
    root = Path(root)
    if root.is_symlink() or not root.is_dir():
        raise Refused(f"A root must be a real directory: {root}")
    rows, seen = [], set()
    def walk(folder):
        for p in sorted(folder.iterdir()):
            rel = p.relative_to(root).as_posix()
            if any(fnmatch.fnmatch(rel, x) or any(fnmatch.fnmatch(a, x) for a in p.relative_to(root).parts) for x in exclude):
                continue
            key = unicodedata.normalize("NFC", rel).casefold()
            if key in seen:
                raise Refused(f"Case/Unicode path collision: {rel}")
            seen.add(key)
            st = p.lstat()
            row = {"path": rel, "mode": stat.S_IMODE(st.st_mode) & 0o777, "mtime_ns": st.st_mtime_ns}
            if stat.S_ISLNK(st.st_mode):
                row.update(kind="symlink", target=os.readlink(p))
            elif stat.S_ISDIR(st.st_mode):
                row.update(kind="directory")
                rows.append(row)
                walk(p)
                continue
            elif stat.S_ISREG(st.st_mode):
                row.update(kind="file", size=st.st_size)
            else:
                raise Refused(f"Unsupported special file: {p}")
            rows.append(row)
    walk(root)
    return rows

def git(root, *args):
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0", GIT_TERMINAL_PROMPT="0")
    try:
        prefix = ["git", "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false", "-C", str(root)]
        options = []
        if args and args[0] == "status":
            # Git status can invoke repository-configured clean/process filters.
            configured = subprocess.run(prefix + ["config", "--name-only", "--get-regexp", r"^filter\..*\.(clean|smudge|process|required)$"], env=env, capture_output=True, text=True, timeout=10)
            drivers = {key.rsplit('.', 1)[0] for key in configured.stdout.splitlines()}
            for driver in drivers:
                for suffix in ("clean", "smudge", "process"):
                    options += ["-c", driver + "." + suffix + "="]
                options += ["-c", driver + ".required=false"]
            args = (*args, "--ignore-submodules=all")
        r = subprocess.run(prefix + options + list(args), env=env,
                           capture_output=True, text=True, timeout=45)
        return r.stdout.rstrip() if r.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None

def redact_remote(text):
    # Retain host/repository, remove URL userinfo, query strings and fragments.
    return re.sub(r"([a-zA-Z][a-zA-Z0-9+.-]*://)[^/@\s]+@", r"\1[redacted]@", text).split("?")[0].split("#")[0]

def git_info(root):
    if not (Path(root) / ".git").exists():
        return {"present": False, "risk": "no-local-version-history"}
    common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    head = git(root, "rev-parse", "--verify", "HEAD")
    return {"present": True, "head": head, "unborn": head is None,
            "branch": git(root, "symbolic-ref", "--short", "-q", "HEAD"),
            "status": git(root, "status", "--porcelain=v1", "--untracked-files=all"),
            "refs": git(root, "for-each-ref", "--format=%(refname) %(objectname) %(upstream)"),
            "remotes": redact_remote(git(root, "remote", "-v") or ""),
            "common_dir": common, "worktrees": git(root, "worktree", "list", "--porcelain"),
            "remote_verified": False}

def discover(roots, max_depth):
    found = set()
    def visit(p, depth):
        if p.is_symlink() or not p.is_dir():
            return
        names = {x.name for x in p.iterdir()}
        if names & MARKERS:
            found.add(p.resolve())
            # Linked/nested projects are still independently discoverable.
        elif depth == max_depth:
            found.add(p.resolve())
        if depth < max_depth:
            for x in sorted(p.iterdir()):
                if x.name not in REBUILDABLE | {".git", ".codex", ".ssh"}:
                    visit(x, depth + 1)
    for root in roots:
        visit(Path(root).expanduser().resolve(), 0)
    # If a root is itself a project, nested content belongs to that unit.
    return [p for p in sorted(found) if not any(p != q and under(p, q) for q in found)]

def inventory(roots, max_depth=1):
    projects = []
    for p in discover(roots, max_depth):
        try:
            rows = tree(p)
            files = [x for x in rows if x["kind"] == "file"]
            physical = sum((p / x["path"]).lstat().st_blocks * 512 for x in files)
            size = sum(x["size"] for x in files)
            caches = sum(x["size"] for x in files if any(a in REBUILDABLE for a in PurePosixPath(x["path"]).parts))
            info = git_info(p)
            projects.append({"id": hashlib.sha256(str(p).encode()).hexdigest()[:16], "name": p.name,
                             "source": str(p), "logical_bytes": size, "allocated_bytes_sum": physical,
                             "rebuildable_bytes": caches, "file_count": len(files), "git": info,
                             "runtime_manifests": sorted(x.name for x in p.iterdir() if x.name in MARKERS - {".git"}),
                             "issues": [], "suggestion": "ssd"})
        except (OSError, Refused) as e:
            projects.append({"id": hashlib.sha256(str(p).encode()).hexdigest()[:16], "name": p.name, "source": str(p), "issues": [str(e)], "suggestion": "review"})
    return {"schema": "workbridge.inventory/v1", "host": platform.node(), "os": platform.system(),
            "projects": projects, "notes": ["Allocated byte sums double-count hardlinks and APFS clones.", "Git remotes are configuration, not proof of an uploaded backup.", "No project scripts, hooks, package managers or network requests were run."]}

def make_plan(inv):
    return {"schema": "workbridge.plan/v1", "projects": [dict(id=p["id"], name=p["name"], source=p["source"], destination="review", logical_bytes=p.get("logical_bytes", 0), git=p.get("git", {})) for p in inv["projects"]],
            "exclude": sorted(REBUILDABLE | SECRET_NAMES | {".env.*"}), "include_secrets": False}

def bundle(plan, out):
    if plan.get("schema") != "workbridge.plan/v1":
        raise Refused("Unsupported plan")
    selected = [p for p in plan["projects"] if p["destination"] in ("ssd", "hdd")]
    if not selected:
        raise Refused("Select projects for SSD/HDD first")
    roots = [Path(p["source"]).expanduser().resolve() for p in selected]
    if len(set(p["id"] for p in selected)) != len(selected) or any(a != b and under(a, b) for a in roots for b in roots):
        raise Refused("Duplicate or overlapping project units")
    nested_outputs(out, roots)
    for p, root in zip(selected, roots):
        safe_relative(p["id"])
        common = git(root, "rev-parse", "--path-format=absolute", "--git-common-dir") if (root / ".git").exists() else None
        if common and not any(under(Path(common).resolve(), r) for r in roots):
            raise Refused("Linked worktree common Git directory must belong to a selected project")
    dest = newdir(out)
    (dest / "blobs").mkdir(mode=0o700)
    exclude = list(plan.get("exclude", []))
    if not plan.get("include_secrets", False):
        exclude += sorted(SECRET_NAMES | {".env.*", ".ssh", ".aws", ".gnupg"})
    units = []
    for p, root in zip(selected, roots):
        before_git = git_info(root)
        rows = tree(root, exclude)
        for row in rows:
            if row["kind"] != "file":
                continue
            src = root / row["path"]
            sha, st = stable_file(src)
            blob = dest / "blobs" / sha
            if not blob.exists():
                with src.open("rb") as a, blob.open("xb") as b:
                    shutil.copyfileobj(a, b)
                os.chmod(blob, 0o600)
                if digest(blob) != sha or stable_file(src)[0] != sha:
                    raise Refused("Source changed during copy; incomplete bundle is not restorable")
            row.update(sha256=sha, size=st.st_size)
        after_git = git_info(root)
        hashes = {row['path']: row.get('sha256') for row in rows}
        after_tree = [dict(x, **({'sha256': hashes.get(x['path'])} if x['kind']=='file' else {})) for x in tree(root, exclude)]
        if rows != after_tree or after_git != before_git:
            raise Refused("Source tree/Git changed during bundle; run a fresh increment")
        units.append({"id": p["id"], "name": p["name"], "source": str(root), "destination": p["destination"], "git": before_git, "entries": rows})
    manifest = {"schema": "workbridge.bundle/v1", "units": units, "excluded_patterns": exclude,
                "preserves": ["file bytes", "basic POSIX modes", "symlink text", "Git objects if selected"],
                "limitations": ["No xattrs, ACLs, resource forks or hardlink identity", "No live database consistency guarantee; stop writers/use logical exports", "Private bundle: Git history and project files may contain secrets"]}
    save(dest / "manifest.json", manifest)
    return {"bundle": str(dest), "manifest_sha256": digest(dest / "manifest.json"), "units": len(units)}

def verified_manifest(bundle_path, expected=None):
    p = Path(bundle_path).resolve()
    if expected and digest(p / "manifest.json") != expected:
        raise Refused("Manifest digest mismatch")
    m = load(p / "manifest.json")
    if m.get("schema") != "workbridge.bundle/v1":
        raise Refused("Unsupported bundle")
    seen = set()
    for u in m["units"]:
        if u["id"] in seen:
            raise Refused("Duplicate bundle unit")
        seen.add(u["id"]); safe_relative(u["id"])
        paths = set()
        for row in u["entries"]:
            safe_relative(row["path"])
            key = unicodedata.normalize("NFC", row["path"]).casefold()
            if key in paths:
                raise Refused("Duplicate/colliding bundle path")
            paths.add(key)
            if row["kind"] == "file":
                sha = row.get("sha256", "")
                if not re.fullmatch(r"[a-f0-9]{64}", sha):
                    raise Refused("Invalid blob identifier")
                blob = p / "blobs" / sha
                if blob.is_symlink() or not blob.is_file() or digest(blob) != sha or blob.stat().st_size != row["size"]:
                    raise Refused("Missing or corrupt blob")
            elif row["kind"] not in ("directory", "symlink"):
                raise Refused("Unknown entry kind")
        kinds = {x['path']: x['kind'] for x in u['entries']}
        for row in u['entries']:
            for parent in PurePosixPath(row['path']).parents:
                if str(parent) != '.' and kinds.get(str(parent)) != 'directory':
                    raise Refused('Bundle parent must be an explicit directory')
    return p, m

def safe_target(root, relative):
    root = Path(root).absolute()
    dest = root.joinpath(*safe_relative(relative).parts)
    for p in [root, *dest.parents]:
        if p.is_symlink():
            raise Refused(f"Symlink target parent: {p}")
    return dest

def targets_for(m, ssd, hdd):
    targets = {}
    occupied = set()
    for u in m["units"]:
        base = ssd if u["destination"] == "ssd" else hdd
        if not base:
            raise Refused(f"Missing destination root for {u['destination']}")
        # Stable IDs avoid collisions between two projects with the same basename.
        root = Path(base).expanduser().resolve() / (re.sub(r"[^\w. -]", "_", u["name"]) + "--" + u["id"])
        key = unicodedata.normalize("NFC", str(root)).casefold()
        if key in occupied: raise Refused("Target collision")
        occupied.add(key); targets[u["id"]] = root
    return targets

def compare(bundle_path, ssd=None, hdd=None, expected=None):
    bp, m = verified_manifest(bundle_path, expected)
    targets = targets_for(m, ssd, hdd)
    changes = []
    for u in m["units"]:
        root = targets[u["id"]]
        for row in u["entries"]:
            p = safe_target(root, row["path"])
            if not p.exists() and not p.is_symlink(): status = "add"
            elif row["kind"] == "directory": status = "same" if p.is_dir() and not p.is_symlink() else "conflict"
            elif row["kind"] == "symlink": status = "same" if p.is_symlink() and os.readlink(p) == row["target"] else "conflict"
            else: status = "same" if p.is_file() and not p.is_symlink() and digest(p) == row["sha256"] and stat.S_IMODE(p.stat().st_mode) == row["mode"] else "conflict"
            changes.append({"unit": u["id"], "path": row["path"], "status": status})
    return {"schema": "workbridge.diff/v1", "manifest_sha256": digest(bp / "manifest.json"), "targets": {k: str(v) for k,v in targets.items()}, "changes": changes, "conflicts": sum(x["status"] == "conflict" for x in changes)}

def restore(bundle_path, ssd=None, hdd=None, expected=None, apply=False):
    review = compare(bundle_path, ssd, hdd, expected)
    if not apply: return review
    if not expected: raise Refused("Apply requires the reviewed manifest SHA256")
    if review["conflicts"]: raise Refused("Target conflicts; no files were changed")
    bp, m = verified_manifest(bundle_path, expected)
    for u in m["units"]:
        root = Path(review["targets"][u["id"]])
        safe_target(root, "guard")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Create all directories/files before links. Never traverse imported links.
        for row in sorted(u["entries"], key=lambda x: x["kind"] == "symlink"):
            p = safe_target(root, row["path"])
            if p.exists() or p.is_symlink(): continue
            p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if row["kind"] == "directory": p.mkdir(mode=0o700)
            elif row["kind"] == "symlink": os.symlink(row["target"], p)
            else:
                # Exclusive create avoids replacing a concurrent user edit.
                with (bp / "blobs" / row["sha256"]).open("rb") as a, p.open("xb") as b:
                    shutil.copyfileobj(a, b)
                os.chmod(p, row["mode"] & 0o777)  # never restore setuid/setgid
                os.utime(p, ns=(row["mtime_ns"], row["mtime_ns"]))
        # Directory modes after children are materialized.
        for row in reversed(u["entries"]):
            if row["kind"] == "directory":
                p = safe_target(root, row["path"])
                # Existing directory permissions belong to the target owner.
                if any(c['unit']==u['id'] and c['path']==row['path'] and c['status']=='add' for c in review['changes']):
                    os.chmod(p, row["mode"] & 0o777)
    result = compare(bundle_path, ssd, hdd, expected)
    if any(c["status"] != "same" for c in result["changes"]): raise Refused("Readback failed; originals were retained")
    result["status"] = "files_verified_git_links_require_review"
    result["git_link_map"] = {u["source"]: result["targets"][u["id"]] for u in m["units"]}
    return result

def repair_git_links(mapping, apply=False):
    """Only new destination control files. Never git reset/checkout/worktree repair."""
    changes = []
    pairs = sorted(((str(Path(a).resolve()), str(Path(b).resolve())) for a,b in mapping.items()), key=lambda x: -len(x[0]))
    def relocate(v):
        for a,b in pairs:
            if v == a or v.startswith(a + "/"): return b + v[len(a):]
        return v
    for old, new in pairs:
        if old == new: raise Refused("Source and target must differ")
        root = Path(new)
        safe_target(root, "guard")
        marker = root / ".git"
        controls = []
        if marker.is_file() and not marker.is_symlink(): controls.append((marker, "gitdir: "))
        elif marker.is_dir() and not marker.is_symlink():
            controls += [(p, "") for p in (marker / "worktrees").glob("*/gitdir")] if (marker / "worktrees").is_dir() else []
        for p,prefix in controls:
            if p.is_symlink(): raise Refused("Git control file is a symlink")
            raw=p.read_text(); value=raw.strip()
            if prefix:
                if not value.startswith(prefix): raise Refused("Invalid .git file")
                value=value[len(prefix):]
            if not Path(value).is_absolute(): raise Refused("Relative Git links need manual review")
            mapped=relocate(value)
            if mapped==value and not any(under(Path(value), Path(b)) for _,b in pairs):
                raise Refused("Git link points outside destination mapping")
            if not Path(mapped).exists(): raise Refused("Mapped Git control path missing")
            if mapped!=value: changes.append({"path":str(p),"before":raw,"after":prefix+mapped+"\n"})
    if apply:
        for c in changes:
            p=Path(c['path'])
            if p.read_text()!=c['before']: raise Refused("Git link changed during review")
            backup_root = p.parent
            # Keep .git-file backups outside the checked-out working directory.
            if p.name == '.git': backup_root = p.parent.parent
            backup_dir = backup_root / ('.workbridge-git-backup-' + uuid.uuid4().hex)
            backup_dir.mkdir(mode=0o700)
            backup = backup_dir / 'before.txt'
            with backup.open('x') as f: f.write(c['before'])
            c['backup'] = str(backup)
            # Only migration control files explicitly identified above.
            with p.open('w') as f: f.write(c['after'])
    return {"applied":apply,"changes":changes,"note":"Run git status/worktree list and compare refs after repair."}
