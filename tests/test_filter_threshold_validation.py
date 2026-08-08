from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "video-frame-feeder.py"


class FilterThresholdValidationTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            stub_dir = Path(temp_dir)
            (stub_dir / "requests.py").write_text(
                '"""Import stub used only by CLI parser tests."""\n',
                encoding="utf-8",
            )
            env = os.environ.copy()
            existing_pythonpath = env.get("PYTHONPATH")
            env["PYTHONPATH"] = (
                str(stub_dir)
                if not existing_pythonpath
                else str(stub_dir) + os.pathsep + existing_pythonpath
            )
            return subprocess.run(
                [sys.executable, str(SCRIPT), *args],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )

    def test_boundary_values_are_accepted(self) -> None:
        for min_change, stddev_min in (("0", "0"), ("64", "255"), ("2", "0.0")):
            with self.subTest(min_change=min_change, stddev_min=stddev_min):
                result = self.run_cli(
                    "--min-change", min_change,
                    "--stddev-min", stddev_min,
                    "--help",
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("--min-change", result.stdout)
                self.assertIn("--stddev-min", result.stdout)

    def test_min_change_rejects_out_of_range_and_non_integer_values(self) -> None:
        for value in ("-1", "65", "1.5", "not-a-number"):
            with self.subTest(value=value):
                result = self.run_cli("--min-change", value, "--help")
                self.assertEqual(result.returncode, 2)
                self.assertIn("must be an integer from 0 through 64", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_stddev_min_rejects_out_of_range_and_non_finite_values(self) -> None:
        for value in ("-1", "256", "nan", "inf", "-inf", "not-a-number"):
            with self.subTest(value=value):
                result = self.run_cli("--stddev-min", value, "--help")
                self.assertEqual(result.returncode, 2)
                self.assertIn("must be a finite number from 0 through 255", result.stderr)
                self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
