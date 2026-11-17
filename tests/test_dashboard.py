"""Humo del dashboard: carga con los logs de ejemplo sin excepciones (usa AppTest de Streamlit)."""
from pathlib import Path

import pytest

streamlit_testing = pytest.importorskip("streamlit.testing.v1")

APP = Path(__file__).resolve().parents[1] / "dashboard" / "app.py"
LOGS = Path(__file__).resolve().parents[1] / "logs"


def test_dashboard_sin_logs_muestra_aviso(tmp_path):
    at = streamlit_testing.AppTest.from_file(str(APP), default_timeout=60).run()
    at.sidebar.text_input[0].set_value(str(tmp_path)).run()
    assert not at.exception
    assert any("No hay logs" in w.value for w in at.warning)


@pytest.mark.skipif(not (LOGS / "resultados.jsonl").exists(), reason="no hay logs generados todavía")
def test_dashboard_con_logs_renderiza_kpis():
    at = streamlit_testing.AppTest.from_file(str(APP), default_timeout=120).run()
    assert not at.exception
    assert len(at.metric) == 5
