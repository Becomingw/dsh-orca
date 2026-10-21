"""Run Codex's own apply-patch scenario suite against orca's applier.

tests/fixtures/codex_scenarios is copied from openai/codex (see its NOTICE):
each case has input/, patch.txt and expected/. orca must reproduce expected/
byte for byte, except where it deliberately refuses more (DIVERGENCES): there
the patch must be refused as a whole and the workdir left exactly as input/.
"""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCENARIOS = ROOT / "tests" / "fixtures" / "codex_scenarios"
SPEC = importlib.util.spec_from_file_location("orca_patch_apply_sc", ROOT / "engine" / "orca_patch_apply.py")
opa = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(opa)

# orca is all-or-nothing and never overwrites what it did not plan to touch.
DIVERGENCES = {
    "010_move_overwrites_existing_destination": "Move to destination already exists",
    "011_add_overwrites_existing_file": "Add File target already exists",
    "015_failure_after_partial_success_leaves_changes": "does not exist",
}


def tree(root):
    out = {}
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        rel = str(path.relative_to(root))
        out[rel] = path.read_bytes() if path.is_file() else "<dir>"
    return out


class CodexScenarioTests(unittest.TestCase):
    def test_scenarios(self):
        cases = sorted(p for p in SCENARIOS.iterdir() if p.is_dir())
        self.assertGreaterEqual(len(cases), 25)
        for case in cases:
            with self.subTest(case=case.name), tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                work = tmp / "work"
                if (case / "input").exists():
                    shutil.copytree(case / "input", work)
                else:
                    work.mkdir()
                before = tree(work)
                patch = (case / "patch.txt").read_text(encoding="utf-8")
                with mock.patch.dict(os.environ, {"HOME": str(tmp)}):
                    code, res = opa.main(json.dumps({
                        "mode": "apply", "workdir": str(work), "patch": patch,
                        "patch_id": "sc", "backup_root": str(tmp / "backups"),
                    }))
                expected = tree(case / "expected")
                if case.name in DIVERGENCES:
                    self.assertFalse(code == 0 and res.get("ok"), res)
                    self.assertIn(DIVERGENCES[case.name], json.dumps(res))
                    self.assertEqual(tree(work), before, "a refused patch must leave the workdir untouched")
                    continue
                if expected == before:
                    # A scenario Codex rejects: orca must reject it too, touching nothing.
                    self.assertFalse(code == 0 and res.get("ok"), res)
                    self.assertEqual(tree(work), before)
                    continue
                self.assertEqual((code, res.get("ok")), (0, True), res)
                self.assertEqual(tree(work), expected)


if __name__ == "__main__":
    unittest.main()
