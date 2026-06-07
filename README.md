# video-frame-feeder

Cross-platform FFmpeg screen capture → POST to Gemini Live voice bridge /frame endpoint.

## Quick start

```bash
# Default: 1fps capture, content-aware filtering (identical frames skipped)
python video-frame-feeder.py

# Label the source for webhook announces
python video-frame-feeder.py --source-label "my-screen"

# Disable content filter (send every frame, like v0.1)
python video-frame-feeder.py --no-content-filter

# Single frame test
python video-frame-feeder.py --once
```

## Content-aware filtering (v0.2)

The feeder pre-captures an 8×8 grayscale thumbnail, computes a 64-bit average hash
(aHash), and skips the full JPEG capture when:

- The thumbnail has zero variance (uniform/solid color) — `--stddev-min 0` (disabled by default)
- The hash hasn't changed enough — `--min-change 2` (Hamming distance, default)

If the thumbnail pipe fails (ffmpeg version / filter incompatibility), the feeder
falls back to unfiltered full-frame capture + POST — no silent blackout.

```
--min-change N        Hamming distance threshold (0-64). Default 2.
--stddev-min F        Min pixel stddev (0-255). Default 0 (disabled).
--no-content-filter   Disable all filtering (v0.1 behavior).
--source-label TEXT   Label passed to bridge for webhook announce.
```

## Bridge endpoint

Default: `http://127.0.0.1:18943/frame` (env: `VOICE_BRIDGE_FRAME_URL`).
