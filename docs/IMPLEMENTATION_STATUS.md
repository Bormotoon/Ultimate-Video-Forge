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
   real processes. Workers write private results; the runner validates artifact
   checksums and publishes files, project, and manifest with a rollback journal.
   Error/cancellation rollback and killed-publisher recovery are tested. Recovery
   runs under the CLI project lock before planning. This is recoverable multi-file
   publication, not simultaneous filesystem visibility or proven power-loss
   durability. Live event forwarding and Windows tree cancellation remain open.
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
- Discovery now requires matching stage identity, current fingerprint, successful
   status, and verified artifacts. Repair optional dependency propagation and include all source identities in
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
   Initial frozen-source transcript/export, fingerprint, lock, and process
   characterization tests exist; the Hybrid contracts remain to be captured.
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

## Continuation audit (2026-10-08)

The earlier inventory above predates the preserved Hybrid integration. Explicit
`sync.mode=complex` now uses migrated matcher, piece planner and renderer, publishes
per-camera voice WAVs and AudioWarpMaps, and has frozen-engine PCM parity tests.
Automatic/simple modes still need integration with this path; multiple recorders,
acoustic fallback and the optional legacy audio steps remain open.

Implemented during this continuation:

- Live validated worker event callback with concurrent stdout/stderr draining;
  silent cancellation, noisy stderr, malformed events and transactional rollback
  remain covered. CLI `run --events`, `--only` and `--skip` expose the runner and
  planner controls. Progress callback tests assert notification before process exit.
- Optional roughcut dependency no longer blocks timeline export.
- Explicit transcription stream decoding; content-cache hits update source paths
  for the current project. OOM batch reduction and unbatched fallback are restored.
  Beam sizes, initial prompt and cache enablement are accepted by typed settings.
- Neutral export includes synchronized voice, applies keep ranges to camera/voice,
  honors requested export formats and FCPXML version, and fingerprints audio/edit
  artifacts. FCPXML connects overlapping media to a timeline gap; replaced camera
  audio is muted. XMEML includes rendered voice audio tracks.

Remaining important gaps found by direct code inspection:

- Program still selects the first camera and does not yet use synchronized voice,
  placement-aware source in-points or audio crossfades.
- Roughcut still treats word gaps as silence without the required energy gate;
  retakes, decision auditioning and camera switching are not integrated.
- XMEML camera-source audio, channel metadata and video/audio links need further
  work. FCPXML multicam, retake auditions, roles, markers and rational media format
  parity remain unverified. Camera clock factors need explicit export retiming.
- CLI failure continuation/reports, capability gating, global settings layers and
  Windows process-tree cancellation remain incomplete.
- Text/reels migration, shared llama session, pyannote facade, usable material/edit
  controls, subtitle save contract and production installers remain incomplete.

Verification uses `/tmp/opencode/studio-py310/bin/python -m pytest -q` on this Linux
host. It exercises the combined package, not the full frozen legacy suites and not
an actual Whisper/model/NLE end-to-end acceptance run.

### Production hardening continuation

- Roughcut execution now has a configurable silence-energy gate for pause and
  head/tail candidates; real generated-audio tests cover silence detection and
  preservation of audible gaps. Thus the earlier energy-gate gap is resolved for
  the currently selected first source stream, not yet arbitrary recorder tracks.
- Program execution now resolves placed camera coverage, selected camera groups,
  and synchronized voice, splits ranges at file boundaries, normalizes media,
  supports silent cameras and preserves output duration. A real ffmpeg integration
  test renders a silent camera with external voice and nonzero timeline placement.
  Audio crossfades, GPU auto-selection and extensive codec/VFR acceptance remain.
- Auto/simple recorder sync now renders through the preserved accepted matching
  facade and strategy selector. Acoustic-first fallback and multi-recorder selection
  are still open. Timeline ordering explicitly includes optional sync.
- Run failures produce a report and continue independent branches; strict settings
  type validation rejects malformed booleans/lists/integers before execution.
- Combined verification: 125 tests passed; Ruff passes on files changed in this
  continuation and git diff --check is clean. This is not full plan acceptance.

### Retakes and review continuation

- Migrated the unchanged frozen token-level retake detector into studio/stages/
  retakes.py with dependency-free local models/settings. Eleven original detector
  regression cases and four direct parity cases pass.
- Roughcut integrates last-take selection, stable cut IDs, saved manual decisions
  and a word-boundary guard. CLI `uvf review SOURCE` lists the edit; `--cut ID
  --accept` / `--reject` transactionally republishes decisions and manifest without
  changing the detector fingerprint. Overrides survive later detector runs by ID.
- FCPXML/XMEML serialize edit reasons and filler markers at edited times. Export
  and program fingerprints now include roughcut settings to invalidate results
  correctly when cutting is toggled while an older edit is still on disk.
- XMEML preserves original camera audio as linked per-channel clipitems and
  disables it when replaced. Sequence dimensions and source channel metadata are
  emitted. Source rates/bit-depth and optimized channel track grouping need work.
- Verification: 143 combined tests pass, changed-file Ruff and diff whitespace
  checks pass. Real NLE marker/link import and GUI auditioning remain unverified.

### Microphone speakers continuation

- SpeakersStage is executable and registered, with typed auto/mics/off settings,
  explicit recorder channel assignments, content fingerprints and transactional
  publication of diarization.json plus provenance report.
- Real ffmpeg channel decoding feeds affine placement-aware RMS bins, preserving
  inpoints/clock factors. Attribution handles bleed, overlap and silence. Missing
  recorder placements are blocked; ordinary stereo is not automatically declared
  to be two separate microphones.
- Tests cover offset stereo attribution through an actual subprocess worker,
  published artifact reuse, channel bounds, silent tracks and clock/inpoint bins.
- Pyannote, short-turn smoothing, RecorderTrack GUI controls and nonlinear warped
  attribution remain open. The current implementation does not establish complete
  speaker/multicam phase acceptance.

### Sequential processing migration: acoustic alignment

- Auto/simple now use migrated waveform-first alignment; complex has waveform
  fallback. GCC output matches the frozen implementation. Real offset audio with
  no transcript is found and camera-only placement retains the measured offset.
- Multiple overlapping recorders are aligned onto the shared primary clock and
  rendered independently. Best mode selects one voice; all mode publishes all
  lanes under sync-tracks keys. Subprocess regression covers two recorder outputs
  and warp maps. Disjoint sessions, lane export and discovery optimization remain.
- Combined suite: 149 tests pass. This does not close the external sync acceptance
  gates or the remaining WhisperSync enhancement/self-check/multicam tasks.

- Boundary Flex is connected to recorder rendering with `sync.boundary_flex=true`.
  Realized waveform verification is migrated and exposed through `sync.verify`
  and CLI `verify`; identical generated audio passes the real measurement/CLI test.
  Enhancement, ambience, self-check/repair and master/segment outputs remain open.

### Voice output continuation

- `sync.voice_segment_minutes` now publishes sample-accurate PCM segments plus
  a rendered-audio index. Full voice outputs remain available. Real PCM tests
  reassemble segments byte-for-byte; worker tests include extensible 24-bit WAV.
- `sync.master_wav` publishes an unedited stereo timeline voice master, chooses
  one replacement on overlapping cameras, retimes camera clock ratios and uses
  silence in uncovered ranges. Audio tests check duration and duplicate-level
  prevention. Quiet seam selection and segment-based NLE links remain open.

### Content self-check continuation

- Preserved content diagnostics and local text/acoustic re-alignment are migrated.
  Nineteen original regression cases pass, including empty-render content loss,
  ASR jitter, repeated occurrences and local repair-alignment rejection.
- `sync.self_check=warn` runs a shared Whisper engine on rendered voices, publishes
  rendered transcripts and content reports, and unloads the model before leaving
  the worker. Three frozen/migrated outcome parity cases and stage tests cover
  report serialization and failed content outcomes. Actual model acceptance is open.
- Automatic repair rendering and atomic warp-map replacement are not implemented;
  repair mode is deliberately not accepted by settings yet. Ambience/enhancement
  integration, master crossfades and remaining multicam tasks are still pending.

### Optional audio backend continuation

- Migrated preserved separation/enhancement and bounded subprocess logging behind
  public-media adapters. Supported denoise/denoise_dereverb/resemble modes are
  configured by sync.voice_enhance; sync.ambience adds separated camera room tone.
- Enhanced voices feed self-check, segments, master and export. Missing modules
  retain original voices with explicit warning reports. Ambience is conformed
  before publication and exported as separate connected audio lanes.
- Seventeen migrated enhancement regression tests pass plus integration dispatch
  tests; combined suite has 193 passing tests. Actual ML environments/models and
  GPU processing are not exercised; separation full regression and real listening
  acceptance remain open. Automatic content repair remains incomplete.

### Verified repair continuation

- `sync.self_check=repair` rerenders failed clips with a freshly derived
  whole-clip linear map. It publishes replacements only after repeated content
  recognition and passed acoustic lag verification. Inconclusive checks do not
  authorize replacement; errors retain the original selected audio and map.
- Selected sync paths, all-recorder selected lanes, warp maps and output report
  update together before segment/master generation. Initial self-check results
  stay intact; separate repair reports record the candidate acceptance evidence.
- Six candidate-policy tests cover acceptance, content failure, lag failure,
  inconclusive lag, skipped clips and backend errors. A stage integration test
  checks selected lane/map/report updates and downstream segment input.
- Repair and enhancement are currently mutually exclusive. Local span repair,
  real Whisper/model acceptance, master crossfades and GPU rendering remain open.
- Full regression run: 201 tests pass; Ruff on changed Python files and
  `git diff --check` pass. Recognition and lag acceptance in repair tests use
  controlled backends; these results do not establish real-model acceptance.

### Program encoder selection

- `program.encoder=auto` probes actual NVENC encoding and selects libx264 when
  unavailable. Explicit cpu/nvenc modes retain their requested behavior; runtime
  auto-mode GPU failures retry the whole job on CPU. `program/render.json` is
  published as `program_report` with encoder selection and fallback diagnostics.
- Local real probe found no h264_nvenc encoder in ffmpeg and selected libx264.
  Real CPU media render and controlled GPU success/failure/timeout tests pass.
  Full suite: 208 passed. Hardware GPU encoding acceptance remains open.

### Voice master crossfades

- Added `sync.master_crossfade_ms` (10 ms default; zero disables) with linear
  complementary fades in real source overlaps, preserving original timeline
  length and silence gaps. No fade is fabricated across non-overlapping sources.
- Fixed timeline-mix limiter latency and sample offset rounding. Real PCM tests
  verify smooth opposite-polarity switches, unchanged correlated-dialogue gain,
  silence gaps and one-sample impulse placement. Full suite: 212 passed; Ruff
  on changed files and `git diff --check` pass.
- Review-program edit joins remain hard cuts. Listening acceptance, VFR/mixed
  source fps acceptance and actual GPU render acceptance remain open.

### Mixed-fps and VFR review master

- Added validated `program.fps` string setting: auto or explicit rational/decimal
  rate from 1 to 240 fps. Review master normalizes source timestamps to CFR with
  cumulative frame rounding and explicit concat durations. Edited transcript
  timing follows the resulting frame padding/trimming, including zero-frame cuts.
- Real fixtures combine 24 fps, 30000/1001 fps and genuinely variable-frame-time
  video. Twelve short retained cuts render at both 25 and 30000/1001 fps with
  verified frame counts, uniform presentation steps and final frame-end duration.
- Fixed CPU encoder alias mismatch between settings and selector; both cpu and
  libx264 work. Full suite: 217 passed; changed-file Ruff and diff checks pass.
- Long-session A/V drift, NLE VFR interpretation and GPU hardware acceptance
  remain open. Next major processing block is speakers/multicam.

### Speaker short-turn smoothing

- Microphone attribution has configurable conservative short-island smoothing,
  preserving uncertain and overlapping speech, pauses and genuine handoffs.
  Raw microphone evidence remains published separately from smoothed turns.
- Eight regressions cover isolated islands, disabled smoothing, unknown/overlap,
  different neighbors, pauses, long turns, short neighbors and overlapping turns.
  Full suite: 225 passed; changed-file Ruff and diff checks pass.
- Pyannote integration, speaker-driven camera switching and true NLE multicam
  export remain open; smoothing does not establish real-world speaker accuracy.

### Speaker-driven review camera selection

- Added `program.speaker_cameras` and `program.min_shot_s` for optional camera
  switching from existing timeline speaker turns. Camera/group mappings are
  coverage-aware; explicit edit choices win, uncertain speech holds the current
  angle and missing preferred coverage falls back. Retained ranges remain intact.
- Program depends optionally on speakers, fingerprints speaker reports/group IDs
  and records its camera plan in render.json. Five regressions cover switching,
  holds, coverage fallback/manual precedence, removed ranges and invalid mappings.
- Full suite: 230 passed; changed-file Ruff and diff checks pass. Pyannote,
  NLE multicam export and real-world speaker/camera acceptance remain open.

### Isolated pyannote integration

- Explicit pyannote method uses the managed diarization environment; missing
  environments block planning. Configure speakers.model/device; model credentials
  use HF_TOKEN in the environment. Primary-source turns map to timeline with
  clipping, explicit overlap resolution and retained raw turns/logs.
- Controlled stage mapping/overlap and missing-module preflight regressions pass.
  Full suite: 232 passed. Real gated model download, inference, GPU usage and
  speaker accuracy have not been exercised. Auto remains microphone-based;
  cross-file speaker identity matching and true NLE multicam export remain open.
