"""Ejecuta los escenarios de evaluación.

Uso: python scripts/ejecutar_escenarios.py [--etiqueta baseline] [--solo nombre_escenario]
Deja logs/trazas.jsonl y logs/resultados.jsonl, que lee el dashboard.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sabores.evaluacion import ESCENARIOS, ejecutar_corrida  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--etiqueta", default="baseline", help="nombre de la corrida (baseline, optimizado...)")
ap.add_argument("--solo", help="ejecutar un solo escenario por nombre")
args = ap.parse_args()
esc = [e for e in ESCENARIOS if not args.solo or e.nombre == args.solo]
if not esc:
    sys.exit(f"Escenario desconocido. Opciones: {[e.nombre for e in ESCENARIOS]}")
ejecutar_corrida(args.etiqueta, esc)
