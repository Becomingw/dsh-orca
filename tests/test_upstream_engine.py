#!/usr/bin/env python3

from __future__ import annotations

import importlib.util
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock


sys.dont_write_bytecode = True

MODULE_PATH = Path(__file__).resolve().parent.parent / "engine" / "remote_fleet_mcp.py"
SPEC = importlib.util.spec_from_file_location("remote_fleet_mcp", MODULE_PATH)
assert SPEC and SPEC.loader
remote_fleet = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(remote_fleet)


class RemoteFleetTests(unittest.TestCase):
    def test_tools_list_contains_only_core_tools(self) -> None:
        self.assertEqual(
            {tool["name"] for tool in remote_fleet.TOOLS},
            {
                "remote_read_file",
                "remote_run",
                "remote_apply_patch",
                "remote_launch_tmux",
                "remote_stop_job",
                "remote_status",
            },
        )

    def test_host_allowlist_from_environment(self) -> None:
        with mock.patch.dict("os.environ", {"REMOTE_FLEET_HOSTS": "13,host-24"}, clear=False):
            self.assertEqual(remote_fleet.validate_host("13"), "13")
            with self.assertRaises(remote_fleet.ToolError):
                remote_fleet.validate_host("other")

    def test_rejects_relative_paths(self) -> None:
        with self.assertRaises(remote_fleet.ToolError):
            remote_fleet.validate_absolute_path("relative/path", "path")

    def test_validates_tmux_session(self) -> None:
        self.assertEqual(remote_fleet.validate_session("exp_001.train"), "exp_001.train")
        with self.assertRaises(remote_fleet.ToolError):
            remote_fleet.validate_session("bad session")

    def test_validates_codex_patch_and_paths(self) -> None:
        patch = (
            "*** Begin Patch\n"
            "*** Update File: src/example.py\n"
            "@@\n"
            "-old\n"
            "+new\n"
            "*** End Patch\n"
        )
        patch_bytes, paths = remote_fleet.validate_codex_patch(patch)
        self.assertEqual(patch_bytes, patch.encode())
        self.assertEqual(paths, ["src/example.py"])

    def test_rejects_unified_git_diff(self) -> None:
        with self.assertRaisesRegex(remote_fleet.ToolError, "must start"):
            remote_fleet.validate_codex_patch("--- a/file\n+++ b/file\n")

    def test_rejects_patch_path_escape(self) -> None:
        with self.assertRaisesRegex(remote_fleet.ToolError, "stay inside"):
            remote_fleet.validate_codex_patch(
                "*** Begin Patch\n"
                "*** Update File: ../outside.txt\n"
                "@@\n"
                "-old\n"
                "+new\n"
                "*** End Patch\n"
            )

    def test_validates_expected_patch_hashes(self) -> None:
        expected = remote_fleet.validate_expected_sha256(
            {"src/example.py": "A" * 64, "new.txt": None},
            ["src/example.py", "new.txt"],
        )
        self.assertEqual(expected["src/example.py"], "a" * 64)
        self.assertIsNone(expected["new.txt"])
        with self.assertRaisesRegex(remote_fleet.ToolError, "not touched"):
            remote_fleet.validate_expected_sha256({"other.txt": None}, ["new.txt"])

    def test_native_apply_patch_result_metadata(self) -> None:
        patch = (
            "*** Begin Patch\n"
            "*** Update File: sample.txt\n"
            "@@\n"
            "-old\n"
            "+new\n"
            "*** End Patch\n"
        )
        metadata = {
            "backend": "codex-native",
            "codex_version": "codex-cli 0.149.1",
            "native_codex_path": "/opt/codex/codex",
            "resolution": "native-discovery",
        }
        ssh_result = {
            "exit_code": 0,
            "stdout": "Success. Updated the following files:\nM sample.txt\n",
            "stderr": remote_fleet.CODEX_NATIVE_METADATA_PREFIX + json.dumps(metadata) + "\n",
            "timed_out": False,
            "truncated": False,
        }
        with (
            mock.patch.object(remote_fleet, "validate_host", return_value="13"),
            mock.patch.object(remote_fleet, "run_ssh", return_value=ssh_result) as run_ssh,
        ):
            result = remote_fleet.remote_apply_patch(
                {"host": "13", "workdir": "/tmp/project", "patch": patch}
            )
        self.assertTrue(result["applied"])
        self.assertEqual(result["backend"], "codex-native")
        self.assertEqual(result["codex_version"], "codex-cli 0.149.1")
        self.assertEqual(result["native_codex_path"], "/opt/codex/codex")
        self.assertEqual(result["touched_paths"], ["sample.txt"])
        self.assertEqual(result["stderr"], "")
        self.assertEqual(run_ssh.call_args.kwargs["stdin"], patch.encode())

    def test_native_apply_patch_sends_guard_metadata(self) -> None:
        patch = (
            "*** Begin Patch\n"
            "*** Update File: sample.txt\n"
            "@@\n"
            "-old\n"
            "+new\n"
            "*** End Patch\n"
        )
        ssh_result = {
            "exit_code": 1,
            "stdout": "",
            "stderr": "precondition failed\n",
            "timed_out": False,
            "truncated": False,
        }
        with (
            mock.patch.object(remote_fleet, "validate_host", return_value="13"),
            mock.patch.object(remote_fleet, "run_ssh", return_value=ssh_result) as run_ssh,
        ):
            remote_fleet.remote_apply_patch(
                {
                    "host": "13",
                    "workdir": "/tmp/project",
                    "patch": patch,
                    "expected_sha256": {"sample.txt": "b" * 64},
                }
            )
        command = run_ssh.call_args.args[1]
        guard = json.loads(base64.urlsafe_b64decode(shlex.split(command)[-1]))
        self.assertEqual(guard["paths"], ["sample.txt"])
        self.assertEqual(guard["expected_sha256"], {"sample.txt": "b" * 64})

    def _run_apply_guard_program(self, workdir: Path, guard: dict):
        fake_bin = workdir.parent / "fake-bin"
        fake_bin.mkdir(exist_ok=True)
        apply_patch = fake_bin / "apply_patch"
        if not apply_patch.exists():
            apply_patch.symlink_to("/usr/bin/true")
        encoded = base64.urlsafe_b64encode(
            json.dumps(guard, separators=(",", ":")).encode()
        ).decode()
        env = os.environ.copy()
        env["HOME"] = str(workdir.parent / "home")
        env["PATH"] = f"{fake_bin}:/usr/bin:/bin"
        return subprocess.run(
            [sys.executable, "-c", remote_fleet.REMOTE_CODEX_APPLY_PROGRAM, str(workdir), encoded],
            input="*** Begin Patch\n*** End Patch\n",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )

    def test_native_apply_guard_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workdir = root / "work"
            outside = root / "outside"
            workdir.mkdir()
            outside.mkdir()
            (workdir / "link").symlink_to(outside, target_is_directory=True)
            completed = self._run_apply_guard_program(
                workdir,
                {"paths": ["link/file.txt"], "expected_sha256": {}},
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("resolves outside workdir", completed.stderr)

    def test_native_apply_guard_rejects_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp) / "work"
            workdir.mkdir()
            (workdir / "sample.txt").write_text("current", encoding="utf-8")
            completed = self._run_apply_guard_program(
                workdir,
                {"paths": ["sample.txt"], "expected_sha256": {"sample.txt": "0" * 64}},
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("precondition failed", completed.stderr)

    def test_native_apply_patch_rejects_check_only(self) -> None:
        patch = "*** Begin Patch\n*** Add File: sample.txt\n+new\n*** End Patch\n"
        with mock.patch.object(remote_fleet, "validate_host", return_value="13"):
            with self.assertRaisesRegex(remote_fleet.ToolError, "no dry-run"):
                remote_fleet.remote_apply_patch(
                    {
                        "host": "13",
                        "workdir": "/tmp/project",
                        "patch": patch,
                        "check_only": True,
                    }
                )

    def test_remote_launch_tmux_structures_request(self) -> None:
        launch_payload = {
            "active": True,
            "command_sha256": "abc",
            "job_id": "exp001-20260828T000000Z-deadbeef",
            "log_path": "/data/logs/exp001.log",
            "pane_command": "bash",
            "pane_pid": 123,
            "record_path": "/home/user/.local/state/remote-fleet/jobs/job.json",
            "session": "exp001",
            "started_at": "2026-08-28T00:00:00+00:00",
            "status_path": "/data/logs/exp001.log.remote-fleet-status.json",
            "workdir": "/data/project",
        }
        ssh_result = {
            "exit_code": 0,
            "stdout": json.dumps(launch_payload),
            "stderr": "",
            "timed_out": False,
            "truncated": False,
        }
        with (
            mock.patch.object(remote_fleet, "validate_host", return_value="21"),
            mock.patch.object(remote_fleet, "run_ssh", return_value=ssh_result) as run_ssh,
        ):
            result = remote_fleet.remote_launch_tmux(
                {
                    "host": "21",
                    "workdir": "/data/project",
                    "session": "exp001",
                    "command": "python train.py",
                    "log_path": "/data/logs/exp001.log",
                }
            )
        self.assertEqual(result["host"], "21")
        sent = json.loads(run_ssh.call_args.kwargs["stdin"])
        self.assertEqual(sent["command"], "python train.py")
        self.assertFalse(sent["append_log"])

    def test_remote_status_parses_payload(self) -> None:
        status_payload = {
            "hostname": "dlserver06",
            "jobs": [],
            "tmux_sessions": [],
            "gpus": [],
        }
        ssh_result = {
            "exit_code": 0,
            "stdout": json.dumps(status_payload),
            "stderr": "",
            "timed_out": False,
            "truncated": False,
        }
        with (
            mock.patch.object(remote_fleet, "validate_host", return_value="21"),
            mock.patch.object(remote_fleet, "run_ssh", return_value=ssh_result),
        ):
            result = remote_fleet.remote_status(
                {"host": "21", "workdir": "/data", "session": "exp001"}
            )
        self.assertEqual(result["host"], "21")
        self.assertEqual(result["hostname"], "dlserver06")

    def test_embedded_remote_programs_compile(self) -> None:
        for name in (
            "REMOTE_READ_PROGRAM",
            "REMOTE_RUN_PROGRAM",
            "REMOTE_CODEX_APPLY_PROGRAM",
            "REMOTE_LAUNCH_TMUX_PROGRAM",
            "REMOTE_STOP_PROGRAM",
            "REMOTE_STATUS_PROGRAM",
        ):
            compile(getattr(remote_fleet, name), f"<{name}>", "exec")

    def test_run_ssh_truncates_output(self) -> None:
        with mock.patch.object(
            remote_fleet, "_ssh_command", return_value=[sys.executable, "-c"]
        ):
            result = remote_fleet.run_ssh(
                "13",
                "import sys; sys.stdout.write('a'*80000); sys.stderr.write('b'*80000)",
                max_output_chars=100,
            )
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["stdout"]) + len(result["stderr"]), 100)

    def test_run_ssh_cancellation_kills_process(self) -> None:
        token = remote_fleet.CancelToken()
        timer = threading.Timer(0.3, token.cancel)
        started = time.monotonic()
        with mock.patch.object(
            remote_fleet, "_ssh_command", return_value=[sys.executable, "-c"]
        ):
            timer.start()
            try:
                with self.assertRaises(remote_fleet.ToolCancelled):
                    remote_fleet.run_ssh(
                        "13",
                        "import time; time.sleep(30)",
                        timeout_seconds=30,
                        cancel_token=token,
                    )
            finally:
                timer.cancel()
        self.assertLess(time.monotonic() - started, 10)

    def test_run_ssh_timeout_includes_blocked_stdin_writer(self) -> None:
        started = time.monotonic()
        with mock.patch.object(
            remote_fleet, "_ssh_command", return_value=[sys.executable, "-c"]
        ):
            result = remote_fleet.run_ssh(
                "13",
                "import time; time.sleep(30)",
                stdin=b"x" * remote_fleet.MAX_COMMAND_BYTES,
                timeout_seconds=1,
            )
        self.assertTrue(result["timed_out"])
        self.assertLess(time.monotonic() - started, 10)

    def test_cancelled_tool_call_sends_no_response(self) -> None:
        def fake_handler(arguments, cancel_token=None):
            cancel_token.cancel()
            raise remote_fleet.ToolCancelled("cancelled")

        written: list[dict] = []
        token = remote_fleet.CancelToken()
        self.assertTrue(remote_fleet._CALL_SLOTS.acquire(blocking=False))
        with remote_fleet._INFLIGHT_LOCK:
            remote_fleet._INFLIGHT[7] = token
        with (
            mock.patch.object(remote_fleet, "DISPATCH_MODE", "inprocess"),
            mock.patch.dict(remote_fleet.TOOL_HANDLERS, {"remote_run": fake_handler}),
            mock.patch.object(remote_fleet, "_write_response", side_effect=written.append),
        ):
            remote_fleet._execute_tool_call(
                {
                    "jsonrpc": "2.0",
                    "id": 7,
                    "method": "tools/call",
                    "params": {"name": "remote_run", "arguments": {}},
                },
                token,
            )
        self.assertEqual(written, [])
        with remote_fleet._INFLIGHT_LOCK:
            self.assertNotIn(7, remote_fleet._INFLIGHT)

    def test_notifications_cancelled_cancels_inflight(self) -> None:
        token = remote_fleet.CancelToken()
        with remote_fleet._INFLIGHT_LOCK:
            remote_fleet._INFLIGHT[42] = token
        try:
            self.assertIsNone(
                remote_fleet.handle_request(
                    {
                        "jsonrpc": "2.0",
                        "method": "notifications/cancelled",
                        "params": {"requestId": 42},
                    }
                )
            )
            self.assertTrue(token.cancelled)
        finally:
            with remote_fleet._INFLIGHT_LOCK:
                remote_fleet._INFLIGHT.pop(42, None)

    def test_tool_queue_has_bounded_capacity(self) -> None:
        gate = threading.Event()
        written: list[dict] = []

        def blocked_handler(arguments, cancel_token=None):
            gate.wait(10)
            return {"ok": True}

        accepted = []
        try:
            with (
                mock.patch.object(remote_fleet, "DISPATCH_MODE", "inprocess"),
                mock.patch.dict(remote_fleet.TOOL_HANDLERS, {"remote_run": blocked_handler}),
                mock.patch.object(remote_fleet, "_write_response", side_effect=written.append),
            ):
                for index in range(remote_fleet.MAX_PENDING_TOOL_CALLS):
                    response = remote_fleet._queue_tool_call(
                        {
                            "jsonrpc": "2.0",
                            "id": 10_000 + index,
                            "method": "tools/call",
                            "params": {"name": "remote_run", "arguments": {}},
                        }
                    )
                    self.assertIsNone(response)
                    accepted.append(index)
                rejected = remote_fleet._queue_tool_call(
                    {
                        "jsonrpc": "2.0",
                        "id": 20_000,
                        "method": "tools/call",
                        "params": {"name": "remote_run", "arguments": {}},
                    }
                )
                self.assertTrue(rejected["result"]["isError"])
                self.assertIn("busy", rejected["result"]["content"][0]["text"])
                gate.set()
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    with remote_fleet._INFLIGHT_LOCK:
                        if not any(key >= 10_000 for key in remote_fleet._INFLIGHT if isinstance(key, int)):
                            break
                    time.sleep(0.02)
        finally:
            gate.set()
        with remote_fleet._INFLIGHT_LOCK:
            self.assertFalse(
                any(key >= 10_000 for key in remote_fleet._INFLIGHT if isinstance(key, int))
            )

    def test_multiplex_options_disabled_by_env(self) -> None:
        with mock.patch.dict(os.environ, {"REMOTE_FLEET_SSH_PERSIST": "0"}):
            self.assertEqual(remote_fleet._ssh_multiplex_options(), [])

    def test_multiplex_options_create_private_socket_dir(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            with (
                mock.patch.dict(os.environ, {"REMOTE_FLEET_SSH_PERSIST": "45"}),
                mock.patch.object(remote_fleet.Path, "home", return_value=Path(tmp)),
            ):
                options = remote_fleet._ssh_multiplex_options()
            joined = " ".join(options)
            self.assertIn("ControlMaster=auto", joined)
            self.assertIn("ControlPersist=45s", joined)
            directory = Path(tmp) / ".ssh" / "rf-mux"
            self.assertTrue(directory.is_dir())
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)

    def test_multiplex_options_fall_back_when_socket_bind_is_blocked(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp:
            with (
                mock.patch.dict(os.environ, {"REMOTE_FLEET_SSH_PERSIST": "45"}),
                mock.patch.object(remote_fleet.Path, "home", return_value=Path(tmp)),
                mock.patch.object(remote_fleet, "_can_bind_control_socket", return_value=False),
            ):
                self.assertEqual(remote_fleet._ssh_multiplex_options(), [])

    def _run_read_program(
        self,
        path: Path,
        *,
        start_line: int = 1,
        end_line: int = 0,
        max_bytes: int = 262_144,
        offset_bytes: int = 0,
        include_sha256: bool = False,
    ) -> dict:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                remote_fleet.REMOTE_READ_PROGRAM,
                str(path),
                str(start_line),
                str(end_line),
                str(max_bytes),
                str(offset_bytes),
                "1" if include_sha256 else "0",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        return json.loads(completed.stdout)

    def test_read_program_slices_and_reports_cursors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "sample.txt"
            target.write_text("".join(f"line{i}\n" for i in range(1, 101)), encoding="utf-8")

            payload = self._run_read_program(target, start_line=10, end_line=12)
            self.assertEqual(payload["content"], "line10\nline11\nline12\n")
            self.assertEqual(payload["first_line"], 10)
            self.assertEqual(payload["last_line"], 12)
            self.assertFalse(payload["eof_reached"])
            self.assertEqual(payload["next_start_line"], 13)
            self.assertIsNone(payload["sha256"])
            self.assertTrue(payload["snapshot_consistent"])

            full = self._run_read_program(target)
            self.assertTrue(full["eof_reached"])
            self.assertEqual(full["total_lines"], 100)
            self.assertIsNone(full["next_start_line"])

    def test_read_program_pages_past_byte_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "big.txt"
            target.write_text("".join(f"row-{i:06d}\n" for i in range(1, 1001)), encoding="utf-8")

            payload = self._run_read_program(
                target, start_line=500, end_line=502, max_bytes=64
            )
            self.assertEqual(payload["content"], "row-000500\nrow-000501\nrow-000502\n")
            self.assertEqual(payload["first_line"], 500)
            self.assertFalse(payload["content_truncated"])

            budget = self._run_read_program(target, start_line=1, max_bytes=25)
            self.assertTrue(budget["content_truncated"])
            self.assertEqual(budget["content"], "row-000001\nrow-000002\n")
            self.assertEqual(budget["next_start_line"], 3)

    def test_read_program_reports_out_of_range_and_offset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "small.txt"
            body = "alpha\nbeta\ngamma\n"
            target.write_text(body, encoding="utf-8")

            beyond = self._run_read_program(target, start_line=1000)
            self.assertEqual(beyond["content"], "")
            self.assertTrue(beyond["eof_reached"])
            self.assertEqual(beyond["total_lines"], 3)
            self.assertIsNone(beyond["first_line"])

            offset = self._run_read_program(target, offset_bytes=len("alpha\n"))
            self.assertEqual(offset["content"], "beta\ngamma\n")
            self.assertEqual(offset["first_line"], 1)
            self.assertIsNone(offset["total_lines"])

            hashed = self._run_read_program(target, include_sha256=True)
            self.assertEqual(
                hashed["sha256"], hashlib.sha256(body.encode("utf-8")).hexdigest()
            )

    def test_run_ssh_budget_favors_longer_stream(self) -> None:
        with mock.patch.object(
            remote_fleet, "_ssh_command", return_value=[sys.executable, "-c"]
        ):
            result = remote_fleet.run_ssh(
                "13",
                "import sys; sys.stdout.write('a'*200); sys.stderr.write('b'*10)",
                max_output_chars=100,
            )
        self.assertTrue(result["truncated"])
        self.assertEqual(result["stderr"], "b" * 10)
        self.assertEqual(result["stdout"], "a" * 90)

    def test_remote_run_sends_command_via_stdin(self) -> None:
        ssh_result = {
            "exit_code": 0,
            "stdout": "hi\n",
            "stderr": "",
            "timed_out": False,
            "truncated": False,
        }
        with (
            mock.patch.object(remote_fleet, "validate_host", return_value="13"),
            mock.patch.object(remote_fleet, "run_ssh", return_value=ssh_result) as run_ssh,
        ):
            result = remote_fleet.remote_run(
                {"host": "13", "workdir": "/tmp", "command": "echo hi"}
            )
        self.assertEqual(result["exit_code"], 0)
        self.assertNotIn("command", result)
        self.assertEqual(result["command_sha256"], hashlib.sha256(b"echo hi").hexdigest())
        self.assertEqual(run_ssh.call_args.kwargs["stdin"], b"echo hi")

    def test_remote_run_maps_remote_watchdog_timeout(self) -> None:
        ssh_result = {
            "exit_code": 124,
            "stdout": "",
            "stderr": "remote-fleet: command timed out after 2s\n",
            "timed_out": False,
            "truncated": False,
        }
        with (
            mock.patch.object(remote_fleet, "validate_host", return_value="13"),
            mock.patch.object(remote_fleet, "run_ssh", return_value=ssh_result),
        ):
            result = remote_fleet.remote_run(
                {"host": "13", "workdir": "/tmp", "command": "sleep 30", "timeout_seconds": 2}
            )
        self.assertTrue(result["timed_out"])
        self.assertEqual(result["exit_code"], 124)

    def test_remote_run_rejects_oversized_command(self) -> None:
        with (
            mock.patch.object(remote_fleet, "validate_host", return_value="13"),
            self.assertRaisesRegex(remote_fleet.ToolError, "command exceeds"),
        ):
            remote_fleet.remote_run(
                {"host": "13", "workdir": "/tmp", "command": "x" * (remote_fleet.MAX_COMMAND_BYTES + 1)}
            )

    def _run_run_program(self, workdir: str, command: str, timeout_s: int):
        return subprocess.run(
            [
                sys.executable,
                "-c",
                remote_fleet.REMOTE_RUN_PROGRAM,
                workdir,
                str(timeout_s),
            ],
            input=command,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    def test_run_program_passes_exit_code_and_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = self._run_run_program(tmp, "echo marker-ok; exit 7", 30)
        self.assertEqual(completed.returncode, 7)
        self.assertIn("marker-ok", completed.stdout)

    def test_run_program_times_out_with_124(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            started = time.monotonic()
            completed = self._run_run_program(tmp, "sleep 30", 1)
        self.assertEqual(completed.returncode, 124)
        self.assertIn("timed out", completed.stderr)
        self.assertLess(time.monotonic() - started, 15)

    def test_run_program_terminates_group_on_parent_signal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "child.pid"
            proc = subprocess.Popen(
                [sys.executable, "-c", remote_fleet.REMOTE_RUN_PROGRAM, tmp, "60"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            assert proc.stdin is not None
            proc.stdin.write(
                f"echo $$ > {shlex.quote(str(pid_file))}; exec sleep 60".encode()
            )
            proc.stdin.close()
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if pid_file.exists() and pid_file.read_text().strip():
                    break
                time.sleep(0.05)
            else:
                proc.kill()
                self.fail("child never started")
            child_pid = int(pid_file.read_text().strip())
            proc.send_signal(signal.SIGTERM)
            self.assertEqual(proc.wait(timeout=10), 143)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                os.kill(child_pid, signal.SIGKILL)
                self.fail("child survived the parent signal")

    def test_run_ssh_multibyte_output_keeps_character_budget(self) -> None:
        with mock.patch.object(
            remote_fleet, "_ssh_command", return_value=[sys.executable, "-c"]
        ):
            result = remote_fleet.run_ssh(
                "13",
                "import sys; sys.stdout.write('\\u6c49'*200); sys.stderr.write('b'*10)",
                max_output_chars=100,
            )
        self.assertTrue(result["truncated"])
        self.assertEqual(result["stderr"], "b" * 10)
        self.assertEqual(result["stdout"], "汉" * 90)

    def _run_launch_program(
        self, home: Path, spec: dict, tmux_behavior: str
    ) -> tuple[subprocess.CompletedProcess, Path]:
        fake_bin = home / "bin"
        fake_bin.mkdir(exist_ok=True)
        call_log = home / "tmux-calls.log"
        tmux = fake_bin / "tmux"
        tmux.write_text(
            "#!/bin/sh\n"
            f"echo \"$1\" >> {shlex.quote(str(call_log))}\n"
            f"{tmux_behavior}\n"
            "exit 0\n",
            encoding="utf-8",
        )
        tmux.chmod(0o755)
        env = {**os.environ, "HOME": str(home), "PATH": f"{fake_bin}:/usr/bin:/bin"}
        completed = subprocess.run(
            [sys.executable, "-c", remote_fleet.REMOTE_LAUNCH_TMUX_PROGRAM],
            input=json.dumps(spec),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )
        return completed, call_log

    def _launch_spec(self, home: Path, **overrides) -> dict:
        spec = {
            "append_log": False,
            "command": "echo hi",
            "log_path": str(home / "logs" / "exp001.log"),
            "session": "exp001",
            "workdir": str(home),
        }
        spec.update(overrides)
        return spec

    def test_launch_program_registers_and_tags_session(self) -> None:
        behavior = (
            'case "$1" in\n'
            "  has-session) exit 1 ;;\n"
            "  new-session) exit 0 ;;\n"
            "  set-option) [ \"${3#=}\" != \"$3\" ] && { echo \"no such session: $3\" >&2; exit 1; }; exit 0 ;;\n"
            "  list-panes) printf '4242\\tbash\\n'; exit 0 ;;\n"
            "esac"
        )
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            completed, call_log = self._run_launch_program(
                home, self._launch_spec(home), behavior
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["registered"])
            self.assertEqual(payload["pane_pid"], 4242)
            self.assertTrue(payload["job_id"].startswith("exp001-"))
            record_path = Path(payload["record_path"])
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertTrue(record["registered"])
            self.assertEqual(record["job_id"], payload["job_id"])
            calls = call_log.read_text(encoding="utf-8").split()
            self.assertEqual(
                calls, ["has-session", "new-session", "set-option", "list-panes"]
            )

    def test_launch_program_rolls_back_when_tagging_fails(self) -> None:
        behavior = (
            'case "$1" in\n'
            "  has-session) exit 1 ;;\n"
            "  new-session) exit 0 ;;\n"
            "  set-option) echo tag-boom >&2; exit 1 ;;\n"
            "esac"
        )
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            completed, call_log = self._run_launch_program(
                home, self._launch_spec(home), behavior
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("tag-boom", completed.stderr)
            registry = home / ".local" / "state" / "remote-fleet" / "jobs"
            self.assertEqual(list(registry.glob("*.json")), [])
            self.assertIn("kill-session", call_log.read_text(encoding="utf-8"))

    def test_launch_program_restores_previous_status_on_rollback(self) -> None:
        behavior = (
            'case "$1" in\n'
            "  has-session) exit 1 ;;\n"
            "  new-session) exit 0 ;;\n"
            "  set-option) exit 1 ;;\n"
            "esac"
        )
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            spec = self._launch_spec(home, append_log=True)
            log_path = Path(spec["log_path"])
            log_path.parent.mkdir(parents=True)
            log_path.write_text("old log\n", encoding="utf-8")
            status_path = Path(str(log_path) + ".remote-fleet-status.json")
            status_path.write_text("OLD-STATUS\n", encoding="utf-8")
            completed, _ = self._run_launch_program(home, spec, behavior)
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(status_path.read_text(encoding="utf-8"), "OLD-STATUS\n")

    def test_launch_program_refuses_existing_session(self) -> None:
        behavior = (
            'case "$1" in\n'
            "  has-session) exit 0 ;;\n"
            "esac"
        )
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            completed, _ = self._run_launch_program(
                home, self._launch_spec(home), behavior
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("already exists", completed.stderr)
            registry = home / ".local" / "state" / "remote-fleet" / "jobs"
            self.assertFalse(registry.exists())

    def test_status_program_tolerates_broken_registry_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            registry = home / ".local" / "state" / "remote-fleet" / "jobs"
            registry.mkdir(parents=True)
            log_path = home / "job.log"
            log_path.write_text("x" * 200_000 + "\nlast\n", encoding="utf-8")
            status_path = Path(str(log_path) + ".remote-fleet-status.json")
            status_path.write_text(
                json.dumps(
                    {
                        "exit_code": 143,
                        "finished_at": "2026-08-28T00:10:00Z",
                        "job_id": "exp001-x",
                        "session": "exp001",
                        "stopped_by_remote_fleet": True,
                    }
                ),
                encoding="utf-8",
            )
            record = {
                "command_sha256": "abc",
                "job_id": "exp001-x",
                "log_path": str(log_path),
                "session": "exp001",
                "started_at": "2026-08-28T00:00:00+00:00",
                "status_path": str(status_path),
                "workdir": str(home),
            }
            (registry / "exp001-x.json").write_text(json.dumps(record), encoding="utf-8")
            (registry / "broken-y.json").write_text("{not json", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    remote_fleet.REMOTE_STATUS_PROGRAM,
                    str(home),
                    "",
                    "5",
                    "20",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
                env={**os.environ, "HOME": str(home), "PATH": "/nonexistent"},
            )
            payload = json.loads(completed.stdout)
        self.assertEqual(len(payload["jobs"]), 1)
        self.assertEqual(payload["registry_records_total"], 2)
        self.assertEqual(payload["jobs"][0]["state"], "stopped")
        self.assertTrue(payload["jobs"][0]["stopped_by_remote_fleet"])
        self.assertLessEqual(len(payload["jobs"][0]["log_tail"].encode()), 50_000)
        self.assertTrue(payload["jobs"][0]["log_tail"].endswith("last\n"))
        self.assertFalse(payload["tmux_available"])

    def test_stop_program_refuses_unregistered_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    remote_fleet.REMOTE_STOP_PROGRAM,
                    "exp001",
                    "5",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                env={**os.environ, "HOME": tmp, "PATH": "/nonexistent"},
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("refusing to stop", completed.stderr)

    def test_stop_program_is_idempotent_for_finished_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            registry = home / ".local" / "state" / "remote-fleet" / "jobs"
            registry.mkdir(parents=True)
            record = {
                "job_id": "exp001-x",
                "log_path": str(home / "job.log"),
                "session": "exp001",
                "started_at": "2026-08-28T00:00:00+00:00",
                "status_path": str(home / "job.log.remote-fleet-status.json"),
                "workdir": str(home),
            }
            (registry / "exp001-x.json").write_text(json.dumps(record), encoding="utf-8")
            fake_bin = home / "bin"
            fake_bin.mkdir()
            fake_tmux = fake_bin / "tmux"
            fake_tmux.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
            fake_tmux.chmod(0o755)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    remote_fleet.REMOTE_STOP_PROGRAM,
                    "exp001",
                    "5",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
                env={**os.environ, "HOME": str(home), "PATH": str(fake_bin)},
            )
            payload = json.loads(completed.stdout)
        self.assertFalse(payload["was_running"])
        self.assertFalse(payload["session_stopped"])
        self.assertFalse(payload["status_written"])
        self.assertEqual(payload["job_id"], "exp001-x")

    def test_stop_program_refuses_mismatched_live_job_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            registry = home / ".local" / "state" / "remote-fleet" / "jobs"
            registry.mkdir(parents=True)
            record = {
                "job_id": "exp001-recorded",
                "log_path": str(home / "job.log"),
                "session": "exp001",
                "started_at": "2026-08-28T00:00:00+00:00",
                "status_path": str(home / "job.log.remote-fleet-status.json"),
                "workdir": str(home),
            }
            (registry / "exp001-recorded.json").write_text(
                json.dumps(record), encoding="utf-8"
            )
            fake_bin = home / "bin"
            fake_bin.mkdir()
            fake_tmux = fake_bin / "tmux"
            fake_tmux.write_text(
                "#!/bin/sh\n"
                "case \"$1\" in\n"
                "  has-session) exit 0 ;;\n"
                "  show-options) case \"$*\" in *'-t ='*) echo 'no such session' >&2; exit 1;; esac; echo exp001-other; exit 0 ;;\n"
                "  *) exit 0 ;;\n"
                "esac\n",
                encoding="utf-8",
            )
            fake_tmux.chmod(0o755)
            completed = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    remote_fleet.REMOTE_STOP_PROGRAM,
                    "exp001",
                    "5",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                env={**os.environ, "HOME": str(home), "PATH": str(fake_bin)},
            )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("identity does not match", completed.stderr)

    def test_remote_stop_job_parses_payload(self) -> None:
        stop_payload = {
            "grace_seconds": 10,
            "hard_killed": False,
            "job_id": "exp001-x",
            "record_path": "/home/user/.local/state/remote-fleet/jobs/exp001-x.json",
            "session": "exp001",
            "session_stopped": True,
            "status_written": True,
            "was_running": True,
        }
        ssh_result = {
            "exit_code": 0,
            "stdout": json.dumps(stop_payload),
            "stderr": "",
            "timed_out": False,
            "truncated": False,
        }
        with (
            mock.patch.object(remote_fleet, "validate_host", return_value="21"),
            mock.patch.object(remote_fleet, "run_ssh", return_value=ssh_result),
        ):
            result = remote_fleet.remote_stop_job({"host": "21", "session": "exp001"})
        self.assertEqual(result["host"], "21")
        self.assertTrue(result["session_stopped"])

    def test_read_program_bounds_single_giant_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "giant.txt"
            target.write_bytes(b"x" * 100_000 + b"\ntail\n")
            payload = self._run_read_program(target, max_bytes=1_024)
            self.assertEqual(len(payload["content"]), 1_024)
            self.assertTrue(payload["content_truncated"])
            self.assertEqual(payload["first_line"], 1)
            self.assertTrue(payload["line_fragment"])
            self.assertIsNone(payload["next_start_line"])
            self.assertEqual(payload["next_offset_bytes"], 1_024)
            continued = self._run_read_program(
                target, offset_bytes=payload["next_offset_bytes"], max_bytes=1_024
            )
            self.assertEqual(continued["content"], "x" * 1_024)

    def test_mcp_initialize_and_list(self) -> None:
        init = remote_fleet.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-06-18"},
            }
        )
        self.assertEqual(init["result"]["serverInfo"]["name"], "remote-fleet")
        listed = remote_fleet.handle_request(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
        )
        self.assertEqual(len(listed["result"]["tools"]), 6)

    def test_mcp_negotiates_unsupported_protocol_version(self) -> None:
        init = remote_fleet.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "1900-01-01"},
            }
        )
        self.assertEqual(init["result"]["protocolVersion"], remote_fleet.LATEST_PROTOCOL_VERSION)

    def test_tool_call_rejects_non_object_arguments(self) -> None:
        response = remote_fleet.handle_request(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "remote_run", "arguments": []},
            }
        )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("must be an object", response["result"]["content"][0]["text"])

    def test_cli_probe_reports_hosts(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), "cli", "probe"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "REMOTE_FLEET_HOSTS": "13,21"},
            check=True,
        )
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["hosts"], ["13", "21"])
        self.assertEqual(payload["version"], remote_fleet.SERVER_VERSION)

    def test_cli_rejects_unlisted_host(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH), "cli", "status", "--host", "nope"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "REMOTE_FLEET_HOSTS": "13"},
            check=False,
        )
        self.assertEqual(completed.returncode, 1)
        self.assertIn("not allowed", completed.stderr)

    def test_cli_launch_uses_same_handler_path(self) -> None:
        launch_payload = {"job_id": "exp001-x", "registered": True, "session": "exp001"}
        ssh_result = {
            "exit_code": 0,
            "stdout": json.dumps(launch_payload),
            "stderr": "",
            "timed_out": False,
            "truncated": False,
        }
        import contextlib
        import io

        buffer = io.StringIO()
        with (
            mock.patch.object(remote_fleet, "validate_host", return_value="21"),
            mock.patch.object(remote_fleet, "run_ssh", return_value=ssh_result) as run_ssh,
            contextlib.redirect_stdout(buffer),
        ):
            code = remote_fleet._cli_main(
                [
                    "launch",
                    "--host", "21",
                    "--workdir", "/data/project",
                    "--session", "exp001",
                    "--command", "python train.py",
                    "--log-path", "/data/logs/exp001.log",
                ]
            )
        self.assertEqual(code, 0)
        printed = json.loads(buffer.getvalue())
        self.assertEqual(printed["job_id"], "exp001-x")
        sent = json.loads(run_ssh.call_args.kwargs["stdin"])
        self.assertEqual(sent["session"], "exp001")
        self.assertFalse(sent["append_log"])

    def test_cli_patch_reads_patch_and_guard_files(self) -> None:
        patch = (
            "*** Begin Patch\n"
            "*** Update File: src/example.py\n"
            "@@\n"
            "-old\n"
            "+new\n"
            "*** End Patch\n"
        )
        ssh_result = {
            "exit_code": 0,
            "stdout": "Success. Updated the following files:\nM src/example.py\n",
            "stderr": "",
            "timed_out": False,
            "truncated": False,
        }
        import contextlib
        import io

        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            patch_path = tmp / "change.patch"
            guard_path = tmp / "guard.json"
            patch_path.write_text(patch, encoding="utf-8")
            guard_path.write_text(
                json.dumps({"src/example.py": "a" * 64}), encoding="utf-8"
            )
            buffer = io.StringIO()
            with (
                mock.patch.object(remote_fleet, "validate_host", return_value="13"),
                mock.patch.object(remote_fleet, "run_ssh", return_value=ssh_result) as run_ssh,
                contextlib.redirect_stdout(buffer),
            ):
                code = remote_fleet._cli_main(
                    [
                        "patch",
                        "--host", "13",
                        "--workdir", "/data/project",
                        "--patch-file", str(patch_path),
                        "--expected-sha256-file", str(guard_path),
                    ]
                )

        self.assertEqual(code, 0)
        printed = json.loads(buffer.getvalue())
        self.assertTrue(printed["applied"])
        self.assertEqual(printed["touched_paths"], ["src/example.py"])
        self.assertEqual(run_ssh.call_args.kwargs["stdin"], patch.encode())
        command = run_ssh.call_args.args[1]
        guard = json.loads(base64.urlsafe_b64decode(shlex.split(command)[-1]))
        self.assertEqual(guard["expected_sha256"], {"src/example.py": "a" * 64})

    def test_cli_patch_reads_patch_from_stdin(self) -> None:
        patch = "*** Begin Patch\n*** Add File: new.txt\n+new\n*** End Patch\n"
        ssh_result = {
            "exit_code": 0,
            "stdout": "ok\n",
            "stderr": "",
            "timed_out": False,
            "truncated": False,
        }
        import contextlib
        import io

        buffer = io.StringIO()
        with (
            mock.patch.object(remote_fleet, "validate_host", return_value="13"),
            mock.patch.object(remote_fleet, "run_ssh", return_value=ssh_result),
            mock.patch.object(remote_fleet.sys, "stdin", io.StringIO(patch)),
            contextlib.redirect_stdout(buffer),
        ):
            code = remote_fleet._cli_main(
                [
                    "patch",
                    "--host", "13",
                    "--workdir", "/data/project",
                    "--patch-file", "-",
                ]
            )
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(buffer.getvalue())["applied"])

    def _full_status_payload(self) -> dict:
        gpu = {
            "index": 0, "name": "NVIDIA A100 80GB PCIe", "uuid": "GPU-4f241103-35d4",
            "memory_total_mib": 81920, "memory_used_mib": 68065,
            "utilization_percent": 11, "temperature_c": 39,
        }
        job = {
            "command_sha256": "abc", "job_id": "exp001-x", "log_path": "/tmp/x.log",
            "registered": True, "session": "exp001", "started_at": "2026-08-30T19:40:26+00:00",
            "status_path": "/tmp/x.status.json", "workdir": "/tmp", "active": False,
            "exit_code": 0, "finished_at": "2026-08-30T19:40:26Z", "log": None,
            "log_tail": None, "record_path": "/home/u/.local/state/remote-fleet/jobs/x.json",
            "state": "succeeded", "stopped_by_remote_fleet": False,
        }
        return {
            "cpu_count": 128, "platform": "Linux", "uptime_seconds": 9.0, "hostname": "dl03",
            "timestamp": "2026-08-30T19:24:40+00:00", "load_average": [1.0, 1.0, 1.0],
            "memory": {"available_bytes": 1, "total_bytes": 2, "used_bytes": 1},
            "disk": {"free_bytes": 1, "path": "/home/u", "total_bytes": 2, "used_bytes": 1},
            "gpus": [gpu, {**gpu, "index": 1, "utilization_percent": 0}],
            "gpu_error": None, "tmux_available": True, "registry_records_total": 1,
            "session_filter": None, "jobs": [job],
            "tmux_sessions": [{"attached": False, "created_at": "t", "name": "exp001",
                               "remote_fleet_job_id": "exp001-x", "windows": 1,
                               "panes": [{"command": "bash", "dead": False, "dead_status": None,
                                          "index": 0, "pid": 7, "window": 0}]}],
        }

    def _status_with(self, arguments: dict) -> dict:
        ssh_result = {"exit_code": 0, "stdout": json.dumps(self._full_status_payload()),
                      "stderr": "", "timed_out": False, "truncated": False}
        with (
            mock.patch.object(remote_fleet, "validate_host", return_value="21"),
            mock.patch.object(remote_fleet, "run_ssh", return_value=ssh_result),
        ):
            return remote_fleet.remote_status({"host": "21", **arguments})

    def test_remote_status_compact_by_default(self) -> None:
        result = self._status_with({})
        self.assertEqual(result["gpu_model"], "NVIDIA A100 80GB PCIe")
        self.assertEqual(set(result["gpus"][0]), set(remote_fleet.COMPACT_GPU_KEYS))
        for gone in ("platform", "uptime_seconds", "cpu_count", "registry_records_total", "session_filter"):
            self.assertNotIn(gone, result)
        job = result["jobs"][0]
        for gone in ("command_sha256", "record_path", "status_path", "registered", "workdir", "log", "log_tail", "stopped_by_remote_fleet"):
            self.assertNotIn(gone, job)
        self.assertEqual(job["state"], "succeeded")
        self.assertEqual(result["tmux_sessions"][0]["panes"], [{"pid": 7, "command": "bash"}])
        self.assertEqual(result["host"], "21")

    def test_remote_status_session_filter_collapses_gpus(self) -> None:
        result = self._status_with({"session": "exp001"})
        self.assertNotIn("gpus", result)
        self.assertEqual(result["gpu_utilization_percent"], [11, 0])
        self.assertEqual(result["gpu_memory_used_mib"], [68065, 68065])
        with_gpus = self._status_with({"session": "exp001", "include_gpus": True})
        self.assertIn("gpus", with_gpus)

    def test_remote_status_full_detail_is_untrimmed(self) -> None:
        result = self._status_with({"detail": "full"})
        self.assertEqual(result["gpus"][0]["uuid"], "GPU-4f241103-35d4")
        self.assertIn("command_sha256", result["jobs"][0])
        with self.assertRaisesRegex(remote_fleet.ToolError, "detail must be"):
            self._status_with({"detail": "verbose"})

    def test_remote_read_file_slims_metadata(self) -> None:
        payload = {
            "content": "alpha\n", "content_truncated": False, "end_line": None,
            "eof_reached": True, "first_line": 1, "last_line": 1, "line_fragment": False,
            "next_offset_bytes": None, "next_start_line": None, "offset_bytes": 0,
            "path": "/tmp/a.txt", "sha256": None, "size_bytes": 6,
            "snapshot_consistent": True, "start_line": 1, "total_lines": 1,
        }
        ssh_result = {"exit_code": 0, "stdout": json.dumps(payload), "stderr": "",
                      "timed_out": False, "truncated": False}
        with (
            mock.patch.object(remote_fleet, "validate_host", return_value="13"),
            mock.patch.object(remote_fleet, "run_ssh", return_value=ssh_result),
        ):
            result = remote_fleet.remote_read_file({"host": "13", "path": "/tmp/a.txt"})
        self.assertEqual(
            set(result), {"content", "eof_reached", "first_line", "last_line", "path", "size_bytes", "total_lines", "host"}
        )
        self.assertTrue(result["eof_reached"])

    def test_cli_status_accepts_detail_flags(self) -> None:
        import contextlib
        import io

        ssh_result = {"exit_code": 0, "stdout": json.dumps(self._full_status_payload()),
                      "stderr": "", "timed_out": False, "truncated": False}
        buffer = io.StringIO()
        with (
            mock.patch.object(remote_fleet, "validate_host", return_value="21"),
            mock.patch.object(remote_fleet, "run_ssh", return_value=ssh_result),
            contextlib.redirect_stdout(buffer),
        ):
            code = remote_fleet._cli_main(["status", "--host", "21", "--detail", "full"])
        self.assertEqual(code, 0)
        self.assertIn("uuid", json.loads(buffer.getvalue())["gpus"][0])

    def test_subprocess_dispatch_returns_tool_error_from_worker(self) -> None:
        with mock.patch.dict(os.environ, {"REMOTE_FLEET_HOSTS": "13"}):
            response = remote_fleet.handle_request(
                {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                 "params": {"name": "remote_status", "arguments": {"host": "nope"}}}
            )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("not allowed", response["result"]["content"][0]["text"])

    def test_subprocess_dispatch_cancel_kills_worker_group(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "grandchild.pid"
            fake_worker = [
                sys.executable, "-c",
                "import pathlib,subprocess,sys,time; p=subprocess.Popen(['sleep','60']); "
                f"pathlib.Path({str(pid_file)!r}).write_text(str(p.pid)); time.sleep(60)",
            ]
            token = remote_fleet.CancelToken()
            timer = threading.Timer(0.5, token.cancel)
            started = time.monotonic()
            with mock.patch.object(remote_fleet, "_child_command", return_value=fake_worker):
                timer.start()
                try:
                    with self.assertRaises(remote_fleet.ToolCancelled):
                        remote_fleet._run_tool_in_child("remote_run", {}, token)
                finally:
                    timer.cancel()
            self.assertLess(time.monotonic() - started, 10)
            grandchild = int(pid_file.read_text().strip())
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    os.kill(grandchild, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                os.kill(grandchild, signal.SIGKILL)
                self.fail("grandchild survived worker cancellation")

    def test_cli_json_worker_maps_errors(self) -> None:
        env = {**os.environ, "REMOTE_FLEET_HOSTS": "13"}
        bad_host = subprocess.run(
            [sys.executable, str(MODULE_PATH), "cli-json"],
            input=json.dumps({"tool": "remote_status", "arguments": {"host": "nope"}}),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=False,
        )
        self.assertEqual(bad_host.returncode, 1)
        self.assertIn("not allowed", json.loads(bad_host.stdout)["error"])
        unknown = subprocess.run(
            [sys.executable, str(MODULE_PATH), "cli-json"],
            input=json.dumps({"tool": "nope", "arguments": {}}),
            text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, check=False,
        )
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("invalid worker request", json.loads(unknown.stdout)["error"])

    def test_stdio_tool_call_goes_through_worker(self) -> None:
        server = subprocess.Popen(
            [sys.executable, str(MODULE_PATH)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={**os.environ, "REMOTE_FLEET_HOSTS": "13"},
        )
        assert server.stdin and server.stdout
        for message in (
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "remote_status", "arguments": {"host": "nope"}}},
        ):
            server.stdin.write(json.dumps(message) + "\n")
        server.stdin.flush()
        responses = {}
        deadline = time.monotonic() + 20
        while 2 not in responses and time.monotonic() < deadline:
            line = server.stdout.readline()
            if not line:
                break
            item = json.loads(line)
            responses[item.get("id")] = item
        server.stdin.close()
        server.wait(timeout=10)
        self.assertIn(2, responses)
        self.assertTrue(responses[2]["result"]["isError"])
        self.assertIn("not allowed", responses[2]["result"]["content"][0]["text"])

    def test_stdio_smoke(self) -> None:
        requests = "\n".join(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"protocolVersion": "2025-06-18"},
                    }
                ),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}),
                "",
            ]
        )
        completed = subprocess.run(
            [sys.executable, str(MODULE_PATH)],
            input=requests,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        responses = [json.loads(line) for line in completed.stdout.splitlines()]
        self.assertEqual([item["id"] for item in responses], [1, 2])

    def test_opencode_config_registers_direct_tools_with_per_tool_permissions(self) -> None:
        config = remote_fleet.render_opencode_config()
        server = config["mcp"]["servers"]["rf"]
        self.assertEqual(server["type"], "local")
        self.assertEqual(server["command"], ["python3", os.path.abspath(str(MODULE_PATH))])
        self.assertIs(server["codemode"], False)  # direct tools, not the Code Mode catalog
        self.assertEqual(server["timeout"], {"execution": remote_fleet.CHILD_TIMEOUT_SECONDS * 1000})
        permission = config["permission"]
        for tool in remote_fleet.TOOLS:
            name = remote_fleet.opencode_tool_name(tool["name"])
            self.assertEqual(name, f"rf_{tool['name']}")
            self.assertRegex(name, r"^[A-Za-z0-9_-]{1,64}$")  # OpenCode tool-name rule
            expected = "allow" if tool["name"] in ("remote_read_file", "remote_status") else "ask"
            self.assertEqual(permission[name], expected, name)
        self.assertEqual(permission["bash"], {"ssh *": "ask"})

    def test_opencode_config_entrypoint_prints_json(self) -> None:
        result = subprocess.run(
            [sys.executable, "-B", str(MODULE_PATH), "opencode-config"], capture_output=True, text=True, timeout=30
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), remote_fleet.render_opencode_config())

if __name__ == "__main__":
    unittest.main(verbosity=2)
