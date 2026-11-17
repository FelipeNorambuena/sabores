"""Configuración del agente y fábricas de modelos (Gemini por defecto, como en la EP1)."""
from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from .observabilidad import LimitadorMedido
from .observabilidad.trazador import crear_cliente_http

load_dotenv()
# Gemini 3 ignora la temperatura y lo avisa en cada llamada; el aviso no aporta a la demo
warnings.filterwarnings("ignore", message=".*fixed sampling defaults.*")

RAIZ = Path(__file__).resolve().parents[2]


def _env(clave: str, defecto: str) -> str:
    return os.getenv(clave) or defecto


@dataclass
class Config:
    proveedor: str = field(default_factory=lambda: _env("LLM_PROVEEDOR", "google"))
    modelo_planificador: str = field(default_factory=lambda: _env("LLM_MODELO_PLANIFICADOR", "gemini-3.6-flash"))
    modelo_ejecutor: str = field(default_factory=lambda: _env("LLM_MODELO_EJECUTOR", "gemini-3.6-flash"))
    modelo_embeddings: str = field(default_factory=lambda: _env("EMBEDDINGS_MODELO", "gemini-embedding-001"))
    # Groq no ofrece embeddings: con Groq como LLM, los embeddings pueden seguir en Gemini
    proveedor_embeddings: str = field(default_factory=lambda: _env("EMBEDDINGS_PROVEEDOR", _env("LLM_PROVEEDOR", "google")))
    data_dir: Path = field(default_factory=lambda: Path(_env("DATA_DIR", str(RAIZ / ".datos"))))
    carta: Path = RAIZ / "data" / "carta.json"
    # Logs de observabilidad (un JSONL con un span por línea)
    logs_dir: Path = field(default_factory=lambda: Path(_env("LOGS_DIR", str(RAIZ / "logs"))))
    # Límites contra bucles infinitos y costo de tokens (material 2.3.1: Max Iterations)
    max_iteraciones_paso: int = 4
    # Perfil del agente: "baseline" (EV2 tal cual) u "optimizado" (cambios de la EV3 justificados con las trazas)
    perfil: str = field(default_factory=lambda: _env("AGENTE_PERFIL", "baseline"))
    max_replanificaciones: int = 2
    # Memoria de corto plazo: últimos N turnos literales, lo anterior se resume
    ventana_turnos: int = 5
    k_recuperacion: int = 4
    # Llamadas por minuto al LLM: la capa gratuita de Gemini limita las solicitudes por minuto (error 429)
    llamadas_por_minuto: float = field(default_factory=lambda: float(_env("LLM_LLAMADAS_POR_MINUTO", "8")))


    @property
    def optimizado(self) -> bool:
        return self.perfil == "optimizado"


_limitadores: dict[float, LimitadorMedido] = {}


def limitador(cfg: Config) -> InMemoryRateLimiter:
    """Un único limitador compartido por todos los modelos: espacia las llamadas en vez de fallar con 429."""
    if cfg.llamadas_por_minuto not in _limitadores:
        _limitadores[cfg.llamadas_por_minuto] = LimitadorMedido(
            requests_per_second=cfg.llamadas_por_minuto / 60, check_every_n_seconds=0.5, max_bucket_size=1)
    return _limitadores[cfg.llamadas_por_minuto]


def crear_llm(modelo: str, cfg: Config, temperatura: float = 0.1):
    if cfg.proveedor == "google":
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(model=modelo, temperature=temperatura, rate_limiter=limitador(cfg),
                                      max_retries=6)
    from langchain_openai import ChatOpenAI

    extra = {}
    if cfg.optimizado and "gpt-oss" in modelo:
        # El 59 a 73% de los tokens de salida del baseline era razonamiento interno: se baja el esfuerzo
        extra["reasoning_effort"] = "low"
    return ChatOpenAI(model=modelo, temperature=temperatura, api_key=os.getenv("LLM_API_KEY"),
                      base_url=os.getenv("LLM_BASE_URL"), rate_limiter=limitador(cfg), max_retries=6,
                      http_client=crear_cliente_http(), **extra)


def crear_embeddings(cfg: Config):
    if cfg.proveedor_embeddings == "google":
        from langchain_google_genai import GoogleGenerativeAIEmbeddings

        return GoogleGenerativeAIEmbeddings(model=cfg.modelo_embeddings, output_dimensionality=768)
    from langchain_openai import OpenAIEmbeddings

    return OpenAIEmbeddings(model=cfg.modelo_embeddings, api_key=os.getenv("LLM_API_KEY"),
                            base_url=os.getenv("LLM_BASE_URL"))
