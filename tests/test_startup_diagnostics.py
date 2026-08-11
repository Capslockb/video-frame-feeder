from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "video-frame-feeder.py"


def load_module():
    requests_stub = types.ModuleType("requests")
    requests_stub.RequestException = Exception
    sys.modules.setdefault("requests", requests_stub)

    spec = importlib.util.spec_from_file_location("video_frame_feeder_startup_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load feeder module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StartupDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.feeder = load_module()

    def run_once(self, *args: str):
        stdout = io.StringIO()
        stderr = io.StringIO()
        pixels = bytes(range(64))

        self.feeder._running = True
        with (
            mock.patch.object(sys, "argv", [str(SCRIPT), *args, "--once"]),
            mock.patch.object(self.feeder, "capture_thumbnail", return_value=pixels),
            mock.patch.object(self.feeder, "capture_full_frame", return_value=b"jpeg"),
            mock.patch.object(self.feeder, "post_frame", return_value={"accepted": True}),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            return_code = self.feeder.main()

        return return_code, stdout.getvalue(), stderr.getvalue()

    def test_sensitive_startup_values_are_redacted(self) -> None:
        endpoint_secret = "ENDPOINT_SECRET"
        source_secret = "Confidential Client - Contract.docx"
        display_secret = ":77.0-DISPLAY_SECRET"
        label_secret = "LABEL_SECRET"
        endpoint = (
            f"https://user:{endpoint_secret}@bridge.example/private/{endpoint_secret}"
            f"?token={endpoint_secret}#{endpoint_secret}"
        )

        return_code, stdout, stderr = self.run_once(
            "--endpoint", endpoint,
            "--source", source_secret,
            "--display", display_secret,
            "--source-label", label_secret,
            "--width", "1280",
            "--height", "720",
        )
        combined = stdout + stderr

        self.assertEqual(return_code, 0, stderr)
        for sentinel in (endpoint_secret, source_secret, display_secret, label_secret, "user:"):
            self.assertNotIn(sentinel, combined)
        self.assertIn("Feeder started — endpoint: configured", stdout)
        self.assertIn("Capture: window @ 1280x720, 1.0s interval", stdout)
        self.assertIn("Source label for webhook: explicit", stdout)
        self.assertNotIn("ffmpeg full:", stdout)
        self.assertNotIn("ffmpeg thumb:", stdout)

    def test_screen_mode_and_implicit_label_remain_understandable(self) -> None:
        return_code, stdout, stderr = self.run_once(
            "--endpoint", "http://127.0.0.1:18943/frame",
            "--source", "screen",
        )

        self.assertEqual(return_code, 0, stderr)
        self.assertIn("Feeder started — endpoint: configured", stdout)
        self.assertIn("Capture: screen @ 768x768, 1.0s interval", stdout)
        self.assertIn("Content filter: ON", stdout)
        self.assertIn("Source label for webhook: implicit", stdout)

    def test_x11_window_identifier_is_not_rendered(self) -> None:
        window_id = "0x00ABCDEF"
        return_code, stdout, stderr = self.run_once(
            "--source", window_id,
            "--display", ":99.0",
        )

        self.assertEqual(return_code, 0, stderr)
        self.assertNotIn(window_id, stdout + stderr)
        self.assertNotIn(":99.0", stdout + stderr)
        self.assertIn("Capture: window", stdout)


if __name__ == "__main__":
    unittest.main()
