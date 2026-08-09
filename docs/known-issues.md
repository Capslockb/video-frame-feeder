# Known issues

## CLI startup is currently blocked

Current `main` cannot construct its argument parser because `argparse` reserves `-h` for help while `video-frame-feeder.py` also assigns `-h` to `--height`.

As a result, normal startup and `python video-frame-feeder.py --help` fail before capture begins. `python -m py_compile video-frame-feeder.py` can still pass because compilation does not execute parser construction.

This is tracked in [Issue #4](https://github.com/Capslockb/video-frame-feeder/issues/4). No documentation-only change can resolve the runtime defect.

## Filter thresholds are not fully validated

The CLI currently accepts any integer for `--min-change` and any floating-point value for `--stddev-min`, even though the meaningful ranges are 0–64 Hamming-distance bits and a **finite** 0–255 grayscale standard deviation.

Out-of-range values can silently change behavior: a `--min-change` value above 64 or a `--stddev-min` value above 255 can suppress later or all frames, while negative values can effectively disable the corresponding check. Non-finite values also require explicit handling: `--stddev-min nan` silently disables the standard-deviation check because comparisons with NaN are false, `inf` suppresses every valid thumbnail, and `-inf` disables the check.

Until [Issue #5](https://github.com/Capslockb/video-frame-feeder/issues/5) is resolved, keep `--min-change` within 0–64 and use a finite `--stddev-min` within 0–255. Current `main` does not enforce these ranges.

## Average-hash filtering can miss global brightness changes

The current average hash records whether each thumbnail pixel is above that thumbnail's own mean. Uniform black, gray, and white thumbnails therefore produce the same all-zero hash, and a global brightness shift that preserves relative pixel ordering can also preserve the hash.

Because `--stddev-min` defaults to `0`, uniform-frame filtering is disabled by default. After one uniform or structurally identical thumbnail is selected, a visibly different dark/light, blank-screen, lock-screen, or theme transition can therefore be classified as unchanged under the default `--min-change 2` threshold.

Until [Issue #12](https://github.com/Capslockb/video-frame-feeder/issues/12) is resolved, use `--no-content-filter` when material global-luminance transitions must always be offered to the bridge. This increases full-frame generation and delivery attempts. Do not assume that lowering `--min-change` preserves the existing filtering contract without separately validating the exact value and workload.

## Capture dimensions and interval values are not fully validated

The parser currently accepts zero or negative `--width` and `--height` values and passes them to FFmpeg, where they fail only during capture. It also accepts non-finite floating-point interval values.

After the startup blocker in Issue #4 is fixed, `--interval nan` can survive the current minimum-interval clamp and raise `ValueError` when continuous mode reaches `time.sleep()`. An infinite interval can raise `OverflowError` at the same boundary. A one-shot run exits before sleeping and may not expose this configuration defect.

Until [Issue #10](https://github.com/Capslockb/video-frame-feeder/issues/10) is resolved, keep width and height positive and use a finite positive interval. The intended normal behavior remains a minimum one-second interval; values below `1.0` are clamped upward by the current implementation.

## One-shot delivery failures can still exit successfully

The process exit status currently reflects capture errors only. In `--once` mode, an HTTP failure or a bridge response with `accepted: false` is logged, but the process can still exit with status `0` when capture itself succeeded.

Until [Issue #6](https://github.com/Capslockb/video-frame-feeder/issues/6) is resolved, do not use the current `--once` exit code as proof that a frame reached or was accepted by the bridge.

## Bridge response JSON is not schema-validated

After a successful HTTP status, `post_frame()` returns the decoded JSON value unchanged. Both delivery paths then assume that value is a mapping and call `result.get("accepted")`.

A valid 2xx response whose top-level JSON value is `null`, a list, string, number, or boolean can therefore raise `AttributeError` and terminate continuous mode. A mapping with a truthy non-boolean value such as `{"accepted": "false"}` can instead be counted and logged as a successful delivery.

Modern Requests releases wrap JSON decoding failures in `requests.exceptions.JSONDecodeError`, which inherits from `RequestException` and is caught by the current handler. The repository does not declare a minimum Requests version, however, so behavior should not be assumed from a particular Requests release.

Until [Issue #13](https://github.com/Capslockb/video-frame-feeder/issues/13) is resolved, treat only a bridge contract known to return a JSON object with a literal boolean `accepted` field as compatible.

## HTTP failure reasons can expose endpoint and query metadata

`post_frame()` currently returns the raw Requests exception text in `reason`, and both delivery paths print that value. Requests exception strings commonly include the request URL, so routine failure output can reproduce the configured endpoint, existing query parameters, `force=true`, the URL-encoded `source` label, or a followed redirect target.

This is especially sensitive while `--source-label` defaults to `--source`: a Windows window title can enter the request URL and then reappear in an HTTP error. A mistakenly configured credential-bearing URL can also be echoed even though credentials in URLs are unsupported and explicitly discouraged.

Until [Issue #15](https://github.com/Capslockb/video-frame-feeder/issues/15) is resolved, keep endpoint query values and source labels non-sensitive, keep console output private, and do not publish raw feeder failure logs.

## Startup diagnostics expose endpoint and capture-source details

Before entering the capture loop, the feeder prints the complete configured endpoint, the raw capture source, the effective source label, and the first eight FFmpeg command arguments for both capture pipelines. On Windows, those command previews include the `title=...` window selector; an endpoint may also contain query metadata, routing identifiers, user-info, or an accidentally embedded secret.

This disclosure occurs on every successful startup and is therefore separate from Issue #15's HTTP-failure redaction. Supplying a neutral `--source-label` addresses the request-metadata exposure tracked in Issue #8, but it does not hide the raw `--source` value or FFmpeg input preview covered here.

Until [Issue #17](https://github.com/Capslockb/video-frame-feeder/issues/17) is resolved, keep all feeder startup output private, keep endpoint values free of secrets and sensitive query metadata, and avoid sensitive window titles where logs may be retained.

## Failed filtered deliveries can suppress the next retry

In the normal filtered path, the feeder stores the selected thumbnail hash before full-frame capture and before the bridge accepts the request. If full-frame capture fails, the HTTP request fails, or the bridge returns `accepted: false`, the hash still advances.

On the next iteration, the same visible content can therefore be classified as unchanged and skipped even though it was never delivered successfully. Delivery may not be attempted again until the screen changes enough to cross `--min-change`.

Until [Issue #11](https://github.com/Capslockb/video-frame-feeder/issues/11) is resolved, treat transient capture or delivery failures as potentially requiring a visible screen change before the filtered path offers another frame.

## Authenticated frame endpoints are not supported

The feeder currently sends only an `image/jpeg` content-type header. It has no supported API-secret, bearer-token, or configurable authentication header, so it cannot deliver frames to a bridge that requires authenticated `/frame` requests.

Do not place credentials in `--endpoint`, `--source-label`, command-line arguments, or URLs, and do not weaken a bridge's authentication to make the feeder connect. Keep the endpoint on localhost or a trusted private network while support is tracked in [Issue #7](https://github.com/Capslockb/video-frame-feeder/issues/7).

## The default source label can expose window titles

When `--source-label` is omitted, the feeder currently reuses `--source` as the network-visible label, prints it locally, and sends it in the URL-encoded `source` query parameter.

On Windows, a non-`screen` capture source is a window title. That title can contain document names, chat participants, customer names, or other sensitive context and may be retained by bridge, proxy, webhook, or telemetry logs.

Until [Issue #8](https://github.com/Capslockb/video-frame-feeder/issues/8) is resolved, supply an explicit neutral label such as `--source-label screen-share` when capturing a named window. This changes only the network-visible metadata label; it does not change which window FFmpeg captures and does not stop the current startup diagnostics from printing the raw `--source` value and part of the generated FFmpeg command. Keep the feeder console and any captured logs private when a window title contains sensitive information.

## `--force` can corrupt an existing endpoint query string

The current force path appends `?force=true` directly to the configured endpoint. If the endpoint already contains a query string, the result contains a second `?`, and the bridge may interpret `force=true` as part of the earlier parameter's value instead of as its own parameter.

Until [Issue #9](https://github.com/Capslockb/video-frame-feeder/issues/9) is resolved, use a query-free endpoint whenever `--force` is enabled. Do not put credentials in endpoint query parameters.

## Frame POST redirects can cross the configured endpoint trust boundary

`requests.post()` follows redirects by default. The current feeder does not disable that behavior. In particular, HTTP 307 and 308 responses preserve the POST method and body, so the captured JPEG can be resent to the redirect target rather than being processed only by the configured bridge endpoint.

Until [Issue #14](https://github.com/Capslockb/video-frame-feeder/issues/14) is resolved, use a direct stable endpoint that does not redirect and keep it on loopback or a trusted private network. Do not treat the final success response as proof that the configured endpoint itself accepted the frame.
