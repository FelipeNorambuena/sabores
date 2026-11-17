# Diseño del sistema

Bocetos de diseño del agente **Sabores** y de su capa de observabilidad (EV3). Los diagramas están en
[Mermaid](https://mermaid.js.org/): GitHub los dibuja directamente y `scripts/render_diagramas.py` genera una página
local para exportarlos como imagen (por ejemplo, para el informe).

Cada diagrama responde a una pregunta distinta:

| # | Diagrama | Pregunta que responde |
|---|---|---|
| 1 | Arquitectura de componentes | ¿De qué se compone el sistema y cómo se conecta? |
| 2 | Ciclo de un turno | ¿Qué pasa desde que el cliente escribe hasta que recibe la respuesta? |
| 3 | Árbol de trazas | ¿Cómo se registra un turno para poder depurarlo? |
| 4 | Flujo de evaluación | ¿Cómo se pasa de los escenarios al dashboard? |
| 5 | Capas de seguridad y privacidad | ¿Qué impide que el agente haga algo inseguro? |
| 6 | Perfiles baseline y optimizado | ¿Qué cambia entre las dos versiones y por qué? |

---

## 1. Arquitectura de componentes

El agente (EV2) no cambia de comportamiento por defecto: la observabilidad se agrega alrededor, mediante callbacks de
LangChain y un trazador, sin tocar los prompts.

```mermaid
flowchart TB
    C["Cliente (WhatsApp o chat)"] --> A

    subgraph AG["Agente Sabores"]
        direction TB
        A["AgenteSabores<br/>orquestador"]
        P["Planificador<br/>router, plan y replan"]
        E["Ejecutor ReAct<br/>function calling"]
        H["10 herramientas"]
        M["Memoria<br/>corto plazo y largo plazo"]
        A --> P
        A --> E
        A --> M
        E --> H
    end

    subgraph MOD["Modelo de lenguaje"]
        LLM["LLM por API<br/>Groq gpt-oss"]
    end

    subgraph DAT["Datos y embeddings"]
        DB[("SQLite<br/>carta, stock, perfiles,<br/>pedidos y derivaciones")]
        IDX[("FAISS<br/>carta y memoria episódica")]
        EMB["Embeddings<br/>Gemini"]
        IDX --> EMB
    end

    P --> LLM
    E --> LLM
    H --> DB
    M --> DB
    M --> IDX

    A -. "turno, pasos y llamadas" .-> T

    subgraph OBS["Observabilidad y evaluación"]
        direction TB
        T["Trazador<br/>callbacks LangChain, hook httpx<br/>y limitador medido"]
        LOG[/"logs/trazas.jsonl"/]
        EV["8 escenarios + runner"]
        RES[/"logs/resultados.jsonl"/]
        D["Dashboard Streamlit"]
        T --> LOG
        EV --> RES
        LOG --> D
        RES --> D
    end

    EV -. "ejecuta" .-> A
```

---

## 2. Ciclo de un turno

Un turno es lo que pasa entre un mensaje del cliente y su respuesta. El **router** decide si basta una llamada
(`directo`) o si hace falta un **plan** de pasos, que puede **replanificarse** cuando una herramienta informa una
condición nueva (plato agotado, pedido rechazado, error).

```mermaid
sequenceDiagram
    autonumber
    actor Cl as Cliente
    participant Ag as AgenteSabores
    participant Me as Memoria
    participant Pl as Planificador (LLM)
    participant Ej as Ejecutor (LLM)
    participant He as Herramientas + SQLite

    Cl->>Ag: mensaje
    Ag->>Me: recuperar perfil, recuerdos y resumen
    Me-->>Ag: contexto del cliente
    Ag->>Pl: decidir_modo(mensaje)
    alt modo directo
        Ag->>Ej: ejecutar(mensaje)
        Ej->>He: 0 o 1 herramienta
    else modo plan
        Ag->>Pl: crear_plan(mensaje, contexto)
        Pl-->>Ag: pasos ordenados por dependencia y prioridad
        loop por cada paso
            Ag->>Ej: ejecutar(paso)
            Ej->>He: tool calls
            He-->>Ej: OK / ERROR / AGOTADO / RECHAZADO / DERIVADO
            opt resultado con alerta
                Ag->>Pl: replanificar(condición)
                Pl-->>Ag: pasos nuevos
            end
        end
    end
    Ag->>Pl: redactar respuesta con los resultados
    Pl-->>Ag: respuesta final
    Ag->>Me: guardar turno
    Ag-->>Cl: respuesta
```

Límites contra bucles y gasto de tokens (`config.py`): máximo **4 iteraciones por paso** y **2 replanificaciones**;
al superarlos el caso se deriva al personal.

---

## 3. Árbol de trazas

Cada turno genera una **traza** (`trace_id`); cada operación es un **span** con `span_id` y `parent_span_id`. Se escribe
una línea JSON por span en `logs/trazas.jsonl`.

```mermaid
flowchart TD
    S["sesión · session_id<br/>agrupa los turnos de una conversación"] --> T
    T["turno · trace_id<br/>tokens, alertas, reintentos, modo"] --> R["recuperación<br/>FAISS + perfil"]
    T --> L1["llm: router"]
    T --> EV1["evento: modo"]
    T --> L2["llm: crear plan"]
    T --> EV2["evento: plan"]
    T --> PA["paso_1<br/>estado: ok o alerta"]
    T --> PB["paso_2"]
    T --> LF["llm: respuesta final"]
    PA --> L3["llm<br/>tokens_entrada · tokens_salida<br/>latencia_modelo_s · espera_limitador_s<br/>reintentos · errores_http"]
    PA --> H1["herramienta<br/>etiqueta OK / AGOTADO ...<br/>resultado enmascarado"]
    PA --> L4["llm"]
    PB --> L5["llm"]
    PB --> H2["herramienta"]
```

Atributos clave por tipo de span:

| Span | Atributos |
|---|---|
| `turno` | `modo`, `n_llm`, `n_herramientas`, `n_alertas`, `n_reintentos`, tokens (entrada, salida, razonamiento), `derivado` |
| `llm` | `modelo`, tokens, `latencia_modelo_s`, `espera_limitador_s`, `intentos_http`, `reintentos`, `errores_http` |
| `herramienta` | `args`, `etiqueta`, `resultado` (todo enmascarado) |
| `paso` | `descripcion`, `herramienta_sugerida`, `n_llamadas` |

La latencia de una llamada al modelo se separa en **espera del limitador** (nuestra propia cuota) y **latencia del
modelo** (que incluye los reintentos 429 del proveedor). Esa separación fue la que permitió ubicar el cuello de botella.

---

## 4. Flujo de evaluación

```mermaid
flowchart LR
    ESC["escenarios.py<br/>8 escenarios con verdad esperada"] --> RUN["runner.py<br/>agente nuevo por ejecución<br/>(base y FAISS aislados)"]
    RUN --> AG["Agente instrumentado"]
    AG --> TZ["logs/trazas.jsonl<br/>un span por línea"]
    RUN --> CHK["checks por escenario<br/>+ es_seguro en código"]
    CHK --> RS["logs/resultados.jsonl<br/>una línea por ejecución"]
    TZ --> MET["metricas.py<br/>pandas puro"]
    RS --> MET
    MET --> DASH["dashboard/app.py<br/>5 pestañas + conversaciones"]
    DASH --> EVI["evidencia/<br/>capturas para el informe"]
```

Principios de la evaluación:

- **La precisión no se juzga leyendo la respuesta.** Cada escenario declara las restricciones reales del cliente y la
  regla `es_seguro` (código, no LLM) decide si un plato recomendado o pedido era seguro.
- **Una falla del proveedor no es una falla del agente.** Las ejecuciones que terminan con error (por ejemplo, cuota
  diaria agotada) quedan como *no evaluables* y no cuentan contra la precisión ni contra la latencia.
- **Cada ejecución parte de cero:** base de datos e índice FAISS propios, para que un escenario no contamine a otro.

---

## 5. Capas de seguridad y privacidad

```mermaid
flowchart TD
    IN["Mensaje del cliente<br/>(puede traer prompt injection)"] --> L1
    L1["Capa 1 · Reglas de sistema<br/>solo información de las herramientas"] --> L2
    L2["Capa 2 · Function calling tipado<br/>un alérgeno inexistente se rechaza antes de ejecutar"] --> L3
    L3["Capa 3 · es_seguro en código<br/>filtrar_platos_seguros y validar_pedido"] --> L4
    L4["Capa 4 · crear_pedido revalida seguridad y stock<br/>aunque el LLM se salte un paso"] --> L5
    L5["Capa 5 · Alergia severa: pedido pendiente<br/>y derivación al personal"] --> L6
    L6["Capa 6 · Límites: 4 iteraciones por paso<br/>y 2 replanificaciones"] --> OUT["Respuesta"]

    subgraph PRIV["Privacidad en los registros"]
        P1["enmascarar(): email, RUT, teléfono"]
        P2["texto truncado a 400 caracteres"]
        P3["claves solo en .env (ignorado por git)"]
    end
    OUT -.-> PRIV
```

Qué cubre cada capa frente a los riesgos de OWASP Top 10 para aplicaciones LLM (las capas se numeran como en el diagrama):

| Riesgo | Mitigación |
|---|---|
| Prompt injection (directa) | Capas 3 y 4: aunque el modelo obedezca, el código no registra un pedido inseguro |
| Fuga del prompt de sistema | Regla de dominio; verificado con el escenario `injection_filtra_prompt` |
| Agotamiento de recursos (DoS por costo) | Capa 6: límites de iteraciones y replanificaciones; el limitador de llamadas |
| Datos personales en logs | Enmascaramiento antes de escribir; los logs no guardan claves |
| Decisión sin supervisión humana | Capa 5: la alergia severa siempre pasa por una persona |

> Limitación honesta: la mitigación de prompt injection se probó con **2 escenarios**, no con una batería adversarial.
> Que no hubiera violaciones en esas pruebas no demuestra que el agente sea inmune.

---

## 6. Perfiles baseline y optimizado

El mismo código corre en dos perfiles (`AGENTE_PERFIL`). Cada cambio del perfil optimizado responde a un hallazgo
medido en las trazas del baseline.

```mermaid
flowchart LR
    subgraph H["Hallazgos"]
        H1["79% de los tokens es entrada;<br/>10 esquemas de herramientas<br/>pesan ~1.000 tokens por llamada"]
        H2["hasta 6 consultas repetidas<br/>y 6 pasos por turno"]
        H3["59 a 73% de la salida<br/>es razonamiento interno"]
        H4["PROMPT_REPLAN ordenaba reemplazar<br/>el plato agotado"]
        H5["el modo directo<br/>nunca replanifica"]
    end
    subgraph O["Cambios"]
        O1["el ejecutor ve solo la<br/>herramienta del paso"]
        O2["se omiten consultas repetidas;<br/>plan de máximo 4 pasos"]
        O3["reasoning_effort bajo"]
        O4["prohíbe pedir un plato no solicitado<br/>y exige informar el agotado"]
        O5["todo pedido va por el plan"]
    end
    H1 --> O1
    H2 --> O2
    H3 --> O3
    H4 --> O4
    H5 --> O5
```

| Variable de entorno | Valores | Efecto |
|---|---|---|
| `AGENTE_PERFIL` | `baseline` (por defecto) · `optimizado` | Activa los cambios de la tabla anterior |
| `LLM_LLAMADAS_POR_MINUTO` | número (por defecto 8) | Ritmo del limitador propio de llamadas |
| `LLM_MODELO_PLANIFICADOR` / `LLM_MODELO_EJECUTOR` | id de modelo | Modelo de cada rol |
