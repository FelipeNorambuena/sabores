"""Utilidades para la demo: mostrar la traza de decisiones y simular condiciones cambiantes."""
from __future__ import annotations

import json
import time


def mostrar_traza(agente) -> None:
    """Imprime, en orden, qué decidió el agente en el último turno (modo, plan, pasos, replanificaciones)."""
    for e in agente.traza:
        tipo = e["evento"]
        if tipo == "contexto":
            print("🧠 CONTEXTO RECUPERADO\n   " + e["contexto"].replace("\n", "\n   "))
        elif tipo == "modo":
            print(f"🧭 MODO: {e['modo']} ({e.get('motivo', '')})")
        elif tipo == "plan":
            print(f"🗺️  PLAN: {e['objetivo']}")
            for p in e["pasos"]:
                print(f"   {p['id']}. [P{p['prioridad']}] {p['descripcion']} -> {p['herramienta']} "
                      f"(depende de {p['depende_de'] or '-'})")
        elif tipo == "paso":
            print(f"▶️  PASO {e['paso']['id']}: {e['paso']['descripcion']}")
            for ll in e["llamadas"]:
                print(f"   🔧 {ll['herramienta']}({json.dumps(ll['args'], ensure_ascii=False)})")
                print(f"      ↳ {ll['resultado'][:220]}")
        elif tipo == "replanificacion":
            print(f"🔁 REPLANIFICACIÓN #{e['numero']} por: {e['condicion'][:160]}")
            for p in e["nuevos_pasos"]:
                print(f"   {p['id']}. [P{p['prioridad']}] {p['descripcion']} -> {p['herramienta']}")
        elif tipo in ("fin_por_derivacion", "derivacion_por_limite"):
            print(f"🧑‍🍳 DERIVACIÓN AL PERSONAL: {e['condicion'][:160]}")
        elif tipo == "respuesta":
            print(f"💬 RESPUESTA AL CLIENTE:\n{e['texto']}")


def responder_con_reintentos(agente, mensaje: str, max_intentos: int = 4, espera_inicial: int = 30) -> str:
    """Igual que en la EP1: si la capa gratuita responde 429 (rate limit), espera y reintenta."""
    for intento in range(max_intentos):
        try:
            return agente.responder(mensaje)
        except Exception as e:
            if "429" in str(e) or "rate" in str(e).lower() or "quota" in str(e).lower():
                espera = espera_inicial * (intento + 1)
                print(f"⏳ Límite de la API alcanzado, reintentando en {espera}s ({intento + 1}/{max_intentos})")
                print(f"   Detalle: {str(e)[:300]}")
                time.sleep(espera)
            else:
                raise
    raise RuntimeError("Se agotaron los reintentos. Espera 1-2 minutos y vuelve a ejecutar la celda.")

def peticion_en_vivo(mensajes, restricciones=None, logs_dir="/content/logs_demo", etiqueta="demo-en-vivo", fabrica=None):
    """Ejecuta una petición libre con el mismo mecanismo de medición de los escenarios y la deja en `logs_dir`.

    Así el dashboard (con SABORES_LOGS_DIR=logs_dir) la muestra completa: trazas, tokens, latencia y seguridad.
    `restricciones` es la verdad del cliente, p. ej. {"alergenos": ["gluten"], "dietas": ["sin_gluten"]}: con ella
    se calcula la violación de seguridad con la regla en código. Sin restricciones, solo se miden tiempos y tokens.
    """
    from pathlib import Path

    from .evaluacion import Escenario, ejecutar_corrida

    if isinstance(mensajes, str):
        mensajes = [mensajes]
    esc = Escenario("peticion_libre", "Petición escrita en vivo durante la demostración", list(mensajes),
                    restricciones=restricciones or {})
    res = ejecutar_corrida(etiqueta, [esc], Path(logs_dir), fabrica=fabrica, repeticiones=1)[0]
    print("\n💬 RESPUESTA AL CLIENTE:\n" + "\n\n".join(res["respuestas"]))
    return res
