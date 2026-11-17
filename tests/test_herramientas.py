import pytest

from fakes import embeddings
from sabores.config import Config
from sabores.datos import BaseDatos
from sabores.herramientas import crear_herramientas
from sabores.memoria import MemoriaLargoPlazo


@pytest.fixture
def entorno():
    db = BaseDatos(":memory:", carta=Config().carta)
    memoria = MemoriaLargoPlazo(embeddings())
    memoria.indexar_carta(db.platos())
    tools = {t.name: t for t in crear_herramientas(db, memoria, "ana")}
    return db, tools


def test_filtro_excluye_gluten_que_el_rag_no_excluye(entorno):
    # Hallazgo de la EP1: el RAG recuperó el Wrap (con gluten) para una consulta sin gluten
    _, t = entorno
    salida = t["filtrar_platos_seguros"].invoke({"alergenos": ["gluten"], "dietas": [],
                                                 "candidatos": ["Wrap de pollo y hummus", "Pollo al limón con quinoa"]})
    assert salida.startswith("OK")
    assert "Pollo al limón con quinoa" in salida.split("descartados")[0]
    assert "Wrap de pollo y hummus (contiene gluten)" in salida


def test_alergia_severa_descarta_contaminacion_cruzada(entorno):
    _, t = entorno
    leve = t["filtrar_platos_seguros"].invoke({"alergenos": ["gluten"], "dietas": ["vegano"], "alergia_severa": False})
    severa = t["filtrar_platos_seguros"].invoke({"alergenos": ["gluten"], "dietas": ["vegano"], "alergia_severa": True})
    assert "Buddha bowl de falafel" in leve.split("descartados")[0]
    assert "Buddha bowl de falafel (riesgo de contaminación cruzada con gluten)" in severa


def test_sin_opciones_seguras_deriva(entorno):
    _, t = entorno
    salida = t["filtrar_platos_seguros"].invoke({"alergenos": ["gluten"], "dietas": ["vegano"],
                                                 "candidatos": ["Lasaña vegetariana"]})
    assert salida.startswith("DERIVADO")


def test_validar_pedido_usa_perfil_guardado(entorno):
    _, t = entorno
    t["guardar_perfil_alimentario"].invoke({"alergenos": ["frutos_secos"], "dietas": [], "alergia_severa": False})
    assert t["validar_pedido"].invoke({"platos": ["Ensalada thai de maní"]}).startswith("RECHAZADO")
    assert t["validar_pedido"].invoke({"platos": ["Curry verde de lentejas"]}).startswith("OK")


def test_crear_pedido_bloquea_plato_inseguro_y_falta_direccion(entorno):
    _, t = entorno
    t["guardar_perfil_alimentario"].invoke({"alergenos": ["lactosa"], "dietas": [], "alergia_severa": False})
    assert t["crear_pedido"].invoke({"platos": ["Risotto de champiñones"], "modalidad": "local"}).startswith("RECHAZADO")
    assert t["crear_pedido"].invoke({"platos": ["Curry verde de lentejas"], "modalidad": "delivery"}).startswith("ERROR")
    ok = t["crear_pedido"].invoke({"platos": ["Curry verde de lentejas"], "modalidad": "delivery",
                                   "direccion": "Av. Siempre Viva 742"})
    assert ok.startswith("OK") and '"estado": "confirmado"' in ok


def test_plato_agotado(entorno):
    db, t = entorno
    db.fijar_stock("Curry verde de lentejas", 0)
    assert t["consultar_disponibilidad"].invoke({"platos": ["Curry verde de lentejas"]}).startswith("AGOTADO")
    assert t["crear_pedido"].invoke({"platos": ["Curry verde de lentejas"], "modalidad": "local"}).startswith("AGOTADO")


def test_alergia_severa_deja_pedido_pendiente_y_deriva(entorno):
    db, t = entorno
    t["guardar_perfil_alimentario"].invoke({"alergenos": ["mariscos"], "dietas": [], "alergia_severa": True})
    salida = t["crear_pedido"].invoke({"platos": ["Ceviche de champiñones"], "modalidad": "local"})
    assert salida.startswith("DERIVADO") and "pendiente_confirmacion" in salida
    assert db.derivaciones("ana")[0]["motivo"] == "alergia_severa"


def test_cliente_no_puede_ver_pedidos_ajenos(entorno):
    db, t = entorno
    pedido = db.crear_pedido("otro", ["Curry verde de lentejas"], "local", None, "confirmado")
    assert t["consultar_pedido"].invoke({"pedido_id": pedido}).startswith("ERROR")
