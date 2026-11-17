"""Observabilidad del agente: trazas (session/trace/span), logs JSONL estructurados y métricas por llamada.

Modelo de datos (material 3.1.1 y 3.2.1):
- session_id: agrupa los turnos de una conversación.
- trace_id: un turno completo (mensaje del cliente -> respuesta).
- span_id / parent_span_id: cada operación dentro del turno (recuperación, paso del plan, llamada al LLM,
  llamada a herramienta). Forman el árbol de ejecución.

Cada span cerrado se escribe como una línea JSON en logs/trazas.jsonl. El código del agente no cambia de
comportamiento: los LLM y las herramientas reciben un callback y los bloques lógicos se envuelven con `span`.
"""
from __future__ import annotations

import json
import re
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.rate_limiters import InMemoryRateLimiter

ETIQUETAS_ALERTA = ("ERROR", "AGOTADO", "RECHAZADO", "DERIVADO")

# Privacidad (IE8): antes de escribir un texto al log se enmascaran datos personales y se acota el largo.
_PATRONES_PII = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"\b\d{1,2}\.?\d{3}\.?\d{3}-[\dkK]\b"), "[rut]"),
    (re.compile(r"\+?\d[\d\s-]{7,}\d"), "[telefono]"),
]


def enmascarar(texto, maximo: int = 400) -> str:
    texto = str(texto)
    for patron, reemplazo in _PATRONES_PII:
        texto = patron.sub(reemplazo, texto)
    return texto if len(texto) <= maximo else texto[:maximo] + "…"


# Intentos HTTP de la llamada al LLM en curso. El cliente de OpenAI reintenta por dentro (429, 5xx) y los
# callbacks de LangChain no lo ven: este hook de httpx registra cada intento y el span lo recoge al cerrarse.
INTENTOS_HTTP: list[dict] = []


def hook_respuesta_http(respuesta) -> None:
    intento = {"status": respuesta.status_code, "retry_after_s": respuesta.headers.get("retry-after"),
               "tokens_restantes": respuesta.headers.get("x-ratelimit-remaining-tokens")}
    if respuesta.status_code >= 400:
        try:
            respuesta.read()
            intento["detalle"] = enmascarar(respuesta.text, 220)
        except Exception:  # el cuerpo no es imprescindible para registrar el intento
            pass
    INTENTOS_HTTP.append(intento)


def crear_cliente_http():
    """Cliente httpx que alimenta INTENTOS_HTTP (se pasa a ChatOpenAI como http_client)."""
    import httpx

    return httpx.Client(event_hooks={"response": [hook_respuesta_http]}, timeout=120.0)


class LimitadorMedido(InMemoryRateLimiter):
    """Limitador de llamadas que además mide cuánto tiempo hizo esperar: separa la espera de la latencia del modelo."""

    espera_total_s: float = 0.0

    def acquire(self, *, blocking: bool = True) -> bool:
        t0 = time.perf_counter()
        try:
            return super().acquire(blocking=blocking)
        finally:
            self.espera_total_s += time.perf_counter() - t0


@dataclass
class Span:
    span_id: str
    parent_id: str | None
    tipo: str
    nombre: str
    t0: float
    inicio: str
    attrs: dict = field(default_factory=dict)


class Trazador:
    def __init__(self, ruta: Path | None = None, sesion: str = "", cliente: str = "", limitador=None,
                 registrar_contenido: bool = True):
        self.ruta = Path(ruta) if ruta else None
        self.sesion, self.cliente = sesion, cliente
        self.limitador = limitador
        self.registrar_contenido = registrar_contenido
        self.eventos: list[dict] = []
        self.trace_id: str | None = None
        self._pila: list[Span] = []
        self._abiertos: dict = {}  # run_id de LangChain -> (Span, espera al abrir)
        self._acum: dict = {}
        self._lock = threading.Lock()
        self.callback = ManejadorCallbacks(self)

    # --- ciclo de vida de spans ---
    def _abrir(self, tipo: str, nombre: str, **attrs) -> Span:
        padre = self._pila[-1].span_id if self._pila else None
        return Span(uuid.uuid4().hex[:12], padre, tipo, nombre, time.perf_counter(),
                    datetime.now().isoformat(timespec="milliseconds"), dict(attrs))

    def _cerrar(self, span: Span, estado: str = "ok", error: str | None = None) -> dict:
        ev = {"session_id": self.sesion, "trace_id": self.trace_id, "span_id": span.span_id,
              "parent_span_id": span.parent_id, "cliente": self.cliente, "tipo": span.tipo, "nombre": span.nombre,
              "inicio": span.inicio, "duracion_s": round(time.perf_counter() - span.t0, 4), "estado": estado,
              "error": enmascarar(error) if error else None, "atributos": span.attrs}
        with self._lock:
            self.eventos.append(ev)
            if self.ruta:
                self.ruta.parent.mkdir(parents=True, exist_ok=True)
                with self.ruta.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        return ev

    @contextmanager
    def turno(self, mensaje: str):
        """Raíz de la traza: un turno completo. Acumula tokens, llamadas y alertas de todos sus spans."""
        self.trace_id = uuid.uuid4().hex[:16]
        self._acum = {"n_llm": 0, "n_herramientas": 0, "n_alertas": 0, "tokens_entrada": 0, "tokens_salida": 0,
                      "tokens_razonamiento": 0, "espera_limitador_s": 0.0, "n_eventos_replanificacion": 0, "n_reintentos": 0}
        span = self._abrir("turno", "turno", mensaje=self._texto(mensaje))
        self._pila.append(span)
        try:
            yield span
        except Exception as e:
            self._pila.pop()
            span.attrs.update(self._acum)
            self._cerrar(span, "error", f"{type(e).__name__}: {e}")
            raise
        else:
            self._pila.pop()
            span.attrs.update(self._acum)
            self._cerrar(span, span.attrs.get("estado_turno", "ok"))

    @contextmanager
    def span(self, tipo: str, nombre: str, **attrs):
        span = self._abrir(tipo, nombre, **attrs)
        self._pila.append(span)
        try:
            yield span
        except Exception as e:
            self._pila.pop()
            self._cerrar(span, "error", f"{type(e).__name__}: {e}")
            raise
        else:
            self._pila.pop()
            self._cerrar(span, span.attrs.pop("estado_span", "ok"))

    def evento(self, nombre: str, **attrs) -> None:
        """Evento puntual (duración 0): modo elegido, plan creado, replanificación, derivación."""
        span = self._abrir("evento", nombre, **attrs)
        self._cerrar(span)
        if nombre == "replanificacion":
            self._acum["n_eventos_replanificacion"] = self._acum.get("n_eventos_replanificacion", 0) + 1

    def _texto(self, valor) -> str:
        return enmascarar(valor) if self.registrar_contenido else "[omitido]"

    def _espera_actual(self) -> float:
        return getattr(self.limitador, "espera_total_s", 0.0) if self.limitador else 0.0

    # --- vinculación con LangChain ---
    def instrumentar(self, *objetos) -> None:
        """Agrega el callback a modelos y herramientas (sin duplicar si el mismo objeto se repite)."""
        for o in objetos:
            if all(c is not self.callback for c in (o.callbacks or [])):
                o.callbacks = [*(o.callbacks or []), self.callback]


class ManejadorCallbacks(BaseCallbackHandler):
    """Convierte los eventos de LangChain (LLM y herramientas) en spans del trazador."""

    def __init__(self, trazador: Trazador):
        self.t = trazador

    # LLM ---------------------------------------------------------------
    def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs):
        modelo = (kwargs.get("invocation_params") or {}).get("model") or (kwargs.get("invocation_params") or {}).get(
            "model_name") or "desconocido"
        span = self.t._abrir("llm", "llm", modelo=modelo, mensajes_entrada=sum(len(m) for m in messages))
        INTENTOS_HTTP.clear()
        self.t._abiertos[run_id] = (span, self.t._espera_actual())

    def on_llm_end(self, response, *, run_id, **kwargs):
        span, espera0 = self.t._abiertos.pop(run_id, (None, 0.0))
        if span is None:
            return
        uso = {}
        try:
            uso = response.generations[0][0].message.usage_metadata or {}
        except (AttributeError, IndexError):
            pass
        espera = round(self.t._espera_actual() - espera0, 4)
        total = time.perf_counter() - span.t0
        detalle = uso.get("output_token_details") or {}
        span.attrs.update(tokens_entrada=uso.get("input_tokens", 0), tokens_salida=uso.get("output_tokens", 0),
                          tokens_razonamiento=detalle.get("reasoning", 0), espera_limitador_s=espera,
                          latencia_modelo_s=round(max(total - espera, 0), 4))
        self._anotar_intentos(span)
        a = self.t._acum
        a["n_llm"] = a.get("n_llm", 0) + 1
        a["tokens_entrada"] = a.get("tokens_entrada", 0) + span.attrs["tokens_entrada"]
        a["tokens_salida"] = a.get("tokens_salida", 0) + span.attrs["tokens_salida"]
        a["tokens_razonamiento"] = a.get("tokens_razonamiento", 0) + span.attrs["tokens_razonamiento"]
        a["espera_limitador_s"] = round(a.get("espera_limitador_s", 0) + espera, 4)
        self.t._cerrar(span)

    def on_llm_error(self, error, *, run_id, **kwargs):
        span, espera0 = self.t._abiertos.pop(run_id, (None, 0.0))
        if span is None:
            return
        span.attrs["espera_limitador_s"] = round(self.t._espera_actual() - espera0, 4)
        self._anotar_intentos(span)
        a = self.t._acum
        a["n_llm"] = a.get("n_llm", 0) + 1
        a["n_alertas"] = a.get("n_alertas", 0) + 1
        self.t._cerrar(span, "error", f"{type(error).__name__}: {error}")

    def _anotar_intentos(self, span: Span) -> None:
        """Copia al span los intentos HTTP: cuántos hubo y cuáles fallaron (típicamente 429 por tokens/minuto)."""
        intentos = list(INTENTOS_HTTP)
        INTENTOS_HTTP.clear()
        if not intentos:
            return
        reintentos = [i for i in intentos if i["status"] >= 400]
        span.attrs["intentos_http"] = len(intentos)
        span.attrs["reintentos"] = len(reintentos)
        if reintentos:
            span.attrs["errores_http"] = reintentos
            a = self.t._acum
            a["n_reintentos"] = a.get("n_reintentos", 0) + len(reintentos)

    # Herramientas ------------------------------------------------------
    def on_tool_start(self, serialized, input_str, *, run_id, inputs=None, **kwargs):
        nombre = (serialized or {}).get("name") or kwargs.get("name") or "herramienta"
        span = self.t._abrir("herramienta", nombre, args=self.t._texto(inputs if inputs is not None else input_str))
        self.t._abiertos[run_id] = (span, 0.0)

    def on_tool_end(self, output, *, run_id, **kwargs):
        span, _ = self.t._abiertos.pop(run_id, (None, 0.0))
        if span is None:
            return
        texto = str(getattr(output, "content", output))
        etiqueta = texto.split(":", 1)[0].strip()
        span.attrs["etiqueta"] = etiqueta
        span.attrs["resultado"] = self.t._texto(texto)
        a = self.t._acum
        a["n_herramientas"] = a.get("n_herramientas", 0) + 1
        alerta = etiqueta in ETIQUETAS_ALERTA
        if alerta:
            a["n_alertas"] = a.get("n_alertas", 0) + 1
        self.t._cerrar(span, "alerta" if alerta else "ok")

    def on_tool_error(self, error, *, run_id, **kwargs):
        span, _ = self.t._abiertos.pop(run_id, (None, 0.0))
        if span is None:
            return
        a = self.t._acum
        a["n_herramientas"] = a.get("n_herramientas", 0) + 1
        a["n_alertas"] = a.get("n_alertas", 0) + 1
        self.t._cerrar(span, "error", f"{type(error).__name__}: {error}")
