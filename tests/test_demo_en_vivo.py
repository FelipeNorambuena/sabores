"""La petición en vivo deja logs que el dashboard puede abrir, en una carpeta aparte de las corridas del informe."""
import json

import pytest
from fakes import ModeloGuion, embeddings, js
from sabores.agente import construir_agente
from sabores.demo import peticion_en_vivo
from sabores.observabilidad import metricas as m


def _fabrica(cliente, sesion, cfg):
    planificador = ModeloGuion(respuestas=[js({"modo": "directo", "motivo": "consulta puntual"}),
                                           "Te recomiendo el Bowl de quinoa y garbanzos."])
    return construir_agente(cliente, sesion, cfg, llm_planificador=planificador,
                            llm_ejecutor=ModeloGuion(respuestas=["ok"]), embeddings=embeddings())


def test_peticion_en_vivo_escribe_trazas_y_resultados_etiquetados(tmp_path):
    res = peticion_en_vivo("Soy celíaca, ¿qué me recomiendas?", {"alergenos": ["gluten"], "dietas": ["sin_gluten"]},
                           logs_dir=tmp_path, fabrica=_fabrica)
    assert res["etiqueta"] == "demo-en-vivo" and res["error"] is None
    spans, resultados = m.cargar(tmp_path)
    assert set(resultados["etiqueta"]) == {"demo-en-vivo"}
    assert (spans["tipo"] == "turno").sum() == 1
    assert len(json.loads((tmp_path / "resultados.jsonl").read_text(encoding="utf-8").splitlines()[0])["respuestas"]) == 1


def test_dashboard_abre_la_carpeta_demo(tmp_path, monkeypatch):
    pytest.importorskip("streamlit.testing.v1")
    from pathlib import Path

    from streamlit.testing.v1 import AppTest
    peticion_en_vivo("Hola", logs_dir=tmp_path, fabrica=_fabrica)
    monkeypatch.setenv("SABORES_LOGS_DIR", str(tmp_path))
    at = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py"), default_timeout=120).run()
    assert not at.exception
    assert at.sidebar.text_input[0].value == str(tmp_path)
