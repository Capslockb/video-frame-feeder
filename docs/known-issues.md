# Known issues

## CLI startup is currently blocked

Current `main` cannot construct its argument parser because `argparse` reserves `-h` for help while `video-frame-feeder.py` also assigns `-h` to `--height`.

As a result, normal startup and `python video-frame-feeder.py --help` fail before capture begins. `python -m py_compile video-frame-feeder.py` can still pass because compilation does not execute parser construction.

The executable fix is tracked in [Issue #4](https://github.com/Capslockb/video-frame-feeder/issues/4). Preserve argparse's standard `-h/--help` action and keep `--height` without the conflicting alias, or use a non-conflicting short option after owner review.

No documentation-only change can resolve this runtime defect.

## Filter thresholds are not range-validated

The CLI currently accepts any integer for `--min-change` and any floating-point value for `--stddev-min`, even though the meaningful ranges are 0–64 Hamming-distance bits and 0–255 grayscale standard deviation.

Out-of-range values can silently change behavior: a `--min-change` value above 64 or a `--stddev-min` value above 255 can suppress later or all frames, while negative values can effectively disable the corresponding check.

Until argument validation is implemented through reviewed executable work, keep `--min-change` within 0–64 and `--stddev-min` within 0–255. The implementation task is tracked in [Issue #5](https://github.com/Capslockb/video-frame-feeder/issues/5) so it does not become entangled with the startup fix.

## Capture dimensions and interval values are not fully validated

The parser currently accepts zero or negative `--width` and `--height` values and passes them to FFmpeg, where they fail only during capture. It also accepts non-finite floating-point interval values.

After the startup blocker in Issue #4 is fixed, `--interval nan` can survive the current minimum-interval clamp and raise `ValueError` when continuous mode reaches `time.sleep()`. An infinite interval can raise `OverflowError` at the same boundary. A one-shot run exits before sleeping and may not expose this configuration defect.

Until [Issue #10](https://github.com/Capslockb/video-frame-feeder/issues/10) is resolved through reviewed executable work, keep width and height positive and use a finite positive interval. The intended normal behavior remains a minimum one-second interval; values below `1.0` are clamped upward by the current implementation.

## One-shot delivery failures can still exit successfully

The process exit status currently reflects capture errors only. In `--once` mode, an HTTP failure or a bridge response with `accepted: false` is logged, but the process can still exit with status `0` when capture itself succeeded.

Do not use the current `--once` exit code as proof that a frame reached or was accepted by the bridge. The executable transport/lifecycle fix and required exit-semantics decision are tracked in [Issue #6](https://github.com/Capslockb/video-frame-feeder/issues/6). Continuous mode is expected to keep logging delivery failures without terminating unless the owner chooses a different policy.

## Authenticated frame endpoints are not supported

The feeder currently sends only an `image/jpeg` content-type header. It has no supported API-secret, bearer-token, or configurable authentication header, so it cannot deliver frames to a bridge that requires authenticated `/frame` requests.

Do not place credentials in `--endpoint`, `--source-label`, command-line arguments, or URLs, and do not weaken a bridge's authentication to make the feeder connect. Keep the endpoint on localhost or a trusted private network while the credential-source decision and reviewed transport change are tracked in [Issue #7](https://github.com/Capslockb/video-frame-feeder/issues/7).

## The default source label can expose window titles

When `--source-label` is omitted, the feeder currently reuses `--source` as the network-visible label, prints it locally, and sends it in the URL-encoded `source` query parameter.

On Windows, a non-`screen` capture source is a window title. That title can contain document names, chat participants, customer names, or other sensitive context and may be retained by bridge, proxy, webhook, or telemetry logs.

Until the privacy-sensitive runtime decision in [Issue #8](https://github.com/Capslockb/video-frame-feeder/issues/8) is implemented through a reviewed PR, supply an explicit neutral label such as `--source-label screen-share` when capturing a named window. This changes only the network-visible metadata label; it does not change which window FFmpeg captures and does not stop the current startup diagnostics from printing the raw `--source` value and part of the generated FFmpeg command. Keep the feeder console and any captured logs private when a window title contains sensitive information.

## `--force` can corrupt an existing endpoint query string

The current force path appends `?force=true` directly to the configured endpoint. If the endpoint already contains a query string, the result contains a second `?`, and the bridge may interpret `force=true` as part of the earlier parameter's value instead of as its own parameter.

Until the HTTP query construction is corrected through the reviewed transport work in [Issue #9](https://github.com/Capslockb/video-frame-feeder/issues/9), use a query-free endpoint whenever `--force` is enabled. Do not put credentials in endpoint query parameters. A future fix must preserve legitimate non-secret parameters and encode the optional source label exactly once without changing capture, filtering, authentication, dependencies, CI, or release behavior.
