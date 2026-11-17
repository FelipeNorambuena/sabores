"""Planificación Plan-and-Execute con priorización y replanificación (material 2.3.1).

El LLM propone el plan como JSON; el código lo valida, lo ordena por dependencias y prioridad, y lo
corrige cuando una herramienta informa una condición nueva (plato agotado, pedido rechazado, error).
"""
from __future__ import annotations

import json
import re

from pydantic import BaseModel, Field, ValidationError

from .ejecutor import texto_de


class Paso(BaseModel):
    id: int
    descripcion: str
    herramienta: str | None = None
    prioridad: int = Field(2, ge=1, le=3, description="1 = alta (seguridad), 2 = media, 3 = baja")
    depende_de: list[int] = []


class Plan(BaseModel):
    objetivo: str
    pasos: list[Paso]


PROMPT_ROUTER = """Clasifica el mensaje del cliente del restaurante Sabores.
- "directo": saludo, pregunta puntual sobre un plato o fuera de dominio (se responde con 0 o 1 herramienta).
- "plan": requiere varias etapas (p. ej. declarar restricciones + recomendar + verificar stock + pedir).
Responde SOLO JSON: {{"modo": "directo" | "plan", "motivo": "<breve>"}}

Mensaje: {mensaje}"""

PROMPT_PLAN = """Eres el planificador del agente del restaurante Sabores. Descompón la solicitud del cliente en
pasos cortos y ejecutables (máximo 6). Herramientas disponibles: {herramientas}.

Reglas de prioridad:
- Prioridad 1: guardar o aplicar restricciones alimentarias y cualquier paso de seguridad (filtrar, validar).
- Prioridad 2: recomendar, verificar disponibilidad, registrar pedidos.
- Prioridad 3: recordar preferencias u otros pasos opcionales.
- Un pedido SIEMPRE depende de un paso previo de filtrado o validación.

Contexto del cliente:
{contexto}

Solicitud: {mensaje}

Responde SOLO JSON: {{"objetivo": "...", "pasos": [{{"id": 1, "descripcion": "...", "herramienta": "...",
"prioridad": 1, "depende_de": []}}]}}"""

PROMPT_REPLAN = """Eres el planificador del agente del restaurante Sabores. Durante la ejecución apareció una
condición nueva y debes ajustar los pasos pendientes.

Objetivo: {objetivo}
Pasos completados y resultados:
{completados}
Condición detectada: {condicion}
Pasos pendientes actuales:
{pendientes}

Reglas: si un plato está AGOTADO, reemplázalo por otro plato seguro y disponible. Si un pedido fue RECHAZADO,
vuelve a filtrar antes de pedir. Si hubo ERROR por falta de datos, agrega un paso para pedírselos al cliente.
Si no hay alternativa segura, agrega un paso derivar_a_personal. Usa ids nuevos mayores a {max_id}.

Responde SOLO JSON con el mismo formato de plan: {{"objetivo": "...", "pasos": [...]}}"""


def extraer_json(texto: str) -> dict:
    """Extrae el primer objeto JSON de la respuesta del LLM (tolera ```json ... ``` y texto alrededor)."""
    texto = texto_de(texto)
    match = re.search(r"\{.*\}", texto, re.DOTALL)
    if not match:
        raise ValueError(f"La respuesta no contiene JSON: {texto[:200]}")
    return json.loads(match.group(0))


def ordenar_pasos(pasos: list[Paso]) -> list[Paso]:
    """Orden topológico por dependencias; entre pasos listos, gana la prioridad más alta (número menor)."""
    ids = {p.id for p in pasos}
    pendientes = {p.id: p for p in pasos}
    hechos: set[int] = set()
    orden: list[Paso] = []
    while pendientes:
        listos = [p for p in pendientes.values() if all(d in hechos or d not in ids for d in p.depende_de)]
        if not listos:  # dependencia circular: se rompe por prioridad para no bloquear el flujo
            listos = list(pendientes.values())
        siguiente = min(listos, key=lambda p: (p.prioridad, p.id))
        orden.append(siguiente)
        hechos.add(siguiente.id)
        del pendientes[siguiente.id]
    return orden


class Planificador:
    def __init__(self, llm, nombres_herramientas: list[str]):
        self.llm = llm
        self.herramientas = nombres_herramientas

    def _invocar_plan(self, prompt: str, objetivo_defecto: str) -> Plan:
        texto = self.llm.invoke(prompt).content
        try:
            plan = Plan.model_validate(extraer_json(texto))
        except (ValueError, ValidationError):
            # Plan mínimo de respaldo: un único paso que el ejecutor resuelve en modo ReAct
            plan = Plan(objetivo=objetivo_defecto, pasos=[Paso(id=1, descripcion=objetivo_defecto)])
        for p in plan.pasos:  # una herramienta inventada no se ejecuta; el paso queda en manos del ejecutor
            if p.herramienta not in self.herramientas:
                p.herramienta = None
        return plan

    def decidir_modo(self, mensaje: str) -> dict:
        try:
            decision = extraer_json(self.llm.invoke(PROMPT_ROUTER.format(mensaje=mensaje)).content)
            if decision.get("modo") in ("directo", "plan"):
                return decision
        except ValueError:
            pass
        return {"modo": "plan", "motivo": "respaldo: no se pudo clasificar"}

    def crear_plan(self, mensaje: str, contexto: str) -> Plan:
        plan = self._invocar_plan(PROMPT_PLAN.format(herramientas=", ".join(self.herramientas),
                                                     contexto=contexto, mensaje=mensaje), mensaje)
        plan.pasos = ordenar_pasos(plan.pasos)
        return plan

    def replanificar(self, objetivo: str, completados: list[dict], pendientes: list[Paso], condicion: str) -> list[Paso]:
        max_id = max([c["paso"]["id"] for c in completados] + [p.id for p in pendientes] + [0])
        prompt = PROMPT_REPLAN.format(
            objetivo=objetivo, condicion=condicion, max_id=max_id,
            completados="\n".join(f"- {c['paso']['descripcion']} -> {c['salida'][:300]}" for c in completados),
            pendientes="\n".join(f"- {p.model_dump_json()}" for p in pendientes) or "(ninguno)")
        plan = self._invocar_plan(prompt, f"Resolver la condición: {condicion}")
        return ordenar_pasos(plan.pasos)
