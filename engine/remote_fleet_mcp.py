#!/usr/bin/env python3
"""Zero-dependency MCP server for structured operations on trusted SSH hosts."""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
import glob
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import signal
import socket
import stat
import subprocess
import sys
import threading
import traceback
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orca_ext  # noqa: E402  (orca extensions; see docs/engine-changes.md)


SERVER_NAME = "remote-fleet"
SERVER_VERSION = "0.7.0"
LATEST_PROTOCOL_VERSION = "2025-06-18"
SUPPORTED_PROTOCOL_VERSIONS = frozenset({LATEST_PROTOCOL_VERSION})
MAX_READ_BYTES = 1_048_576
MAX_PATCH_BYTES = 1_048_576
MAX_PATCH_GUARD_BYTES = 131_072
MAX_COMMAND_BYTES = 1_048_576
MAX_OUTPUT_CHARS = 200_000
MAX_TIMEOUT_SECONDS = 3_600
MAX_CONCURRENT_TOOL_CALLS = 8
MAX_PENDING_TOOL_CALLS = 32
HOST_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
SESSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$")
CODEX_NATIVE_METADATA_PREFIX = "REMOTE_FLEET_CODEX_NATIVE="
CODEX_PATCH_ACTION_PATTERN = re.compile(
    r"^\*\*\* (?:Add File|Delete File|Update File|Move to): (.+)$"
)
# orca: tmux rewrites '.' and ':' in session names, so launches use a narrower rule.
LAUNCH_SESSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$")
# orca: Codex apply_patch trims header lines; check every line the same way.
PATCH_LINE_SPLIT = re.compile(r"\r\n|[\n\r\x0b\x0c\x1c-\x1e\x85\u2028\u2029]")


class ToolError(RuntimeError):
    """A user-facing tool execution error."""


class ToolCancelled(RuntimeError):
    """The client cancelled the request while it was in flight."""


def _json_text(value: Any) -> str:
    # Keep MCP responses compact. The same value is also available as
    # structuredContent, so pretty-printing only adds transport and context cost.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _tool_result(value: Any, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": _json_text(value)}],
        "isError": is_error,
        "structuredContent": value if isinstance(value, dict) else {"result": value},
    }


def _error_result(message: str) -> dict[str, Any]:
    return _tool_result({"error": message}, is_error=True)


_STDOUT_LOCK = threading.Lock()


def _write_response(response: dict[str, Any]) -> None:
    payload = json.dumps(response, ensure_ascii=False, separators=(",", ":"))
    with _STDOUT_LOCK:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


def _kill_process(process: subprocess.Popen) -> None:
    """Kill a child; a session leader we started takes its whole group with it."""
    try:
        if process.pid and os.getpgid(process.pid) == process.pid:
            os.killpg(process.pid, signal.SIGKILL)
            return
    except (OSError, ProcessLookupError):
        pass
    try:
        process.kill()
    except OSError:
        pass


class _BoundedPipeCapture:
    """Drain one subprocess pipe while retaining only a fixed byte prefix."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.chunks: list[bytes] = []
        self.size = 0
        self.truncated = False

    def drain(self, stream: Any) -> None:
        try:
            while True:
                chunk = stream.read(65_536)
                if not chunk:
                    break
                remaining = self.limit - self.size
                if remaining > 0:
                    kept = chunk[:remaining]
                    self.chunks.append(kept)
                    self.size += len(kept)
                if len(chunk) > max(remaining, 0):
                    self.truncated = True
        except OSError:
            self.truncated = True
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def value(self) -> bytes:
        return b"".join(self.chunks)


class CancelToken:
    """Cancellation state for one in-flight tools/call request."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = False
        self._process: subprocess.Popen | None = None

    def register(self, process: subprocess.Popen) -> None:
        with self._lock:
            self._process = process
            cancelled = self._cancelled
        if cancelled:
            _kill_process(process)

    def cancel(self) -> None:
        with self._lock:
            self._cancelled = True
            process = self._process
        if process is not None:
            _kill_process(process)

    @property
    def cancelled(self) -> bool:
        with self._lock:
            return self._cancelled


_INFLIGHT_LOCK = threading.Lock()
_INFLIGHT: dict[Any, CancelToken] = {}
_CALL_SLOTS = threading.BoundedSemaphore(MAX_PENDING_TOOL_CALLS)
_TOOL_EXECUTOR = ThreadPoolExecutor(
    max_workers=MAX_CONCURRENT_TOOL_CALLS,
    thread_name_prefix="remote-fleet-call",
)
_SHUTTING_DOWN = threading.Event()


def _cancel_inflight(request_id: Any) -> None:
    with _INFLIGHT_LOCK:
        token = _INFLIGHT.get(request_id)
    if token is not None:
        token.cancel()


# Tool dispatch. "subprocess" (default) runs every tools/call in a fresh worker
# process that re-imports this file from disk, so code updates take effect on
# the next call without restarting the resident MCP server; only the JSON-RPC
# framing stays resident. "inprocess" runs handlers directly (tests, debugging).
DISPATCH_MODE = os.environ.get("REMOTE_FLEET_DISPATCH", "subprocess")
CHILD_TIMEOUT_SECONDS = MAX_TIMEOUT_SECONDS + 120


def _child_command() -> list[str]:
    return [sys.executable, "-B", os.path.abspath(__file__), "cli-json"]


def _run_tool_in_child(
    name: str, arguments: dict[str, Any], cancel_token: CancelToken | None
) -> Any:
    request = json.dumps({"tool": name, "arguments": arguments}, ensure_ascii=False).encode("utf-8")
    process = subprocess.Popen(
        _child_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    if cancel_token is not None:
        cancel_token.register(process)
    try:
        out, err = process.communicate(request, timeout=CHILD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_process(process)
        out, err = process.communicate()
        if cancel_token is not None and cancel_token.cancelled:
            raise ToolCancelled(f"{name} was cancelled by the client")
        raise ToolError(f"{name} did not finish within {CHILD_TIMEOUT_SECONDS}s")
    if cancel_token is not None and cancel_token.cancelled:
        raise ToolCancelled(f"{name} was cancelled by the client")
    text = out.decode("utf-8", errors="replace")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        detail = err.decode("utf-8", errors="replace").strip()[-2000:] or text.strip()[-500:]
        raise ToolError(f"tool worker exited {process.returncode} without a JSON result: {detail}")
    if process.returncode != 0:
        message = payload.get("error") if isinstance(payload, dict) else None
        raise ToolError(str(message or f"tool worker exited {process.returncode}"))
    return payload


def _invoke_tool(name: str, handler: Any, arguments: dict[str, Any], cancel_token: CancelToken) -> Any:
    if DISPATCH_MODE == "inprocess":
        return handler(arguments, cancel_token)
    return _run_tool_in_child(name, arguments, cancel_token)


def _cli_json_main() -> int:
    """Worker entry: one tool call from stdin JSON, result JSON on stdout."""
    try:
        request = json.loads(sys.stdin.read())
        name = request["tool"]
        arguments = request.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise ValueError("arguments must be an object")
        handler = TOOL_HANDLERS[name]
    except Exception as exc:
        sys.stdout.buffer.write(_json_text({"error": f"invalid worker request: {exc}"}).encode("utf-8"))
        return 2
    try:
        result = handler(arguments)
    except ToolError as exc:
        sys.stdout.buffer.write(_json_text({"error": str(exc)}).encode("utf-8"))
        return 1
    except Exception as exc:
        traceback.print_exc()
        sys.stdout.buffer.write(_json_text({"error": f"unexpected remote-fleet error: {exc}"}).encode("utf-8"))
        return 2
    sys.stdout.buffer.write(_json_text(result).encode("utf-8"))
    return 0


def _read_ssh_hosts_from_file(path: Path, seen: set[Path]) -> set[str]:
    try:
        resolved = path.expanduser().resolve()
    except OSError:
        return set()
    if resolved in seen or not resolved.is_file():
        return set()
    seen.add(resolved)

    hosts: set[str] = set()
    try:
        lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return hosts

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            parts = shlex.split(line, comments=True)
        except ValueError:
            continue
        if not parts:
            continue
        keyword = parts[0].lower()
        if keyword == "host":
            for alias in parts[1:]:
                if HOST_PATTERN.fullmatch(alias) and not any(c in alias for c in "*?!"):
                    hosts.add(alias)
        elif keyword == "include":
            for pattern in parts[1:]:
                candidate = Path(pattern).expanduser()
                if not candidate.is_absolute():
                    candidate = resolved.parent / candidate
                for included in glob.glob(str(candidate)):
                    hosts.update(_read_ssh_hosts_from_file(Path(included), seen))
    return hosts


def configured_hosts() -> set[str]:
    explicit = os.environ.get("REMOTE_FLEET_HOSTS", "").strip()
    if explicit:
        return {
            item.strip()
            for item in explicit.split(",")
            if HOST_PATTERN.fullmatch(item.strip())
        }
    return _read_ssh_hosts_from_file(Path.home() / ".ssh" / "config", set())


def validate_host(host: Any) -> str:
    if not isinstance(host, str) or not HOST_PATTERN.fullmatch(host):
        raise ToolError("host must be a concrete SSH alias containing only letters, digits, '.', '_' or '-'")
    allowed = configured_hosts()
    if host not in allowed:
        listed = ", ".join(sorted(allowed)) or "none"
        raise ToolError(f"SSH host {host!r} is not allowed; configured concrete aliases: {listed}")
    return host


def validate_absolute_path(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or "\x00" in value:
        raise ToolError(f"{name} must be an absolute remote path")
    return value


def validate_session(value: Any, *, required: bool = True) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not SESSION_PATTERN.fullmatch(value):
        raise ToolError(
            "session must be 1-80 characters using only letters, digits, '.', '_' or '-'"
        )
    return value


def validate_codex_patch(patch: Any) -> tuple[bytes, list[str]]:
    if not isinstance(patch, str) or not patch.strip():
        raise ToolError("patch must be a non-empty Codex apply_patch document")
    patch_bytes = patch.encode("utf-8")
    if len(patch_bytes) > MAX_PATCH_BYTES:
        raise ToolError(f"patch exceeds the {MAX_PATCH_BYTES}-byte limit")

    lines = patch.splitlines()
    if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
        raise ToolError("patch must start with '*** Begin Patch' and end with '*** End Patch'")

    paths: list[str] = []
    for line in PATCH_LINE_SPLIT.split(patch):
        match = CODEX_PATCH_ACTION_PATTERN.fullmatch(line.strip())
        if not match:
            continue
        raw_path = match.group(1).strip()
        parsed = PurePosixPath(raw_path)
        if (
            not raw_path
            or "\x00" in raw_path
            or parsed.is_absolute()
            or raw_path == "."
            or ".." in parsed.parts
        ):
            raise ToolError(f"patch path must stay inside workdir: {raw_path!r}")
        if raw_path not in paths:
            paths.append(raw_path)
    if not paths:
        raise ToolError("patch must contain at least one Add File, Delete File, or Update File action")
    return patch_bytes, paths


def validate_expected_sha256(
    value: Any, touched_paths: list[str]
) -> dict[str, str | None]:
    """Validate optional optimistic-concurrency checks for patch targets."""

    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ToolError("expected_sha256 must be an object mapping patch paths to hashes or null")
    touched = set(touched_paths)
    validated: dict[str, str | None] = {}
    for path, expected in value.items():
        if not isinstance(path, str) or path not in touched:
            raise ToolError(f"expected_sha256 path is not touched by this patch: {path!r}")
        if expected is None:
            validated[path] = None
        elif isinstance(expected, str) and re.fullmatch(r"[0-9A-Fa-f]{64}", expected):
            validated[path] = expected.lower()
        else:
            raise ToolError(f"expected_sha256[{path!r}] must be a 64-digit hex hash or null")
    return validated


def bounded_int(value: Any, name: str, default: int, minimum: int, maximum: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ToolError(f"{name} must be an integer between {minimum} and {maximum}")
    return value


MUX_DIR_MAX_CHARS = 40
_MUX_PROBE_LOCK = threading.Lock()
_MUX_PROBE_CACHE: dict[str, bool] = {}


def _can_bind_control_socket(directory: Path) -> bool:
    """Probe once per directory so restricted runtimes gracefully disable muxing."""

    key = str(directory)
    with _MUX_PROBE_LOCK:
        cached = _MUX_PROBE_CACHE.get(key)
        if cached is not None:
            return cached
        probe_path = directory / f".rf-probe-{os.getpid()}"
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        usable = False
        try:
            try:
                probe_path.unlink()
            except FileNotFoundError:
                pass
            probe.bind(str(probe_path))
            usable = True
        except OSError:
            usable = False
        finally:
            probe.close()
            try:
                probe_path.unlink()
            except OSError:
                pass
        _MUX_PROBE_CACHE[key] = usable
        return usable


def _ssh_multiplex_options() -> list[str]:
    raw = os.environ.get("REMOTE_FLEET_SSH_PERSIST", "600").strip().lower()
    if raw in {"", "0", "no", "off", "false"}:
        return []
    persist_seconds = int(raw) if raw.isdigit() else 600
    directory = Path.home() / ".ssh" / "rf-mux"
    if len(str(directory)) > MUX_DIR_MAX_CHARS:
        # Unix socket paths must stay under ~104 bytes; skip multiplexing instead of failing.
        return []
    try:
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = directory.stat()
        if info.st_uid != os.getuid():
            return []
        if stat.S_IMODE(info.st_mode) != 0o700:
            directory.chmod(0o700)
    except OSError:
        return []
    if not _can_bind_control_socket(directory):
        return []
    return [
        "-o", "ControlMaster=auto",
        "-o", f"ControlPath={directory}/%C",
        "-o", f"ControlPersist={persist_seconds}s",
    ]


def _ssh_command(host: str) -> list[str]:
    return [
        "ssh",
        "-T",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=2",
        *_ssh_multiplex_options(),
        host,
    ]


def run_ssh(
    host: str,
    remote_command: str,
    *,
    stdin: bytes | None = None,
    timeout_seconds: int = 60,
    max_output_chars: int = MAX_OUTPUT_CHARS,
    cancel_token: CancelToken | None = None,
) -> dict[str, Any]:
    process = subprocess.Popen(
        [*_ssh_command(host), remote_command],
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if cancel_token is not None:
        cancel_token.register(process)
    assert process.stdout is not None and process.stderr is not None
    # Capture up to 4 bytes per budgeted character (the UTF-8 worst case) so
    # multibyte output still yields the full character budget after decoding.
    capture_limit = max_output_chars * 4
    stdout_capture = _BoundedPipeCapture(capture_limit)
    stderr_capture = _BoundedPipeCapture(capture_limit)
    readers = [
        threading.Thread(target=stdout_capture.drain, args=(process.stdout,), daemon=True),
        threading.Thread(target=stderr_capture.drain, args=(process.stderr,), daemon=True),
    ]
    for reader in readers:
        reader.start()
    writer: threading.Thread | None = None
    if stdin is not None and process.stdin is not None:
        def feed_stdin() -> None:
            try:
                assert process.stdin is not None
                process.stdin.write(stdin)
            except (BrokenPipeError, OSError):
                pass
            finally:
                try:
                    assert process.stdin is not None
                    process.stdin.close()
                except OSError:
                    pass

        # Feeding stdin on a worker keeps a stalled SSH peer inside the same timeout
        # budget as the rest of the call.
        writer = threading.Thread(target=feed_stdin, daemon=True)
        writer.start()
    try:
        process.wait(timeout=timeout_seconds)
        timed_out = False
        exit_code: int | None = process.returncode
    except subprocess.TimeoutExpired:
        _kill_process(process)
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _kill_process(process)
            process.wait()
        timed_out = True
        exit_code = None
    for reader in readers:
        reader.join(timeout=5)
    if writer is not None:
        writer.join(timeout=5)
    if cancel_token is not None and cancel_token.cancelled:
        raise ToolCancelled(f"call to {host!r} was cancelled by the client")

    stdout_bytes = stdout_capture.value()
    stderr_bytes = stderr_capture.value()
    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")
    total = len(stdout) + len(stderr)
    truncated = stdout_capture.truncated or stderr_capture.truncated or total > max_output_chars
    if truncated:
        # Each stream is guaranteed half the budget; whatever one does not use
        # is handed to the other so no budget is wasted.
        stderr_budget = min(len(stderr), max_output_chars // 2)
        stdout_budget = min(len(stdout), max_output_chars - stderr_budget)
        stderr_budget = min(len(stderr), max_output_chars - stdout_budget)
        stdout = stdout[:stdout_budget]
        stderr = stderr[:stderr_budget]

    return {
        "exit_code": exit_code,
        "stderr": stderr,
        "stdout": stdout,
        "timed_out": timed_out,
        "truncated": truncated,
    }


REMOTE_READ_PROGRAM = r'''
import hashlib
import json
from pathlib import Path
import sys

path = Path(sys.argv[1])
start_line = int(sys.argv[2])
end_line = int(sys.argv[3])
max_bytes = int(sys.argv[4])
offset_bytes = int(sys.argv[5])
include_sha256 = sys.argv[6] == "1"

if not path.is_file():
    raise SystemExit(f"not a regular file: {path}")
stat_before = path.stat()
size_bytes = stat_before.st_size
# orca: "auto" hashes small files only, so edits can be checked against what was read.
if sys.argv[6] == "auto":
    include_sha256 = size_bytes <= 4 * 1024 * 1024

sha256 = None
if include_sha256:
    digest_builder = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest_builder.update(chunk)
    sha256 = digest_builder.hexdigest()

selected = bytearray()
line_number = 0
first_line = None
last_line = None
content_truncated = False
eof_reached = False
next_start_line = None
next_offset_bytes = None
line_fragment = False
with path.open("rb") as handle:
    if offset_bytes:
        handle.seek(offset_bytes)
    at_line_start = True
    while True:
        # Bounded readline so a single pathological line cannot exhaust memory.
        piece_start = handle.tell()
        piece = handle.readline(1024 * 1024)
        if not piece:
            eof_reached = True
            break
        starts_line = at_line_start
        at_line_start = piece.endswith(b"\n")
        if starts_line:
            line_number += 1
        if line_number < start_line:
            continue
        if end_line and line_number > end_line:
            next_start_line = line_number
            break
        if len(selected) + len(piece) > max_bytes:
            content_truncated = True
            if not selected:
                first_line = line_number
                last_line = line_number
                selected.extend(piece[:max_bytes])
                line_fragment = True
                next_offset_bytes = piece_start + max_bytes
            elif last_line == line_number:
                # The current logical line spans multiple bounded readline pieces.
                # Resume at the exact byte offset instead of silently skipping it.
                line_fragment = True
                next_offset_bytes = piece_start
            else:
                next_start_line = line_number
            break
        if first_line is None:
            first_line = line_number
        last_line = line_number
        selected.extend(piece)

try:
    stat_after = path.stat()
    snapshot_consistent = (
        stat_before.st_dev,
        stat_before.st_ino,
        stat_before.st_size,
        stat_before.st_mtime_ns,
    ) == (
        stat_after.st_dev,
        stat_after.st_ino,
        stat_after.st_size,
        stat_after.st_mtime_ns,
    )
except OSError:
    snapshot_consistent = False

result = {
    "content": bytes(selected).decode("utf-8", errors="replace"),
    "content_truncated": content_truncated,
    "end_line": end_line or None,
    "eof_reached": eof_reached,
    "first_line": first_line,
    "last_line": last_line,
    "line_fragment": line_fragment,
    "next_offset_bytes": next_offset_bytes,
    "next_start_line": next_start_line,
    "offset_bytes": offset_bytes,
    "path": str(path),
    "sha256": sha256,
    "size_bytes": size_bytes,
    "snapshot_consistent": snapshot_consistent,
    "start_line": start_line,
    "total_lines": line_number if eof_reached and offset_bytes == 0 else None,
}
print(json.dumps(result, ensure_ascii=False))
'''.strip()


REMOTE_RUN_PROGRAM = r'''
import os
import signal
import subprocess
import sys
import time

workdir = sys.argv[1]
timeout_seconds = int(sys.argv[2])
command = sys.stdin.buffer.read().decode("utf-8")
if not os.path.isdir(workdir):
    raise SystemExit(f"not a directory: {workdir}")

process = subprocess.Popen(
    ["bash", "-lc", command],
    cwd=workdir,
    stdin=subprocess.DEVNULL,
    start_new_session=True,
)


def kill_group(sig):
    try:
        os.killpg(os.getpgid(process.pid), sig)
    except (OSError, ProcessLookupError):
        pass


def handle_parent_signal(signum, _frame):
    # SSH disconnects normally signal this watchdog. The child is in a separate
    # session, so explicitly terminate its process group before exiting.
    kill_group(signal.SIGTERM)
    try:
        # Polling wait only: a blocking wait here could deadlock on Popen's
        # non-reentrant wait lock if the signal interrupted the main wait loop
        # while it held the lock.
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        kill_group(signal.SIGKILL)
    os._exit(128 + signum)


for parent_signal in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
    signal.signal(parent_signal, handle_parent_signal)


initial_ppid = os.getppid()
deadline = time.monotonic() + timeout_seconds
exit_code = None
try:
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(command, timeout_seconds)
        try:
            exit_code = process.wait(timeout=min(1.0, remaining))
            break
        except subprocess.TimeoutExpired:
            # orca: ssh -T gives no pty, so a dropped connection only shows up
            # as this watchdog being reparented.
            if initial_ppid != 1 and os.getppid() != initial_ppid:
                handle_parent_signal(signal.SIGHUP, None)
except subprocess.TimeoutExpired:
    kill_group(signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        kill_group(signal.SIGKILL)
        process.wait()
    print(f"remote-fleet: command timed out after {timeout_seconds}s", file=sys.stderr)
    raise SystemExit(124)
raise SystemExit(128 - exit_code if exit_code < 0 else exit_code)
'''.strip()


REMOTE_CODEX_APPLY_PROGRAM = r'''
import base64
import fcntl
import glob
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

METADATA_PREFIX = "REMOTE_FLEET_CODEX_NATIVE="
workdir = Path(sys.argv[1])
if not workdir.is_dir():
    raise SystemExit(f"not a directory: {workdir}")
workdir = workdir.resolve()
os.chdir(workdir)

try:
    guard = json.loads(base64.urlsafe_b64decode(sys.argv[2].encode("ascii")))
except Exception as exc:
    raise SystemExit(f"invalid patch guard: {exc}")
guard_paths = guard.get("paths")
expected_sha256 = guard.get("expected_sha256")
if not isinstance(guard_paths, list) or not isinstance(expected_sha256, dict):
    raise SystemExit("invalid patch guard shape")

# Serialize Remote Fleet patches per real workdir. This is an advisory lock: it
# prevents two plugin calls from racing without pretending to lock out unrelated
# editors or build processes.
lock_dir = Path.home() / ".local" / "state" / "remote-fleet" / "locks"
lock_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
lock_key = hashlib.sha256(str(workdir).encode("utf-8")).hexdigest()
lock_handle = (lock_dir / f"{lock_key}.lock").open("a+b")
lock_deadline = time.monotonic() + 10
while True:
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        break
    except BlockingIOError:
        if time.monotonic() >= lock_deadline:
            raise SystemExit("another Remote Fleet patch is active for this workdir")
        time.sleep(0.05)
os.set_inheritable(lock_handle.fileno(), True)


def verify_guard():
    for raw_path in guard_paths:
        if not isinstance(raw_path, str) or not raw_path or raw_path.startswith("/"):
            raise SystemExit(f"invalid guarded patch path: {raw_path!r}")
        candidate = workdir / raw_path
        try:
            resolved = candidate.resolve(strict=False)
            if os.path.commonpath([str(workdir), str(resolved)]) != str(workdir):
                raise SystemExit(f"patch path resolves outside workdir: {raw_path}")
        except OSError as exc:
            raise SystemExit(f"cannot resolve patch path {raw_path!r}: {exc}")

        if raw_path not in expected_sha256:
            continue
        expected = expected_sha256[raw_path]
        exists = candidate.exists() or candidate.is_symlink()
        if expected is None:
            if exists:
                raise SystemExit(f"patch precondition failed; expected absent path: {raw_path}")
            continue
        if not isinstance(expected, str) or len(expected) != 64 or not candidate.is_file():
            raise SystemExit(f"patch precondition failed; expected regular file: {raw_path}")
        digest = hashlib.sha256()
        with candidate.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
        actual = digest.hexdigest()
        if actual != expected:
            raise SystemExit(
                f"patch precondition failed for {raw_path}: expected {expected}, found {actual}"
            )


def executable_file(value):
    try:
        path = Path(value).expanduser().resolve()
        return path if path.is_file() and os.access(path, os.X_OK) else None
    except (OSError, TypeError, ValueError):
        return None


def native_binary(value):
    path = executable_file(value)
    if path is None:
        return None
    try:
        with path.open("rb") as handle:
            magic = handle.read(4)
    except OSError:
        return None
    if magic == b"\x7fELF" or magic in {
        b"\xcf\xfa\xed\xfe",
        b"\xfe\xed\xfa\xcf",
        b"\xca\xfe\xba\xbe",
    }:
        return path
    return None


def add_unique(values, value):
    if not value:
        return
    text = str(value)
    if text not in values:
        values.append(text)


direct_apply_patch = native_binary(shutil.which("apply_patch"))
if direct_apply_patch:
    verify_guard()
    metadata = {
        "backend": "codex-native",
        "codex_version": None,
        "native_codex_path": str(direct_apply_patch),
        "resolution": "apply_patch-path",
    }
    print(METADATA_PREFIX + json.dumps(metadata, separators=(",", ":")), file=sys.stderr, flush=True)
    os.execve(str(direct_apply_patch), ["apply_patch"], os.environ.copy())


cli_candidates = []
add_unique(cli_candidates, shutil.which("codex"))
try:
    login_lookup = subprocess.run(
        ["bash", "-lc", "command -v codex"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        timeout=10,
        check=False,
    )
    if login_lookup.returncode == 0 and login_lookup.stdout.strip():
        add_unique(cli_candidates, login_lookup.stdout.strip().splitlines()[0])
except (OSError, subprocess.SubprocessError, IndexError):
    pass

for pattern in (
    "~/.local/bin/codex",
    "~/.npm-global/bin/codex",
    "~/.nvm/versions/node/*/bin/codex",
    "/usr/local/bin/codex",
    "/usr/bin/codex",
    "/opt/homebrew/bin/codex",
    "/usr/local/lib/nodejs/*/bin/codex",
):
    for candidate in glob.glob(str(Path(pattern).expanduser())):
        add_unique(cli_candidates, candidate)

package_roots = []
native_candidates = []
for candidate in cli_candidates:
    native = native_binary(candidate)
    if native:
        add_unique(native_candidates, native)
        continue
    resolved = executable_file(candidate)
    if resolved and resolved.name in {"codex.js", "codex"} and resolved.parent.name == "bin":
        add_unique(package_roots, resolved.parent.parent)

for pattern in (
    "~/.local/lib/node_modules/@openai/codex",
    "~/.npm-global/lib/node_modules/@openai/codex",
    "~/.nvm/versions/node/*/lib/node_modules/@openai/codex",
    "/usr/lib/node_modules/@openai/codex",
    "/usr/local/lib/node_modules/@openai/codex",
    "/usr/local/lib/nodejs/*/lib/node_modules/@openai/codex",
):
    for candidate in glob.glob(str(Path(pattern).expanduser())):
        add_unique(package_roots, candidate)

for root_text in package_roots:
    root = Path(root_text)
    patterns = (
        root / "vendor" / "*" / "bin" / "codex",
        root / "vendor" / "*" / "codex" / "codex",
        root / "node_modules" / "@openai" / "codex-*" / "vendor" / "*" / "bin" / "codex",
        root / "node_modules" / "@openai" / "codex-*" / "vendor" / "*" / "codex" / "codex",
        root.parent / "codex-*" / "vendor" / "*" / "bin" / "codex",
        root.parent / "codex-*" / "vendor" / "*" / "codex" / "codex",
    )
    for pattern in patterns:
        for candidate in glob.glob(str(pattern)):
            native = native_binary(candidate)
            if native:
                add_unique(native_candidates, native)

if not native_candidates:
    searched = {"cli_candidates": cli_candidates, "package_roots": package_roots}
    raise SystemExit(
        "could not locate the native Codex binary; searched "
        + json.dumps(searched, ensure_ascii=False, separators=(",", ":"))
    )

native = Path(native_candidates[0])
try:
    version_result = subprocess.run(
        [str(native), "--version"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=10,
        check=False,
    )
    codex_version = version_result.stdout.strip() or None
except (OSError, subprocess.SubprocessError):
    codex_version = None

metadata = {
    "backend": "codex-native",
    "codex_version": codex_version,
    "native_codex_path": str(native),
    "resolution": "native-discovery",
}
verify_guard()
print(METADATA_PREFIX + json.dumps(metadata, separators=(",", ":")), file=sys.stderr, flush=True)
os.execve(str(native), ["apply_patch"], os.environ.copy())
'''.strip()


REMOTE_LAUNCH_TMUX_PROGRAM = r'''
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import uuid

spec = json.load(sys.stdin)
session = spec["session"]
workdir = Path(spec["workdir"])
command = spec["command"]
log_path = Path(spec["log_path"])
append_log = spec["append_log"]

if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", session):
    raise SystemExit("invalid tmux session name")
if not workdir.is_absolute() or not workdir.is_dir():
    raise SystemExit(f"workdir is not an absolute directory: {workdir}")
if not log_path.is_absolute():
    raise SystemExit(f"log_path is not absolute: {log_path}")
if not shutil.which("tmux"):
    raise SystemExit("tmux is not installed on the remote host")
if not shutil.which("tee"):
    raise SystemExit("tee is not installed on the remote host")

existing = subprocess.run(
    ["tmux", "has-session", "-t", f"={session}"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    check=False,
)
if existing.returncode == 0:
    raise SystemExit(f"tmux session already exists: {session}")
if log_path.exists() and not append_log:
    raise SystemExit(f"log already exists; choose a new path or set append_log=true: {log_path}")

log_path.parent.mkdir(parents=True, exist_ok=True)
status_path = Path(str(log_path) + ".remote-fleet-status.json")
previous_status = None
if status_path.exists():
    if not append_log:
        raise SystemExit(f"status file already exists: {status_path}")
    try:
        previous_status = status_path.read_bytes()
    except OSError as exc:
        raise SystemExit(f"cannot preserve existing status file: {exc}")
    status_path.unlink()


def restore_previous_status():
    if previous_status is None:
        return
    restore_tmp = Path(f"{status_path}.{os.getpid()}.restore")
    try:
        restore_tmp.write_bytes(previous_status)
        os.replace(restore_tmp, status_path)
    except OSError:
        try:
            restore_tmp.unlink()
        except OSError:
            pass

started_at = datetime.now(timezone.utc).isoformat()
job_id = f"{session}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex[:8]}"
command_sha256 = hashlib.sha256(command.encode("utf-8")).hexdigest()

# orca: optional structured-log follower under <log>.orca/ (docs/logging.md).
# It follows the log file rather than joining the pipe, so PIPESTATUS indices
# and the job's stdout are untouched; upstream ignores the directory.
orca_log = spec.get("orca_log")
orca_dir = Path(str(log_path) + ".orca")
orca_dir_created = False
logger_start = ""
logger_stop = ""
if orca_log:
    import getpass
    import socket

    orca_dir_created = not orca_dir.exists()
    try:
        orca_dir.mkdir(parents=True, exist_ok=True)
        (orca_dir / "logger.py").write_text(orca_log["source"], encoding="utf-8")
        try:
            (orca_dir / ".stop").unlink()
        except FileNotFoundError:
            pass
        git = subprocess.run(
            ["git", "-C", str(workdir), "rev-parse", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        ) if shutil.which("git") else None
        meta = {
            "command": command,
            "command_sha256": command_sha256,
            "git_sha": git.stdout.strip() if git is not None and git.returncode == 0 else None,
            "host": socket.gethostname(),
            "job_id": job_id,
            "log_path": str(log_path),
            "session": session,
            "started_at": started_at,
            "user": getpass.getuser(),
            "workdir": str(workdir),
        }
        meta_tmp = orca_dir / f".meta.{os.getpid()}.tmp"
        meta_tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(meta_tmp, orca_dir / "meta.json")
    except OSError as exc:
        restore_previous_status()
        if orca_dir_created:
            shutil.rmtree(str(orca_dir), ignore_errors=True)
        raise SystemExit(f"cannot prepare structured log directory {orca_dir}: {exc}")
    start_offset = log_path.stat().st_size if log_path.exists() else 0
    logger_command = shlex.join([
        "python3", str(orca_dir / "logger.py"), str(log_path), str(orca_dir), str(start_offset),
        str(int(orca_log["tail_lines"])), str(int(orca_log["rotate_bytes"])), str(int(orca_log["keep_segments"])),
    ])
    logger_start = (
        f"{logger_command} >>{shlex.quote(str(orca_dir / 'logger.err'))} 2>&1 </dev/null &\n"
        "orca_logger=$!\n"
    )
    # A stop file, not a signal: a fast job can finish before the follower has
    # installed its handlers. Give it 30s to drain, then make sure it is gone.
    logger_stop = (
        f": > {shlex.quote(str(orca_dir / '.stop'))}\n"
        'for _ in $(seq 300); do kill -0 "$orca_logger" 2>/dev/null || break; sleep 0.1; done\n'
        'kill -KILL "$orca_logger" 2>/dev/null\n'
        'wait "$orca_logger" 2>/dev/null\n'
    )

    _restore_status_only = restore_previous_status

    def restore_previous_status():
        _restore_status_only()
        if orca_dir_created:
            shutil.rmtree(str(orca_dir), ignore_errors=True)

wrapper = f"""
set -o pipefail
{logger_start}if ! printf '[remote-fleet] started_at=%s job_id=%s session=%s workdir=%s\\n' \
  {shlex.quote(started_at)} {shlex.quote(job_id)} {shlex.quote(session)} {shlex.quote(str(workdir))} \
  | tee -a -- {shlex.quote(str(log_path))}; then
  exit 125
fi
bash -lc {shlex.quote(command)} 2>&1 | tee -a -- {shlex.quote(str(log_path))}
command_status=${{PIPESTATUS[0]}}
tee_status=${{PIPESTATUS[1]}}
if [ "$tee_status" -ne 0 ]; then job_status=125; else job_status="$command_status"; fi
finished_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)
printf '[remote-fleet] finished_at=%s exit_code=%s\\n' "$finished_at" "$job_status" \
  | tee -a -- {shlex.quote(str(log_path))}
status_tmp={shlex.quote(str(status_path))}.$$
printf '{{"exit_code":%s,"finished_at":"%s","job_id":"%s","session":"%s"}}\\n' \
  "$job_status" "$finished_at" {shlex.quote(job_id)} {shlex.quote(session)} > "$status_tmp"
mv -f -- "$status_tmp" {shlex.quote(str(status_path))}
{logger_stop}exit "$job_status"
""".strip()

shell_command = shlex.join(["bash", "-lc", wrapper])

registry_dir = Path.home() / ".local" / "state" / "remote-fleet" / "jobs"
registry_dir.mkdir(parents=True, exist_ok=True)
record = {
    "command_sha256": command_sha256,
    "job_id": job_id,
    "log_path": str(log_path),
    "registered": False,
    "session": session,
    "started_at": started_at,
    "status_path": str(status_path),
    "workdir": str(workdir),
}
record_path = registry_dir / f"{job_id}.json"


def write_record():
    record_tmp = registry_dir / f".{job_id}.{os.getpid()}.tmp"
    record_tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(record_tmp, record_path)


# A pending record is cheap and makes launch failures recoverable.
write_record()
launch = subprocess.run(
    ["tmux", "new-session", "-d", "-s", session, "-c", str(workdir), shell_command],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    check=False,
)
if launch.returncode != 0:
    try:
        record_path.unlink()
    except OSError:
        pass
    restore_previous_status()
    raise SystemExit(launch.stderr.strip() or f"tmux launch failed with exit code {launch.returncode}")

# Bare session target: tmux 3.2a's options commands (set-option/show-options)
# reject the '=' exact-match prefix that has-session/kill-session accept.
tagged = subprocess.run(
    ["tmux", "set-option", "-t", session, "@remote_fleet_job_id", job_id],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
    check=False,
)
if tagged.returncode != 0:
    subprocess.run(
        ["tmux", "kill-session", "-t", f"={session}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    try:
        record_path.unlink()
    except OSError:
        pass
    restore_previous_status()
    raise SystemExit(
        tagged.stderr.strip()
        or f"failed to tag tmux job identity; session rolled back with exit code {tagged.returncode}"
    )

record["registered"] = True
try:
    write_record()
except OSError as exc:
    # Never leave a running job that structured status/stop cannot identify.
    subprocess.run(
        ["tmux", "kill-session", "-t", f"={session}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    try:
        record_path.unlink()
    except OSError:
        pass
    restore_previous_status()
    raise SystemExit(f"failed to register remote-fleet job; session rolled back: {exc}")

pane = subprocess.run(
    [
        "tmux",
        "list-panes",
        "-t",
        f"={session}",
        "-F",
        "#{pane_pid}\t#{pane_current_command}",
    ],
    stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL,
    text=True,
    check=False,
)
pane_fields = pane.stdout.rstrip("\n").split("\t") if pane.returncode == 0 else []
result = {
    "active": pane.returncode == 0,
    "command_sha256": command_sha256,
    "job_id": job_id,
    "log_path": str(log_path),
    "orca_dir": str(orca_dir) if orca_log else None,
    "pane_command": pane_fields[1] if len(pane_fields) > 1 else None,
    "pane_pid": int(pane_fields[0]) if pane_fields and pane_fields[0].isdigit() else None,
    "record_path": str(record_path),
    "registered": True,
    "session": session,
    "started_at": started_at,
    "status_path": str(status_path),
    "workdir": str(workdir),
}
print(json.dumps(result, ensure_ascii=False))
'''.strip()


REMOTE_STATUS_PROGRAM = r'''
import csv
from datetime import datetime, timezone
import heapq
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import sys

requested_path = Path(sys.argv[1]) if sys.argv[1] else Path.home()
session_filter = sys.argv[2] or None
tail_lines = int(sys.argv[3])
job_limit = int(sys.argv[4])
if not requested_path.is_absolute() or not requested_path.exists():
    raise SystemExit(f"status path does not exist or is not absolute: {requested_path}")


def run(args, timeout=15):
    try:
        return subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def iso_from_epoch(value):
    try:
        return datetime.fromtimestamp(float(value), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


memory = None
meminfo = Path("/proc/meminfo")
if meminfo.is_file():
    values = {}
    for line in meminfo.read_text(encoding="utf-8", errors="replace").splitlines():
        key, separator, rest = line.partition(":")
        if separator:
            fields = rest.strip().split()
            if fields and fields[0].isdigit():
                values[key] = int(fields[0]) * 1024
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    memory = {
        "available_bytes": available,
        "total_bytes": total,
        "used_bytes": total - available if total is not None and available is not None else None,
    }

uptime_seconds = None
try:
    uptime_seconds = float(Path("/proc/uptime").read_text().split()[0])
except (OSError, ValueError, IndexError):
    pass

disk = shutil.disk_usage(requested_path)
sessions = []
active_session_names = set()
session_created_epochs = {}
session_job_ids = {}
if shutil.which("tmux"):
    listed = run(
        [
            "tmux",
            "list-sessions",
            "-F",
            "#{session_name}\x1f#{session_created}\x1f#{session_attached}\x1f#{session_windows}\x1f#{@remote_fleet_job_id}",
        ]
    )
    if listed and listed.returncode == 0:
        for line in listed.stdout.splitlines():
            fields = line.split("\x1f")
            if len(fields) != 5:
                continue
            name = fields[0]
            if session_filter and name != session_filter:
                continue
            active_session_names.add(name)
            try:
                session_created_epochs[name] = float(fields[1])
            except ValueError:
                pass
            session_job_ids[name] = fields[4] or None
            sessions.append(
                {
                    "attached": fields[2] != "0",
                    "created_at": iso_from_epoch(fields[1]),
                    "name": name,
                    "panes": [],
                    "remote_fleet_job_id": fields[4] or None,
                    "windows": int(fields[3]) if fields[3].isdigit() else None,
                }
            )
        panes = run(
            [
                "tmux",
                "list-panes",
                "-a",
                "-F",
                "#{session_name}\x1f#{window_index}\x1f#{pane_index}\x1f#{pane_pid}\x1f#{pane_current_command}\x1f#{pane_dead}\x1f#{pane_dead_status}",
            ]
        )
        by_name = {item["name"]: item for item in sessions}
        if panes and panes.returncode == 0:
            for line in panes.stdout.splitlines():
                fields = line.split("\x1f")
                if len(fields) != 7 or fields[0] not in by_name:
                    continue
                by_name[fields[0]]["panes"].append(
                    {
                        "command": fields[4],
                        "dead": fields[5] == "1",
                        "dead_status": int(fields[6]) if fields[6].lstrip("-").isdigit() else None,
                        "index": int(fields[2]) if fields[2].isdigit() else None,
                        "pid": int(fields[3]) if fields[3].isdigit() else None,
                        "window": int(fields[1]) if fields[1].isdigit() else None,
                    }
                )

gpus = []
gpu_error = None
if shutil.which("nvidia-smi"):
    gpu_result = run(
        [
            "nvidia-smi",
            "--query-gpu=index,name,uuid,memory.total,memory.used,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits",
        ],
        timeout=20,
    )
    if gpu_result and gpu_result.returncode == 0:
        for row in csv.reader(io.StringIO(gpu_result.stdout)):
            if len(row) != 7:
                continue
            cleaned = [item.strip() for item in row]
            gpus.append(
                {
                    "index": int(cleaned[0]) if cleaned[0].isdigit() else cleaned[0],
                    "memory_total_mib": int(cleaned[3]) if cleaned[3].isdigit() else None,
                    "memory_used_mib": int(cleaned[4]) if cleaned[4].isdigit() else None,
                    "name": cleaned[1],
                    "temperature_c": int(cleaned[6]) if cleaned[6].isdigit() else None,
                    "utilization_percent": int(cleaned[5]) if cleaned[5].isdigit() else None,
                    "uuid": cleaned[2],
                }
            )
    elif gpu_result:
        gpu_error = gpu_result.stderr.strip() or f"nvidia-smi exited {gpu_result.returncode}"


per_job_log_bytes = min(65536, max(4096, 1_000_000 // max(job_limit, 1)))


def tail_text(path, lines):
    if lines <= 0 or not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            handle.seek(0, 2)
            size = handle.tell()
            handle.seek(max(0, size - per_job_log_bytes))
            payload = handle.read(per_job_log_bytes)
    except OSError:
        return None
    return b"".join(payload.splitlines(keepends=True)[-lines:]).decode(
        "utf-8", errors="replace"
    )


def job_tail(log_path, lines):
    """orca: prefer the normalized <log>.orca/tail.ring (KB-sized) over the raw log."""
    ring = Path(str(log_path) + ".orca") / "tail.ring"
    if lines > 0 and ring.is_file():
        text = tail_text(ring, lines)
        if text is not None:
            return text, "ring"
    return tail_text(log_path, lines), "log"


jobs = []
registry_dir = Path.home() / ".local" / "state" / "remote-fleet" / "jobs"
record_paths = []
registry_records_total = 0
if registry_dir.is_dir():
    record_pattern = f"{session_filter}-*.json" if session_filter else "*.json"
    # orca: "exp-*" also matches session "exp-2"; drop those before job_limit.
    exact_name = (
        re.compile(re.escape(session_filter) + r"-\d{8}T\d{6}Z-[0-9a-f]{8}\.json")
        if session_filter
        else None
    )
    newest = []
    try:
        for item in registry_dir.glob(record_pattern):
            if exact_name and not exact_name.fullmatch(item.name):
                continue
            registry_records_total += 1
            # Tolerate records vanishing mid-scan instead of dropping the whole list.
            try:
                candidate = (item.stat().st_mtime_ns, str(item))
            except OSError:
                continue
            if len(newest) < job_limit:
                heapq.heappush(newest, candidate)
            elif candidate > newest[0]:
                heapq.heapreplace(newest, candidate)
    except OSError:
        pass
    record_paths = [Path(item) for _, item in sorted(newest, reverse=True)]
for record_path in record_paths:
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    if not isinstance(record, dict):
        continue
    session = record.get("session")
    if session_filter and session != session_filter:
        continue
    status_path_text = record.get("status_path")
    log_path_text = record.get("log_path")
    status_path = (
        Path(status_path_text)
        if isinstance(status_path_text, str) and status_path_text.startswith("/")
        else None
    )
    log_path = (
        Path(log_path_text)
        if isinstance(log_path_text, str) and log_path_text.startswith("/")
        else None
    )
    exit_code = None
    finished_at = None
    stopped_by_remote_fleet = False
    if status_path is not None and status_path.is_file():
        try:
            status_payload = json.loads(status_path.read_text(encoding="utf-8"))
            exit_code = status_payload.get("exit_code")
            finished_at = status_payload.get("finished_at")
            stopped_by_remote_fleet = bool(status_payload.get("stopped_by_remote_fleet"))
        except (OSError, json.JSONDecodeError, AttributeError):
            pass
    live_job_id = session_job_ids.get(session)
    record_job_id = record.get("job_id")
    active = (
        session in active_session_names
        and exit_code is None
        and isinstance(record_job_id, str)
        and live_job_id == record_job_id
    )
    legacy_active = (
        session in active_session_names
        and exit_code is None
        and live_job_id is None
    )
    if legacy_active:
        # Guard against session-name reuse: the live session only belongs to this
        # job if it was created around the job's own start time.
        created_epoch = session_created_epochs.get(session)
        started_epoch = None
        started_at_text = record.get("started_at")
        if isinstance(started_at_text, str):
            try:
                started_epoch = datetime.fromisoformat(started_at_text).timestamp()
            except ValueError:
                started_epoch = None
        if (
            created_epoch is not None
            and started_epoch is not None
            and abs(created_epoch - started_epoch) > 120
        ):
            legacy_active = False
    active = active or legacy_active
    if stopped_by_remote_fleet:
        state = "stopped"
    elif exit_code == 0:
        state = "succeeded"
    elif exit_code is not None:
        state = "failed"
    elif active:
        state = "running"
    else:
        state = "unknown"
    log_stat = None
    try:
        stat = log_path.stat() if log_path is not None else None
        if stat is None:
            raise OSError
        log_stat = {
            "modified_at": iso_from_epoch(stat.st_mtime),
            "size_bytes": stat.st_size,
        }
    except OSError:
        pass
    log_tail, log_tail_source = (
        job_tail(log_path, tail_lines) if log_path is not None else (None, None)
    )
    jobs.append(
        {
            **record,
            "active": active,
            "exit_code": exit_code,
            "finished_at": finished_at,
            "log": log_stat,
            "log_tail": log_tail,
            "log_tail_source": log_tail_source,
            "record_path": str(record_path),
            "state": state,
            "stopped_by_remote_fleet": stopped_by_remote_fleet,
        }
    )

result = {
    "cpu_count": os.cpu_count(),
    "disk": {
        "free_bytes": disk.free,
        "path": str(requested_path),
        "total_bytes": disk.total,
        "used_bytes": disk.used,
    },
    "gpu_error": gpu_error,
    "gpus": gpus,
    "hostname": socket.gethostname(),
    "jobs": jobs,
    "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
    "memory": memory,
    "platform": platform.platform(),
    "registry_records_total": registry_records_total,
    "session_filter": session_filter,
    "timestamp": datetime.now(timezone.utc).isoformat(),
    "tmux_available": shutil.which("tmux") is not None,
    "tmux_sessions": sessions,
    "uptime_seconds": uptime_seconds,
}
print(json.dumps(result, ensure_ascii=False))
'''.strip()


REMOTE_STOP_PROGRAM = r'''
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

session = sys.argv[1]
grace_seconds = int(sys.argv[2])
if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", session):
    raise SystemExit("invalid tmux session name")

registry_dir = Path.home() / ".local" / "state" / "remote-fleet" / "jobs"
records = []
if registry_dir.is_dir():
    try:
        candidates = list(registry_dir.glob(session + "-*.json"))
    except OSError:
        candidates = []
    for item in candidates:
        try:
            parsed = json.loads(item.read_text(encoding="utf-8"))
            mtime = item.stat().st_mtime
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(parsed, dict) or parsed.get("session") != session:
            continue
        records.append((mtime, item, parsed))
if not records:
    raise SystemExit(
        f"refusing to stop {session!r}: no remote-fleet job record found; "
        "only sessions launched by remote_launch_tmux can be stopped"
    )
if not shutil.which("tmux"):
    raise SystemExit("tmux is not installed on the remote host")


def session_alive():
    probe = subprocess.run(
        ["tmux", "has-session", "-t", f"={session}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return probe.returncode == 0


was_running = session_alive()
live_job_id = None
if was_running:
    # Bare session target: tmux 3.2a's show-options rejects the '=' prefix.
    identity = subprocess.run(
        [
            "tmux",
            "show-options",
            "-v",
            "-t",
            session,
            "@remote_fleet_job_id",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    live_job_id = identity.stdout.strip() if identity.returncode == 0 else None
    if not live_job_id:
        raise SystemExit(
            f"refusing to stop {session!r}: live session has no remote-fleet job identity"
        )
    matching = [entry for entry in records if entry[2].get("job_id") == live_job_id]
    if not matching:
        raise SystemExit(
            f"refusing to stop {session!r}: live tmux identity does not match its job records"
        )
    _, record_path, record = max(matching, key=lambda entry: entry[0])
else:
    _, record_path, record = max(records, key=lambda entry: entry[0])

hard_killed = False
sigkilled_pgids = []
if was_running:
    panes = subprocess.run(
        ["tmux", "list-panes", "-t", f"={session}", "-F", "#{pane_pid}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    pane_pids = (
        [int(field) for field in panes.stdout.split() if field.isdigit()]
        if panes.returncode == 0
        else []
    )
    pane_pgids = set()
    for pid in pane_pids:
        try:
            pane_pgids.add(os.getpgid(pid))
        except (OSError, ProcessLookupError):
            pass
    for pgid in pane_pgids:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    deadline = time.monotonic() + grace_seconds
    while session_alive() and time.monotonic() < deadline:
        time.sleep(0.5)
    if session_alive():
        subprocess.run(
            ["tmux", "kill-session", "-t", f"={session}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        hard_killed = True
        deadline = time.monotonic() + 5
        while session_alive() and time.monotonic() < deadline:
            time.sleep(0.2)
    # orca: kill-session only sends SIGHUP; finish off groups that ignored it so
    # a "stopped" job never keeps holding a GPU. Give well-behaved members (e.g.
    # the structured-log follower) a moment to finish exiting first.
    def group_alive(pgid):
        try:
            os.killpg(pgid, 0)
            return True
        except (OSError, ProcessLookupError):
            return False

    settle_deadline = time.monotonic() + max(1, min(grace_seconds, 5))
    while any(group_alive(pgid) for pgid in pane_pgids) and time.monotonic() < settle_deadline:
        time.sleep(0.1)
    for pgid in sorted(pane_pgids):
        try:
            os.killpg(pgid, 0)
        except (OSError, ProcessLookupError):
            continue
        try:
            os.killpg(pgid, signal.SIGKILL)
            sigkilled_pgids.append(pgid)
        except (OSError, ProcessLookupError):
            pass

session_stopped = was_running and not session_alive()
status_written = False
status_path_text = record.get("status_path") or ""
if session_stopped and status_path_text:
    status_path = Path(status_path_text)
    if status_path.is_absolute() and not status_path.exists():
        # The tee wrapper died with the pane, so record completion here to keep
        # the job discoverable through remote_status.
        payload = {
            "exit_code": 143,
            "finished_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "job_id": record.get("job_id"),
            "session": session,
            "stopped_by_remote_fleet": True,
        }
        try:
            tmp_path = Path(f"{status_path}.{os.getpid()}")
            tmp_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            os.replace(tmp_path, status_path)
            status_written = True
        except OSError:
            status_written = False

result = {
    "grace_seconds": grace_seconds,
    "hard_killed": hard_killed,
    "job_id": record.get("job_id"),
    "live_job_id": live_job_id,
    "record_path": str(record_path),
    "session": session,
    "session_stopped": session_stopped,
    "sigkilled_pgids": sigkilled_pgids,
    "status_written": status_written,
    "was_running": was_running,
}
print(json.dumps(result, ensure_ascii=False))
'''.strip()


COMPACT_GPU_KEYS = ("index", "memory_used_mib", "memory_total_mib", "utilization_percent")
COMPACT_JOB_DROP = frozenset({"command_sha256", "record_path", "status_path", "registered", "workdir"})
READ_ECHO_KEYS = frozenset({"start_line", "end_line", "offset_bytes"})


def _compact_status(
    payload: dict[str, Any], *, session_filter: bool, include_gpus: bool
) -> dict[str, Any]:
    """Trim a remote status payload to what a monitoring call actually needs.

    Every field returned here lands in the model's context for the rest of the
    session, so static host facts, GPU UUIDs, and registry bookkeeping are left
    to detail="full".
    """
    out: dict[str, Any] = {}
    for key in ("hostname", "timestamp", "load_average"):
        if payload.get(key) is not None:
            out[key] = payload[key]
    memory = payload.get("memory")
    if isinstance(memory, dict):
        out["memory"] = {k: memory.get(k) for k in ("available_bytes", "total_bytes")}
    disk = payload.get("disk")
    if isinstance(disk, dict):
        out["disk"] = {k: disk.get(k) for k in ("path", "free_bytes", "total_bytes")}
    gpus = [g for g in (payload.get("gpus") or []) if isinstance(g, dict)]
    if gpus:
        names = {g.get("name") for g in gpus}
        if len(names) == 1:
            out["gpu_model"] = next(iter(names))
        if session_filter and not include_gpus:
            out["gpu_utilization_percent"] = [g.get("utilization_percent") for g in gpus]
            out["gpu_memory_used_mib"] = [g.get("memory_used_mib") for g in gpus]
        else:
            out["gpus"] = [{k: g.get(k) for k in COMPACT_GPU_KEYS} for g in gpus]
    if payload.get("gpu_error"):
        out["gpu_error"] = payload["gpu_error"]
    if payload.get("tmux_available") is False:
        out["tmux_available"] = False
    sessions_out = []
    for item in payload.get("tmux_sessions") or []:
        if not isinstance(item, dict):
            continue
        entry: dict[str, Any] = {
            "name": item.get("name"),
            "created_at": item.get("created_at"),
            "attached": item.get("attached"),
        }
        if item.get("remote_fleet_job_id"):
            entry["remote_fleet_job_id"] = item["remote_fleet_job_id"]
        panes = []
        for pane in item.get("panes") or []:
            if not isinstance(pane, dict):
                continue
            slim = {"pid": pane.get("pid"), "command": pane.get("command")}
            if pane.get("dead"):
                slim["dead"] = True
                slim["dead_status"] = pane.get("dead_status")
            panes.append(slim)
        entry["panes"] = panes
        sessions_out.append(entry)
    out["tmux_sessions"] = sessions_out
    jobs_out = []
    for job in payload.get("jobs") or []:
        if not isinstance(job, dict):
            continue
        slim_job = {
            k: v
            for k, v in job.items()
            if k not in COMPACT_JOB_DROP and v is not None and not (k == "stopped_by_remote_fleet" and v is False)
        }
        jobs_out.append(slim_job)
    out["jobs"] = jobs_out
    return out


def _slim_read_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Drop echoed inputs and null/false cursors; absent means false."""
    slim = {
        k: v
        for k, v in payload.items()
        if k not in READ_ECHO_KEYS and v is not None and (v is not False or k == "eof_reached")
    }
    if slim.get("snapshot_consistent") is True:
        del slim["snapshot_consistent"]
    return slim


def remote_read_file(
    arguments: dict[str, Any], cancel_token: CancelToken | None = None
) -> dict[str, Any]:
    host = validate_host(arguments.get("host"))
    path = validate_absolute_path(arguments.get("path"), "path")
    start_line = bounded_int(arguments.get("start_line"), "start_line", 1, 1, 1_000_000_000)
    end_line = bounded_int(arguments.get("end_line"), "end_line", 0, 0, 1_000_000_000)
    if end_line and end_line < start_line:
        raise ToolError("end_line must be zero or greater than or equal to start_line")
    max_bytes = bounded_int(arguments.get("max_bytes"), "max_bytes", 262_144, 1, MAX_READ_BYTES)
    offset_bytes = bounded_int(
        arguments.get("offset_bytes"), "offset_bytes", 0, 0, 1_000_000_000_000_000
    )
    include_sha256 = arguments.get("include_sha256", False)
    if not isinstance(include_sha256, bool):
        raise ToolError("include_sha256 must be a boolean")
    # orca: hash small files automatically (see REMOTE_READ_PROGRAM "auto").
    auto_sha256 = arguments.get("orca_auto_sha256", False) is True and not include_sha256

    command = "python3 -c {} {} {} {} {} {} {}".format(
        shlex.quote(REMOTE_READ_PROGRAM),
        shlex.quote(path),
        start_line,
        end_line,
        max_bytes,
        offset_bytes,
        "auto" if auto_sha256 else 1 if include_sha256 else 0,
    )
    result = run_ssh(
        host,
        command,
        timeout_seconds=180 if include_sha256 else 45,
        max_output_chars=MAX_READ_BYTES * 8,
        cancel_token=cancel_token,
    )
    if result["timed_out"]:
        raise ToolError(f"reading {path!r} on {host!r} timed out")
    if result["truncated"]:
        raise ToolError("remote read payload exceeded the transport limit; lower max_bytes")
    if result["exit_code"] != 0:
        raise ToolError(result["stderr"].strip() or f"remote read failed with exit code {result['exit_code']}")
    try:
        payload = json.loads(result["stdout"])
    except json.JSONDecodeError as exc:
        raise ToolError(f"remote read returned invalid JSON: {exc}") from exc
    payload = _slim_read_payload(payload)
    payload["host"] = host
    return payload


def remote_run(
    arguments: dict[str, Any], cancel_token: CancelToken | None = None
) -> dict[str, Any]:
    host = validate_host(arguments.get("host"))
    workdir = validate_absolute_path(arguments.get("workdir"), "workdir")
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip() or "\x00" in command:
        raise ToolError("command must be a non-empty string")
    command_bytes = command.encode("utf-8")
    if len(command_bytes) > MAX_COMMAND_BYTES:
        raise ToolError(f"command exceeds the {MAX_COMMAND_BYTES}-byte limit")
    timeout_seconds = bounded_int(
        arguments.get("timeout_seconds"), "timeout_seconds", 60, 1, MAX_TIMEOUT_SECONDS
    )
    max_output_chars = bounded_int(
        arguments.get("max_output_chars"), "max_output_chars", 100_000, 1_000, MAX_OUTPUT_CHARS
    )

    remote_command = "python3 -c {} {} {}".format(
        shlex.quote(REMOTE_RUN_PROGRAM),
        shlex.quote(workdir),
        timeout_seconds,
    )
    result = run_ssh(
        host,
        remote_command,
        stdin=command_bytes,
        timeout_seconds=timeout_seconds + 15,
        max_output_chars=max_output_chars,
        cancel_token=cancel_token,
    )
    remote_timeout_marker = f"remote-fleet: command timed out after {timeout_seconds}s"
    if result["exit_code"] == 124 and remote_timeout_marker in result["stderr"]:
        result["timed_out"] = True
    result.update(
        {
            "command_sha256": hashlib.sha256(command_bytes).hexdigest(),
            "host": host,
            "workdir": workdir,
        }
    )
    return result


def remote_apply_patch(
    arguments: dict[str, Any], cancel_token: CancelToken | None = None
) -> dict[str, Any]:
    host = validate_host(arguments.get("host"))
    workdir = validate_absolute_path(arguments.get("workdir"), "workdir")
    patch_bytes, touched_paths = validate_codex_patch(arguments.get("patch"))
    expected_sha256 = validate_expected_sha256(arguments.get("expected_sha256"), touched_paths)
    if arguments.get("check_only") is True:
        raise ToolError("the native Codex apply_patch runtime has no dry-run mode; read files before applying")
    if "check_only" in arguments and arguments["check_only"] not in (None, False):
        raise ToolError("check_only must be false when using the native Codex apply_patch runtime")

    guard_bytes = json.dumps(
        {"expected_sha256": expected_sha256, "paths": touched_paths},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(guard_bytes) > MAX_PATCH_GUARD_BYTES:
        raise ToolError(f"patch path metadata exceeds the {MAX_PATCH_GUARD_BYTES}-byte limit")
    guard = base64.urlsafe_b64encode(guard_bytes).decode("ascii")
    command = "python3 -c {} {} {}".format(
        shlex.quote(REMOTE_CODEX_APPLY_PROGRAM),
        shlex.quote(workdir),
        shlex.quote(guard),
    )
    result = run_ssh(
        host,
        command,
        stdin=patch_bytes,
        timeout_seconds=120,
        max_output_chars=MAX_OUTPUT_CHARS,
        cancel_token=cancel_token,
    )
    metadata: dict[str, Any] = {}
    visible_stderr: list[str] = []
    for line in result["stderr"].splitlines(keepends=True):
        if line.startswith(CODEX_NATIVE_METADATA_PREFIX) and not metadata:
            raw_metadata = line[len(CODEX_NATIVE_METADATA_PREFIX):].strip()
            try:
                parsed_metadata = json.loads(raw_metadata)
            except json.JSONDecodeError:
                visible_stderr.append(line)
            else:
                if isinstance(parsed_metadata, dict):
                    metadata = parsed_metadata
                else:
                    visible_stderr.append(line)
        else:
            visible_stderr.append(line)
    result["stderr"] = "".join(visible_stderr)
    result.update(
        {
            "applied": result["exit_code"] == 0 and not result["timed_out"],
            "backend": metadata.get("backend", "codex-native"),
            "codex_version": metadata.get("codex_version"),
            "host": host,
            "native_codex_path": metadata.get("native_codex_path"),
            "patch_sha256": hashlib.sha256(patch_bytes).hexdigest(),
            "resolution": metadata.get("resolution"),
            "touched_paths": touched_paths,
            "workdir": workdir,
        }
    )
    return result


def remote_launch_tmux(
    arguments: dict[str, Any], cancel_token: CancelToken | None = None
) -> dict[str, Any]:
    host = validate_host(arguments.get("host"))
    workdir = validate_absolute_path(arguments.get("workdir"), "workdir")
    session = validate_session(arguments.get("session"))
    if not LAUNCH_SESSION_PATTERN.fullmatch(session):
        raise ToolError("session for a new launch may not contain '.' (tmux renames it to '_')")
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip() or "\x00" in command:
        raise ToolError("command must be a non-empty string")
    if len(command.encode("utf-8")) > MAX_COMMAND_BYTES:
        raise ToolError(f"command exceeds the {MAX_COMMAND_BYTES}-byte limit")
    log_path = validate_absolute_path(arguments.get("log_path"), "log_path")
    append_log = arguments.get("append_log", False)
    if not isinstance(append_log, bool):
        raise ToolError("append_log must be a boolean")

    spec = {
        "append_log": append_log,
        "command": command,
        "log_path": log_path,
        "session": session,
        "workdir": workdir,
    }
    orca_log = arguments.get("orca_log")
    if orca_log is not None:
        spec["orca_log"] = orca_ext.logger_spec(globals(), orca_log)
    remote_command = f"python3 -c {shlex.quote(REMOTE_LAUNCH_TMUX_PROGRAM)}"
    result = run_ssh(
        host,
        remote_command,
        stdin=json.dumps(spec, ensure_ascii=False).encode("utf-8"),
        timeout_seconds=45,
        max_output_chars=100_000,
        cancel_token=cancel_token,
    )
    if result["timed_out"]:
        raise ToolError(f"launching tmux session {session!r} on {host!r} timed out")
    if result["exit_code"] != 0:
        raise ToolError(result["stderr"].strip() or f"remote tmux launch failed with exit code {result['exit_code']}")
    try:
        payload = json.loads(result["stdout"])
    except json.JSONDecodeError as exc:
        raise ToolError(f"remote tmux launch returned invalid JSON: {exc}") from exc
    payload["host"] = host
    return payload


def remote_status(
    arguments: dict[str, Any], cancel_token: CancelToken | None = None
) -> dict[str, Any]:
    host = validate_host(arguments.get("host"))
    requested_path = arguments.get("workdir")
    if requested_path is None:
        requested_path = ""
    else:
        requested_path = validate_absolute_path(requested_path, "workdir")
    session = validate_session(arguments.get("session"), required=False) or ""
    log_tail_lines = bounded_int(
        arguments.get("log_tail_lines"), "log_tail_lines", 10, 0, 200
    )
    job_limit = bounded_int(arguments.get("job_limit"), "job_limit", 20, 1, 100)
    detail = arguments.get("detail", "compact")
    if detail not in ("compact", "full"):
        raise ToolError("detail must be 'compact' or 'full'")
    include_gpus = arguments.get("include_gpus", False)
    if not isinstance(include_gpus, bool):
        raise ToolError("include_gpus must be a boolean")

    remote_command = "python3 -c {} {} {} {} {}".format(
        shlex.quote(REMOTE_STATUS_PROGRAM),
        shlex.quote(requested_path),
        shlex.quote(session),
        log_tail_lines,
        job_limit,
    )
    result = run_ssh(
        host,
        remote_command,
        timeout_seconds=45,
        max_output_chars=MAX_READ_BYTES * 2,
        cancel_token=cancel_token,
    )
    if result["timed_out"]:
        raise ToolError(f"status probe on {host!r} timed out")
    if result["truncated"]:
        raise ToolError("remote status output exceeded the safety limit; reduce job_limit or log_tail_lines")
    if result["exit_code"] != 0:
        raise ToolError(result["stderr"].strip() or f"remote status failed with exit code {result['exit_code']}")
    try:
        payload = json.loads(result["stdout"])
    except json.JSONDecodeError as exc:
        raise ToolError(f"remote status returned invalid JSON: {exc}") from exc
    if detail == "compact":
        payload = _compact_status(payload, session_filter=bool(session), include_gpus=include_gpus)
    payload["host"] = host
    return payload


def remote_stop_job(
    arguments: dict[str, Any], cancel_token: CancelToken | None = None
) -> dict[str, Any]:
    host = validate_host(arguments.get("host"))
    session = validate_session(arguments.get("session"))
    grace_seconds = bounded_int(arguments.get("grace_seconds"), "grace_seconds", 10, 0, 60)

    remote_command = "python3 -c {} {} {}".format(
        shlex.quote(REMOTE_STOP_PROGRAM),
        shlex.quote(session),
        grace_seconds,
    )
    result = run_ssh(
        host,
        remote_command,
        timeout_seconds=grace_seconds + 30,
        max_output_chars=100_000,
        cancel_token=cancel_token,
    )
    if result["timed_out"]:
        raise ToolError(f"stopping session {session!r} on {host!r} timed out")
    if result["exit_code"] != 0:
        raise ToolError(
            result["stderr"].strip() or f"remote stop failed with exit code {result['exit_code']}"
        )
    try:
        payload = json.loads(result["stdout"])
    except json.JSONDecodeError as exc:
        raise ToolError(f"remote stop returned invalid JSON: {exc}") from exc
    payload["host"] = host
    return payload


TOOLS: list[dict[str, Any]] = [
    {
        "name": "remote_read_file",
        "description": (
            "Read a line slice of a remote UTF-8 text file, streamed remotely so start_line "
            "works anywhere in huge files. Returns content plus paging cursors "
            "(eof_reached, next_start_line, next_offset_bytes, total_lines); null/false "
            "fields are omitted."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "host": {"type": "string", "description": "Concrete alias from ~/.ssh/config."},
                "path": {"type": "string", "description": "Absolute remote file path."},
                "start_line": {
                    "type": "integer",
                    "minimum": 1,
                    "default": 1,
                    "description": "First line to return, counted from offset_bytes.",
                },
                "end_line": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": "Inclusive end line; 0 = until EOF or max_bytes.",
                },
                "max_bytes": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_READ_BYTES,
                    "default": 262_144,
                    "description": "Byte budget; continue with next_start_line.",
                },
                "offset_bytes": {
                    "type": "integer",
                    "minimum": 0,
                    "default": 0,
                    "description": "Byte offset to start at; line numbers restart there.",
                },
                "include_sha256": {
                    "type": "boolean",
                    "default": False,
                    "description": "Also hash the whole file (slow on huge files).",
                },
            },
            "required": ["host", "path"],
        },
    },
    {
        "name": "remote_run",
        "description": (
            "Run a bounded shell command in an absolute working directory on a trusted SSH host. "
            "Returns stdout, stderr, exit code, timeout state, and truncation state."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "host": {"type": "string", "description": "Concrete alias from ~/.ssh/config."},
                "workdir": {"type": "string", "description": "Absolute remote working directory."},
                "command": {"type": "string", "description": "Command interpreted by remote bash."},
                "timeout_seconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": MAX_TIMEOUT_SECONDS,
                    "default": 60,
                },
                "max_output_chars": {
                    "type": "integer",
                    "minimum": 1_000,
                    "maximum": MAX_OUTPUT_CHARS,
                    "default": 100_000,
                    "description": "Decoded-character budget shared by stdout and stderr.",
                },
            },
            "required": ["host", "workdir", "command"],
        },
    },
    {
        "name": "remote_apply_patch",
        "description": (
            "Apply a Codex Begin Patch document with the native Codex apply_patch runtime installed "
            "on a trusted SSH host. Read target files first because the native runtime has no dry run."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "host": {"type": "string", "description": "Concrete alias from ~/.ssh/config."},
                "workdir": {"type": "string", "description": "Absolute patch root directory."},
                "patch": {
                    "type": "string",
                    "description": "Codex patch (*** Begin Patch ... *** End Patch).",
                },
                "expected_sha256": {
                    "type": "object",
                    "description": (
                        "Optional per-path SHA-256 preconditions (null = must not exist); "
                        "use remote_read_file include_sha256 to obtain them."
                    ),
                    "additionalProperties": {
                        "anyOf": [
                            {"type": "string", "pattern": "^[0-9A-Fa-f]{64}$"},
                            {"type": "null"},
                        ]
                    },
                },
            },
            "required": ["host", "workdir", "patch"],
        },
    },
    {
        "name": "remote_launch_tmux",
        "description": (
            "Launch a registered long-running command in a detached tmux session with a tee "
            "log, job identity, exit code, and completion status."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "host": {"type": "string", "description": "Concrete alias from ~/.ssh/config."},
                "workdir": {"type": "string", "description": "Absolute remote working directory."},
                "session": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$",
                    "description": "Unique tmux session name for this job.",
                },
                "command": {"type": "string", "description": "Long-running command for remote bash."},
                "log_path": {
                    "type": "string",
                    "description": "Absolute tee log path; parent dirs are created.",
                },
                "append_log": {
                    "type": "boolean",
                    "default": False,
                    "description": "Allow appending to an existing log.",
                },
            },
            "required": ["host", "workdir", "session", "command", "log_path"],
        },
    },
    {
        "name": "remote_stop_job",
        "description": (
            "Stop a job launched by remote_launch_tmux: SIGTERM, wait grace_seconds, then kill "
            "the session; records the exit status. Refuses sessions without a fleet identity."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "host": {"type": "string", "description": "Concrete alias from ~/.ssh/config."},
                "session": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$",
                    "description": "Session of a fleet-launched job.",
                },
                "grace_seconds": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 60,
                    "default": 10,
                    "description": "Seconds after SIGTERM before the session is killed.",
                },
            },
            "required": ["host", "session"],
        },
    },
    {
        "name": "remote_status",
        "description": (
            "Host load, memory, disk, GPU, tmux sessions, and registered job status. Compact by "
            "default; with a session filter GPUs collapse to utilization/memory lists."
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "host": {"type": "string", "description": "Concrete alias from ~/.ssh/config."},
                "workdir": {
                    "type": "string",
                    "description": "Path whose disk usage to report (default: remote home).",
                },
                "session": {
                    "type": "string",
                    "pattern": "^[A-Za-z0-9][A-Za-z0-9_.-]{0,79}$",
                    "description": "Optional exact tmux session/job filter.",
                },
                "log_tail_lines": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 200,
                    "default": 10,
                },
                "job_limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 100,
                    "default": 20,
                },
                "detail": {
                    "type": "string",
                    "enum": ["compact", "full"],
                    "default": "compact",
                    "description": "compact omits GPU UUIDs, static host facts, and registry bookkeeping; full returns everything.",
                },
                "include_gpus": {
                    "type": "boolean",
                    "default": False,
                    "description": "With a session filter, keep per-GPU objects instead of utilization/memory lists.",
                },
            },
            "required": ["host"],
        },
    },
]


TOOL_HANDLERS = {
    "remote_read_file": remote_read_file,
    "remote_run": remote_run,
    "remote_apply_patch": remote_apply_patch,
    "remote_launch_tmux": remote_launch_tmux,
    "remote_stop_job": remote_stop_job,
    "remote_status": remote_status,
}
# orca: DSH-only tools (grep/ls/patch/rollback) reach the engine through
# cli-json only; TOOLS (the MCP surface) stays exactly upstream's.
TOOL_HANDLERS.update(orca_ext.install(globals()))


OPENCODE_SERVER_NAME = "rf"
# OpenCode 2.x: third-party tools are MCP servers; with codemode=false their tools
# are direct tools named <server>_<tool> and permission actions use the same name.
# (1.x custom tools/*.ts are no longer loaded, and 2.x plugin tools cannot ask for
# permission, so MCP is the only route with per-tool approval.)
OPENCODE_READ_ONLY_TOOLS = ("remote_read_file", "remote_status")


def opencode_tool_name(tool: str) -> str:
    """OpenCode 2.x exposes MCP tools as <server>_<tool>; other characters become '_'."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", f"{OPENCODE_SERVER_NAME}_{tool}")


def render_opencode_config() -> dict[str, Any]:
    """The opencode.jsonc fragment: server rf as direct tools plus per-tool permissions."""
    permission: dict[str, Any] = {
        opencode_tool_name(tool["name"]): "allow" if tool["name"] in OPENCODE_READ_ONLY_TOOLS else "ask"
        for tool in TOOLS
    }
    permission["bash"] = {"ssh *": "ask"}
    return {
        "permission": permission,
        "mcp": {
            "servers": {
                OPENCODE_SERVER_NAME: {
                    "type": "local",
                    "command": ["python3", os.path.abspath(__file__)],
                    "environment": {"PYTHONDONTWRITEBYTECODE": "1"},
                    "codemode": False,
                    # An integer here makes OpenCode drop the whole mcp block silently.
                    "timeout": {"execution": CHILD_TIMEOUT_SECONDS * 1000},
                }
            }
        },
    }


def _tool_call_response(
    message: dict[str, Any], cancel_token: CancelToken
) -> dict[str, Any] | None:
    params = message.get("params") or {}
    name = params.get("name")
    arguments = params.get("arguments")
    if arguments is None:
        arguments = {}
    handler = TOOL_HANDLERS.get(name)
    if handler is None:
        result = _error_result(f"unknown tool: {name}")
    elif not isinstance(arguments, dict):
        result = _error_result("tool arguments must be an object")
    else:
        try:
            result = _tool_result(_invoke_tool(name, handler, arguments, cancel_token))
        except ToolCancelled:
            # Cancelled requests must not receive a response.
            return None
        except ToolError as exc:
            result = _error_result(str(exc))
        except Exception as exc:  # Keep the MCP server alive on unexpected tool errors.
            print(f"unexpected tool error: {exc!r}", file=sys.stderr, flush=True)
            result = _error_result(f"unexpected remote-fleet error: {exc}")
    return {"jsonrpc": "2.0", "id": message.get("id"), "result": result}


def _execute_tool_call(message: dict[str, Any], token: CancelToken) -> None:
    request_id = message.get("id")
    try:
        response = None if token.cancelled else _tool_call_response(message, token)
    except Exception as exc:  # Never lose a response to a worker-thread crash.
        print(f"unexpected dispatch error: {exc!r}", file=sys.stderr, flush=True)
        response = {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": _error_result(f"unexpected remote-fleet error: {exc}"),
        }
    finally:
        with _INFLIGHT_LOCK:
            if _INFLIGHT.get(request_id) is token:
                _INFLIGHT.pop(request_id, None)
        _CALL_SLOTS.release()
    if response is None or token.cancelled:
        return
    _write_response(response)


def _queue_tool_call(message: dict[str, Any]) -> dict[str, Any] | None:
    request_id = message.get("id")
    if _SHUTTING_DOWN.is_set() or not _CALL_SLOTS.acquire(blocking=False):
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": _error_result(
                f"remote-fleet is busy; at most {MAX_PENDING_TOOL_CALLS} calls may be active or queued"
            ),
        }
    token = CancelToken()
    with _INFLIGHT_LOCK:
        if request_id in _INFLIGHT:
            _CALL_SLOTS.release()
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": _error_result(f"duplicate in-flight request id: {request_id!r}"),
            }
        _INFLIGHT[request_id] = token
    try:
        _TOOL_EXECUTOR.submit(_execute_tool_call, message, token)
    except RuntimeError as exc:
        with _INFLIGHT_LOCK:
            if _INFLIGHT.get(request_id) is token:
                _INFLIGHT.pop(request_id, None)
        _CALL_SLOTS.release()
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": _error_result(f"remote-fleet cannot queue the call: {exc}"),
        }
    return None


def _shutdown_tool_calls() -> None:
    _SHUTTING_DOWN.set()
    with _INFLIGHT_LOCK:
        tokens = list(_INFLIGHT.values())
    for token in tokens:
        token.cancel()
    # Queued calls observe cancellation before dispatch; active SSH processes are
    # killed by their tokens, so shutdown normally completes within a few seconds.
    _TOOL_EXECUTOR.shutdown(wait=True)


def handle_request(
    message: dict[str, Any], cancel_token: CancelToken | None = None
) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    params = message.get("params") or {}

    if method == "notifications/cancelled":
        _cancel_inflight(params.get("requestId"))
        return None
    if method == "notifications/initialized":
        return None
    if request_id is None:
        return None
    if method == "initialize":
        requested = params.get("protocolVersion")
        negotiated = (
            requested if requested in SUPPORTED_PROTOCOL_VERSIONS else LATEST_PROTOCOL_VERSION
        )
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": negotiated,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "Use only concrete SSH aliases configured on this host. Read before patching. "
                    "remote_apply_patch uses the native Codex runtime installed on the remote host. "
                    "remote_stop_job stops only jobs started by remote_launch_tmux."
                ),
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": request_id, "result": {"tools": TOOLS}}
    if method == "tools/call":
        return _tool_call_response(message, cancel_token or CancelToken())
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32601, "message": f"method not found: {method}"},
    }


def main() -> int:
    try:
        for line in sys.stdin:
            if not line.strip():
                continue
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("JSON-RPC message must be an object")
            except Exception as exc:
                _write_response(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"parse error: {exc}"},
                    }
                )
                continue
            if message.get("method") == "tools/call" and message.get("id") is not None:
                response = _queue_tool_call(message)
                if response is not None:
                    _write_response(response)
                continue
            try:
                response = handle_request(message)
            except Exception as exc:
                response = {
                    "jsonrpc": "2.0",
                    "id": message.get("id"),
                    "error": {"code": -32603, "message": f"internal error: {exc}"},
                }
            if response is not None:
                _write_response(response)
    finally:
        _shutdown_tool_calls()
    return 0


def _cli_main(argv: list[str]) -> int:
    """CLI entry over the same handlers and remote registry as the MCP tools.

    Lets shell scripts (research-hub launch_job.sh --transport fleet) and
    humans use fleet semantics where no MCP session exists.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="remote_fleet_mcp.py cli",
        description="Remote Fleet CLI (same code path as the MCP tools).",
    )
    sub = parser.add_subparsers(dest="cli_command", required=True)

    sub.add_parser("probe", help="report CLI availability, version, and configured hosts")

    p = sub.add_parser("status", help="host and job status")
    p.add_argument("--host", required=True)
    p.add_argument("--workdir")
    p.add_argument("--session")
    p.add_argument("--log-tail-lines", type=int, dest="log_tail_lines")
    p.add_argument("--job-limit", type=int, dest="job_limit")
    p.add_argument("--detail", choices=["compact", "full"])
    p.add_argument("--include-gpus", action="store_true", dest="include_gpus")

    p = sub.add_parser("read", help="read a remote file slice")
    p.add_argument("--host", required=True)
    p.add_argument("--path", required=True)
    p.add_argument("--start-line", type=int, dest="start_line")
    p.add_argument("--end-line", type=int, dest="end_line")
    p.add_argument("--max-bytes", type=int, dest="max_bytes")
    p.add_argument("--offset-bytes", type=int, dest="offset_bytes")
    p.add_argument("--include-sha256", action="store_true", dest="include_sha256")

    p = sub.add_parser("run", help="bounded foreground command")
    p.add_argument("--host", required=True)
    p.add_argument("--workdir", required=True)
    p.add_argument("--command", required=True)
    p.add_argument("--timeout-seconds", type=int, dest="timeout_seconds")
    p.add_argument("--max-output-chars", type=int, dest="max_output_chars")

    p = sub.add_parser("patch", help="apply a native Codex patch")
    p.add_argument("--host", required=True)
    p.add_argument("--workdir", required=True)
    p.add_argument(
        "--patch-file",
        required=True,
        dest="patch_file",
        help="UTF-8 Codex Begin Patch file; use '-' to read the patch from stdin",
    )
    p.add_argument(
        "--expected-sha256-file",
        dest="expected_sha256_file",
        help="optional JSON object mapping touched relative paths to SHA-256 or null",
    )

    p = sub.add_parser("launch", help="registered tmux long job")
    p.add_argument("--host", required=True)
    p.add_argument("--workdir", required=True)
    p.add_argument("--session", required=True)
    p.add_argument("--command", required=True)
    p.add_argument("--log-path", required=True, dest="log_path")
    p.add_argument("--append-log", action="store_true", dest="append_log")

    p = sub.add_parser("stop", help="stop a fleet-launched job")
    p.add_argument("--host", required=True)
    p.add_argument("--session", required=True)
    p.add_argument("--grace-seconds", type=int, dest="grace_seconds")

    args = parser.parse_args(argv)

    if args.cli_command == "probe":
        payload: dict[str, Any] = {
            "hosts": sorted(configured_hosts()),
            "ok": True,
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
        }
        print(_json_text(payload))
        return 0

    raw_arguments = vars(args).copy()
    cli_command = raw_arguments.pop("cli_command")
    if cli_command == "patch":
        patch_file = raw_arguments.pop("patch_file")
        expected_file = raw_arguments.pop("expected_sha256_file")
        try:
            if patch_file == "-":
                patch_text = sys.stdin.read()
            else:
                patch_text = Path(patch_file).read_text(encoding="utf-8")
            raw_arguments["patch"] = patch_text
            if expected_file:
                expected_text = Path(expected_file).read_text(encoding="utf-8")
                raw_arguments["expected_sha256"] = json.loads(expected_text)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            print(f"error: could not read patch input: {exc}", file=sys.stderr)
            return 2

    handler = {
        "status": remote_status,
        "read": remote_read_file,
        "run": remote_run,
        "patch": remote_apply_patch,
        "launch": remote_launch_tmux,
        "stop": remote_stop_job,
    }[cli_command]
    arguments = {
        key: value
        for key, value in raw_arguments.items()
        if value is not None and value is not False
    }
    try:
        result = handler(arguments)
    except ToolError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(_json_text(result))
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        raise SystemExit(_cli_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "cli-json":
        raise SystemExit(_cli_json_main())
    if len(sys.argv) > 1 and sys.argv[1] == "opencode-config":
        sys.stdout.write(json.dumps(render_opencode_config(), indent=2) + "\n")
        raise SystemExit(0)
    raise SystemExit(main())
