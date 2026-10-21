"""Contract tests: orca and upstream must see and stop each other's jobs.

The remote programs are the only code that touches the remote protocol
(state dir, registry, status sidecar, @remote_fleet_job_id), so running them
locally against a real tmux server on an isolated socket exercises the exact
bytes both engines exchange on a real host. See docs/compatibility.md.

Upstream is located via $ORCA_UPSTREAM_ENGINE or ../remote-fleet; the suite is
skipped when either upstream or tmux is unavailable.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parent.parent
ORCA_PATH = ROOT / "engine" / "remote_fleet_mcp.py"
UPSTREAM_PATH = Path(
    os.environ.get(
        "ORCA_UPSTREAM_ENGINE",
        ROOT.parent / "remote-fleet" / "scripts" / "remote_fleet_mcp.py",
    )
)


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


orca = _load("orca_engine", ORCA_PATH)
upstream = _load("upstream_engine", UPSTREAM_PATH) if UPSTREAM_PATH.is_file() else None


@unittest.skipUnless(upstream is not None, f"upstream engine not found at {UPSTREAM_PATH}")
@unittest.skipUnless(shutil.which("tmux"), "tmux not installed")
class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        # /tmp keeps the tmux socket path well under the 104-byte limit.
        self._tmp = tempfile.TemporaryDirectory(dir="/tmp", prefix="orca-ct-")
        self.home = Path(self._tmp.name)
        (self.home / "tmux").mkdir()
        self.env = {
            "HOME": str(self.home),
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "TMUX_TMPDIR": str(self.home / "tmux"),
            "LANG": "C.UTF-8",
        }

    def tearDown(self) -> None:
        subprocess.run(["tmux", "kill-server"], env=self.env, capture_output=True, check=False)
        self._tmp.cleanup()

    # -- remote program drivers ------------------------------------------------

    def _program(self, engine, name: str, args: list[str], stdin: str | None = None) -> dict:
        completed = subprocess.run(
            [sys.executable, "-c", getattr(engine, name), *args],
            input=stdin,
            text=True,
            capture_output=True,
            env=self.env,
            timeout=60,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def launch(self, engine, session: str, command: str, orca_log=None) -> dict:
        spec = {
            "append_log": False,
            "command": command,
            "log_path": str(self.home / "logs" / f"{session}.log"),
            "session": session,
            "workdir": str(self.home),
        }
        if orca_log is not None:
            spec["orca_log"] = orca_log
        return self._program(engine, "REMOTE_LAUNCH_TMUX_PROGRAM", [], json.dumps(spec))

    def status(self, engine, session: str = "") -> dict:
        return self._program(engine, "REMOTE_STATUS_PROGRAM", [str(self.home), session, "5", "20"])

    def stop(self, engine, session: str, grace: int = 2) -> dict:
        return self._program(engine, "REMOTE_STOP_PROGRAM", [session, str(grace)])

    def job(self, engine, session: str) -> dict:
        jobs = [job for job in self.status(engine, session)["jobs"] if job["session"] == session]
        self.assertEqual(len(jobs), 1, jobs)
        return jobs[0]

    def wait_state(self, engine, session: str, state: str, timeout: float = 15) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            job = self.job(engine, session)
            if job["state"] == state or time.monotonic() > deadline:
                self.assertEqual(job["state"], state, job)
                return job
            time.sleep(0.2)

    # -- contract ---------------------------------------------------------------

    def test_orca_launch_is_visible_to_upstream_with_exit_code(self) -> None:
        launched = self.launch(orca, "ct1", "sleep 1; exit 7")
        running = self.job(upstream, "ct1")
        self.assertEqual(running["job_id"], launched["job_id"])
        self.assertTrue(running["active"])
        done = self.wait_state(upstream, "ct1", "failed")
        self.assertEqual(done["exit_code"], 7)

    def test_upstream_launch_is_visible_to_orca(self) -> None:
        launched = self.launch(upstream, "ct2", "echo hello; sleep 1")
        running = self.job(orca, "ct2")
        self.assertEqual(running["job_id"], launched["job_id"])
        self.assertTrue(running["active"])
        done = self.wait_state(orca, "ct2", "succeeded")
        self.assertEqual(done["exit_code"], 0)
        self.assertIn("hello", done["log_tail"])

    def test_orca_stops_upstream_job(self) -> None:
        launched = self.launch(upstream, "ct3", "sleep 300")
        result = self.stop(orca, "ct3")
        self.assertTrue(result["session_stopped"])
        self.assertEqual(result["job_id"], launched["job_id"])
        self.assertEqual(self.job(upstream, "ct3")["state"], "stopped")

    def test_upstream_stops_orca_job(self) -> None:
        launched = self.launch(orca, "ct4", "sleep 300")
        result = self.stop(upstream, "ct4")
        self.assertTrue(result["session_stopped"])
        self.assertEqual(result["job_id"], launched["job_id"])
        self.assertEqual(self.job(orca, "ct4")["state"], "stopped")

    def test_both_refuse_to_stop_untagged_session(self) -> None:
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", "ct5", "sleep 300"], env=self.env, check=True
        )
        for engine in (orca, upstream):
            completed = subprocess.run(
                [sys.executable, "-c", engine.REMOTE_STOP_PROGRAM, "ct5", "1"],
                text=True,
                capture_output=True,
                env=self.env,
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
        alive = subprocess.run(["tmux", "has-session", "-t", "=ct5"], env=self.env, check=False)
        self.assertEqual(alive.returncode, 0)

    # -- orca-only behaviour on real tmux ----------------------------------------

    def test_orca_stop_sigkills_processes_ignoring_term_and_hup(self) -> None:
        pid_file = self.home / "stubborn.pid"
        self.launch(orca, "ct6", f"trap '' TERM HUP; echo $$ > {pid_file}; while :; do sleep 1; done")
        deadline = time.monotonic() + 10
        while not (pid_file.exists() and pid_file.read_text().strip()):
            self.assertLess(time.monotonic(), deadline, "job never started")
            time.sleep(0.05)
        pid = int(pid_file.read_text())
        result = self.stop(orca, "ct6", grace=1)
        self.assertTrue(result["session_stopped"])
        self.assertTrue(result["sigkilled_pgids"], result)
        time.sleep(0.3)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)


    # -- M4 structured logging on real tmux ------------------------------------------

    def _wait_for(self, predicate, timeout: float = 15) -> None:
        deadline = time.monotonic() + timeout
        while not predicate():
            self.assertLess(time.monotonic(), deadline, "condition not reached")
            time.sleep(0.1)

    def test_structured_log_normalizes_and_stays_compatible(self) -> None:
        spec = orca.orca_ext.logger_spec(orca.__dict__, {"tail_lines": 50})
        command = (
            "printf '\\033[31mred\\033[0m\\n'; "
            "printf 'epoch 1 10%%\\repoch 1 50%%\\repoch 1 100%%\\n'; "
            "for i in 1 2 3; do echo same; done; "
            "echo 'RuntimeError: boom' >&2; exit 7"
        )
        launched = self.launch(orca, "ct7", command, orca_log=spec)
        orca_dir = Path(launched["orca_dir"])
        # Exit code still comes from PIPESTATUS[0], for both engines.
        self.assertEqual(self.wait_state(upstream, "ct7", "failed")["exit_code"], 7)
        self._wait_for(lambda: "logger" in json.loads((orca_dir / "meta.json").read_text()))
        meta = json.loads((orca_dir / "meta.json").read_text())
        self.assertEqual(meta["exit_code"], 7)
        self.assertEqual(meta["job_id"], launched["job_id"])
        ring = (orca_dir / "tail.ring").read_text().splitlines()
        self.assertIn("red", ring)
        self.assertIn("epoch 1 100%", ring)
        self.assertNotIn("epoch 1 10%", "\n".join(ring))
        self.assertIn("same ×3", ring)
        self.assertTrue(any(line.startswith("[remote-fleet] finished_at=") for line in ring))
        events = [json.loads(line) for line in (orca_dir / "events.ndjson").read_text().splitlines()]
        self.assertIn("error", {e["level"] for e in events if "boom" in e["msg"]})
        self.assertIn({"repeat": 2, "msg": "same"}, [{"repeat": e.get("repeat"), "msg": e["msg"]} for e in events if e.get("repeat")])
        # status reads the ring, upstream still reads the raw log.
        job = self.job(orca, "ct7")
        self.assertEqual(job["log_tail_source"], "ring")
        self.assertIn("same ×3", job["log_tail"])
        self.assertIn("same\nsame\n", self.job(upstream, "ct7")["log_tail"])

    def test_stop_finalizes_structured_log(self) -> None:
        spec = orca.orca_ext.logger_spec(orca.__dict__, True)
        launched = self.launch(orca, "ct8", "echo started; sleep 300", orca_log=spec)
        orca_dir = Path(launched["orca_dir"])
        self._wait_for(lambda: (orca_dir / "tail.ring").exists())
        self.stop(orca, "ct8", grace=2)
        self._wait_for(lambda: "logger" in json.loads((orca_dir / "meta.json").read_text()))
        self.assertIn("started", (orca_dir / "tail.ring").read_text())

    def test_launch_rollback_removes_orca_dir(self) -> None:
        subprocess.run(["tmux", "new-session", "-d", "-s", "ct9", "sleep 60"], env=self.env, check=True)
        spec = {
            "append_log": False, "command": "true", "session": "ct9", "workdir": str(self.home),
            "log_path": str(self.home / "logs" / "ct9.log"),
            "orca_log": orca.orca_ext.logger_spec(orca.__dict__, True),
        }
        completed = subprocess.run(
            [sys.executable, "-c", orca.REMOTE_LAUNCH_TMUX_PROGRAM], input=json.dumps(spec),
            text=True, capture_output=True, env=self.env, check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertFalse((self.home / "logs" / "ct9.log.orca").exists())


if __name__ == "__main__":
    unittest.main()
