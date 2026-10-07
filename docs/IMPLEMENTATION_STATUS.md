# Implementation status

This file distinguishes implemented contracts from plan items that still need
legacy migration or external acceptance. It intentionally does not mark a
phase complete merely because a similarly named module exists.

## Current foundations (not phase acceptance)

- Source snapshots, source provenance, Python 3.12 lock files, CI definitions,
  and local wheel/PyInstaller smoke builds. Remote matrix and clean-machine
  acceptance have not been demonstrated.
- Deterministic two-camera/recorder/drift/pause/retake/GoPro fixture generator.
- Versioned project, artifact manifest, transcript, four-time-domain,
  SourcePlacement, AudioWarpMap, and EditMap contracts.
- Planner and JSON-lines subprocess runner foundations. Effective settings are
  snapshotted per worker; silent cancellation and large stderr are tested with
  real processes. Atomic file helpers exist, but stage artifacts and project
  changes are NOT yet published as a transaction. Live event forwarding and
  Windows process-tree cancellation remain open.
- Media scan, prepare input declaration, Whisper stage facade and content
  cache, affine text-anchor placement, timeline transcript, deterministic
  pause rough cut, program transcript mapping, microphone energy attribution,
  neutral sequences, FCPXML and XMEML serializers.
- CLI `scan`, `plan`, `run`, and `doctor`; initial PyQt QProcess desktop shell;
  isolated module manager; owned llama-server lifecycle.

## Requires further implementation

- No phase is closed. Phase 0 still requires the private acceptance recording
  and verified CI/installed-artifact checks. Prior task-list completion of
  phase 0 was premature.
- Replace manifest-existence discovery checks with validated input revisions;
  repair optional dependency propagation and include all source identities in
  fingerprints. A completed prototype unit suite is not an end-to-end run.
- Fix source selection/stream decoding and carry over Whisper OOM recovery.
  The current sync implementation fits one affine map even when labelled
  strategy 3; it does not render synchronized recorder audio. Existing NLE
  serializers do not establish synchronized-audio or legacy export parity.
- Rough-cut pause removal still lacks the required energy gate; program
  rendering currently uses one source camera without the required placement,
  synchronized audio, and crossfade integration. These need regression tests
  before use on real material.
- Full characterization/golden migration of WhisperSync matcher, RANSAC,
  acoustic fallback, strategies 1/2/3, piece renderer, Boundary Flex,
  ambience, enhancement, self-check, repair, segments, and verification.
- FCPXML behavior parity for connected clips, auditions, roles, synchronized
  rendered audio, relative relinking, multicam, and rough-cut markers.
- Editable Material screen, persistent manual overrides, RecorderTrack model,
  camera/speaker assignment, multicam switching, and edit decision auditioning.
- Forge proofread, article, term check, analysis/reels selection, reel render,
  subtitle burn/editor save contract, YouTube fetch, and unattended batch.
- Model download UI, module settings UI, first-run wizard, auto-tuning, and
  production installers for all three operating systems.

## External blockers

- A private 20-40 minute two-camera plus recorder sample has not been selected
  in this workspace.
- Final Cut Pro and Premiere Pro versions and machines are not available in
  this environment, so import, relinking, lip-sync, and multicam acceptance is
  open.
- The required overnight channel batch run has not occurred.
- Clean-machine installer acceptance on Linux, Windows, and macOS has not
  occurred.
