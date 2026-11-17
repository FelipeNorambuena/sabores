from sabores.demo import mostrar_traza


class AgenteFalso:
    traza = [{"evento": "modo", "modo": "plan", "motivo": "x"},
             {"evento": "plan", "objetivo": "o", "pasos": [{"id": 1, "prioridad": 1, "descripcion": "d",
                                                            "herramienta": "h", "depende_de": []}]},
             {"evento": "paso", "paso": {"id": 1, "descripcion": "d"},
              "llamadas": [{"herramienta": "h", "args": {"a": 1}, "resultado": "OK: {}"}]},
             {"evento": "replanificacion", "numero": 1, "condicion": "AGOTADO: x", "nuevos_pasos": []},
             {"evento": "respuesta", "texto": "hola"}]


def test_mostrar_traza(capsys):
    mostrar_traza(AgenteFalso())
    salida = capsys.readouterr().out
    assert "REPLANIFICACIÓN #1" in salida and "🔧 h" in salida
