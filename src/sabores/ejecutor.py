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

# Prefijos que las herramientas ponen a su resultado; el orquestador los lee para decidir si replanifica.
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
        self.llm, self.herramientas, self.max_iteraciones = llm, herramientas, max_iteraciones
        self._executors: dict[frozenset, AgentExecutor] = {}
        self.executor = self._construir(None)

    def _construir(self, nombres: frozenset | None) -> AgentExecutor:
        """Executor con todas las herramientas o con un subconjunto (menos esquemas = menos tokens por llamada)."""
        if nombres not in self._executors:
            tools = [h for h in self.herramientas if nombres is None or h.name in nombres]
            agente = create_tool_calling_agent(self.llm, tools, PROMPT_AGENTE)
            self._executors[nombres] = AgentExecutor(agent=agente, tools=tools, max_iterations=self.max_iteraciones,
                                                     return_intermediate_steps=True, handle_parsing_errors=True)
        return self._executors[nombres]

    def ejecutar(self, sistema: str, mensajes: list[BaseMessage], solo: set[str] | None = None) -> ResultadoPaso:
        """`solo` restringe las herramientas visibles para el modelo en este paso (perfil optimizado)."""
        # El último mensaje es la instrucción del paso; el resto es el historial de la conversación.
        *historial, entrada = mensajes
        # Los executors se cachean por subconjunto de herramientas: construirlos es caro y el perfil optimizado
        # usa uno distinto por paso.
        executor = self._construir(frozenset(solo) & frozenset(self.nombres) if solo else None)
        respuesta = executor.invoke({"sistema": sistema, "chat_history": historial,
                                          "input": entrada.content})
        resultado = ResultadoPaso(salida=texto_de(respuesta["output"]))
        # Cada (acción, observación) es una llamada a herramienta: se guarda para la traza y para detectar condiciones.
        for accion, observacion in respuesta["intermediate_steps"]:
            obs = str(observacion)
            # Si el modelo inventa una herramienta, se registra como ERROR en vez de ejecutarla.
            if accion.tool not in self.nombres:
                obs = f"ERROR: la herramienta {accion.tool} no existe."
            resultado.llamadas.append({"herramienta": accion.tool, "args": accion.tool_input, "resultado": obs})
            resultado.observaciones.append(obs)
        # AgentExecutor devuelve este texto al llegar a max_iterations; se convierte en un ERROR que el orquestador entiende.
        if "iteration limit" in resultado.salida:
            resultado.salida = "Se alcanzó el máximo de iteraciones del paso."
            resultado.observaciones.append("ERROR: máximo de iteraciones alcanzado.")
        return resultado


def texto_de(contenido) -> str:
    """Algunos modelos (p. ej. Gemini 3) devuelven el contenido como lista de bloques; se une como texto."""
    if isinstance(contenido, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in contenido)
    return str(contenido)
