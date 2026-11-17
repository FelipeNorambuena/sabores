"""Orquestador del agente Sabores: une memoria, planificación, ejecución con herramientas y respuesta final.

Flujo por mensaje: recuperar contexto -> decidir modo (directo / plan) -> ejecutar pasos -> detectar
condiciones -> replanificar si corresponde -> redactar respuesta -> guardar memoria y traza.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage

from .config import Config, crear_embeddings, crear_llm
from .datos import BaseDatos
from .ejecutor import Ejecutor, ResultadoPaso, texto_de
from .herramientas import crear_herramientas
from .memoria import MemoriaCortoPlazo, MemoriaLargoPlazo
from .planificador import Paso, Planificador

# Prompt de sistema de la EP1, extendido con el uso de herramientas
PROMPT_SISTEMA = """Eres el asistente de Sabores Conscientes. Recomiendas platos de la carta según las
restricciones alimentarias del cliente y gestionas sus pedidos. Usa SOLO información que entreguen las
herramientas (no inventes ingredientes ni alérgenos).

Reglas:
1. Si el cliente declara alergias, intolerancias o dietas, guárdalas con guardar_perfil_alimentario.
2. buscar_platos encuentra platos parecidos, pero NO excluye alérgenos: antes de recomendar aplica
   filtrar_platos_seguros con TODAS las restricciones del perfil.
3. Antes de registrar un pedido, verifica disponibilidad; crear_pedido valida seguridad otra vez.
4. Si ninguna opción es segura, hay ambigüedad o la alergia es severa, deriva al personal.
5. Fuera del dominio del restaurante, declina con amabilidad.

Contexto del cliente:
{contexto}"""

PROMPT_RESPUESTA = """Redacta la respuesta final al cliente en español, con tono cercano y breve (formato apto
para WhatsApp). Menciona el nombre exacto de cada plato y por qué cumple la restricción. Si hubo derivación al
personal o un pedido, entrega el número. No agregues información que no esté en los resultados.

Mensaje del cliente: {mensaje}

Resultados de la ejecución:
{resultados}"""


class AgenteSabores:
    def __init__(self, cfg: Config, cliente: str, sesion: str, db: BaseDatos, memoria: MemoriaLargoPlazo,
                 llm_planificador, llm_ejecutor, ruta_traza: Path | None = None):
        self.cfg, self.cliente, self.db, self.memoria = cfg, cliente, db, memoria
        self.llm = llm_planificador
        self.corto = MemoriaCortoPlazo(db.con, sesion, cfg.ventana_turnos, llm_resumen=llm_planificador)
        herramientas = crear_herramientas(db, memoria, cliente, k=cfg.k_recuperacion)
        self.ejecutor = Ejecutor(llm_ejecutor, herramientas, cfg.max_iteraciones_paso)
        self.planificador = Planificador(llm_planificador, [h.name for h in herramientas])
        self.ruta_traza = ruta_traza
        self.traza: list[dict] = []

    # --- contexto: memoria de largo plazo + resumen de corto plazo ---
    def contexto(self, mensaje: str) -> str:
        perfil = self.db.perfil(self.cliente)
        recuerdos = self.memoria.recuperar(mensaje, self.cliente, k=3)
        lineas = [
            f"Perfil alimentario guardado: alérgenos={perfil['alergenos'] or 'ninguno'}, "
            f"dietas={perfil['dietas'] or 'ninguna'}, alergia_severa={perfil['alergia_severa']}",
            "Recuerdos relevantes: " + ("; ".join(d.page_content for d in recuerdos) or "ninguno"),
        ]
        if self.corto.resumen():
            lineas.append(f"Resumen de la conversación: {self.corto.resumen()}")
        return "\n".join(lineas)

    def _registrar(self, evento: str, **datos) -> None:
        self.traza.append({"evento": evento, **datos})

    # --- ciclo principal ---
    def responder(self, mensaje: str) -> str:
        self.traza = []
        contexto = self.contexto(mensaje)
        self._registrar("contexto", contexto=contexto)
        sistema = PROMPT_SISTEMA.format(contexto=contexto)
        historial = self.corto.mensajes()

        decision = self.planificador.decidir_modo(mensaje)
        self._registrar("modo", **decision)
        if decision["modo"] == "directo":
            res = self.ejecutor.ejecutar(sistema, [*historial, HumanMessage(mensaje)])
            completados = [self._resultado(Paso(id=0, descripcion=mensaje), res)]
        else:
            completados = self._ejecutar_plan(mensaje, sistema, contexto, historial)

        resultados = "\n".join(f"- Paso {c['paso']['id']} ({c['paso']['descripcion']}): {c['salida']} "
                               f"| observaciones: {c['observaciones']}" for c in completados)
        respuesta = texto_de(self.llm.invoke([SystemMessage(sistema), *historial, HumanMessage(
            PROMPT_RESPUESTA.format(mensaje=mensaje, resultados=resultados))]).content)
        self.corto.guardar_turno(mensaje, respuesta)
        self._registrar("respuesta", texto=respuesta)
        self._guardar_traza(mensaje)
        return respuesta

    def _ejecutar_plan(self, mensaje: str, sistema: str, contexto: str, historial) -> list[dict]:
        plan = self.planificador.crear_plan(mensaje, contexto)
        self._registrar("plan", objetivo=plan.objetivo, pasos=[p.model_dump() for p in plan.pasos])
        pendientes, completados, replanificaciones = list(plan.pasos), [], 0
        while pendientes:
            paso = pendientes.pop(0)
            previos = "\n".join(f"- {c['paso']['descripcion']}: {c['salida']} {c['observaciones']}"
                                for c in completados) or "(ninguno)"
            instruccion = (f"Solicitud original del cliente: {mensaje}\n"
                           f"Ejecuta SOLO este paso del plan: {paso.descripcion}"
                           + (f" (herramienta sugerida: {paso.herramienta})" if paso.herramienta else "")
                           + f"\nResultados de pasos anteriores:\n{previos}")
            res = self.ejecutor.ejecutar(sistema, [*historial, HumanMessage(instruccion)])
            completados.append(self._resultado(paso, res))
            condicion = res.condicion()
            if condicion is None:
                continue
            if condicion.startswith("DERIVADO"):
                # Protocolo de derivación: el caso pasa a una persona y el agente no sigue actuando solo
                self._registrar("fin_por_derivacion", condicion=condicion)
                break
            if replanificaciones >= self.cfg.max_replanificaciones:
                caso = self.db.derivar(self.cliente, "informacion_insuficiente",
                                       f"Límite de replanificación alcanzado. Última condición: {condicion}")
                completados.append({"paso": {"id": -1, "descripcion": "Derivación automática"},
                                    "salida": f"DERIVADO: caso {caso}", "observaciones": [condicion]})
                self._registrar("derivacion_por_limite", condicion=condicion, caso=caso)
                break
            replanificaciones += 1
            pendientes = self.planificador.replanificar(plan.objetivo, completados, pendientes, condicion)
            self._registrar("replanificacion", numero=replanificaciones, condicion=condicion,
                            nuevos_pasos=[p.model_dump() for p in pendientes])
        return completados

    def _resultado(self, paso: Paso, res: ResultadoPaso) -> dict:
        datos = {"paso": paso.model_dump(), "salida": res.salida, "observaciones": res.observaciones}
        self._registrar("paso", **datos, llamadas=res.llamadas)
        return datos

    def _guardar_traza(self, mensaje: str) -> None:
        """Observabilidad: cada turno queda en un JSONL para auditar por qué el agente decidió algo."""
        if not self.ruta_traza:
            return
        self.ruta_traza.parent.mkdir(parents=True, exist_ok=True)
        with self.ruta_traza.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"fecha": datetime.now().isoformat(timespec="seconds"), "cliente": self.cliente,
                                "mensaje": mensaje, "traza": self.traza}, ensure_ascii=False) + "\n")


def construir_agente(cliente: str, sesion: str | None = None, cfg: Config | None = None,
                     llm_planificador=None, llm_ejecutor=None, embeddings=None, persistir: bool = True) -> AgenteSabores:
    """Fábrica usada por el Colab y las pruebas. Sin argumentos de modelo, usa Gemini según .env/Secrets."""
    cfg = cfg or Config()
    llm_planificador = llm_planificador or crear_llm(cfg.modelo_planificador, cfg)
    llm_ejecutor = llm_ejecutor or crear_llm(cfg.modelo_ejecutor, cfg)
    embeddings = embeddings or crear_embeddings(cfg)
    db = BaseDatos(cfg.data_dir / "sabores.db" if persistir else ":memory:", carta=cfg.carta)
    memoria = MemoriaLargoPlazo(embeddings, cfg.data_dir / "faiss" if persistir else None)
    memoria.indexar_carta(db.platos())
    return AgenteSabores(cfg, cliente, sesion or cliente, db, memoria, llm_planificador, llm_ejecutor,
                         ruta_traza=cfg.data_dir / "traza.jsonl" if persistir else None)
