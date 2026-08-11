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
SENTINEL = "raw-response-secret-marker"


class RequestException(Exception):
    pass


class FakeResponse:
    def __init__(self) -> None:
        self.json_result = {"accepted": True}
        self.json_error = None
        self.raise_error = None

    def raise_for_status(self) -> None:
        if self.raise_error is not None:
            raise self.raise_error

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.json_result


class RequestsStub(types.ModuleType):
    RequestException = RequestException

    def __init__(self) -> None:
        super().__init__("requests")
        self.calls = []
        self.response = FakeResponse()

    def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


REQUESTS = RequestsStub()
sys.modules["requests"] = REQUESTS
spec = importlib.util.spec_from_file_location("video_frame_feeder_response_schema", SCRIPT)
assert spec and spec.loader
FEEDER = importlib.util.module_from_spec(spec)
spec.loader.exec_module(FEEDER)


class BridgeResponseSchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        REQUESTS.calls.clear()
        REQUESTS.response = FakeResponse()
        FEEDER._running = True

    def tearDown(self) -> None:
        FEEDER._running = True

    def test_literal_true_is_the_only_success_value(self) -> None:
        REQUESTS.response.json_result = {"accepted": True, "extra": SENTINEL}

        result = FEEDER.post_frame("http://127.0.0.1:18943/frame", b"jpeg")

        self.assertEqual(result, {"accepted": True})
        self.assertNotIn(SENTINEL, repr(result))

    def test_literal_false_is_normalized_without_raw_reason(self) -> None:
        REQUESTS.response.json_result = {
            "accepted": False,
            "reason": f"upstream rejected body={SENTINEL}",
        }

        result = FEEDER.post_frame("http://127.0.0.1:18943/frame", b"jpeg")

        self.assertEqual(result, {"accepted": False, "reason": "bridge_rejected"})
        self.assertNotIn(SENTINEL, result["reason"])

    def test_missing_accepted_is_rejected(self) -> None:
        REQUESTS.response.json_result = {"reason": SENTINEL}

        result = FEEDER.post_frame("http://127.0.0.1:18943/frame", b"jpeg")

        self.assertEqual(
            result,
            {"accepted": False, "reason": "bridge_response_invalid"},
        )
        self.assertNotIn(SENTINEL, repr(result))

    def test_non_boolean_accepted_values_are_rejected(self) -> None:
        for value in ("true", "false", 1, 0, None, [], {}):
            with self.subTest(value=value):
                REQUESTS.response.json_result = {"accepted": value, "raw": SENTINEL}

                result = FEEDER.post_frame(
                    "http://127.0.0.1:18943/frame",
                    b"jpeg",
                )

                self.assertEqual(
                    result,
                    {"accepted": False, "reason": "bridge_response_invalid"},
                )
                self.assertNotIn(SENTINEL, repr(result))

    def test_non_object_top_level_json_is_rejected(self) -> None:
        for payload in (None, [], [True], "accepted", 1, 0, True, False):
            with self.subTest(payload=payload):
                REQUESTS.response.json_result = payload

                result = FEEDER.post_frame(
                    "http://127.0.0.1:18943/frame",
                    b"jpeg",
                )

                self.assertEqual(
                    result,
                    {"accepted": False, "reason": "bridge_response_invalid"},
                )

    def test_malformed_json_is_normalized_without_body_text(self) -> None:
        REQUESTS.response.json_error = ValueError(
            f"malformed response body containing {SENTINEL}"
        )

        result = FEEDER.post_frame("http://127.0.0.1:18943/frame", b"jpeg")

        self.assertEqual(
            result,
            {"accepted": False, "reason": "bridge_response_invalid_json"},
        )
        self.assertNotIn(SENTINEL, result["reason"])

    def test_request_shape_is_unchanged(self) -> None:
        result = FEEDER.post_frame(
            "http://127.0.0.1:18943/frame",
            b"jpeg",
            force=True,
            source_label="neutral label",
        )

        self.assertEqual(result, {"accepted": True})
        self.assertEqual(len(REQUESTS.calls), 1)
        url, kwargs = REQUESTS.calls[-1]
        self.assertEqual(
            url,
            "http://127.0.0.1:18943/frame?force=true&source=neutral%20label",
        )
        self.assertEqual(kwargs["data"], b"jpeg")
        self.assertEqual(kwargs["headers"], {"Content-Type": "image/jpeg"})
        self.assertEqual(kwargs["timeout"], 5)

    def run_once_with_capture(self, thumbnail, payload) -> tuple[int, str]:
        REQUESTS.response.json_result = payload
        stdout = io.StringIO()
        argv = [str(SCRIPT), "--once", "--source-label", "neutral"]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(FEEDER, "capture_thumbnail", return_value=thumbnail),
            mock.patch.object(FEEDER, "capture_full_frame", return_value=b"jpeg"),
            contextlib.redirect_stdout(stdout),
        ):
            status = FEEDER.main()
        return status, stdout.getvalue()

    def test_filtered_delivery_path_rejects_invalid_response_without_crash(self) -> None:
        status, output = self.run_once_with_capture(bytes(range(64)), [SENTINEL])

        self.assertEqual(status, 0)
        self.assertIn("Bridge rejected", output)
        self.assertIn("bridge_response_invalid", output)
        self.assertNotIn(SENTINEL, output)

    def test_thumbnail_fallback_path_rejects_wrong_type_without_crash(self) -> None:
        status, output = self.run_once_with_capture(
            None,
            {"accepted": "false", "raw": SENTINEL},
        )

        self.assertEqual(status, 0)
        self.assertIn("Thumbnail failed", output)
        self.assertIn("Bridge rejected", output)
        self.assertIn("bridge_response_invalid", output)
        self.assertNotIn(SENTINEL, output)

    def test_continuous_mode_survives_normalized_rejection(self) -> None:
        calls = []

        def fake_post_frame(*args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                return {"accepted": False, "reason": "bridge_response_invalid"}
            FEEDER._running = False
            return {"accepted": True}

        stdout = io.StringIO()
        argv = [str(SCRIPT), "--no-content-filter", "--source-label", "neutral"]
        with (
            mock.patch.object(sys, "argv", argv),
            mock.patch.object(FEEDER, "capture_thumbnail", return_value=bytes(range(64))),
            mock.patch.object(FEEDER, "capture_full_frame", return_value=b"jpeg"),
            mock.patch.object(FEEDER, "post_frame", side_effect=fake_post_frame),
            mock.patch.object(FEEDER.time, "sleep", return_value=None),
            contextlib.redirect_stdout(stdout),
        ):
            status = FEEDER.main()

        self.assertEqual(status, 0)
        self.assertEqual(len(calls), 2)
        output = stdout.getvalue()
        self.assertIn("Bridge rejected", output)
        self.assertIn("Sent 4B", output)
        self.assertIn("'sent': 1", output)


if __name__ == "__main__":
    unittest.main()
