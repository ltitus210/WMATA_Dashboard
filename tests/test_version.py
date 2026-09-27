from pathlib import Path
import tomllib

from app import VERSION


def test_runtime_and_package_versions_stay_in_sync():
    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(project_file.read_text(encoding="utf-8"))

    assert VERSION == "1.1.0"
    assert project["project"]["version"] == VERSION
