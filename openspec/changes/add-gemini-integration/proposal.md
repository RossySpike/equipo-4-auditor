# Proposal

## Why

Las siete verificaciones actuales son heurísticas deterministas: detectan formato, solapamiento de términos y números, pero no pueden juzgar de forma semántica la claridad, la coherencia editorial, el tono ni las afirmaciones engañosas. Integrar Google Gemini (`gemini-2.5-flash`) como evaluador opcional mejora esos juicios sin romper el contrato JSON v1.0 ni la suite de pytest, que exige resultados reproducibles y campos exactos.

## What Changes

- Migrar `app/config.py` de `dataclass` + `os.getenv` a `pydantic-settings.BaseSettings`, leyendo `AI_KEY` desde el `.env` de la raíz del repositorio, con variables de entorno que prevalecen sobre el archivo.
- Añadir configuración explícita: `AUDITOR_AI_ENABLED` (por defecto `false`), `AUDITOR_AI_MODEL` (`gemini-2.5-flash`) y `AUDITOR_AI_TIMEOUT_SECONDS` (por defecto `20`).
- Añadir un adaptador `app/gemini.py` que importe `google.generativeai` de forma perezosa, configure la API key, cree el modelo con temperatura `0` y respuesta JSON, construya el prompt desde el `AuditRequest` y valide la respuesta.
- Aplicar Gemini solo a las verificaciones semánticas (ortografía, coherencia, estrategia, marca y riesgo). Veracidad comercial y límites de Instagram siguen siendo deterministas.
- Reconciliar de forma *fail-closed*: Gemini solo puede convertir un `PASSED` determinista en `FAILED`; nunca puede aprobar lo que el motor determinista rechazó.
- Mantener intactos `score`, `veredicto`, `checks[]` (orden y nombres), `hallazgos[]`, `return_to`, `data_sources`, `approval_id`, `content_hash`, `content_signature`, `warnings`, `error` y timestamps.
- Ante cualquier fallo de Gemini (timeout, cuota, bloqueo de seguridad, JSON inválido), usar la auditoría determinista y registrar una advertencia sanitizada, sin exponer la clave.
- Añadir dependencias `pydantic-settings` y `google-generativeai`, aislar la IA en pytest (desactivada por defecto) y añadir pruebas con un cliente falso.
- Documentar las nuevas variables en `README.md` y `.env.example`.

## Capabilities

### New Capabilities

<!-- Ninguna: la integración extiende el comportamiento del servicio existente. -->

### Modified Capabilities

- `auditor-api`: configuración de IA desde el entorno, activación opcional de Gemini 2.5 Flash, reconciliación que preserva el contrato de respuesta y degradación determinista ante fallos.

## Impact

- Código: `app/config.py`, `app/service.py`, nuevo `app/gemini.py`, `tests/conftest.py` y nuevos `tests/test_gemini.py`; `requirements.txt`, `README.md` y `.env.example`.
- Dependencias de ejecución: `pydantic-settings>=2.2,<3.0` y `google-generativeai>=0.8,<1.0`.
- Configuración: nueva `AI_KEY` (secreta, leída de `.env`), `AUDITOR_AI_ENABLED`, `AUDITOR_AI_MODEL` y `AUDITOR_AI_TIMEOUT_SECONDS`.
- API: sin cambios de ruta, esquema ni códigos HTTP. Con la IA desactivada, la respuesta es idéntica a la actual.
- Operación: llamadas salientes a la API de Gemini solo cuando `AUDITOR_AI_ENABLED=true`; coste y latencia acotados por el timeout.
- Seguridad: la clave nunca se incluye en respuestas, trazas ni logs; `.env` ya está en `.gitignore`.
