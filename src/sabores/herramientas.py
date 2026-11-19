"""Herramientas del agente, expuestas al LLM por Function Calling (el JSON Schema sale de los tipos).

Consulta:      buscar_platos, consultar_plato, consultar_disponibilidad, consultar_pedido
Razonamiento:  filtrar_platos_seguros, validar_pedido
Escritura:     guardar_perfil_alimentario, recordar_preferencia, crear_pedido, derivar_a_personal

Cada resultado empieza con una etiqueta fija (OK, ERROR, AGOTADO, RECHAZADO, DERIVADO). El orquestador
lee esas etiquetas para decidir si el plan sigue o se replanifica, sin depender de que el LLM lo "note".
"""
from __future__ import annotations

import json
from typing import Literal

from langchain_core.tools import tool

from .datos import BaseDatos
from .memoria import MemoriaLargoPlazo

Alergeno = Literal["gluten", "lactosa", "frutos_secos", "pescado", "mariscos", "huevo", "soja", "sésamo"]
Dieta = Literal["vegano", "vegetariano", "sin_gluten", "sin_lactosa", "sin_frutos_secos"]


def _json(etiqueta: str, **datos) -> str:
    return f"{etiqueta}: " + json.dumps(datos, ensure_ascii=False)


def es_seguro(plato: dict, alergenos: list[str], dietas: list[str], alergia_severa: bool) -> tuple[bool, str]:
    """Regla dura de seguridad: se aplica en código, nunca se delega al criterio del LLM."""
    presentes = set(plato["alergenos"]) & set(alergenos)
    if presentes:
        return False, f"contiene {', '.join(sorted(presentes))}"
    faltantes = set(dietas) - set(plato["apto_para"])
    if faltantes:
        return False, f"no es apto para {', '.join(sorted(faltantes))}"
    cruzada = set(plato["contaminacion_cruzada"]) & set(alergenos)
    if cruzada and alergia_severa:
        return False, f"riesgo de contaminación cruzada con {', '.join(sorted(cruzada))}"
    return True, "cumple las restricciones"


def crear_herramientas(db: BaseDatos, memoria: MemoriaLargoPlazo, cliente: str, k: int = 4,
                       perfil_en_busqueda: bool = False):
    """Crea las herramientas ligadas a un cliente. El id del cliente lo fija el sistema, no el LLM.

    perfil_en_busqueda (perfil optimizado): buscar_platos excluye por sí misma los platos incompatibles con el perfil
    guardado del cliente. Antes la seguridad dependía de que el modelo encadenara buscar y luego filtrar; al mostrarle
    solo la herramienta de cada paso esa cadena dejó de estar garantizada y se recomendaron platos con gluten.
    """

    @tool
    def buscar_platos(consulta: str) -> str:
        """Búsqueda semántica en la carta (RAG). Úsala para encontrar platos por gusto o tipo de comida.
        OJO: no filtra alérgenos; después aplica filtrar_platos_seguros."""
        perfil = db.perfil(cliente) if perfil_en_busqueda else None
        filtrar = bool(perfil and (perfil["alergenos"] or perfil["dietas"]))
        docs = memoria.buscar_platos(consulta, k=k * 4 if filtrar else k)   # se busca de más: algunos se descartarán
        nombres = [d.metadata["nombre"] for d in docs]
        if not filtrar:
            return _json("OK", candidatos=nombres)
        seguros, excluidos = [], []
        for nombre in nombres:
            plato = db.buscar_plato(nombre)
            ok, _ = es_seguro(plato, perfil["alergenos"], perfil["dietas"], perfil["alergia_severa"]) if plato else (False, "")
            (seguros if ok else excluidos).append(nombre)
        return _json("OK", candidatos=seguros[:k], excluidos_por_perfil=excluidos)

    @tool
    def consultar_plato(nombre: str) -> str:
        """Devuelve la ficha completa de un plato: ingredientes, alérgenos, dietas aptas y contaminación cruzada."""
        plato = db.buscar_plato(nombre)
        if not plato:
            return _json("ERROR", detalle=f"El plato '{nombre}' no está en la carta.")
        return _json("OK", **plato)

    @tool
    def filtrar_platos_seguros(alergenos: list[Alergeno], dietas: list[Dieta], alergia_severa: bool = False,
                               candidatos: list[str] | None = None) -> str:
        """Filtra la carta (o una lista de candidatos) dejando solo platos sin los alérgenos indicados, aptos
        para las dietas indicadas y, si la alergia es severa, sin riesgo de contaminación cruzada."""
        platos = db.platos() if not candidatos else [p for c in candidatos if (p := db.buscar_plato(c))]
        seguros, descartados = [], []
        for p in platos:
            ok, motivo = es_seguro(p, alergenos, dietas, alergia_severa)
            (seguros if ok else descartados).append(p["nombre"] if ok else f"{p['nombre']} ({motivo})")
        if not seguros:
            return _json("DERIVADO", detalle="Ningún plato cumple todas las restricciones; derivar al personal.",
                         descartados=descartados)
        return _json("OK", seguros=seguros, descartados=descartados)

    @tool
    def consultar_disponibilidad(platos: list[str]) -> str:
        """Consulta el stock del día de uno o más platos."""
        disponibles, agotados = [], []
        for nombre in platos:
            p = db.buscar_plato(nombre)
            if not p:
                return _json("ERROR", detalle=f"El plato '{nombre}' no está en la carta.")
            (disponibles if p["stock"] > 0 else agotados).append(p["nombre"])
        return _json("AGOTADO" if agotados else "OK", disponibles=disponibles, agotados=agotados)

    @tool
    def guardar_perfil_alimentario(alergenos: list[Alergeno], dietas: list[Dieta], alergia_severa: bool) -> str:
        """Guarda (y acumula) las restricciones alimentarias que declaró el cliente para futuras visitas."""
        return _json("OK", perfil=db.guardar_perfil(cliente, alergenos, dietas, alergia_severa))

    @tool
    def recordar_preferencia(dato: str) -> str:
        """Guarda en memoria de largo plazo un gusto o dato útil del cliente (p. ej. 'prefiere comida picante')."""
        memoria.recordar(dato, cliente=cliente)
        return _json("OK", guardado=dato)

    @tool
    def validar_pedido(platos: list[str]) -> str:
        """Verifica, contra el perfil guardado del cliente, que todos los platos del pedido sean seguros."""
        perfil = db.perfil(cliente)
        problemas = []
        for nombre in platos:
            p = db.buscar_plato(nombre)
            if not p:
                problemas.append(f"{nombre} (no existe)")
                continue
            ok, motivo = es_seguro(p, perfil["alergenos"], perfil["dietas"], perfil["alergia_severa"])
            if not ok:
                problemas.append(f"{p['nombre']} ({motivo})")
        if problemas:
            return _json("RECHAZADO", problemas=problemas, perfil=perfil)
        return _json("OK", detalle="Todos los platos son seguros para el perfil del cliente.")

    @tool
    def crear_pedido(platos: list[str], modalidad: Literal["local", "delivery"], direccion: str = "") -> str:
        """Registra el pedido. Valida seguridad y stock antes de escribir. Si el cliente tiene alergia
        severa, el pedido queda pendiente de confirmación del personal (protocolo de derivación)."""
        if not platos:
            return _json("ERROR", detalle="El pedido no tiene platos.")
        if modalidad == "delivery" and len(direccion.strip()) < 5:
            return _json("ERROR", detalle="Falta la dirección de despacho; pídesela al cliente.")
        validacion = validar_pedido.invoke({"platos": platos})
        if validacion.startswith("RECHAZADO"):
            return validacion
        disponibilidad = consultar_disponibilidad.invoke({"platos": platos})
        if not disponibilidad.startswith("OK"):
            return disponibilidad
        nombres = [db.buscar_plato(n)["nombre"] for n in platos]
        perfil = db.perfil(cliente)
        estado = "pendiente_confirmacion" if perfil["alergia_severa"] else "confirmado"
        pedido_id = db.crear_pedido(cliente, nombres, modalidad, direccion.strip() or None, estado)
        memoria.recordar(f"Pidió {', '.join(nombres)} ({modalidad})", cliente=cliente, tipo="pedido")
        if perfil["alergia_severa"]:
            caso = db.derivar(cliente, "alergia_severa", f"Confirmar pedido {pedido_id}: {', '.join(nombres)}",
                              pedido_id)
            return _json("DERIVADO", pedido_id=pedido_id, estado=estado, caso_personal=caso,
                         detalle="Pedido registrado; el personal debe confirmarlo por la alergia severa.")
        return _json("OK", pedido_id=pedido_id, estado=estado, platos=nombres)

    @tool
    def consultar_pedido(pedido_id: int) -> str:
        """Consulta el estado de un pedido del cliente."""
        pedido = db.pedido(pedido_id)
        if not pedido or pedido["cliente"] != cliente:
            return _json("ERROR", detalle=f"No existe el pedido {pedido_id} para este cliente.")
        return _json("OK", **pedido)

    @tool
    def derivar_a_personal(motivo: Literal["alergia_severa", "informacion_insuficiente", "reclamo", "otro"],
                           detalle: str) -> str:
        """Deriva el caso a una persona del local cuando hay riesgo, ambigüedad o el cliente lo pide."""
        caso = db.derivar(cliente, motivo, detalle)
        return _json("DERIVADO", caso_personal=caso, detalle="Un integrante del equipo contactará al cliente.")

    return [buscar_platos, consultar_plato, filtrar_platos_seguros, consultar_disponibilidad,
            guardar_perfil_alimentario, recordar_preferencia, validar_pedido, crear_pedido,
            consultar_pedido, derivar_a_personal]
