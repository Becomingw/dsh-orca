"""orca structured-log follower (remote program; stdlib only, Python 3.6+).

Runs beside a launched job and *follows* the tee log file instead of sitting
in the job's pipe, so it can never apply backpressure to the training
process. It maintains, under ``<log>.orca/``:

  tail.ring        last N normalized lines, rewritten atomically (cheap tail)
  events.ndjson    {"ts","level","msg"} per normalized line
  events-NNNN.ndjson.gz  rotated segments, newest = highest number
  meta.json        finished_at / exit_code / counters are merged in at exit

Usage: python3 logger.py LOG_PATH ORCA_DIR START_OFFSET TAIL_LINES ROTATE_BYTES KEEP_SEGMENTS
SIGTERM triggers a final drain and finalization; see docs/logging.md.
"""

import collections
import gzip
import json
import os
import re
import signal
import sys
import time
from datetime import datetime, timezone

POLL_SECONDS = 0.5
RING_FLUSH_SECONDS = 1.0
MAX_LINE_BYTES = 4096
READ_CHUNK = 1 << 20
# Beyond this unread backlog the follower skips ahead instead of lagging forever.
MAX_BACKLOG_BYTES = 64 << 20
SKIP_KEEP_BYTES = 1 << 20

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")
ERROR_RE = re.compile(r"\b(?:error|exception|fatal|killed|out of memory|nan loss|nan)\b", re.I)
# Exception class names: RuntimeError, ValueError, CUDAException, ...
EXCEPTION_RE = re.compile(r"Traceback \(most recent call last\)|\b[A-Z][A-Za-z0-9]*(?:Error|Exception)\b")
WARN_RE = re.compile(r"\bwarn(?:ing)?\b|\b[A-Z][A-Za-z0-9]*Warning\b", re.I)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def atomic_write(path, data):
    tmp = "{}.{}.tmp".format(path, os.getpid())
    with open(tmp, "wb") as handle:
        handle.write(data)
    os.replace(tmp, path)


def normalize(raw):
    """One physical line -> display text (ANSI stripped, \\r progress collapsed, bounded)."""
    text = raw.decode("utf-8", "replace")
    if "\r" in text:
        parts = [part for part in text.split("\r") if part.strip()]
        text = parts[-1] if parts else ""
    text = ANSI_RE.sub("", text).rstrip()
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_LINE_BYTES:
        text = encoded[:MAX_LINE_BYTES].decode("utf-8", "ignore") + " …[{} bytes]".format(len(encoded))
    return text


def level_of(text):
    if ERROR_RE.search(text) or EXCEPTION_RE.search(text):
        return "error"
    if WARN_RE.search(text):
        return "warn"
    return "info"


class Follower(object):
    def __init__(self, log_path, orca_dir, offset, tail_lines, rotate_bytes, keep_segments):
        self.log_path = log_path
        self.orca_dir = orca_dir
        self.offset = offset
        self.ring = collections.deque(maxlen=max(1, tail_lines))
        self.rotate_bytes = rotate_bytes
        self.keep_segments = keep_segments
        self.pending = b""
        self.last_text = None
        self.repeat = 0
        self.ring_dirty = False
        self.ring_flushed = 0.0
        self.lines = 0
        self.skipped_bytes = 0
        self.levels = {"info": 0, "warn": 0, "error": 0}
        self.events_path = os.path.join(orca_dir, "events.ndjson")
        self.events = open(self.events_path, "ab")
        self.load_ring()

    def load_ring(self):
        try:
            with open(os.path.join(self.orca_dir, "tail.ring"), "rb") as handle:
                for line in handle.read().decode("utf-8", "replace").splitlines():
                    self.ring.append(line)
        except OSError:
            pass

    # -- line pipeline ---------------------------------------------------------------

    def write_event(self, level, msg, repeat=None):
        record = {"ts": utc_now(), "level": level, "msg": msg}
        if repeat:
            record["repeat"] = repeat
        self.events.write((json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8"))

    def push(self, text):
        """Emit a line at once; fold consecutive duplicates into ``msg ×N``."""
        if text == "":
            return
        if text == self.last_text:
            self.repeat += 1
            self.ring[-1] = "{} ×{}".format(text, self.repeat + 1)
            self.ring_dirty = True
            return
        self.flush_repeat()
        level = level_of(text)
        self.levels[level] += 1
        self.lines += 1
        self.ring.append(text)
        self.ring_dirty = True
        self.write_event(level, text)
        self.last_text = text
        self.repeat = 0

    def flush_repeat(self):
        """Record how often the previous line repeated, once the run ends."""
        if self.last_text is not None and self.repeat:
            self.write_event(level_of(self.last_text), self.last_text, repeat=self.repeat)
        self.repeat = 0

    # -- io --------------------------------------------------------------------------

    def read_new(self, final=False):
        try:
            size = os.path.getsize(self.log_path)
        except OSError:
            return False
        if size < self.offset:  # truncated or replaced: start over
            self.offset = 0
        if size - self.offset > MAX_BACKLOG_BYTES:
            new_offset = size - SKIP_KEEP_BYTES
            self.skipped_bytes += new_offset - self.offset
            self.offset = new_offset
            self.pending = b""
            self.push("[orca] follower fell behind; skipped {} bytes".format(self.skipped_bytes))
        progressed = False
        with open(self.log_path, "rb") as handle:
            handle.seek(self.offset)
            while True:
                chunk = handle.read(READ_CHUNK)
                if not chunk:
                    break
                progressed = True
                self.offset += len(chunk)
                data = self.pending + chunk
                lines = data.split(b"\n")
                self.pending = lines.pop()
                for raw in lines:
                    self.push(normalize(raw))
                if len(self.pending) > 4 * MAX_LINE_BYTES:
                    self.push(normalize(self.pending))
                    self.pending = b""
        if final and self.pending:
            self.push(normalize(self.pending))
            self.pending = b""
        return progressed

    def flush(self, force=False):
        if force:
            self.flush_repeat()
            self.last_text = None
        self.events.flush()
        now = time.time()
        if self.ring_dirty and (force or now - self.ring_flushed >= RING_FLUSH_SECONDS):
            body = "\n".join(self.ring) + ("\n" if self.ring else "")
            atomic_write(os.path.join(self.orca_dir, "tail.ring"), body.encode("utf-8"))
            self.ring_dirty = False
            self.ring_flushed = now
        if self.rotate_bytes > 0 and self.events.tell() >= self.rotate_bytes:
            self.rotate()

    def rotate(self):
        self.events.close()
        numbers = sorted(
            int(name[7:11])
            for name in os.listdir(self.orca_dir)
            if re.match(r"events-\d{4}\.ndjson\.gz$", name)
        )
        number = (numbers[-1] + 1) if numbers else 1
        target = os.path.join(self.orca_dir, "events-{:04d}.ndjson.gz".format(number))
        with open(self.events_path, "rb") as src, gzip.open(target + ".tmp", "wb") as dst:
            while True:
                block = src.read(READ_CHUNK)
                if not block:
                    break
                dst.write(block)
        os.replace(target + ".tmp", target)
        os.unlink(self.events_path)
        numbers.append(number)
        for old in numbers[: max(0, len(numbers) - self.keep_segments)]:
            try:
                os.unlink(os.path.join(self.orca_dir, "events-{:04d}.ndjson.gz".format(old)))
            except OSError:
                pass
        self.events = open(self.events_path, "ab")

    def finalize(self):
        meta_path = os.path.join(self.orca_dir, "meta.json")
        try:
            with open(meta_path, "r") as handle:
                meta = json.load(handle)
        except (OSError, ValueError):
            meta = {}
        status_path = self.log_path + ".remote-fleet-status.json"
        try:
            with open(status_path, "r") as handle:
                status = json.load(handle)
            meta["exit_code"] = status.get("exit_code")
            meta["finished_at"] = status.get("finished_at")
        except (OSError, ValueError):
            meta.setdefault("finished_at", utc_now())
        meta["logger"] = {
            "lines": self.lines,
            "levels": self.levels,
            "skipped_bytes": self.skipped_bytes,
            "offset": self.offset,
        }
        atomic_write(meta_path, (json.dumps(meta, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
        self.events.close()


def main(argv):
    log_path, orca_dir = argv[1], argv[2]
    offset, tail_lines, rotate_bytes, keep_segments = (int(value) for value in argv[3:7])
    stop_file = os.path.join(orca_dir, ".stop")
    signalled = []
    signal.signal(signal.SIGTERM, lambda *_: signalled.append(True))
    signal.signal(signal.SIGHUP, lambda *_: signalled.append(True))

    def stopping():
        # The wrapper creates the stop file when the job ends; orca_stop signals.
        return bool(signalled) or os.path.exists(stop_file)

    deadline = time.time() + 30
    while not os.path.exists(log_path) and not stopping() and time.time() < deadline:
        time.sleep(0.1)
    follower = Follower(log_path, orca_dir, offset, tail_lines, rotate_bytes, keep_segments)
    while not stopping():
        if not follower.read_new():
            time.sleep(POLL_SECONDS)
        follower.flush()
    follower.read_new(final=True)
    follower.flush(force=True)
    follower.finalize()
    try:
        os.unlink(stop_file)
    except OSError:
        pass


if __name__ == "__main__":
    main(sys.argv)
