import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..config import Settings, get_settings
from .result_validator import validate_result
from .storage import read_result, sha256_file, verify_bundle


class DetectorAdapterError(RuntimeError):
    """A safe detector failure; subprocess diagnostics stay private."""


class DetectorCancelled(DetectorAdapterError):
    pass


@dataclass(frozen=True)
class DetectorRun:
    result: dict
    result_path: Path
    bundle_path: Path | None = None


class DetectorAdapter:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

    def paths(self) -> tuple[Path, Path, Path, Path, Path]:
        settings = self.settings
        if not settings.detector_root or not settings.detector_python:
            raise DetectorAdapterError("DETECTOR_ROOT and DETECTOR_PYTHON must be configured explicitly")
        root = Path(settings.detector_root).expanduser().resolve()
        if not root.is_dir():
            raise DetectorAdapterError("detector root does not exist")
        entry = Path(settings.detector_entrypoint).expanduser() if settings.detector_entrypoint else root / "main.py"
        config = Path(settings.detector_config).expanduser() if settings.detector_config else root / "config.json"
        # Use the checkpoint already specified by the protected detector config.
        try:
            model = Path(settings.detector_model) if settings.detector_model else root / json.loads(config.read_text(encoding="utf-8"))["model"]["weights"]
        except (OSError, ValueError, KeyError, TypeError):
            raise DetectorAdapterError("detector model config cannot be read") from None
        for path in (entry, config, model, root / "docs" / "result.schema.json"):
            if not path.is_absolute() or not path.resolve().is_relative_to(root) or not path.is_file():
                raise DetectorAdapterError("detector entrypoint/config/model/schema must exist inside its root")
        python = Path(settings.detector_python).expanduser()
        if not python.is_absolute() or not python.is_file():
            raise DetectorAdapterError("detector Python interpreter must be an existing absolute path")
        return root, python, entry, config, model

    def run(
        self,
        input_path: Path,
        output_dir: Path,
        cancelled: Callable[[], bool] = lambda: False,
        *,
        device: str | None = None,
        show_preview: bool = False,
        extra_args: list[str] | None = None,
    ) -> DetectorRun:
        """Run one analysis.

        ``device``, ``show_preview`` and ``extra_args`` are optional operator
        overrides used by the local demo script. They all default to the
        headless, config-driven behaviour used by the upload pipeline, so an
        HTTP-triggered job is unaffected by them.
        """
        root, python, entry, config, model = self.paths()
        input_path, output_dir = input_path.resolve(), output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        source_hash, checkpoint_hash = sha256_file(input_path), sha256_file(model)
        command = [str(python), str(entry), "--source", str(input_path), "--config", str(config), "--model", str(model), "--show" if show_preview else "--no-show", "--save-output", "--output-dir", str(output_dir), "--json"]
        if device:
            command += ["--device", device]
        if extra_args:
            command += list(extra_args)
        # The child receives OS essentials only, never Backend/database/cloud config.
        sensitive_fragments = ("PASSWORD", "SECRET", "TOKEN", "CREDENTIAL", "PRIVATE_KEY", "DATABASE_URL", "FIREBASE_CONFIG")
        environment = {key: value for key, value in os.environ.items() if not (key.upper() == "DATABASE_URL" or any(fragment in key.upper() for fragment in sensitive_fragments))}
        environment.update(PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        stdout_path, stderr_path = output_dir / "stdout.json", output_dir / "stderr.log"
        process = None
        try:
            with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                process = subprocess.Popen(command, cwd=root, env=environment, stdout=stdout, stderr=stderr, shell=False)
                deadline = time.monotonic() + self.settings.detector_timeout_seconds
                while process.poll() is None:
                    if cancelled():
                        raise DetectorCancelled("analysis cancelled")
                    if time.monotonic() >= deadline:
                        raise DetectorAdapterError("detector exceeded its configured timeout")
                    if stdout_path.stat().st_size > 64 * 1024 * 1024 or stderr_path.stat().st_size > 8 * 1024 * 1024:
                        raise DetectorAdapterError("detector output exceeded configured bounds")
                    time.sleep(0.2)
                if process.returncode != 0:
                    raise DetectorAdapterError("detector could not analyze this video; private diagnostics are available to the operator")
            # --json promises one JSON object; diagnostics belong on stderr.
            result = validate_result(read_result(stdout_path), root)
            if result["source"]["sha256"] != source_hash or result["model"]["sha256"] != checkpoint_hash:
                raise DetectorAdapterError("detector source/checkpoint identity mismatch")
            if sha256_file(input_path) != source_hash:
                raise DetectorAdapterError("input changed during analysis")
            bundle = verify_bundle(result, output_dir)
            return DetectorRun(result, Path(result["artifact_paths"]["json_report"]), bundle)
        except DetectorAdapterError:
            raise
        except (OSError, ValueError, KeyError, TypeError):
            raise DetectorAdapterError("detector output or artifacts failed validation") from None
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
