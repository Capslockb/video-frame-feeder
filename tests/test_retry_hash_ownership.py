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


class RequestException(Exception):
    pass


REQUESTS = types.ModuleType("requests")
REQUESTS.RequestException = RequestException
REQUESTS.post = mock.Mock()
sys.modules["requests"] = REQUESTS

spec = importlib.util.spec_from_file_location("video_frame_feeder_retry_hash", SCRIPT)
assert spec and spec.loader
FEEDER = importlib.util.module_from_spec(spec)
spec.loader.exec_module(FEEDER)


THUMB_A = bytes([0] * 32 + [255] * 32)
THUMB_B = bytes([255] * 32 + [0] * 32)
JPEG = b"jpeg"


class RetryHashOwnershipTests(unittest.TestCase):
    def setUp(self) -> None:
        FEEDER._running = True

    def tearDown(self) -> None:
        FEEDER._running = True

    def run_continuous(self, thumbnails, full_frames, post_results, extra_args=None):
        thumb_values = list(thumbnails)
        full_values = list(full_frames)
        result_values = list(post_results)
        calls = {"thumb": 0, "full": 0, "post": 0}

        def fake_thumbnail(_cmd):
            index = calls["thumb"]
            calls["thumb"] += 1
            value = thumb_values[index]
            if calls["thumb"] >= len(thumb_values):
                FEEDER._running = False
            return value

        def fake_full(_cmd):
            index = calls["full"]
            calls["full"] += 1
            return full_values[index]

        def fake_post(*_args, **_kwargs):
            index = calls["post"]
            calls["post"] += 1
            return result_values[index]

        argv = [str(SCRIPT), "--source-label", "neutral"]
        if extra_args:
            argv.extend(extra_args)

        stdout = io.StringIO()
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(FEEDER, "capture_thumbnail", side_effect=fake_thumbnail),
            mock.patch.object(FEEDER, "capture_full_frame", side_effect=fake_full),
            mock.patch.object(FEEDER, "post_frame", side_effect=fake_post),
            mock.patch.object(FEEDER.time, "sleep", return_value=None),
            contextlib.redirect_stdout(stdout),
        ):
            status = FEEDER.main()

        return status, calls, stdout.getvalue()

    def test_rejected_first_delivery_retries_identical_thumbnail(self) -> None:
        status, calls, output = self.run_continuous(
            [THUMB_A, THUMB_A],
            [JPEG, JPEG],
            [
                {"accepted": False, "reason": "bridge_rejected"},
                {"accepted": True},
            ],
        )

        self.assertEqual(status, 0)
        self.assertEqual(calls["full"], 2)
        self.assertEqual(calls["post"], 2)
        self.assertIn("Bridge rejected", output)
        self.assertIn("Sent 4B", output)
        self.assertIn("'sent': 1", output)
        self.assertNotIn("'skipped_unchanged': 1", output)

    def test_full_frame_capture_failure_retries_identical_thumbnail(self) -> None:
        status, calls, output = self.run_continuous(
            [THUMB_A, THUMB_A],
            [None, JPEG],
            [{"accepted": True}],
        )

        self.assertEqual(status, 1)
        self.assertEqual(calls["full"], 2)
        self.assertEqual(calls["post"], 1)
        self.assertIn("Full-frame capture failed", output)
        self.assertIn("Sent 4B", output)
        self.assertIn("'sent': 1", output)

    def test_failed_changed_frame_does_not_replace_previous_accepted_hash(self) -> None:
        status, calls, output = self.run_continuous(
            [THUMB_A, THUMB_B, THUMB_B],
            [JPEG, JPEG, JPEG],
            [
                {"accepted": True},
                {"accepted": False, "reason": "http_error"},
                {"accepted": True},
            ],
        )

        self.assertEqual(status, 0)
        self.assertEqual(calls["full"], 3)
        self.assertEqual(calls["post"], 3)
        self.assertIn("Bridge rejected", output)
        self.assertEqual(output.count("Sent 4B"), 2)
        self.assertIn("'sent': 2", output)
        self.assertNotIn("'skipped_unchanged': 1", output)

    def test_accepted_thumbnail_becomes_baseline_and_suppresses_duplicate(self) -> None:
        status, calls, output = self.run_continuous(
            [THUMB_A, THUMB_A],
            [JPEG],
            [{"accepted": True}],
        )

        self.assertEqual(status, 0)
        self.assertEqual(calls["full"], 1)
        self.assertEqual(calls["post"], 1)
        self.assertIn("'sent': 1", output)
        self.assertIn("'skipped_unchanged': 1", output)

    def test_normalized_http_json_and_bridge_failures_all_retry(self) -> None:
        for reason in ("http_error", "json_error", "bridge_rejected"):
            with self.subTest(reason=reason):
                FEEDER._running = True
                status, calls, output = self.run_continuous(
                    [THUMB_A, THUMB_A],
                    [JPEG, JPEG],
                    [
                        {"accepted": False, "reason": reason},
                        {"accepted": True},
                    ],
                )

                self.assertEqual(status, 0)
                self.assertEqual(calls["post"], 2)
                self.assertIn("'sent': 1", output)
                self.assertNotIn("'skipped_unchanged': 1", output)

    def test_no_content_filter_still_sends_each_attempt(self) -> None:
        status, calls, output = self.run_continuous(
            [THUMB_A, THUMB_A],
            [JPEG, JPEG],
            [{"accepted": True}, {"accepted": True}],
            extra_args=["--no-content-filter"],
        )

        self.assertEqual(status, 0)
        self.assertEqual(calls["full"], 2)
        self.assertEqual(calls["post"], 2)
        self.assertIn("'sent': 2", output)


if __name__ == "__main__":
    unittest.main()
