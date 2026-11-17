from fakes import ModeloGuion, js
from sabores.planificador import Paso, Planificador, extraer_json, ordenar_pasos


def test_orden_respeta_dependencias_y_prioridad():
    pasos = [Paso(id=1, descripcion="recomendar", prioridad=2, depende_de=[2]),
             Paso(id=2, descripcion="filtrar", prioridad=1),
             Paso(id=3, descripcion="recordar gusto", prioridad=3),
             Paso(id=4, descripcion="guardar perfil", prioridad=1)]
    assert [p.id for p in ordenar_pasos(pasos)] == [2, 4, 1, 3]


def test_dependencia_circular_no_bloquea():
    pasos = [Paso(id=1, descripcion="a", depende_de=[2]), Paso(id=2, descripcion="b", depende_de=[1])]
    assert len(ordenar_pasos(pasos)) == 2


def test_extraer_json_tolera_bloques_de_codigo():
    assert extraer_json('Aquí va:\n```json\n{"modo": "plan"}\n```') == {"modo": "plan"}


def test_plan_invalido_usa_respaldo_y_herramienta_inventada_se_anula():
    llm = ModeloGuion(respuestas=["no sé", js({"objetivo": "x", "pasos": [
        {"id": 1, "descripcion": "hackear", "herramienta": "borrar_base_datos"}]})])
    plan = Planificador(llm, ["buscar_platos"])
    assert plan.crear_plan("hola", "").pasos[0].descripcion == "hola"
    assert plan.crear_plan("hola", "").pasos[0].herramienta is None


def test_router_con_respaldo():
    llm = ModeloGuion(respuestas=[js({"modo": "directo", "motivo": "saludo"}), "???"])
    plan = Planificador(llm, [])
    assert plan.decidir_modo("hola")["modo"] == "directo"
    assert plan.decidir_modo("...")["modo"] == "plan"
