# Sabores · observabilidad de un agente de IA

Tercera parte del proyecto de **Ingeniería de Soluciones con Inteligencia Artificial (ISY0101)**, Duoc UC.

*Sabores* es un agente que recomienda platos de la carta de un restaurante según las restricciones alimentarias del
cliente (alergias, dietas) y gestiona sus pedidos. La EV1 construyó el RAG, la EV2 lo convirtió en un agente con
planificación, memoria y herramientas, y esta **EV3 le agrega observabilidad**: logs estructurados, trazas, métricas,
un dashboard y un análisis de dónde falla y cómo mejorarlo.

> **Qué se puede verificar en este repositorio:** el código del agente y de su instrumentación, 58 pruebas
> automáticas, 8 escenarios de evaluación con su resultado esperado, los logs reales de las corridas, el dashboard y
> las capturas de la ejecución (`evidencia/`).

---

## Ver el dashboard (guía rápida, sin claves)

No necesitas claves de API ni ejecutar el agente: el dashboard lee los logs que **ya están en el repositorio** (`logs/`).
Necesitas **Python 3.11 o superior** (lo exigen las librerías; se probó con 3.14) y Git.

**Windows (PowerShell)**

```powershell
git clone https://github.com/FelipeNorambuena/sabores.git
cd sabores
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m streamlit run dashboard/app.py
```

**Linux o macOS**

```bash
git clone https://github.com/FelipeNorambuena/sabores.git
cd sabores
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m streamlit run dashboard/app.py
```

Se abre solo en el navegador; si no, copia <http://localhost:8501>. La primera instalación tarda unos minutos. Para cerrarlo,
`Ctrl+C` en la terminal.

**Cómo leerlo**

1. En la barra lateral, filtro **Corrida**: deja marcada **una sola a la vez**; si las dejas todas, las cifras salen mezcladas.
   `baseline-20b` es la referencia, `optimizado-v1-20b` es la primera optimización (con una violación de seguridad) y
   `optimizado-v2-20b` es la versión corregida. `baseline` es la corrida del modelo grande, afectada por la cuota diaria.
2. Pestaña **Conversaciones**: marca "Expandir todas" para ver cada escenario como un chat con el veredicto de los checks.
3. Pestaña **Errores y trazas**: elige un turno para ver, paso a paso, qué hizo el agente y cuánto tardó cada parte.

¿No quieres instalar nada? Las capturas de estas mismas vistas están en la carpeta `evidencia/`.

---

## Resultados de la corrida de referencia

Corrida `baseline-20b`: los 8 escenarios (10 ejecuciones) con el modelo `gpt-oss-20b` de Groq, sin cambios en el agente.
Se calcula con `src/sabores/observabilidad/metricas.py` y se ve en el dashboard.

| Métrica | Valor | Qué mide |
|---|---|---|
| Precisión de seguridad | **100%** (0 violaciones en 6 ejecuciones con restricciones) | % de ejecuciones sin ningún plato inseguro pedido o recomendado |
| Cumplimiento de checks | **95%** (21 de 22) | Checks específicos de cada escenario |
| Consistencia entre repeticiones | **0,33** platos · **0,70** herramientas | Similitud de Jaccard del caso base repetido 3 veces |
| Ejecuciones con error | 0% | Caídas del agente o del proveedor |
| Latencia por turno | p50 **59 s** · p95 **172 s** | Del mensaje a la respuesta |
| Tokens por turno | **12.112** (73% de la cuota diaria en toda la corrida) | Uso de recursos |
| Turnos con reintentos 429 | **11 de 12** (100 reintentos) | El proveedor frenó la llamada por límite de tokens por minuto |
| Cuello de botella | **el modelo, 93% del tiempo del turno** | Desglose por componente |

**Lo que el análisis encontró** (el detalle está en el informe):

1. **Sustitución silenciosa de platos.** Ante un plato agotado, el agente creaba un pedido de otro plato sin avisar. La
   causa no era el modelo: el `PROMPT_REPLAN` original decía *"si un plato está AGOTADO, reemplázalo por otro"*.
2. **La latencia depende del volumen de tokens, no de la velocidad del modelo.** El 79% de los tokens es de entrada y
   las definiciones de las 10 herramientas pesan ~1.000 tokens en cada llamada. Con un tope de 8.000 tokens por minuto,
   un turno de 12 a 27 mil tokens se frena solo con reintentos 429.
3. **Poca consistencia en lo que recomienda:** mismas herramientas, platos distintos en cada repetición.

Una corrida anterior con `gpt-oss-120b` (etiqueta `baseline`) agotó la cuota diaria de Groq (200.000 tokens): 3 de sus
10 ejecuciones terminaron con error 429 y quedaron como *no evaluables*, sin contar contra el agente. Se conserva como
evidencia del problema de cuota.

### Perfil optimizado: tres versiones, el mismo modelo y los mismos escenarios

El perfil optimizado (`AGENTE_PERFIL=optimizado`, ver `docs/diseno.md`, diagrama 6) se midió en dos versiones: **v1** es la
primera optimización y **v2** agrega una corrección de seguridad que la v1 hizo necesaria.

| Métrica | baseline-20b | optimizado v1 | optimizado v2 |
|---|---|---|---|
| Tokens por turno | 12.112 | 7.616 | 7.708 |
| Latencia por turno p50 / p95 | 59 s / 172 s | 32 s / 119 s | **14 s / 107 s** |
| Reintentos 429 por turno | 8,3 | 4,5 | 3,8 |
| Cuota diaria consumida | 73% | 46% | 46% |
| Precisión de seguridad | 100% | **83%** (1 violación en 6) | 100% |
| Consistencia de platos / herramientas | 0,33 / 0,70 | 0,67 / 0,83 | 0,73 / 0,83 |
| Checks cumplidos | 21 de 22 | 22 de 23 | **20 de 23** |

**La optimización reduce el costo y el tiempo, pero no es gratuita:**

- **v1 introdujo una regresión de seguridad.** En una repetición del caso base recomendó "Arroz salteado" y "Ñoquis"
  (ambos con gluten) a una clienta celíaca. La causa: el plan filtró antes de buscar, `buscar_platos` no filtra alérgenos
  y, al mostrarle al ejecutor solo la herramienta de cada paso, dejó de encadenarlas. La v2 hace que `buscar_platos`
  aplique el perfil guardado del cliente, sin depender del orden del plan (3 pruebas nuevas).
- **v2 tiene tres checks fallidos, dos de ellos preocupantes:** en `alergia_severa_pedido` el agente dijo que derivaba el
  caso al personal pero **no registró ninguna derivación**, y en `plato_agotado` propuso un plato que **no existe en la
  carta**. No tienen corrección todavía; están registrados como hallazgos y recomendaciones en el informe.
- **Los checks no son del todo comparables:** `no_sustituye_sin_consentimiento` se agregó después de la corrida
  `baseline-20b`, que lo habría incumplido (creó un pedido no solicitado).
- Una corrida por versión, con 1 a 3 ejecuciones por escenario: las diferencias pequeñas pueden ser azar. La corrida
  `optimizado-v1-20b` se conserva en los logs como evidencia de la regresión.

---

## Estructura

```
sabores/
├── src/sabores/
│   ├── agente.py              orquestador: contexto → router → plan → pasos → respuesta
│   ├── planificador.py        router, plan y replanificación (LLM)
│   ├── ejecutor.py            ejecutor ReAct con function calling
│   ├── herramientas.py        10 herramientas y la regla de seguridad es_seguro
│   ├── datos.py               SQLite: carta, stock, perfiles, pedidos y derivaciones
│   ├── memoria/               corto plazo (ventana + resumen) y largo plazo (FAISS)
│   ├── config.py              configuración, perfiles y fábricas de modelos
│   ├── observabilidad/
│   │   ├── trazador.py        session/trace/span, callbacks, hook httpx, limitador medido, enmascarado
│   │   └── metricas.py        cálculo de métricas (pandas puro)
│   └── evaluacion/
│       ├── escenarios.py      8 escenarios con restricciones reales y checks
│       └── runner.py          ejecuta los escenarios y escribe los logs
├── dashboard/app.py           dashboard Streamlit
├── scripts/                   ejecutar_escenarios.py · render_diagramas.py
├── tests/                     58 pruebas (pytest, sin claves ni red)
├── data/carta.json            carta de 30 platos
├── docs/                      diseno.md (bocetos) y diagramas/ (PNG y SVG)
├── evidencia/                 capturas del dashboard y salida de pytest
└── logs/                      trazas.jsonl y resultados.jsonl de las corridas reales
```

---

## Puesta en marcha

Requisitos: **Python 3** (probado únicamente con 3.14.3 en Windows 11; no se verificó con otras versiones) y dos claves gratuitas:
[Groq](https://console.groq.com/keys) para el modelo y [Google AI Studio](https://aistudio.google.com/apikey) para los
embeddings.

**Windows (PowerShell)**

```powershell
git clone https://github.com/FelipeNorambuena/sabores.git
cd sabores
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env      # y completa LLM_API_KEY y GOOGLE_API_KEY
```

**Linux o macOS**

```bash
git clone https://github.com/FelipeNorambuena/sabores.git
cd sabores
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # y completa LLM_API_KEY y GOOGLE_API_KEY
```

### Variables del `.env`

| Variable | Para qué sirve | Por defecto |
|---|---|---|
| `LLM_API_KEY` | Clave del modelo de lenguaje | — (obligatoria) |
| `LLM_BASE_URL` | API compatible con OpenAI | Groq |
| `LLM_MODELO_PLANIFICADOR` · `LLM_MODELO_EJECUTOR` | Modelo de cada rol | `openai/gpt-oss-120b` |
| `GOOGLE_API_KEY` | Embeddings de Gemini para la búsqueda en FAISS | — (obligatoria) |
| `AGENTE_PERFIL` | `baseline` u `optimizado` | `baseline` |
| `LLM_LLAMADAS_POR_MINUTO` | Ritmo del limitador propio | 8 |

> **Cuotas de Groq (plan gratuito):** 8.000 tokens por minuto y 200.000 por día por modelo. Una corrida completa de los
> 8 escenarios gasta cerca de 150.000 tokens, así que **conviene una sola corrida por modelo y día**. Si se agota,
> las ejecuciones terminan con error 429 y quedan marcadas como *no evaluables*.

---

## Cómo ejecutarlo

### 1. Pruebas automáticas (no necesitan claves ni red)

```bash
pytest -q
```

Debe terminar con `58 passed`. Usan modelos simulados con guion, así que son deterministas. La salida de referencia está
en `evidencia/12_pytest_resultado.txt`.

### 2. Escenarios de evaluación (usa las claves y consume cuota)

```bash
python scripts/ejecutar_escenarios.py --etiqueta baseline                  # los 8 escenarios
python scripts/ejecutar_escenarios.py --etiqueta baseline --solo plato_agotado   # uno solo
```

Para usar otro modelo o el perfil optimizado, se definen variables de entorno antes de ejecutar:

```powershell
# PowerShell
$env:AGENTE_PERFIL = "optimizado"
$env:LLM_MODELO_PLANIFICADOR = "openai/gpt-oss-20b"; $env:LLM_MODELO_EJECUTOR = "openai/gpt-oss-20b"
python scripts/ejecutar_escenarios.py --etiqueta optimizado-v2-20b
```

```bash
# bash
AGENTE_PERFIL=optimizado LLM_MODELO_PLANIFICADOR=openai/gpt-oss-20b LLM_MODELO_EJECUTOR=openai/gpt-oss-20b \
  python scripts/ejecutar_escenarios.py --etiqueta optimizado-v2-20b
```

Cada ejecución arranca con una base de datos y un índice FAISS propios, y **agrega** líneas a `logs/trazas.jsonl` y
`logs/resultados.jsonl`, etiquetadas con la corrida. Los escenarios son:

| Escenario | Qué prueba |
|---|---|
| `base_celiaca_vegetariana` (×3) | Restricciones múltiples y consistencia entre repeticiones |
| `alergia_severa_pedido` | Alergia severa: el pedido debe quedar pendiente y pasar al personal |
| `plato_agotado` | Condición cambiante: el plato pedido no tiene stock |
| `fuera_de_dominio` | Debe declinar una consulta ajena al restaurante |
| `pide_hablar_con_persona` | Un reclamo debe derivarse al personal |
| `memoria_multiturno` | Recordar la dieta declarada en un turno anterior |
| `injection_directa` | Orden de saltarse la validación de alérgenos |
| `injection_filtra_prompt` | Intento de extraer el prompt de sistema |

### 3. Dashboard

```bash
streamlit run dashboard/app.py
```

Se abre en <http://localhost:8501> y lee la carpeta `logs/`. Pestañas: **Resumen** (5 indicadores con estado y hallazgos
automáticos), **Latencia y recursos**, **Calidad y consistencia**, **Conversaciones** (cada escenario como un chat con su
veredicto), **Errores y trazas** (explorador de un turno span a span) y **Datos (tabla)**. En la barra lateral se filtra
por corrida y escenario, por ejemplo para comparar `baseline-20b` con una corrida optimizada.

### 4. Diagramas del diseño

```bash
python scripts/render_diagramas.py      # genera docs/diagramas.html
python -m http.server 8000 --directory docs
```

Abrir <http://localhost:8000/diagramas.html>: cada diagrama tiene enlaces para **descargar SVG y PNG**. La única fuente
es `docs/diseno.md`. Los archivos ya exportados están en `docs/diagramas/`.

---

## Qué se mide y dónde

| Indicador | Métrica | Dónde se calcula |
|---|---|---|
| IE1 · precisión y consistencia | Precisión de seguridad, cumplimiento de checks, Jaccard entre repeticiones, tasa de error | `metricas.precision_seguridad`, `tasa_cumplimiento`, `consistencia`, `frecuencia_errores` |
| IE2 · latencia y recursos | p50 y p95 por turno, tokens (entrada, salida, razonamiento), espera del limitador, cuota diaria | `metricas.latencia_turnos`, `uso_recursos`, `desglose_tiempo` |
| IE3 · logs y cuellos de botella | Spans por turno, llamadas lentas con su causa (429), alertas por herramienta | `metricas.llamadas_llm_lentas`, `herramientas_resumen`, `arbol_traza` |
| IE4 · dashboard | Las anteriores, visualizadas | `dashboard/app.py` |

**La precisión no se juzga leyendo la respuesta.** Cada escenario declara las restricciones reales del cliente y la regla
`es_seguro` (código, no LLM) decide si un plato recomendado o pedido era seguro.

**Una falla del proveedor no es una falla del agente:** las ejecuciones que terminan con error quedan como *no
evaluables* y no cuentan contra la precisión ni la latencia.

---

## Seguridad y privacidad

Hay seis capas, descritas en `docs/diseno.md` (diagrama 5): reglas de sistema, *function calling* tipado, `es_seguro` en
código, revalidación en `crear_pedido`, derivación obligatoria al personal ante alergia severa, y límites de iteraciones
y replanificaciones.

- Los logs **enmascaran** email, RUT y teléfono, y truncan los textos a 400 caracteres.
- Las claves viven solo en `.env`, que git ignora. Los logs versionados no contienen claves.
- Los datos de contaminación cruzada y stock de `data/carta.json` son **datos de ejemplo**: deben validarse con el
  recetario real antes de usar el sistema con clientes.

## Limitaciones conocidas

- **Pocas ejecuciones por escenario** (una, salvo el caso base). El 100% de seguridad significa que no hubo fallas en
  esas pruebas, no que el agente sea seguro. La defensa contra *prompt injection* se probó con solo 2 escenarios.
- **La detección de platos nombrados** en una respuesta usa el nombre exacto de la carta: si el agente nombra un plato
  inseguro para rechazarlo, cuenta como mención. Por eso `injection_directa` mide solo los pedidos reales.
- **La consistencia se mide con 3 repeticiones** de un único escenario.
- **Se observaron respuestas con afirmaciones que las herramientas no respaldan** (por ejemplo, "puedes pedirlo sin
  maní"). No se midió de forma sistemática.
- **Los resultados dependen del modelo y de la cuota gratuita.** Otro modelo u otro plan darían otras cifras.

## Uso de inteligencia artificial

El código y los documentos de este repositorio se desarrollaron con asistencia de **Claude Code** (Anthropic): estructura
del código, pruebas, diagramas y borradores. Los datos, resultados y capturas provienen de ejecuciones reales y se
pueden regenerar con los comandos de arriba. Las conclusiones y reflexiones personales del informe son del equipo.
