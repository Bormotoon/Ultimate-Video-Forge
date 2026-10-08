"""Optional enhancement and room-tone outputs with explicit failure reports."""

import json
import tempfile
from pathlib import Path

from studio.stages import sync_enhance, sync_separation
from studio.stages.sync_decode import extract_audio_to_wav
from studio.stages.sync_media import pcm_codec_for, probe
from studio.stages.sync_render import conform_wav_to


def process_audio(
    voices: dict[str, Path], cameras: dict[str, Path], directory: Path,
    conf: dict[str, object],
) -> tuple[dict[str, Path], dict[str, Path], tuple[Path, ...]]:
    directory.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    enhanced = dict(voices)
    ambience: dict[str, Path] = {}
    artifacts: list[Path] = []
    warnings = []
    mode = str(conf.get("voice_enhance", "off"))
    if mode != "off":
        try:
            results = sync_enhance.run_batch(mode, list(voices.values()), directory / "voice", root)
            for asset_id, path in voices.items():
                if path in results:
                    enhanced[asset_id] = results[path]
                    artifacts.append(results[path])
                else:
                    warnings.append(
                        f"{asset_id}: enhancement produced no output; original retained"
                    )
        except (RuntimeError, OSError) as exc:
            warnings.append(f"enhancement unavailable; original voices retained: {exc}")
    if conf.get("ambience"):
        try:
            with tempfile.TemporaryDirectory(prefix=".camera-", dir=directory) as temporary:
                decoded = {}
                for asset_id, path in cameras.items():
                    audio = Path(temporary) / f"{asset_id}.wav"
                    extract_audio_to_wav(path, audio, sample_rate=48000, mono=False)
                    decoded[asset_id] = audio
                results = sync_separation.extract_ambience_batch(
                    list(decoded.values()), directory / "room", root,
                    str(conf.get("ambience_model", "model_bs_roformer_ep_317_sdr_12.9755.ckpt")),
                )
                for asset_id, audio in decoded.items():
                    if audio not in results:
                        warnings.append(f"{asset_id}: ambience produced no output")
                        continue
                    info = probe(audio)
                    output = directory / f"{asset_id}-ambience.wav"
                    conform_wav_to(results[audio], output, info.duration,
                                   info.audio_sample_rate or 48000, info.audio_channels or 2,
                                   pcm_codec_for(info))
                    ambience[asset_id] = output
                    artifacts.append(output)
        except (RuntimeError, OSError) as exc:
            warnings.append(f"ambience unavailable: {exc}")
    report = directory / "report.json"
    report.write_text(json.dumps({"schema_version": 1, "voice_enhance": mode,
                                 "ambience": bool(conf.get("ambience")), "warnings": warnings},
                                indent=2) + "\n", encoding="utf-8")
    artifacts.append(report)
    return enhanced, ambience, tuple(artifacts)
