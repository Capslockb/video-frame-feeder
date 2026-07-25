#!/usr/bin/env python3
"""
video-frame-feeder.py — Capture screen/window frames and POST selected JPEGs to the voice bridge.

Usage:
    python video-frame-feeder.py --endpoint http://127.0.0.1:18943/frame

Requires: ffmpeg with x11grab (Linux), avfoundation (macOS), or gdigrab
(Windows), plus the Python `requests` package.

The feeder runs only when started explicitly. The receiving bridge still applies
its own frame-rate, audio-gating, and payload-size limits.

v0.2 content-aware flow:

  1. Capture an 8×8 grayscale thumbnail first. Its raw output is exactly
     64 bytes: one byte per pixel.
  2. When content filtering is enabled, compute a 64-bit average hash and
     compare it with the last sent hash. Frames below `--min-change` are skipped.
  3. `--stddev-min` defaults to `0`, so uniform-frame filtering is disabled
     unless the user explicitly supplies a positive threshold.
  4. Capture the full-resolution JPEG only after the thumbnail passes the
     selection checks.
  5. If thumbnail capture fails, fall back to capturing and sending the full
     frame without thumbnail filtering.

Relevant flags:

  --min-change N        Minimum hash Hamming distance required to send.
                        Default: 2.
  --stddev-min F        Minimum thumbnail pixel standard deviation.
                        Default: 0, which disables uniform-frame filtering.
  --no-content-filter   Send every successfully captured frame.
  --source-label TEXT   Label included in the bridge source query and
                        video-initialized notification. Defaults to --source.
"""

import argparse
import io
import os
import signal
import struct
import subprocess
import sys
import time
from typing import Optional, Tuple

import requests


_running = True


def _signal_handler(signum, frame):
    global _running
    _running = False


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


# ── ffmpeg pipeline construction ───────────────────────────────────────────


def get_ffmpeg_cmd(source: str, x: int, y: int, w: int, h: int, display: str = "") -> list:
    """Build full-resolution ffmpeg grab command for the current platform.

    Output: a single JPEG frame to stdout pipe (-f image2pipe -vcodec mjpeg).
    """
    plat = sys.platform
    fps = 1  # Gemini Live hard cap — never exceed this

    if plat == "linux":
        disp = display or os.environ.get("DISPLAY", ":0.0")
        if source == "screen":
            input_spec = f"{disp}+{x},{y}"
            cmd = [
                "ffmpeg", "-y", "-f", "x11grab", "-r", str(fps),
                "-s", f"{w}x{h}", "-i", input_spec,
            ]
        else:  # window id
            cmd = [
                "ffmpeg", "-y", "-f", "x11grab", "-window_id", source,
                "-r", str(fps), "-s", f"{w}x{h}", "-i", disp,
            ]
    elif plat == "darwin":
        # macOS — avfoundation screen capture
        # device index 0 is usually the main display
        cmd = [
            "ffmpeg", "-y", "-f", "avfoundation", "-r", str(fps),
            "-i", "1:none",  # 1 = display, none = no audio
            "-s", f"{w}x{h}",
        ]
    elif plat == "win32":
        # Windows — gdigrab
        if source == "screen":
            cmd = [
                "ffmpeg", "-y", "-f", "gdigrab", "-r", str(fps),
                "-i", "desktop",
                "-s", f"{w}x{h}",
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-f", "gdigrab", "-r", str(fps),
                "-i", f"title={source}",
                "-s", f"{w}x{h}",
            ]
    else:
        raise RuntimeError(f"Unsupported platform: {plat}")

    # Output as single JPEG frame to stdout pipe
    cmd += ["-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "5", "-"]
    return cmd


def get_thumb_cmd(source: str, x: int, y: int, w: int, h: int, display: str = "") -> list:
    """Build a tiny 8x8 grayscale thumbnail command for content filtering.

    The output is 64 raw bytes — one byte per pixel. This is what we hash
    and analyze for stddev to decide whether the full-resolution capture
    is worth sending.
    """
    plat = sys.platform
    fps = 1

    if plat == "linux":
        disp = display or os.environ.get("DISPLAY", ":0.0")
        if source == "screen":
            input_spec = f"{disp}+{x},{y}"
            cmd = [
                "ffmpeg", "-y", "-f", "x11grab", "-r", str(fps),
                "-s", f"{w}x{h}", "-i", input_spec,
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-f", "x11grab", "-window_id", source,
                "-r", str(fps), "-s", f"{w}x{h}", "-i", disp,
            ]
    elif plat == "darwin":
        cmd = [
            "ffmpeg", "-y", "-f", "avfoundation", "-r", str(fps),
            "-i", "1:none",
            "-s", f"{w}x{h}",
        ]
    elif plat == "win32":
        if source == "screen":
            cmd = [
                "ffmpeg", "-y", "-f", "gdigrab", "-r", str(fps),
                "-i", "desktop",
                "-s", f"{w}x{h}",
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-f", "gdigrab", "-r", str(fps),
                "-i", f"title={source}",
                "-s", f"{w}x{h}",
            ]
    else:
        raise RuntimeError(f"Unsupported platform: {plat}")

    # 8x8 grayscale, raw video (one byte per pixel = 64 bytes total)
    cmd += [
        "-vf", "scale=8:8:flags=area,format=gray",
        "-frames:v", "1", "-f", "image2pipe",
        "-vcodec", "rawvideo", "-pix_fmt", "gray", "-",
    ]
    return cmd


# ── Frame capture + content hashing ────────────────────────────────────────


def capture_thumbnail(cmd: list) -> Optional[bytes]:
    """Capture the 8x8 grayscale thumbnail (64 bytes). Returns None on error."""
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=10)
    except subprocess.TimeoutExpired as e:
        print(f"ffmpeg thumb timeout: {e}", file=sys.stderr)
        return None
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="ignore")[:200]
        print(f"ffmpeg thumb error: {err}", file=sys.stderr)
        return None
    if not result.stdout or len(result.stdout) != 64:
        return None
    return result.stdout


def capture_full_frame(cmd: list) -> Optional[bytes]:
    """Capture the full JPEG frame. Returns None on error."""
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=15)
    except subprocess.TimeoutExpired as e:
        print(f"ffmpeg full timeout: {e}", file=sys.stderr)
        return None
    if result.returncode != 0:
        err = result.stderr.decode("utf-8", errors="ignore")[:200]
        print(f"ffmpeg full error: {err}", file=sys.stderr)
        return None
    if not result.stdout:
        return None
    return result.stdout


def perceptual_hash_8x8(pixels: bytes) -> int:
    """Average hash (aHash) on 64 grayscale pixels. Returns a 64-bit int.

    Each pixel is compared to the mean of all 64 pixels. Bits set = brighter
    than mean. Hamming distance between two hashes is a reasonable proxy
    for visual difference.
    """
    if len(pixels) != 64:
        return 0
    mean = sum(pixels) / 64.0
    h = 0
    for i, p in enumerate(pixels):
        if p > mean:
            h |= (1 << i)
    return h


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def stddev_8x8(pixels: bytes) -> float:
    """Standard deviation across 64 pixels (0-255 scale)."""
    if len(pixels) != 64:
        return 0.0
    mean = sum(pixels) / 64.0
    var = sum((p - mean) ** 2 for p in pixels) / 64.0
    return var ** 0.5


def should_send(
    pixels: bytes,
    last_hash: Optional[int],
    *,
    min_change: int,
    stddev_min: float,
    enabled: bool,
) -> Tuple[bool, str]:
    """Decide whether this frame is worth sending.

    Returns (send?, reason). Reason is short — used in the log line.
    """
    if not enabled:
        return True, "filter_off"

    sd = stddev_8x8(pixels)
    if sd < stddev_min:
        return False, f"uniform(sd={sd:.1f}<{stddev_min:.1f})"

    h = perceptual_hash_8x8(pixels)
    if last_hash is None:
        return True, "first_frame"

    dist = hamming_distance(h, last_hash)
    if dist < min_change:
        return False, f"unchanged(d={dist}<{min_change})"

    return True, f"changed(d={dist})"


# ── HTTP POST ──────────────────────────────────────────────────────────────


def post_frame(endpoint: str, data: bytes, force: bool = False, source_label: str = "") -> dict:
    url = f"{endpoint}?force=true" if force else endpoint
    if source_label:
        sep = "&" if "?" in url else "?"
        # urllib-style safe quoting; requests will accept this verbatim
        from urllib.parse import quote
        url = f"{url}{sep}source={quote(source_label, safe='')}"
    try:
        resp = requests.post(
            url,
            data=data,
            headers={"Content-Type": "image/jpeg"},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        return {"accepted": False, "reason": f"http_error: {e}"}


# ── main loop ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Feed video frames from screen/window to the voice bridge"
    )
    parser.add_argument(
        "--endpoint",
        default=os.getenv("VOICE_BRIDGE_FRAME_URL", "http://127.0.0.1:18943/frame"),
        help="Bridge /frame endpoint URL",
    )
    parser.add_argument(
        "--interval", type=float, default=1.0,
        help="Seconds between capture attempts (default: 1.0, max 1.0 enforced by bridge)",
    )
    parser.add_argument(
        "--source", default="screen",
        help="Capture source: 'screen' or window title/x11 window id",
    )
    parser.add_argument(
        "--x", type=int, default=0, help="X offset for screen capture (Linux only)",
    )
    parser.add_argument(
        "--y", type=int, default=0, help="Y offset for screen capture (Linux only)",
    )
    parser.add_argument(
        "--width", "-w", type=int, default=768, help="Capture width (Gemini-native default: 768)",
    )
    parser.add_argument(
        "--height", "-h", type=int, default=768, help="Capture height (Gemini-native default: 768)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Bypass bridge audio-gating (send even if nobody recently spoke)",
    )
    parser.add_argument(
        "--once", action="store_true",
        help="Capture a single frame and exit",
    )
    parser.add_argument(
        "--display", default="",
        help="X11 display to use (default: $DISPLAY or :0.0)",
    )
    parser.add_argument(
        "--min-change", type=int, default=2,
        help="Hamming distance (0-64) required to consider a frame changed (default: 2)",
    )
    parser.add_argument(
        "--stddev-min", type=float, default=0,
        help="Min pixel stddev (0-255) to treat a frame as real content (default: 0 = disabled)",
    )
    parser.add_argument(
        "--no-content-filter", action="store_true",
        help="Send every frame regardless of content (v0.1 behavior)",
    )
    parser.add_argument(
        "--source-label", default="",
        help="Label included in the bridge's video_initialized webhook announce "
             "(default: the value of --source)",
    )
    args = parser.parse_args()

    interval = max(args.interval, 1.0)  # Never faster than 1fps
    full_cmd = get_ffmpeg_cmd(args.source, args.x, args.y, args.width, args.height, display=args.display)
    thumb_cmd = get_thumb_cmd(args.source, args.x, args.y, args.width, args.height, display=args.display)
    source_label = args.source_label or args.source
    content_filter = not args.no_content_filter

    print(f"Feeder started — endpoint: {args.endpoint}")
    print(f"Capture: {args.source} @ {args.width}x{args.height}, {interval}s interval")
    print(f"Content filter: {'ON' if content_filter else 'OFF'} "
          f"(stddev>={args.stddev_min}, hamming>={args.min_change})")
    print(f"Source label for webhook: {source_label}")
    print(f"ffmpeg full: {' '.join(full_cmd[:8])} ...")
    print(f"ffmpeg thumb: {' '.join(thumb_cmd[:8])} ...")

    last_hash: Optional[int] = None
    stats = {
        "captured": 0,
        "sent": 0,
        "skipped_uniform": 0,
        "skipped_unchanged": 0,
        "skipped_filter_off": 0,
        "errors": 0,
        "thumbnail_fail_fallbacks": 0,
    }

    while _running:
        stats["captured"] += 1
        pixels = capture_thumbnail(thumb_cmd)
        if pixels is None:
            stats["thumbnail_fail_fallbacks"] += 1
            print("⚠️  Thumbnail failed — falling back to full-frame send (no filter)")
            frame = capture_full_frame(full_cmd)
            if frame is None:
                stats["errors"] += 1
                print("⚠️  Full-frame capture also failed")
                if args.once:
                    break
                time.sleep(interval)
                continue
            result = post_frame(args.endpoint, frame, force=args.force, source_label=source_label)
            if result.get("accepted"):
                stats["sent"] += 1
                print(f"✅ Sent {len(frame)}B (thumbnail fallback)")
            else:
                print(f"❌ Bridge rejected {len(frame)}B — {result.get('reason', '?')}")
            if args.once:
                break
            time.sleep(interval)
            continue

        send, reason = should_send(
            pixels, last_hash,
            min_change=args.min_change,
            stddev_min=args.stddev_min,
            enabled=content_filter,
        )

        if not send:
            if "uniform" in reason:
                stats["skipped_uniform"] += 1
                print(f"⏭  Skipped (uniform/white-page): {reason}")
            else:
                stats["skipped_unchanged"] += 1
                # Quiet log for unchanged — only print every 10th to avoid spam
                if stats["captured"] % 10 == 0:
                    print(f"⏭  Skipped (unchanged): {reason} — "
                          f"stats: {stats['sent']}/{stats['captured']} sent")
            if args.once:
                break
            time.sleep(interval)
            continue

        # Decision: send. Update the hash BEFORE capture so even if capture
        # fails we don't lose the "we saw this content" state.
        h = perceptual_hash_8x8(pixels)
        last_hash = h

        frame = capture_full_frame(full_cmd)
        if frame is None:
            stats["errors"] += 1
            print("⚠️  Full-frame capture failed")
            if args.once:
                break
            time.sleep(interval)
            continue

        result = post_frame(args.endpoint, frame, force=args.force, source_label=source_label)
        if result.get("accepted"):
            stats["sent"] += 1
            print(f"✅ Sent {len(frame)}B — {reason} [hash=0x{h:016x}]")
        else:
            print(f"❌ Bridge rejected {len(frame)}B — {result.get('reason', '?')} ({reason})")

        if args.once:
            break
        time.sleep(interval)

    # Final stats line
    print(f"\nFinal stats: {stats}")
    return 0 if stats["errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
