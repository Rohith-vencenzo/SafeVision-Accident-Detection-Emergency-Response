import json
import hashlib
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

from app.config import Settings
from app.services.detector import DetectorAdapter, DetectorAdapterError
from app.services.result_validator import DetectorResultError, validate_result


DETECTOR_ROOT = Path(__file__).parents[2]
RESULT_PATH = DETECTOR_ROOT / "outputs" / "phase5 simulation result.json"


def test_existing_published_result_validates_against_detector_schema():
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    assert validate_result(result, DETECTOR_ROOT)["schema_version"] == "1.0"
    assert result["incident_decision"] == "confirmed_incident"


def test_result_validator_rejects_missing_contract_field():
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    result.pop("run_id")
    with pytest.raises(DetectorResultError):
        validate_result(result, DETECTOR_ROOT)


def test_adapter_uses_argument_list_and_strips_backend_secrets(monkeypatch, tmp_path):
    result = json.loads(RESULT_PATH.read_text(encoding="utf-8"))
    input_path = tmp_path / "input.mp4"
    input_path.write_bytes(b"test video placeholder")
    output_dir = tmp_path / "job"
    result["source"]["sha256"] = hashlib.sha256(input_path.read_bytes()).hexdigest()
    result["model"]["sha256"] = hashlib.sha256((DETECTOR_ROOT / "models" / "yolov11.pt").read_bytes()).hexdigest()
    for incident in result["incidents"]:
        incident["evidence_frames"] = []
    result["artifact_paths"] = {"run_directory": str(output_dir), "json_report": str(output_dir / "result.json"), "human_report": str(output_dir / "report.md")}
    (output_dir / "result.json").parent.mkdir(parents=True, exist_ok=True)
    (output_dir / "result.json").write_text(json.dumps(result), encoding="utf-8")
    (output_dir / "report.md").write_text("test", encoding="utf-8")
    (output_dir / "COMPLETE.json").write_text("{}", encoding="utf-8")
    captured = {}

    class FakeProcess:
        returncode = 0
        def poll(self):
            return 0
        def wait(self, timeout=None):
            return 0
        def terminate(self):
            return None
        def kill(self):
            return None

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        stdout = kwargs["stdout"]
        stdout.write(json.dumps(result).encode("utf-8"))
        stdout.flush()
        return FakeProcess()

    monkeypatch.setattr("app.services.detector.subprocess.Popen", fake_popen)
    settings = Settings(
        detector_root=str(DETECTOR_ROOT),
        detector_python=sys.executable,
        detector_entrypoint=str(DETECTOR_ROOT / "main.py"),
        detector_config=str(DETECTOR_ROOT / "config.json"),
        detector_model=str(DETECTOR_ROOT / "models" / "yolov11.pt"),
        detector_timeout_seconds=30,
        jwt_secret="unit-test-secret-" + "x" * 40,
        token_encryption_key="",
    )
    run = DetectorAdapter(settings).run(input_path, output_dir)
    assert run.result["run_id"] == result["run_id"]
    assert captured["kwargs"]["shell"] is False
    assert Path(captured["kwargs"]["cwd"]) == DETECTOR_ROOT
    assert "--source" in captured["command"] and str(input_path) in captured["command"]
    assert captured["kwargs"]["env"].get("DATABASE_URL") is None
    assert captured["kwargs"]["env"].get("JWT_SECRET") is None
    assert captured["kwargs"]["env"].get("TOKEN_ENCRYPTION_KEY") is None
    assert run.result_path.is_file()


def test_adapter_rejects_missing_detector_root(tmp_path):
    settings = Settings(detector_root=str(tmp_path / "missing"), detector_python=sys.executable)
    with pytest.raises(DetectorAdapterError):
        DetectorAdapter(settings).run(tmp_path / "input.mp4", tmp_path / "job")
