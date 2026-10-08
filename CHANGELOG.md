# Changelog

All notable changes to Studio will be documented in this file.

## Unreleased

### Fixed

- Worker cancellation remains responsive without stdout output, both pipes are
  drained concurrently, and buffered events receive the same protocol checks.
- Worker diagnostics use stderr; failures emit a protocol-valid failed event.
- Workers receive a private snapshot of effective settings, including CLI overrides;
  manifests record the settings actually used, without modifying project YAML.
- Invalid CLI settings return exit code 2 through the installed console entry
  point and write diagnostics to stderr, keeping JSON stdout clean.

### Added

- Frozen-source characterization tests and golden transcript/export contracts,
  including lock interruption, fingerprint adoption, and subprocess errors.
- Frozen source snapshots for WhisperSync and Podcast Reels Forge.
- Reproducible Python 3.12 lock files for both legacy applications.
- Initial installable Studio package and packaged UI resources.
- Deterministic two-camera, recorder, drift, pause, retake, and GoPro chapter fixture generator.
- CI for Python 3.10-3.13, both frozen legacy test baselines, clean wheel installs, PyInstaller, and cross-platform artifact imports.
- Recoverable stage publication with private worker outputs, rollback on
  cancellation or errors, and journal recovery before CLI planning.
- Discovery plan gating validates stage identity, fingerprint, successful status,
  and artifact checksums instead of accepting manifest file existence.
