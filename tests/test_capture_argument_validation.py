from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "video-frame-feeder.py"


class CaptureArgumentValidationTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            stub_dir = Path(temp_dir)
            (stub_dir / "requests.py").write_text(
                '"""Import stub used only by CLI parser validation tests."""\n',
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

    def test_positive_dimensions_and_subsecond_interval_parse(self) -> None:
        result = self.run_cli(
            "--width", "1",
            "--height", "2",
            "--interval", "0.25",
            "--help",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        normalized_help = " ".join(result.stdout.split())
        self.assertIn("values below 1.0 are clamped to 1.0", normalized_help)
        self.assertNotIn("Traceback", result.stderr)

    def test_default_help_preserves_capture_defaults(self) -> None:
        result = self.run_cli("--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Capture width (Gemini-native default: 768)", result.stdout)
        self.assertIn("Capture height (Gemini-native default: 768)", result.stdout)
        self.assertIn("default: 1.0", result.stdout)
        self.assertIn("--source SOURCE", result.stdout)
        self.assertIn("--min-change MIN_CHANGE", result.stdout)
        self.assertIn("--stddev-min STDDEV_MIN", result.stdout)
        self.assertIn("--force", result.stdout)
        self.assertIn("--once", result.stdout)

    def test_zero_and_negative_dimensions_are_rejected(self) -> None:
        for option in ("--width", "--height"):
            for value in ("0", "-1"):
                with self.subTest(option=option, value=value):
                    result = self.run_cli(f"{option}={value}", "--help")
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("must be greater than 0", result.stderr)
                    self.assertNotIn("Traceback", result.stderr)

    def test_non_integer_dimensions_are_rejected(self) -> None:
        for option in ("--width", "--height"):
            with self.subTest(option=option):
                result = self.run_cli(f"{option}=1.5", "--help")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("must be an integer", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_non_finite_and_non_positive_intervals_are_rejected(self) -> None:
        for value in ("0", "-1", "nan", "inf", "-inf"):
            with self.subTest(value=value):
                result = self.run_cli(f"--interval={value}", "--help")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("must be a finite number greater than 0", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_positive_finite_interval_above_one_parses(self) -> None:
        result = self.run_cli("--interval", "2.5", "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
