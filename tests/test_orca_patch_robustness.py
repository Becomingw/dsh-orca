"""Robustness tests for orca_patch_apply.py: crashes, concurrency, identity, feedback.

Each test pins one failure mode found in review (docs/engine-changes.md, "orca_patch").
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parent.parent / "engine" / "orca_patch_apply.py"
SPEC = importlib.util.spec_from_file_location("orca_patch_apply_rb", MODULE_PATH)
opa = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(opa)


def P(*body):
    return "\n".join(("*** Begin Patch",) + body + ("*** End Patch",)) + "\n"


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.home = base / "home"
        self.home.mkdir()
        self.work = base / "work"
        self.work.mkdir()
        self.backups = base / "backups"
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._tmp.cleanup)

    def write(self, rel, data, mode=None):
        path = self.work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode() if isinstance(data, str) else data)
        if mode is not None:
            os.chmod(path, mode)
        return path

    def read(self, rel):
        return (self.work / rel).read_bytes()

    def snapshot(self):
        return {str(p.relative_to(self.work)): (p.read_bytes(), p.stat().st_mode & 0o7777)
                for p in sorted(self.work.rglob("*")) if p.is_file()}

    def request(self, mode, patch=None, **extra):
        req = {"mode": mode, "backup_root": str(self.backups)}
        if mode in ("apply", "dry_run"):
            req.update(workdir=str(self.work), patch=patch)
        if mode != "dry_run":
            req.setdefault("patch_id", "p1")
        if mode == "dry_run":
            req.pop("backup_root")
        req.update(extra)
        return req

    def call(self, mode, patch=None, **extra):
        return opa.main(json.dumps(self.request(mode, patch, **extra)))

    def run_killed(self, patch, die_on_op, sig):
        """Run the shipped source in a subprocess that receives `sig` while mutating op #die_on_op."""
        harness = textwrap.dedent(f"""
            import importlib.util, json, os, signal, sys
            spec = importlib.util.spec_from_file_location("m", {str(MODULE_PATH)!r})
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
            real, count = m.mutate, [0]
            def mutate(root, op):
                count[0] += 1
                if count[0] == {die_on_op}:
                    os.kill(os.getpid(), {int(sig)})
                    import time; time.sleep(5)
                return real(root, op)
            m.mutate = mutate
            code, res = m.main(sys.stdin.read())
            print(json.dumps(res)); sys.exit(code)
        """)
        return subprocess.run([sys.executable, "-c", harness], input=json.dumps(self.request("apply", patch)),
                              text=True, capture_output=True, env=dict(os.environ, HOME=str(self.home)), timeout=60)


THREE = P(
    "*** Update File: a.txt", "@@", "-a", "+A",
    "*** Update File: b.txt", "@@", "-b", "+B",
    "*** Add File: c.txt", "+c",
)


class CrashRecoveryTests(Base):
    def setUp(self):
        super().setUp()
        self.write("a.txt", "a\n")
        self.write("b.txt", "b\n")
        self.before = self.snapshot()

    def test_sigkill_mid_apply_leaves_prepared_journal_and_rollback_recovers_without_force(self):
        proc = self.run_killed(THREE, die_on_op=2, sig=signal.SIGKILL)
        self.assertEqual(proc.returncode, -signal.SIGKILL)
        self.assertEqual(self.read("a.txt"), b"A\n")  # half-applied
        self.assertEqual(self.read("b.txt"), b"b\n")
        code, status = self.call("status")
        self.assertEqual(status["state"], "prepared")
        self.assertEqual({f["path"]: f["now"] for f in status["files"]}, {"a.txt": "after", "b.txt": "before", "c.txt": "before"})
        code, res = self.call("rollback")
        self.assertEqual((code, res["ok"], res["state"]), (0, True, "rolled_back"), res)
        self.assertEqual(self.snapshot(), self.before)

    def test_retry_with_same_patch_id_recovers_and_applies(self):
        self.run_killed(THREE, die_on_op=2, sig=signal.SIGKILL)
        code, res = self.call("apply", THREE)
        self.assertEqual((code, res["state"]), (0, "applied"), res)
        self.assertTrue(any("interrupted" in w for w in res["warnings"]))
        self.assertEqual((self.read("a.txt"), self.read("b.txt"), self.read("c.txt")), (b"A\n", b"B\n", b"c\n"))

    def test_other_patch_on_half_applied_file_is_blocked(self):
        self.run_killed(THREE, die_on_op=2, sig=signal.SIGKILL)
        code, res = self.call("apply", P("*** Update File: a.txt", "@@", "-A", "+AA"), patch_id="p2")
        self.assertEqual(res["state"], "conflict")
        self.assertIn("orca_rollback p1", res["conflicts"][0]["message"])

    def test_sigterm_mid_apply_restores_everything(self):
        proc = self.run_killed(THREE, die_on_op=2, sig=signal.SIGTERM)
        res = json.loads(proc.stdout)
        self.assertEqual((proc.returncode, res["state"]), (0, "restored"), proc.stderr)
        self.assertEqual(self.snapshot(), self.before)

    def test_killed_between_move_destination_and_source_unlink(self):
        self.write("m.txt", "m\n")
        before = self.snapshot()
        patch = P("*** Update File: m.txt", "*** Move to: n.txt", "@@", "-m", "+M")
        real_unlink = os.unlink

        def crash_on_source_unlink(path, *a, **k):
            if str(path).endswith("m.txt"):
                raise KeyboardInterrupt  # stands in for SIGKILL: escapes every except clause
            return real_unlink(path, *a, **k)

        with mock.patch.object(opa.os, "unlink", crash_on_source_unlink), self.assertRaises(KeyboardInterrupt):
            self.call("apply", patch)
        self.assertTrue((self.work / "n.txt").exists() and (self.work / "m.txt").exists())
        code, res = self.call("rollback")
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.snapshot(), before)


class ResultContractTests(Base):
    def test_restore_failure_is_reported_as_partial_with_patch_id(self):
        self.write("a.txt", "a\n")
        self.write("b.txt", "b\n")
        real_replace, calls = os.replace, []

        def flaky(src, dst):
            calls.append(dst)
            if str(dst).endswith("b.txt") or (str(dst).endswith("a.txt") and len([c for c in calls if str(c).endswith("a.txt")]) > 1):
                raise OSError(28, "No space left on device")
            return real_replace(src, dst)

        with mock.patch.object(opa.os, "replace", flaky):
            code, res = self.call("apply", P("*** Update File: a.txt", "@@", "-a", "+A", "*** Update File: b.txt", "@@", "-b", "+B"))
        self.assertEqual(code, 0)
        self.assertEqual(res["state"], "partial")
        self.assertEqual(res["patch_id"], "p1")
        self.assertIn("PARTIALLY APPLIED", res["error"])
        self.assertTrue(res["restore_errors"])
        # The journal stays "prepared", so a plain rollback finishes the job.
        res = self.call("rollback")[1]
        self.assertEqual(res["state"], "rolled_back", res)
        self.assertEqual(self.read("a.txt"), b"a\n")

    def test_status_reports_each_lifecycle_state(self):
        self.write("a.txt", "a\n")
        self.assertEqual(self.call("status")[1]["state"], "not_found")
        self.call("apply", P("*** Update File: a.txt", "@@", "-a", "+A"))
        self.assertEqual(self.call("status")[1]["state"], "committed")
        self.call("rollback")
        self.assertEqual(self.call("status")[1]["state"], "rolled_back")

    def test_noop_update_writes_nothing(self):
        path = self.write("a.txt", "a\n")
        inode = path.stat().st_ino
        code, res = self.call("apply", P("*** Update File: a.txt", "@@", " a"))
        self.assertEqual(res["state"], "unchanged")
        self.assertEqual(path.stat().st_ino, inode)
        self.assertFalse(self.backups.exists())


class ConcurrencyTests(Base):
    def with_intrusion(self, when_path, action):
        real = opa.mutate

        def mutate(root, op):
            if op.path == when_path:
                action()
            return real(root, op)

        return mock.patch.object(opa, "mutate", mutate)

    def test_concurrent_create_of_add_target_is_never_overwritten(self):
        self.write("a.txt", "a\n")
        patch = P("*** Update File: a.txt", "@@", "-a", "+A", "*** Add File: new.txt", "+ours")
        with self.with_intrusion("new.txt", lambda: self.write("new.txt", "theirs\n")):
            code, res = self.call("apply", patch)
        self.assertEqual((code, res["state"]), (0, "restored"), res)
        self.assertEqual(self.read("new.txt"), b"theirs\n")
        self.assertEqual(self.read("a.txt"), b"a\n")
        self.assertIn("another process", res["conflicts"][0]["message"])

    def test_edit_by_lock_ignoring_editor_is_detected_and_kept(self):
        self.write("a.txt", "a\n")
        self.write("b.txt", "b\n")
        patch = P("*** Update File: a.txt", "@@", "-a", "+A", "*** Update File: b.txt", "@@", "-b", "+B")
        with self.with_intrusion("b.txt", lambda: self.write("b.txt", "b\nepochs: 99\n")):
            code, res = self.call("apply", patch)
        self.assertEqual(res["state"], "restored", res)
        self.assertEqual(self.read("b.txt"), b"b\nepochs: 99\n")
        self.assertEqual(self.read("a.txt"), b"a\n")


class IdentityTests(Base):
    def test_hard_links_keep_sharing_content(self):
        path = self.write("a.txt", "a\n")
        other = self.work / "link.txt"
        os.link(path, other)
        self.assertTrue(self.call("apply", P("*** Update File: a.txt", "@@", "-a", "+A"))[1]["ok"])
        self.assertEqual(other.read_bytes(), b"A\n")
        self.assertEqual(path.stat().st_ino, other.stat().st_ino)

    def test_plain_files_use_atomic_replace(self):
        path = self.write("a.txt", "a\n", 0o640)
        inode = path.stat().st_ino
        self.call("apply", P("*** Update File: a.txt", "@@", "-a", "+A"))
        self.assertNotEqual(path.stat().st_ino, inode)
        self.assertEqual(path.stat().st_mode & 0o777, 0o640)

    def test_group_is_preserved_when_possible(self):
        groups = [g for g in os.getgroups() if g != os.getegid()]
        if not groups:
            self.skipTest("user is in a single group")
        path = self.write("a.txt", "a\n")
        os.chown(path, -1, groups[0])
        self.call("apply", P("*** Update File: a.txt", "@@", "-a", "+A"))
        self.assertEqual(path.stat().st_gid, groups[0])

    @unittest.skipIf(os.geteuid() == 0, "root bypasses permissions")
    def test_read_only_file_and_directory_are_conflicts_in_dry_run(self):
        self.write("ro.txt", "a\n", 0o444)
        res = self.call("dry_run", P("*** Update File: ro.txt", "@@", "-a", "+A"))[1]
        self.assertIn("read-only", res["conflicts"][0]["message"])
        locked = self.work / "locked"
        locked.mkdir()
        self.write("locked/x.txt", "x\n")
        os.chmod(locked, 0o555)
        self.addCleanup(os.chmod, locked, 0o755)
        res = self.call("dry_run", P("*** Delete File: locked/x.txt", "*** Add File: locked/y.txt", "+y"))[1]
        self.assertEqual(len(res["conflicts"]), 2, res)

    def test_bom_is_kept_and_does_not_block_line_one(self):
        self.write("b.txt", "﻿first\nsecond\n".encode("utf-8"))
        res = self.call("apply", P("*** Update File: b.txt", "@@", "-first", "+FIRST"))[1]
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.read("b.txt"), "﻿FIRST\nsecond\n".encode("utf-8"))

    def test_crlf_context_lines_are_untouched(self):
        self.write("c.txt", b"keep  \r\nold\r\ntail\n")
        self.call("apply", P("*** Update File: c.txt", "@@", " keep", "-old", "+new"))
        self.assertEqual(self.read("c.txt"), b"keep  \r\nnew\r\ntail\n")


class FeedbackTests(Base):
    def test_conflict_shows_all_expected_lines_and_closest_match(self):
        self.write("f.py", "def train():\n    lr = 0.01\n    epochs = 10\n    return lr\n")
        res = self.call("dry_run", P("*** Update File: f.py", "@@", " def train():", "-    lr = 0.1", "-    epochs = 10", "+    lr = 0.2"))[1]
        message = res["conflicts"][0]["message"]
        self.assertIn("patch line 3", message)
        self.assertIn("  |    lr = 0.1", message)
        self.assertIn("closest match at line 1", message)
        self.assertIn("  |    lr = 0.01", message)

    def test_already_applied_hint(self):
        self.write("f.txt", "x\nNEW\n")
        message = self.call("dry_run", P("*** Update File: f.txt", "@@", " x", "-old", "+NEW"))[1]["conflicts"][0]["message"]
        self.assertIn("already applied", message)

    def test_fuzzy_and_ambiguous_matches_are_reported_and_strict_refuses(self):
        self.write("f.txt", "k\n  v = 1  \nk\n  v = 1\n")
        patch = P("*** Update File: f.txt", "@@", " k", "-v = 1", "+v = 2")
        res = self.call("dry_run", patch)[1]
        self.assertTrue(res["ok"])
        [note] = res["files"][0]["matches"]
        self.assertEqual((note["line"], note["level"], note["also_matches_lines"]), (1, "strip", [3]))
        strict = self.call("dry_run", patch, strict=True)[1]
        self.assertIn("strict mode: ambiguous", strict["conflicts"][0]["message"])


class RetentionTests(Base):
    def test_prune_keeps_prepared_and_recent(self):
        self.backups.mkdir()
        now = time.time()

        def make(name, state, age):
            d = self.backups / name
            d.mkdir()
            (d / "manifest.json").write_text(json.dumps({"state": state, "workdir": "/w", "files": []}))
            os.utime(d, (now - age, now - age))

        make("old-committed", "committed", 30 * 86400)
        make("new-committed", "committed", 60)
        make("old-prepared", "prepared", 30 * 86400)
        make("x.rolled-back-20260101T000000Z", "rolled_back", 10 * 86400)
        removed = opa.prune_backups(str(self.backups), now)
        self.assertEqual(sorted(removed), ["old-committed", "x.rolled-back-20260101T000000Z"])
        self.assertEqual(sorted(p.name for p in self.backups.iterdir()), ["new-committed", "old-prepared"])

    def test_prune_enforces_total_size_oldest_first_but_keeps_prepared(self):
        self.backups.mkdir()
        now = time.time()

        def make(name, state, age, size):
            d = self.backups / name
            d.mkdir()
            (d / "manifest.json").write_text(json.dumps({"state": state, "workdir": "/w", "files": []}))
            (d / "blob").write_bytes(b"x" * size)
            os.utime(d, (now - age, now - age))

        make("c-old", "committed", 300, 600)
        make("p-old", "prepared", 400, 600)
        make("c-new", "committed", 100, 600)
        with mock.patch.object(opa, "MAX_BACKUP_BYTES", 1500):
            removed = opa.prune_backups(str(self.backups), now)
        self.assertEqual(removed, ["c-old"])
        self.assertEqual(sorted(p.name for p in self.backups.iterdir()), ["c-new", "p-old"])

    def test_refuses_when_disk_would_fill_and_touches_nothing(self):
        self.write("a.txt", "a\n")

        class Full(object):
            f_bavail, f_frsize = 10, 4096  # 40 KB free

        with mock.patch.object(opa.os, "statvfs", lambda path: Full()):
            code, res = self.call("apply", P("*** Update File: a.txt", "@@", "-a", "+A"))
        self.assertEqual(code, 1)
        self.assertIn("not enough free disk space", res["error"])
        self.assertEqual(self.read("a.txt"), b"a\n")
        self.assertFalse((self.backups / "p1").exists())

    def test_force_rollback_saves_what_it_overwrites(self):
        self.write("a.txt", "a\n")
        self.call("apply", P("*** Update File: a.txt", "@@", "-a", "+A"))
        self.write("a.txt", "someone else's work\n")
        res = self.call("rollback", force=True)[1]
        self.assertTrue(res["ok"], res)
        [saved] = res["saved"]
        self.assertEqual(Path(saved["saved_as"]).read_bytes(), b"someone else's work\n")
        self.assertEqual(self.read("a.txt"), b"a\n")


def reference_seek(lines, pattern, start, eof):
    """The straightforward Codex seek_sequence, kept as the oracle for LineIndex."""
    if not pattern:
        return start, "exact"
    if len(pattern) > len(lines):
        return None, None
    last = len(lines) - len(pattern)
    lo = max(last, start) if eof else start
    for name, norm in opa.LEVELS:
        want = [norm(p) for p in pattern]
        for i in range(lo, last + 1):
            if all(norm(lines[i + j]) == want[j] for j in range(len(pattern))):
                return i, name
    return None, None


class MatcherTests(unittest.TestCase):
    def test_index_agrees_with_reference_on_random_inputs(self):
        import random
        rng = random.Random(1234)
        alphabet = ["a", "b", " a", "a  ", "\tb", "c\u2014d", "c-d", "", "  ", "\u201cq\u201d", '"q"']
        for _ in range(3000):
            lines = [rng.choice(alphabet) for _ in range(rng.randint(0, 25))]
            pattern = [rng.choice(alphabet) for _ in range(rng.randint(0, 4))]
            start = rng.randint(0, max(0, len(lines)))
            eof = rng.random() < 0.3
            self.assertEqual(opa.LineIndex(lines).seek(pattern, start, eof), reference_seek(lines, pattern, start, eof),
                             (lines, pattern, start, eof))

    def test_also_matches_lists_later_starts_at_the_same_level(self):
        index = opa.LineIndex(["k", "v", "k", " v", "k", "v"])
        found, level = index.seek(["k", "v"], 0, False)
        self.assertEqual((found, level), (0, "exact"))
        self.assertEqual(index.also_matches(["k", "v"], "exact", found), [4])
        self.assertEqual(index.also_matches(["k", "v"], "strip", found), [2, 4])

    def test_generated_diff_reconstructs_the_new_file(self):
        import random
        import re as _re
        rng = random.Random(99)

        def apply_unified(old, diff):
            out, pos = [], 0
            lines = diff.split("\n")[2:]
            i = 0
            while i < len(lines) and lines[i]:
                m = _re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", lines[i])
                self.assertIsNotNone(m, lines[i])
                a_start, a_len = int(m.group(1)), int(m.group(2) or 1)
                b_start, b_len = int(m.group(3)), int(m.group(4) or 1)
                a0 = a_start - 1 if a_len else a_start
                out.extend(old[pos:a0])
                self.assertEqual(len(out) + 1 if b_len else len(out), b_start)
                pos, seen_a, seen_b = a0, 0, 0
                i += 1
                while i < len(lines) and lines[i] and not lines[i].startswith("@@"):
                    tag, text = lines[i][0], lines[i][1:]
                    if tag == " ":
                        self.assertEqual(old[pos], text)
                        out.append(text); pos += 1; seen_a += 1; seen_b += 1
                    elif tag == "-":
                        self.assertEqual(old[pos], text)
                        pos += 1; seen_a += 1
                    elif tag == "+":
                        out.append(text); seen_b += 1
                    i += 1
                self.assertEqual((seen_a, seen_b), (a_len, b_len))
            return out + old[pos:]

        for _ in range(400):
            n = rng.randint(1, 60)
            old = [f"line {k}" for k in range(n)]
            chunks, cursor = [], 0
            while cursor < n and len(chunks) < 4:
                start = rng.randint(cursor, n - 1)
                length = rng.randint(1, min(4, n - start))
                ctx_before = old[max(cursor, start - 1):start]
                removed = old[start:start + length]
                added = [f"new {rng.random():.6f}" for _ in range(rng.randint(0, 3))]
                chunk = {"context": None, "old": ctx_before + removed, "new": ctx_before + added,
                         "ctx": [(0, 0)] if ctx_before else [], "eof": False, "line": 1}
                chunks.append(chunk)
                cursor = start + length
            text = "\n".join(old) + "\n"
            new_text, _notes, (diff, added_n, removed_n) = opa.apply_chunks(text, chunks, False, ("a/f", "b/f"))
            new_lines = new_text.split("\n")[:-1]
            self.assertEqual(apply_unified(old, diff), new_lines, diff)
            self.assertEqual(sum(1 for l in diff.split("\n")[2:] if l.startswith("+")), added_n)
            self.assertEqual(sum(1 for l in diff.split("\n")[2:] if l.startswith("-")), removed_n)

    def test_pathological_inputs_stay_fast(self):
        lines = ["    pass"] * 100_000
        started = time.perf_counter()
        self.assertEqual(opa.LineIndex(lines).seek(["    pass"] * 50 + ["missing"], 0, False), (None, None))
        index = opa.LineIndex([f"value_{i} = {i}" for i in range(100_000)])
        best = opa._closest(index, ["value_50000 = 50000", "value_50001 = 999", "value_50002 = 50002"])
        self.assertEqual(best[0], 50000)
        self.assertLess(time.perf_counter() - started, 3.0)  # was ~4 s for the first alone on a server


if __name__ == "__main__":
    unittest.main()
