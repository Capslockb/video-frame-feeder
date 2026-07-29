# Known issues

## CLI startup is currently blocked

Current `main` cannot construct its argument parser because `argparse` reserves `-h` for help while `video-frame-feeder.py` also assigns `-h` to `--height`.

As a result, normal startup and `python video-frame-feeder.py --help` fail before capture begins. `python -m py_compile video-frame-feeder.py` can still pass because compilation does not execute parser construction.

The smallest executable correction is prepared in draft [PR #18](https://github.com/Capslockb/video-frame-feeder/pull/18): remove the conflicting `-h` alias, preserve argparse's standard `-h/--help` action, and retain the long `--height` option with its existing default. Exact-head [`cli-smoke` run 2](https://github.com/Capslockb/video-frame-feeder/actions/runs/30438163210) passed compilation and parser regressions on Python 3.11, 3.12, and 3.13 at `27e97f18060086c52b89d74ab041d782e9203ee7`. The PR remains draft and unapproved; current `main` remains blocked until owner review, integration revalidation, and manual integration.

No documentation-only change can resolve this runtime defect.

## Filter thresholds are not fully validated

The CLI currently accepts any integer for `--min-change` and any floating-point value for `--stddev-min`, even though the meaningful ranges are 0–64 Hamming-distance bits and a **finite** 0–255 grayscale standard deviation.

Out-of-range values can silently change behavior: a `--min-change` value above 64 or a `--stddev-min` value above 255 can suppress later or all frames, while negative values can effectively disable the corresponding check. Non-finite values also require explicit handling: `--stddev-min nan` silently disables the standard-deviation check because comparisons with NaN are false, `inf` suppresses every valid thumbnail, and `-inf` disables the check.

Until argument validation is implemented through reviewed executable work, keep `--min-change` within 0–64 and use a finite `--stddev-min` within 0–255. The implementation task is tracked in [Issue #5](https://github.com/Capslockb/video-frame-feeder/issues/5) so it does not become entangled with the startup fix.

Owner review has accepted the focused validation change. The eventual parser fix must explicitly reject NaN and positive or negative infinity rather than relying only on ordinary lower/upper-bound comparisons. Boundary tests should cover `0`, `64`, `0.0`, and `255.0`, plus negative, above-range, `nan`, `inf`, and `-inf` inputs. No executable correction or exact-head CI evidence is present yet.

## Average-hash filtering can miss global brightness changes

The current average hash records whether each thumbnail pixel is above that thumbnail's own mean. Uniform black, gray, and white thumbnails therefore produce the same all-zero hash, and a global brightness shift that preserves relative pixel ordering can also preserve the hash.

Because `--stddev-min` defaults to `0`, uniform-frame filtering is disabled by default. After one uniform or structurally identical thumbnail is selected, a visibly different dark/light, blank-screen, lock-screen, or theme transition can therefore be classified as unchanged under the default `--min-change 2` threshold.

Until [Issue #12](https://github.com/Capslockb/video-frame-feeder/issues/12) is resolved through a separately reviewed filtering change, use `--no-content-filter` when material global-luminance transitions must always be offered to the bridge. This increases full-frame generation and delivery attempts. Do not assume that lowering `--min-change` preserves the existing filtering contract without separately validating the exact value and workload.

The correcting PR must add a reviewed luminance-sensitive signal or another deterministic equivalent, preserve genuinely unchanged-frame suppression and `--no-content-filter`, and include synthetic-thumbnail tests for black-to-white, dark-to-light, identical frames, structural changes, and threshold boundaries. No executable correction or exact-head CI evidence is present yet.

## Capture dimensions and interval values are not fully validated

The parser currently accepts zero or negative `--width` and `--height` values and passes them to FFmpeg, where they fail only during capture. It also accepts non-finite floating-point interval values.

After the startup blocker in Issue #4 is fixed, `--interval nan` can survive the current minimum-interval clamp and raise `ValueError` when continuous mode reaches `time.sleep()`. An infinite interval can raise `OverflowError` at the same boundary. A one-shot run exits before sleeping and may not expose this configuration defect.

Until [Issue #10](https://github.com/Capslockb/video-frame-feeder/issues/10) is resolved through reviewed executable work, keep width and height positive and use a finite positive interval. The intended normal behavior remains a minimum one-second interval; values below `1.0` are clamped upward by the current implementation.

Owner review has accepted this correction for implementation. The pending code change must reject non-positive dimensions and non-finite or non-positive intervals through normal argparse errors, preserve the current defaults, document the selected sub-second compatibility policy, and add parser tests after Issue #4 removes the conflicting `-h` alias. No executable correction or exact-head test evidence is present yet.

## One-shot delivery failures can still exit successfully

The process exit status currently reflects capture errors only. In `--once` mode, an HTTP failure or a bridge response with `accepted: false` is logged, but the process can still exit with status `0` when capture itself succeeded.

Do not use the current `--once` exit code as proof that a frame reached or was accepted by the bridge. The executable transport/lifecycle fix and required exit-semantics decision are tracked in [Issue #6](https://github.com/Capslockb/video-frame-feeder/issues/6). Continuous mode is expected to keep logging delivery failures without terminating unless the owner chooses a different policy.

## Bridge response JSON is not schema-validated

After a successful HTTP status, `post_frame()` returns the decoded JSON value unchanged. Both delivery paths then assume that value is a mapping and call `result.get("accepted")`.

A valid 2xx response whose top-level JSON value is `null`, a list, string, number, or boolean can therefore raise `AttributeError` and terminate continuous mode. A mapping with a truthy non-boolean value such as `{"accepted": "false"}` can instead be counted and logged as a successful delivery.

Modern Requests releases wrap JSON decoding failures in `requests.exceptions.JSONDecodeError`, which inherits from `RequestException` and is caught by the current handler. The repository does not declare a minimum Requests version, however, so the correcting implementation should still normalize JSON-decode failure explicitly rather than relying on dependency-version-specific exception inheritance.

Until [Issue #13](https://github.com/Capslockb/video-frame-feeder/issues/13) is resolved through reviewed HTTP/runtime work, treat only a bridge contract known to return a JSON object with a literal boolean `accepted` field as compatible. The correction must normalize malformed shapes and wrong-type fields to bounded rejection results, avoid logging raw response bodies, preserve continuous-mode operation, and add mocked response tests for both the normal filtered path and thumbnail-fallback path. This is separate from Issue #6's one-shot exit-code policy.

## HTTP failure reasons can expose endpoint and query metadata

`post_frame()` currently returns the raw Requests exception text in `reason`, and both delivery paths print that value. Requests exception strings commonly include the request URL, so routine failure output can reproduce the configured endpoint, existing query parameters, `force=true`, the URL-encoded `source` label, or a followed redirect target.

This is especially sensitive while `--source-label` defaults to `--source`: a Windows window title can enter the request URL and then reappear in an HTTP error. A mistakenly configured credential-bearing URL can also be echoed even though credentials in URLs are unsupported and explicitly discouraged.

Until [Issue #15](https://github.com/Capslockb/video-frame-feeder/issues/15) is resolved through reviewed HTTP/privacy work, keep endpoint query values and source labels non-sensitive, keep console output private, and do not publish raw feeder failure logs. The correction must replace raw exception text with stable bounded reason codes, include at most a numeric HTTP status where useful, redact URLs, queries, redirect locations, response bodies, and frame data, and add mocked stdout/stderr tests with sensitive sentinel values. This remains separate from Issues #8, #9, #13, and #14.

## Startup diagnostics expose endpoint and capture-source details

Before entering the capture loop, the feeder prints the complete configured endpoint, the raw capture source, the effective source label, and the first eight FFmpeg command arguments for both capture pipelines. On Windows, those command previews include the `title=...` window selector; an endpoint may also contain query metadata, routing identifiers, user-info, or an accidentally embedded secret.

This disclosure occurs on every successful startup and is therefore separate from Issue #15's HTTP-failure redaction. Supplying a neutral `--source-label` also does not hide the raw `--source` value or FFmpeg input preview covered by Issue #8.

Until [Issue #17](https://github.com/Capslockb/video-frame-feeder/issues/17) is resolved through reviewed logging/privacy work, keep all feeder startup output private, keep endpoint values free of secrets and sensitive query metadata, and avoid sensitive window titles where logs may be retained. The correction must replace raw values with bounded mode/state summaries, remove or redact input specifications from command previews, and add isolated stdout/stderr tests proving that endpoint, window-title, display, and window-ID sentinels are absent.

## Failed filtered deliveries can suppress the next retry

In the normal filtered path, the feeder stores the selected thumbnail hash before full-frame capture and before the bridge accepts the request. If full-frame capture fails, the HTTP request fails, or the bridge returns `accepted: false`, the hash still advances.

On the next iteration, the same visible content can therefore be classified as unchanged and skipped even though it was never delivered successfully. Delivery may not be attempted again until the screen changes enough to cross `--min-change`.

This is separate from Issue #6's exit-status problem. Until [Issue #11](https://github.com/Capslockb/video-frame-feeder/issues/11) is resolved through reviewed executable work, treat transient capture or delivery failures as potentially requiring a visible screen change before the filtered path offers another frame.

Owner review has accepted a narrowly scoped correction: keep the selected hash pending until full-frame capture succeeds and the bridge returns `accepted: true`; only then may it replace the previous accepted hash. Full-capture failure, HTTP or JSON failure, and `accepted: false` must leave identical content eligible for the next attempt. Preserve thumbnail-failure fallback, filtering thresholds, capture paths, and continuous-mode non-termination. Deterministic mocked tests must cover accepted delivery, each failure path, same-content retry, and later changed content. No executable correction or exact-head CI evidence is present yet.

## Authenticated frame endpoints are not supported

The feeder currently sends only an `image/jpeg` content-type header. It has no supported API-secret, bearer-token, or configurable authentication header, so it cannot deliver frames to a bridge that requires authenticated `/frame` requests.

Do not place credentials in `--endpoint`, `--source-label`, command-line arguments, or URLs, and do not weaken a bridge's authentication to make the feeder connect. Keep the endpoint on localhost or a trusted private network while the credential-source decision and reviewed transport change are tracked in [Issue #7](https://github.com/Capslockb/video-frame-feeder/issues/7).

## The default source label can expose window titles

When `--source-label` is omitted, the feeder currently reuses `--source` as the network-visible label, prints it locally, and sends it in the URL-encoded `source` query parameter.

On Windows, a non-`screen` capture source is a window title. That title can contain document names, chat participants, customer names, or other sensitive context and may be retained by bridge, proxy, webhook, or telemetry logs.

Until the accepted privacy correction in [Issue #8](https://github.com/Capslockb/video-frame-feeder/issues/8) is implemented through a reviewed PR, supply an explicit neutral label such as `--source-label screen-share` when capturing a named window. This changes only the network-visible metadata label; it does not change which window FFmpeg captures and does not stop the current startup diagnostics from printing the raw `--source` value and part of the generated FFmpeg command. Keep the feeder console and any captured logs private when a window title contains sensitive information.

Owner review has accepted making the default network-visible label absent or neutral, preserving explicit `--source-label` values with exactly-once encoding, keeping capture selection unchanged, and preventing implicit source/window names from entering routine request URLs or logs. The runtime patch and isolated privacy tests remain pending and must not be combined with capture, hashing, authentication, dependency, CI, or release work.

## `--force` can corrupt an existing endpoint query string

The current force path appends `?force=true` directly to the configured endpoint. If the endpoint already contains a query string, the result contains a second `?`, and the bridge may interpret `force=true` as part of the earlier parameter's value instead of as its own parameter.

Until the accepted HTTP correction in [Issue #9](https://github.com/Capslockb/video-frame-feeder/issues/9) is implemented, use a query-free endpoint whenever `--force` is enabled. Do not put credentials in endpoint query parameters.

Owner review has accepted rebuilding the request URL with standard URL/query utilities so existing non-secret parameters survive and optional `force` and `source` values are added as distinct, exactly-once-encoded parameters. Mocked tests must cover existing queries, blank and duplicate values, encoded labels, force-disabled behavior, and fragments without changing capture, filtering, authentication, dependencies, CI, or release behavior. No corrective commit or exact-head test evidence is present yet.

## Frame POST redirects can cross the configured endpoint trust boundary

`requests.post()` follows redirects by default. The current feeder does not disable that behavior. In particular, HTTP 307 and 308 responses preserve the POST method and body, so the captured JPEG can be resent to the redirect target rather than being processed only by the configured bridge endpoint.

Until [Issue #14](https://github.com/Capslockb/video-frame-feeder/issues/14) is resolved through reviewed HTTP/media-delivery work, use a direct stable endpoint that does not redirect and keep it on loopback or a trusted private network. Do not treat the final success response as proof that the configured endpoint itself accepted the frame.

The safe correction is to disable automatic redirects for frame uploads and normalize every 3xx response as a bounded rejection without logging the raw `Location` value. Mocked tests must cover 301, 302, 303, 307, and 308, prove that no second request receives the JPEG body, preserve Issue #13 response normalization, and preserve Issue #11 retry eligibility. No executable correction or exact-head CI evidence is present yet.
