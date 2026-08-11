from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import sys
import types
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "video-frame-feeder.py"


requests_stub = types.ModuleType("requests")


class RequestException(Exception):
    pass


requests_stub.RequestException = RequestException
_previous_requests = sys.modules.get("requests")
sys.modules["requests"] = requests_stub
try:
    spec = importlib.util.spec_from_file_location("video_frame_feeder_source_label_test", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load video-frame-feeder.py")
    feeder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(feeder)
finally:
    if _previous_requests is None:
        sys.modules.pop("requests", None)
    else:
        sys.modules["requests"] = _previous_requests


class SourceLabelPrivacyTests(unittest.TestCase):
    def test_capture_source_is_not_used_as_implicit_network_label(self) -> None:
        capture_source = "Confidential Client - Contract.docx"
        posted: dict[str, object] = {}

        def fake_post_frame(
            endpoint: str,
            data: bytes,
            force: bool = False,
            source_label: str = "",
        ) -> dict:
            posted.update(
                endpoint=endpoint,
                data=data,
                force=force,
                source_label=source_label,
            )
            return {"accepted": True}

        stdout = io.StringIO()
        with (
            mock.patch.object(
                sys,
                "argv",
                [str(SCRIPT), "--source", capture_source, "--once"],
            ),
            mock.patch.object(feeder, "_running", True),
            mock.patch.object(feeder, "capture_thumbnail", return_value=bytes(range(64))),
            mock.patch.object(feeder, "capture_full_frame", return_value=b"jpeg"),
            mock.patch.object(feeder, "post_frame", side_effect=fake_post_frame),
            redirect_stdout(stdout),
        ):
            result = feeder.main()

        self.assertEqual(result, 0)
        self.assertEqual(posted["source_label"], "")
        self.assertIn("Source label for webhook: (none)", stdout.getvalue())
        self.assertNotIn(
            f"Source label for webhook: {capture_source}",
            stdout.getvalue(),
        )

    def test_explicit_source_label_is_preserved_and_encoded_once(self) -> None:
        label = "desk / review Δ 50%"
        response = mock.Mock()
        response.raise_for_status.return_value = None
        response.json.return_value = {"accepted": True}

        with mock.patch.object(
            feeder.requests,
            "post",
            create=True,
            return_value=response,
        ) as post:
            result = feeder.post_frame(
                "http://127.0.0.1:18943/frame",
                b"jpeg",
                source_label=label,
            )

        self.assertEqual(result, {"accepted": True})
        request_url = post.call_args.args[0]
        query = parse_qs(urlsplit(request_url).query, keep_blank_values=True)
        self.assertEqual(query.get("source"), [label])
        self.assertEqual(len(query), 1)

    def test_source_label_help_describes_opt_in_behavior(self) -> None:
        stdout = io.StringIO()
        with (
            mock.patch.object(sys, "argv", [str(SCRIPT), "--help"]),
            redirect_stdout(stdout),
            self.assertRaises(SystemExit) as raised,
        ):
            feeder.main()

        self.assertEqual(raised.exception.code, 0)
        help_text = stdout.getvalue()
        self.assertIn("--source-label SOURCE_LABEL", help_text)
        self.assertIn("omitted by default", help_text)
        self.assertNotIn("default: the value of --source", help_text)


if __name__ == "__main__":
    unittest.main()
