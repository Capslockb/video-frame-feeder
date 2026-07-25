# Known issues

## CLI startup is currently blocked

Current `main` cannot construct its argument parser because `argparse` reserves `-h` for help while `video-frame-feeder.py` also assigns `-h` to `--height`.

As a result, normal startup and `python video-frame-feeder.py --help` fail before capture begins. `python -m py_compile video-frame-feeder.py` can still pass because compilation does not execute parser construction.

The executable fix is tracked in [Issue #4](https://github.com/Capslockb/video-frame-feeder/issues/4). Preserve argparse's standard `-h/--help` action and keep `--height` without the conflicting alias, or use a non-conflicting short option after owner review.

No documentation-only change can resolve this runtime defect.
