from __future__ import annotations

import contextlib
import importlib.util
import io
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "video-frame-feeder.py"
SENTINEL = "secret-route-token"
ENDPOINT = f"https://user:{SENTINEL}@bridge.example/frame?route={SENTINEL}#private"
SOURCE = f"Customer Window {SENTINEL}"


class RequestException(Exception):
    pass


class Timeout(RequestException):
    pass


class ConnectionError(RequestException):
    pass


class HTTPError(RequestException):
    def __init__(self, message: str, response=None) -> None:
        super().__init__(message)
        self.response = response


class FakeResponse:
    def __init__(self) -> None:
        self.status_code = 200
        self.raise_error = None
        self.json_result = {"accepted": True}
        self.json_error = None

    def raise_for_status(self) -> None:
        if self.raise_error is not None:
            raise self.raise_error

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.json_result


class RequestsStub(types.ModuleType):
    RequestException = RequestException
    Timeout = Timeout
    ConnectionError = ConnectionError
    HTTPError = HTTPError

    def __init__(self) -> None:
        super().__init__("requests")
        self.calls = []
        self.post_error = None
        self.response = FakeResponse()

    def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        if self.post_error is not None:
            raise self.post_error
        return self.response


REQUESTS = RequestsStub()
sys.modules["requests"] = REQUESTS
spec = importlib.util.spec_from_file_location("video_frame_feeder_http_redaction", SCRIPT)
assert spec and spec.loader
FEEDER = importlib.util.module_from_spec(spec)
spec.loader.exec_module(FEEDER)


class HttpFailureRedactionTests(unittest.TestCase):
    def setUp(self) -> None:
        REQUESTS.calls.clear()
        REQUESTS.post_error = None
        REQUESTS.response = FakeResponse()

    def assert_bounded_reason(self, result: dict, expected: str) -> None:
        self.assertEqual(result, {"accepted": False, "reason": expected})
        self.assertNotIn(SENTINEL, result["reason"])
        self.assertNotIn("bridge.example", result["reason"])

        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            print(result["reason"])
        output = stdout.getvalue() + stderr.getvalue()
        self.assertNotIn(SENTINEL, output)
        self.assertNotIn("bridge.example", output)

    def test_timeout_is_bounded_and_redacted(self) -> None:
        REQUESTS.post_error = Timeout(f"timed out requesting {ENDPOINT}")

        result = FEEDER.post_frame(ENDPOINT, b"jpeg", source_label=SOURCE)

        self.assert_bounded_reason(result, "http_timeout")

    def test_connection_failure_is_bounded_and_redacted(self) -> None:
        REQUESTS.post_error = ConnectionError(f"failed to connect to {ENDPOINT}")

        result = FEEDER.post_frame(ENDPOINT, b"jpeg", source_label=SOURCE)

        self.assert_bounded_reason(result, "http_connection_error")

    def test_other_transport_failure_is_bounded_and_redacted(self) -> None:
        REQUESTS.post_error = RequestException(f"transport failed for {ENDPOINT}")

        result = FEEDER.post_frame(ENDPOINT, b"jpeg", source_label=SOURCE)

        self.assert_bounded_reason(result, "http_transport_error")

    def test_http_failure_exposes_only_numeric_status(self) -> None:
        REQUESTS.response.status_code = 503
        REQUESTS.response.raise_error = HTTPError(
            f"503 response from {ENDPOINT} body={SENTINEL}",
            response=REQUESTS.response,
        )

        result = FEEDER.post_frame(ENDPOINT, b"jpeg", source_label=SOURCE)

        self.assert_bounded_reason(result, "http_status:503")

    def test_json_decode_failure_is_bounded_and_redacted(self) -> None:
        REQUESTS.response.json_error = ValueError(
            f"invalid body {SENTINEL} returned by {ENDPOINT}"
        )

        result = FEEDER.post_frame(ENDPOINT, b"jpeg", source_label=SOURCE)

        self.assert_bounded_reason(result, "http_response_decode_error")

    def test_request_shape_and_success_contract_are_unchanged(self) -> None:
        result = FEEDER.post_frame(
            "https://bridge.example/frame",
            b"jpeg",
            force=True,
            source_label="neutral label",
        )

        self.assertEqual(result, {"accepted": True})
        self.assertEqual(len(REQUESTS.calls), 1)
        url, kwargs = REQUESTS.calls[-1]
        self.assertEqual(
            url,
            "https://bridge.example/frame?force=true&source=neutral%20label",
        )
        self.assertEqual(kwargs["data"], b"jpeg")
        self.assertEqual(kwargs["headers"], {"Content-Type": "image/jpeg"})
        self.assertEqual(kwargs["timeout"], 5)


if __name__ == "__main__":
    unittest.main()
