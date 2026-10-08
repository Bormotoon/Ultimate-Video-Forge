# Frozen source contracts

Run `python -m pytest -q tests/characterization` from the Studio checkout.
The suite imports the preserved sources in `legacy/` without modifying them;
it needs ffprobe for the real rendered-WAV metadata check.

Snapshots are the commits listed in `docs/source-snapshots/README.md`:
WhisperSync `c3b4b048b3610ad795cac20a3bda45c8fe14f67e` and Forge
`846fdc451474e95696d1447edf6bd8cd06a574ea`.

Golden files pin observable transcript rounding, SRT cues, source paths,
connected-clip intervals, camera-audio replacement, and staged relative paths.
Other tests characterize fingerprint bytes, legacy adoption, lock contention,
interrupted publication, subprocess status, bounded logs, and timeouts.

The Hybrid golden pins pause absorption of a one-second offset, unchanged
speech rates, contiguous source coverage, and sentence placement. Real ffmpeg
tests pin PCM format, sample counts for copy/resample/atempo, and exact source
samples on the unchanged-rate path. This is an initial Hybrid oracle, not full
coverage of nonlinear drift, Boundary Flex, self-check, or assembled audio.

These tests are a migration oracle, not a claim that Studio matches it yet.
In particular, legacy fingerprint adoption is documented rather than approved:
Studio requires complete manifests and checked artifacts. Do not update golden
files to accommodate new code without an explicit, reviewed contract change.
