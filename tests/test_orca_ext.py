"""Tests for orca's engine extensions: log follower, grep/ls, and tool handlers."""

from __future__ import annotations

import gzip
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ENGINE_DIR = Path(__file__).resolve().parent.parent / "engine"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, ENGINE_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


logger = _load("orca_logger_t", "orca_logger.py")
fs = _load("orca_fs_t", "orca_fs.py")
engine = _load("orca_engine_t", "remote_fleet_mcp.py")
orca_ext = sys.modules["orca_ext"]


class LoggerTests(unittest.TestCase):
    def test_normalize(self) -> None:
        self.assertEqual(logger.normalize(b"\x1b[1;32mok\x1b[0m"), "ok")
        self.assertEqual(logger.normalize(b"a 1%\rb 50%\rc 100%"), "c 100%")
        self.assertEqual(logger.normalize(b"done\r"), "done")
        long = logger.normalize(b"x" * 10_000)
        self.assertTrue(long.endswith("[10000 bytes]"))
        self.assertLess(len(long), 4200)

    def test_levels(self) -> None:
        self.assertEqual(logger.level_of("ValueError: bad"), "error")
        self.assertEqual(logger.level_of("CUDA out of memory"), "error")
        self.assertEqual(logger.level_of("loss is nan"), "error")
        self.assertEqual(logger.level_of("UserWarning: x"), "warn")
        self.assertEqual(logger.level_of("terror of the seas"), "info")
        self.assertEqual(logger.level_of("epoch 3 loss 0.12"), "info")

    def _follower(self, tmp: Path, **kw):
        log = tmp / "job.log"
        orca_dir = tmp / "job.log.orca"
        orca_dir.mkdir()
        log.write_bytes(b"")
        args = dict(offset=0, tail_lines=5, rotate_bytes=0, keep_segments=2)
        args.update(kw)
        return log, orca_dir, logger.Follower(str(log), str(orca_dir), **args)

    def test_ring_is_bounded_and_latest_line_visible_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log, orca_dir, f = self._follower(Path(tmp))
            log.write_bytes(b"".join(b"line %d\n" % i for i in range(20)))
            f.read_new()
            f.flush(force=True)
            ring = (orca_dir / "tail.ring").read_text().splitlines()
            self.assertEqual(ring, [f"line {i}" for i in range(15, 20)])

    def test_rotation_compresses_and_keeps_n_segments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log, orca_dir, f = self._follower(Path(tmp), rotate_bytes=200, keep_segments=2)
            for batch in range(4):
                with log.open("ab") as handle:
                    handle.write(b"".join(b"batch %d row %d\n" % (batch, i) for i in range(10)))
                f.read_new()
                f.flush()
            segments = sorted(p.name for p in orca_dir.glob("events-*.ndjson.gz"))
            self.assertEqual(len(segments), 2)
            with gzip.open(orca_dir / segments[-1], "rt") as handle:
                first = json.loads(handle.readline())
            self.assertIn("row", first["msg"])

    def test_skips_ahead_when_far_behind(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(logger, "MAX_BACKLOG_BYTES", 1000), mock.patch.object(logger, "SKIP_KEEP_BYTES", 100):
            log, orca_dir, f = self._follower(Path(tmp))
            log.write_bytes(b"y" * 5000 + b"\nlast\n")
            f.read_new()
            f.flush(force=True)
            self.assertGreater(f.skipped_bytes, 0)
            self.assertIn("last", (orca_dir / "tail.ring").read_text())
            f.finalize()
            meta = json.loads((orca_dir / "meta.json").read_text())
            self.assertEqual(meta["logger"]["skipped_bytes"], f.skipped_bytes)


class FsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("import os\nx = 1\nraise ValueError('a')\ny = 2\n")
        (self.root / "src" / "b.txt").write_text("nothing\n")
        (self.root / ".hidden").write_text("ValueError hidden\n")
        (self.root / "blob.bin").write_bytes(b"\0ValueError")
        with gzip.open(self.root / "events-0001.ndjson.gz", "wt") as handle:
            handle.write('{"msg": "ValueError in gz"}\n')
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "x.js").write_text("ValueError\n")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_fs(self, request: dict) -> tuple[int, dict]:
        return fs.main(json.dumps(request))

    def test_grep_skips_binary_hidden_and_vendor_dirs_but_reads_gz(self) -> None:
        code, out = self.run_fs({"op": "grep", "path": str(self.root), "pattern": "ValueError"})
        self.assertEqual(code, 0)
        paths = sorted(Path(m["path"]).name for m in out["matches"])
        self.assertEqual(paths, ["a.py", "events-0001.ndjson.gz"])

    def test_grep_context_glob_and_limits(self) -> None:
        code, out = self.run_fs({"op": "grep", "path": str(self.root), "pattern": "valueerror", "ignore_case": True, "glob": "*.py", "context": 1})
        self.assertEqual(code, 0)
        [match] = out["matches"]
        self.assertEqual((match["line"], match["before"], match["after"]), (3, ["x = 1"], ["y = 2"]))
        code, out = self.run_fs({"op": "grep", "path": str(self.root / "src" / "a.py"), "pattern": "=", "max_matches": 1})
        self.assertEqual((len(out["matches"]), out["truncated"]), (1, "max_matches"))

    def test_grep_rejects_bad_regex(self) -> None:
        code, out = self.run_fs({"op": "grep", "path": str(self.root), "pattern": "("})
        self.assertEqual(code, 1)
        self.assertIn("invalid regex", out["error"])

    def test_ls_depth_and_truncation(self) -> None:
        code, out = self.run_fs({"op": "ls", "path": str(self.root), "depth": 1})
        names = [e["path"] for e in out["entries"]]
        self.assertIn("src", names)
        self.assertNotIn("src/a.py", names)
        self.assertNotIn(".hidden", names)
        code, out = self.run_fs({"op": "ls", "path": str(self.root), "depth": 2, "max_entries": 3})
        self.assertTrue(out["truncated"])
        self.assertEqual(len(out["entries"]), 3)

    def test_ships_as_python_c(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-c", (ENGINE_DIR / "orca_fs.py").read_text(), ],
            input=json.dumps({"op": "ls", "path": str(self.root)}), text=True, capture_output=True, check=True,
        )
        self.assertIn("src", completed.stdout)


class HandlerTests(unittest.TestCase):
    def call(self, tool: str, arguments: dict, stdout: dict, exit_code: int = 0):
        result = {"exit_code": exit_code, "stdout": json.dumps(stdout), "stderr": "", "timed_out": False, "truncated": False}
        with mock.patch.dict(os.environ, {"REMOTE_FLEET_HOSTS": "13"}), mock.patch.object(engine, "run_ssh", return_value=result) as run_ssh:
            value = engine.TOOL_HANDLERS[tool](arguments)
        return value, run_ssh

    def test_mcp_surface_is_unchanged_but_cli_json_has_orca_tools(self) -> None:
        self.assertNotIn("orca_grep", {t["name"] for t in engine.TOOLS})
        self.assertTrue({"orca_grep", "orca_ls", "orca_patch", "orca_rollback"} <= set(engine.TOOL_HANDLERS))

    def test_grep_sends_request_on_stdin(self) -> None:
        value, run_ssh = self.call("orca_grep", {"host": "13", "path": "/data", "pattern": "x", "context_lines": 2}, {"matches": []})
        self.assertEqual(value["host"], "13")
        request = json.loads(run_ssh.call_args.kwargs["stdin"])
        self.assertEqual((request["op"], request["context"]), ("grep", 2))

    def test_patch_runs_guard_locally_and_assigns_patch_id(self) -> None:
        patch = "*** Begin Patch\n*** Add File: a.txt\n+a\n*** End Patch"
        _value, run_ssh = self.call("orca_patch", {"host": "13", "workdir": "/w", "patch": patch}, {"ok": True})
        request = json.loads(run_ssh.call_args.kwargs["stdin"])
        self.assertEqual(request["mode"], "apply")
        self.assertRegex(request["patch_id"], r"^orca-\d{8}T\d{6}Z-[0-9a-f]{8}$")
        _value, run_ssh = self.call("orca_patch", {"host": "13", "workdir": "/w", "patch": patch, "dry_run": True}, {"ok": True})
        self.assertNotIn("patch_id", json.loads(run_ssh.call_args.kwargs["stdin"]))
        with self.assertRaises(engine.ToolError):
            self.call("orca_patch", {"host": "13", "workdir": "/w", "patch": patch.replace("a.txt", " ../a.txt")}, {})

    def test_remote_error_payload_becomes_tool_error(self) -> None:
        with self.assertRaisesRegex(engine.ToolError, "nope"):
            self.call("orca_rollback", {"host": "13", "patch_id": "orca-x"}, {"error": "nope"}, exit_code=1)
        with self.assertRaises(engine.ToolError):
            self.call("orca_rollback", {"host": "13", "patch_id": "../x"}, {})


class PatchOutcomeTests(unittest.TestCase):
    PATCH = "*** Begin Patch\n*** Add File: a.txt\n+a\n*** End Patch"

    def run_with(self, responses, arguments=None):
        """responses: successive run_ssh results (apply first, then status)."""
        calls = []

        def fake(host, command, stdin=None, **kw):
            calls.append(json.loads(stdin))
            return responses[len(calls) - 1]

        args = {"host": "13", "workdir": "/w", "patch": self.PATCH}
        args.update(arguments or {})
        with mock.patch.dict(os.environ, {"REMOTE_FLEET_HOSTS": "13"}), mock.patch.object(engine, "run_ssh", fake):
            return engine.TOOL_HANDLERS["orca_patch"](args), calls

    @staticmethod
    def reply(payload=None, exit_code=0, timed_out=False, stdout=None):
        return {"exit_code": exit_code, "stdout": stdout if stdout is not None else json.dumps(payload),
                "stderr": "", "timed_out": timed_out, "truncated": False}

    def test_partial_result_is_returned_not_raised(self):
        value, _ = self.run_with([self.reply({"ok": False, "state": "partial", "patch_id": "x", "error": "boom"})])
        self.assertEqual((value["state"], value["patch_id"]), ("partial", "x"))

    def test_timeout_checks_status_and_reports_truth(self):
        value, calls = self.run_with([self.reply(timed_out=True), self.reply({"ok": True, "mode": "status", "state": "committed"})])
        self.assertEqual((value["ok"], value["state"]), (True, "applied"))
        self.assertEqual(calls[1]["mode"], "status")
        self.assertEqual(calls[1]["patch_id"], calls[0]["patch_id"])
        self.assertIn("timed out", value["uncertain"])

    def test_lost_reply_with_nothing_applied(self):
        value, _ = self.run_with([self.reply(stdout="", exit_code=255), self.reply({"ok": True, "state": "not_found"})])
        self.assertEqual(value["state"], "not_applied")

    def test_interrupted_apply_is_reported_partial(self):
        value, _ = self.run_with([self.reply(timed_out=True), self.reply({"ok": True, "state": "prepared"})])
        self.assertEqual(value["state"], "partial")
        self.assertIn("orca_rollback", value["error"])

    def test_model_supplied_patch_id_is_used_and_validated(self):
        _, calls = self.run_with([self.reply({"ok": True, "state": "applied"})], {"patch_id": "retry-1"})
        self.assertEqual(calls[0]["patch_id"], "retry-1")
        with self.assertRaises(engine.ToolError):
            self.run_with([], {"patch_id": "../x"})


class RemoteCacheTests(unittest.TestCase):
    """The launcher/installer pair really runs here, with a temporary HOME."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        (self.home / "dir").mkdir()
        (self.home / "dir" / "f.txt").write_text("needle\n")
        self.commands = []
        self.addCleanup(self._tmp.cleanup)

    def fake_ssh(self, host, command, stdin=None, **kw):
        self.commands.append(command)
        done = subprocess.run(["/bin/sh", "-c", command], input=stdin, capture_output=True,
                              env={**os.environ, "HOME": str(self.home)}, timeout=60)
        return {"exit_code": done.returncode, "stdout": done.stdout.decode(), "stderr": done.stderr.decode(),
                "timed_out": False, "truncated": False}

    def grep(self):
        self.commands.clear()
        with mock.patch.dict(os.environ, {"REMOTE_FLEET_HOSTS": "13"}), mock.patch.object(engine, "run_ssh", self.fake_ssh):
            value = engine.TOOL_HANDLERS["orca_grep"]({"host": "13", "path": str(self.home / "dir"), "pattern": "needle"})
        self.assertEqual(len(value["matches"]), 1)
        return len(self.commands)

    def cache_dir(self):
        return self.home / ".local/state/orca/bin"

    def test_miss_installs_then_hits(self):
        self.assertEqual(self.grep(), 2)  # launcher miss, installer
        [cached] = list(self.cache_dir().glob("orca_fs_*.py"))
        self.assertEqual(cached.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.cache_dir().stat().st_mode & 0o777, 0o700)
        self.assertEqual(self.grep(), 1)  # cached
        self.assertLess(len(self.commands[0]), 1000)

    def test_tampered_cache_is_reinstalled(self):
        self.grep()
        [cached] = list(self.cache_dir().glob("orca_fs_*.py"))
        cached.write_text("import sys\ndef _entrypoint():\n    print('{\"evil\": 1}'); sys.exit(0)\n")
        self.assertEqual(self.grep(), 2)
        self.assertEqual(self.grep(), 1)

    def test_new_version_replaces_old(self):
        self.grep()
        real = orca_ext._source
        with mock.patch.object(orca_ext, "_source", lambda name: real(name) + "\n# v2\n"):
            self.assertEqual(self.grep(), 2)
        self.assertEqual(len(list(self.cache_dir().glob("orca_fs_*.py"))), 1)

    def test_unwritable_home_still_runs_uncached(self):
        state = self.home / ".local/state"
        state.mkdir(parents=True)
        os.chmod(state, 0o500)
        self.addCleanup(os.chmod, state, 0o700)
        if os.access(state, os.W_OK):
            self.skipTest("running as root")
        self.assertEqual(self.grep(), 2)
        self.assertEqual(self.grep(), 2)


if __name__ == "__main__":
    unittest.main()
