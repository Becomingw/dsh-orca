"""orca engine extensions: tools that exist only for DSH (via ``cli-json``).

``install(engine_globals)`` is called once by remote_fleet_mcp.py and returns
extra TOOL_HANDLERS. Remote programs live in sibling files (orca_fs.py,
orca_patch_apply.py, orca_logger.py), so each is unit-testable on its own.
orca_fs and orca_patch_apply are cached on the remote host by content hash
(``~/.local/state/orca/bin``): a call ships a ~300-byte launcher, and only a
cache miss ships the full source once per host and version. Nothing here touches the
protocol shared with upstream except the per-workdir patch lock, which
orca_patch_apply.py takes with upstream's exact naming.
"""

import hashlib
import json
import re
import shlex
import uuid
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKUP_ROOT = "~/.local/state/orca/backups"
PATCH_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
MAX_PATTERN_CHARS = 1000



def _source(name):
    return (HERE / name).read_text(encoding="utf-8")


REMOTE_CACHE_DIR = "~/.local/state/orca/bin"
CACHE_MISS_EXIT = 197

# Runs the cached module if its bytes still hash to the expected version;
# otherwise exits CACHE_MISS_EXIT before touching stdin.
_LAUNCHER = """import hashlib,importlib,os,sys
d=os.path.expanduser(@DIR@);n=@MOD@;p=os.path.join(d,n+'.py')
try:
    ok=hashlib.sha256(open(p,'rb').read()).hexdigest()==@SHA@
except OSError:
    ok=False
if not ok:
    sys.exit(@MISS@)
sys.path.insert(0,d)
importlib.import_module(n)._entrypoint()
"""

# Installs the module atomically (dropping older versions), then runs it.
# If the home directory is not writable it still runs, uncached.
_INSTALLER = """import importlib,os,sys,tempfile,types
src=@SRC@;d=os.path.expanduser(@DIR@);n=@MOD@;pre=@PREFIX@
try:
    os.makedirs(d,mode=0o700,exist_ok=True)
    for sub in (d,os.path.join(d,'__pycache__')):
        for old in (os.listdir(sub) if os.path.isdir(sub) else ()):
            if old.startswith(pre) and not old.startswith(n+'.'):
                os.unlink(os.path.join(sub,old))
    fd,tmp=tempfile.mkstemp(dir=d,suffix='.tmp')
    with os.fdopen(fd,'w',encoding='utf-8') as f:
        f.write(src)
    os.chmod(tmp,0o600)
    os.replace(tmp,os.path.join(d,n+'.py'))
    sys.path.insert(0,d)
    m=importlib.import_module(n)
except Exception:
    m=types.ModuleType(n)
    exec(compile(src,n,'exec'),m.__dict__)
m._entrypoint()
"""


def _fill(template, **values):
    for key, value in values.items():
        template = template.replace("@" + key + "@", repr(value))
    return template


def remote_program(source_name):
    """(launcher_command, installer_command) for a cached remote program."""
    source = _source(source_name)
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    stem = source_name[:-3]
    module = "{}_{}".format(stem, digest[:16])
    launcher = _fill(_LAUNCHER, DIR=REMOTE_CACHE_DIR, MOD=module, SHA=digest, MISS=CACHE_MISS_EXIT)
    installer = _fill(_INSTALLER, SRC=source, DIR=REMOTE_CACHE_DIR, MOD=module, PREFIX=stem + "_")
    return "python3 -c {}".format(shlex.quote(launcher)), "python3 -c {}".format(shlex.quote(installer))


def logger_spec(g, options):
    """Validate launch-time logging options and attach the follower source."""
    if options is True:
        options = {}
    if not isinstance(options, dict):
        raise g["ToolError"]("orca_log must be an object")
    bounded = g["bounded_int"]
    return {
        "source": _source("orca_logger.py"),
        "tail_lines": bounded(options.get("tail_lines"), "orca_log.tail_lines", 2000, 10, 20000),
        "rotate_bytes": bounded(options.get("rotate_bytes"), "orca_log.rotate_bytes", 32 << 20, 0, 1 << 34),
        "keep_segments": bounded(options.get("keep_segments"), "orca_log.keep_segments", 5, 1, 1000),
    }


def _run_program(g, host, source_name, request, timeout_seconds, cancel_token, what, on_uncertain=None):
    """Run a remote program. Exit 0 returns its payload (even ok:false); exit 1
    means nothing was touched and raises. When the outcome is unknown (timeout,
    lost or garbled reply) ``on_uncertain(reason)`` decides what to return."""
    ToolError = g["ToolError"]
    launcher, installer = remote_program(source_name)
    stdin = json.dumps(request, ensure_ascii=False).encode("utf-8")

    def ssh(command):
        return g["run_ssh"](
            host,
            command,
            stdin=stdin,
            timeout_seconds=timeout_seconds,
            max_output_chars=g["MAX_OUTPUT_CHARS"],
            cancel_token=cancel_token,
        )

    result = ssh(launcher)
    if result["exit_code"] == CACHE_MISS_EXIT and not result["stdout"].strip() and not result["timed_out"]:
        # Nothing ran (the launcher exits before reading stdin): install and run.
        result = ssh(installer)
    if result["timed_out"]:
        reason = "{} on {!r} timed out".format(what, host)
        if on_uncertain is not None:
            return on_uncertain(reason)
        raise ToolError(reason)
    try:
        payload = json.loads(result["stdout"])
    except ValueError:
        detail = result["stderr"].strip() or "exit code {}".format(result["exit_code"])
        reason = "{} on {!r} failed: {}".format(what, host, detail[-2000:])
        if on_uncertain is not None:
            return on_uncertain(reason)
        raise ToolError(reason)
    if result["truncated"]:
        raise ToolError("{} output exceeded the safety limit; narrow the request".format(what))
    if result["exit_code"] != 0:
        raise ToolError(str(payload.get("error") or "{} failed with exit code {}".format(what, result["exit_code"])))
    payload["host"] = host
    return payload


def _flag(g, arguments, name):
    value = arguments.get(name, False)
    if not isinstance(value, bool):
        raise g["ToolError"]("{} must be a boolean".format(name))
    return value


def orca_grep(g, arguments, cancel_token=None):
    ToolError = g["ToolError"]
    host = g["validate_host"](arguments.get("host"))
    path = g["validate_absolute_path"](arguments.get("path"), "path")
    pattern = arguments.get("pattern")
    if not isinstance(pattern, str) or not pattern or len(pattern) > MAX_PATTERN_CHARS:
        raise ToolError("pattern must be a non-empty string of at most {} characters".format(MAX_PATTERN_CHARS))
    glob = arguments.get("glob")
    if glob is not None and (not isinstance(glob, str) or not glob):
        raise ToolError("glob must be a non-empty string")
    bounded = g["bounded_int"]
    timeout = bounded(arguments.get("timeout_seconds"), "timeout_seconds", 30, 1, 300)
    request = {
        "op": "grep",
        "path": path,
        "pattern": pattern,
        "fixed": _flag(g, arguments, "fixed_string"),
        "ignore_case": _flag(g, arguments, "ignore_case"),
        "include_hidden": _flag(g, arguments, "include_hidden"),
        "glob": glob,
        "max_matches": bounded(arguments.get("max_matches"), "max_matches", 100, 1, 2000),
        "context": bounded(arguments.get("context_lines"), "context_lines", 0, 0, 10),
        "max_line_chars": 400,
        "max_files": 20000,
        "timeout_seconds": timeout,
    }
    return _run_program(g, host, "orca_fs.py", request, timeout + 15, cancel_token, "remote grep")


def orca_ls(g, arguments, cancel_token=None):
    ToolError = g["ToolError"]
    host = g["validate_host"](arguments.get("host"))
    path = g["validate_absolute_path"](arguments.get("path"), "path")
    glob = arguments.get("glob")
    if glob is not None and (not isinstance(glob, str) or not glob):
        raise ToolError("glob must be a non-empty string")
    bounded = g["bounded_int"]
    request = {
        "op": "ls",
        "path": path,
        "depth": bounded(arguments.get("depth"), "depth", 1, 1, 5),
        "max_entries": bounded(arguments.get("max_entries"), "max_entries", 300, 1, 5000),
        "glob": glob,
        "include_hidden": _flag(g, arguments, "include_hidden"),
    }
    return _run_program(g, host, "orca_fs.py", request, 60, cancel_token, "remote ls")


def new_patch_id():
    return "orca-{}-{}".format(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"), uuid.uuid4().hex[:8])


def _patch_status(g, host, patch_id):
    request = {"mode": "status", "patch_id": patch_id, "backup_root": BACKUP_ROOT}
    return _run_program(g, host, "orca_patch_apply.py", request, 60, None, "remote patch status")


def orca_patch(g, arguments, cancel_token=None):
    host = g["validate_host"](arguments.get("host"))
    workdir = g["validate_absolute_path"](arguments.get("workdir"), "workdir")
    # The upstream guard (with orca's E1 fix) runs locally first; the remote
    # applier re-validates every path against the resolved workdir.
    _patch_bytes, touched = g["validate_codex_patch"](arguments.get("patch"))
    expected = g["validate_expected_sha256"](arguments.get("expected_sha256"), touched)
    dry_run = _flag(g, arguments, "dry_run")
    request = {
        "mode": "dry_run" if dry_run else "apply",
        "workdir": workdir,
        "patch": arguments["patch"],
        "expected_sha256": expected,
        "max_diff_chars": 20000,
        "strict": _flag(g, arguments, "strict"),
    }
    if dry_run:
        return _run_program(g, host, "orca_patch_apply.py", request, 120, cancel_token, "remote patch")

    # patch_id doubles as an idempotency key: retrying with the same id never
    # applies a patch twice.
    patch_id = arguments.get("patch_id")
    if patch_id is None:
        patch_id = new_patch_id()
    elif not isinstance(patch_id, str) or not PATCH_ID_PATTERN.fullmatch(patch_id):
        raise g["ToolError"]("patch_id must match [A-Za-z0-9_.-]{1,100}")
    request["patch_id"] = patch_id
    request["backup_root"] = BACKUP_ROOT

    def uncertain(reason):
        # ssh -T has no pty: the remote apply may well have finished. Ask.
        try:
            status = _patch_status(g, host, patch_id)
        except Exception as exc:  # noqa: BLE001 - report both failures
            raise g["ToolError"](
                "{}; outcome UNKNOWN and the status check failed too ({}). Retry orca_patch with "
                "patch_id={!r}: it is idempotent and will not apply twice.".format(reason, exc, patch_id)
            )
        state = status.get("state")
        payload = {"mode": "apply", "patch_id": patch_id, "host": host, "uncertain": reason, "status": status}
        if state == "committed":
            payload.update(ok=True, state="applied", note="the reply was lost but the patch is fully applied")
        elif state == "prepared":
            payload.update(ok=False, state="partial",
                           error="the apply was interrupted and the workdir is PARTIALLY APPLIED; "
                                 "run orca_rollback {} or retry orca_patch with the same patch_id".format(patch_id))
        elif state == "not_found":
            payload.update(ok=False, state="not_applied",
                           error="the patch was not applied (nothing changed); retry orca_patch with the same patch_id")
        else:
            payload.update(ok=False, state=state, error="the patch did not take effect ({})".format(state))
        return payload

    return _run_program(g, host, "orca_patch_apply.py", request, 120, cancel_token, "remote patch", uncertain)


def orca_rollback(g, arguments, cancel_token=None):
    ToolError = g["ToolError"]
    host = g["validate_host"](arguments.get("host"))
    patch_id = arguments.get("patch_id")
    if not isinstance(patch_id, str) or not PATCH_ID_PATTERN.fullmatch(patch_id):
        raise ToolError("patch_id must be the id returned by orca_patch")
    request = {
        "mode": "rollback",
        "patch_id": patch_id,
        "backup_root": BACKUP_ROOT,
        "force": _flag(g, arguments, "force"),
    }
    return _run_program(g, host, "orca_patch_apply.py", request, 120, cancel_token, "remote rollback")


def install(engine_globals):
    """Bind handlers to one engine's live globals (so mock.patch on it applies)."""

    def bind(handler):
        def call(arguments, cancel_token=None):
            return handler(engine_globals, arguments, cancel_token)

        call.__name__ = handler.__name__
        return call

    return {name: bind(fn) for name, fn in (
        ("orca_grep", orca_grep),
        ("orca_ls", orca_ls),
        ("orca_patch", orca_patch),
        ("orca_rollback", orca_rollback),
    )}
