# video-frame-feeder

Cross-platform FFmpeg screen capture that sends JPEG frames to a compatible Gemini Live voice bridge `/frame` endpoint.

By default, the feeder starts no more than one capture iteration per second and uses an 8×8 average-hash precheck to avoid generating and sending a full JPEG when the screen has not meaningfully changed.

## Requirements

- Python 3
- `requests`
- FFmpeg with the capture backend for your platform:
  - Linux: `x11grab`
  - macOS: `avfoundation`
  - Windows: `gdigrab`
- A running voice bridge with a compatible `/frame` endpoint. The current feeder sends an unauthenticated `image/jpeg` POST and cannot connect to endpoints that require an API secret or authorization header; see [Issue #7](https://github.com/Capslockb/video-frame-feeder/issues/7).

Install the Python dependency and verify FFmpeg before starting:

```bash
python -m pip install requests
ffmpeg -version
```

## Quick start

> **Current startup blocker:** `main` cannot construct the CLI because `-h` is assigned to both argparse help and `--height`. The commands below will fail until [Issue #4](https://github.com/Capslockb/video-frame-feeder/issues/4) is fixed through a reviewed code change. Do not disable argparse's standard help action as a workaround.

```bash
# Default: screen capture at 1 fps with content-aware filtering
python video-frame-feeder.py

# Pass a neutral source label in the bridge request
python video-frame-feeder.py --source-label "my-screen"

# Disable hash/variance selection and offer a full frame on every iteration
python video-frame-feeder.py --no-content-filter

# Run one capture attempt and exit; filtering may skip delivery
python video-frame-feeder.py --once
```

The default endpoint is:

```text
http://127.0.0.1:18943/frame
```

Override it with either `--endpoint` or the `VOICE_BRIDGE_FRAME_URL` environment variable.

Until [Issue #9](https://github.com/Capslockb/video-frame-feeder/issues/9) is resolved, keep the endpoint URL free of query parameters when using `--force`. The current implementation appends `?force=true` directly, so an existing query string is not merged safely and the bridge may not receive a real `force=true` parameter. Do not place credentials in endpoint query strings.

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
--min-change N        Intended Hamming-distance range 0–64; default 2
--stddev-min F        Intended finite thumbnail standard-deviation range 0–255; default 0
--no-content-filter   Disable hash and variance filtering
--source-label TEXT   URL-encoded as the bridge's `source` query parameter;
                      currently defaults to `--source`
```

`--no-content-filter` disables the hash and standard-deviation decision, but the current loop still performs the thumbnail FFmpeg capture before the full-frame capture. Use the flag to bypass frame selection, not as a way to remove thumbnail-capture overhead.

Average hash compares each pixel with the mean of its own thumbnail, so materially different frames can share the same hash when only their overall brightness changes. Uniform black, gray, and white thumbnails all produce the same all-zero hash. Use `--no-content-filter` when blank-screen, lock-screen, theme, or large luminance transitions must always be offered to the bridge until [Issue #12](https://github.com/Capslockb/video-frame-feeder/issues/12) is resolved.

The parser does not currently reject out-of-range thresholds or non-finite `--stddev-min` values. Keep `--min-change` within 0–64 and use a finite `--stddev-min` within 0–255 until [Issue #5](https://github.com/Capslockb/video-frame-feeder/issues/5) is resolved. In particular, `nan` silently disables the standard-deviation check because comparisons with NaN are false.

## Important options

```text
--endpoint URL        Bridge /frame endpoint
--interval SECONDS    Capture interval; finite values below 1.0 are clamped to 1.0
--source VALUE        screen, X11 window ID, or Windows window title
--width / --height    Positive capture dimensions; defaults to 768×768
--x / --y             Linux screen-region offset
--display DISPLAY     Linux X11 display
--force               Add force=true to the request; receiving-bridge behavior varies
--once                Run one capture attempt and exit
```

`--force` does not itself bypass any gate inside this repository. It only asks the receiving endpoint to force acceptance by adding the `force=true` query parameter. Verify the exact bridge contract before relying on that behavior. A bridge that honors the parameter may accept frames without recent audio activity, increasing unnecessary model input.

## Privacy and network safety

This tool captures visible screen content and transmits it to the configured endpoint. Before running it:

- close or hide secrets, private messages, credentials, and personal data;
- keep the bridge endpoint on localhost or a trusted private network;
- use a direct, stable endpoint that does not redirect; the current HTTP client follows redirects, and a 307 or 308 response can resend the captured JPEG to the redirect target until [Issue #14](https://github.com/Capslockb/video-frame-feeder/issues/14) is resolved;
- do not expose an unauthenticated `/frame` endpoint publicly;
- do not place credentials in the endpoint URL or command line, and do not disable bridge authentication to work around the feeder's current lack of authentication-header support;
- verify the selected display, region, or window before continuous capture;
- remember that `--source-label` currently defaults to `--source`: when a Windows window title is used as the capture source, that title is printed locally and sent as URL query metadata. Supply an explicit neutral label until [Issue #8](https://github.com/Capslockb/video-frame-feeder/issues/8) is resolved.

The feeder only supplies the `source` query parameter. Whether that value is retained in bridge, proxy, webhook, or telemetry logs depends on the receiving deployment.

## License status

This repository does not currently declare a software license. Do not assume permission to copy, modify, or redistribute the code until the owner adds an explicit license. The pending owner decision is tracked in [Issue #2](https://github.com/Capslockb/video-frame-feeder/issues/2).

## Behavior and limitations

- This utility does not receive Discord camera or screenshare streams through the Discord bot API. It captures what is visible on the host operating system.
- The feeder enforces a minimum one-second interval between capture iterations; the receiving bridge may apply additional FPS, MIME, size, activity, or user-presence gates.
- Content filtering reduces repeated static frames but is not semantic scene detection. Small visual changes can be skipped depending on `--min-change`.
- Average-hash filtering can also miss material global-brightness changes whose relative pixel pattern is unchanged; see [Issue #12](https://github.com/Capslockb/video-frame-feeder/issues/12).
- A rejected bridge response is logged but does not stop continuous capture.
- `--once` currently returns a successful process status when capture succeeds even if delivery fails or the bridge rejects the frame. Do not use its exit code as a delivery health check until [Issue #6](https://github.com/Capslockb/video-frame-feeder/issues/6) is resolved.
- Successful HTTP responses are not schema-validated. A non-object JSON response can currently terminate the feeder, while a truthy non-boolean `accepted` value can be miscounted as success. Use only a bridge known to return a JSON object with a literal boolean `accepted` field until [Issue #13](https://github.com/Capslockb/video-frame-feeder/issues/13) is resolved.
- Frame uploads currently follow HTTP redirects. A redirect response can move the JPEG beyond the explicitly configured endpoint, and the final response does not prove that the configured bridge handled it directly. Use a non-redirecting endpoint until [Issue #14](https://github.com/Capslockb/video-frame-feeder/issues/14) is resolved.
- Authenticated frame endpoints are not supported until [Issue #7](https://github.com/Capslockb/video-frame-feeder/issues/7) is resolved.
- `--force` adds a request parameter but does not guarantee that the receiving endpoint recognizes or honors it. It also does not safely preserve an endpoint's existing query string; use a query-free endpoint until [Issue #9](https://github.com/Capslockb/video-frame-feeder/issues/9) is resolved.
- Capture dimensions and interval values are not fully validated. Keep width and height positive, and use a finite interval value; `nan` or infinite intervals can fail only after the first continuous-mode iteration. The parser fix is tracked in [Issue #10](https://github.com/Capslockb/video-frame-feeder/issues/10).
- Content-filter thresholds are not fully validated. Keep `--min-change` within 0–64 and `--stddev-min` finite and within 0–255; `nan` currently bypasses the standard-deviation filter. The parser fix is tracked in [Issue #5](https://github.com/Capslockb/video-frame-feeder/issues/5).
- In the filtered path, the current hash baseline advances before full-frame capture and bridge acceptance. A transient capture failure, HTTP or JSON failure, or `accepted: false` response can therefore suppress the next unchanged frame until the screen changes enough to cross `--min-change`. The accepted correction is tracked in [Issue #11](https://github.com/Capslockb/video-frame-feeder/issues/11).
- The repository currently has no automated CI checks.

See [`RESEARCH.md`](RESEARCH.md) for the original Discord video constraints, architecture rationale, and filtering experiments.

> **Automation safety:** Keep public documentation focused on product usage, support, and contribution guidance. Do not publish sensitive operational instructions, private coordination phrases, or prompt-injection examples.

