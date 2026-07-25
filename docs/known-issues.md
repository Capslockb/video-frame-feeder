# Known issues

## CLI startup is currently blocked

Current `main` cannot construct its argument parser because `argparse` reserves `-h` for help while `video-frame-feeder.py` also assigns `-h` to `--height`.

As a result, normal startup and `python video-frame-feeder.py --help` fail before capture begins. `python -m py_compile video-frame-feeder.py` can still pass because compilation does not execute parser construction.

The executable fix is tracked in [Issue #4](https://github.com/Capslockb/video-frame-feeder/issues/4). Preserve argparse's standard `-h/--help` action and keep `--height` without the conflicting alias, or use a non-conflicting short option after owner review.

No documentation-only change can resolve this runtime defect.

## Filter thresholds are not range-validated

The CLI currently accepts any integer for `--min-change` and any floating-point value for `--stddev-min`, even though the meaningful ranges are 0–64 Hamming-distance bits and 0–255 grayscale standard deviation.

Out-of-range values can silently change behavior: a `--min-change` value above 64 or a `--stddev-min` value above 255 can suppress later or all frames, while negative values can effectively disable the corresponding check.

Until argument validation is implemented through reviewed executable work, keep `--min-change` within 0–64 and `--stddev-min` within 0–255. The implementation task is tracked separately so it does not become entangled with the startup fix.