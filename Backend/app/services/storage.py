import hashlib
import json
from pathlib import Path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def confined_path(root: Path, value: str) -> Path:
    path = Path(value)
    candidate = (root / path).resolve() if not path.is_absolute() else path.resolve()
    if not candidate.is_relative_to(root.resolve()):
        raise ValueError("artifact escaped private job directory")
    # Reject links even if they currently point inside the directory.
    current = candidate
    while current != root.resolve():
        if current.is_symlink():
            raise ValueError("symlink artifacts are not supported")
        current = current.parent
    return candidate


def read_result(path: Path) -> dict:
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("result exceeds storage limit")
    return json.loads(path.read_text(encoding="utf-8"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non-finite JSON value")))


def verify_bundle(result: dict, output_dir: Path) -> Path:
    paths = result["artifact_paths"]
    bundle = confined_path(output_dir, paths["run_directory"] or "")
    json_path = confined_path(output_dir, paths["json_report"] or "")
    report = confined_path(output_dir, paths["human_report"] or "")
    if not all(path.is_file() for path in (json_path, report, bundle / "COMPLETE.json")):
        raise ValueError("published detector bundle is incomplete")
    published = read_result(json_path)
    if published != result:
        raise ValueError("published detector result does not match stdout")
    for candidate in result["incidents"]:
        for evidence in candidate["evidence_frames"]:
            path = confined_path(bundle, evidence["path"])
            if not path.is_file() or sha256_file(path) != evidence["sha256"]:
                raise ValueError("evidence hash mismatch or missing evidence")
    return bundle
