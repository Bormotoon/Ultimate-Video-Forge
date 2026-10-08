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
