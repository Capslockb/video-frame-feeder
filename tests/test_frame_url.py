from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "video-frame-feeder.py"


class RequestException(Exception):
    pass


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"accepted": True}


class RequestsStub(types.ModuleType):
    RequestException = RequestException

    def __init__(self) -> None:
        super().__init__("requests")
        self.calls = []

    def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse()


REQUESTS = RequestsStub()
sys.modules["requests"] = REQUESTS
spec = importlib.util.spec_from_file_location("video_frame_feeder", SCRIPT)
assert spec and spec.loader
FEEDER = importlib.util.module_from_spec(spec)
spec.loader.exec_module(FEEDER)


class FrameUrlTests(unittest.TestCase):
    def setUp(self) -> None:
        REQUESTS.calls.clear()

    def test_force_adds_parameter_without_existing_query(self) -> None:
        result = FEEDER.post_frame(
            "http://127.0.0.1:18943/frame", b"jpeg", force=True
        )

        self.assertEqual(result, {"accepted": True})
        url, kwargs = REQUESTS.calls[-1]
        parts = urlsplit(url)
        self.assertEqual(parts.path, "/frame")
        self.assertEqual(
            parse_qsl(parts.query, keep_blank_values=True),
            [("force", "true")],
        )
        self.assertEqual(kwargs["data"], b"jpeg")
        self.assertEqual(kwargs["headers"], {"Content-Type": "image/jpeg"})
        self.assertEqual(kwargs["timeout"], 5)

    def test_existing_query_duplicates_blanks_source_and_fragment_survive(self) -> None:
        label = "Desk / α?x=1&y=2"
        FEEDER.post_frame(
            "https://bridge.example/frame?route=primary&blank=&route=secondary#keep-me",
            b"jpeg",
            force=True,
            source_label=label,
        )

        url, _ = REQUESTS.calls[-1]
        parts = urlsplit(url)
        self.assertEqual(parts.fragment, "keep-me")
        self.assertEqual(
            parse_qsl(parts.query, keep_blank_values=True),
            [
                ("route", "primary"),
                ("blank", ""),
                ("route", "secondary"),
                ("force", "true"),
                ("source", label),
            ],
        )

    def test_force_disabled_does_not_add_force(self) -> None:
        FEEDER.post_frame(
            "https://bridge.example/frame?route=primary",
            b"jpeg",
            source_label="screen share",
        )

        url, _ = REQUESTS.calls[-1]
        self.assertEqual(
            parse_qsl(urlsplit(url).query, keep_blank_values=True),
            [("route", "primary"), ("source", "screen share")],
        )

    def test_source_label_is_encoded_once(self) -> None:
        label = "team%2Fdesk + café"
        FEEDER.post_frame(
            "https://bridge.example/frame?route=a%2Fb",
            b"jpeg",
            source_label=label,
        )

        url, _ = REQUESTS.calls[-1]
        pairs = parse_qsl(urlsplit(url).query, keep_blank_values=True)
        self.assertEqual(pairs, [("route", "a/b"), ("source", label)])
        self.assertEqual(sum(1 for key, _ in pairs if key == "source"), 1)


if __name__ == "__main__":
    unittest.main()
