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
| `AI_KEY` | vacío | Credencial de Google Gemini. Es secreta y se lee del `.env` de la raíz; nunca se incluye en respuestas ni trazas. |
| `AUDITOR_AI_ENABLED` | `false` | Activa la revisión semántica opcional con Gemini. |
| `AUDITOR_AI_MODEL` | `gemini-2.5-flash` | Modelo usado cuando la revisión con IA está activa. |
| `AUDITOR_AI_TIMEOUT_SECONDS` | `20` | Tiempo máximo, en segundos, de cada llamada a Gemini. |

Las variables de entorno del proceso prevalecen sobre los valores del archivo `.env`. Use `.env.example` como plantilla; nunca versione el archivo `.env` real.

### Revisión opcional con Gemini

La auditoría determinista es la base del servicio y sigue funcionando sin conexión ni credenciales. Cuando `AUDITOR_AI_ENABLED=true` y `AI_KEY` está configurada, Gemini (`gemini-2.5-flash`, temperatura `0`, respuesta JSON) revisa únicamente los cinco criterios semánticos: ortografía, coherencia texto–imagen–idea, alineación estratégica, voz y tono, y riesgo de afirmaciones. La veracidad comercial y los límites de Instagram permanecen deterministas.

La reconciliación es *fail-closed*: Gemini puede convertir un `PASSED` determinista en `FAILED`, pero nunca puede aprobar lo que el motor determinista rechazó. El `score`, el veredicto, el enrutamiento, `data_sources`, `approval_id`, `content_hash` y `content_signature` se siguen calculando con las reglas deterministas. Ante timeout, cuota agotada, bloqueo de seguridad o JSON inválido, el servicio responde `200` con la auditoría determinista y añade una advertencia sanitizada.

Para habilitarla localmente:

```bash
cp .env.example .env
# Edite .env y defina AI_KEY y AUDITOR_AI_ENABLED=true
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Las pruebas desactivan la IA automáticamente y usan un cliente falso; nunca realizan llamadas de red ni necesitan una credencial.

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

### Prueba E2E con curl

Prueba de humo contra el servicio real: levanta la API, invoca `/invoke` con el sobre JSON v1.0 y comprueba el veredicto, los siete controles, la firma y la traza. El payload de ejemplo está en [`examples/payload.json`](examples/payload.json); cópielo como `payload.json` y ejecute el servicio con una clave conocida:

```bash
# Terminal A: servicio con clave y traza conocidas
AUDITOR_SIGNING_KEY=demo-key \
AUDITOR_TRACE_PATH=/tmp/auditor_trace.jsonl \
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

```bash
# Terminal B: prueba E2E
set -euo pipefail

# 0. Payload de ejemplo del contrato JSON v1.0
cp examples/payload.json payload.json

# 1. Salud del servicio
curl -sf http://127.0.0.1:8000/health | grep -q '"agent_id":"AG-AUD"'

# 2. Invocación completa; debe responder HTTP 200
http_code=$(curl -sS -o response.json -w '%{http_code}' \
  -X POST http://127.0.0.1:8000/invoke \
  -H "Content-Type: application/json" \
  --data @payload.json)
[ "$http_code" = "200" ]

# 3. Verificación del contrato, la firma y la traza
python3 - <<'PY'
import hashlib, hmac, json

body = json.load(open("response.json", encoding="utf-8"))
assert body["status"] == "completed"
out = body["output"]
assert out["veredicto"] == "APROBADO"
assert out["score"] == 100
assert out["approval_id"] and out["content_hash"] and out["content_signature"]
assert len(out["checks"]) == 7
assert all(check["resultado"] == "PASSED" for check in out["checks"])

# AG-PUB puede recalcular hash y firma antes de publicar
payload = json.load(open("payload.json", encoding="utf-8"))
copy = payload["input"]["copy"]
base = "\n".join([
    payload["input"]["borrador"]["asset_url"],
    copy["caption"],
    ",".join(copy["hashtags"]),
])
expected_hash = hashlib.sha256(base.encode()).hexdigest()
assert expected_hash == out["content_hash"]
expected_signature = hmac.new(
    b"demo-key", expected_hash.encode(), hashlib.sha256
).hexdigest()
assert expected_signature == out["content_signature"]

print("E2E OK:", out["approval_id"], out["content_hash"])
PY

# 4. La invocación dejó una línea en la traza append-only
[ -s /tmp/auditor_trace.jsonl ]
```

Salida esperada (los identificadores y horas varían):

```text
E2E OK: APP-20261111-f5a3b96f 59926a1669265fc7e93841c07c9b1d03dabf0c7675636effd4fe64450b1f85c3
```

Para el camino de rechazo, cambie el copy por una afirmación no sustentada; la API responde `200` con `status: "rejected"`, `veredicto: "RECHAZADO"`, `approval_id: null` y `return_to: "AG-COP"`:

```bash
python3 - <<'PY'
import json
body = json.load(open("payload.json", encoding="utf-8"))
body["input"]["copy"]["caption"] = "La mejor calidad del mercado. Visita la tienda."
json.dump(body, open("payload_rejected.json", "w", encoding="utf-8"), ensure_ascii=False)
PY

curl -s -X POST http://127.0.0.1:8000/invoke \
  -H "Content-Type: application/json" \
  --data @payload_rejected.json \
| python3 -c '
import json, sys
body = json.load(sys.stdin)
assert body["status"] == "rejected"
assert body["output"]["veredicto"] == "RECHAZADO"
assert body["output"]["approval_id"] is None
assert body["output"]["return_to"] == "AG-COP"
print("E2E rechazo OK")
'
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
│   ├── gemini.py                   # Revisión semántica opcional y reconciliación
│   ├── service.py                  # Score, veredicto, routing y firma
│   ├── signing.py                  # Hash SHA-256 y HMAC
│   ├── trace.py                    # Traza JSONL append-only
│   └── config.py                   # Variables de entorno (.env y proceso)
├── examples/
│   └── payload.json                # Payload de ejemplo del contrato JSON v1.0
├── tests/                          # Suite exhaustiva: contrato, checks, IA, config, traza
├── .env.example                    # Plantilla de variables de entorno
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

La suite cubre endpoints y validación del contrato, las siete verificaciones con sus casos límite, puntaje/veredicto/enrutamiento, firma y traza, configuración y la integración con Gemini (prompt, parseo estricto, SDK simulado, reconciliación *fail-closed* y degradación determinista). No requiere red ni credenciales: un fixture autouse mantiene la IA desactivada y `tests/test_gemini.py` usa dobles de prueba.
