# Studio

Studio is a local desktop workflow for synchronizing multi-camera recordings,
building NLE projects, and producing podcast text and short-form video. The
project is being assembled from the frozen WhisperSync and Podcast Reels Forge
snapshots under `legacy/`.

The current milestone establishes reproducible source baselines, packaging,
CI, and synthetic fixtures before the shared `studio` core is implemented.
See `legacy/forge/docs/UNIFIED_APP_PLAN.md` for the migration plan and
`docs/source-snapshots/README.md` for exact source provenance.

## Development

Python 3.12 is the primary environment. Legacy baselines remain isolated:

```bash
python3.12 -m pytest -q legacy/whispersync/tests
python3.12 -m pytest -q legacy/forge/tests
```

Build the Studio package with:

```bash
python3.12 -m build
```

Generate the deterministic two-camera synchronization fixture with:

```bash
python3.12 tools/make_fixtures.py --force
```

The generator requires `ffmpeg`, `ffprobe`, and either `espeak-ng` or `espeak`.
