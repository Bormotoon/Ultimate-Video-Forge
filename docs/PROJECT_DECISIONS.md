# Project decisions

Confirmed by the project owner on 2026-10-08.

- Product name: Ultimate Video Forge.
- Development repository: `/srv/storage/docs/Ultimate-Video-Forge/`.
- License for the combined project: MIT. Original license notices in preserved
  source snapshots remain intact; third-party assets retain their own licenses.
- Immediate acceptance target: the current Linux workstation. Other platforms
  are deferred until the local workflow can be tested end to end.
- Original WhisperSync and Podcast Reels Forge repositories are left untouched.
  No feature freeze or archival changes are applied to them.
- Subtitle editor: system browser first; embedded browser integration later.
- Technology policy: prefer the newest stable releases compatible with the
  actual dependency stack and verified processing workflow. Evaluate Python
  3.14 and current Qt/CTranslate2/torch releases before adopting them; retain
  Python 3.12 as the verified execution baseline until that evaluation passes.
  Optional modules may use separately verified interpreter versions.

## Existing implementation

Implementation and imported source histories currently live in the separate
local repository `/srv/storage/docs/Studio/`, through commit `b95d3c6`.
The destination already contains its own initial documentation commit. Bring
the implementation history into this repository while preserving its existing
documentation and Git remote; do not overwrite either repository or force-push.
The implementation is now merged into the destination with both parent histories
preserved. Existing destination README, MIT license, plan, and remote win conflicts.
The package and command names still need migration from `studio`.

Distribution identity is `ultimate-video-forge`, with `uvf`,
`ultimate-video-forge`, and `uvf-gui` entry points. Keep the internal `studio`
namespace and old commands during migration to avoid invalidating subprocess
entry points and recorded project paths in the same change.

## Autonomous implementation decisions (2026-10-08)

- Continue in dependency order: reliable execution, source decoding, synchronized
  export, then remaining sync modes, editing/program, speakers, Forge stages,
  desktop controls and distribution. Phase 7 remains the follow-up roadmap after
  phases 1-6, as specified by the plan.
- Preserve frozen legacy snapshots until executable parity checks pass; do not
  delete legacy merely because a stage facade exists.
- Optional stage dependencies still determine ordering but cannot prevent export
  when roughcut is disabled. Required dependencies retain failure propagation.
- Forward validated JSON-lines events while workers are alive. Keep artifact
  publication transactional and separate from progress notifications. Bound the
  retained stderr diagnostic tail to avoid unbounded memory use.
- Explicit transcription streams use ffmpeg's absolute stream index (`0:N`),
  consistent with probe/prepare. Decode selected streams to private mono 16 kHz
  PCM; cache identity remains original content plus stream and decoding settings.
- Whisper OOM retries halve batches, then use unbatched decoding with beam 1.
  Never silently switch a requested GPU job to CPU. Other runtime errors propagate.
- Connect synchronized voice outputs in both export formats. FCPXML uses a gap
  bed with connected camera/audio lanes so overlapping cameras do not become
  overlapping primary-storyline clips; mute original camera audio when replaced.
- Apply the same keep ranges to video and synchronized voice in neutral export.
  Include voice/edit artifact checksums in export fingerprints and honor targets
  and FCPXML version. NLE import acceptance remains unverified.
- A passing combined unit suite is not completion of the entire migration.
  Private recordings, overnight operation, clean installers and NLE import require
  recorded evidence before closing their corresponding acceptance gates.

### Production hardening continuation

- Actual roughcut execution requires decoded-energy evidence: remove a candidate
  only when ffmpeg silencedetect covers its entire interior at the configured
  threshold. Apply this to head/tail as well as gaps. Audible untranscribed sounds
  remain retained; missing energy sources fail instead of silently guessing.
- Render the review master in private per-piece intermediates. Resolve each keep
  range against camera coverage and group selection, convert timeline to source
  time through SourcePlacement, and use the camera's synchronized voice WAV when
  available. Reject uncovered ranges rather than silently render the first camera.
- Normalize video dimensions/fps and audio sample rate/layout before concatenation;
  silent cameras get explicit silence. Current joins are duration-preserving hard
  joins; the required crossfade integration remains a tracked task.
- Auto/simple recorder modes now use the preserved accepted matcher and renderer:
  strategy 1 for simple, drift-based selection for auto, configured strategy for
  complex. Render WAV even near unity until safe original-source NLE references
  are proven. Acoustic-first auto remains outstanding.
- Timeline explicitly orders after sync, with sync optional for a single source.
  Derived timeline transcripts are excluded from sync fingerprint inputs to avoid
  recursive invalidation. Include recorder content identity in the render fingerprint.
- Stage failures preserve previous published results, skip required dependents,
  and let independent/optional branches proceed. CLI writes report.json and exits
  nonzero after processing independent branches. Settings reject wrong types rather
  than treating string booleans as enabled flags.

### Retakes and edit review

- Preserve the frozen WhisperSync token restart detector without changing its
  matching algorithm; adapt only imports, configuration and dataclasses. Run its
  eleven original regression cases against the migrated implementation plus four
  direct frozen/migrated parity cases. The original snapshots remain untouched.
- Roughcut enables deterministic retakes by default, keeps the last attempt and
  removes the preceding attempts only when the keeper boundary intersects no word.
  Retake cuts do not require silence across speech intentionally being removed.
- Give each cut a stable ID derived from boundaries and reason. Persist explicit
  acceptance/rejection in edit-overrides.json; apply matching overrides after future
  detector runs. Unknown IDs are rejected by the review command.
- CLI review transactions update edit.json, override storage and the existing
  roughcut artifact checksum together. Preserve the detector fingerprint so review
  does not rerun recognition or detection. Export/program fingerprints include edit
  checksums and roughcut enablement, so only derived outputs require rebuilding.
- Removed-span markers belong to the edited splice. Retained filler markers map
  through EditMap; markers inside removed ranges are omitted. Export both FCPXML
  and XMEML markers. UI playback/audition controls are still outstanding.
- Preserve original camera audio in XMEML as explicit linked channel items,
  disabled when replaced by synchronized voice. Add sequence dimensions and source
  channel metadata. Mixed-rate/source-format acceptance and NLE import remain open.

### Microphone speaker attribution

- Register the speakers stage in the public execution graph between timeline and
  roughcut. Roughcut orders after it but can run when attribution is unavailable.
- Configure explicit channel assignments with `speakers.tracks`, mapping
  `asset_id:zero_based_channel` to a speaker name. Explicit mics mode may use
  separate channels of one recorder. Auto does not assume ordinary stereo means
  two independent lavaliers; multichannel recordings require explicit assignment.
- Decode microphone channels once to 16 kHz float PCM and integrate RMS energy
  into 50 ms timeline bins using each recorder's SourcePlacement, including in
  points, drift and negative offsets. Require placements for every selected track;
  never compare unaligned microphones. Nonlinear per-camera AudioWarpMap attribution
  and pyannote remain separate future integration tasks.
- Classify silent words as unknown instead of overlap. Retain the 6 dB dominance
  rule for cross-bleed and overlap. Several files assigned to the same speaker
  combine by maximum envelope instead of inventing duplicate speaker identities.
- Persist Forge-compatible turn lists and a separate timeline-domain provenance
  report. Fingerprints include selected source content, placements and assignments.

### Sequential processing migration: acoustic alignment

- Preserve the full frozen acoustic algorithm in sync_acoustic.py, replacing only
  configuration/decoder dependencies. Auto/simple use waveform evidence first;
  complex uses text first with waveform fallback. Both paths use the existing
  acceptance gate; ambiguous/insufficient waveform evidence never becomes placement.
- Place camera-only projects in the first camera's clock, then normalize the
  earliest offset to zero. Retain affine clock factors rather than discard drift.
- Multiple recorders share the first recorder reference clock. Independently align
  each recorder and each camera/recorder pair. Fail when common-clock evidence is
  missing; disjoint recorder sessions are not guessed. Choose best voice by residual
  then inlier count; all mode additionally publishes every rendered recorder lane.
- Keep conservative prepare transcript requirements until acoustic evidence is
  integrated into discovery. Rendering multiple recorders still eagerly renders
  candidate lanes before selection; optimization follows correctness tests.
