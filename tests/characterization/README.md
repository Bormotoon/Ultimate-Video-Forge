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
coverage of self-check or all Hybrid edge cases. Additional cases pin nonlinear
clock pieces and test Boundary Flex correction direction and contiguous geometry
with controlled GCC measurements. Real assembly checks exact PCM ordering and
leading/trailing silence; it does not substitute for acoustic end-to-end checks.
The nonlinear plan overshoots its 30-second clip by about 44.6 ms. This is frozen
behavior, not an approved Studio warp-coverage policy: assembly trims to the clip
duration, so a future adapter must explicitly reconcile the rendered endpoint.

`test_render_parity.py` compares the migrated Studio renderer with the frozen
engine sample for sample for mixed copy/resample/atempo pieces, fades, leading
silence, final trimming, and tail padding. The renderer and map adapter exist as
standalone building blocks; SyncStage still needs the real piece planner wired in.

These tests are a migration oracle, not a claim that Studio matches it yet.
In particular, legacy fingerprint adoption is documented rather than approved:
Studio requires complete manifests and checked artifacts. Do not update golden
files to accommodate new code without an explicit, reviewed contract change.
