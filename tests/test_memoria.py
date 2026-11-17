from fakes import embeddings
from sabores.config import Config
from sabores.datos import BaseDatos
from sabores.memoria import MemoriaCortoPlazo, MemoriaLargoPlazo


def test_ventana_resume_mensajes_antiguos():
    db = BaseDatos(":memory:")
    corto = MemoriaCortoPlazo(db.con, "s1", ventana_turnos=2)
    for i in range(4):
        corto.agregar("cliente", f"mensaje {i}")
        corto.agregar("agente", f"respuesta {i}")
    mensajes = corto.mensajes()
    assert "mensaje 0" in corto.resumen()          # lo antiguo pasó al resumen
    assert len(mensajes) == 4                       # ventana de 2 turnos (el resumen va en el contexto)
    assert mensajes[-1].content == "respuesta 3"


def test_corto_plazo_sobrevive_reinicio(tmp_path):
    ruta = tmp_path / "s.db"
    MemoriaCortoPlazo(BaseDatos(ruta).con, "s1").agregar("cliente", "soy celíaca")
    retomada = MemoriaCortoPlazo(BaseDatos(ruta).con, "s1")
    assert retomada.mensajes()[0].content == "soy celíaca"


def test_largo_plazo_filtra_por_cliente_y_persiste(tmp_path):
    emb = embeddings()
    memoria = MemoriaLargoPlazo(emb, tmp_path)
    memoria.recordar("prefiere comida picante", cliente="ana")
    memoria.recordar("no le gusta el cilantro", cliente="beto")
    recargada = MemoriaLargoPlazo(emb, tmp_path)   # nueva sesión / reinicio del Colab
    recuerdos = recargada.recuperar("algo picante", cliente="ana")
    assert [d.page_content for d in recuerdos] == ["prefiere comida picante"]


def test_indice_de_carta_se_reutiliza_si_no_cambia(tmp_path):
    db = BaseDatos(":memory:", carta=Config().carta)
    MemoriaLargoPlazo(embeddings(), tmp_path).indexar_carta(db.platos())
    huella = (tmp_path / "carta" / "huella.txt").read_text()
    segunda = MemoriaLargoPlazo(embeddings(), tmp_path)
    segunda.indexar_carta(db.platos())
    assert (tmp_path / "carta" / "huella.txt").read_text() == huella
    assert len(segunda.buscar_platos("quinoa", k=4)) == 4


def test_perfil_acumula_restricciones():
    db = BaseDatos(":memory:")
    db.guardar_perfil("ana", ["gluten"], [], False)
    perfil = db.guardar_perfil("ana", ["lactosa"], ["vegetariano"], True)
    assert perfil["alergenos"] == ["gluten", "lactosa"] and perfil["alergia_severa"] is True


def test_resumen_usa_conversation_summary_memory_con_llm():
    from fakes import ModeloGuion
    llm = ModeloGuion(respuestas=["La clienta es celíaca y pidió el curry."])
    corto = MemoriaCortoPlazo(BaseDatos(":memory:").con, "s1", ventana_turnos=1, llm_resumen=llm)
    corto.guardar_turno("soy celíaca", "anotado")
    corto.guardar_turno("quiero el curry", "pedido 1 creado")
    assert corto.resumen() == "La clienta es celíaca y pidió el curry."
    assert [m.content for m in corto.mensajes()] == ["quiero el curry", "pedido 1 creado"]
