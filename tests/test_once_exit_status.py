from __future__ import annotations

import importlib.util
import io
import sys
import types
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "video-frame-feeder.py"


def load_feeder_module():
    module_name = f"video_frame_feeder_once_{uuid.uuid4().hex}"
    requests_stub = types.ModuleType("requests")

    class RequestException(Exception):
        pass

    requests_stub.RequestException = RequestException
    requests_stub.post = mock.Mock(side_effect=AssertionError("network access is forbidden in unit tests"))

    previous_requests = sys.modules.get("requests")
    sys.modules["requests"] = requests_stub
    try:
        spec = importlib.util.spec_from_file_location(module_name, SCRIPT)
        if spec is None or spec.loader is None:
            raise RuntimeError("unable to load video-frame-feeder.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        if previous_requests is None:
            sys.modules.pop("requests", None)
        else:
            sys.modules["requests"] = previous_requests


class OnceExitStatusTests(unittest.TestCase):
    def run_once(
        self,
        *,
        thumbnail: bytes | None = bytes(range(64)),
        frame: bytes | None = b"jpeg",
        delivery: dict | None = None,
        extra_args: tuple[str, ...] = (),
    ):
        module = load_feeder_module()
        delivery_result = delivery if delivery is not None else {"accepted": True}

        with (
            mock.patch.object(sys, "argv", [str(SCRIPT), "--once", *extra_args]),
            mock.patch.object(module, "get_ffmpeg_cmd", return_value=["ffmpeg", "full"]),
            mock.patch.object(module, "get_thumb_cmd", return_value=["ffmpeg", "thumb"]),
            mock.patch.object(module, "capture_thumbnail", return_value=thumbnail),
            mock.patch.object(module, "capture_full_frame", return_value=frame) as capture_full,
            mock.patch.object(module, "post_frame", return_value=delivery_result) as post_frame,
            redirect_stdout(io.StringIO()),
        ):
            return module.main(), capture_full, post_frame

    def test_accepted_delivery_exits_zero(self) -> None:
        exit_code, _, _ = self.run_once(delivery={"accepted": True})
        self.assertEqual(exit_code, 0)

    def test_rejected_or_failed_delivery_exits_nonzero(self) -> None:
        for reason in ("network_failure", "http_rejection", "bridge_rejected"):
            with self.subTest(reason=reason):
                exit_code, _, _ = self.run_once(
                    delivery={"accepted": False, "reason": reason}
                )
                self.assertEqual(exit_code, 1)

    def test_thumbnail_fallback_rejection_exits_nonzero(self) -> None:
        exit_code, _, _ = self.run_once(
            thumbnail=None,
            delivery={"accepted": False, "reason": "bridge_rejected"},
        )
        self.assertEqual(exit_code, 1)

    def test_capture_failure_exits_nonzero(self) -> None:
        exit_code, _, post_frame = self.run_once(frame=None)
        self.assertEqual(exit_code, 1)
        post_frame.assert_not_called()

    def test_intentional_content_filter_skip_exits_zero(self) -> None:
        exit_code, capture_full, post_frame = self.run_once(
            thumbnail=bytes(64),
            extra_args=("--stddev-min", "1"),
        )
        self.assertEqual(exit_code, 0)
        capture_full.assert_not_called()
        post_frame.assert_not_called()


if __name__ == "__main__":
    unittest.main()
