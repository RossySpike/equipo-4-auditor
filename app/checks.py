"""Deterministic implementation of the seven AG-AUD audit checks.

The checks are intentionally rule based: verdicts must be reproducible and
explainable, and the service must work without an external language model.
Each check returns the public `CheckResult` fields plus scoring metadata.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from .models import AuditRequest, ContextPack, DataSource

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

STOPWORDS = {
    "a", "al", "algo", "algun", "alguna", "algunas", "alguno", "algunos",
    "ante", "antes", "como", "con", "contra", "cual", "cuando", "de", "del",
    "desde", "donde", "dos", "el", "ella", "ellas", "ellos", "en", "entre",
    "era", "es", "esa", "esas", "ese", "eso", "esos", "esta", "estas",
    "este", "esto", "estos", "fue", "ha", "hasta", "hay", "la", "las", "le",
    "les", "lo", "los", "mas", "me", "mi", "mis", "mucho", "muy", "no",
    "nos", "o", "para", "pero", "poco", "por", "porque", "que", "quien",
    "se", "sin", "sobre", "su", "sus", "te", "tu", "tus", "un", "una",
    "unas", "uno", "unos", "y", "ya", "the", "and", "for", "with", "you",
    "your", "our", "this", "that", "are", "was", "were", "from",
}

TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)

CTA_TERMS = (
    "compra", "comprar", "visita", "ordena", "aprovecha", "descubre",
    "reserva", "llama", "escríbenos", "siguenos", "síguenos", "haz tu pedido",
    "pide", "haz clic", "clic aquí", "consigue", "lleva", "regístrate",
    "solicita", "contáctanos", "link en bio", "enlace en bio", "shop now",
    "buy now", "order now", "click here", "visit",
)

RISK_TERMS = (
    "garantizado", "garantizada", "garantizados", "garantizadas",
    "garantía total", "el mejor", "la mejor", "único", "única", "siempre",
    "nunca", "milagro", "sin riesgo", "definitivo", "definitiva", "infalible",
    "resultados garantizados", "éxito asegurado", "100%",
)

PLACEHOLDER_RE = re.compile(r"\b(TODO|TBD|XXX|Lorem ipsum)\b", re.IGNORECASE)
MULTISPACE_RE = re.compile(r"\S {2,}\S")
SPACE_BEFORE_PUNCT_RE = re.compile(r"\s+[,.!?;:]")
REPEATED_PUNCT_RE = re.compile(r"[!?]{3,}|,\s*,|;\s*;|:\s*:")
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)?")
PERCENT_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*%")
PRICE_RE = re.compile(
    r"(?:\$|USD|EUR|Bs\.?)\s*(\d+(?:[.,]\d+)?)"
    r"|(\d+(?:[.,]\d+)?)\s*(?:USD|EUR|Bs\.?)",
    re.IGNORECASE,
)


@dataclass
class CheckOutcome:
    """Result of one check plus the metadata used for scoring and routing."""

    criterio: str
    weight: int
    severe: bool
    route: str
    passed: bool
    evidencia: str
    hallazgo: str | None = None
    data_sources: list[DataSource] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def meaningful_tokens(text: str) -> set[str]:
    return {
        token.lower()
        for token in TOKEN_RE.findall(text)
        if len(token) > 2 and token.lower() not in STOPWORDS
    }


def _number_key(raw: str) -> str:
    value = raw.replace(",", ".")
    try:
        return f"{float(value):g}"
    except ValueError:
        return value


def _numbers_in(text: str) -> set[str]:
    return {_number_key(match) for match in NUMBER_RE.findall(text)}


def _stringify(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(str(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return " ".join(_stringify(item) for item in value)
    return str(value)


def _context_corpus(context: ContextPack) -> str:
    parts = [
        _stringify(context.empresa or ""),
        _stringify(context.promociones),
        _stringify(context.facts),
        _stringify(context.reglas_marca or ""),
    ]
    return " ".join(parts).lower()


def _fact_ids(context: ContextPack) -> set[str]:
    identifiers: set[str] = set()
    for entry in context.facts:
        if isinstance(entry, dict):
            identifier = entry.get("id") or entry.get("fact_id")
            if identifier:
                identifiers.add(str(identifier))
        else:
            match = re.match(r"\s*([A-Za-z]{2,}-\d+)", str(entry))
            if match:
                identifiers.add(match.group(1))
    return identifiers


def _entry_ref(entry: str | dict, prefix: str, index: int) -> str:
    if isinstance(entry, dict):
        identifier = entry.get("id") or entry.get("ref") or entry.get("promo_id")
        if identifier:
            return str(identifier)
    return f"{prefix}-{index + 1:03d}"


def _prohibited_terms(context: ContextPack) -> list[str]:
    terms = [str(term).strip() for term in context.palabras_prohibidas if str(term).strip()]
    rules = context.reglas_marca

    if isinstance(rules, dict):
        for key in (
            "palabras_prohibidas",
            "prohibidas",
            "terminos_prohibidos",
            "forbidden_terms",
        ):
            value = rules.get(key)
            if isinstance(value, str):
                terms.append(value.strip())
            elif isinstance(value, (list, tuple)):
                terms.extend(str(item).strip() for item in value)

    if isinstance(rules, list):
        for item in rules:
            if isinstance(item, dict):
                for key in ("termino", "term", "palabra", "value"):
                    if item.get(key):
                        terms.append(str(item[key]).strip())

    return [term for term in terms if term]


def _canonical(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def check_spelling(request: AuditRequest) -> CheckOutcome:
    caption = request.input.copy_payload.caption
    issues: list[str] = []

    if not caption.strip():
        issues.append("el caption está vacío")
    if PLACEHOLDER_RE.search(caption):
        issues.append("el caption contiene un marcador de plantilla")
    for opening, closing in (("(", ")"), ("[", "]"), ("{", "}")):
        if caption.count(opening) != caption.count(closing):
            issues.append(f"delimitadores desbalanceados: {opening}{closing}")
    if MULTISPACE_RE.search(caption):
        issues.append("el caption contiene espacios dobles")
    if SPACE_BEFORE_PUNCT_RE.search(caption):
        issues.append("hay espacio antes de un signo de puntuación")
    if REPEATED_PUNCT_RE.search(caption):
        issues.append("el caption repite signos de puntuación")

    if issues:
        return CheckOutcome(
            criterio="Ortografía, Gramática y Claridad",
            weight=10,
            severe=False,
            route="AG-COP",
            passed=False,
            evidencia="; ".join(issues),
            hallazgo="Corregir redacción: " + "; ".join(issues),
        )
    return CheckOutcome(
        criterio="Ortografía, Gramática y Claridad",
        weight=10,
        severe=False,
        route="AG-COP",
        passed=True,
        evidencia="Sin errores de formato, plantillas ni puntuación detectados.",
    )


def check_coherence(request: AuditRequest) -> CheckOutcome:
    alt_text = request.input.borrador.alt_text.strip()
    caption = request.input.copy_payload.caption
    objetivo = request.input.brief.objetivo

    if not alt_text:
        return CheckOutcome(
            criterio="Coherencia Texto–Imagen–Idea",
            weight=16,
            severe=False,
            route="AG-BOR",
            passed=False,
            evidencia="El borrador no incluye alt_text para comparar con el copy.",
            hallazgo="El borrador visual no describe su contenido; alinear alt_text con el copy.",
        )

    caption_tokens = meaningful_tokens(caption)
    visual_tokens = meaningful_tokens(f"{alt_text} {objetivo}")
    overlap = caption_tokens & visual_tokens

    if not overlap:
        return CheckOutcome(
            criterio="Coherencia Texto–Imagen–Idea",
            weight=16,
            severe=False,
            route="AG-BOR",
            passed=False,
            evidencia="El copy no comparte términos significativos con el alt_text ni con el objetivo.",
            hallazgo="El texto, la imagen y la idea no están alineados.",
        )

    matched = ", ".join(sorted(overlap)[:5])
    return CheckOutcome(
        criterio="Coherencia Texto–Imagen–Idea",
        weight=16,
        severe=False,
        route="AG-BOR",
        passed=True,
        evidencia=f"Términos compartidos: {matched}.",
    )


def check_veracity(request: AuditRequest) -> CheckOutcome:
    context = request.input.context_pack
    caption = request.input.copy_payload.caption
    facts_used = request.input.copy_payload.facts_used

    corpus = _context_corpus(context)
    corpus_numbers = _numbers_in(corpus)
    sources: list[DataSource] = []
    seen_sources: set[tuple[str, str]] = set()
    issues: list[str] = []

    claims: list[tuple[str, str]] = []
    for match in PERCENT_RE.finditer(caption):
        claims.append((match.group(0), _number_key(match.group(1))))
    for match in PRICE_RE.finditer(caption):
        raw = match.group(1) or match.group(2)
        claims.append((match.group(0), _number_key(raw)))

    for raw_claim, key in claims:
        if key in corpus_numbers:
            for index, promo in enumerate(context.promociones):
                if key in _numbers_in(_stringify(promo)):
                    ref = _entry_ref(promo, "PROMO", index)
                    if ("perfil_negocio.promociones", ref) not in seen_sources:
                        seen_sources.add(("perfil_negocio.promociones", ref))
                        sources.append(
                            DataSource(source="perfil_negocio.promociones", ref=ref)
                        )
                    break
            for index, fact in enumerate(context.facts):
                if key in _numbers_in(_stringify(fact)):
                    ref = _entry_ref(fact, "FACT", index)
                    if ("perfil_negocio.facts", ref) not in seen_sources:
                        seen_sources.add(("perfil_negocio.facts", ref))
                        sources.append(DataSource(source="perfil_negocio.facts", ref=ref))
                    break
        else:
            issues.append(f"la afirmación '{raw_claim.strip()}' no figura en el contexto")

    known_facts = _fact_ids(context)
    if context.facts:
        for fact_id in facts_used:
            if fact_id not in known_facts:
                issues.append(f"el hecho referenciado '{fact_id}' no existe en context_pack.facts")

    if issues:
        return CheckOutcome(
            criterio="Veracidad de Datos Comercial",
            weight=24,
            severe=True,
            route="AG-COP",
            passed=False,
            evidencia="; ".join(issues),
            hallazgo="Datos comerciales no verificados: " + "; ".join(issues),
            data_sources=sources,
        )

    if claims:
        evidence = f"{len(claims)} afirmación(es) comercial(es) verificada(s) contra el contexto."
    else:
        evidence = "Sin afirmaciones comerciales numéricas que verificar."
    return CheckOutcome(
        criterio="Veracidad de Datos Comercial",
        weight=24,
        severe=True,
        route="AG-COP",
        passed=True,
        evidencia=evidence,
        data_sources=sources,
    )


def check_strategy(request: AuditRequest) -> CheckOutcome:
    caption = request.input.copy_payload.caption.lower()
    objetivo = request.input.brief.objetivo

    cta = next((term for term in CTA_TERMS if term in caption), None)
    overlap = meaningful_tokens(caption) & meaningful_tokens(objetivo)

    if cta:
        return CheckOutcome(
            criterio="Alineación Estratégica y Audiencia",
            weight=16,
            severe=False,
            route="AG-EST",
            passed=True,
            evidencia=f"Llamado a la acción detectado: '{cta}'.",
        )
    if overlap:
        return CheckOutcome(
            criterio="Alineación Estratégica y Audiencia",
            weight=16,
            severe=False,
            route="AG-EST",
            passed=True,
            evidencia="El copy comparte el objetivo: " + ", ".join(sorted(overlap)[:5]) + ".",
        )
    return CheckOutcome(
        criterio="Alineación Estratégica y Audiencia",
        weight=16,
        severe=False,
        route="AG-EST",
        passed=False,
        evidencia="El copy no contiene un llamado a la acción ni retoma el objetivo del brief.",
        hallazgo="Alinear el copy con el objetivo comercial y añadir un CTA.",
    )


def check_brand(request: AuditRequest) -> CheckOutcome:
    context = request.input.context_pack
    caption = request.input.copy_payload.caption
    lowered = caption.lower()

    found = [term for term in _prohibited_terms(context) if term.lower() in lowered]
    if found:
        quoted = ", ".join(f"'{term}'" for term in found)
        return CheckOutcome(
            criterio="Voz, Tono y Reglas de Marca",
            weight=12,
            severe=False,
            route="AG-COP",
            passed=False,
            evidencia=f"Términos prohibidos detectados: {quoted}.",
            hallazgo=f"Eliminar términos prohibidos: {quoted}.",
        )

    letters = [character for character in caption if character.isalpha()]
    if len(letters) >= 20 and all(character.isupper() for character in letters):
        return CheckOutcome(
            criterio="Voz, Tono y Reglas de Marca",
            weight=12,
            severe=False,
            route="AG-COP",
            passed=False,
            evidencia="El caption está escrito enteramente en mayúsculas.",
            hallazgo="Usar el tono de marca y evitar mayúsculas sostenidas.",
        )

    return CheckOutcome(
        criterio="Voz, Tono y Reglas de Marca",
        weight=12,
        severe=False,
        route="AG-COP",
        passed=True,
        evidencia="Sin términos prohibidos ni abuso de mayúsculas.",
    )


def check_risk(request: AuditRequest) -> CheckOutcome:
    context = request.input.context_pack
    caption = request.input.copy_payload.caption
    lowered = caption.lower()
    corpus = _canonical(_context_corpus(context))

    unsupported = [
        term
        for term in RISK_TERMS
        if term in lowered and _canonical(term) not in corpus
    ]

    if unsupported:
        quoted = ", ".join(f"'{term}'" for term in unsupported)
        return CheckOutcome(
            criterio="Riesgo y Afirmaciones No Sustentadas",
            weight=12,
            severe=True,
            route="AG-COP",
            passed=False,
            evidencia=f"Afirmaciones absolutas no sustentadas: {quoted}.",
            hallazgo=f"Retirar o respaldar las afirmaciones: {quoted}.",
        )

    return CheckOutcome(
        criterio="Riesgo y Afirmaciones No Sustentadas",
        weight=12,
        severe=True,
        route="AG-COP",
        passed=True,
        evidencia="Sin promesas absolutas o engañosas detectadas.",
    )


def check_instagram_limits(request: AuditRequest) -> CheckOutcome:
    caption = request.input.copy_payload.caption
    hashtags = request.input.copy_payload.hashtags
    asset_url = request.input.borrador.asset_url
    issues: list[str] = []

    if len(caption) > 2200:
        issues.append(f"el caption tiene {len(caption)} caracteres (máximo 2200)")
    if len(hashtags) > 30:
        issues.append(f"se enviaron {len(hashtags)} hashtags (máximo 30)")
    for hashtag in hashtags:
        if not hashtag.startswith("#") or len(hashtag) <= 1:
            issues.append(f"hashtag inválido: '{hashtag}'")
        elif any(character.isspace() for character in hashtag):
            issues.append(f"hashtag con espacios: '{hashtag}'")

    extension = Path(urlparse(asset_url).path).suffix.lower().lstrip(".")
    allowed = {"jpg", "jpeg", "png", "webp"}
    if extension and extension not in allowed:
        issues.append(f"formato de imagen no soportado: '{extension}'")

    if issues:
        return CheckOutcome(
            criterio="Límites Técnicos de Instagram",
            weight=10,
            severe=True,
            route="AG-COP",
            passed=False,
            evidencia="; ".join(issues),
            hallazgo="Ajustar el contenido a los límites de Instagram: " + "; ".join(issues),
        )

    return CheckOutcome(
        criterio="Límites Técnicos de Instagram",
        weight=10,
        severe=True,
        route="AG-COP",
        passed=True,
        evidencia=(
            f"Caption de {len(caption)} caracteres, {len(hashtags)} hashtags "
            f"y formato '{extension or 'sin extensión'}' válidos."
        ),
    )


def run_checks(request: AuditRequest) -> list[CheckOutcome]:
    """Run the seven checks in the documented order."""

    return [
        check_spelling(request),
        check_coherence(request),
        check_veracity(request),
        check_strategy(request),
        check_brand(request),
        check_risk(request),
        check_instagram_limits(request),
    ]
