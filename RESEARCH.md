# Discord Video/Screenshare → Gemini Live — Research Summary

**Date:** 2026-05-27
**Researcher:** S0RA Agent
**Goal:** Detect and handle Discord video/screenshare in the live voice plugin, conforming to Gemini Live's 1fps target without burning wallet.

---

## Executive Summary

**Discord bots cannot natively receive video streams from voice channels.** This is an intentional Discord platform restriction, not a library limitation. The existing `discord-ext-voice-recv` only supports audio (AudioSink). All video path solutions require workarounds.

**Gemini Live API hard-caps video at 1fps** and charges per image frame tokenized. Wallet-safe operation requires aggressive gating, not just frame-rate limiting.

---

## Part 1: Discord Video Reception Reality

### The Hard "No"
- **Stack Overflow (Jan 2024)**: *"No. Video Streams are not made available to Discord Bots."*
- **Discord-video-stream README**: *"No, Discord blocks video from bots which is why this library uses a selfbot"*
- **aixxe blog (2021)**: *"Discord bots can transmit audio in voice channels, but can't screen share or stream from a camera."*
- **Discord Userdoccers (docs.discord.food)**: Video uses separate WebRTC connections; bots get audio-only.

### What Discord Does Allow
| Feature | Bot Access | Notes |
|---|---|---|
| Audio RX/TX | ✅ Yes | `discord-ext-voice-recv` |
| Video TX (Go Live) | ⚠️ Selfbot only | `discord-video-stream` uses user tokens |
| Video RX (screenshare) | ❌ No | Never exposed to bot API |
| Camera RX | ❌ No | Never exposed to bot API |
| Speaking state | ✅ Yes | `get_speaking()` in voice_recv |
| Stream preview image | ⚠️ Private API | Gist exists but requires selfbot; only thumbnail |

### Why It's Blocked
Discord voice uses WebRTC. Audio flows over the standard voice UDP connection. Video (screenshare/camera) uses a **separate** RTP/WebRTC connection with additional encryption and routing. The Discord Gateway API (Opcode 12 Video payload) is documented but **not implemented for bot tokens** — the server rejects video negotiation from bot sessions.

---

## Part 2: Gemini Live Video Specs (Confirmed from Official Docs)

### Input Format
- **Frames sent as individual images**: JPEG or PNG
- **Hard maximum**: 1 frame per second (exceeding this is rejected or ignored)
- **MIME types**: `image/jpeg`, `image/png`
- **No raw video streams**: No WebRTC, no H.264, no VP8 directly

### How Video Is Tokenized (Cost Driver)
- **Default resolution**: ~258 tokens per frame
- **Low resolution**: ~100 tokens per frame (use `mediaResolution: "LOW"` in setup)
- **Audio**: 32 tokens per second
- **Total video+audio**: ~300 tokens/sec at default, ~140 tokens/sec at low

### Pricing (Gemini 3.1 Flash Live — most relevant model)
| Tier | Input Cost | Notes |
|---|---|---|
| **Free tier** | $0.00 | Generous daily limits; good for testing/light use |
| Paid — Standard | $0.25 / 1M tokens | Text/image/video input |
| Paid — Flex | $0.125 / 1M tokens | Deferred/batched |

### Cost Math (Worst Case, 1hr session)
```
1 fps × 258 tokens/frame × 3600s = 928,800 video tokens
Audio: 32 tokens/sec × 3600s = 115,200 tokens
Total: ~1,044,000 tokens/hour

Paid cost: ~$0.26/hour (Standard) or ~$0.13/hour (Flex)
Free tier: Should fit within daily limits for moderate use
```

### Setup Config for Video
```json
{
  "setup": {
    "model": "models/gemini-3.1-flash-live-preview",
    "generationConfig": {
      "responseModalities": ["AUDIO"]
    },
    "realtimeInputConfig": {
      "activityHandling": "START_OF_ACTIVITY_INTERRUPTS",
      "turnCoverage": "TURN_INCLUDES_ONLY_ACTIVITY"
    }
  }
}
```
Video frames sent via:
```json
{"realtimeInput": {"video": {"data": "base64...", "mimeType": "image/jpeg"}}}
```

**Important**: For 3.1 Flash Live, `turnCoverage` defaults to `TURN_INCLUDES_AUDIO_ACTIVITY_AND_ALL_VIDEO` — meaning ALL video frames in a turn are billed, even if the user didn't speak. This is a wallet trap. Use `TURN_INCLUDES_ONLY_ACTIVITY` to only bill when there's actual activity.

---

## Part 3: Existing Plugin Video Infrastructure

The bridge.py already has video support wired:

### Constants (Lines 98–102)
```python
VIDEO_ENABLED = os.getenv("DISCORD_VOICE_LIVE_VIDEO_ENABLED", "true").lower() in {"1","true","yes","on"}
VIDEO_MAX_FPS = min(float(os.getenv("DISCORD_VOICE_LIVE_VIDEO_MAX_FPS", "1")), 1.0)  # Hard capped at 1
VIDEO_WHEN_RECENT_AUDIO_SECONDS = float(os.getenv("DISCORD_VOICE_LIVE_VIDEO_WHEN_RECENT_AUDIO_SECONDS", "8"))
VIDEO_MAX_BYTES = int(os.getenv("DISCORD_VOICE_LIVE_VIDEO_MAX_BYTES", str(512 * 1024)))
```

### Feed Method (Lines 392–428)
```python
def feed_video_frame(self, data: bytes, mime_type: str, force: bool = False) -> Dict[str, Any]:
    # Gating logic already implemented:
    # 1. Check VIDEO_ENABLED
    # 2. Check mime_type ∈ {image/jpeg, image/png}
    # 3. Check data size ≤ VIDEO_MAX_BYTES (512KB default)
    # 4. Check fps ≤ VIDEO_MAX_FPS (1fps hard cap)
    # 5. Check recent audio within VIDEO_WHEN_RECENT_AUDIO_SECONDS (8s)
```

### HTTP Endpoint (Lines 975–1000)
```python
# POST /frame with Content-Type header and body
# Query param: force=true to bypass audio-gating
# Returns: {"accepted": true/false, "reason": "..."}
```

### Metrics Tracked
- `video_in_frames`: total frames offered
- `video_sent_frames`: frames actually sent to Gemini
- `video_dropped_frames`: frames rejected by gating
- `video_last_reason`: last drop reason

---

## Part 4: Practical Approaches Given Discord Restrictions

Since Discord bots cannot receive video natively, here are the realistic options:

### Approach A: External Frame Feeder (Recommended)
Use a separate lightweight process to capture video from the host and POST to `/frame`.

**Options:**
1. **FFmpeg window capture** (if Discord client visible on host):
   ```bash
   ffmpeg -f x11grab -window_id $(xdotool search --name "Discord") \
          -r 1 -s 1280x720 -f image2pipe -vcodec mjpeg - \
   | python feeder.py --endpoint http://127.0.0.1:18943/frame
   ```

2. **Headless browser watching Discord** (Puppeteer/Playwright):
   - Open Discord Web in headless Chrome
   - Join voice channel with user credentials (⚠️ ToS risk — selfbot territory)
   - Capture `<video>` element via Chrome DevTools Protocol
   - Very heavy, fragile, against Discord ToS

3. **OS screen region capture**:
   ```bash
   ffmpeg -f x11grab -r 1 -s 1280x720 -i :0.0+100,200 \
          -f image2pipe -vcodec mjpeg -frames:v 1 -
   ```

### Approach B: Speaking-State Detection (Partial)
Since we can't get video, at least **know when someone is streaming**:
- `voice_recv.VoiceRecvClient.get_speaking(member)` — speaking state
- Discord Gateway `VOICE_STATE_UPDATE` — has `self_video` and `self_stream` flags
- These tell us WHEN someone turned on camera/screenshare, but not the content

**Implementation:**
```python
@discord.event
async def on_voice_state_update(member, before, after):
    if after.self_stream and not before.self_stream:
        # Someone started screensharing
        await bridge._gemini.send_text("I notice someone started screen sharing.")
    if after.self_video and not before.self_video:
        # Someone turned on camera
        await bridge._gemini.send_text("I notice someone turned on their camera.")
```

### Approach C: User-Pushed Frames (Manual)
User manually sends screenshots to the agent via a command:
```
/voice-live-frame <attach image>
```
The bot forwards the attached image to Gemini via `feed_video_frame()`.

---

## Part 5: Wallet-Safe Recommendations

### 1. Keep Existing Gating (It's Good)
The current `feed_video_frame()` gating is solid:
- ✅ 1fps hard cap
- ✅ 512KB max frame size
- ✅ Only sends when recent audio detected (8s window)
- ✅ JPEG/PNG only

### 2. Add `mediaResolution: LOW` to Setup
Add to `_connect_model()` setup payload:
```json
{
  "setup": {
    "generationConfig": {
      "mediaResolution": "LOW"
    }
  }
}
```
This cuts tokens per frame from ~258 to ~100 (60% savings).

### 3. Add Turn Coverage Override
Ensure `turnCoverage` is `TURN_INCLUDES_ONLY_ACTIVITY` to avoid billing for silent video-only turns:
```json
{
  "realtimeInputConfig": {
    "turnCoverage": "TURN_INCLUDES_ONLY_ACTIVITY"
  }
}
```

### 4. Add a "Video Budget" Cap
Track cumulative video tokens and hard-stop after a threshold:
```python
VIDEO_BUDGET_TOKENS = int(os.getenv("DISCORD_VOICE_LIVE_VIDEO_BUDGET_TOKENS", "500000"))
# ~30 minutes of 1fps low-res video
```

### 5. Disable Video by Default
Current default is `VIDEO_ENABLED=true`. Consider defaulting to `false` and requiring explicit opt-in.

---

## Part 6: Implementation Scaffold

### Files to Create/Modify

#### 1. `bridge.py` — Add mediaResolution + turnCoverage (Lines 462–510)
Modify `_connect_model()` setup payload to include:
```python
"generationConfig": {
    "responseModalities": ["AUDIO"],
    "mediaResolution": "LOW",  # NEW: wallet safety
    "speechConfig": { ... }
},
"realtimeInputConfig": {
    "activityHandling": "START_OF_ACTIVITY_INTERRUPTS",
    "turnCoverage": "TURN_INCLUDES_ONLY_ACTIVITY",  # NEW: don't bill silent video
    ...
}
```

#### 2. `bridge.py` — Add Video Budget Gating (Line 392)
```python
VIDEO_BUDGET_TOKENS = int(os.getenv("DISCORD_VOICE_LIVE_VIDEO_BUDGET_TOKENS", "0"))  # 0 = unlimited

# In feed_video_frame():
if VIDEO_BUDGET_TOKENS > 0 and self._video_tokens_used >= VIDEO_BUDGET_TOKENS:
    return {"accepted": False, "reason": "budget_exhausted"}
```

#### 3. `__init__.py` — Add Voice State Update Handler
```python
@discord_adapter.bot.event
async def on_voice_state_update(member, before, after):
    bridge = ... # get active bridge for guild
    if not bridge or not bridge._running:
        return
    if after.self_stream and not before.self_stream:
        await bridge._gemini.send_text("I notice someone started screen sharing. I can't see the screen, but I know it's happening.")
    if after.self_video and not before.self_video:
        await bridge._gemini.send_text("I notice someone turned on their camera. I can't see the video feed, but I'm listening.")
```

#### 4. New Script: `video-frame-feeder.py`
External utility that captures screen and POSTs to `/frame`:
```python
#!/usr/bin/env python3
"""Capture screen/window and feed frames to the voice bridge /frame endpoint."""
import os, sys, time, subprocess, requests, argparse

def capture_frame(source="screen", x=0, y=0, w=1280, h=720):
    cmd = [
        "ffmpeg", "-y", "-f", "x11grab", "-r", "1",
        "-s", f"{w}x{h}", "-i", f":0.0+{x},{y}",
        "-frames:v", "1", "-f", "image2pipe", "-vcodec", "mjpeg", "-"
    ]
    result = subprocess.run(cmd, capture_output=True)
    return result.stdout

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="http://127.0.0.1:18943/frame")
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args()

    while True:
        frame = capture_frame()
        if frame:
            r = requests.post(args.endpoint, data=frame,
                              headers={"Content-Type": "image/jpeg"})
            print(r.json())
        time.sleep(args.interval)

if __name__ == "__main__":
    main()
```

#### 5. New Command: `/voice-live-frame`
Allow users to manually attach an image that gets forwarded:
```python
# In adapter.py or __init__.py
@discord_adapter.bot.command(name="voice-live-frame")
async def voice_live_frame(ctx, attachment: discord.Attachment):
    bridge = ... # get active bridge
    if not bridge:
        await ctx.send("Not in a voice channel.")
        return
    data = await attachment.read()
    result = bridge._gemini.feed_video_frame(data, attachment.content_type or "image/jpeg")
    await ctx.send(f"Frame: {result}")
```

---

## Conclusion

**Discord bots cannot receive video. This is a platform limitation, not a bug.**

The practical path forward:
1. ✅ Keep the existing `feed_video_frame()` infrastructure — it's solid and wallet-aware
2. ✅ Add `mediaResolution: LOW` and `turnCoverage: TURN_INCLUDES_ONLY_ACTIVITY` to setup
3. ✅ Detect stream/camera state changes via `on_voice_state_update` and inform Gemini verbally
4. 🛠️ Build external frame feeder for users who want to pipe video in manually
5. 🛠️ Add `/voice-live-frame` command for manual image injection
6. 🛠️ Consider defaulting `VIDEO_ENABLED=false` until user opts in

This gives users awareness of video activity without pretending we can see it natively, while keeping the door open for external video feeding via the already-built `/frame` HTTP endpoint.

---

## Part 7: v0.2 Feeder Content-Aware Filtering (2026-06-07)

### Problem

The v0.1 feeder sent every captured frame at 1fps — solid overlays, static desktops,
Discord UI chrome — all cost ~258 tokens/frame to ingest. The model would honestly
describe "I see a white page" because that's what the feeder sent.

### Solution: Pre-capture 8×8 thumbnail → perceptual hash → filter

The v0.2 feeder (`video-frame-feeder.py`) now runs two ffmpeg processes per tick:

1. **Thumbnail pipe**: `ffmpeg -vf scale=8:8:flags=area,format=gray -frames:v 1 -f rawvideo`
   → 64 bytes of grayscale pixels

2. **Hash + stddev analysis**:
   - `stddev_8x8()`: standard deviation across 64 pixels. Near-zero = uniform frame (white/black overlay)
   - `perceptual_hash_8x8()`: average hash — each pixel compared to the mean → 64-bit aHash
   - `hamming_distance()`: bit-difference between consecutive hashes
   - `should_send()`: only returns True when content actually changed (Hamming ≥ min_change)

3. **Full JPEG capture** (`capture_full_frame`): only triggered when `should_send()` says yes

### Fallback

If the thumbnail pipe fails (ffmpeg version, filter incompatibility, no display), the
feeder logs a warning and falls through to full-frame capture + POST unfiltered. No
silent blackout.

### CLI reference

```
--min-change N        Hamming distance threshold (0-64). Default 2.
--stddev-min F        Min pixel stddev (0-255). Default 0 (disabled).
--no-content-filter   Send every frame (v0.1 behavior).
--source-label TEXT   Label passed to bridge for video_initialized webhook.
```

### Key lessons

- **Do not silently skip frames on pipe failure.** The original design `continue`d back
  to the loop when `capture_thumbnail` returned `None`, meaning if the thumbnail pipe
  ever failed, zero frames were ever sent. Always fall back.
- **stddev on 8×8 is too coarse for default filtering.** Real content (code editor with
  whitespace, dark theme with sparse text) can have low pixel variance at 8×8 resolution.
  The Hamming-distance filter is sufficient for static-content dedup; the stddev filter
  is opt-in (`--stddev-min`).
- **Do not pause the capture feed on the full frame if the thumb failed.** The original
  v0.2 had this bug: thumbnail fail → `continue` → loop forever with no frames sent.

### Related bridge changes (v0.2.8)

- `feed_video_frame()` now accepts optional `source` parameter, passed through to the
  `emit_video_initialized` webhook announce
- `emit_video_initialized()` fires when the bridge accepts a frame after ≥30s silence
  (configurable via `DISCORD_VOICE_LIVE_VIDEO_INITIALIZED_QUIET_THRESHOLD_S`)
- New webhook event class `bridge.video` (env `DISCORD_VOICE_LIVE_WEBHOOK_VIDEO`)
- System prompt rewritten to be strictly conditional — model no longer hallucinates
  "I see you're sharing your screen" on connect
- `mediaResolution: "LOW"` was REMOVED from setup payload — it causes WebSocket 1007
  on all current Gemini Live models (3.1-flash-live-preview, 2.5-flash-native-audio-*)

