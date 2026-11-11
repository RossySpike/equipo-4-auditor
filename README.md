# Equipo 04 - Agente Auditor (AG-AUD)

**Ruta Base:** `/mnt/shared/agentic/equipo-4-auditor`

## Documentación del Sistema

- **Visión General y Contratos JSON v1.0:** [`docs/overview/project_general_overview.md`](/mnt/shared/agentic/equipo-4-auditor/docs/overview/project_general_overview.md)
- **Especificación Técnica del Agente Auditor:** [`docs/agents/auditor-agente.md`](/mnt/shared/agentic/equipo-4-auditor/docs/agents/auditor-agente.md)

---

## API REST del Auditor

El servicio implementa el contrato JSON v1.0 del Agente Auditor (paso 5 del flujo) con FastAPI.

### Instalación y ejecución

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

La documentación interactiva queda disponible en `http://localhost:8000/docs`.

### Variables de entorno

| Variable | Default | Descripción |
| --- | --- | --- |
| `AUDITOR_SIGNING_KEY` | clave de desarrollo | Clave HMAC usada para firmar `content_hash`. **Obligatoria en producción.** |
| `AUDITOR_APPROVAL_THRESHOLD` | `85` | Score mínimo (0-100) para aprobar. |
| `AUDITOR_TRACE_PATH` | `data/audit_trace.jsonl` | Ruta del registro de traza append-only. |

### Endpoints

#### `GET /health`

```json
{ "status": "ok", "agent_id": "AG-AUD" }
```

#### `POST /invoke`

Recibe el sobre JSON v1.0 con `contract_version: "1.0"`, `agent_id: "AG-AUD"` e `input` con `brief`, `borrador`, `copy` y `context_pack`.

```bash
curl -X POST http://localhost:8000/invoke \
  -H "Content-Type: application/json" \
  -d '{
    "contract_version": "1.0",
    "execution_id": "EXE-0007",
    "step": 5,
    "iteration": 1,
    "agent_id": "AG-AUD",
    "business_profile_id": "BP-001",
    "user_request": "Quiero una publicacion para promocionar la liquidacion de ropa deportiva",
    "input": {
      "brief": {
        "idea_id": "IDEA-01",
        "objetivo": "Trafico a tienda",
        "audiencia": "Adultos 25-40"
      },
      "borrador": {
        "asset_url": "https://cdn.example.com/posts/liquidacion.jpg",
        "alt_text": "Modelo en tienda deportiva con prendas en oferta"
      },
      "copy": {
        "caption": "¡Este fin de semana tu rutina se renueva! 20% OFF en ropa deportiva. Visita la tienda.",
        "hashtags": ["#Deporte", "#Oferta", "#FinDeSemana"],
        "facts_used": ["FACT-012"]
      },
      "context_pack": {
        "empresa": "RetailVenture C.A.",
        "promociones": ["20% OFF en ropa deportiva (Sab-Dom)"],
        "facts": [{"id": "FACT-012", "texto": "20% OFF en ropa deportiva (Sab-Dom)"}],
        "reglas_marca": {"palabras_prohibidas": ["barato"]},
        "tono": "cercano"
      }
    },
    "sent_at": "2026-11-11T14:32:10-04:00"
  }'
```

La respuesta contiene:

- `output.checks[]`: los siete controles (ortografía, coherencia, veracidad, estrategia, marca, riesgo y límites de Instagram) con `resultado` `PASSED`/`FAILED`.
- `output.score`: puntaje 0-100.
- `output.veredicto`: `APROBADO` o `RECHAZADO`.
- `output.return_to`: `AG-EST`, `AG-BOR`, `AG-COP` o `null` si fue aprobado.
- `output.approval_id`, `output.content_hash` y `output.content_signature` (solo en aprobaciones).
- `data_sources[]`: promociones y hechos usados como evidencia.

El `content_hash` es `SHA-256(asset_url + "\n" + caption + "\n" + ",".join(hashtags))`; la firma es `HMAC-SHA256(AUDITOR_SIGNING_KEY, content_hash)`, de modo que AG-PUB puede recalcular y verificar la integridad antes de publicar.

### Pruebas

```bash
python3 -m pytest
```

---

## Estructura del Repositorio

```text
/mnt/shared/agentic/equipo-4-auditor/
├── .opencode/                      # Skills y Comandos de OpenSpec (auto-generados)
├── openspec/                       # Configuración, especificaciones y cambios
├── docs/
│   ├── overview/
│   │   └── project_general_overview.md
│   └── agents/
│       └── auditor-agente.md
├── app/                            # Servicio FastAPI del AG-AUD
│   ├── main.py                     # Endpoints /health y /invoke
│   ├── models.py                   # Contrato JSON v1.0 (Pydantic)
│   ├── checks.py                   # Matriz de 7 verificaciones
│   ├── service.py                  # Score, veredicto, routing y firma
│   ├── signing.py                  # Hash SHA-256 y HMAC
│   ├── trace.py                    # Traza JSONL append-only
│   └── config.py                   # Variables de entorno
├── tests/                          # Pruebas de contrato, checks, veredicto y traza
├── requirements.txt
├── pytest.ini
├── README.md
└── .opencode.json                  # Configuración de Ollama y OpenRouter
```

## Ejecución de Pruebas Unitarias

Dado que el módulo principal se encuentra en la carpeta `app/`, **siempre debes ejecutar las pruebas con `python3 -m pytest`** para incluir la raíz del proyecto en el `sys.path` de Python:

```bash
# Ejecutar todas las pruebas unitarias
python3 -m pytest

# Ejecutar con salida detallada (verbose)
python3 -m pytest -v
```
