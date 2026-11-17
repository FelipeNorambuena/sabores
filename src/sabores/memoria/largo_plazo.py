"""Memoria de largo plazo y recuperación semántica de contexto con FAISS (mismo motor que la EP1).

Dos índices vectoriales persistidos en disco:
- carta: un documento por plato (plato_a_texto de la EP1) para la búsqueda semántica de platos.
- episodica: preferencias y pedidos pasados de cada cliente, recuperados por similitud y filtrados por cliente.

El perfil alimentario (alérgenos, dietas) se guarda aparte en SQLite porque debe aplicarse como regla
dura, no como algo "parecido": ver BaseDatos.perfil().
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document


def plato_a_texto(plato: dict) -> str:
    alergenos = ", ".join(plato["alergenos"]) or "ninguno"
    return (f"Plato: {plato['nombre']}\nDescripción: {plato['descripcion']}\n"
            f"Alérgenos presentes: {alergenos}\nApto para: {', '.join(plato['apto_para'])}")


class MemoriaLargoPlazo:
    def __init__(self, embeddings, directorio: str | Path | None = None):
        self.embeddings = embeddings
        self.dir = Path(directorio) if directorio else None
        self.carta: FAISS | None = None
        self.episodica: FAISS | None = self._cargar("episodica")

    def _cargar(self, nombre: str) -> FAISS | None:
        if self.dir and (self.dir / nombre / "index.faiss").exists():
            # Índices generados por este mismo sistema (no archivos de terceros)
            return FAISS.load_local(str(self.dir / nombre), self.embeddings, allow_dangerous_deserialization=True)
        return None

    def _guardar(self, nombre: str, indice: FAISS) -> None:
        if self.dir:
            indice.save_local(str(self.dir / nombre))

    # --- carta (RAG) ---
    def indexar_carta(self, platos: list[dict]) -> None:
        """Reutiliza el índice guardado si la carta no cambió; si cambió, lo reconstruye (sin reentrenar nada)."""
        huella = hashlib.sha256(json.dumps(platos, sort_keys=True).encode()).hexdigest()
        marca = self.dir / "carta" / "huella.txt" if self.dir else None
        if marca and marca.exists() and marca.read_text() == huella:
            self.carta = self._cargar("carta")
            if self.carta:
                return
        docs = [Document(plato_a_texto(p), metadata={"id": p["id"], "nombre": p["nombre"]}) for p in platos]
        self.carta = FAISS.from_documents(docs, self.embeddings)
        self._guardar("carta", self.carta)
        if marca:
            marca.write_text(huella)

    def buscar_platos(self, consulta: str, k: int = 4) -> list[Document]:
        return self.carta.similarity_search(consulta, k=k) if self.carta else []

    # --- memoria episódica ---
    def recordar(self, texto: str, cliente: str, tipo: str = "preferencia") -> None:
        doc = Document(texto, metadata={"cliente": cliente, "tipo": tipo,
                                        "fecha": datetime.now().isoformat(timespec="minutes")})
        if self.episodica is None:
            self.episodica = FAISS.from_documents([doc], self.embeddings)
        else:
            self.episodica.add_documents([doc])
        self._guardar("episodica", self.episodica)

    def recuperar(self, consulta: str, cliente: str, k: int = 3) -> list[Document]:
        """Recuperación semántica filtrada por cliente: solo vuelve lo relevante para la consulta actual."""
        if self.episodica is None:
            return []
        return self.episodica.similarity_search(consulta, k=k, filter={"cliente": cliente}, fetch_k=50)
