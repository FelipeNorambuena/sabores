"""Modelo de chat con guion: permite probar el flujo del agente sin API ni costo de tokens."""
from __future__ import annotations

import itertools
import json

from langchain_core.embeddings import DeterministicFakeEmbedding
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

_ids = itertools.count(1)


def llamada(_herramienta: str, **args) -> AIMessage:
    """Respuesta del LLM que pide ejecutar una herramienta (Function Calling)."""
    return AIMessage(content="", tool_calls=[{"name": _herramienta, "args": args, "id": f"call_{next(_ids)}"}])


def js(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


class ModeloGuion(BaseChatModel):
    respuestas: list
    llamadas_recibidas: list = []

    @property
    def _llm_type(self) -> str:
        return "guion"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.llamadas_recibidas.append(messages)
        if not self.respuestas:
            raise AssertionError("El guion se quedó sin respuestas")
        r = self.respuestas.pop(0)
        msg = r if isinstance(r, AIMessage) else AIMessage(content=r)
        return ChatResult(generations=[ChatGeneration(message=msg)])


def embeddings():
    return DeterministicFakeEmbedding(size=64)
