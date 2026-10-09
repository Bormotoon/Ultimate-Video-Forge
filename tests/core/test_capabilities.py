import json

from studio.core.capabilities import inspect_compute
from studio.stages.program_encoder import EncoderChoice


def test_compute_recommendations_use_backend_types(monkeypatch) -> None:
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type(
            "Result",
            (),
            {
                "returncode": 0,
                "stdout": json.dumps(
                    {"available": True, "cuda_devices": 1, "compute_types": ["int8_float16"]}
                ),
            },
        )(),
    )
    monkeypatch.setattr(
        "studio.stages.program_encoder.select_encoder",
        lambda mode: EncoderChoice(mode, "h264_nvenc", "passed"),
    )
    report = inspect_compute()
    assert report["recommendations"]["transcribe"]["device"] == "cuda"
    assert report["recommendations"]["transcribe"]["compute_type"] == "int8_float16"
    assert report["recommendations"]["program"]["encoder"] == "nvenc"


def test_missing_backend_recommends_cpu(monkeypatch) -> None:
    monkeypatch.setattr(
        "subprocess.run",
        lambda *a, **kw: type("Result", (), {"returncode": 1, "stderr": "backend absent"})(),
    )
    monkeypatch.setattr(
        "studio.stages.program_encoder.select_encoder",
        lambda mode: EncoderChoice(mode, "libx264", "no nvenc"),
    )
    report = inspect_compute()
    assert report["recommendations"]["transcribe"]["batch_size"] == 1
    assert not report["whisper"]["available"]
