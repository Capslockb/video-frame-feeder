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

spec = importlib.util.spec_from_file_location("video_frame_feeder_luminance", SCRIPT)
assert spec and spec.loader
FEEDER = importlib.util.module_from_spec(spec)
spec.loader.exec_module(FEEDER)


BLACK = bytes([0] * 64)
WHITE = bytes([255] * 64)
DARK_PATTERN = bytes([20] * 32 + [40] * 32)
LIGHT_PATTERN = bytes([100] * 32 + [120] * 32)
STRUCT_A = bytes([0] * 32 + [255] * 32)
STRUCT_B = bytes([255] * 32 + [0] * 32)
JPEG = b"jpeg"


class LuminanceFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        FEEDER._running = True

    def tearDown(self) -> None:
        FEEDER._running = True

    def decide(self, pixels, last_pixels=None, *, min_luma_change=8.0, enabled=True):
        last_signature = (
            None if last_pixels is None else FEEDER.frame_signature_8x8(last_pixels)
        )
        return FEEDER.should_send(
            pixels,
            last_signature,
            min_change=2,
            min_luma_change=min_luma_change,
            stddev_min=0,
            enabled=enabled,
        )

    def test_black_to_white_is_detected_despite_identical_ahash(self) -> None:
        self.assertEqual(FEEDER.perceptual_hash_8x8(BLACK), FEEDER.perceptual_hash_8x8(WHITE))
        send, reason = self.decide(WHITE, BLACK)
        self.assertTrue(send)
        self.assertIn("luma=255.0", reason)

    def test_same_spatial_pattern_brightness_shift_is_detected(self) -> None:
        self.assertEqual(
            FEEDER.perceptual_hash_8x8(DARK_PATTERN),
            FEEDER.perceptual_hash_8x8(LIGHT_PATTERN),
        )
        send, reason = self.decide(LIGHT_PATTERN, DARK_PATTERN)
        self.assertTrue(send)
        self.assertIn("luma=80.0", reason)

    def test_identical_uniform_frame_is_suppressed(self) -> None:
        send, reason = self.decide(BLACK, BLACK)
        self.assertFalse(send)
        self.assertIn("unchanged", reason)

    def test_identical_non_uniform_frame_is_suppressed(self) -> None:
        send, reason = self.decide(STRUCT_A, STRUCT_A)
        self.assertFalse(send)
        self.assertIn("unchanged", reason)

    def test_structural_ahash_change_remains_a_change_signal(self) -> None:
        send, reason = self.decide(STRUCT_B, STRUCT_A)
        self.assertTrue(send)
        self.assertIn("d=64", reason)

    def test_luminance_threshold_boundary_is_explicit(self) -> None:
        base = bytes([100] * 64)
        below = bytes([107] * 64)
        boundary = bytes([108] * 64)

        self.assertFalse(self.decide(below, base, min_luma_change=8.0)[0])
        send, reason = self.decide(boundary, base, min_luma_change=8.0)
        self.assertTrue(send)
        self.assertIn("luma=8.0", reason)

    def test_zero_luminance_threshold_disables_only_luminance_signal(self) -> None:
        send, reason = self.decide(WHITE, BLACK, min_luma_change=0)
        self.assertFalse(send)
        self.assertIn("luma=255.0<0.0", reason)

    def test_no_content_filter_remains_unconditional(self) -> None:
        send, reason = self.decide(BLACK, BLACK, enabled=False)
        self.assertTrue(send)
        self.assertEqual(reason, "filter_off")

    def run_continuous(self, thumbnails, full_frames, post_results):
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

        stdout = io.StringIO()
        argv = [str(SCRIPT), "--source-label", "neutral"]
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

    def test_rejected_luminance_change_does_not_advance_signature(self) -> None:
        for reason in ("http_error", "json_error", "bridge_rejected"):
            with self.subTest(reason=reason):
                FEEDER._running = True
                status, calls, output = self.run_continuous(
                    [DARK_PATTERN, LIGHT_PATTERN, LIGHT_PATTERN],
                    [JPEG, JPEG, JPEG],
                    [
                        {"accepted": True},
                        {"accepted": False, "reason": reason},
                        {"accepted": True},
                    ],
                )
                self.assertEqual(status, 0)
                self.assertEqual(calls["post"], 3)
                self.assertEqual(output.count("Sent 4B"), 2)
                self.assertNotIn("'skipped_unchanged': 1", output)

    def test_capture_failure_does_not_advance_luminance_signature(self) -> None:
        status, calls, output = self.run_continuous(
            [DARK_PATTERN, LIGHT_PATTERN, LIGHT_PATTERN],
            [JPEG, None, JPEG],
            [{"accepted": True}, {"accepted": True}],
        )
        self.assertEqual(status, 1)
        self.assertEqual(calls["full"], 3)
        self.assertEqual(calls["post"], 2)
        self.assertIn("Full-frame capture failed", output)
        self.assertEqual(output.count("Sent 4B"), 2)
        self.assertNotIn("'skipped_unchanged': 1", output)

    def test_luminance_threshold_cli_rejects_non_finite_or_out_of_range(self) -> None:
        for value in ("nan", "inf", "-1", "256"):
            with self.subTest(value=value):
                FEEDER._running = True
                with (
                    mock.patch.object(sys, "argv", [str(SCRIPT), "--min-luma-change", value]),
                    contextlib.redirect_stderr(io.StringIO()),
                    self.assertRaises(SystemExit) as raised,
                ):
                    FEEDER.main()
                self.assertNotEqual(raised.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
