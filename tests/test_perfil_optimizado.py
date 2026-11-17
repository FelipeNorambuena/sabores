"""Pruebas del perfil optimizado de la EV3: cada cambio responde a un hallazgo de las trazas del baseline."""
from fakes import ModeloGuion, embeddings, js, llamada
from sabores.agente import construir_agente
from sabores.config import Config, crear_llm
from sabores.datos import BaseDatos
from sabores.ejecutor import Ejecutor
from sabores.herramientas import crear_herramientas
from sabores.planificador import Planificador


def _plan(*pasos):
    return js({"objetivo": "atender", "pasos": list(pasos)})


def _paso(id, herramienta, depende_de=()):
    return {"id": id, "descripcion": f"usar {herramienta}", "herramienta": herramienta, "prioridad": 2,
            "depende_de": list(depende_de)}


def _agente(tmp_path, perfil, planificador, ejecutor):
    cfg = Config(data_dir=tmp_path, logs_dir=tmp_path / "logs", perfil=perfil)
    return construir_agente("ana", "s1", cfg, llm_planificador=planificador, llm_ejecutor=ejecutor,
                            embeddings=embeddings())


def _dos_consultas_iguales():
    planificador = ModeloGuion(respuestas=[js({"modo": "plan", "motivo": "x"}),
                                           _plan(_paso(1, "buscar_platos"), _paso(2, "buscar_platos")),
                                           "Te recomiendo el Bowl de quinoa y garbanzos."])
    return planificador


def test_baseline_ejecuta_la_consulta_repetida(tmp_path):
    ejecutor = ModeloGuion(respuestas=[llamada("buscar_platos", consulta="bowl"), "ok",
                                       llamada("buscar_platos", consulta="bowl"), "ok"])
    ag = _agente(tmp_path, "baseline", _dos_consultas_iguales(), ejecutor)
    ag.responder("recomiéndame algo")
    assert ejecutor.respuestas == []                                    # consumió las 4 respuestas: ejecutó ambos pasos
    assert not [e for e in ag.traza if e["evento"] == "paso_omitido"]


def test_optimizado_omite_la_consulta_repetida(tmp_path):
    ejecutor = ModeloGuion(respuestas=[llamada("buscar_platos", consulta="bowl"), "ok"])   # solo alcanza para un paso
    ag = _agente(tmp_path, "optimizado", _dos_consultas_iguales(), ejecutor)
    ag.responder("recomiéndame algo")
    omitidos = [e for e in ag.traza if e["evento"] == "paso_omitido"]
    assert len(omitidos) == 1 and omitidos[0]["paso"] == 2
    assert ejecutor.respuestas == []


def test_optimizado_vuelve_a_consultar_tras_una_condicion_nueva(tmp_path):
    """Si un plato se agota, el estado cambió: la consulta de disponibilidad NO debe omitirse."""
    planificador = ModeloGuion(respuestas=[
        js({"modo": "plan", "motivo": "x"}),
        _plan(_paso(1, "consultar_disponibilidad")),
        _plan(_paso(2, "consultar_disponibilidad")),            # replanificación tras AGOTADO
        "El curry está agotado; ¿quieres que te proponga alternativas?"])
    ejecutor = ModeloGuion(respuestas=[llamada("consultar_disponibilidad", platos=["Curry verde de lentejas"]), "ok",
                                       llamada("consultar_disponibilidad", platos=["Bowl de quinoa y garbanzos"]), "ok"])
    ag = _agente(tmp_path, "optimizado", planificador, ejecutor)
    ag.db.fijar_stock("Curry verde de lentejas", 0)
    ag.responder("quiero el curry")
    assert [e["evento"] for e in ag.traza].count("replanificacion") == 1
    assert not [e for e in ag.traza if e["evento"] == "paso_omitido"]
    assert ejecutor.respuestas == []


def test_el_ejecutor_puede_ver_solo_un_subconjunto_de_herramientas():
    db = BaseDatos(":memory:", carta=Config().carta)

    class Mem:
        def buscar_platos(self, *a, **k):
            return []

        def recordar(self, *a, **k):
            pass

    herramientas = crear_herramientas(db, Mem(), "ana")
    ej = Ejecutor(ModeloGuion(respuestas=["ok"]), herramientas)
    ej.ejecutar("sistema", [__import__("langchain_core.messages", fromlist=["HumanMessage"]).HumanMessage("hola")],
                solo={"buscar_platos", "derivar_a_personal"})
    claves = [k for k in ej._executors if k is not None]
    assert claves == [frozenset({"buscar_platos", "derivar_a_personal"})]
    assert len(herramientas) == 10                                       # el baseline sigue viendo las 10


def test_optimizado_pasa_solo_la_herramienta_del_paso_al_ejecutor(tmp_path):
    ejecutor = ModeloGuion(respuestas=[llamada("buscar_platos", consulta="bowl"), "ok"])
    planificador = ModeloGuion(respuestas=[js({"modo": "plan", "motivo": "x"}), _plan(_paso(1, "buscar_platos")), "listo"])
    ag = _agente(tmp_path, "optimizado", planificador, ejecutor)
    ag.responder("recomiéndame algo")
    visibles = {frozenset(h.name for h in ex.tools) for ex in ag.ejecutor._executors.values()}
    assert frozenset({"buscar_platos", "derivar_a_personal"}) in visibles


def test_replanificacion_optimizada_ya_no_ordena_reemplazar_el_plato_agotado():
    for optimizado, presente, ausente in [(True, "NO lo reemplaces", "reemplázalo"), (False, "reemplázalo", "NO lo reemplaces")]:
        llm = ModeloGuion(respuestas=[_plan(_paso(9, "consultar_disponibilidad"))])
        Planificador(llm, ["consultar_disponibilidad"], optimizado=optimizado).replanificar(
            "pedir", [{"paso": {"id": 1, "descripcion": "x"}, "salida": "s", "observaciones": []}], [],
            "AGOTADO: curry")
        texto = str(llm.llamadas_recibidas[0])
        assert presente in texto and ausente not in texto


def test_el_prompt_del_ejecutor_prohibe_la_sustitucion_solo_en_el_perfil_optimizado(tmp_path):
    for perfil, esperado in [("optimizado", True), ("baseline", False)]:
        ejecutor = ModeloGuion(respuestas=["ok"])
        planificador = ModeloGuion(respuestas=[js({"modo": "directo", "motivo": "x"}), "hola"])
        ag = _agente(tmp_path / perfil, perfil, planificador, ejecutor)
        ag.responder("hola")
        assert ("distinto al que el cliente pidió" in str(ejecutor.llamadas_recibidas)) is esperado
        assert ("explícitamente (qué plato y por qué)" in str(planificador.llamadas_recibidas)) is esperado


def test_reasoning_effort_bajo_solo_en_optimizado_y_solo_para_gpt_oss(monkeypatch):
    monkeypatch.setenv("LLM_API_KEY", "clave-de-prueba")
    monkeypatch.setenv("LLM_BASE_URL", "https://api.groq.com/openai/v1")
    opt, base = Config(proveedor="groq", perfil="optimizado"), Config(proveedor="groq", perfil="baseline")
    assert crear_llm("openai/gpt-oss-20b", opt).reasoning_effort == "low"
    assert crear_llm("openai/gpt-oss-20b", base).reasoning_effort is None
    assert crear_llm("mistral-small-latest", opt).reasoning_effort is None


def test_el_router_optimizado_manda_todo_pedido_al_plan():
    for optimizado, esperado in [(True, True), (False, False)]:
        llm = ModeloGuion(respuestas=[js({"modo": "plan", "motivo": "x"})])
        Planificador(llm, ["buscar_platos"], optimizado=optimizado).decidir_modo("Quiero pedir un bowl")
        assert ("PEDIR o registrar un pedido" in str(llm.llamadas_recibidas[0])) is esperado
        assert '{"modo": "directo" | "plan"' in str(llm.llamadas_recibidas[0]).replace("{{", "{")  # el formato JSON sigue intacto
