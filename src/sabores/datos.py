"""Datos operativos del restaurante en SQLite: carta, stock, perfiles de clientes, pedidos y derivaciones."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path


def _ahora() -> str:
    return datetime.now().isoformat(timespec="seconds")


class BaseDatos:
    def __init__(self, ruta: str | Path = ":memory:", carta: str | Path | None = None):
        if ruta != ":memory:":
            Path(ruta).parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(ruta), check_same_thread=False)
        self.con.row_factory = sqlite3.Row
        self.con.executescript(
            """
            CREATE TABLE IF NOT EXISTS platos (
                id TEXT PRIMARY KEY, nombre TEXT UNIQUE, descripcion TEXT, alergenos TEXT,
                apto_para TEXT, contaminacion_cruzada TEXT, stock INTEGER);
            CREATE TABLE IF NOT EXISTS clientes (
                id TEXT PRIMARY KEY, alergenos TEXT DEFAULT '[]', dietas TEXT DEFAULT '[]',
                alergia_severa INTEGER DEFAULT 0, actualizado_en TEXT);
            CREATE TABLE IF NOT EXISTS pedidos (
                id INTEGER PRIMARY KEY AUTOINCREMENT, cliente TEXT, platos TEXT, modalidad TEXT,
                direccion TEXT, estado TEXT, creado_en TEXT);
            CREATE TABLE IF NOT EXISTS derivaciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT, cliente TEXT, motivo TEXT, detalle TEXT,
                pedido_id INTEGER, creado_en TEXT);
            """
        )
        if carta:
            self.cargar_carta(carta)

    # --- carta y stock ---
    def cargar_carta(self, ruta: str | Path) -> int:
        """Carga o actualiza la carta sin tocar el resto del agente (requisito de mantenibilidad EP1)."""
        platos = json.loads(Path(ruta).read_text(encoding="utf-8"))
        for p in platos:
            self.con.execute(
                "INSERT INTO platos VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
                "nombre=excluded.nombre, descripcion=excluded.descripcion, alergenos=excluded.alergenos, "
                "apto_para=excluded.apto_para, contaminacion_cruzada=excluded.contaminacion_cruzada",
                (p["id"], p["nombre"], p["descripcion"], json.dumps(p["alergenos"]), json.dumps(p["apto_para"]),
                 json.dumps(p["contaminacion_cruzada"]), p["stock_diario"]),
            )
        self.con.commit()
        return len(platos)

    def _plato(self, fila) -> dict:
        p = dict(fila)
        for k in ("alergenos", "apto_para", "contaminacion_cruzada"):
            p[k] = json.loads(p[k])
        return p

    def platos(self) -> list[dict]:
        return [self._plato(f) for f in self.con.execute("SELECT * FROM platos ORDER BY id")]

    def buscar_plato(self, nombre: str) -> dict | None:
        fila = self.con.execute("SELECT * FROM platos WHERE lower(nombre) = lower(?) OR id = upper(?)",
                                (nombre.strip(), nombre.strip())).fetchone()
        return self._plato(fila) if fila else None

    def fijar_stock(self, nombre: str, stock: int) -> None:
        """Usado en la demo y las pruebas para simular condiciones cambiantes (plato agotado)."""
        self.con.execute("UPDATE platos SET stock = ? WHERE lower(nombre) = lower(?)", (stock, nombre))
        self.con.commit()

    # --- perfil alimentario del cliente (memoria de largo plazo estructurada) ---
    def perfil(self, cliente: str) -> dict:
        fila = self.con.execute("SELECT * FROM clientes WHERE id = ?", (cliente,)).fetchone()
        if not fila:
            return {"id": cliente, "alergenos": [], "dietas": [], "alergia_severa": False}
        return {"id": cliente, "alergenos": json.loads(fila["alergenos"]), "dietas": json.loads(fila["dietas"]),
                "alergia_severa": bool(fila["alergia_severa"])}

    def guardar_perfil(self, cliente: str, alergenos: list[str], dietas: list[str], alergia_severa: bool) -> dict:
        actual = self.perfil(cliente)
        nuevo = {
            "alergenos": sorted(set(actual["alergenos"]) | set(alergenos)),
            "dietas": sorted(set(actual["dietas"]) | set(dietas)),
            "alergia_severa": actual["alergia_severa"] or alergia_severa,
        }
        self.con.execute("INSERT OR REPLACE INTO clientes VALUES (?, ?, ?, ?, ?)",
                         (cliente, json.dumps(nuevo["alergenos"]), json.dumps(nuevo["dietas"]),
                          int(nuevo["alergia_severa"]), _ahora()))
        self.con.commit()
        return {"id": cliente, **nuevo}

    # --- pedidos y derivaciones ---
    def crear_pedido(self, cliente: str, platos: list[str], modalidad: str, direccion: str | None,
                     estado: str) -> int:
        for nombre in platos:
            self.con.execute("UPDATE platos SET stock = stock - 1 WHERE nombre = ?", (nombre,))
        cur = self.con.execute(
            "INSERT INTO pedidos (cliente, platos, modalidad, direccion, estado, creado_en) VALUES (?, ?, ?, ?, ?, ?)",
            (cliente, json.dumps(platos, ensure_ascii=False), modalidad, direccion, estado, _ahora()))
        self.con.commit()
        return cur.lastrowid

    def pedido(self, pedido_id: int) -> dict | None:
        fila = self.con.execute("SELECT * FROM pedidos WHERE id = ?", (pedido_id,)).fetchone()
        return {**dict(fila), "platos": json.loads(fila["platos"])} if fila else None

    def derivar(self, cliente: str, motivo: str, detalle: str, pedido_id: int | None = None) -> int:
        cur = self.con.execute(
            "INSERT INTO derivaciones (cliente, motivo, detalle, pedido_id, creado_en) VALUES (?, ?, ?, ?, ?)",
            (cliente, motivo, detalle, pedido_id, _ahora()))
        self.con.commit()
        return cur.lastrowid

    def derivaciones(self, cliente: str) -> list[dict]:
        return [dict(f) for f in self.con.execute("SELECT * FROM derivaciones WHERE cliente = ?", (cliente,))]
