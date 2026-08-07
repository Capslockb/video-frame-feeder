from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "video-frame-feeder.py"


class CliHelpTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            stub_dir = Path(temp_dir)
            (stub_dir / "requests.py").write_text(
                '"""Import stub used only by CLI parser smoke tests."""\n',
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

    def test_default_help_uses_reserved_short_option(self) -> None:
        result = self.run_cli("--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("-h, --help", result.stdout)
        self.assertIn("--height HEIGHT", result.stdout)
        self.assertNotIn("-h HEIGHT", result.stdout)
        self.assertNotIn("Traceback", result.stderr)

    def test_height_value_parses_before_help_exit(self) -> None:
        result = self.run_cli("--height", "720", "--help")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--height HEIGHT", result.stdout)
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main()
