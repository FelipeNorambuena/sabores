"""Memoria de corto plazo con las clases de LangChain vistas en IL2.2, persistida en SQLite.

- ConversationBufferWindowMemory(k): los últimos k turnos se pasan literalmente al agente.
- ConversationSummaryMemory: lo que sale de la ventana se resume con el LLM (ahorra tokens sin perder datos).
- HistorialSQLite: guarda los mensajes en la base del restaurante, así la conversación sobrevive a un reinicio.
"""
from __future__ import annotations

import sqlite3
import warnings

warnings.filterwarnings("ignore", message=".*was deprecated in LangChain.*")

from langchain_classic.memory import ConversationBufferWindowMemory, ConversationSummaryMemory  # noqa: E402
from langchain_core.chat_history import BaseChatMessageHistory  # noqa: E402
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage  # noqa: E402
from langchain_core.prompts import PromptTemplate  # noqa: E402

PROMPT_RESUMEN = PromptTemplate.from_template(
    "Resume en máximo 5 líneas esta conversación entre un cliente y el asistente del restaurante Sabores. "
    "Conserva: restricciones y alergias declaradas, platos recomendados o descartados, pedidos y "
    "números de pedido, y cualquier compromiso pendiente.\n\nResumen previo:\n{summary}\n\nMensajes nuevos:\n{new_lines}"
)


class HistorialSQLite(BaseChatMessageHistory):
    """Historial de chat de LangChain guardado en la tabla `mensajes` (una fila por mensaje)."""

    def __init__(self, con: sqlite3.Connection, sesion_id: str):
        self.con, self.sesion_id = con, sesion_id
        con.execute("CREATE TABLE IF NOT EXISTS mensajes ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, sesion TEXT, rol TEXT, contenido TEXT)")

    @property
    def messages(self) -> list[BaseMessage]:
        filas = self.con.execute("SELECT rol, contenido FROM mensajes WHERE sesion = ? ORDER BY id",
                                 (self.sesion_id,)).fetchall()
        return [HumanMessage(c) if rol == "cliente" else AIMessage(c) for rol, c in filas]

    def add_message(self, message: BaseMessage) -> None:
        rol = "cliente" if isinstance(message, HumanMessage) else "agente"
        self.con.execute("INSERT INTO mensajes (sesion, rol, contenido) VALUES (?, ?, ?)",
                         (self.sesion_id, rol, message.content))
        self.con.commit()

    def clear(self) -> None:
        self.con.execute("DELETE FROM mensajes WHERE sesion = ?", (self.sesion_id,))
        self.con.commit()


class MemoriaCortoPlazo:
    def __init__(self, con: sqlite3.Connection, sesion_id: str, ventana_turnos: int = 5, llm_resumen=None):
        self.con, self.sesion_id = con, sesion_id
        self.historial = HistorialSQLite(con, sesion_id)
        self.ventana = ConversationBufferWindowMemory(k=ventana_turnos, chat_memory=self.historial,
                                                      memory_key="chat_history", return_messages=True)
        self.resumidor = (ConversationSummaryMemory(llm=llm_resumen, prompt=PROMPT_RESUMEN,
                                                    chat_memory=self.historial)
                          if llm_resumen is not None else None)
        con.execute("CREATE TABLE IF NOT EXISTS resumenes (sesion TEXT PRIMARY KEY, resumen TEXT, resumidos INTEGER)")

    def agregar(self, rol: str, contenido: str) -> None:
        self.historial.add_message(HumanMessage(contenido) if rol == "cliente" else AIMessage(contenido))
        self._resumir_lo_que_sale_de_la_ventana()

    def guardar_turno(self, entrada: str, salida: str) -> None:
        """Igual que memory.save_context() en el notebook 2-memory-agent-advanced.ipynb."""
        self.ventana.save_context({"input": entrada}, {"output": salida})
        self._resumir_lo_que_sale_de_la_ventana()

    def resumen(self) -> str:
        fila = self.con.execute("SELECT resumen FROM resumenes WHERE sesion = ?", (self.sesion_id,)).fetchone()
        return fila[0] if fila else ""

    def mensajes(self) -> list[BaseMessage]:
        """Ventana literal de mensajes recientes. El resumen se inyecta aparte, dentro del prompt de sistema
        (algunos proveedores, como Gemini, aceptan un solo mensaje de sistema al inicio)."""
        return self.ventana.load_memory_variables({})["chat_history"]

    def _resumir_lo_que_sale_de_la_ventana(self) -> None:
        todos = self.historial.messages
        fila = self.con.execute("SELECT resumidos FROM resumenes WHERE sesion = ?", (self.sesion_id,)).fetchone()
        resumidos = fila[0] if fila else 0
        fuera = max(0, len(todos) - 2 * self.ventana.k)  # un turno = mensaje del cliente + respuesta
        nuevos = todos[resumidos:fuera]
        if not nuevos:
            return
        if self.resumidor is not None:
            nuevo = self.resumidor.predict_new_summary(nuevos, self.resumen() or "(vacío)")
        else:  # sin LLM: resumen extractivo truncado, para no perder continuidad
            texto = "\n".join(f"{'cliente' if isinstance(m, HumanMessage) else 'agente'}: {m.content}" for m in nuevos)
            nuevo = (self.resumen() + "\n" + texto)[-1500:]
        self.con.execute("INSERT OR REPLACE INTO resumenes VALUES (?, ?, ?)", (self.sesion_id, nuevo.strip(), fuera))
        self.con.commit()
