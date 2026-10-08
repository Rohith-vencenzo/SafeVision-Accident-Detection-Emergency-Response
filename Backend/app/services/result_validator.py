import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


class DetectorResultError(ValueError):
    """Raised when a detector output is not the documented v1.0 contract."""


def load_result_schema(detector_root: Path) -> dict[str, Any]:
    schema_path = detector_root / "docs" / "result.schema.json"
    if not schema_path.is_file():
        raise DetectorResultError("detector result schema is missing")
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        return schema
    except (OSError, json.JSONDecodeError) as error:
        raise DetectorResultError("detector result schema cannot be read") from error


def validate_result(result: Any, detector_root: Path) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise DetectorResultError("detector output must be a JSON object")
    schema = load_result_schema(detector_root)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(result), key=lambda error: str(list(error.path)))
    if errors:
        location = ".".join(str(part) for part in errors[0].path) or "root"
        raise DetectorResultError(f"detector result failed schema validation at {location}")
    if result.get("incident_decision") == "confirmed_incident":
        if not result.get("incidents"):
            raise DetectorResultError("confirmed result contains no incidents")
        if any(item.get("state") != "CONFIRMED" for item in result["incidents"]):
            raise DetectorResultError("confirmed result contains a non-confirmed incident")
        if result["accident_detected"] is not True:
            raise DetectorResultError("inconsistent confirmation flag")
    elif result["incidents"]:
        raise DetectorResultError("non-confirmed decision contains incidents")
    elif result["incident_decision"] == "no_confirmed_incident":
        if result["accident_detected"] is not False or not result["complete_video_processed"]:
            raise DetectorResultError("negative decision requires complete video coverage")
    elif result["accident_detected"] is not None:
        raise DetectorResultError("inconclusive decision must not assert an accident flag")
    return result
