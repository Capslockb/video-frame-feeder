from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "video-frame-feeder.py"


class RequestException(Exception):
    pass


class FakeResponse:
    def __init__(self, status_code: int, location: str = "") -> None:
        self.status_code = status_code
        self.headers = {"Location": location} if location else {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RequestException(f"status={self.status_code}")

    def json(self) -> dict:
        return {"accepted": True}


class RequestsStub(types.ModuleType):
    RequestException = RequestException

    def __init__(self) -> None:
        super().__init__("requests")
        self.calls = []
        self.status_code = 200
        self.location = ""

    def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.status_code, self.location)


REQUESTS = RequestsStub()
sys.modules["requests"] = REQUESTS
spec = importlib.util.spec_from_file_location("video_frame_feeder", SCRIPT)
assert spec and spec.loader
FEEDER = importlib.util.module_from_spec(spec)
spec.loader.exec_module(FEEDER)


class RedirectHandlingTests(unittest.TestCase):
    def setUp(self) -> None:
        REQUESTS.calls.clear()
        REQUESTS.status_code = 200
        REQUESTS.location = ""

    def test_redirects_are_disabled_on_frame_posts(self) -> None:
        FEEDER.post_frame("https://bridge.example/frame", b"jpeg")

        self.assertEqual(len(REQUESTS.calls), 1)
        _, kwargs = REQUESTS.calls[-1]
        self.assertIs(kwargs["allow_redirects"], False)
        self.assertEqual(kwargs["data"], b"jpeg")
        self.assertEqual(kwargs["headers"], {"Content-Type": "image/jpeg"})
        self.assertEqual(kwargs["timeout"], 5)

    def test_all_redirect_statuses_are_rejected_without_second_request(self) -> None:
        for status in (301, 302, 303, 307, 308):
            with self.subTest(status=status):
                REQUESTS.calls.clear()
                REQUESTS.status_code = status
                REQUESTS.location = "https://other.example/private?token=do-not-log"

                result = FEEDER.post_frame(
                    "https://bridge.example/frame", b"jpeg"
                )

                self.assertEqual(
                    result,
                    {"accepted": False, "reason": f"http_redirect:{status}"},
                )
                self.assertEqual(len(REQUESTS.calls), 1)
                self.assertNotIn("other.example", result["reason"])
                self.assertNotIn("do-not-log", result["reason"])

    def test_same_host_redirect_is_also_rejected(self) -> None:
        REQUESTS.status_code = 307
        REQUESTS.location = "https://bridge.example/other-frame"

        result = FEEDER.post_frame("https://bridge.example/frame", b"jpeg")

        self.assertEqual(result, {"accepted": False, "reason": "http_redirect:307"})
        self.assertEqual(len(REQUESTS.calls), 1)

    def test_successful_response_contract_is_unchanged(self) -> None:
        result = FEEDER.post_frame("https://bridge.example/frame", b"jpeg")

        self.assertEqual(result, {"accepted": True})
        self.assertEqual(len(REQUESTS.calls), 1)


if __name__ == "__main__":
    unittest.main()
