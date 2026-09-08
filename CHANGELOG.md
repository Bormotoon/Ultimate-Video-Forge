# Changelog

All notable changes to WhisperSync will be documented in this file.

## [Unreleased]

### Audit remediation (PROJECT_AUDIT_2026-09-08)

A project-wide audit found that the main risk was not style but **silently
wrong results**: work that looked successful while the audio, the timeline or
the diagnosis was incorrect. The changes below are grouped by what could go
wrong, not by file.

#### Results could be overwritten or invented

- **Every source now has a stable id** (`engine/sources.py`). File stems were
  being used as identifiers, so `/A/take.wav` and `/B/take.wav` both produced
  `.master/take_master.wav`: the second extraction overwrote the first and
  *both* recorders then rendered from one file. The same collision hit
  `clip.mov` vs `clip.mp4`, two cameras' identically-named clips, and their
  exported transcripts. Master audio, rendered voice, transcripts and ambience
  are all named from the id; display names are for the UI only.
- **Each run owns its scratch and locks its output** (`engine/workspace.py`).
  Runs shared `audio_synced/`, `.master/` and `enhance_tmp/` under ffmpeg's
  `-y`, so a second run overwrote the audio the first run's FCPXML still
  referenced and either run's cleanup deleted files the other was reading. A
  concurrent run on the same output folder is now refused with an explanation.
- **Results are published atomically.** The FCPXML, the master WAV and every
  rendered voice track are written to a temporary beside their destination and
  moved into place only when complete, so a crash or cancellation mid-render
  can no longer replace a previous good result with a fragment.
- **The separator can no longer return a stale file.** Each batch runs into a
  private output directory and every output is verified (readable, non-empty,
  positive duration) before publication — a partially failed earlier run used
  to leave files with exactly the names the next run would look for, and those
  were accepted as its results.
- **Cache pruning only deletes its own files.** With a user-chosen `cache_dir`
  and `cache_max_age_days` set, retention deleted every old `*.json` in that
  directory. Transcripts now live in a `transcripts/` subdirectory and each
  file's name shape and schema marker are checked before any unlink.
- **A duplicated input file is rejected** instead of being processed twice
  under two identities.

#### Audio and timeline could be wrong

- **One acceptance gate for every alignment** (`matcher.evaluate_alignment`).
  Placement and rendering judged maps separately, so a map placement had
  rejected could still be used to cut audio; neither looked at the clock ratio,
  the residual or how much of the clip the evidence covered. A two-anchor fit
  with `k=10`, `offset=-1004 s` and a sub-millisecond residual passed both. A
  map that fails the gate becomes an explicit *unresolved* clip.
- **Boundary Flex actually moves the speech.** The correction shifted a piece's
  read position and its output position by compensating amounts, which cancel
  exactly: a −80 ms correction left a recorder event at 107 s on camera 7 s,
  precisely where it started. Corrections now re-target read boundaries while
  output positions stay fixed, so the plan's length and contiguity are
  invariant by construction.
- **Pause ducking used the time map backwards.** Recorder words were projected
  with `(t − offset) / k` — the inverse of the map — so with a non-zero offset
  real speech was classified as silence and attenuated by 18 dB. Invisible on
  the identity maps the old tests used.
- **An implausible tempo now rejects the breakpoint** instead of being clamped.
  Clamping kept the bad anchor and broke the geometry: a 10 s clip came out
  with 9.8 s of pieces. Plans are validated (`pipeline.validate_pieces`).
- **The acoustic scan has no blind spots.** It probed the recorder on a 30 s
  grid and searched ±1 s around each probe — 2 s inspected out of every 30 — so
  a true offset landing in a hole was reported as "no match". Camera windows
  are now correlated against whole overlapping recorder blocks, and a window
  whose best match barely beats a rival elsewhere is declined rather than
  resolved by argmax.
- **A successful acoustic match is no longer discarded.** Confidence was
  measured by `len(anchors)`, which is zero for an acoustic map by
  construction; `AlignmentMap.provenance`/`inliers`/`evidence_span_s` now carry
  the evidence.
- **Float recorders keep their headroom.** A 32-bit float source was conformed
  to `pcm_s32le` purely because it was "32-bit", hard-clipping every sample
  above full scale — unrecoverable.
- **Probing and decoding agree on the audio stream.** ffprobe's "first audio
  stream" and ffmpeg's automatic selection are different rules, so a container
  with a mono mic plus a silent stereo scratch track could be probed on one and
  decoded from the other. The chosen stream is recorded and mapped explicitly.
- **VFR duration** uses timestamps instead of `nb_frames / r_frame_rate`, which
  reported 8 s for a 10 s file.
- **Negative lip-sync calibration is delivered, not clamped.** Two serializers
  independently clamped the resulting negative offset to zero, producing
  exactly the uncalibrated result. The whole plan's origin is shifted instead.
- **Unresolved clips fall back per camera**, not after an unrelated camera's
  last clip.
- **Large WAVs are written as RF64 when needed** (`-rf64 auto`) — plain RIFF
  cannot describe a file past 4 GiB, which stereo 48 kHz/24-bit reaches in
  ~4.1 hours.
- **The master mix no longer clips.** `amix` with `normalize=0` sums past full
  scale; correlated microphones produced hard-clipped output (measured flat
  factor ~30). The mix runs in float with a limiter at 0 dBFS.

#### The exported project could not be trusted

- **Simultaneous cameras are stacked, not queued.** Every camera went into the
  spine, which is a single sequential track, so two cameras both covering
  [0, 10] were exported as A [0, 10] and B [10, 20] — twenty seconds of footage
  from a ten-second shoot, with the audio still at its true positions. One
  camera forms the primary storyline; the rest are connected clips on their own
  lanes.
- **Replaced camera audio is muted** with `srcEnable="video"`. `videoRole` does
  not disable audio, so the camera's own microphone played under the clean
  synced voice — two copies of the same speech, comb-filtering. Clips with no
  replacement keep their sound.
- **Retakes are exported as markers, not auditions.** The audition played the
  keeper take's audio at the group's start while the picture did not switch at
  all: takes at [2,4] and [5,8] put the voice 3 s ahead and left a hole in the
  clean track. Markers convey the same finding with no risk to the A/V
  relationship.
- **FCPXML validation actually validates.** It accepted a document with a
  dangling `ref` and a nonsense duration. It now checks resource references,
  time values, positive durations, media existence and that the sequence covers
  its spine. `export.fcpxml_intervals` recovers absolute intervals for
  round-trip testing.
- **Retake groups keep all their attempts.** Three identical takes produced a
  group of two with the middle one kept, because the backward extension walked
  into the previous attempt and the resulting negative gap rejected the third.

#### Diagnosis could not be trusted

- **Self-check distinguishes passed / failed / inconclusive.** An empty span
  list meant all three: a render containing *no speech at all* against a camera
  clip with six words was reported as clean — total content loss reading as a
  pass — and "could not be checked" was indistinguishable from "checked and
  fine".
- **A lost tail is flagged at the tail.** A render keeping only [0, 0.2] of a
  clip whose camera held six more words out to 20 s produced a span of
  [0.2, 0.2]: a zero-length flag at the start for a twenty-second hole at the
  end, which no repair could act on.
- **Verification uses the real source and range.** Voice clips were matched
  back to their video by `display_name.startswith(stem)`, which paired `A1`
  with `A10`, collapsed two cameras' identically named clips, and compared a
  voice *segment* covering minutes 5–10 against the opening seconds of the
  video — reporting the five-minute content difference as lip-sync lag. Clips
  now carry an explicit `source_ref`.
- **Verification reports honest coverage.** Rejected windows were dropped
  before counting, so the report always read N/N: three windows with sharpness
  [100, 1, 1] reported "1/1 confident" instead of 1/3. A pass now also requires
  enough independent, well-spread measurements; `inconclusive` is a distinct
  outcome and exit code.
- **`--grid-s 0` no longer hangs.** A zero grid left the sampling cursor in
  place and a negative one walked it backwards, so the loop never terminated.

#### Cancelling, closing and failing

- **Cancel actually stops the run.** It emitted a log line and nothing else, so
  neither `finished` nor `error` fired — and those are what stop the thread and
  restore the buttons. The QThread kept running and the window stayed stuck
  mid-run. `SyncWorker.cancelled` is now a third terminal outcome.
- **Closing waits for the pipeline.** `quit()` cannot interrupt an executing
  slot, so the window closed over a live worker. The close is deferred until
  the thread really stops; a second close forces it.
- **Cleanup waits for render workers.** `shutdown(wait=False)` returned while
  ffmpeg workers were still running, and the scratch and masters they were
  using were then deleted underneath them.
- **`fork` is never used for the render pool.** The `threading.active_count()`
  heuristic cannot see Qt, CTranslate2 or CUDA native threads — probed inside a
  real QThread it returned 1, selecting `fork` in exactly the situation the
  check existed to prevent.
- **Cancellation reaches long stages.** Extraction, Flex, enhancement,
  self-check and segmentation each ran to completion first.
- **Optional-backend timeouts degrade instead of aborting.**
  `subprocess.TimeoutExpired` is not a `RuntimeError`/`OSError`, so it flew past
  every caller's fallback and destroyed a finished project for an optional
  stage.
- **A padded clip whose length cannot be restored is discarded**, not published
  — a 12-second file standing in for a 1.75-second clip desynchronises
  everything after it.
- **Ambience skips silent clips and survives per-clip failures.** One silent
  b-roll clip raised outside the batch's `try` and threw away the whole export
  after hours of rendering.
- **Resemble staging resolves its symlinks.** A relative target produced a
  directory of dangling links that every existence check passed.
- **The system check survives its own diagnostics.** An ffmpeg that hung or was
  not executable took the whole report down; disk space is now measured on the
  output, cache and temp filesystems rather than `/`.
- **Configuration is validated once, up front** (`WhisperSyncConfig.validate`).
  `seed_bin_width=0`, the string `"false"` for a boolean, `NaN`, and unknown
  enum values were all accepted and surfaced hours later as strange results.
  Config errors are now a usage error (exit 2) with a message naming every
  problem at once.
- **The GUI remembers all recorders**, and browse/drop/restore share one
  handler; missing paths are named rather than silently dropped.

#### Found during the verification pass

- **Voice segmentation re-introduced the float-clipping defect.** The
  segment cutter chose its codec from bit depth alone, so a float voice
  monolith was cut to `pcm_s32le` — hard-clipping every sample above full
  scale at the very last step, after the render path had carefully preserved
  it. Every codec-selection site now goes through the format-aware helper.
- **The self-check repair map was not gated.** A local fit over a handful of
  anchors inside one flagged span could produce an implausible clock ratio and
  then re-render that span at the wrong tempo — replacing a defect the user
  could hear with one they could not explain. It now faces the same clock-ratio
  and residual bar as any other map, and declines rather than guessing.
- **The two negative-offset clamps now warn instead of absorbing silently.**
  The plan's origin is normalised upstream so they are unreachable, but a clamp
  that quietly discards a requested offset is exactly how the original defect
  hid; if one is ever reached again it says so.

#### Packaging and performance

- **`--verify` works from an installed wheel.** Its implementation lived in
  `tools/`, which package discovery does not ship, so it raised
  `ModuleNotFoundError` at the END of a completed sync run. It moved to
  `whispersync/engine/verify.py`; `tools/verify_sync.py` is a thin CLI wrapper.
- **The GUI stylesheet ships**, declared as package data and loaded via
  `importlib.resources`; the wheel silently had no theme.
- **`setuptools>=77.0.3`** — the declared floor of 68 cannot parse this
  project's own metadata.
- **CI tests what ships**: a job builds the wheel, installs it into a clean
  environment and smoke-tests entry points, late imports and package resources
  from outside the checkout; plus Python 3.13 and a Windows/macOS import smoke.
- **Optional backends are found on any platform** — `Scripts/` as well as
  `bin/`, an explicit `WHISPERSYNC_SEP_VENV` path, and the user data directory,
  with the executable bit actually checked. `requirements-sep.txt` pins the
  separation stack.
- **Setup scripts no longer destroy a working environment** before the
  replacement is proven: preflight checks first, old environment moved aside
  and restored on any failure.
- **Camera transcripts hit the cache.** The key was built from the throw-away
  scratch WAV, whose path and mtime differ every run, so the expensive
  transcription was always redone and every entry was written once and never
  read.
- **Cache failures cost one re-transcription, not the run.** Invalid UTF-8
  raised out of the pipeline; writes are now atomic and validated
  (schema, finite times).
- **The recorder is decoded once per Boundary Flex pass**, not once per clip
  (~230 MB per hour at float32, plus an ffmpeg pass, per repetition), and
  analysis arrays are float32.
- **The GPU model is released before the CPU fallback loads** — both were alive
  at once, on a machine that had just run out of memory.
- **Resemble Enhance processes in overlapping chunks**, bounding peak memory
  independently of clip length.
- **ML subprocess output is streamed to disk** rather than buffered whole in
  the parent (`engine/proc.py`).
- **The full-reference re-match is bounded** — `difflib` is ~quadratic on
  repetitive tokens, and the unbounded retry ran on the very clips where it was
  slowest.

### Fixed — a 71-clip field run lost every ambience track to one 1.75 s clip

A full episode (`/srv/storage/files/rudnikon`, 71 camera clips) finished with
"Ambience batch separation failed … no 'Instrumental' output was found" and no
ambience lane at all, leaving 70 successfully separated WAVs on disk under
names like `tmp0plj448a_(Instrumental)_melband_roformer_inst_v2.wav` that no
editor could match back to a clip. Root cause and fixes:

- **audio-separator swallows per-file failures.** `separator.py::separate()`
  catches every per-file exception, logs it, and still exits 0 — so a batch
  where one input died looked like a clean success. The dead input here was
  `DJI_0762.MOV` (1.75 s): below ~10 s the MDX-C/RoFormer path enables
  `override_model_segment_size` and dies with "The size of tensor a (0) must
  match the size of tensor b (76734)". Inputs shorter than
  `separation.MIN_INPUT_SECONDS` are now padded with silence before separation
  and trimmed back to their exact original length afterwards.
- **One bad clip no longer costs the whole batch.** `run_separator_batch` used
  to raise if *any* input was missing an output, which threw away ~30 minutes
  of GPU work and the other 70 clips' ambience. It now returns what it did
  produce, retries the missing files individually (a failure can be transient),
  and only raises if the batch produced *nothing*. Each skipped clip is named
  in the log with the reason recovered from the separator's stderr, and the
  pipeline reports "no ambience for N of M clip(s)" as a warning.
- **Separator inputs are named after their clip.** The pipeline fed the
  separator anonymous `tempfile` WAVs, and the separator derives output names
  from input names — hence the unusable `tmp…` leftovers. Camera audio is now
  extracted as `DJI_0762.wav` into a scratch dir, so even a partially failed
  run leaves identifiable files behind.
- **Output-name matching now mirrors the separator's own sanitisation**
  (invalid chars → `_`, runs of `_` collapsed, leading/trailing `_. ` stripped)
  instead of approximating it by stripping trailing separators.

### Fixed — unusable `voice_enhance` modes are reported before the run, not after

The same run ended with "Voice enhancement (resemble) failed (… not available
yet in this build)" *after* several hours of transcription and rendering.
`run_pipeline` now pre-flights both optional environments (voice enhancement
and the ambience separator) in its first seconds and warns then, while the run
can still be cancelled and the setup fixed. Modes with no backend in this build
(`sgmse_denoise`/`sgmse_dereverb`/`reuse`) are greyed out in the GUI dropdown
and are not restored from a saved config.

### Added — `voice_enhance=resemble` is now a working backend

Resemble Enhance is driven through its Python API from `.sep-venv` (where it is
`pip install`-ed alongside audio-separator). Its own `resemble-enhance` console
script is deliberately bypassed: as of torchaudio 2.9 its `torchaudio.load` /
`save` delegate to TorchCodec, which isn't installed there, so the CLI dies with
an `ImportError` before reading any audio — the API itself is pure tensor work
and is unaffected. WhisperSync does the file I/O with `soundfile`, prefers the
weights already on disk (upstream's `download()` runs a `git pull` on every call
and fails offline), runs the whole shoot in one process (one model load), and
tolerates per-file failures. It is a mono model; `conform_wav_to` puts each
result back to the original's exact duration/rate/channels.

### Fixed — enhanced-output name collision across cameras

`_conform_to_originals` named every output `<stem>_enhanced.wav`, so two clips
with the same filename from different cameras produced one file that was then
spliced over *both* — one clip silently getting the other's audio. Names are
de-duplicated now, as are the ambience separator's per-clip inputs.

### Added — Voice Enhancement (`--voice-enhance`)

A listening test compared 6 third-party speech-enhancement variants of the rendered voice monolith. Rather than pick one winner, the new opt-in `voice_enhance` config field (`off` by default) exposes all of them as a per-project choice, wired into the pipeline as a stage that runs right after rendering and **before** self-check (so self-check validates whatever audio the user actually gets):

- **`denoise`** / **`denoise_dereverb`** — Mel-Roformer denoise, optionally chained with a De-Reverb pass. Reuses the existing `.sep-venv`/audio-separator stack (same environment as `--ambience-track`); fast, no new dependencies. Implemented in the new `engine/enhance.py`, sharing `engine/separation.py`'s batching/output-matching logic (generalized from a hard-coded "Instrumental" stem to an arbitrary `stem` parameter via the new `run_separator_batch`).
- **`resemble`**, **`sgmse_denoise`**/**`sgmse_dereverb`**, **`reuse`** — surfaced in config/CLI/GUI/docs as selectable modes, but not yet backed by a working engine; selecting one raises a clear "not available yet in this build" error rather than silently no-opping. (`resemble` was implemented later in this same Unreleased block — see "Added — `voice_enhance=resemble` is now a working backend" above.) Two real gaps found while researching the integration, left open: Resemble Enhance needed undocumented upstream compatibility patches (only 1 of a referenced set could be located); NVIDIA RE-USE's model is NSCLv1 (noncommercial-only) and its inference code is all-rights-reserved, so it can only ever be orchestrated against a user-built Docker image and a user-supplied clone of NVIDIA's own source — never vendored into this repo.
- Every backend's output is conformed back to the exact duration/sample-rate/channel-count of the pre-enhancement audio (new `timestretch.conform_wav_to`) before it replaces the monolith — a third-party tool's own native rate, a mono-only model, or a few samples of resampling drift would otherwise desync the timeline. A missing environment or a backend failure is reported as a warning and the unenhanced monolith is kept, the same non-fatal pattern as `ambience_track` on a missing `.sep-venv`.
- CLI: `--voice-enhance {off,denoise,denoise_dereverb,resemble,sgmse_denoise,sgmse_dereverb,reuse}`, `--reuse-source-dir`. GUI: a "Voice enhancement" dropdown next to the Self-check one. `system_check.py` reports which backends' environments are set up. Full pros/cons documented in the GUI Help tab and in the README's new "Voice Enhancement" section.

### Fixed/Changed — self-check hardening after a real-footage QA run

A full review + field run (6.9-min DJI clip against the 93-min recorder,
`--self-check repair --detect-retakes --verify`) surfaced three real bugs in
the freshly-added repair path and showed the detection thresholds were far
too trusting of Whisper word timings on real (echoey) camera audio:

- **Repair rendered silence (coordinate-space bug):** `_repair_span` fed
  `_sentence_pieces` a window of the clip while its planning math places
  sentences at ABSOLUTE clip-local target times — for a span at e.g. 150s
  the "repaired" chunk began with ~150s of lead silence and was trimmed to
  the window length, i.e. pure silence. The original integration test
  checked only duration/channels and passed anyway; it now verifies window
  CONTENT (RMS + GCC-PHAT lag vs the original, plus a nonzero-offset-map
  variant), and `_repair_span` hands `_sentence_pieces` a window-relative
  alignment map (offset and anchor cam-times shifted by −window-start).
- **Repaired monolith vanished before export:** the repaired file lived in a
  `whispersync_repair_*` scratch dir that the pipeline's `finally` deletes,
  so the FCPXML would reference a path that no longer existed. The repair
  now `os.replace`s the original monolith in place; `aclip.path` never
  changes.
- **Local acoustic re-check probed the wrong recorder spot:** it ignored the
  alignment's offset (`rec_guess = cam_mid / k` instead of
  `(cam_mid − offset) / k`) and used a wider recorder window that gcc_phat
  silently truncates from the START, biasing the measured lag by the width
  difference. Both fixed; the probe also now decodes only the needed window
  (`load_mono16k_track(start_s=, duration_s=)`) instead of the entire
  (possibly multi-hour) recorder per span.
- **Two model copies in VRAM:** the main engine's unload was deferred "for
  self-check to reuse", but self-check builds its OWN engine with its own
  mode — during self-check both models sat in VRAM. The main engine now
  always unloads before rendering; self-check loads fresh (one reload/run).
- **Splice boundaries snap to camera word gaps** (reusing seam-snap), so the
  re-rendered chunk's edge fades land in room tone instead of mid-word.
- **Detection discriminators (the QA run's main lesson):** on a clip whose
  measured realized lag was 8.5 ms median / 23 ms p90 (69/69 confident
  GCC-PHAT windows — i.e. every flagged span was a false positive), the old
  detector flagged 10 spans, including a "5.9 s shift" that was difflib
  matching a repeated phrase to its other occurrence. Word deltas now take
  the minimum over start/end edges (echo smears onsets by 300-500 ms on the
  camera track even in perfect sync; a real shift moves both edges), and a
  large delta is discarded when the same token exists on the camera side at
  the right time (wrong-occurrence match, not a defect). Defaults
  recalibrated to the softest zero-false-positive values on that material:
  `self_check_min_run_words` 3→5, `self_check_shift_threshold_s` 0.25→0.35,
  `self_check_min_content_words` 3→5.

### Added — post-render self-check (content diagnostics + optional repair)

- **`self_check_mode` / `--self-check {warn,repair}` / GUI "Self-check"
  dropdown (Off / Warn only / Warn + auto-repair)** (off by default): after a
  clip's voice monolith is rendered, re-transcribes it with Whisper and
  compares its words against the camera clip's OWN transcript (already
  computed during alignment), flagging spans where content or timing diverge
  beyond normal cross-run Whisper jitter. Complements `--verify`'s GCC-PHAT
  acoustic lag measurement, which can only see *where* two waveforms
  correlate and is blind to CONTENT defects (a dropped/duplicated word, a
  piece built from the wrong recorder span) — those can still show a small
  measured lag if enough surrounding audio still lines up.
- Detection (`whispersync/engine/self_check.py`) matches normalized tokens
  between the rendered and camera transcripts with the same difflib approach
  used for anchor matching, then judges two independent signals: a sliding
  window over consecutive matched word-pairs flags a `shifted` span when its
  median timing delta exceeds `self_check_shift_threshold_s` (default 0.25s,
  chosen to sit above normal ±50-100ms per-word Whisper jitter) over at least
  `self_check_min_run_words` (default 3) words; a long replace/insert/delete
  run in the same diff (at least `self_check_min_content_words`, default 3)
  flags a `content` mismatch. The sliding window (rather than one greedy run
  merged across the whole matched sequence) exists specifically so a
  genuinely shifted tail can't be diluted below threshold by a long
  well-synced prefix sharing the same run — an early version of the
  algorithm had exactly this bug on a synthetic long-prefix/short-shifted-tail
  case caught during testing.
- **`repair` mode** (`self_check.realign_span` + `pipeline._repair_span`): a
  single mechanism handles BOTH span kinds, because an ffmpeg render is
  deterministic — re-rendering the exact same recorder span verbatim would
  reproduce a content defect byte-for-byte, so only re-deriving where in the
  recorder a stretch of speech actually comes from can fix either a `shifted`
  or a `content` span. For each flagged span, `realign_span` re-aligns just
  that neighbourhood (span ± `REPAIR_CONTEXT_S`=6s of context): first a
  transcript re-match (the same normalize+difflib+RANSAC approach as the
  whole-clip alignment, windowed to the span), falling back to a single local
  GCC-PHAT cross-correlation (reusing the acoustic-fallback/Boundary-Flex
  primitives) when too few words are nearby to trust a re-match. The repaired
  stretch is re-planned with the same sentence-wise piece logic as the main
  render (`_sentence_pieces`), rendered, and spliced into the existing
  monolith by cutting out `[span.start-margin, span.end+margin]` and
  reassembling — everything outside that window is untouched byte-for-byte.
  The monolith is re-transcribed and re-checked once after all of a clip's
  spans are repaired, so a span that couldn't be confidently re-aligned (or
  still doesn't match after the attempt) is reported exactly like a
  `warn`-mode finding instead of risking a worse edit.
- `warn` mode (mirrors how `detect_retakes` shipped as a pure detector first)
  remains available and is the safer starting point: findings become
  `warnings` entries naming the affected clip, span, and kind, with no
  automatic repair.
- Self-check transcription is a deliberate second Whisper pass, independent
  of the main `transcribe_mode` (`self_check_transcribe_mode`, default
  `fast`). The main engine is unloaded before rendering as usual; self-check
  then loads its own engine — one model reload per run, and never two copies
  of the model in VRAM at once (an earlier revision deferred the main
  engine's unload "for self-check to reuse", but self-check builds its own
  engine with its own mode, so that only doubled VRAM for nothing).

### Added — retake detection (Final Cut auditions)

- **`detect_retakes` / `--detect-retakes` / GUI "Detect retakes" checkbox**
  (off by default): finds lines the speaker re-recorded back-to-back in an
  unedited take (flub, stop, restart — common in lecture/monologue
  recordings) and exports each set of attempts as a Final Cut **audition** —
  a non-destructive stack of alternative clips (press <kbd>Q</kbd> in Final
  Cut to browse takes), with the last attempt active by default. Nothing is
  ever cut or reordered on disk; the editor reviews and picks.
- Detection (`whispersync/engine/retakes.py`) works at the token level, not
  whole-sentence blocks: it scans the recorder's own transcript (already
  computed for sync) for a short run of words (`retake_min_words`, default 4)
  that repeats verbatim within `retake_max_gap_s` (default 6.0s) of when it
  was first spoken. A match is extended to its maximal common span in both
  directions before being judged, so the same underlying repeat is decided
  consistently regardless of which token offset within it was tried first —
  an earlier per-candidate-local-gap design gave different answers depending
  on scan order and produced false positives on unrelated later callbacks to
  the same short phrase. Consecutive restarts of the same line chain into one
  group (2+ attempts); the final attempt's own extent uses the same
  sentence-pause threshold the renderer uses (a more lenient threshold was
  tried first but let ordinary speech pauses minutes later swallow the
  keeper into an unusably long take — non-destructive audition export makes
  "keeper truncated a bit early" a far cheaper failure mode than "keeper
  three minutes long").
- FCPXML export (`whispersync/engine/export.py`) renders each retake group as
  an `<audition>` anchored under the connected voice clip (a valid
  `anchor_item` in the FCPXML 1.8+/1.9 DTD, confirmed against the public DTD
  entity declarations), with the keeper as the first/active child and the
  discarded attempts as alternates — verified structurally valid and
  round-tripped through `validate_fcpxml`.
- Validated on the real POS-vyp26 dataset transcript (93 minutes): found 6
  plausible retakes, including one where the true repeated span begins
  mid-sentence after an unrelated lead-in phrase — confirming the
  match-extension approach recovers the correct span rather than only
  whole-phrase-aligned repeats.
- Intentionally the first tier of a two-tier design: an optional LLM refiner
  (`refine_retakes`, currently a no-op seam) is planned to catch paraphrased
  restarts an exact token match misses and judge which take was best
  delivered, mirroring Podcast Reels Forge's local llama.cpp moment-scoring.

### Added — voice segmentation for NLE-side re-sync

- **`voice_segment_minutes` / `--voice-segment-minutes` / GUI "Voice file
  split" combo (Monolith · 1 · 2 · 3 · 5 · 10 min)**: optionally split each
  rendered voice WAV into ~N-minute segments, with every cut snapped to the
  quietest moment within ±15 s of its nominal mark so a boundary never lands
  inside speech. Segments are cut PCM→same-PCM (bit-identical; they
  concatenate back into the monolith exactly) and each is placed at its own
  timeline offset in the FCPXML. Rationale: FCPX/Resolve's own audio
  synchronization aligns each audio item once as a whole — with one monolith
  per clip, residual intra-clip drift becomes audible doubling a minute in;
  with segments the NLE re-aligns every few minutes and the drift resets at
  each boundary. Default remains one continuous file per clip.

### Changed — field-validated defaults

- **`ambience_track` now defaults to ON** (validated on real shoots; skipped
  with a warning when `.sep-venv` isn't set up — and the GUI checkbox now
  unchecks itself in that case instead of silently requesting the
  impossible). `boundary_flex=true` and `timebase_source="camera"` remain
  the defaults, now confirmed as the recommended production configuration.

### Changed — sentence-wise Hybrid rendering (stutter/micro-repeat elimination)

A real 8-clip run on strategy 3 still produced stutters and micro-repeats.
A code audit found three independent artifact generators in the piecewise
render path, and industry research (PluralEyes: one constant speed conform per
file; Revoice Pro / academic clock-drift work: continuous warp, never
chunk-cut speech) confirmed the design direction:

- **Strategy 3 is now sentence-wise.** The recorder is segmented into
  sentences by its own transcript (a pause ≥ `phrase_gap_threshold` ends a
  sentence — the same segmentation the SRT export uses); pieces alternate
  [pause][sentence][pause]…, cutting ONLY inside real pauses. Each
  sentence piece is conformed at the smoothed local drift rate (a
  transparent resample — a fraction of a percent), and each pause piece
  stretches however much is needed to land the next sentence exactly on
  target: placement error dies in every pause instead of reaching speech.
  Previously "Hybrid" cut roughly every second at raw anchor positions,
  turning Whisper's ±50–100 ms word-timing jitter into ±5–10 % tempo
  wobble between sub-second pieces.
- **Boundary Flex can no longer create micro-repeats.** It used to slide a
  piece's whole read window without touching the neighbour — a −80 ms
  nudge played the same 80 ms of recorder content twice across the seam
  («подга-га-товил»). A nudge now moves the BOUNDARY: the previous piece's
  duration absorbs the shift (its factor recomputed so downstream timing
  is untouched), the nudged piece keeps its own tempo, and content stays
  contiguous by construction (tested invariant).
- **Seam-snap moves both sides of a breakpoint** (strategy 2): moving only
  the recorder side changed one neighbour's input length while both output
  lengths stayed put, kicking adjacent tempo factors apart by up to ±30 %
  at every snapped seam — an audible tempo see-saw. The camera side now
  moves with it, scaled by the clip's rate, so factors stay at the true
  drift rate.
- Anchor targets and speech rates now come from a **smoothed drift map**
  (tricube-weighted local regression over ±30 s of anchors, global-line
  fallback) instead of raw per-anchor timestamps.

### Fixed

- **Ambience separation no longer fails on unlucky temp-file names.**
  audio-separator normalizes the input's base name when building its output
  name (observed: input `tmpsj40fum_.wav` → output
  `tmpsj40fum_(Instrumental)_...`, trailing underscore swallowed), so the
  exact-name prediction missed it and the whole batch was discarded with
  "Separator reported success but no instrumental output was found".
  Outputs are now matched by normalized base-name equality (exact, so a
  stem that is a prefix of another can't cross-match), verified against the
  real failed run's files.
- **GUI log flooding during camera-clip transcription.** Every transcription
  progress tick re-sent the clip's file name as a message, and the log
  printed each one — dozens of identical `[INFO] DJI_0829.MOV` lines per
  clip. Progress ticks now carry no message (the clip is announced once),
  and the GUI worker additionally drops consecutive duplicate messages.
- **Auto-strategy no longer cries "non-linear" on essentially every
  recording.** The old heuristic compared clock rates between CONSECUTIVE
  anchor pairs — two anchors half a second apart turn Whisper's ±50–100 ms
  word-timing jitter into absurd local-rate "spreads" (a real run printed
  1865‰), and its threshold had mismatched units, so any residual above the
  linear gate triggered a strategy-2 recommendation. Non-linearity is now
  detected by fitting a separate least-squares clock ratio to each half of
  the clip (only when each half has enough anchors over enough time) and
  comparing the halves — constant-rate-but-noisy recordings correctly fall
  through to the Hybrid recommendation.

- **The model no longer *appears* to re-download on every start.** The
  engine now checks the disk first: a model already present (a local
  CTranslate2 directory or a complete Hugging Face cache snapshot) is
  loaded directly from its local path — fully offline, with a status
  message saying "found on disk — loading into memory". Previously the
  model NAME was passed to faster-whisper every time, which re-checked
  the model revision online on each start and printed "Fetching 5 files"
  progress bars over an already-complete cache — indistinguishable from
  the (long finished) multi-GB download happening again. Only a genuinely
  missing model now reports (and performs) the one-time download. Bonus:
  start-up works with no network connection at all.

## [0.1.0] — 2026-07-03

First public release.

### Changed — GitHub publication prep (2026-07-03)

- **License changed from MIT to PolyForm Noncommercial 1.0.0**: WhisperSync
  is now source-available and free for noncommercial use; commercial use
  requires a separate license from the author. `pyproject.toml`, LICENSE,
  CONTRIBUTING, and both READMEs updated accordingly.
- **README overhaul**: English is now the default `README.md` (the Russian
  version moved to `README.ru.md`, replacing the old `README.en.md`
  arrangement). Both are full mirrors covering every feature, the complete
  CLI/config reference, output files, verification tools, architecture, and
  data flow.
- **Fresh GUI screenshots** rendered from the current UI (multi-recorder
  drop zone, recorder-mode picker, Re-run button, settings dialog, populated
  multitrack timeline); the obsolete strategy-4 diagram was removed and the
  strategy/simulator shots regenerated to match the merged 3-strategy model.

### Changed — final plan-completion audit (2026-07-03)

A start-to-finish re-verification of the remediation plan against the code
found and closed the last few gaps:

- **One shared render pool for the whole run** (PROJECT_ANALYSIS.md §6.4):
  pieces of every clip now render through a single process pool, and each
  clip's final assembly overlaps the rendering of the next clips' pieces —
  previously a new pool was created per clip and its single-threaded
  assembly idled every core at each clip boundary.
- **Mid-job cancellation actually works on multi-core renders**: the pooled
  path now polls the cancel event while waiting for each piece (the old
  per-job pool only honoured cancellation on the sequential
  `render_workers=1` path, so cancelling during a large clip silently
  waited for the whole clip to finish). Queued pieces are dropped
  immediately on cancel.
- **Fork safety** (PROJECT_ANALYSIS.md §3.3): the render pool forks only
  when the process is single-threaded (the CLI path); a multi-threaded
  process — the GUI always renders from a Qt worker thread — gets
  forkserver/spawn instead, eliminating the classic
  fork-a-multithreaded-process deadlock risk that Python 3.12+ warns about.
  `main.py` calls `multiprocessing.freeze_support()` for frozen builds.
- **Transcript-cache retention**: new `cache_max_age_days` config field
  (default `0` = keep forever) prunes cache entries older than N days at
  engine startup, capping the previously unbounded growth of
  `~/.cache/whispersync/`.
- Removed the now-dead `apply_pause_ducking` (ducking has been folded into
  the single-pass assembly since the Stage 1 render overhaul; nothing
  called it anymore).

### Added — new features (Stage 7, 2026-07-03)

- **Auto-strategy recommendation**: after a run, the residual/local-drift
  characteristics of the best alignment are checked against the strategy
  actually used; if a different strategy would likely fit better, a warning
  suggests it (transcripts are cached, so re-running is cheap).
- **Acoustic fallback ("Strategy 0")**: a clip with no usable transcript match
  against any recorder (music, background noise, near-silence, a language
  Whisper garbles) now falls back to a coarse GCC-PHAT cross-correlation grid
  scan across the waveforms to estimate offset/K directly — turning a hard
  failure into a still-working (if less precise) placement, as long as the
  same physical audio event reaches both the camera and recorder mic. On by
  default (`acoustic_fallback`).
- **Per-camera AV/lip-sync calibration**: `camera_av_offset_ms` /
  `camera_av_offset_ms_by_camera` (and `--camera-av-offset-ms`) apply a
  constant correction for a camera's own mic-to-lips delay, which no
  acoustic method can see on its own.
- **`--render-master-wav`**: optionally render a single WAV spanning the
  whole timeline (every synced voice clip, and the ambience track if
  enabled, mixed at their timeline offsets over a silent bed) next to the
  FCPXML, for anyone without an NLE.
- **GUI parity with the CLI**: the Recorder Audio drop zone now accepts
  multiple files (drag-drop or Browse) with a `best`/`all` recorder-mode
  picker; a new "Transcription Settings..." dialog exposes
  model/language/device/compute-type/initial-prompt/transcribe-mode; a
  "Re-run with Selected Strategy" button appears after a successful run
  (transcripts are cached, so it skips straight to alignment/render); and
  the status/log now shows "Loading Whisper model..." during a first-time
  (possibly HuggingFace-downloading) model load instead of looking frozen.

### Changed — quality/reliability overhaul (2026-07-02)

A full project audit (`PROJECT_ANALYSIS.md`) found that the rendered voice
track was audibly worse than the recorder source for reasons unrelated to
synchronization, that strategies 3 and 4 had silently become identical, and a
long tail of reliability/cross-platform/dead-code issues. This release fixes
all of it:

- **Bit-perfect render path**: the render no longer forces mono/16-bit —
  every stage (extract, resample-conform/atempo, assemble, pause-duck)
  preserves the recorder's native channel count and a lossless PCM codec
  matching its bit depth. Recorders are normalized to a lossless master WAV
  once up front (fixes non-sample-accurate cutting from lossy sources like
  mp3/m4a, and format mismatches between pieces and lead-silence). A new
  transparent resample ("varispeed") conform replaces `atempo`/WSOLA for the
  small tempo changes real clock drift produces, avoiding WSOLA's phase/
  texture artifacts where they weren't needed. Fades apply only to seams that
  are acoustically discontinuous (e.g. nudged by Boundary Flex), not to every
  piece boundary — the old behaviour carved an audible volume dip into
  otherwise-continuous audio on nearly every seam of the phrase-wise
  strategies. Pause-ducking is folded into the assembly pass instead of a
  second full decode/encode.
- **Strategies 3 and 4 merged**: in the real render path they had become
  byte-identical (old strategy 3 "Silence Padding" promised zero pitch-shift
  but was actually time-stretching every phrase like Hybrid). Now one honest
  "Hybrid" strategy at id 3; `--strategy 4` is a deprecated alias.
- **Seam-snap-to-silence** replaces the old tempo-factor smoothing (which
  fixed the mid-word stutter by averaging atempo factors but let speech drift
  off the picture by up to 1.4s): piece boundaries now snap to the nearest
  recorder inter-word silence, so a seam never lands mid-word without
  touching any piece's tempo factor.
- **Pause ducking** now ducks only where BOTH the camera and recorder tracks
  are actually silent (from full word lists), not gaps between matched
  anchors — a quiet phrase or a word Whisper missed no longer gets
  attenuated as a false "pause".
- **Reliability**: Whisper's VRAM is freed right after alignment instead of
  being held through rendering/ambience separation (a common GPU-OOM cause);
  clips with no audio track no longer abort the whole run; a JSON config with
  a typo'd key now warns instead of silently no-opping; the transcript cache
  key is keyed by the actually-resolved device/compute-type, not the literal
  `"auto"`.
- **Cross-platform**: `file://` URIs use `Path.as_uri()` (the old hand-rolled
  version mis-encoded Windows paths); "Open Output Folder" uses
  `QDesktopServices` instead of Linux-only `xdg-open`.
- **CLI**: `--json` now sends all human-readable output to stderr so the
  report on stdout is clean for piping; added `--version`; exit codes
  distinguish usage errors (2) from run failures (1); fixed `main.py --cli`
  passing `--cli` through to argparse unstripped.
- **Dead code removed**: `engine/dtw.py` (banded-DTW anchor matcher — shelved
  after real-data measurements showed it performed worse than the legacy
  matcher), `acoustic.refine_anchors` (Tier-2 anchor correction, superseded
  once the real float bug was found in the render path, not the anchor
  layer), the entire `plan_clip`-based `SyncStrategy` class hierarchy in
  `strategies.py` (the pipeline only ever read `.name`; real planning lives
  in `pipeline.clip_pieces`), and several unused `timestretch`/`naming`
  helpers.
- **Packaging**: `pyproject.toml` now declares a build backend and its actual
  dependencies (`pip install .` previously installed nothing); dropped the
  unused `pydub`/`ffmpeg-python`; the GUI/CLI dispatcher moved from
  repo-root `main.py` into `whispersync/app.py` so the `whispersync-gui`
  entry point resolves after a wheel install.
- Unified defaults: `default_strategy` (1 → 3) and `boundary_flex`
  (off → on) are now read from `WhisperSyncConfig` by both the CLI and the
  GUI, instead of disagreeing with each other.

See `PROJECT_ANALYSIS.md` for the full technical audit this release addresses.

### Added
- **GitHub-ready project**: real GUI screenshots (rendered via Qt offscreen) in
  the README, bilingual README (RU + `README.en.md`), `CONTRIBUTING.md`,
  `CODE_OF_CONDUCT.md`, `SECURITY.md`, issue/PR templates, a GitHub Actions CI
  workflow (ruff + black + mypy + pytest on 3.11/3.12), and richer `pyproject`
  metadata (urls, classifiers, keywords).
- **Full transcript export (JSON + SRT):** the transcription is computed for
  alignment anyway, so it is now also saved next to the output under
  `output/transcripts/` — one `.json` + `.srt` per recorder and per camera clip,
  in the Podcast Reels Forge format (segments with word-level timestamps +
  sentence groups). Toggle with `save_transcripts` / `--save-transcripts` /
  `--no-save-transcripts` (default on).
- **Filename-aware ordering & preliminary layout:** clips are sorted in natural
  order (DJI_9 < DJI_10 < DJI_100) and consecutive runs (DJI_0838, DJI_0839, …)
  are detected. The timeline is now populated from filenames right after scanning
  (clips laid end-to-end per camera) and the current clip is highlighted while it
  transcribes, so progress is visible before alignment finishes. Clips that fail
  alignment fall back to their filename order, and a warning is raised if matched
  timecodes contradict the filename order (likely misalignment).
- **Full multi-track timeline in the GUI:** one row per camera and per audio
  lane, showing each clip's real position (how far it moved), the applied speed
  change (e.g. `+0.10%`), and live sync status — pending (dashed/dim), working
  (orange outline), done (solid). Updates live as clips are processed; hover a
  clip for offset / duration / in-point / speed / status. Driven by per-clip
  timeline snapshots emitted from the pipeline (`PipelineProgress.clips`).
- **Toggleable seam crossfades:** short equal-power fades at audio segment
  joints declick the seams produced by the Local Time-Stretch strategy. They
  are length-preserving (no extra drift) and can be turned off via the GUI
  checkbox or `--crossfade`/`--no-crossfade` (`crossfade_enabled`, `crossfade_ms`).
- **Production-grade Whisper engine** (settings ported from the Podcast Reels
  Forge pipeline, tuned on an RTX 5060 Ti 16GB):
  - `device`/`compute_type` now default to `auto` — CUDA when available with
    float16 (or int8_float16 on older GPUs), float32 on CPU.
  - Batched GPU inference (`batch_size`, default 16) with an OOM fallback ladder
    (GPU batch → batch/2 → … → CPU) for fast multi-hour transcription.
  - Anti-hallucination decoding: temperature ladder, `condition_on_previous_text`
    off by default, `repetition_penalty`, `no_repeat_ngram_size`, plus
    compression-ratio / log-prob / no-speech thresholds and tuned VAD params —
    kills the "endless Спасибо." loop, yielding cleaner anchors.
  - `fast` (batched) vs `quality` (sequential, context-aware) modes, optional
    `initial_prompt`, `best_of`, `patience`. New CLI: `--batch-size`, `--mode`,
    `--initial-prompt`. `HF_HUB_DISABLE_XET=1` set to avoid HF network hangs.
- **Multiple recorders (different devices):** pass several `--audio-file` flags.
  Each clip is aligned against every recorder; the timeline is placed from the
  best-covering ("primary") recorder. `recorder_mode` / `--recorder-mode`:
  `best` (default) syncs each clip from its strongest recorder on one audio lane;
  `all` places every recorder on its own audio lane (-1, -2, …) for multi-mic /
  multi-speaker setups. (Chunks of one device should just be concatenated first —
  same clock, lossless.)
- **Multi-camera support:** put each camera's clips in its own sub-folder of the
  video directory; each camera is placed on its own lane (1, 2, 3, …) and aligned
  to the recorder independently. The clean audio is synced once from a chosen
  reference camera (`audio_source_camera` / `--audio-source-camera`, default
  auto-picks the best-aligned camera) so it isn't duplicated across angles.
- **Windowed matching for long recordings:** each clip is first coarsely located
  in the (possibly multi-hour) reference by rare-word delta voting, then matched
  precisely only inside a window around that estimate. This avoids O(N²) difflib
  over the full stream and the false matches caused by phrases repeating across
  hours. Falls back to a full search if the window looks weak. Tunable via
  `match_window_margin`, `seed_max_occurrences`, `seed_bin_width`.
- **Strategy 4 — Hybrid (Global + Silence):** each phrase is tempo-corrected by
  the clip's global drift K and then placed at its anchor position with silence
  absorbing the rest. Robust against non-linear drift and near pitch-perfect.
- **Per-clip alignment & timecode-based placement:** every camera clip is now
  aligned to the recorder independently and positioned on the timeline from its
  matched recorder start time. Clips are no longer assumed contiguous — real
  gaps between recordings are preserved (works for arbitrary sources).
- **Timebase source selection** (`timebase_source`, `--timebase-source`, GUI
  dropdown): choose whether FCPXML audio time values snap to the camera (default)
  or recorder sample rate.

### Changed
- Strategies now plan per camera clip (`plan_clip`) instead of one global plan.

### Fixed
- **Critical:** only the first camera clip was transcribed, so on the real
  "one long recorder track + many video files" workflow anchors covered just
  the first clip and drift across the full session was never corrected. The
  pipeline now transcribes the scratch audio of *every* clip and merges it
  onto the concatenated camera timeline (word times shifted by clip offset).
- **Critical:** sync strategies dropped the camera video clips, so exported
  FCPXML contained no video — all three strategies now keep video on lane 1
- **Critical:** Local Time-Stretch / Silence Padding produced clips whose
  `start` pointed past the trimmed segment file length; pipeline now resets
  `in_point` to 0 after extraction/atempo
- Sequence/gap duration now spans the full video extent, not just audio
- FCPXML now emits standard `<asset-clip>` elements (was non-standard
  `<clip ref=...>`) with audio assets declared on the audio sample-rate
  timebase and proper `audioRate`/`audioChannels`
- Strategy and alignment-quality warnings are now propagated into `SyncResult`
- `--no-cache` now actually disables the transcription cache (`config.use_cache`)
- Whisper `unload()` now runs `gc.collect()` + `torch.cuda.empty_cache()` to
  truly free VRAM

### Changed
- Segment extraction uses fast input seeking (`-ss` before `-i`) to avoid
  re-decoding the whole recording for every segment

### Added
- Project scaffolding with full package structure (whispersync/engine/, gui/, widgets/)
- Core data models: Word, Segment, Transcript, Anchor, AlignmentMap, MediaClip, SyncPlan, SyncResult
- Configuration management with JSON config support and CLI overrides
- System check utility (ffmpeg, CUDA, Python, dependencies, disk space)
- Media probing via ffprobe (duration, fps, codecs, sample rate)
- Audio extraction to 16kHz mono WAV for Whisper
- WhisperEngine with lazy model loading and SHA256-based transcription cache
- Anchor matching with SequenceMatcher, uniqueness and monotonicity filters
- RANSAC-based linear regression for clock drift (K) and offset estimation
- Three synchronization strategies:
  - Strategy 1: Global Linear Calibration (single atempo pass)
  - Strategy 2: Local Time-Stretch (per-segment atempo between anchors)
  - Strategy 3: Silence Padding (pitch-safe, speech segments untouched)
- Time-stretch utilities: atempo chain decomposition, segment extraction, silence generation, concatenation, crossfade
- FCPXML generator with rational time values, DOCTYPE, spine/gap/clip structure
- End-to-end pipeline orchestrator with progress callbacks
- PyQt6 GUI with aggressive dark theme (#0A0A0A, #D32F2F, #FF5722)
  - Drag & drop zones for video folder and audio file
  - Strategy selection with visual diagrams
  - Timeline preview with dual-lane visualization
  - Colored log viewer with autoscroll
  - QObject worker with moveToThread pattern and cancellation
  - QSettings persistence for last paths
- CLI headless mode with argparse (--video-dir, --audio-file, --strategy, --json, --verbose)
- PyInstaller spec for --onedir packaging
- Comprehensive test suite: matcher, strategies, timestretch, export (16 tests)

## [0.1.0] - 2026-06-28

### Added
- Initial release
