# Security Policy

## Supported Versions

WhisperSync is in active development; security fixes target the latest `main`.

## Reporting a Vulnerability

Please **do not** open a public issue for security problems.

Instead, report privately via GitHub's
[private vulnerability reporting](../../security/advisories/new) (Security →
Advisories → "Report a vulnerability"). Include:

- a description of the issue and its impact,
- steps to reproduce or a proof of concept,
- affected version/commit and your environment.

We aim to acknowledge reports within a few days and will keep you informed about
the fix and disclosure timeline.

## Scope notes

WhisperSync processes your media locally and sends **no telemetry**. Your audio,
video and transcripts are never uploaded anywhere by this project.

### Network access

The previous version of this document promised a single one-time Whisper model
download. That understated the perimeter: the optional stages fetch their own
weights too, which matters for offline and confidential work. Every stage that
can reach the network:

| Stage | When | Destination | Cache location |
|---|---|---|---|
| Whisper model weights | First transcription with a model not yet on disk | Hugging Face | `~/.cache/huggingface` (`HF_HOME`) |
| Separation environment setup | `setup_sep_venv.sh` | PyPI | the `.sep-venv` itself |
| Ambience separation model | First run with `--ambience-track` | the model host `audio-separator` is configured for | `models/separator/` |
| Voice-enhancement models (`denoise`, `denoise_dereverb`) | First run with that mode | same as above | `models/separator/` |
| Resemble Enhance weights | First run with `--voice-enhance resemble` | the upstream project's own repository | its own cache under `.sep-venv` |

Nothing else contacts the network at run time, and **no stage transmits your
media or transcripts**.

### Running fully offline

1. Run each stage once on a connected machine to populate the caches above, or
   copy an existing `models/` directory and Hugging Face cache into place.
2. Set `HF_HUB_OFFLINE=1` so the Whisper backend never attempts a hub request
   (WhisperSync already loads a cached model by local path when one exists).
3. Leave `--ambience-track` and `--voice-enhance` off unless their model files
   are already present under `models/separator/`; both report an unavailable
   backend at the START of a run rather than after rendering.

Model weights are third-party artifacts under their own licences, downloaded
from third-party hosts. This project does not pin or verify their checksums —
if that matters for your threat model, populate the caches yourself from a
source you trust and run offline.

### Data handling

- Generated `.fcpxml` files contain `file://` paths to your media.
- Exported transcripts (`output/transcripts/`) and the transcript cache
  (under the platform cache directory, `transcripts/` subfolder) contain the
  full **text of your recordings**. Treat both as you would the source media.
  `--no-save-transcripts` disables the export; `use_cache: false` disables the
  cache.
- Deleting a project's output folder does not clear the transcript cache; it
  lives in the user cache directory and is pruned only when
  `cache_max_age_days` is set.

### Trust boundaries

- `PATH` (`ffmpeg`/`ffprobe` are invoked by name), the optional `.sep-venv`
  interpreter, and the output directory are all trusted inputs. A writable
  output directory shared with another user is a risk: run WhisperSync with an
  output folder only you can write to.
- Third-party model files are executed as data by their respective frameworks;
  their supply chain is outside this project's control.
