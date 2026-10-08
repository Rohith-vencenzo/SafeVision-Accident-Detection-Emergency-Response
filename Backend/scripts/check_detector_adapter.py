"""Run one supplied local clip through the protected detector adapter."""
from pathlib import Path
import shutil

from app.services.detector import DetectorAdapter
from scripts.common import run_operator


def main() -> None:
    backend = Path(__file__).parents[1]
    source = backend.parent / "test_clip" / "accident_demo.mp4"
    output = backend / "private-data" / "adapter-check"
    output.parent.mkdir(parents=True, exist_ok=True)
    run = DetectorAdapter().run(source, output)
    print(f"detector result validated: schema={run.result['schema_version']} decision={run.result['incident_decision']}")
    shutil.rmtree(output, ignore_errors=True)


if __name__ == "__main__":
    run_operator(main)
