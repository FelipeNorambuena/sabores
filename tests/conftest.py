import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def _logs_aislados(tmp_path, monkeypatch):
    """Los tests nunca escriben en logs/ del repo: ahí viven solo las trazas reales de las corridas."""
    monkeypatch.setenv("LOGS_DIR", str(tmp_path / "logs_tests"))
