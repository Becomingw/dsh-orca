"""orca grep / ls (remote program; stdlib only, Python 3.6+).

Shipped as ``python3 -c <source>`` with a JSON request on stdin; prints one
JSON object. Filtering happens on the remote host so only matches cross the
wire. ``.gz`` files (rotated events-*.ndjson.gz) are searched transparently.
"""

import fnmatch
import gzip
import json
import os
import re
import stat
import sys
import time

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache"}
BINARY_PROBE = 8192


class RequestError(Exception):
    pass


def is_hidden(name):
    return name.startswith(".") and name not in (".", "..")


def walk(root, include_hidden, glob, max_files, deadline, state):
    """Yield candidate files under root (a file yields itself)."""
    if os.path.isfile(root):
        yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in SKIP_DIRS and (include_hidden or not is_hidden(d))
        )
        for name in sorted(filenames):
            if not include_hidden and is_hidden(name):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            if glob and not (fnmatch.fnmatch(name, glob) or fnmatch.fnmatch(rel, glob)):
                continue
            if state["files_scanned"] >= max_files:
                state["truncated"] = "max_files"
                return
            if time.time() > deadline:
                state["timed_out"] = True
                return
            yield path


def open_text(path):
    if path.endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    with open(path, "rb") as probe:
        if b"\0" in probe.read(BINARY_PROBE):
            return None
    return open(path, "r", encoding="utf-8", errors="replace")


def clip(text, limit):
    text = text.rstrip("\n")
    return text if len(text) <= limit else text[:limit] + "…"


def grep(req):
    root = req["path"]
    if not os.path.isabs(root) or not os.path.exists(root):
        raise RequestError("path does not exist or is not absolute: {}".format(root))
    flags = re.IGNORECASE if req.get("ignore_case") else 0
    pattern = req["pattern"]
    try:
        regex = re.compile(re.escape(pattern) if req.get("fixed") else pattern, flags)
    except re.error as exc:
        raise RequestError("invalid regex: {}".format(exc))
    max_matches = int(req.get("max_matches", 100))
    context = int(req.get("context", 0))
    line_chars = int(req.get("max_line_chars", 400))
    deadline = time.time() + float(req.get("timeout_seconds", 30))
    state = {"files_scanned": 0, "truncated": None, "timed_out": False}
    matches = []
    files_matched = 0
    single_file = os.path.isfile(root)
    for path in walk(root, req.get("include_hidden", False), req.get("glob"), int(req.get("max_files", 20000)), deadline, state):
        try:
            handle = open_text(path)
        except OSError:
            continue
        if handle is None:
            continue
        state["files_scanned"] += 1
        before = []
        pending_after = []  # matches still collecting trailing context
        matched_here = False
        with handle:
            for number, line in enumerate(handle, 1):
                if number % 4096 == 0 and time.time() > deadline:
                    state["timed_out"] = True
                    break
                text = clip(line, line_chars)
                for match in pending_after:
                    match["after"].append(text)
                pending_after = [m for m in pending_after if len(m["after"]) < context]
                if regex.search(line):
                    if len(matches) >= max_matches:
                        state["truncated"] = "max_matches"
                        break
                    match = {"path": path if not single_file else root, "line": number, "text": text}
                    if context:
                        match["before"] = list(before)
                        match["after"] = []
                        pending_after.append(match)
                    matches.append(match)
                    matched_here = True
                if context:
                    before.append(text)
                    if len(before) > context:
                        before.pop(0)
        files_matched += matched_here
        if state["truncated"] or state["timed_out"]:
            break
    for match in matches:
        if not match.get("after"):
            match.pop("after", None)
        if not match.get("before"):
            match.pop("before", None)
    result = {
        "files_matched": files_matched,
        "files_scanned": state["files_scanned"],
        "matches": matches,
        "path": root,
    }
    if state["truncated"]:
        result["truncated"] = state["truncated"]
    if state["timed_out"]:
        result["timed_out"] = True
    return result


def ls(req):
    root = req["path"]
    if not os.path.isabs(root) or not os.path.isdir(root):
        raise RequestError("path is not an absolute directory: {}".format(root))
    depth = int(req.get("depth", 1))
    max_entries = int(req.get("max_entries", 500))
    glob = req.get("glob")
    include_hidden = req.get("include_hidden", False)
    entries = []
    truncated = False
    root_depth = root.rstrip("/").count("/")
    for dirpath, dirnames, filenames in os.walk(root):
        level = dirpath.rstrip("/").count("/") - root_depth + 1
        dirnames[:] = sorted(d for d in dirnames if include_hidden or not is_hidden(d))
        names = sorted([(d, True) for d in dirnames] + [(f, False) for f in filenames if include_hidden or not is_hidden(f)])
        for name, _is_dir in names:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            if glob and not (fnmatch.fnmatch(name, glob) or fnmatch.fnmatch(rel, glob)):
                continue
            try:
                info = os.lstat(path)
            except OSError:
                continue
            kind = "d" if stat.S_ISDIR(info.st_mode) else "l" if stat.S_ISLNK(info.st_mode) else "f"
            entry = {"path": rel, "type": kind, "mtime": int(info.st_mtime)}
            if kind == "f":
                entry["size"] = info.st_size
            if kind == "l":
                try:
                    entry["target"] = os.readlink(path)
                except OSError:
                    pass
            entries.append(entry)
            if len(entries) >= max_entries:
                truncated = True
                break
        if truncated or level >= depth:
            dirnames[:] = []
        if truncated:
            break
    result = {"entries": entries, "path": root}
    if truncated:
        result["truncated"] = True
    return result


def main(stdin_text):
    try:
        req = json.loads(stdin_text)
        op = req.get("op")
        if op == "grep":
            return 0, grep(req)
        if op == "ls":
            return 0, ls(req)
        raise RequestError("unknown op: {!r}".format(op))
    except (RequestError, KeyError, ValueError, TypeError) as exc:
        return 1, {"error": str(exc) if not isinstance(exc, KeyError) else "missing field {}".format(exc)}


def _entrypoint():
    code, payload = main(sys.stdin.read())
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    sys.exit(code)


if __name__ == "__main__":
    _entrypoint()
