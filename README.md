# video-frame-feeder

Cross-platform FFmpeg screen capture that sends JPEG frames to a compatible Gemini Live voice bridge `/frame` endpoint.

The feeder captures at no more than 1 frame per second and uses an 8×8 average-hash precheck to avoid generating and sending a full JPEG when the screen has not meaningfully changed.

## Requirements

- Python 3
- `requests`
- FFmpeg with the capture backend for your platform:
  - Linux: `x11grab`
  - macOS: `avfoundation`
  - Windows: `gdigrab`
- A running voice bridge with a compatible `/frame` endpoint

Install the Python dependency and verify FFmpeg before starting:

```bash
python -m pip install requests
ffmpeg -version
```

## Quick start

```bash
# Default: screen capture at 1 fps with content-aware filtering
python video-frame-feeder.py

# Label the source in bridge requests and webhook announcements
python video-frame-feeder.py --source-label "my-screen"

# Disable content filtering and send every captured frame
python video-frame-feeder.py --no-content-filter

# Capture one frame, attempt delivery, and exit
python video-frame-feeder.py --once
```

The default endpoint is:

```text
http://127.0.0.1:18943/frame
```

Override it with either `--endpoint` or the `VOICE_BRIDGE_FRAME_URL` environment variable.

## Platform capture notes

### Linux

`--source screen` captures an X11 display. Use `--display`, `--x`, `--y`, `--width`, and `--height` to select a region.

A non-`screen` source is treated as an X11 window ID:

```bash
python video-frame-feeder.py --source 0x04600007 --width 1280 --height 720
```

Wayland sessions may require XWayland or a different capture path supported by the local FFmpeg build.

### macOS

The current implementation uses the FFmpeg `avfoundation` display input `1:none`. The correct display index can vary by machine and should be confirmed with your local FFmpeg device listing. Window-title selection and the Linux-only offset flags are not used on macOS.

### Windows

`--source screen` captures the desktop. Any other `--source` value is treated as a window title for `gdigrab`:

```bash
python video-frame-feeder.py --source "Discord" --width 1280 --height 720
```

## Content-aware filtering

For each capture attempt, the feeder:

1. Captures an 8×8 grayscale thumbnail with the current `scale=8:8:flags=area,format=gray` raw-video pipeline: 64 bytes, one byte per pixel.
2. Computes a 64-bit average hash (`aHash`).
3. Compares it with the last frame selected for delivery.
4. Captures the full JPEG only when the hash changed enough.

A frame is skipped when:

- its thumbnail variance is below `--stddev-min`; this check is disabled by default with `--stddev-min 0`, or
- its hash distance is below `--min-change`; the default is `2`.

If thumbnail capture fails, the feeder falls back to a full-frame capture and delivery attempt instead of silently stopping the feed.

```text
--min-change N        Hamming-distance threshold from 0 to 64; default 2
--stddev-min F        Minimum thumbnail pixel standard deviation; default 0
--no-content-filter   Disable hash and variance filtering
--source-label TEXT   URL-encoded as the bridge's `source` query parameter
```

## Important options

```text
--endpoint URL        Bridge /frame endpoint
--interval SECONDS    Capture interval; values below 1.0 are clamped to 1.0
--source VALUE        screen, X11 window ID, or Windows window title
--width / --height    Capture dimensions; defaults to 768×768
--x / --y             Linux screen-region offset
--display DISPLAY     Linux X11 display
--force               Bypass the bridge's recent-audio gate
--once                Run one capture attempt and exit
```

Use `--force` deliberately: it can cause frames to be accepted even when nobody has recently spoken, increasing unnecessary model input.

## Privacy and network safety

This tool captures visible screen content and transmits it to the configured endpoint. Before running it:

- close or hide secrets, private messages, credentials, and personal data;
- keep the bridge endpoint on localhost or a trusted private network;
- do not expose an unauthenticated `/frame` endpoint publicly;
- verify the selected display, region, or window before continuous capture.

## License status

This repository does not currently declare a software license. Do not assume permission to copy, modify, or redistribute the code until the owner adds an explicit license. The pending owner decision is tracked in [Issue #2](https://github.com/Capslockb/video-frame-feeder/issues/2).

## Behavior and limitations

- This utility does not receive Discord camera or screenshare streams through the Discord bot API. It captures what is visible on the host operating system.
- The feeder enforces a minimum one-second interval; the receiving bridge may apply additional FPS, MIME, size, activity, or user-presence gates.
- Content filtering reduces repeated static frames but is not semantic scene detection. Small visual changes can be skipped depending on `--min-change`.
- A rejected bridge response is logged but does not stop continuous capture.
- The repository currently has no automated CI checks.

See [`RESEARCH.md`](RESEARCH.md) for the original Discord video constraints, architecture rationale, and filtering experiments.
