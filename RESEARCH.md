# Discord Video/Screenshare → Gemini Live — Research Summary

**Original research date:** 2026-05-27  
**Implementation update:** 2026-06-07  
**Documentation status checked:** 2026-07-26

> [!IMPORTANT]
> This document combines a historical research snapshot with the behavior shipped in this repository.
> Model names, API fields, pricing, token estimates, Discord internals, and upstream bridge details can change and must be freshly verified before operational use. The current feeder behavior is defined by `video-frame-feeder.py` and summarized in `README.md`.

## Current operational blockers

The flow described below is implemented in the repository, but current `main` cannot construct its CLI because `-h` is assigned to both argparse help and `--height`. All commands fail before capture begins until [Issue #4](https://github.com/Capslockb/video-frame-feeder/issues/4) is resolved through reviewed executable work.

Additional current boundaries:

- the feeder sends unauthenticated `/frame` requests and cannot connect to endpoints requiring an API secret or authorization header; see [Issue #7](https://github.com/Capslockb/video-frame-feeder/issues/7);
- `--source-label` defaults to `--source`, which can expose a Windows window title in request metadata and logs; use an explicit neutral label while [Issue #8](https://github.com/Capslockb/video-frame-feeder/issues/8) remains open;
- `--once` can exit successfully after HTTP failure or bridge rejection, so its process status is not delivery evidence; see [Issue #6](https://github.com/Capslockb/video-frame-feeder/issues/6);
- `--force` does not safely merge an endpoint's existing query string; use a query-free endpoint while [Issue #9](https://github.com/Capslockb/video-frame-feeder/issues/9) remains open;
- capture dimensions and interval values are not fully validated; keep dimensions positive and the interval finite while [Issue #10](https://github.com/Capslockb/video-frame-feeder/issues/10) remains open;
- filter-threshold ranges are documented but not enforced; see [Issue #5](https://github.com/Capslockb/video-frame-feeder/issues/5);
- the filtered-path hash baseline advances before full-frame capture and bridge acceptance, so a transient failure can suppress an unchanged retry; see accepted [Issue #11](https://github.com/Capslockb/video-frame-feeder/issues/11); and
- average hash can miss material global-brightness changes that preserve relative pixel structure; see accepted [Issue #12](https://github.com/Capslockb/video-frame-feeder/issues/12).

## Current repository status

The shipped feeder is an external host-screen capture utility. It does **not** receive Discord camera or screenshare media through the Discord bot API.

For each capture attempt, it:

1. Captures an 8×8 grayscale thumbnail with the current `scale=8:8:flags=area,format=gray` raw-video pipeline: exactly 64 bytes, one byte per pixel.
2. Computes a 64-bit average hash (`aHash`).
3. Optionally applies a thumbnail standard-deviation threshold.
4. Compares the hash with the last frame selected for delivery.
5. Captures a full JPEG only when the frame should be sent.
6. Posts the JPEG to a compatible bridge `/frame` endpoint.

The default filtering behavior is:

```text
--min-change 2
--stddev-min 0        # variance filtering disabled by default
```

`--no-content-filter` bypasses hash and variance selection and attempts a full-frame capture and delivery on every iteration. It does **not** remove the thumbnail FFmpeg capture step, guarantee bridge acceptance, or disable bridge video. To disable video acceptance, configure the receiving bridge itself, for example with its documented bridge-level video-enable setting.

If thumbnail capture fails, the feeder falls back to full-frame capture and a delivery attempt instead of silently stopping.

## Current integration boundary

### External feeder

`video-frame-feeder.py` captures visible host content and POSTs JPEG frames to:

```text
http://127.0.0.1:18943/frame
```

The endpoint can be overridden with `--endpoint` or `VOICE_BRIDGE_FRAME_URL`.

When `--force` is enabled, the current implementation appends `?force=true` directly. This repository only constructs that request; whether the receiving endpoint recognizes the parameter or changes any gate is outside the feeder's control. An endpoint that already contains query parameters can become malformed. Keep forced endpoints query-free until Issue #9 is fixed, never place credentials in endpoint query strings, and verify the receiving bridge contract before relying on forced acceptance.

The feeder enforces a minimum one-second interval for ordinary finite values. The receiving bridge may apply additional FPS, MIME-type, size, recent-audio, user-presence, or enable/disable gates. Non-finite interval values are not currently rejected and can fail at the continuous-mode sleep boundary.

### Hermes `voice_live_frame` tool

The shipped upstream integration is not a Discord slash command that consumes an attachment. It is a Hermes tool named `voice_live_frame` that:

1. receives an image URL;
2. fetches the image;
3. POSTs it to the bridge control API.

Any older proposal below describing `/voice-live-frame <attach image>` is historical and was not the shipped interface.

### Bridge video enablement versus feeder filtering

These are separate controls:

- **Bridge video enablement** determines whether the bridge accepts video frames at all.
- **Feeder content filtering** determines which captured frames are worth offering to the bridge.
- `--no-content-filter` increases frame delivery attempts; it is not an off switch.

## Confirmed architectural constraint from the original research

The research concluded that standard Discord bot sessions could not directly consume camera or screenshare media from voice channels. Therefore the selected architecture was:

```text
host screen/window capture
        ↓
video-frame-feeder.py
        ↓ JPEG over HTTP
bridge /frame endpoint
        ↓
Gemini Live image input
```

This avoids selfbot/user-token capture paths and keeps capture under explicit operator control.

## Shipped filtering design

### Thumbnail-first analysis

The feeder runs a lightweight thumbnail capture before full JPEG generation:

```text
ffmpeg
  -vf scale=8:8:flags=area,format=gray
  -frames:v 1
  -f rawvideo
```

The output is 64 grayscale pixels, encoded as one raw byte per pixel by the current pipeline.

### Average hash

Each pixel is compared with the thumbnail mean to produce a 64-bit average hash. Hamming distance between consecutive selected hashes is used as a low-cost approximation of visual change.

A frame is skipped when its distance is lower than `--min-change`.

Because the comparison is relative to each thumbnail's own mean, the hash is not luminance-complete. Uniform black, gray, and white thumbnails all produce the same zero hash, and a material brightness shift that preserves relative pixel ordering can preserve the full hash. Until [Issue #12](https://github.com/Capslockb/video-frame-feeder/issues/12) is implemented through reviewed executable work, use `--no-content-filter` when blank-screen, lock-screen, theme, or large luminance transitions must always be offered to the bridge.

### Optional uniform-frame filter

`--stddev-min` rejects thumbnails whose pixel standard deviation is below the configured threshold.

This is disabled by default because an 8×8 thumbnail is too coarse to safely distinguish all legitimate low-variance scenes, such as sparse text, dark interfaces, or mostly white documents.

### Failure behavior

Thumbnail-pipeline failure must not cause a permanent blackout. The implementation logs the failure, captures the full frame, and attempts delivery without content analysis for that iteration.

In the ordinary filtered path, the implementation currently stores a selected hash before full-frame capture and before the bridge reports `accepted: true`. Full-frame capture failure, HTTP or JSON failure, and bridge rejection can therefore leave unchanged content ineligible for the next attempt. Accepted [Issue #11](https://github.com/Capslockb/video-frame-feeder/issues/11) requires the complete selected signature to remain pending until successful bridge acceptance.

## Current CLI reference

```text
--endpoint URL        Bridge /frame endpoint
--interval SECONDS    Finite values below 1.0 are clamped to 1.0;
                      non-finite values are not currently rejected
--source VALUE        screen, X11 window ID, or Windows window title
--width / --height    Positive capture dimensions expected; defaults to 768×768
--x / --y             Linux screen-region offset
--display DISPLAY     Linux X11 display
--force               Add force=true to the request; bridge behavior is endpoint-defined
--once                Run one capture attempt and exit
--min-change N        Hamming-distance threshold, 0–64; default 2
--stddev-min F        Thumbnail pixel standard-deviation threshold; default 0
--no-content-filter   Disable hash and variance filtering
--source-label TEXT   URL-encoded as the bridge's `source` query parameter;
                      currently defaults to `--source`
```

## Historical Gemini Live research snapshot

The following findings were recorded on 2026-05-27 and updated with implementation observations on 2026-06-07. They are retained for design provenance, not as current API or pricing documentation.

### Image-input shape

The research used individual JPEG or PNG image frames sent through the Gemini Live realtime-input channel rather than a raw video codec or WebRTC stream.

The bridge design applied:

- a maximum frame rate of 1 fps;
- a maximum frame size;
- supported-image MIME validation;
- recent-audio gating unless explicitly forced.

These upstream bridge details should be verified against the current bridge repository before deployment.

### `mediaResolution: "LOW"` rejection

The original proposal recommended:

```json
{
  "generationConfig": {
    "mediaResolution": "LOW"
  }
}
```

This recommendation is **superseded**.

On 2026-06-07, the field was reported rejected by the tested Gemini Live websocket endpoint with WS 1007 and an unknown-field error. It was removed from the bridge setup payload.

Do not add `mediaResolution` based on this historical document. Re-check current official API documentation and test the exact target model before using any resolution-control field.

### Turn coverage

The historical implementation record says the bridge already used:

```text
TURN_INCLUDES_ONLY_ACTIVITY
```

This was intended to avoid including silent video-only periods in a turn. Confirm the current field name and semantics against the target API version before relying on it.

### Pricing, model names, and token estimates

The original document referenced preview model names, approximate per-frame token counts, and then-current pricing. Those figures are deliberately not repeated as current guidance here because they are time-sensitive.

For budgeting:

1. check the current official model and pricing pages;
2. measure accepted frames after bridge gating;
3. test the exact model and API version;
4. treat `--force` and `--no-content-filter` as potentially cost-increasing options when the receiving bridge honors them.

## Historical alternatives considered

### Host FFmpeg capture

Selected. Lightweight, explicit, and compatible with the bridge HTTP endpoint.

### Headless browser or user-token Discord capture

Rejected as heavy, fragile, and potentially contrary to Discord terms or account-safety expectations.

### Voice-state notification only

Considered as a way to know that a user started streaming without receiving the actual media. This did not replace host capture and was not implemented in the feeder.

### Manual image submission

The proposal described a Discord command accepting an attachment. The shipped upstream implementation instead became the URL-based Hermes `voice_live_frame` tool described above.

## Historical implementation proposals and outcomes

| Proposal | Outcome | Current interpretation |
|---|---|---|
| External frame feeder | Shipped | Implemented in this repository |
| Content-aware aHash filtering | Shipped | Enabled by default |
| Uniform-frame stddev filtering | Shipped, opt-in | Default threshold is `0` |
| Thumbnail failure fallback | Shipped | Falls through to full capture |
| `mediaResolution: "LOW"` | Rejected and removed | Do not use without fresh verification |
| Turn-coverage override | Reported already present upstream | Verify against current bridge/API |
| Video token-budget counter | Not implemented | Use capture and bridge gating plus measured usage |
| Disable bridge video by default | Not changed in historical upstream status | Configure the bridge, not feeder filtering flags |
| Attachment-based `/voice-live-frame` | Not shipped | Replaced by URL-based Hermes tool |
| Source-label webhook metadata | Shipped | Passes source context through the URL-encoded `source` query parameter |

## Safety conclusions

- Screen capture can expose credentials, messages, personal data, or private windows.
- Keep the `/frame` endpoint on localhost or a trusted private network.
- Do not publicly expose an unauthenticated frame-ingestion endpoint.
- Do not put credentials in endpoint URLs or command-line arguments.
- Supply an explicit neutral `--source-label` when the capture source itself contains sensitive context.
- Keep capture dimensions positive and use a finite interval value until Issue #10 is resolved.
- Verify the selected display, region, or window before continuous capture.
- Use `--force` only when requesting forced acceptance is intentional, keep the endpoint query-free until Issue #9 is resolved, and verify that the receiving bridge recognizes the parameter.
- Use `--no-content-filter` when materially different global-luminance transitions must always be offered, or when sending every captured scene change is otherwise intentional.
- Treat filtered-path delivery failures as potentially suppressing an unchanged retry until Issue #11 is resolved.
- Reverify all external platform, API, model, and pricing claims before production use.

## Source-of-truth hierarchy

When documents disagree, use this order:

1. current `video-frame-feeder.py` behavior;
2. current `README.md` usage documentation;
3. current receiving-bridge source and documentation;
4. this historical research summary.
