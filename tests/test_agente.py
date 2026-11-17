"""Pruebas de flujo completo: planificación, condiciones cambiantes, derivación y continuidad entre sesiones."""
import pytest

from fakes import ModeloGuion, embeddings, js, llamada
from sabores.agente import construir_agente
from sabores.config import Config


def plan(*pasos):
    return js({"objetivo": "atender al cliente", "pasos": list(pasos)})


def paso(id, descripcion, herramienta, prioridad=2, depende_de=()):
    return {"id": id, "descripcion": descripcion, "herramienta": herramienta, "prioridad": prioridad,
            "depende_de": list(depende_de)}


def agente(tmp_path, planificador, ejecutor, cliente="ana", sesion=None, **cfg):
    return construir_agente(cliente, sesion, Config(data_dir=tmp_path, **cfg), llm_planificador=planificador,
                            llm_ejecutor=ejecutor, embeddings=embeddings())


def eventos(ag, tipo):
    return [e for e in ag.traza if e["evento"] == tipo]


def test_plato_agotado_gatilla_replanificacion(tmp_path):
    planificador = ModeloGuion(respuestas=[
        js({"modo": "plan", "motivo": "restricciones + pedido"}),
        plan(paso(4, "Registrar el pedido", "crear_pedido", 2, [3]),
             paso(1, "Guardar perfil celíaca y vegana", "guardar_perfil_alimentario", 1),
             paso(3, "Verificar stock del curry", "consultar_disponibilidad", 2, [2]),
             paso(2, "Filtrar platos seguros", "filtrar_platos_seguros", 1, [1])),
        plan(paso(5, "Verificar stock del bowl de quinoa", "consultar_disponibilidad", 1),
             paso(6, "Registrar pedido del bowl", "crear_pedido", 2, [5])),
        "¡Listo! El curry se agotó, así que registré tu Bowl de quinoa y garbanzos (pedido 1).",
    ])
    ejecutor = ModeloGuion(respuestas=[
        llamada("guardar_perfil_alimentario", alergenos=["gluten"], dietas=["vegano"], alergia_severa=False), "ok",
        llamada("filtrar_platos_seguros", alergenos=["gluten"], dietas=["vegano"]), "ok",
        llamada("consultar_disponibilidad", platos=["Curry verde de lentejas"]), "agotado",
        llamada("consultar_disponibilidad", platos=["Bowl de quinoa y garbanzos"]), "ok",
        llamada("crear_pedido", platos=["Bowl de quinoa y garbanzos"], modalidad="local"), "ok",
    ])
    ag = agente(tmp_path, planificador, ejecutor)
    ag.db.fijar_stock("Curry verde de lentejas", 0)

    respuesta = ag.responder("Soy celíaca y vegana, quiero un curry para retirar en el local")

    orden = [p["id"] for p in eventos(ag, "plan")[0]["pasos"]]
    assert orden == [1, 2, 3, 4]                       # el plan se ordenó por dependencias y prioridad
    replan = eventos(ag, "replanificacion")
    assert len(replan) == 1 and replan[0]["condicion"].startswith("AGOTADO")
    assert ag.db.pedido(1)["platos"] == ["Bowl de quinoa y garbanzos"]
    assert ag.db.perfil("ana")["dietas"] == ["vegano"]
    assert "pedido 1" in respuesta


def test_alergia_severa_rechaza_y_termina_en_derivacion(tmp_path):
    planificador = ModeloGuion(respuestas=[
        js({"modo": "plan", "motivo": "alergia + pedido"}),
        plan(paso(1, "Guardar alergia severa", "guardar_perfil_alimentario", 1),
             paso(2, "Validar la sopa", "validar_pedido", 1, [1]),
             paso(3, "Registrar pedido", "crear_pedido", 2, [2])),
        plan(paso(4, "Pedir un plato seguro", "crear_pedido", 1)),
        "La sopa tiene riesgo de contaminación cruzada. Dejé tu Curry verde de lentejas pendiente: el personal te confirmará (caso 1).",
    ])
    ejecutor = ModeloGuion(respuestas=[
        llamada("guardar_perfil_alimentario", alergenos=["frutos_secos"], dietas=[], alergia_severa=True), "ok",
        llamada("validar_pedido", platos=["Sopa de zapallo y jengibre"]), "rechazado",
        llamada("crear_pedido", platos=["Curry verde de lentejas"], modalidad="local"), "derivado",
    ])
    ag = agente(tmp_path, planificador, ejecutor)

    ag.responder("Tengo alergia severa a los frutos secos, quiero la sopa de zapallo")

    assert eventos(ag, "replanificacion")[0]["condicion"].startswith("RECHAZADO")
    assert eventos(ag, "fin_por_derivacion")
    assert ag.db.pedido(1)["estado"] == "pendiente_confirmacion"
    assert ag.db.derivaciones("ana")[0]["motivo"] == "alergia_severa"


def test_limite_de_replanificacion_deriva_automaticamente(tmp_path):
    reintento = plan(paso(1, "Buscar la pizza", "consultar_plato", 1))
    planificador = ModeloGuion(respuestas=[js({"modo": "plan", "motivo": "x"}), reintento, reintento, reintento,
                                           "No encontré ese plato; un integrante del equipo te contactará."])
    ejecutor = ModeloGuion(respuestas=[llamada("consultar_plato", nombre="Pizza napolitana"), "no existe"] * 3)
    ag = agente(tmp_path, planificador, ejecutor, max_replanificaciones=2)

    ag.responder("Quiero una pizza napolitana")

    assert len(eventos(ag, "replanificacion")) == 2
    assert eventos(ag, "derivacion_por_limite")
    assert ag.db.derivaciones("ana")[0]["motivo"] == "informacion_insuficiente"


def test_fuera_de_dominio_en_modo_directo(tmp_path):
    planificador = ModeloGuion(respuestas=[js({"modo": "directo", "motivo": "fuera de dominio"}),
                                           "Solo puedo ayudarte con la carta de Sabores."])
    ejecutor = ModeloGuion(respuestas=["Declino: no es del restaurante."])
    ag = agente(tmp_path, planificador, ejecutor)

    assert "Sabores" in ag.responder("¿Me das un hola mundo en HTML?")
    assert eventos(ag, "modo")[0]["modo"] == "directo"
    assert not eventos(ag, "plan")


def test_continuidad_entre_sesiones(tmp_path):
    p1 = ModeloGuion(respuestas=[js({"modo": "directo", "motivo": "declaración"}), "Anotado."])
    e1 = ModeloGuion(respuestas=[
        llamada("guardar_perfil_alimentario", alergenos=["lactosa"], dietas=[], alergia_severa=False),
        llamada("recordar_preferencia", dato="Le gusta la comida picante"), "ok"])
    agente(tmp_path, p1, e1, sesion="lunes").responder("Soy intolerante a la lactosa y me encanta lo picante")

    # Otro día: nueva sesión (memoria de corto plazo vacía), mismo cliente
    p2 = ModeloGuion(respuestas=[js({"modo": "directo", "motivo": "x"}), "Te recomiendo el curry."])
    e2 = ModeloGuion(respuestas=["ok"])
    ag2 = agente(tmp_path, p2, e2, sesion="viernes")
    ag2.responder("¿Qué me recomiendas hoy?")

    contexto = eventos(ag2, "contexto")[0]["contexto"]
    assert "lactosa" in contexto and "picante" in contexto
    assert ag2.corto.mensajes()[0].content == "¿Qué me recomiendas hoy?"   # la sesión nueva parte limpia
    assert (tmp_path / "traza.jsonl").read_text().count("\n") == 2
