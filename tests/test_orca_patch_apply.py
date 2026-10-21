"""Tests for engine/orca_patch_apply.py, the transactional remote patch applier."""

import ast
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

MODULE_PATH = Path(__file__).resolve().parent.parent / "engine" / "orca_patch_apply.py"
SPEC = importlib.util.spec_from_file_location("orca_patch_apply", MODULE_PATH)
opa = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(opa)

# Optional cross-check against a local Codex binary. Set ORCA_CODEX_BIN to the
# native `codex` executable; the comparison cases skip when it is absent.
CODEX_BIN = os.environ.get("ORCA_CODEX_BIN", "")


def P(*body):
    return "\n".join(("*** Begin Patch",) + body + ("*** End Patch",)) + "\n"


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name).resolve()
        self.home = base / "home"
        self.home.mkdir()
        self.work = base / "work"
        self.work.mkdir()
        self.outside = base / "outside"
        self.outside.mkdir()
        self.backups = base / "backups"
        env = mock.patch.dict(os.environ, {"HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(self._tmp.cleanup)

    def write(self, rel, data, mode=None):
        path = self.work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
        if mode is not None:
            os.chmod(path, mode)
        return path

    def read(self, rel):
        return (self.work / rel).read_bytes()

    def snapshot(self):
        tree = {}
        for path in sorted(self.work.rglob("*")):
            rel = str(path.relative_to(self.work))
            if path.is_symlink():
                tree[rel] = ("link", os.readlink(path))
            elif path.is_file():
                tree[rel] = ("file", path.read_bytes(), path.stat().st_mode & 0o7777)
            else:
                tree[rel] = ("dir",)
        return tree

    def call(self, mode, patch=None, **extra):
        request = {"mode": mode}
        if mode != "rollback":
            request["workdir"] = str(self.work)
            request["patch"] = patch
        if mode != "dry_run":
            request.setdefault("patch_id", "p1")
            request.setdefault("backup_root", str(self.backups))
        request.update(extra)
        return opa.main(json.dumps(request))

    def assert_ok(self, outcome):
        code, result = outcome
        self.assertEqual(code, 0, result)
        self.assertTrue(result.get("ok"), result)
        return result


class ParseTests(unittest.TestCase):
    def test_parse_all_sections(self):
        hunks = opa.parse_patch(
            P(
                "*** Add File: a.txt",
                "+one",
                "+two",
                "*** Delete File: b.txt",
                "*** Update File: c.txt",
                "*** Move to: d.txt",
                "@@ def foo():",
                " ctx",
                "-old",
                "+new",
                "@@",
                "-x",
                "+y",
                "*** End of File",
            )
        )
        self.assertEqual([h["type"] for h in hunks], ["add", "delete", "update"])
        self.assertEqual(hunks[0]["contents"], "one\ntwo\n")
        self.assertEqual(hunks[2]["move_to"], "d.txt")
        chunks = hunks[2]["chunks"]
        self.assertEqual(chunks[0]["context"], "def foo():")
        self.assertEqual(chunks[0]["old"], ["ctx", "old"])
        self.assertEqual(chunks[0]["new"], ["ctx", "new"])
        self.assertIsNone(chunks[1]["context"])
        self.assertTrue(chunks[1]["eof"])

    def test_first_hunk_may_omit_marker_but_later_ones_may_not(self):
        hunks = opa.parse_patch(P("*** Update File: a", "-x", "+y"))
        self.assertEqual(hunks[0]["chunks"][0]["old"], ["x"])
        with self.assertRaises(opa.PatchError):
            opa.parse_patch(P("*** Update File: a", "-x", "+y", "*** Update File: a", "garbage"))

    def test_bad_boundaries_and_headers(self):
        for text in ("", "*** Add File: a\n+x\n*** End Patch", "*** Begin Patch\n*** Add File: a\n+x",
                     P("*** Frobnicate File: a"), P(), P("*** Update File: a")):
            with self.assertRaises(opa.PatchError, msg=text):
                opa.parse_patch(text)

    def test_indented_headers_are_trimmed(self):
        hunks = opa.parse_patch("  *** Begin Patch\n   *** Add File: x\n+1\n  *** End Patch  ")
        self.assertEqual(hunks[0]["path"], "x")


class DryRunAndApplyTests(Base):
    def test_add_update_delete_move_happy_path(self):
        self.write("upd.txt", "a\nb\nc\n", 0o755)
        self.write("del.txt", "gone\n")
        self.write("mv.txt", "m1\nm2\n", 0o640)
        patch = P(
            "*** Add File: new/dir/added.txt",
            "+hello",
            "*** Update File: upd.txt",
            "@@",
            " a",
            "-b",
            "+B",
            "*** Delete File: del.txt",
            "*** Update File: mv.txt",
            "*** Move to: moved/mv2.txt",
            "@@",
            "-m2",
            "+M2",
        )
        before = self.snapshot()
        dry = self.assert_ok(self.call("dry_run", patch))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual([f["action"] for f in dry["files"]], ["add", "update", "delete", "move"])
        self.assertEqual(dry["files"][1]["added"], 1)
        self.assertEqual(dry["files"][1]["removed"], 1)
        self.assertEqual(dry["files"][3]["move_to"], "moved/mv2.txt")
        self.assertIn("+B", dry["diff"])
        self.assertFalse(dry["diff_truncated"])

        res = self.assert_ok(self.call("apply", patch))
        self.assertEqual(self.read("new/dir/added.txt"), b"hello\n")
        self.assertEqual(self.read("upd.txt"), b"a\nB\nc\n")
        self.assertEqual((self.work / "upd.txt").stat().st_mode & 0o777, 0o755)
        self.assertFalse((self.work / "del.txt").exists())
        self.assertFalse((self.work / "mv.txt").exists())
        self.assertEqual(self.read("moved/mv2.txt"), b"m1\nM2\n")
        self.assertEqual((self.work / "moved/mv2.txt").stat().st_mode & 0o777, 0o640)
        self.assertEqual(res["files"], dry["files"])

        backup = Path(res["backup_dir"])
        self.assertEqual(backup, self.backups / "p1")
        manifest = json.loads((backup / "manifest.json").read_text())
        self.assertEqual(manifest["patch_id"], "p1")
        self.assertEqual(manifest["workdir"], str(self.work))
        self.assertTrue(manifest["created_at"].endswith("Z"))
        self.assertEqual(manifest["created_dirs"], ["new", "new/dir", "moved"])
        by_path = {e["path"]: e for e in manifest["files"]}
        self.assertIsNone(by_path["new/dir/added.txt"]["backup"])
        self.assertEqual(by_path["upd.txt"]["mode"], 0o755)
        self.assertEqual((backup / by_path["upd.txt"]["backup"]).read_bytes(), b"a\nb\nc\n")
        self.assertEqual(by_path["del.txt"]["sha256_after"], None)
        self.assertEqual(by_path["mv.txt"]["move_to"], "moved/mv2.txt")
        self.assertEqual(by_path["mv.txt"]["sha256_after"], sha(b"m1\nM2\n"))

    def test_multiple_hunks_and_context_seek(self):
        self.write(
            "f.py",
            "def a():\n    return 1\n\ndef b():\n    return 1\n\ndef c():\n    return 1\n",
        )
        patch = P(
            "*** Update File: f.py",
            "@@ def b():",
            "-    return 1",
            "+    return 2",
            "@@ def c():",
            "-    return 1",
            "+    return 3",
        )
        self.assert_ok(self.call("apply", patch))
        self.assertEqual(
            self.read("f.py"),
            b"def a():\n    return 1\n\ndef b():\n    return 2\n\ndef c():\n    return 3\n",
        )

    def test_whitespace_tolerant_rstrip_and_strip(self):
        self.write("r.txt", "alpha   \nbeta\t\ngamma\n")
        self.write("s.txt", "    indented\n  also\nend\n")
        patch = P(
            "*** Update File: r.txt",
            "@@",
            "-alpha",
            "-beta",
            "+ALPHA",
            "*** Update File: s.txt",
            "@@",
            "-indented",
            "-also",
            "+X",
        )
        self.assert_ok(self.call("apply", patch))
        self.assertEqual(self.read("r.txt"), b"ALPHA\ngamma\n")
        self.assertEqual(self.read("s.txt"), b"X\nend\n")

    def test_exact_match_wins_over_earlier_fuzzy_match(self):
        self.write("f.txt", "x  \ny\nx\n")
        self.assert_ok(self.call("apply", P("*** Update File: f.txt", "@@", "-x", "+Z")))
        self.assertEqual(self.read("f.txt"), b"x  \ny\nZ\n")

    def test_end_of_file_anchoring(self):
        self.write("f.txt", "x\nmid\nx\n")
        self.assert_ok(self.call("apply", P("*** Update File: f.txt", "@@", "-x", "+END", "*** End of File")))
        self.assertEqual(self.read("f.txt"), b"x\nmid\nEND\n")
        self.write("g.txt", "x\nmid\ntail\n")
        code, res = self.call("dry_run", P("*** Update File: g.txt", "@@", "-x", "+E", "*** End of File"))
        self.assertEqual(code, 0)
        self.assertFalse(res["ok"])
        self.assertIn("end of file", res["conflicts"][0]["message"])

    def test_pure_addition_appends_at_eof_like_codex(self):
        self.write("f.txt", "a\nb\nc\n")
        self.assert_ok(self.call("apply", P("*** Update File: f.txt", "@@ a", "+added")))
        self.assertEqual(self.read("f.txt"), b"a\nb\nc\nadded\n")

    def test_output_gets_trailing_newline(self):
        self.write("f.txt", "a\nb")
        self.assert_ok(self.call("apply", P("*** Update File: f.txt", "@@", "-a", "+A")))
        self.assertEqual(self.read("f.txt"), b"A\nb\n")

    def test_conflict_is_all_or_nothing(self):
        self.write("a.txt", "one\n")
        self.write("b.txt", "two\n")
        patch = P(
            "*** Update File: a.txt",
            "@@",
            "-one",
            "+ONE",
            "*** Add File: c.txt",
            "+new",
            "*** Update File: b.txt",
            "@@",
            " not-there",
            "-two",
            "+TWO",
        )
        before = self.snapshot()
        code, res = self.call("apply", patch)
        self.assertEqual(code, 0)
        self.assertFalse(res["ok"])
        self.assertEqual(res["conflicts"][0]["path"], "b.txt")
        self.assertIn("  |not-there", res["conflicts"][0]["message"])
        self.assertEqual(res["state"], "conflict")
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(self.backups.exists())

    def test_missing_context_header_conflict(self):
        self.write("a.txt", "one\n")
        code, res = self.call("dry_run", P("*** Update File: a.txt", "@@ nope", "-one", "+1"))
        self.assertFalse(res["ok"])
        self.assertIn("'nope'", res["conflicts"][0]["message"])

    def test_existence_conflicts(self):
        self.write("exists.txt", "x\n")
        self.write("src.txt", "s\n")
        cases = [
            P("*** Add File: exists.txt", "+y"),
            P("*** Update File: missing.txt", "@@", "-x", "+y"),
            P("*** Delete File: missing.txt"),
            P("*** Update File: src.txt", "*** Move to: exists.txt", "@@", "-s", "+t"),
            P("*** Update File: src.txt", "@@", "-s", "+t", "*** Delete File: src.txt"),
        ]
        before = self.snapshot()
        for patch in cases:
            code, res = self.call("apply", patch)
            self.assertEqual(code, 0, res)
            self.assertFalse(res["ok"], patch)
            self.assertTrue(res["conflicts"], patch)
        self.assertEqual(self.snapshot(), before)

    def test_non_utf8_target_conflict(self):
        self.write("bin.dat", b"\xff\xfe\x00abc\n")
        code, res = self.call("dry_run", P("*** Update File: bin.dat", "@@", "-abc", "+x"))
        self.assertFalse(res["ok"])
        self.assertIn("binary or non-UTF-8", res["conflicts"][0]["message"])

    def test_diff_truncation(self):
        self.write("f.txt", "".join(f"line {i}\n" for i in range(200)))
        res = self.assert_ok(self.call("dry_run", P("*** Update File: f.txt", "@@", "-line 5", "+LINE 5"), max_diff_chars=40))
        self.assertTrue(res["diff_truncated"])
        self.assertIn("[diff truncated", res["diff"])


class PathSafetyTests(Base):
    def assert_rejected(self, patch, needle="unsafe path"):
        before = self.snapshot()
        for mode in ("dry_run", "apply"):
            code, res = self.call(mode, patch)
            self.assertEqual(code, 1, res)
            self.assertIn(needle, res["error"])
        self.assertEqual(self.snapshot(), before)

    def test_dotdot_rejected(self):
        self.assert_rejected(P("*** Add File: ../escape.txt", "+x"))
        self.assert_rejected(P("*** Add File: a/../../escape.txt", "+x"))
        self.assertFalse((self.work.parent / "escape.txt").exists())

    def test_absolute_rejected(self):
        self.assert_rejected(P("*** Add File: " + str(self.outside / "x.txt"), "+x"))
        self.assertFalse((self.outside / "x.txt").exists())

    def test_indented_header_with_escape_rejected(self):
        self.assert_rejected("*** Begin Patch\n   *** Add File: ../x\n+pwn\n*** End Patch\n")

    def test_symlink_escape_rejected(self):
        (self.outside / "secret.txt").write_text("secret\n")
        os.symlink(str(self.outside), str(self.work / "link"))
        os.symlink(str(self.outside / "secret.txt"), str(self.work / "file-link"))
        self.assert_rejected(P("*** Add File: link/new.txt", "+x"))
        self.assert_rejected(P("*** Update File: link/secret.txt", "@@", "-secret", "+pwned"))
        self.assert_rejected(P("*** Update File: file-link", "@@", "-secret", "+pwned"))
        self.assertEqual((self.outside / "secret.txt").read_text(), "secret\n")
        self.assertFalse((self.outside / "new.txt").exists())

    def test_move_destination_escape_rejected(self):
        self.write("a.txt", "a\n")
        self.assert_rejected(P("*** Update File: a.txt", "*** Move to: ../a.txt", "@@", "-a", "+b"))

    def test_internal_symlink_target_is_a_conflict(self):
        self.write("real.txt", "r\n")
        os.symlink("real.txt", str(self.work / "alias.txt"))
        code, res = self.call("dry_run", P("*** Update File: alias.txt", "@@", "-r", "+R"))
        self.assertEqual(code, 0)
        self.assertIn("symbolic link", res["conflicts"][0]["message"])


class ExpectedShaTests(Base):
    def test_mismatch_and_match(self):
        self.write("a.txt", "a\n")
        patch = P("*** Update File: a.txt", "@@", "-a", "+b")
        code, res = self.call("apply", patch, expected_sha256={"a.txt": "0" * 64})
        self.assertFalse(res["ok"])
        self.assertIn("changed since it was last read", res["conflicts"][0]["message"])
        self.assertEqual(self.read("a.txt"), b"a\n")
        self.assert_ok(self.call("apply", patch, expected_sha256={"a.txt": sha(b"a\n").upper()}))
        self.assertEqual(self.read("a.txt"), b"b\n")

    def test_null_means_must_not_exist(self):
        patch = P("*** Add File: n.txt", "+x")
        self.assert_ok(self.call("dry_run", patch, expected_sha256={"n.txt": None}))
        self.write("m.txt", "here\n")
        code, res = self.call(
            "dry_run",
            P("*** Update File: m.txt", "*** Move to: dest.txt", "@@", "-here", "+there"),
            expected_sha256={"dest.txt": None, "m.txt": sha(b"here\n")},
        )
        self.assertTrue(res["ok"], res)
        self.write("dest.txt", "x\n")
        code, res = self.call(
            "dry_run",
            P("*** Update File: m.txt", "@@", "-here", "+there"),
            expected_sha256={"m.txt": None},
        )
        self.assertFalse(res["ok"])
        self.assertIn("must not exist", res["conflicts"][0]["message"])

    def test_untouched_or_malformed_keys_are_request_errors(self):
        self.write("a.txt", "a\n")
        patch = P("*** Update File: a.txt", "@@", "-a", "+b")
        self.assertEqual(self.call("dry_run", patch, expected_sha256={"other.txt": None})[0], 1)
        self.assertEqual(self.call("dry_run", patch, expected_sha256={"a.txt": "abc"})[0], 1)


class RequestValidationTests(Base):
    def test_bad_requests(self):
        self.assertEqual(opa.main("not json")[0], 1)
        self.assertEqual(opa.main("[]")[0], 1)
        self.assertEqual(opa.main(json.dumps({"mode": "nope"}))[0], 1)
        self.assertEqual(opa.main(json.dumps({"mode": "dry_run", "workdir": "rel", "patch": P()}))[0], 1)
        patch = P("*** Add File: a", "+x")
        for bad_id in ("..", ".", "a/b", "", "x" * 101, None):
            code, res = self.call("apply", patch, patch_id=bad_id)
            self.assertEqual(code, 1, bad_id)
        self.assertEqual(self.call("apply", patch, backup_root="relative")[0], 1)
        self.assertEqual(self.call("dry_run", patch, bogus=1)[0], 1)
        self.assertEqual(self.call("dry_run", patch, max_diff_chars=True)[0], 1)

    def test_patch_id_reuse(self):
        patch = P("*** Add File: a", "+x")
        self.assert_ok(self.call("apply", patch))
        # Same id, same patch: idempotent (e.g. a retry after a lost reply).
        again = self.assert_ok(self.call("apply", patch))
        self.assertEqual(again["state"], "already_applied")
        self.assertEqual(self.read("a"), b"x\n")
        # Same id, different patch: refused, nothing touched.
        code, res = self.call("apply", P("*** Add File: b", "+x"))
        self.assertEqual(code, 1)
        self.assertIn("different patch", res["error"])
        self.assertFalse((self.work / "b").exists())
        # Same id, same patch, but the file changed since: refused.
        self.write("a", "edited\n")
        code, res = self.call("apply", patch)
        self.assertEqual(code, 1)
        self.assertIn("changed since", res["error"])


class RollbackTests(Base):
    def setup_tree(self):
        self.write("upd.txt", b"a\r\nb\nc  \n", 0o751)
        self.write("del.txt", b"bye\n", 0o600)
        self.write("mv.txt", b"m\n", 0o644)
        self.patch = P(
            "*** Update File: upd.txt",
            "@@",
            "-b",
            "+B",
            "*** Delete File: del.txt",
            "*** Add File: deep/new/add.txt",
            "+added",
            "*** Update File: mv.txt",
            "*** Move to: sub/mv.txt",
            "@@",
            "-m",
            "+M",
        )

    def test_rollback_restores_exact_bytes_and_mode(self):
        self.setup_tree()
        before = self.snapshot()
        self.assert_ok(self.call("apply", self.patch))
        self.assertNotEqual(self.snapshot(), before)
        res = self.assert_ok(self.call("rollback"))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(set(res["restored"]), {"upd.txt", "del.txt", "deep/new/add.txt", "mv.txt"})
        self.assertFalse((self.backups / "p1").exists())
        self.assertTrue(any(p.name.startswith("p1.rolled-back-") for p in self.backups.iterdir()))
        again = self.assert_ok(self.call("rollback"))
        self.assertEqual(again["state"], "already_rolled_back")

    def test_rollback_refuses_on_change_then_force(self):
        self.setup_tree()
        before = self.snapshot()
        self.assert_ok(self.call("apply", self.patch))
        self.write("upd.txt", "edited by someone else\n")
        self.write("del.txt", "recreated\n")
        changed = self.snapshot()
        code, res = self.call("rollback")
        self.assertEqual(code, 0)
        self.assertFalse(res["ok"])
        self.assertEqual({c["path"] for c in res["conflicts"]}, {"upd.txt", "del.txt"})
        self.assertEqual(self.snapshot(), changed)
        self.assertTrue((self.backups / "p1" / "manifest.json").exists())
        res = self.assert_ok(self.call("rollback", force=True))
        self.assertTrue(res["forced"])
        self.assertEqual(self.snapshot(), before)

    def test_rollback_keeps_preexisting_and_nonempty_dirs(self):
        self.write("keep/x.txt", "x\n")
        self.assert_ok(self.call("apply", P("*** Add File: keep/y.txt", "+y", "*** Add File: made/z.txt", "+z")))
        self.write("made/other.txt", "user file\n")
        self.assert_ok(self.call("rollback"))
        self.assertTrue((self.work / "keep/x.txt").exists())
        self.assertFalse((self.work / "keep/y.txt").exists())
        self.assertTrue((self.work / "made/other.txt").exists())

    def test_rollback_missing_backup(self):
        code, res = self.call("rollback", patch_id="never")
        self.assertEqual(code, 1)
        self.assertIn("no backup for patch_id", res["error"])


class LockAndFailureTests(Base):
    def test_lock_contention(self):
        self.write("a.txt", "a\n")
        lock_dir = self.home / ".local/state/remote-fleet/locks"
        lock_dir.mkdir(parents=True)
        key = hashlib.sha256(str(self.work).encode("utf-8")).hexdigest()
        with open(lock_dir / (key + ".lock"), "a+b") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            with mock.patch.object(opa, "LOCK_TIMEOUT_SECONDS", 0.3):
                code, res = self.call("apply", P("*** Update File: a.txt", "@@", "-a", "+b"))
        self.assertEqual(code, 1)
        self.assertIn("workdir is locked by another patch", res["error"])
        self.assertEqual(self.read("a.txt"), b"a\n")
        self.assertFalse(self.backups.exists())
        # Lock released: the same request now succeeds and the lock dir is private.
        self.assert_ok(self.call("apply", P("*** Update File: a.txt", "@@", "-a", "+b")))
        self.assertEqual(opa.lock_path_for(str(self.work))[1], str(lock_dir / (key + ".lock")))

    def test_lock_dir_created_private(self):
        self.write("a.txt", "a\n")
        self.assert_ok(self.call("apply", P("*** Update File: a.txt", "@@", "-a", "+b")))
        mode = (self.home / ".local/state/remote-fleet/locks").stat().st_mode & 0o777
        self.assertEqual(mode, 0o700)

    def test_mid_apply_failure_restores_first_file(self):
        self.write("a.txt", "a\n", 0o640)
        self.write("b.txt", "b\n")
        before = self.snapshot()
        real_replace = os.replace

        def flaky_replace(src, dst):
            if str(dst).endswith("b.txt"):
                raise OSError(28, "No space left on device")
            return real_replace(src, dst)

        patch = P(
            "*** Update File: a.txt", "@@", "-a", "+A",
            "*** Add File: newdir/n.txt", "+n",
            "*** Update File: b.txt", "@@", "-b", "+B",
        )
        with mock.patch.object(opa.os, "replace", flaky_replace):
            code, res = self.call("apply", patch)
        # Mutation had started, so the answer is a result (exit 0), not a bare error.
        self.assertEqual(code, 0)
        self.assertFalse(res["ok"])
        self.assertEqual(res["state"], "restored")
        self.assertIn("No space left", res["error"])
        self.assertEqual(self.snapshot(), before)
        # Restored apply frees the patch_id; a retry works.
        self.assert_ok(self.call("apply", patch))
        self.assertEqual(self.read("b.txt"), b"B\n")


class ShippingTests(Base):
    def run_source(self, request):
        source = MODULE_PATH.read_text()
        proc = subprocess.run(
            [sys.executable, "-c", source],
            input=json.dumps(request).encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(os.environ, HOME=str(self.home)),
            cwd=str(self.outside),
            timeout=60,
        )
        return proc.returncode, json.loads(proc.stdout.decode("utf-8")), proc.stderr

    def test_runs_as_python_c(self):
        self.write("a.txt", "a\n")
        patch = P("*** Update File: a.txt", "@@", "-a", "+b")
        code, res, err = self.run_source({"mode": "dry_run", "workdir": str(self.work), "patch": patch})
        self.assertEqual((code, res["ok"]), (0, True), err)
        self.assertEqual(self.read("a.txt"), b"a\n")
        req = {"mode": "apply", "workdir": str(self.work), "patch": patch, "patch_id": "x",
               "backup_root": str(self.backups)}
        code, res, err = self.run_source(req)
        self.assertEqual((code, res["ok"]), (0, True), err)
        self.assertEqual(self.read("a.txt"), b"b\n")
        code, res, err = self.run_source({"mode": "rollback", "patch_id": "x", "backup_root": str(self.backups)})
        self.assertEqual((code, res["ok"]), (0, True), err)
        self.assertEqual(self.read("a.txt"), b"a\n")
        code, res, err = self.run_source({"mode": "bogus"})
        self.assertEqual(code, 1)
        self.assertIn("error", res)

    def test_backup_root_tilde_expands(self):
        self.write("a.txt", "a\n")
        res = self.assert_ok(
            self.call("apply", P("*** Update File: a.txt", "@@", "-a", "+b"), backup_root="~/.local/state/orca/backups")
        )
        self.assertEqual(res["backup_dir"], str(self.home / ".local/state/orca/backups/p1"))

    def test_source_is_python36_compatible(self):
        source = MODULE_PATH.read_text()
        ast.parse(source, feature_version=(3, 6))
        self.assertNotIn("import dataclasses", source)
        self.assertNotIn("@dataclass", source)
        self.assertNotIn("capture_output", source)
        self.assertNotIn(":=", source)
        self.assertNotIn("removeprefix", source)
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.assertIsNone(node.returns, node.name)
                args = node.args
                self.assertFalse(getattr(args, "posonlyargs", []), node.name)
                for arg in args.args + args.kwonlyargs:
                    self.assertIsNone(arg.annotation, node.name)
            self.assertNotIsInstance(node, ast.AnnAssign)
        for name in ("python3.6", "python3.7"):
            old = shutil.which(name)
            if old:
                subprocess.run([old, "-c", "import ast,sys; compile(open(sys.argv[1]).read(), 'x', 'exec')",
                                str(MODULE_PATH)], check=True)


# Patches exercised identically by Codex and this module: (initial tree, patch).
CODEX_CASES = [
    (
        {"multi.py": "def a():\n    return 1\n\ndef b():\n    return 1\n\ndef c():\n    x = 1\n    return x\n"},
        P(
            "*** Update File: multi.py",
            "@@ def b():",
            "-    return 1",
            "+    return 2",
            "@@ def c():",
            "     x = 1",
            "-    return x",
            "+    return x + 1",
        ),
    ),
    (
        {"ws.txt": "alpha   \n    beta\t\ngamma\r\ndelta\n"},
        P("*** Update File: ws.txt", "@@", "-alpha", "-beta", "+ALPHA", " gamma", "-delta", "+DELTA"),
    ),
    (
        {"uni.txt": "x — “quoted”\nkeep\n"},
        P("*** Update File: uni.txt", "@@", '-x - "quoted"', "+plain", " keep"),
    ),
    (
        {"eof.txt": "x\nmid\nx\n"},
        P("*** Update File: eof.txt", "@@", " mid", "-x", "+last", "*** End of File"),
    ),
    (
        {"src/mv.txt": "one\ntwo\n", "other.txt": "o\n"},
        P(
            "*** Update File: src/mv.txt",
            "*** Move to: dst/deeper/mv.txt",
            "@@",
            " one",
            "-two",
            "+TWO",
            "*** Delete File: other.txt",
            "*** Add File: added/new.txt",
            "+n1",
            "+n2",
        ),
    ),
    (
        {"append.txt": "a\nb\n", "nonl.txt": "a\nb", "blank.txt": "a\n\n\n", "empty.txt": ""},
        P(
            "*** Update File: append.txt",
            "@@ a",
            "+tail",
            "*** Update File: nonl.txt",
            "-a",
            "+A",
            "*** Update File: blank.txt",
            "@@",
            "-a",
            "+A",
            "*** Update File: empty.txt",
            "+first",
            "*** Add File: zero.txt",
        ),
    ),
    (
        {"seq.txt": "k\nv1\nk\nv2\nk\nv3\n"},
        P("*** Update File: seq.txt", "@@", " k", "-v1", "+V1", "@@", " k", "-v3", "+V3"),
    ),
]


# Cases whose result changed when Codex started preserving line endings
# (CRLF lines; a blank line at EOF is no longer dropped).
LINE_ENDING_SENSITIVE = {1, 5}


@unittest.skipUnless(os.path.isfile(CODEX_BIN) and os.access(CODEX_BIN, os.X_OK), "native codex binary absent")
class CodexParityTests(Base):
    def materialize(self, root, tree):
        for rel, text in tree.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(text.encode("utf-8"))

    def read_tree(self, root):
        out = {}
        for path in sorted(root.rglob("*")):
            if path.is_file():
                out[str(path.relative_to(root))] = path.read_bytes()
        return out

    def codex_preserves_line_endings(self):
        """Codex >= ~0.150 keeps CRLF; older builds (e.g. 0.141) rewrite touched lines as LF."""
        probe = Path(self._tmp.name).resolve() / "probe"
        probe.mkdir()
        (probe / "f.txt").write_bytes(b"a\r\nb\r\n")
        subprocess.run(["apply_patch"], executable=CODEX_BIN, input=P("*** Update File: f.txt", "@@", " a", "-b", "+B").encode(),
                       cwd=str(probe), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        return (probe / "f.txt").read_bytes() == b"a\r\nB\r\n"

    def test_identical_trees(self):
        base = Path(self._tmp.name).resolve()
        modern = self.codex_preserves_line_endings()
        for i, (tree, patch) in enumerate(CODEX_CASES):
            if i in LINE_ENDING_SENSITIVE and not modern:
                continue  # the vendored Codex scenarios (test_codex_scenarios.py) cover current behaviour
            with self.subTest(case=i):
                codex_dir = base / f"codex{i}"
                ours_dir = base / f"ours{i}"
                codex_dir.mkdir()
                ours_dir.mkdir()
                self.materialize(codex_dir, tree)
                self.materialize(ours_dir, tree)
                proc = subprocess.run(
                    ["apply_patch"], executable=CODEX_BIN, input=patch.encode("utf-8"),
                    cwd=str(codex_dir), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60,
                )
                self.assertEqual(proc.returncode, 0, proc.stderr.decode() + proc.stdout.decode())
                code, res = opa.main(json.dumps({
                    "mode": "apply", "workdir": str(ours_dir), "patch": patch,
                    "patch_id": f"cmp{i}", "backup_root": str(self.backups),
                }))
                self.assertEqual(code, 0, res)
                self.assertTrue(res["ok"], res)
                self.assertEqual(self.read_tree(ours_dir), self.read_tree(codex_dir))

    def test_both_reject_unfindable_hunk(self):
        base = Path(self._tmp.name).resolve()
        patch = P("*** Update File: f.txt", "@@", "-b", "+B", "*** End of File")
        for name in ("codex_rej", "ours_rej"):
            (base / name).mkdir()
            (base / name / "f.txt").write_text("a\nb\nc\n")
        proc = subprocess.run(["apply_patch"], executable=CODEX_BIN, input=patch.encode(), cwd=str(base / "codex_rej"),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        self.assertNotEqual(proc.returncode, 0)
        code, res = opa.main(json.dumps({"mode": "dry_run", "workdir": str(base / "ours_rej"), "patch": patch}))
        self.assertEqual(code, 0)
        self.assertFalse(res["ok"])


if __name__ == "__main__":
    unittest.main()
