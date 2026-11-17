"""Pruebas de la capa de observabilidad: trazas con session/trace/span, logs JSONL, alertas y privacidad."""
import json

from fakes import ModeloGuion, embeddings, js, llamada
from sabores.agente import construir_agente
from sabores.config import Config
from sabores.observabilidad import LimitadorMedido, enmascarar


def _plan(*pasos):
    return js({"objetivo": "atender al cliente", "pasos": list(pasos)})


def _paso(id, descripcion, herramienta, prioridad=2, depende_de=()):
    return {"id": id, "descripcion": descripcion, "herramienta": herramienta, "prioridad": prioridad,
            "depende_de": list(depende_de)}


def _turno_con_replanificacion(tmp_path):
    planificador = ModeloGuion(respuestas=[
        js({"modo": "plan", "motivo": "restricciones + pedido"}),
        _plan(_paso(1, "Guardar perfil", "guardar_perfil_alimentario", 1),
              _paso(2, "Verificar stock del curry", "consultar_disponibilidad", 2, [1])),
        _plan(_paso(3, "Verificar stock del bowl", "consultar_disponibilidad", 1)),
        "Listo, te recomiendo el Bowl de quinoa y garbanzos.",
    ])
    ejecutor = ModeloGuion(respuestas=[
        llamada("guardar_perfil_alimentario", alergenos=["gluten"], dietas=["vegano"], alergia_severa=False), "ok",
        llamada("consultar_disponibilidad", platos=["Curry verde de lentejas"]), "agotado",
        llamada("consultar_disponibilidad", platos=["Bowl de quinoa y garbanzos"]), "ok",
    ])
    cfg = Config(data_dir=tmp_path, logs_dir=tmp_path / "logs")
    ag = construir_agente("ana", "sesion-1", cfg, llm_planificador=planificador, llm_ejecutor=ejecutor,
                          embeddings=embeddings())
    ag.db.fijar_stock("Curry verde de lentejas", 0)
    ag.responder("Soy celíaca y vegana, quiero un curry. Mi correo es ana@correo.cl")
    return ag, tmp_path / "logs" / "trazas.jsonl"


def test_log_jsonl_tiene_arbol_de_spans_consistente(tmp_path):
    ag, ruta = _turno_con_replanificacion(tmp_path)
    spans = [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines()]

    assert {s["trace_id"] for s in spans} == {ag.trazador.trace_id}      # un turno = una traza
    assert {s["session_id"] for s in spans} == {"sesion-1"}
    ids = {s["span_id"] for s in spans}
    raiz = [s for s in spans if s["parent_span_id"] is None]
    assert [s["tipo"] for s in raiz] == ["turno"]
    assert all(s["parent_span_id"] in ids for s in spans if s["parent_span_id"])   # sin spans huérfanos
    tipos = {s["tipo"] for s in spans}
    assert {"turno", "paso", "llm", "herramienta", "recuperacion", "evento"} <= tipos


def test_turno_resume_llamadas_alertas_y_replanificacion(tmp_path):
    ag, ruta = _turno_con_replanificacion(tmp_path)
    spans = [json.loads(linea) for linea in ruta.read_text(encoding="utf-8").splitlines()]
    turno = next(s for s in spans if s["tipo"] == "turno")["atributos"]

    herramientas = [s for s in spans if s["tipo"] == "herramienta"]
    assert len(herramientas) == 3 and turno["n_herramientas"] == 3
    assert [s["estado"] for s in herramientas].count("alerta") == 1          # el AGOTADO queda marcado
    assert turno["n_alertas"] == 1
    assert turno["n_eventos_replanificacion"] == 1
    assert turno["modo"] == "plan" and turno["derivado"] is False
    assert all(s["duracion_s"] >= 0 for s in spans)


def test_el_log_no_guarda_datos_personales(tmp_path):
    _, ruta = _turno_con_replanificacion(tmp_path)
    texto = ruta.read_text(encoding="utf-8")
    assert "ana@correo.cl" not in texto and "[email]" in texto


def test_enmascarar_email_rut_y_telefono():
    t = enmascarar("Soy ana@x.cl, RUT 12.345.678-5, fono +56 9 1234 5678")
    assert "@" not in t and "12.345.678-5" not in t and "1234 5678" not in t
    assert len(enmascarar("a" * 1000)) <= 401


def test_limitador_mide_la_espera():
    lim = LimitadorMedido(requests_per_second=20, check_every_n_seconds=0.01, max_bucket_size=1)
    for _ in range(3):
        lim.acquire()
    assert lim.espera_total_s > 0.05
