"""Genera docs/diagramas.html con los diagramas Mermaid de docs/diseno.md, para verlos y exportarlos como imagen.

Uso:  python scripts/render_diagramas.py   y luego abrir docs/diagramas.html en el navegador
      (o servirlo con:  python -m http.server 8000 --directory docs ).
La única fuente de los diagramas es docs/diseno.md; este script no los duplica, solo los extrae.
"""
import html
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
MD = RAIZ / "docs" / "diseno.md"
SALIDA = RAIZ / "docs" / "diagramas.html"

texto = MD.read_text(encoding="utf-8")
titulos = re.findall(r"^## (.+)$", texto, flags=re.M)
bloques = re.findall(r"```mermaid\n(.*?)```", texto, flags=re.S)
if not bloques:
    raise SystemExit("No se encontraron bloques mermaid en docs/diseno.md")

secciones = []
for i, bloque in enumerate(bloques):
    titulo = titulos[i] if i < len(titulos) else f"Diagrama {i + 1}"
    secciones.append(f'<section id="d{i + 1}"><h2>{html.escape(titulo)}</h2>'
                     f'<pre class="mermaid">{html.escape(bloque)}</pre></section>')

SALIDA.write_text(f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Diagramas del diseño · Sabores</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 0 auto; max-width: 1200px; padding: 16px 24px; background: #fcfcfb; color: #0b0b0b; }}
  section {{ margin: 28px 0 56px; }}
  h2 {{ font-size: 18px; margin: 0 0 12px; }}
  .mermaid {{ background: #fcfcfb; text-align: center; }}
</style></head><body>
<h1>Diagramas del diseño</h1>
{''.join(secciones)}
<script type="module">
  import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.esm.min.mjs";
  // htmlLabels:false deja el texto como SVG nativo: sin foreignObject el canvas no se "contamina" y se puede exportar PNG
  mermaid.initialize({{ startOnLoad: false, theme: "neutral", flowchart: {{ htmlLabels: false, curve: "basis" }} }});
  await mermaid.run();

  const enlace = (texto, href, nombre) => {{
    const a = document.createElement("a");
    a.textContent = texto; a.href = href; a.download = nombre;
    a.style.cssText = "margin-right:14px;font-size:13px";
    return a;
  }};
  document.querySelectorAll("section").forEach((sec, i) => {{
    const svg = sec.querySelector("svg");
    if (!svg) return;
    const caja = svg.viewBox.baseVal;
    const ancho = caja.width || svg.getBoundingClientRect().width;
    const alto = caja.height || svg.getBoundingClientRect().height;
    // El ancho fijo solo se pone en una copia para exportar: el SVG visible sigue ajustándose a la página
    const copia = svg.cloneNode(true);
    copia.setAttribute("xmlns", "http://www.w3.org/2000/svg");
    copia.setAttribute("width", ancho); copia.setAttribute("height", alto);
    copia.style.maxWidth = "none";
    const xml = new XMLSerializer().serializeToString(copia);
    const barra = document.createElement("p");
    barra.appendChild(enlace("Descargar SVG", URL.createObjectURL(new Blob([xml], {{ type: "image/svg+xml" }})), `diagrama-${{i + 1}}.svg`));
    sec.appendChild(barra);
    const img = new Image();
    img.onload = () => {{
      const escala = 2, lienzo = document.createElement("canvas");
      lienzo.width = ancho * escala; lienzo.height = alto * escala;
      const ctx = lienzo.getContext("2d");
      ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, lienzo.width, lienzo.height);
      ctx.drawImage(img, 0, 0, lienzo.width, lienzo.height);
      lienzo.toBlob(b => barra.appendChild(enlace("Descargar PNG", URL.createObjectURL(b), `diagrama-${{i + 1}}.png`)));
    }};
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(xml);
  }});
</script></body></html>
""", encoding="utf-8")
print(f"{len(bloques)} diagramas -> {SALIDA}")
