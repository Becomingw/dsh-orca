"""Regression tests for orca's changes to the frozen engine.

Each test pins one deviation from upstream v0.7.0; see docs/engine-changes.md.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ENGINE_PATH = Path(__file__).resolve().parent.parent / "engine" / "remote_fleet_mcp.py"
SPEC = importlib.util.spec_from_file_location("orca_engine", ENGINE_PATH)
engine = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(engine)


def _patch(*body: str, sep: str = "\n") -> str:
    return sep.join(["*** Begin Patch", *body, "*** End Patch"])


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _wait_for(predicate, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


class PatchGuardTests(unittest.TestCase):
    def test_rejects_indented_header_escape(self) -> None:
        # Codex trims header lines, so this would write ../escaped.txt.
        patch = _patch("*** Add File: ok.txt", "+ok", "   *** Add File: ../escaped.txt", "+x")
        with self.assertRaisesRegex(engine.ToolError, "inside workdir"):
            engine.validate_codex_patch(patch)

    def test_rejects_trailing_whitespace_and_tab_indent(self) -> None:
        for header in ("\t*** Update File: /etc/passwd", "*** Delete File: ../x   "):
            with self.subTest(header=header):
                with self.assertRaises(engine.ToolError):
                    engine.validate_codex_patch(_patch("*** Add File: ok.txt", "+ok", header))

    def test_rejects_header_hidden_behind_unusual_line_separators(self) -> None:
        for sep in ("\r", " ", "\x0b"):
            with self.subTest(sep=repr(sep)):
                patch = _patch("*** Add File: ok.txt", f"+ok{sep}*** Add File: ../x", "+x")
                with self.assertRaises(engine.ToolError):
                    engine.validate_codex_patch(patch)

    def test_collects_trimmed_paths(self) -> None:
        _, paths = engine.validate_codex_patch(
            _patch("*** Add File: a.txt", "+a", "  *** Update File: b.txt", "@@", "-x", "+y")
        )
        self.assertEqual(paths, ["a.txt", "b.txt"])


class LaunchSessionTests(unittest.TestCase):
    def test_launch_rejects_dotted_session(self) -> None:
        with mock.patch.dict(os.environ, {"REMOTE_FLEET_HOSTS": "13"}), mock.patch.object(
            engine, "run_ssh"
        ) as run_ssh:
            with self.assertRaisesRegex(engine.ToolError, "may not contain"):
                engine.remote_launch_tmux(
                    {
                        "host": "13",
                        "workdir": "/data/p",
                        "session": "exp.1",
                        "command": "true",
                        "log_path": "/data/p/x.log",
                    }
                )
            run_ssh.assert_not_called()

    def test_status_and_stop_still_accept_dotted_session(self) -> None:
        # Records written by upstream may carry such names; we must still see them.
        self.assertEqual(engine.validate_session("exp.1"), "exp.1")


class MuxTests(unittest.TestCase):
    def test_default_control_persist_is_ten_minutes(self) -> None:
        env = {k: v for k, v in os.environ.items() if k != "REMOTE_FLEET_SSH_PERSIST"}
        with tempfile.TemporaryDirectory(dir="/tmp") as tmp, mock.patch.dict(
            os.environ, env, clear=True
        ), mock.patch.object(engine.Path, "home", return_value=Path(tmp)):
            options = engine._ssh_multiplex_options()
        self.assertIn("ControlPersist=600s", options)


class RunWatchdogTests(unittest.TestCase):
    def test_run_program_kills_command_when_reparented(self) -> None:
        """ssh -T has no pty: a dropped connection only reparents the watchdog."""
        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "child.pid"
            launcher = (
                "import subprocess, sys, os\n"
                "p = subprocess.Popen([sys.executable, '-c', sys.argv[1], sys.argv[2], '120'],"
                " stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                "p.stdin.write(sys.argv[3].encode()); p.stdin.close()\n"
                "import time\n"
                "deadline = time.time() + 10\n"
                "while time.time() < deadline and not os.path.exists(sys.argv[4]): time.sleep(0.05)\n"
                "os._exit(0)\n"
            )
            command = f"echo $$ > {shlex.quote(str(pid_file))}; exec sleep 120"
            subprocess.run(
                [sys.executable, "-c", launcher, engine.REMOTE_RUN_PROGRAM, tmp, command, str(pid_file)],
                check=True,
                timeout=20,
            )
            self.assertTrue(_wait_for(lambda: pid_file.exists() and pid_file.read_text().strip(), 5))
            child = int(pid_file.read_text().strip())
            try:
                self.assertTrue(
                    _wait_for(lambda: not _pid_alive(child), 10), "command survived disconnect"
                )
            finally:
                if _pid_alive(child):
                    os.kill(child, signal.SIGKILL)


class StatusFilterTests(unittest.TestCase):
    def test_session_filter_ignores_prefix_siblings_before_job_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            registry = home / ".local" / "state" / "remote-fleet" / "jobs"
            registry.mkdir(parents=True)

            def record(session: str, job_id: str, mtime: int) -> None:
                path = registry / f"{job_id}.json"
                path.write_text(
                    json.dumps(
                        {
                            "job_id": job_id,
                            "session": session,
                            "log_path": str(home / f"{job_id}.log"),
                            "status_path": str(home / f"{job_id}.log.remote-fleet-status.json"),
                            "started_at": "2026-10-21T00:00:00+00:00",
                            "workdir": str(home),
                        }
                    ),
                    encoding="utf-8",
                )
                os.utime(path, (mtime, mtime))

            record("exp", "exp-20261021T000000Z-aaaaaaaa", 1_000)
            # Newer records of a different session whose name shares the prefix.
            record("exp-2", "exp-2-20261021T000001Z-bbbbbbbb", 2_000)
            record("exp-2", "exp-2-20261021T000002Z-cccccccc", 3_000)
            completed = subprocess.run(
                [sys.executable, "-c", engine.REMOTE_STATUS_PROGRAM, str(home), "exp", "0", "1"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=True,
                env={**os.environ, "HOME": str(home), "PATH": "/nonexistent"},
            )
            payload = json.loads(completed.stdout)
        self.assertEqual([job["job_id"] for job in payload["jobs"]], ["exp-20261021T000000Z-aaaaaaaa"])


if __name__ == "__main__":
    unittest.main()
