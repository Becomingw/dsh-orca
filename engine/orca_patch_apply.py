"""Transactional Codex-format patch applier for dsh-orca remote hosts.

This file is a *remote program*: its full source is sent over SSH and run as
``python3 -c "<source>"`` with one JSON request on stdin; it prints exactly one
JSON object on stdout.  Stdlib only, Python 3.6 compatible (no walrus, no
dataclasses, no annotations).

Result contract
---------------
exit 1, {"error": ...}           nothing in the workdir was touched (bad request,
                                 unsafe path, lock timeout, backup failure).
exit 0, {"ok", "mode", "state"}  everything else.  ``state`` is one of
    dry_run:  "clean" | "conflict"
    apply:    "applied" | "already_applied" | "unchanged" | "conflict"
              | "restored"  (a write failed; every touched file was put back)
              | "partial"   (a write failed and restoring failed too; the
                             manifest stays "prepared" -> run rollback)
    rollback: "rolled_back" | "already_rolled_back" | "conflict" | "partial"
    status:   "committed" | "prepared" | "restored" | "rolled_back" | "not_found"

Transactions
------------
apply takes the per-workdir flock shared with remote-fleet, plans everything
in memory, writes backups plus a manifest in state "prepared", mutates, then
marks the manifest "committed".  A process killed mid-apply leaves a
"prepared" manifest; rollback (idempotent: entries already in their pre-patch
state are skipped) and a retried apply with the same patch_id both recover
from it.  Every mutation first re-checks that the file still holds the bytes
that were planned against, so editors that ignore the lock are detected.

Text semantics follow current codex-rs/apply-patch (parser.rs, seek_sequence.rs,
file_update.rs, text_file.rs): per-line endings are preserved, context lines
are left untouched, inserted lines use the file's first line ending.  See
docs/engine-changes.md for where this backend deliberately refuses more.
"""

from bisect import bisect_left
import difflib
import errno
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import sys
import tempfile
import time

BEGIN_PATCH = "*** Begin Patch"
END_PATCH = "*** End Patch"
ADD_FILE = "*** Add File: "
DELETE_FILE = "*** Delete File: "
UPDATE_FILE = "*** Update File: "
MOVE_TO = "*** Move to: "
END_OF_FILE = "*** End of File"
CHANGE_CONTEXT = "@@ "
EMPTY_CHANGE_CONTEXT = "@@"

LOCK_TIMEOUT_SECONDS = 10.0
LOCK_POLL_SECONDS = 0.1
DEFAULT_MAX_DIFF_CHARS = 20000
MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 2
# Backup retention: committed backups older than this, or beyond the newest
# MAX_BACKUPS, are pruned; retired (rolled back / restored) ones sooner.
BACKUP_RETENTION_SECONDS = 14 * 86400
RETIRED_RETENTION_SECONDS = 7 * 86400
MAX_BACKUPS = 100
# Total size of kept backups, and free space that must remain after a patch.
MAX_BACKUP_BYTES = 1 << 30
FREE_SPACE_RESERVE_BYTES = 256 << 20
# Closest-match search in conflict messages is skipped above this many
# line comparisons, to keep a failing call cheap on huge files.
CLOSEST_MATCH_BUDGET = 400000

PATCH_ID_RE = re.compile(r"\A[A-Za-z0-9_.-]{1,100}\Z")
SHA256_RE = re.compile(r"\A[0-9A-Fa-f]{64}\Z")
RETIRED_RE = re.compile(r"\.(rolled-back|restored|interrupted)-\d{8}T\d{6}Z(\.\d+)?\Z")

REQUEST_KEYS = frozenset(
    ["mode", "workdir", "patch", "expected_sha256", "patch_id", "backup_root", "max_diff_chars", "force", "strict"]
)


class PatchError(Exception):
    """Nothing was touched: reported as {"error": ...} with exit 1."""


class HunkConflict(Exception):
    """A planned change cannot be made; reported per path as a conflict."""


class ConcurrentChange(Exception):
    """A file changed between planning and writing (an editor ignored the lock)."""


class Interrupted(BaseException):
    """SIGTERM/SIGHUP/SIGINT during a mutation; triggers a restore."""


# ---------------------------------------------------------------------------
# Parsing (codex-rs/apply-patch/src/parser.rs)
# ---------------------------------------------------------------------------


def _rust_lines(text):
    """Equivalent of Rust's str::lines(): split on \\n, drop one trailing \\r."""
    if not text:
        return []
    parts = text.split("\n")
    if parts and parts[-1] == "":
        parts.pop()
    return [p[:-1] if p.endswith("\r") else p for p in parts]


def parse_patch(text):
    """Parse a Codex apply_patch document into a list of hunk dicts.

      {"type": "add", "path", "contents", "line"}
      {"type": "delete", "path", "line"}
      {"type": "update", "path", "move_to", "chunks", "line"}

    A chunk is {"context", "old", "new", "ctx", "eof", "line"} where ``ctx``
    pairs (old_index, new_index) of lines parsed as context.
    """
    if not isinstance(text, str):
        raise PatchError("patch must be a string")
    lines = _rust_lines(text.strip())
    first = lines[0].strip() if lines else None
    last = lines[-1].strip() if lines else None
    if first != BEGIN_PATCH:
        raise PatchError("invalid patch: the first line of the patch must be '*** Begin Patch'")
    if len(lines) < 2 or last != END_PATCH:
        raise PatchError("invalid patch: the last line of the patch must be '*** End Patch'")
    hunks = []
    remaining = lines[1:-1]
    line_number = 2
    while remaining:
        hunk, used = _parse_one_hunk(remaining, line_number)
        hunks.append(hunk)
        line_number += used
        remaining = remaining[used:]
    if not hunks:
        raise PatchError("invalid patch: no Add File, Delete File, or Update File sections")
    return hunks


def _parse_one_hunk(lines, line_number):
    header = lines[0].strip()
    if header.startswith(ADD_FILE):
        contents = []
        used = 1
        for line in lines[1:]:
            if not line.startswith("+"):
                break
            contents.append(line[1:] + "\n")
            used += 1
        return {"type": "add", "path": header[len(ADD_FILE):], "contents": "".join(contents), "line": line_number}, used
    if header.startswith(DELETE_FILE):
        return {"type": "delete", "path": header[len(DELETE_FILE):], "line": line_number}, 1
    if header.startswith(UPDATE_FILE):
        path = header[len(UPDATE_FILE):]
        remaining = lines[1:]
        used = 1
        move_to = None
        # Codex matches the Move to marker on the raw (untrimmed) line.
        if remaining and remaining[0].startswith(MOVE_TO):
            move_to = remaining[0][len(MOVE_TO):]
            remaining = remaining[1:]
            used += 1
        chunks = []
        while remaining:
            if remaining[0].strip() == "":
                used += 1
                remaining = remaining[1:]
                continue
            if remaining[0].startswith("***"):
                break
            chunk, chunk_used = _parse_update_chunk(remaining, line_number + used, not chunks)
            chunks.append(chunk)
            used += chunk_used
            remaining = remaining[chunk_used:]
        if not chunks:
            raise PatchError(f"invalid patch line {line_number}: Update File section for {path!r} has no hunks")
        return {"type": "update", "path": path, "move_to": move_to, "chunks": chunks, "line": line_number}, used
    raise PatchError(
        f"invalid patch line {line_number}: {header!r} is not a valid hunk header; expected "
        "'*** Add File: <path>', '*** Delete File: <path>', or '*** Update File: <path>'"
    )


def _parse_update_chunk(lines, line_number, allow_missing_context):
    if lines[0] == EMPTY_CHANGE_CONTEXT:
        context, start = None, 1
    elif lines[0].startswith(CHANGE_CONTEXT):
        context, start = lines[0][len(CHANGE_CONTEXT):], 1
    else:
        if not allow_missing_context:
            raise PatchError(
                f"invalid patch line {line_number}: expected update hunk to start with an @@ "
                f"context marker, got {lines[0]!r}"
            )
        context, start = None, 0
    if start >= len(lines):
        raise PatchError(f"invalid patch line {line_number + 1}: update hunk does not contain any lines")
    chunk = {"context": context, "old": [], "new": [], "ctx": [], "eof": False, "line": line_number}
    parsed = 0
    for line in lines[start:]:
        if line == END_OF_FILE:
            if parsed == 0:
                raise PatchError(f"invalid patch line {line_number + 1}: update hunk does not contain any lines")
            chunk["eof"] = True
            parsed += 1
            break
        if line == "" or line[0] == " ":
            text = line[1:]
            chunk["ctx"].append((len(chunk["old"]), len(chunk["new"])))
            chunk["old"].append(text)
            chunk["new"].append(text)
        elif line[0] == "+":
            chunk["new"].append(line[1:])
        elif line[0] == "-":
            chunk["old"].append(line[1:])
        else:
            if parsed == 0:
                raise PatchError(
                    f"invalid patch line {line_number + start}: unexpected line {line!r} in update "
                    "hunk; every line should start with ' ' (context), '+' (added), or '-' (removed)"
                )
            break
        parsed += 1
    return chunk, parsed + start


# ---------------------------------------------------------------------------
# Text model (codex-rs/apply-patch/src/text_file.rs)
# ---------------------------------------------------------------------------

_ENDING_RE = re.compile(r"(\r\n|\r|\n)")  # capturing: split keeps the endings
BOM = "﻿"


class SourceFile(object):
    """Lines with their own endings (parallel lists); the first ending is preferred for inserts.

    A leading BOM is kept aside so it neither blocks line-1 matches nor gets lost.
    """

    def __init__(self, text):
        self.bom = text.startswith(BOM)
        if self.bom:
            text = text[1:]
        if "\r" not in text:  # fast path: LF only
            texts = text.split("\n")
            endings = ["\n"] * (len(texts) - 1)
            if texts[-1] == "":
                texts.pop()
            else:
                endings.append(None)
            self.preferred = "\n"
        else:
            parts = _ENDING_RE.split(text)  # text, ending, text, ending, ..., tail
            texts, endings = parts[0:-1:2], parts[1::2]
            if parts[-1]:
                texts.append(parts[-1])
                endings.append(None)
            self.preferred = endings[0] if endings and endings[0] else "\n"
        self.texts, self.endings = texts, endings
        self.missing_final_newline = bool(endings) and endings[-1] is None

    def apply(self, replacements):
        """Source-ordered, non-overlapping (start, old_len, new_texts) replacements."""
        texts, endings = [], []
        index = 0
        for start, old_len, new_texts in replacements:
            texts.extend(self.texts[index:start])
            endings.extend(self.endings[index:start])
            texts.extend(new_texts)
            endings.extend([self.preferred] * len(new_texts))
            index = start + old_len
        texts.extend(self.texts[index:])
        endings.extend(self.endings[index:])
        if endings and endings[-1] is None:  # updates always end every line, like Codex
            endings[-1] = self.preferred
        self.texts, self.endings = texts, endings

    def contents(self):
        if all(e == "\n" for e in self.endings):
            body = "\n".join(self.texts) + ("\n" if self.texts else "")
        else:
            body = "".join(t + (e or "") for t, e in zip(self.texts, self.endings))
        return (BOM + body) if self.bom else body


# ---------------------------------------------------------------------------
# Matching (codex-rs/apply-patch/src/seek_sequence.rs + file_update.rs)
# ---------------------------------------------------------------------------

_PUNCT_MAP = {}
for _c in "‐‑‒–—―−":
    _PUNCT_MAP[ord(_c)] = "-"
for _c in "‘’‚‛":
    _PUNCT_MAP[ord(_c)] = "'"
for _c in "“”„‟":
    _PUNCT_MAP[ord(_c)] = '"'
for _c in "            　":
    _PUNCT_MAP[ord(_c)] = " "

LEVELS = (
    ("exact", lambda s: s),
    ("rstrip", lambda s: s.rstrip()),
    ("strip", lambda s: s.strip()),
    ("unicode", lambda s: s.strip().translate(_PUNCT_MAP)),
)


LEVEL_FUNCS = dict(LEVELS)
# Closest-match candidates per pattern line, and windows scored at most.
CLOSEST_POSITIONS_PER_LINE = 200
CLOSEST_MAX_WINDOWS = 5000


class LineIndex(object):
    """Per-file match index, built lazily per level.

    Codex semantics are unchanged (the first level that matches anywhere wins;
    within it, the first start >= lo).  The index only skips starts whose first
    line cannot match and compares candidate windows as list slices, so cost
    tracks the number of plausible starts instead of lines x pattern x levels.
    """

    def __init__(self, lines):
        self.lines = lines
        self._norm = {}
        self._pos = {}

    def normalized(self, level):
        got = self._norm.get(level)
        if got is None:
            norm = LEVEL_FUNCS[level]
            got = self.lines if level == "exact" else [norm(line) for line in self.lines]
            self._norm[level] = got
        return got

    def positions(self, level, value):
        index = self._pos.get(level)
        if index is None:
            index = {}
            for i, line in enumerate(self.normalized(level)):
                index.setdefault(line, []).append(i)
            self._pos[level] = index
        return index.get(value, ())

    def find(self, want, level, lo, hi, limit=1):
        """Ascending starts in [lo, hi] where normalised ``want`` matches (at most ``limit``)."""
        lines = self.normalized(level)
        n = len(want)
        hits = []
        candidates = self.positions(level, want[0])
        for i in candidates[bisect_left(candidates, lo):]:
            if i > hi:
                break
            if n == 1 or lines[i:i + n] == want:
                hits.append(i)
                if len(hits) >= limit:
                    break
        return hits

    def seek(self, pattern, start, eof):
        """(index, level) of the first match at/after start, or (None, None).

        With eof the match must end at the last line, but never before start
        (current Codex).
        """
        if not pattern:
            return start, "exact"
        if len(pattern) > len(self.lines):
            return None, None
        last = len(self.lines) - len(pattern)
        lo = max(last, start) if eof else start
        for name, norm in LEVELS:
            hits = self.find([norm(p) for p in pattern], name, lo, last)
            if hits:
                return hits[0], name
        return None, None

    def also_matches(self, pattern, level, after, limit=3):
        """Other starts after ``after`` where pattern matches at the same level."""
        norm = LEVEL_FUNCS[level]
        return self.find([norm(p) for p in pattern], level, after + 1, len(self.lines) - len(pattern), limit)


def seek_sequence(lines, pattern, start, eof):
    """Codex seek_sequence: (index, level) or (None, None). See LineIndex.seek."""
    return LineIndex(lines).seek(pattern, start, eof)


def _closest(index, pattern):
    """(start, ratio) of the window most similar to pattern, or None.

    Windows are anchored where some pattern line occurs (stripped) in the file,
    which covers the usual "one line differs" case cheaply; only when no line
    occurs at all is every window scored, and then only within a budget.
    """
    lines = index.lines
    n = len(pattern)
    if not lines or n == 0:
        return None
    want = [p.strip() for p in pattern]
    last = max(0, len(lines) - n)
    starts = set()
    for k, line in enumerate(want):
        if line:
            for pos in index.positions("strip", line)[:CLOSEST_POSITIONS_PER_LINE]:
                if 0 <= pos - k <= last:
                    starts.add(pos - k)
    if starts:
        starts = sorted(starts)[:CLOSEST_MAX_WINDOWS]
    elif len(lines) * n <= CLOSEST_MATCH_BUDGET:
        starts = range(0, last + 1)
    else:
        return None
    stripped = index.normalized("strip")
    matcher = difflib.SequenceMatcher(None, "", "\n".join(want), autojunk=False)
    best = None
    for i in starts:
        matcher.set_seq1("\n".join(stripped[i:i + n]))
        if best is not None and matcher.quick_ratio() <= best[1]:
            continue
        ratio = matcher.ratio()
        if best is None or ratio > best[1]:
            best = (i, ratio)
    return best


def _quote(lines, limit=12):
    shown = ["  |" + line for line in lines[:limit]]
    if len(lines) > limit:
        shown.append(f"  | ... ({len(lines) - limit} more line(s))")
    return "\n".join(shown)


def _not_found(index, chunk, number, cursor):
    old = chunk["old"]
    where = "at end of file" if chunk["eof"] else f"at or after line {cursor + 1}"
    parts = [
        f"hunk {number} (patch line {chunk['line']}): could not find these "
        f"{len(old)} line(s) {where}:\n{_quote(old)}"
    ]
    added = [line for line in chunk["new"] if line.strip()]
    if added and index.seek(chunk["new"], 0, False)[0] is not None:
        parts.append("the hunk's replacement lines are already present: was this patch already applied?")
    best = _closest(index, old)
    if best is not None and best[1] >= 0.5:
        start, ratio = best
        window = index.lines[start:start + len(old)]
        parts.append(f"closest match at line {start + 1} ({int(ratio * 100)}% similar):\n{_quote(window)}")
    parts.append("re-read the file and regenerate this hunk")
    return "\n".join(parts)


def compute_replacements(lines, chunks, strict=False):
    """Codex compute_replacements plus match notes. Raises HunkConflict.

    Returns (replacements, notes); notes record non-exact and ambiguous matches.
    """
    replacements = []
    notes = []
    cursor = 0
    index = LineIndex(lines)
    for number, chunk in enumerate(chunks, 1):
        ctx = chunk["context"]
        if ctx is not None:
            idx, _level = index.seek([ctx], cursor, False)
            if idx is None:
                raise HunkConflict(
                    f"hunk {number} (patch line {chunk['line']}): could not find the @@ context line "
                    f"{ctx!r} at or after line {cursor + 1}"
                )
            cursor = idx + 1
        old, new = chunk["old"], chunk["new"]
        if not old:
            replacements.append((len(lines), 0, list(new)))
            continue
        found, level = index.seek(old, cursor, chunk["eof"])
        if found is None and old[-1] == "":
            # A trailing empty line stands for the final newline; retry without it.
            old = old[:-1]
            if new and new[-1] == "":
                new = new[:-1]
            found, level = index.seek(old, cursor, chunk["eof"])
        if found is None:
            raise HunkConflict(_not_found(index, chunk, number, cursor))

        note = {"hunk": number, "line": found + 1, "level": level}
        others = [] if chunk["eof"] else index.also_matches(old, level, found)
        if others:
            note["also_matches_lines"] = [i + 1 for i in others]
        if level != "exact" or others:
            notes.append(note)
            if strict:
                why = "ambiguous" if others else f"only matched after {level} normalisation"
                raise HunkConflict(f"hunk {number} (patch line {chunk['line']}): strict mode: {why} (line {found + 1})")

        # Keep context lines physically untouched so their exact text and
        # endings survive (current Codex).
        old_start = new_start = 0
        for old_ctx, new_ctx in chunk["ctx"]:
            if old_ctx >= len(old) or new_ctx >= len(new):
                break
            if old_start != old_ctx or new_start != new_ctx:
                replacements.append((found + old_start, old_ctx - old_start, new[new_start:new_ctx]))
            old_start, new_start = old_ctx + 1, new_ctx + 1
        if old_start != len(old) or new_start != len(new):
            replacements.append((found + old_start, len(old) - old_start, new[new_start:]))
        cursor = found + len(old)
    replacements.sort(key=lambda r: r[0])
    return replacements, notes


def apply_chunks(original, chunks, strict=False, diff_names=None):
    """Return (new_text, notes) after applying update chunks (raises HunkConflict).

    With ``diff_names=(from, to)`` also return (diff, added, removed), built
    from the replacements themselves rather than by re-diffing whole files.
    """
    source = SourceFile(original)
    old_texts = source.texts
    replacements, notes = compute_replacements(old_texts, chunks, strict)
    missing_newline = source.missing_final_newline
    source.apply(replacements)
    if diff_names is None:
        return source.contents(), notes
    diff = diff_from_replacements(old_texts, replacements, diff_names[0], diff_names[1], missing_newline)
    return source.contents(), notes, diff


def _unified_range(start, length):
    """difflib's unified range format for a 0-based start."""
    if length == 1:
        return str(start + 1)
    return "{},{}".format(start if length == 0 else start + 1, length)


def diff_from_replacements(old, replacements, from_name, to_name, missing_final_newline=False, context=3):
    """Unified diff (and added/removed counts) straight from (start, old_len, new) replacements."""
    reps = list(replacements)
    last = len(old) - 1
    if missing_final_newline and last >= 0 and not any(s <= last < s + n for s, n, _ in reps):
        reps.append((last, 1, [old[last]]))  # the final newline Codex adds
        reps.sort(key=lambda r: r[0])
    groups = []
    for rep in reps:
        if groups and rep[0] - (groups[-1][-1][0] + groups[-1][-1][1]) <= 2 * context:
            groups[-1].append(rep)
        else:
            groups.append([rep])
    out, added, removed, delta = [], 0, 0, 0
    for group in groups:
        lo = max(0, group[0][0] - context)
        hi = min(len(old), group[-1][0] + group[-1][1] + context)
        body, pos, new_count = [], lo, 0
        for start, old_len, new in group:
            body.extend(" " + old[i] for i in range(pos, start))
            for i in range(start, start + old_len):
                body.append("-" + old[i])
                if missing_final_newline and i == last:
                    body.append("\\ No newline at end of file")
            body.extend("+" + line for line in new)
            new_count += (start - pos) + len(new)
            removed += old_len
            added += len(new)
            pos = start + old_len
        body.extend(" " + old[i] for i in range(pos, hi))
        new_count += hi - pos
        out.append("@@ -{} +{} @@".format(_unified_range(lo, hi - lo), _unified_range(lo + delta, new_count)))
        out.extend(body)
        delta += sum(len(new) - old_len for _s, old_len, new in group)
    if not out:
        return "", 0, 0
    return "--- {}\n+++ {}\n".format(from_name, to_name) + "\n".join(out) + "\n", added, removed


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _inside(root, path):
    return path != root and os.path.commonpath([root, path]) == root


def _validate_rel(rel):
    if not isinstance(rel, str) or rel == "":
        raise PatchError("unsafe path: empty path")
    if "\0" in rel:
        raise PatchError(f"unsafe path {rel!r}: contains NUL")
    if os.path.isabs(rel) or rel.startswith("~"):
        raise PatchError(f"unsafe path {rel!r}: must be relative to workdir")
    if ".." in rel.replace("\\", "/").split("/"):
        raise PatchError(f"unsafe path {rel!r}: '..' components are not allowed")
    norm = os.path.normpath(rel)
    if norm in (".", "") or norm.startswith(".." + os.sep) or norm == "..":
        raise PatchError(f"unsafe path {rel!r}: does not name a file inside workdir")
    return norm


def resolve_path(root, rel):
    """(normalized_rel, real_abs_path, is_symlink); PatchError if it escapes root."""
    norm = _validate_rel(rel)
    joined = os.path.join(root, norm)
    real = os.path.realpath(joined)
    if not _inside(root, real):
        raise PatchError(f"unsafe path {rel!r}: resolves outside workdir ({real})")
    return norm, real, os.path.islink(joined)


def _resolve_workdir(workdir):
    if not isinstance(workdir, str) or not workdir:
        raise PatchError("workdir must be a non-empty string")
    workdir = os.path.expanduser(workdir)
    if not os.path.isabs(workdir):
        raise PatchError("workdir must be an absolute path")
    if not os.path.isdir(workdir):
        raise PatchError(f"workdir is not a directory: {workdir}")
    return os.path.realpath(workdir)


def _rel(root, real):
    return os.path.relpath(real, root)


# ---------------------------------------------------------------------------
# Filesystem primitives
# ---------------------------------------------------------------------------


def _lstat(path):
    try:
        return os.lstat(path)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return None
        raise


def read_regular(real):
    """(bytes, stat) of a regular file, None if absent; HunkConflict otherwise.

    O_NOFOLLOW refuses a symlink swapped in after planning; O_NONBLOCK keeps a
    FIFO from hanging the call.
    """
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(real, flags)
    except OSError as exc:
        if exc.errno in (errno.ENOENT, errno.ENOTDIR):
            return None
        if exc.errno == errno.ELOOP:
            raise HunkConflict("is a symbolic link; refusing to modify it")
        raise
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise HunkConflict("exists but is not a regular file")
        chunks = []
        while True:
            block = os.read(fd, 1 << 20)
            if not block:
                break
            chunks.append(block)
        return b"".join(chunks), st
    finally:
        os.close(fd)


def _current(real):
    """Current bytes of a path: bytes, None (absent), or False (not a regular file)."""
    try:
        got = read_regular(real)
    except HunkConflict:
        return False
    return None if got is None else got[0]


def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # some filesystems (e.g. certain NFS setups) refuse directory fsync
    finally:
        os.close(fd)


def _has_xattrs(path):
    listxattr = getattr(os, "listxattr", None)
    if listxattr is None:
        return False
    try:
        return bool(listxattr(path, follow_symlinks=False))
    except OSError:
        return False


def _default_file_mode():
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


def _temp_beside(path, data, mode, gid=None):
    directory = os.path.dirname(path)
    prefix = "." + os.path.basename(path)[:60] + "."
    fd, tmp = tempfile.mkstemp(prefix=prefix, suffix=".orca-tmp", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if gid is not None:
            try:
                os.chown(tmp, -1, gid)  # keep the group on shared machines (best effort)
            except OSError:
                pass
        os.chmod(tmp, mode)
    except BaseException:
        _unlink_quiet(tmp)
        raise
    return tmp


def _unlink_quiet(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def write_replace(path, data, mode, gid=None):
    """Atomic replace (new inode): readers see old or new, never half."""
    tmp = _temp_beside(path, data, mode, gid)
    try:
        os.replace(tmp, path)
    except BaseException:
        _unlink_quiet(tmp)
        raise
    _fsync_dir(os.path.dirname(path))


def write_inplace(path, data):
    """Rewrite in place: keeps inode, owner, group, ACLs, xattrs and hard links."""
    fd = os.open(path, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        os.ftruncate(fd, 0)
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def create_exclusive(path, data, mode):
    """Create path only if it does not exist (EEXIST -> ConcurrentChange)."""
    tmp = _temp_beside(path, data, mode)
    try:
        try:
            os.link(tmp, path)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise ConcurrentChange("appeared after planning")
            if exc.errno not in (errno.EPERM, errno.ENOTSUP, errno.EOPNOTSUPP, errno.EXDEV, errno.EMLINK):
                raise
            # No hard links here: fall back to O_EXCL (exclusive, not atomic).
            try:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
            except OSError as exc2:
                if exc2.errno == errno.EEXIST:
                    raise ConcurrentChange("appeared after planning")
                raise
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(path, mode)
    finally:
        _unlink_quiet(tmp)
    _fsync_dir(os.path.dirname(path))


def _missing_dirs(root, real_path):
    """Missing ancestors of real_path inside root, outermost first."""
    missing = []
    d = os.path.dirname(real_path)
    while d != root and _inside(root, d):
        st = _lstat(d)
        if st is None:
            missing.append(d)
        elif not stat.S_ISDIR(st.st_mode):
            raise HunkConflict(f"parent {os.path.relpath(d, root)!r} exists and is not a directory")
        else:
            break
        d = os.path.dirname(d)
    missing.reverse()
    return missing


def _writable_parent(root, real_path):
    """Can we create real_path (its nearest existing ancestor is writable)?"""
    d = os.path.dirname(real_path)
    while not os.path.isdir(d) and d != root:
        d = os.path.dirname(d)
    return os.access(d, os.W_OK | os.X_OK)


def _ensure_dirs(dirs):
    for d in dirs:
        if not os.path.isdir(d):
            os.mkdir(d, 0o777)
            _fsync_dir(os.path.dirname(d))


def _remove_empty_dirs(dirs):
    for d in sorted(dirs, key=len, reverse=True):
        try:
            os.rmdir(d)
        except OSError:
            pass


def _utc_stamp(compact):
    return time.strftime("%Y%m%dT%H%M%SZ" if compact else "%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


class Op(object):
    """One file change, fully computed before anything is written."""

    __slots__ = (
        "index", "action", "path", "real", "move_to", "move_real", "before", "after",
        "mode", "uid", "gid", "strategy", "notes", "line",
    )

    def __init__(self, **fields):
        for name in self.__slots__:
            setattr(self, name, fields.get(name))

    @property
    def unchanged(self):
        return self.action == "update" and self.before == self.after

    def entry(self, backup):
        """Manifest form: everything rollback needs, nothing it doesn't."""
        entry = {
            "path": self.path,
            "action": self.action,
            "backup": backup,
            "mode": self.mode,
            "gid": self.gid,
            "strategy": self.strategy,
            "sha256_before": _sha256(self.before) if self.before is not None else None,
            "sha256_after": _sha256(self.after) if self.after is not None else None,
        }
        if self.move_to is not None:
            entry["move_to"] = self.move_to
        return entry

    def summary(self, added, removed):
        out = {"path": self.path, "action": self.action, "added": added, "removed": removed}
        if self.move_to is not None:
            out["move_to"] = self.move_to
        if self.before is not None:
            out["sha256_before"] = _sha256(self.before)
        if self.after is not None:
            out["sha256_after"] = _sha256(self.after)
        if self.unchanged:
            out["unchanged"] = True
        if self.notes:
            out["matches"] = self.notes
        return out


def _update_strategy(real, st):
    """'inplace' when a new inode would lose identity, else atomic 'replace'."""
    if (
        st.st_nlink > 1
        or st.st_uid != os.geteuid()
        or _has_xattrs(real)
        or not os.access(os.path.dirname(real), os.W_OK | os.X_OK)
    ):
        return "inplace"
    return "replace"


def _diff(old_text, new_text, from_name, to_name):
    """Unified diff plus (added, removed) counts in one pass."""
    a = old_text.splitlines(True)
    b = new_text.splitlines(True)
    out = []
    added = removed = 0
    for i, line in enumerate(difflib.unified_diff(a, b, from_name, to_name)):
        if i >= 2:
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1
        out.append(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n")
    return "".join(out), added, removed


def _check_hex(value, name):
    if value is None:
        return None
    if isinstance(value, str) and SHA256_RE.match(value):
        return value.lower()
    raise PatchError(f"{name} must be a 64-digit hex sha256 or null")


def _plan_op(root, op, hunk, default_mode, strict, created_dirs):
    """Fill op.before/after/mode/strategy; return the file's diff. Raises HunkConflict."""
    current = read_regular(op.real)
    action = op.action
    if action == "add":
        if current is not None or os.path.lexists(op.real):
            raise HunkConflict("Add File target already exists")
        if not _writable_parent(root, op.real):
            raise HunkConflict("cannot create: directory is not writable")
        created_dirs.extend(d for d in _missing_dirs(root, op.real) if d not in created_dirs)
        op.after = hunk["contents"].encode("utf-8")
        op.mode, op.strategy = default_mode, "create"
        return _diff("", hunk["contents"], "/dev/null", "b/" + op.path)

    if current is None:
        raise HunkConflict(("Delete" if action == "delete" else "Update") + " File target does not exist")
    op.before, st = current
    op.mode, op.uid, op.gid = stat.S_IMODE(st.st_mode), st.st_uid, st.st_gid
    if action == "delete":
        if not os.access(os.path.dirname(op.real), os.W_OK | os.X_OK):
            raise HunkConflict("cannot delete: directory is not writable")
        op.strategy = "unlink"
        try:
            return _diff(op.before.decode("utf-8"), "", "a/" + op.path, "/dev/null")
        except UnicodeDecodeError:
            return f"Binary file a/{op.path} deleted\n", 0, 0

    try:
        old_text = op.before.decode("utf-8")
    except UnicodeDecodeError:
        raise HunkConflict("binary or non-UTF-8 file; refusing to patch it as text")
    to_name = "b/" + (op.move_to if action == "move" else op.path)
    new_text, op.notes, diff = apply_chunks(old_text, hunk["chunks"], strict, ("a/" + op.path, to_name))
    op.after = new_text.encode("utf-8")
    if action == "move":
        if os.path.lexists(op.move_real):
            raise HunkConflict("Move to destination already exists")
        if not os.access(os.path.dirname(op.real), os.W_OK | os.X_OK) or not _writable_parent(root, op.move_real):
            raise HunkConflict("cannot move: directory is not writable")
        created_dirs.extend(d for d in _missing_dirs(root, op.move_real) if d not in created_dirs)
        op.strategy = "move"
        return (f"rename from {op.path}\nrename to {op.move_to}\n" + diff[0],) + diff[1:]
    if not os.access(op.real, os.W_OK):
        raise HunkConflict("read-only file (no write permission)")
    op.strategy = _update_strategy(op.real, st)
    return diff


def plan(workdir, patch_text, expected=None, strict=False):
    """Parse, validate and compute every resulting file in memory. Writes nothing.

    Returns {"root", "ops", "files", "conflicts", "diff", "created_dirs"};
    raises PatchError for malformed requests/patches and unsafe paths.
    """
    root = _resolve_workdir(workdir)
    hunks = parse_patch(patch_text)
    if expected is None:
        expected = {}
    if not isinstance(expected, dict):
        raise PatchError("expected_sha256 must be an object mapping paths to sha256 hex or null")

    ops = []
    for index, hunk in enumerate(hunks):
        norm, real, is_link = resolve_path(root, hunk["path"])
        op = Op(index=index, action=hunk["type"], path=norm, real=real, line=hunk["line"], notes=[])
        if hunk["type"] == "update" and hunk["move_to"] is not None:
            op.move_to, op.move_real, move_link = resolve_path(root, hunk["move_to"])
            op.action = "move"
            is_link = is_link or move_link
        ops.append((op, hunk, is_link))

    names = {}
    for op, _hunk, _link in ops:
        names[op.path] = op
        if op.move_to is not None:
            names[op.move_to] = op
    wants = []
    for key, value in expected.items():
        if not isinstance(key, str):
            raise PatchError("expected_sha256 keys must be strings")
        norm = _validate_rel(key)
        if norm not in names:
            raise PatchError(f"expected_sha256 path is not touched by this patch: {key!r}")
        wants.append((key, norm, _check_hex(value, f"expected_sha256[{key!r}]")))

    default_mode = _default_file_mode()
    conflicts, diffs, files, created_dirs, seen, failed = [], [], [], [], {}, set()
    for op, hunk, is_link in ops:
        label = op.path
        try:
            for target, name in ((op.real, op.path), (op.move_real, op.move_to)):
                if target is None:
                    continue
                if target in seen:
                    label = name
                    raise HunkConflict("touched more than once in this patch; combine its changes into one section")
                seen[target] = op.index
            if is_link:
                raise HunkConflict("is a symbolic link; refusing to modify it")
            diff, added, removed = _plan_op(root, op, hunk, default_mode, strict, created_dirs)
        except HunkConflict as exc:
            conflicts.append({"path": label, "message": str(exc)})
            failed.add(op.index)
            continue
        except OSError as exc:
            conflicts.append({"path": label, "message": f"cannot read: {exc}"})
            failed.add(op.index)
            continue
        files.append(op.summary(added, removed))
        if not op.unchanged:
            diffs.append(diff)

    # expected_sha256 is checked against the very bytes the plan was built on.
    for key, norm, want in wants:
        op = names[norm]
        if op.index in failed:
            continue  # already reported
        creates = op.action == "add" or (op.move_to == norm and op.path != norm)
        actual = None if creates else _sha256(op.before)
        if want == actual:
            continue
        if want is None:
            message = "expected_sha256 says it must not exist, but it does"
        elif actual is None:
            message = "expected_sha256 given but the file does not exist"
        else:
            message = f"changed since it was last read (sha256 {actual}, expected {want}); re-read it and regenerate the patch"
        conflicts.append({"path": key, "message": message})

    return {
        "root": root,
        "ops": [op for op, _h, _l in ops],
        "files": files,
        "conflicts": conflicts,
        "diff": "".join(diffs),
        "created_dirs": created_dirs,
    }


def _bounded_diff(diff, limit):
    if len(diff) <= limit:
        return diff, False
    return diff[:limit] + f"\n... [diff truncated: {len(diff) - limit} more characters]\n", True


# ---------------------------------------------------------------------------
# Locking
# ---------------------------------------------------------------------------


def lock_path_for(root):
    """Lock file shared with the remote-fleet engine: sha256(str(realpath(workdir)))."""
    lock_dir = os.path.join(os.path.expanduser("~"), ".local", "state", "remote-fleet", "locks")
    key = hashlib.sha256(root.encode("utf-8")).hexdigest()
    return lock_dir, os.path.join(lock_dir, key + ".lock")


def acquire_lock(root):
    lock_dir, path = lock_path_for(root)
    os.makedirs(lock_dir, mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except (IOError, OSError) as exc:
            if exc.errno == errno.ENOLCK:
                os.close(fd)
                raise PatchError("this filesystem does not support locking (ENOLCK); refusing to patch without the workdir lock")
            if exc.errno not in (errno.EAGAIN, errno.EWOULDBLOCK, errno.EACCES):
                os.close(fd)
                raise
        if time.monotonic() >= deadline:
            os.close(fd)
            raise PatchError("workdir is locked by another patch; retry shortly")
        time.sleep(LOCK_POLL_SECONDS)


def release_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Journal (backup dir + manifest)
# ---------------------------------------------------------------------------


def _validate_patch_id(value):
    if not isinstance(value, str) or not PATCH_ID_RE.match(value) or value.strip(".") == "":
        raise PatchError("patch_id must match [A-Za-z0-9_.-]{1,100} and not be '.' or '..'")
    return value


def _validate_backup_root(value):
    if not isinstance(value, str) or not value:
        raise PatchError("backup_root must be a non-empty string")
    path = os.path.expanduser(value)
    if not os.path.isabs(path) or "\0" in path:
        raise PatchError("backup_root must be an absolute path")
    return os.path.normpath(path)


def _write_json(path, data):
    write_replace(path, json.dumps(data, indent=2, sort_keys=True).encode("utf-8"), 0o600)


def _read_manifest(backup_dir):
    try:
        with open(os.path.join(backup_dir, MANIFEST_NAME), "rb") as handle:
            manifest = json.loads(handle.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise PatchError(f"invalid or missing backup manifest in {backup_dir}: {exc}")
    if not isinstance(manifest, dict) or not isinstance(manifest.get("files"), list) or "workdir" not in manifest:
        raise PatchError(f"invalid backup manifest in {backup_dir}")
    manifest.setdefault("state", "committed")  # version 1 manifests had no state
    return manifest


def _set_state(backup_dir, manifest, state):
    manifest["state"] = state
    manifest["updated_at"] = _utc_stamp(False)
    _write_json(os.path.join(backup_dir, MANIFEST_NAME), manifest)


def _retire(backup_dir, label):
    target = f"{backup_dir}.{label}-{_utc_stamp(True)}"
    candidate, n = target, 1
    while os.path.lexists(candidate):
        candidate, n = f"{target}.{n}", n + 1
    os.rename(backup_dir, candidate)
    _fsync_dir(os.path.dirname(backup_dir))
    return candidate


def _write_backup(backup_dir, root, p, patch_id, patch_sha256):
    """Copy originals and write the manifest in state "prepared"."""
    os.mkdir(os.path.join(backup_dir, "files"), 0o700)
    entries = []
    for i, op in enumerate(op for op in p["ops"] if not op.unchanged):
        backup = None
        if op.before is not None:
            backup = f"files/{i}"
            write_replace(os.path.join(backup_dir, backup), op.before, 0o600)
        entries.append(op.entry(backup))
    manifest = {
        "version": MANIFEST_VERSION,
        "patch_id": patch_id,
        "patch_sha256": patch_sha256,
        "workdir": root,
        "created_at": _utc_stamp(False),
        "created_dirs": [_rel(root, d) for d in p["created_dirs"]],
        "files": entries,
    }
    _set_state(backup_dir, manifest, "prepared")
    _fsync_dir(backup_dir)
    _fsync_dir(os.path.dirname(backup_dir))
    return manifest


def _load_backup(backup_dir, entry):
    backup = entry.get("backup")
    if backup is None:
        return None
    if not re.match(r"\Afiles/[0-9]+\Z", backup):
        raise PatchError(f"invalid manifest backup reference {backup!r}")
    with open(os.path.join(backup_dir, backup), "rb") as handle:
        data = handle.read()
    if _sha256(data) != entry.get("sha256_before"):
        raise PatchError(f"backup copy for {entry['path']} is corrupt (sha256 mismatch)")
    return data


def _entry_paths(root, entry):
    action = entry.get("action")
    if action not in ("add", "update", "delete", "move"):
        raise PatchError(f"invalid manifest action {action!r}")
    real = resolve_path(root, entry["path"])[1]
    move_real = resolve_path(root, entry["move_to"])[1] if action == "move" else None
    return real, move_real


def classify(root, entry):
    """Where an entry's paths are now: "after", "before", or "other".

    Idempotency hinges on this: an entry already in its pre-patch state needs
    no restore, so rollback after a crash works without force.
    """
    real, move_real = _entry_paths(root, entry)
    sha = lambda data: _sha256(data) if isinstance(data, bytes) else data  # noqa: E731
    cur = sha(_current(real))
    before, after = entry.get("sha256_before"), entry.get("sha256_after")
    action = entry["action"]
    if action == "add":
        return "before" if cur is None else "after" if cur == after else "other"
    if action == "delete":
        return "after" if cur is None else "before" if cur == before else "other"
    if action == "update":
        return "after" if cur == after else "before" if cur == before else "other"
    dest = sha(_current(move_real))
    if cur == before and dest is None:
        return "before"
    if cur is None and dest == after:
        return "after"
    if cur == before and dest == after:
        return "after"  # killed between writing the destination and removing the source
    return "other"


def restore_entry(root, entry, data):
    """Put one entry's paths back to their pre-patch state (keeps file identity)."""
    real, move_real = _entry_paths(root, entry)
    action = entry["action"]
    if action == "add":
        if os.path.isdir(real) and not os.path.islink(real):
            raise OSError(errno.EISDIR, "is a directory now; left in place")
        _unlink_quiet(real)
        _fsync_dir(os.path.dirname(real))
        return
    if action == "move":
        if _current(move_real) is not None:
            os.unlink(move_real)
            _fsync_dir(os.path.dirname(move_real))
    current = _current(real)
    if isinstance(current, bytes) and current == data:
        return
    if not os.path.isdir(os.path.dirname(real)):
        os.makedirs(os.path.dirname(real))
    if action == "update" and entry.get("strategy") == "inplace" and isinstance(current, bytes):
        write_inplace(real, data)
    else:
        write_replace(real, data, entry["mode"], entry.get("gid"))


# ---------------------------------------------------------------------------
# Mutation
# ---------------------------------------------------------------------------


def _verify_unchanged(op):
    """Re-check right before writing: catches editors that ignore the lock."""
    current = _current(op.real)
    expected = op.before if op.action != "add" else None
    if current != expected:
        raise ConcurrentChange(f"{op.path} changed after planning")
    if op.action == "move" and _current(op.move_real) is not None:
        raise ConcurrentChange(f"{op.move_to} appeared after planning")


def mutate(root, op):
    _verify_unchanged(op)
    if op.action == "add":
        _ensure_dirs(_missing_dirs(root, op.real))
        create_exclusive(op.real, op.after, op.mode)
    elif op.action == "delete":
        os.unlink(op.real)
        _fsync_dir(os.path.dirname(op.real))
    elif op.action == "update":
        if op.strategy == "inplace":
            write_inplace(op.real, op.after)
        else:
            write_replace(op.real, op.after, op.mode, op.gid)
    elif op.action == "move":
        _ensure_dirs(_missing_dirs(root, op.move_real))
        create_exclusive(op.move_real, op.after, op.mode)
        os.unlink(op.real)
        _fsync_dir(os.path.dirname(op.real))


class _SignalGuard(object):
    """Turn SIGTERM/SIGHUP/SIGINT into Interrupted while mutating, so we restore."""

    SIGNALS = ("SIGTERM", "SIGHUP", "SIGINT")

    def __enter__(self):
        self.previous = {}
        for name in self.SIGNALS:
            signum = getattr(signal, name, None)
            if signum is not None:
                self.previous[signum] = signal.signal(signum, self._raise)
        return self

    def _raise(self, signum, _frame):
        raise Interrupted(f"signal {signum}")

    def __exit__(self, *_exc):
        for signum, handler in self.previous.items():
            signal.signal(signum, handler)
        return False


# ---------------------------------------------------------------------------
# Backup retention
# ---------------------------------------------------------------------------


def _dir_bytes(path):
    total = 0
    for dirpath, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def prune_backups(backup_root, now=None):
    """Drop old or excess backups, oldest first; never one still "prepared".

    Limits: retired (rolled back / restored) after RETIRED_RETENTION_SECONDS;
    committed after BACKUP_RETENTION_SECONDS or beyond the newest MAX_BACKUPS;
    then anything removable until the total is within MAX_BACKUP_BYTES.
    """
    now = time.time() if now is None else now
    try:
        names = os.listdir(backup_root)
    except OSError:
        return []
    removed, keep = [], []  # keep: (mtime, name, path, size, removable)

    def drop(name, path):
        shutil.rmtree(path, ignore_errors=True)
        removed.append(name)

    committed = []
    for name in names:
        path = os.path.join(backup_root, name)
        try:
            mtime = os.lstat(path).st_mtime
        except OSError:
            continue
        if RETIRED_RE.search(name):
            if now - mtime > RETIRED_RETENTION_SECONDS:
                drop(name, path)
            else:
                keep.append([mtime, name, path, True])
            continue
        try:
            state = _read_manifest(path)["state"]
        except PatchError:
            continue
        if state == "committed":
            committed.append([mtime, name, path, True])
        else:
            keep.append([mtime, name, path, False])  # "prepared": recovery depends on it
    committed.sort(reverse=True)  # newest first
    for rank, item in enumerate(committed):
        if now - item[0] > BACKUP_RETENTION_SECONDS or rank >= MAX_BACKUPS:
            drop(item[1], item[2])
        else:
            keep.append(item)
    sized = [(mtime, name, path, removable, _dir_bytes(path)) for mtime, name, path, removable in keep]
    total = sum(item[4] for item in sized)
    for mtime, name, path, removable, size in sorted(sized):
        if total <= MAX_BACKUP_BYTES:
            break
        if removable:
            drop(name, path)
            total -= size
    return removed


def _free_bytes(path):
    """Free bytes for an unprivileged user on the filesystem holding path (or its nearest existing parent)."""
    while not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent
    st = os.statvfs(path)
    return st.f_bavail * st.f_frsize, os.stat(path).st_dev


def check_free_space(root, backup_root, ops):
    """PatchError unless backups plus new contents fit with FREE_SPACE_RESERVE_BYTES to spare."""
    need = {}
    for path, size in (
        (backup_root, sum(len(op.before) for op in ops if op.before is not None)),
        (root, sum(len(op.after) for op in ops if op.after is not None)),
    ):
        free, dev = _free_bytes(path)
        have, want = need.get(dev, (free, 0))
        need[dev] = (have, want + size)
    for free, want in need.values():
        if free - want < FREE_SPACE_RESERVE_BYTES:
            raise PatchError(
                f"not enough free disk space: this patch needs {want} bytes (backups + new contents) and "
                f"{FREE_SPACE_RESERVE_BYTES} must remain free, but only {free} are available; nothing was changed"
            )


def interrupted_patches(backup_root, root, exclude):
    """Manifests for this workdir still "prepared": a previous apply died mid-way."""
    found = []
    try:
        names = os.listdir(backup_root)
    except OSError:
        return found
    for name in names:
        if name == exclude or RETIRED_RE.search(name):
            continue
        try:
            manifest = _read_manifest(os.path.join(backup_root, name))
        except PatchError:
            continue
        if manifest["state"] == "prepared" and manifest.get("workdir") == root:
            paths = set()
            for entry in manifest["files"]:
                paths.add(entry["path"])
                if entry.get("move_to"):
                    paths.add(entry["move_to"])
            found.append((name, paths))
    return found


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def _max_diff(request):
    value = request.get("max_diff_chars")
    if value is None:
        return DEFAULT_MAX_DIFF_CHARS
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PatchError("max_diff_chars must be a non-negative integer")
    return value


def _flag(request, name):
    value = request.get(name, False)
    if not isinstance(value, bool):
        raise PatchError(f"{name} must be a boolean")
    return value


def _result(mode, p, max_diff, state):
    diff, truncated = _bounded_diff(p["diff"], max_diff)
    return {
        "ok": not p["conflicts"],
        "mode": mode,
        "state": state,
        "files": p["files"],
        "conflicts": p["conflicts"],
        "diff": diff,
        "diff_truncated": truncated,
    }


def run_dry_run(request):
    p = plan(request.get("workdir"), request.get("patch"), request.get("expected_sha256"), _flag(request, "strict"))
    return 0, _result("dry_run", p, _max_diff(request), "conflict" if p["conflicts"] else "clean")


def _rollback_entries(root, backup_dir, manifest, force):
    """Shared by rollback and apply-recovery. Returns (restored, conflicts, errors, saved)."""
    items = []
    conflicts = []
    for entry in manifest["files"]:
        where = classify(root, entry)
        items.append((entry, where))
        if where == "other":
            conflicts.append({"path": entry.get("move_to") or entry["path"], "message": "changed since the patch was applied"})
    if conflicts and not force:
        return [], conflicts, [], []
    saved = []
    if conflicts:
        # force: keep whatever we are about to overwrite.
        forced_dir = os.path.join(backup_dir, f"forced-{_utc_stamp(True)}")
        os.makedirs(forced_dir, mode=0o700, exist_ok=True)
        for i, (entry, where) in enumerate(items):
            if where != "other":
                continue
            for rel in (entry["path"], entry.get("move_to")):
                if not rel:
                    continue
                data = _current(resolve_path(root, rel)[1])
                if isinstance(data, bytes):
                    name = f"{i}-{os.path.basename(rel)}"
                    write_replace(os.path.join(forced_dir, name), data, 0o600)
                    saved.append({"path": rel, "saved_as": os.path.join(forced_dir, name)})
    restored, errors = [], []
    for entry, where in reversed(items):
        if where == "before":
            continue
        try:
            restore_entry(root, entry, _load_backup(backup_dir, entry))
            restored.append(entry["path"])
        except (OSError, PatchError) as exc:
            errors.append({"path": entry["path"], "message": str(exc)})
    dirs = []
    for rel in manifest.get("created_dirs", []):
        try:
            dirs.append(resolve_path(root, rel)[1])
        except PatchError:
            pass
    _remove_empty_dirs(dirs)
    return restored, conflicts, errors, saved


def run_apply(request):
    max_diff = _max_diff(request)
    strict = _flag(request, "strict")
    patch_id = _validate_patch_id(request.get("patch_id"))
    backup_root = _validate_backup_root(request.get("backup_root"))
    root = _resolve_workdir(request.get("workdir"))
    backup_dir = os.path.join(backup_root, patch_id)
    patch_text = request.get("patch")
    patch_sha256 = _sha256(patch_text.encode("utf-8")) if isinstance(patch_text, str) else None

    lock_fd = acquire_lock(root)
    try:
        prune_backups(backup_root)
        warnings = []
        if os.path.lexists(backup_dir):
            manifest = _read_manifest(backup_dir)
            if manifest.get("workdir") != root or manifest.get("patch_sha256") not in (None, patch_sha256):
                raise PatchError(f"patch_id {patch_id!r} was already used for a different patch or workdir; use a new patch_id")
            if manifest["state"] == "committed":
                states = {classify(root, e) for e in manifest["files"]}
                if states <= {"after"}:
                    files = [{"path": e["path"], "action": e["action"]} for e in manifest["files"]]
                    return 0, {"ok": True, "mode": "apply", "state": "already_applied", "patch_id": patch_id,
                               "backup_dir": backup_dir, "files": files, "conflicts": [], "diff": "", "diff_truncated": False}
                raise PatchError(f"patch_id {patch_id!r} was already applied and the files changed since; use a new patch")
            # "prepared": an earlier attempt with this id died mid-way. Undo it, then start clean.
            restored, conflicts, errors, _saved = _rollback_entries(root, backup_dir, manifest, force=False)
            if conflicts or errors:
                return 0, {"ok": False, "mode": "apply", "state": "partial", "patch_id": patch_id, "backup_dir": backup_dir,
                           "conflicts": conflicts + errors, "files": [], "diff": "", "diff_truncated": False,
                           "error": "an earlier interrupted attempt with this patch_id could not be undone automatically; "
                                    "inspect the files, then orca_rollback with force"}
            _retire(backup_dir, "interrupted")
            warnings.append(f"recovered an interrupted earlier attempt with this patch_id (restored: {', '.join(restored) or 'nothing'})")

        p = plan(root, patch_text, request.get("expected_sha256"), strict)
        touched = {f["path"] for f in p["files"]} | {f["move_to"] for f in p["files"] if f.get("move_to")}
        for other, paths in interrupted_patches(backup_root, root, patch_id):
            overlap = sorted(paths & touched)
            if overlap:
                for path in overlap:
                    p["conflicts"].append({"path": path, "message": f"left half-applied by interrupted patch {other}; run orca_rollback {other} first"})
            else:
                warnings.append(f"interrupted patch {other} is still pending in this workdir; consider orca_rollback {other}")
        if p["conflicts"]:
            result = _result("apply", p, max_diff, "conflict")
            if warnings:
                result["warnings"] = warnings
            return 0, result
        result = _result("apply", p, max_diff, "applied")
        result["patch_id"] = patch_id
        if warnings:
            result["warnings"] = warnings
        changing = [op for op in p["ops"] if not op.unchanged]
        if not changing:
            result["state"] = "unchanged"
            return 0, result

        check_free_space(root, backup_root, changing)
        os.makedirs(backup_root, mode=0o700, exist_ok=True)
        try:
            os.mkdir(backup_dir, 0o700)
            manifest = _write_backup(backup_dir, root, p, patch_id, patch_sha256)
        except OSError as exc:
            if os.path.isdir(backup_dir):
                shutil.rmtree(backup_dir, ignore_errors=True)
            raise PatchError(f"could not write backup (workdir untouched): {exc}")
        result["backup_dir"] = backup_dir

        done = []
        try:
            with _SignalGuard():
                for op in changing:
                    done.append(op)
                    mutate(root, op)
                _set_state(backup_dir, manifest, "committed")
        except (Exception, Interrupted) as exc:
            reason = f"{done[-1].path if done else '?'}: {exc}"
            entries = {e["path"]: e for e in manifest["files"]}
            restore_errors = []
            for op in reversed(done):
                entry = entries[op.path]
                try:
                    # A concurrent writer's bytes are not ours to overwrite.
                    if isinstance(exc, ConcurrentChange) and op is done[-1]:
                        continue
                    if classify(root, entry) != "before":
                        restore_entry(root, entry, op.before)
                except Exception as rexc:  # keep going: restore as much as possible
                    restore_errors.append({"path": op.path, "message": str(rexc)})
            _remove_empty_dirs(p["created_dirs"])
            result["ok"] = False
            if isinstance(exc, ConcurrentChange):
                result["conflicts"] = [{"path": done[-1].path, "message": f"{exc}: another process edited it; nothing of this patch was kept"}]
            if restore_errors:
                result["state"] = "partial"
                result["restore_errors"] = restore_errors
                result["error"] = f"apply failed at {reason}; restoring failed too: workdir is PARTIALLY APPLIED; run orca_rollback {patch_id}"
            else:
                result["state"] = "restored"
                result["error"] = f"apply failed at {reason}; every touched file was restored"
                try:
                    _set_state(backup_dir, manifest, "restored")
                    result["backup_dir"] = _retire(backup_dir, "restored")
                except OSError:
                    pass
            return 0, result
        return 0, result
    finally:
        release_lock(lock_fd)


def _find_backup(backup_root, patch_id):
    """(path, retired_label) of the newest backup dir for patch_id, or (None, None)."""
    exact = os.path.join(backup_root, patch_id)
    if os.path.isdir(exact):
        return exact, None
    try:
        names = sorted(n for n in os.listdir(backup_root) if n.startswith(patch_id + ".") and RETIRED_RE.search(n))
    except OSError:
        names = []
    if not names:
        return None, None
    name = names[-1]
    return os.path.join(backup_root, name), RETIRED_RE.search(name).group(1)


def run_rollback(request):
    patch_id = _validate_patch_id(request.get("patch_id"))
    backup_root = _validate_backup_root(request.get("backup_root"))
    force = _flag(request, "force")
    backup_dir, retired = _find_backup(backup_root, patch_id)
    if backup_dir is None:
        raise PatchError(f"no backup for patch_id {patch_id!r} under {backup_root}")
    if retired is not None:
        return 0, {"ok": True, "mode": "rollback", "state": "already_rolled_back", "patch_id": patch_id,
                   "note": f"nothing to do: this patch was {retired.replace('-', ' ')} ({os.path.basename(backup_dir)})"}
    manifest = _read_manifest(backup_dir)
    if manifest.get("patch_id") != patch_id:
        raise PatchError("backup manifest patch_id does not match")
    root = manifest["workdir"]
    if not isinstance(root, str) or not os.path.isdir(root) or os.path.realpath(root) != root:
        raise PatchError(f"manifest workdir is not an existing real directory: {root!r}")

    lock_fd = acquire_lock(root)
    try:
        restored, conflicts, errors, saved = _rollback_entries(root, backup_dir, manifest, force)
        base = {"mode": "rollback", "patch_id": patch_id, "was": manifest["state"], "workdir": root}
        if conflicts and not force:
            return 0, dict(base, ok=False, state="conflict", conflicts=conflicts,
                           hint="files changed after the patch; force=true overwrites them (their current contents are saved first)")
        result = dict(base, ok=not errors, restored=restored)
        if force:
            result["forced"] = True
            result["conflicts"] = conflicts
            result["saved"] = saved
        if errors:
            result.update(state="partial", errors=errors, error="rollback incomplete; backup kept in place, retry or fix the listed paths")
            return 0, result
        _set_state(backup_dir, manifest, "rolled_back")
        retired = _retire(backup_dir, "rolled-back")
        for item in result.get("saved", []):  # forced copies moved with the backup dir
            item["saved_as"] = retired + item["saved_as"][len(backup_dir):]
        result.update(state="rolled_back", backup_dir=retired)
        return 0, result
    finally:
        release_lock(lock_fd)


def run_status(request):
    """Where did a patch_id end up? Used after a timeout or a lost reply."""
    patch_id = _validate_patch_id(request.get("patch_id"))
    backup_root = _validate_backup_root(request.get("backup_root"))
    backup_dir, retired = _find_backup(backup_root, patch_id)
    if backup_dir is None:
        return 0, {"ok": True, "mode": "status", "state": "not_found", "patch_id": patch_id}
    manifest = _read_manifest(backup_dir)
    state = {"rolled-back": "rolled_back", "restored": "restored", "interrupted": "restored"}.get(retired, manifest["state"])
    files = []
    root = manifest["workdir"]
    for entry in manifest["files"]:
        try:
            where = classify(root, entry)
        except (PatchError, OSError) as exc:
            where = f"error: {exc}"
        files.append({"path": entry["path"], "action": entry["action"], "now": where})
    return 0, {"ok": True, "mode": "status", "state": state, "patch_id": patch_id, "backup_dir": backup_dir, "files": files}


MODES = {"dry_run": run_dry_run, "apply": run_apply, "rollback": run_rollback, "status": run_status}


def main(stdin_text):
    """Process one JSON request. Returns (exit_code, result_dict)."""
    try:
        try:
            request = json.loads(stdin_text)
        except ValueError as exc:
            raise PatchError(f"request is not valid JSON: {exc}")
        if not isinstance(request, dict):
            raise PatchError("request must be a JSON object")
        unknown = sorted(set(request) - REQUEST_KEYS)
        if unknown:
            raise PatchError(f"unknown request field(s): {', '.join(unknown)}")
        handler = MODES.get(request.get("mode"))
        if handler is None:
            raise PatchError("mode must be one of " + ", ".join(sorted(MODES)))
        return handler(request)
    except PatchError as exc:
        return 1, {"error": str(exc)}
    except Exception as exc:  # never crash without a JSON answer
        return 1, {"error": f"internal error: {type(exc).__name__}: {exc}"}


def _entrypoint():
    raw = sys.stdin.buffer.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        code, result = 1, {"error": "request is not valid UTF-8"}
    else:
        code, result = main(text)
    sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
    sys.stdout.flush()
    sys.exit(code)


if __name__ == "__main__":
    _entrypoint()
