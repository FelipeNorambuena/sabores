"""Ejecutor ReAct con Function Calling nativo, armado con AgentExecutor de LangChain (material IL2.1 y IL2.2).

Ciclo: Thought -> Action (tool call) -> Observation -> ... hasta que el modelo responde sin pedir herramientas.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

warnings.filterwarnings("ignore", message=".*was deprecated in LangChain.*")

from langchain_classic.agents import AgentExecutor, create_tool_calling_agent  # noqa: E402
from langchain_core.messages import BaseMessage  # noqa: E402
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder  # noqa: E402

ETIQUETAS_ALERTA = ("ERROR", "AGOTADO", "RECHAZADO", "DERIVADO")

# Mismo prompt que el notebook 3-langchain-agent.ipynb: sistema, historial, entrada y scratchpad del agente.
# El sistema va como variable porque cambia en cada turno (perfil, recuerdos y resumen del cliente).
PROMPT_AGENTE = ChatPromptTemplate.from_messages([
    ("system", "{sistema}"),
    MessagesPlaceholder("chat_history", optional=True),
    ("human", "{input}"),
    MessagesPlaceholder("agent_scratchpad"),
])


@dataclass
class ResultadoPaso:
    salida: str
    observaciones: list[str] = field(default_factory=list)
    llamadas: list[dict] = field(default_factory=list)

    def condicion(self) -> str | None:
        """Primera observación con etiqueta de alerta: es la señal que gatilla la replanificación."""
        for obs in self.observaciones:
            if obs.startswith(ETIQUETAS_ALERTA):
                return obs
        return None


class Ejecutor:
    def __init__(self, llm, herramientas, max_iteraciones: int = 4):
        for h in herramientas:
            # Una herramienta nunca debe tumbar al agente: el error vuelve al modelo como observación
            h.handle_tool_error = lambda e: f"ERROR: {e}"
            h.handle_validation_error = lambda e: f"ERROR: argumentos inválidos: {e}"
        self.nombres = {h.name for h in herramientas}
        agente = create_tool_calling_agent(llm, herramientas, PROMPT_AGENTE)
        self.executor = AgentExecutor(agent=agente, tools=herramientas, max_iterations=max_iteraciones,
                                      return_intermediate_steps=True, handle_parsing_errors=True)

    def ejecutar(self, sistema: str, mensajes: list[BaseMessage]) -> ResultadoPaso:
        *historial, entrada = mensajes
        respuesta = self.executor.invoke({"sistema": sistema, "chat_history": historial,
                                          "input": entrada.content})
        resultado = ResultadoPaso(salida=texto_de(respuesta["output"]))
        for accion, observacion in respuesta["intermediate_steps"]:
            obs = str(observacion)
            if accion.tool not in self.nombres:
                obs = f"ERROR: la herramienta {accion.tool} no existe."
            resultado.llamadas.append({"herramienta": accion.tool, "args": accion.tool_input, "resultado": obs})
            resultado.observaciones.append(obs)
        if "iteration limit" in resultado.salida:
            resultado.salida = "Se alcanzó el máximo de iteraciones del paso."
            resultado.observaciones.append("ERROR: máximo de iteraciones alcanzado.")
        return resultado


def texto_de(contenido) -> str:
    """Algunos modelos (p. ej. Gemini 3) devuelven el contenido como lista de bloques; se une como texto."""
    if isinstance(contenido, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in contenido)
    return str(contenido)
