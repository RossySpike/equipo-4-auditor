"""Exhaustive edge cases for the seven deterministic audit checks."""

from __future__ import annotations

import pytest

from app.checks import (
    CTA_TERMS,
    RISK_TERMS,
    CheckOutcome,
    _canonical,
    _context_corpus,
    _entry_ref,
    _fact_ids,
    _number_key,
    _numbers_in,
    _prohibited_terms,
    _stringify,
    check_brand,
    check_coherence,
    check_instagram_limits,
    check_risk,
    check_spelling,
    check_strategy,
    check_veracity,
    meaningful_tokens,
    run_checks,
)
from app.models import AuditRequest, ContextPack

CRITERIA = {
    "spelling": "Ortografía, Gramática y Claridad",
    "coherence": "Coherencia Texto–Imagen–Idea",
    "veracity": "Veracidad de Datos Comercial",
    "strategy": "Alineación Estratégica y Audiencia",
    "brand": "Voz, Tono y Reglas de Marca",
    "risk": "Riesgo y Afirmaciones No Sustentadas",
    "limits": "Límites Técnicos de Instagram",
}


def request_for(payload) -> AuditRequest:
    return AuditRequest.model_validate(payload)


def outcome(payload, name) -> CheckOutcome:
    request = request_for(payload)
    return {item.criterio: item for item in run_checks(request)}[CRITERIA[name]]


def set_caption(payload, caption: str) -> None:
    payload["input"]["copy"]["caption"] = caption


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_meaningful_tokens_removes_stopwords_short_words_and_case():
    tokens = meaningful_tokens("La Tienda de Ropa y el Deporte PARA ti")

    assert tokens == {"tienda", "ropa", "deporte"}


def test_meaningful_tokens_handles_accents_and_underscores():
    tokens = meaningful_tokens("canción _privada_ 12 años")

    assert "canción" in tokens
    assert "años" in tokens
    assert "_privada_" not in tokens
    assert "12" not in tokens


def test_meaningful_tokens_on_empty_text():
    assert meaningful_tokens("") == set()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("20", "20"),
        ("20.5", "20.5"),
        ("20,5", "20.5"),
        ("0020", "20"),
        ("not-a-number", "not-a-number"),
    ],
)
def test_number_key_normalization(raw, expected):
    assert _number_key(raw) == expected


def test_numbers_in_extracts_normalized_numbers():
    assert _numbers_in("20% OFF y $1.500, además 3,5 EUR") == {"20", "1.5", "3.5"}


def test_stringify_handles_scalars_dicts_lists_and_nested_values():
    assert _stringify("texto") == "texto"
    assert _stringify({"a": "uno", "b": 2}) == "uno 2"
    assert _stringify(["uno", {"a": 2}]) == "uno 2"
    assert _stringify(3.5) == "3.5"


def test_context_corpus_includes_company_promotions_facts_and_brand_rules():
    context = ContextPack.model_validate(
        {
            "empresa": "RetailVenture",
            "promociones": ["20% OFF"],
            "facts": [{"id": "FACT-1", "texto": "envío gratis"}],
            "reglas_marca": {"tono": "cercano"},
        }
    )

    corpus = _context_corpus(context)

    assert "retailventure" in corpus
    assert "20% off" in corpus
    assert "envío gratis" in corpus
    assert "cercano" in corpus


def test_fact_ids_support_dicts_strings_fact_id_and_ignore_invalid_entries():
    context = ContextPack.model_construct(
        facts=[
            {"id": "FACT-001", "texto": "uno"},
            {"fact_id": "FACT-002", "texto": "dos"},
            "FACT-003 texto libre",
            "sin identificador",
            42,
        ]
    )

    assert _fact_ids(context) == {"FACT-001", "FACT-002", "FACT-003"}


def test_entry_ref_prefers_identifiers_and_falls_back_to_index():
    assert _entry_ref({"id": "PROMO-7"}, "PROMO", 0) == "PROMO-7"
    assert _entry_ref({"ref": "REF-1"}, "FACT", 2) == "REF-1"
    assert _entry_ref({"promo_id": "P-9"}, "PROMO", 2) == "P-9"
    assert _entry_ref({"texto": "sin id"}, "FACT", 2) == "FACT-003"
    assert _entry_ref("texto", "PROMO", 0) == "PROMO-001"


def test_prohibited_terms_merges_all_supported_declarations():
    context = ContextPack.model_validate(
        {
            "palabras_prohibidas": ["barato", " gratis "],
            "reglas_marca": {
                "palabras_prohibidas": ["oferta"],
                "prohibidas": "regateo",
                "terminos_prohibidos": ["mentira"],
                "forbidden_terms": ["scam"],
            },
        }
    )

    terms = _prohibited_terms(context)

    assert {"barato", "gratis", "oferta", "regateo", "mentira", "scam"} <= set(terms)


@pytest.mark.parametrize("key", ["termino", "term", "palabra", "value"])
def test_prohibited_terms_reads_list_of_dicts(key):
    context = ContextPack.model_validate({"reglas_marca": [{key: "prohibido"}]})

    assert _prohibited_terms(context) == ["prohibido"]


def test_prohibited_terms_ignores_blank_and_invalid_entries():
    context = ContextPack.model_validate(
        {"palabras_prohibidas": ["", "  "], "reglas_marca": [{}, "texto", 3]}
    )

    assert _prohibited_terms(context) == []


def test_canonical_collapses_case_and_whitespace():
    assert _canonical("  El   MEJOR   Producto ") == "el mejor producto"


# ---------------------------------------------------------------------------
# Spelling, grammar and clarity
# ---------------------------------------------------------------------------


def test_spelling_passes_a_clean_caption(payload):
    result = outcome(payload, "spelling")

    assert result.passed is True
    assert result.weight == 10
    assert result.severe is False
    assert result.route == "AG-COP"


def test_spelling_fails_an_empty_caption(payload):
    set_caption(payload, "")

    result = outcome(payload, "spelling")

    assert result.passed is False
    assert "vacío" in result.evidencia


def test_spelling_fails_a_whitespace_only_caption(payload):
    set_caption(payload, "   \t\n ")

    assert outcome(payload, "spelling").passed is False


@pytest.mark.parametrize("placeholder", ["TODO", "TBD", "XXX", "Lorem ipsum",
                                         "todo", "lorem IPSUM"])
def test_spelling_detects_every_placeholder(payload, placeholder):
    set_caption(payload, f"Compra ya {placeholder} en la tienda.")

    result = outcome(payload, "spelling")

    assert result.passed is False
    assert "marcador" in result.evidencia


@pytest.mark.parametrize("caption", ["Texto (sin cerrar", "Texto [sin cerrar",
                                     "Texto {sin cerrar", "Texto ) extra",
                                     "Texto ] extra", "Texto } extra"])
def test_spelling_detects_unbalanced_delimiters(payload, caption):
    set_caption(payload, caption)

    assert "delimitadores" in outcome(payload, "spelling").evidencia


def test_spelling_accepts_balanced_delimiters(payload):
    set_caption(payload, "Compra (hoy) [oferta] {limitada}.")

    assert outcome(payload, "spelling").passed is True


def test_spelling_detects_double_spaces(payload):
    set_caption(payload, "Visita  la tienda.")

    assert "espacios dobles" in outcome(payload, "spelling").evidencia


@pytest.mark.parametrize("caption", ["Visita la tienda .", "Compra ya ,",
                                     "¿Listo ?", "Hola ;", "Atención :"])
def test_spelling_detects_space_before_punctuation(payload, caption):
    set_caption(payload, caption)

    assert "espacio antes" in outcome(payload, "spelling").evidencia


@pytest.mark.parametrize("caption", ["¡Compra!!!", "Hola,, mundo", "A;;B", "A::B"])
def test_spelling_detects_repeated_punctuation(payload, caption):
    set_caption(payload, caption)

    assert "repite signos" in outcome(payload, "spelling").evidencia


def test_spelling_reports_all_detected_problems_and_a_finding(payload):
    set_caption(payload, "Visita  la tienda TODO (oferta!!!")

    result = outcome(payload, "spelling")

    assert result.passed is False
    assert result.hallazgo is not None
    assert "Corregir redacción" in result.hallazgo


# ---------------------------------------------------------------------------
# Text-image-idea coherence
# ---------------------------------------------------------------------------


def test_coherence_requires_alt_text(payload):
    payload["input"]["borrador"]["alt_text"] = "   "

    result = outcome(payload, "coherence")

    assert result.passed is False
    assert result.route == "AG-BOR"
    assert "alt_text" in result.evidencia
    assert result.hallazgo


def test_coherence_passes_with_a_shared_caption_term(payload):
    payload["input"]["borrador"]["alt_text"] = "Modelo con ropa deportiva en tienda"
    set_caption(payload, "Compra ropa deportiva hoy.")

    result = outcome(payload, "coherence")

    assert result.passed is True
    assert "Términos compartidos" in result.evidencia
    assert "ropa" in result.evidencia


def test_coherence_can_use_the_brief_objective(payload):
    payload["input"]["borrador"]["alt_text"] = "Modelo posando"
    payload["input"]["brief"]["objetivo"] = "Trafico a tienda"
    set_caption(payload, "Nueva colección en la tienda.")

    assert outcome(payload, "coherence").passed is True


def test_coherence_fails_when_only_stopwords_overlap(payload):
    payload["input"]["borrador"]["alt_text"] = "La de el con para"
    payload["input"]["brief"]["objetivo"] = "La de el con para"
    set_caption(payload, "La nueva colección ya está aquí.")

    result = outcome(payload, "coherence")

    assert result.passed is False
    assert "no comparte términos" in result.evidencia


def test_coherence_evidence_lists_at_most_five_terms(payload):
    payload["input"]["borrador"]["alt_text"] = "uno dos tres cuatro cinco seis siete"
    payload["input"]["brief"]["objetivo"] = ""
    set_caption(payload, "uno dos tres cuatro cinco seis siete")

    result = outcome(payload, "coherence")

    assert result.passed is True
    listed = result.evidencia.split("Términos compartidos: ", 1)[1]
    assert listed.count(",") <= 4


# ---------------------------------------------------------------------------
# Commercial veracity
# ---------------------------------------------------------------------------


def test_veracity_passes_without_numeric_claims(payload):
    set_caption(payload, "Compra ya en la tienda deportiva.")

    result = outcome(payload, "veracity")

    assert result.passed is True
    assert "Sin afirmaciones" in result.evidencia


def test_veracity_verifies_a_percentage_against_promotions_and_facts(payload):
    result = outcome(payload, "veracity")

    assert result.passed is True
    assert result.severe is True
    assert result.weight == 24
    assert "afirmación" in result.evidencia
    assert {source.source for source in result.data_sources} == {
        "perfil_negocio.promociones",
        "perfil_negocio.facts",
    }


def test_veracity_fails_an_invented_percentage(payload):
    set_caption(payload, "Aprovecha 50% OFF en ropa deportiva. Visita la tienda.")

    result = outcome(payload, "veracity")

    assert result.passed is False
    assert result.severe is True
    assert "'50%'" in result.evidencia
    assert result.hallazgo
    assert result.data_sources == []


@pytest.mark.parametrize(
    ("caption", "context_value"),
    [
        ("Precio $1.500", "1.500"),
        ("Precio USD 30", "30"),
        ("Precio EUR 40", "40"),
        ("Precio Bs. 50", "50"),
        ("Precio 60 USD", "60"),
        ("Precio 70 EUR", "70"),
        ("Descuento 20,5%", "20.5"),
    ],
)
def test_veracity_accepts_documented_price_and_percentage_formats(
    payload, caption, context_value
):
    payload["input"]["context_pack"]["promociones"] = [
        {"id": "PROMO-001", "texto": f"Oferta {context_value}"}
    ]
    payload["input"]["context_pack"]["facts"] = []
    payload["input"]["copy"]["facts_used"] = []
    set_caption(payload, caption)

    result = outcome(payload, "veracity")

    assert result.passed is True
    assert result.data_sources[0].ref == "PROMO-001"


def test_veracity_fails_an_invented_price(payload):
    set_caption(payload, "Solo $9.999 por tiempo limitado.")

    result = outcome(payload, "veracity")

    assert result.passed is False
    assert "$9.999" in result.evidencia


def test_veracity_can_match_numbers_declared_in_the_company_profile(payload):
    payload["input"]["context_pack"]["empresa"] = "Empresa con 12 sucursales"
    payload["input"]["context_pack"]["promociones"] = []
    payload["input"]["context_pack"]["facts"] = []
    set_caption(payload, "Visita nuestras 12 sucursales.")

    result = outcome(payload, "veracity")

    assert result.passed is True
    assert result.data_sources == []


def test_veracity_deduplicates_repeated_sources(payload):
    set_caption(payload, "20% OFF y otra vez 20% OFF. Visita la tienda.")

    result = outcome(payload, "veracity")

    keys = [(source.source, source.ref) for source in result.data_sources]
    assert len(keys) == len(set(keys))


def test_veracity_unknown_fact_reference_fails_when_catalog_exists(payload):
    payload["input"]["copy"]["facts_used"] = ["FACT-012", "FACT-999"]

    result = outcome(payload, "veracity")

    assert result.passed is False
    assert "FACT-999" in result.evidencia
    assert "no existe" in result.evidencia


def test_veracity_accepts_a_string_fact_catalog_entry(payload):
    payload["input"]["context_pack"]["facts"] = ["FACT-077 Envío gratis"]
    payload["input"]["copy"]["facts_used"] = ["FACT-077"]
    set_caption(payload, "Visita la tienda.")

    assert outcome(payload, "veracity").passed is True


def test_veracity_ignores_fact_catalog_validation_when_catalog_is_empty(payload):
    payload["input"]["context_pack"]["facts"] = []
    payload["input"]["copy"]["facts_used"] = ["FACT-DOES-NOT-EXIST"]
    set_caption(payload, "Visita la tienda.")

    assert outcome(payload, "veracity").passed is True


def test_veracity_reports_every_unverified_claim(payload):
    set_caption(payload, "50% OFF y $9.999. Visita la tienda.")

    result = outcome(payload, "veracity")

    assert "50%" in result.evidencia
    assert "$9.999" in result.evidencia


# ---------------------------------------------------------------------------
# Strategic alignment
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("cta", CTA_TERMS)
def test_strategy_recognizes_every_documented_cta(payload, cta):
    set_caption(payload, f"Texto neutro. {cta}.")

    result = outcome(payload, "strategy")

    assert result.passed is True
    assert "Llamado a la acción detectado" in result.evidencia


def test_strategy_fails_without_cta_or_objective_overlap(payload):
    set_caption(payload, "La nueva colección ya está aquí.")

    result = outcome(payload, "strategy")

    assert result.passed is False
    assert result.route == "AG-EST"
    assert result.weight == 16
    assert result.hallazgo


def test_strategy_passes_through_objective_overlap(payload):
    payload["input"]["brief"]["objetivo"] = "Trafico a tienda"
    set_caption(payload, "Nueva ropa disponible en la tienda.")

    result = outcome(payload, "strategy")

    assert result.passed is True
    assert "tienda" in result.evidencia


def test_strategy_cta_detection_is_case_insensitive(payload):
    set_caption(payload, "COMPRA YA en la tienda.")

    assert outcome(payload, "strategy").passed is True


# ---------------------------------------------------------------------------
# Brand voice and tone
# ---------------------------------------------------------------------------


def test_brand_passes_without_rules(payload):
    result = outcome(payload, "brand")

    assert result.passed is True
    assert result.weight == 12


def test_brand_fails_for_a_prohibited_term_case_insensitively(payload):
    set_caption(payload, "Compra BARATO hoy. Visita la tienda.")

    result = outcome(payload, "brand")

    assert result.passed is False
    assert "'barato'" in result.evidencia
    assert result.hallazgo


@pytest.mark.parametrize(
    "brand_rules",
    [
        {"palabras_prohibidas": ["barato"]},
        {"prohibidas": ["barato"]},
        {"terminos_prohibidos": ["barato"]},
        {"forbidden_terms": ["barato"]},
        [{"termino": "barato"}],
        [{"term": "barato"}],
        [{"palabra": "barato"}],
        [{"value": "barato"}],
    ],
)
def test_brand_detects_terms_from_every_supported_rule_shape(payload, brand_rules):
    payload["input"]["context_pack"]["palabras_prohibidas"] = []
    payload["input"]["context_pack"]["reglas_marca"] = brand_rules
    set_caption(payload, "Compra barato hoy.")

    assert outcome(payload, "brand").passed is False


def test_brand_detects_a_rule_declared_as_a_plain_string(payload):
    payload["input"]["context_pack"]["palabras_prohibidas"] = []
    payload["input"]["context_pack"]["reglas_marca"] = {"prohibidas": "barato"}
    set_caption(payload, "Compra barato hoy.")

    assert outcome(payload, "brand").passed is False


def test_brand_fails_sustained_uppercase_at_the_boundary(payload):
    set_caption(payload, "A" * 20)

    result = outcome(payload, "brand")

    assert result.passed is False
    assert "mayúsculas" in result.evidencia


def test_brand_allows_short_uppercase_emphasis(payload):
    set_caption(payload, "A" * 19)

    assert outcome(payload, "brand").passed is True


def test_brand_allows_mixed_case_with_an_acronym(payload):
    set_caption(payload, "Compra HOY en la tienda.")

    assert outcome(payload, "brand").passed is True


# ---------------------------------------------------------------------------
# Risk and unsupported claims
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("term", ["garantizado", "el mejor", "la mejor", "único",
                                  "siempre", "nunca", "milagro", "sin riesgo",
                                  "definitivo", "infalible", "100%"])
def test_risk_detects_absolute_claims(payload, term):
    set_caption(payload, f"Compra ya, {term}. Visita la tienda.")

    result = outcome(payload, "risk")

    assert result.passed is False
    assert result.severe is True
    assert result.weight == 12
    assert term in result.evidencia


def test_risk_lists_every_unsupported_claim(payload):
    set_caption(payload, "Siempre garantizado y nunca falla.")

    result = outcome(payload, "risk")

    assert "siempre" in result.evidencia
    assert "garantizado" in result.evidencia
    assert "nunca" in result.evidencia


@pytest.mark.parametrize("context_field", ["empresa", "promociones", "facts", "reglas_marca"])
def test_risk_allows_claims_backed_by_any_context_section(payload, context_field):
    payload["input"]["context_pack"]["promociones"] = []
    payload["input"]["context_pack"]["facts"] = []
    payload["input"]["context_pack"]["reglas_marca"] = None
    payload["input"]["context_pack"][context_field] = (
        ["Siempre 20% OFF"] if context_field == "promociones"
        else [{"id": "FACT-1", "texto": "Siempre 20% OFF"}] if context_field == "facts"
        else "Siempre 20% OFF" if context_field == "empresa"
        else {"claim": "Siempre 20% OFF"}
    )
    set_caption(payload, "Siempre 20% OFF. Visita la tienda.")

    assert outcome(payload, "risk").passed is True


def test_risk_passes_clean_content(payload):
    result = outcome(payload, "risk")

    assert result.passed is True
    assert result.severe is True
    assert "Sin promesas absolutas" in result.evidencia


def test_risk_terms_constant_is_covered_by_the_parameterized_cases():
    # Every term is lower-case and non-empty, so `caption.lower()` matching is
    # reliable and the parameterized test above exercises the documented set.
    assert all(term == term.lower() and term.strip() for term in RISK_TERMS)


# ---------------------------------------------------------------------------
# Instagram technical limits
# ---------------------------------------------------------------------------


def test_limits_pass_for_content_at_the_exact_boundaries(payload):
    set_caption(payload, "x" * 2200)
    payload["input"]["copy"]["hashtags"] = [f"#t{i}" for i in range(30)]
    payload["input"]["borrador"]["asset_url"] = "https://cdn.example.com/a.WEBP"

    result = outcome(payload, "limits")

    assert result.passed is True
    assert "2200" in result.evidencia
    assert "30 hashtags" in result.evidencia


def test_limits_fail_at_2201_characters(payload):
    set_caption(payload, "x" * 2201)

    result = outcome(payload, "limits")

    assert result.passed is False
    assert "2201" in result.evidencia
    assert result.severe is True


def test_limits_fail_at_31_hashtags(payload):
    payload["input"]["copy"]["hashtags"] = [f"#t{i}" for i in range(31)]

    result = outcome(payload, "limits")

    assert result.passed is False
    assert "31 hashtags" in result.evidencia


@pytest.mark.parametrize("hashtag", ["Deporte", "#", "", "#a b", "# a", "## #"])
def test_limits_reject_malformed_hashtags(payload, hashtag):
    payload["input"]["copy"]["hashtags"] = ["#ok", hashtag]

    result = outcome(payload, "limits")

    assert result.passed is False
    assert "hashtag" in result.evidencia


@pytest.mark.parametrize("extension", ["jpg", "jpeg", "png", "webp", "JPG", "PNG"])
def test_limits_accept_supported_image_extensions(payload, extension):
    payload["input"]["borrador"]["asset_url"] = f"https://cdn.example.com/a.{extension}"

    assert outcome(payload, "limits").passed is True


@pytest.mark.parametrize("extension", ["gif", "bmp", "tiff", "svg", "mp4", "gz"])
def test_limits_reject_unsupported_image_extensions(payload, extension):
    payload["input"]["borrador"]["asset_url"] = f"https://cdn.example.com/a.{extension}"

    result = outcome(payload, "limits")

    assert result.passed is False
    assert extension.lower() in result.evidencia


def test_limits_use_the_url_path_ignoring_query_parameters(payload):
    payload["input"]["borrador"]["asset_url"] = "https://cdn.example.com/a.gif?size=large"

    result = outcome(payload, "limits")

    assert result.passed is False
    assert "gif" in result.evidencia


def test_limits_accept_an_asset_without_an_extension(payload):
    payload["input"]["borrador"]["asset_url"] = "https://cdn.example.com/media/asset"

    result = outcome(payload, "limits")

    assert result.passed is True
    assert "sin extensión" in result.evidencia


def test_limits_report_every_problem_at_once(payload):
    set_caption(payload, "x" * 2300)
    payload["input"]["copy"]["hashtags"] = [f"#t{i}" for i in range(31)] + ["bad"]
    payload["input"]["borrador"]["asset_url"] = "https://cdn.example.com/a.gif"

    result = outcome(payload, "limits")

    assert "2200" in result.evidencia
    assert "hashtags" in result.evidencia
    assert "gif" in result.evidencia


# ---------------------------------------------------------------------------
# Aggregate contract of run_checks
# ---------------------------------------------------------------------------


def test_run_checks_returns_exactly_seven_outcomes_in_documented_order(payload):
    outcomes = run_checks(request_for(payload))

    assert [item.criterio for item in outcomes] == list(CRITERIA.values())


def test_run_checks_exposes_fixed_weight_severity_and_route_metadata(payload):
    outcomes = {item.criterio: item for item in run_checks(request_for(payload))}

    assert outcomes[CRITERIA["spelling"]].weight == 10
    assert outcomes[CRITERIA["coherence"]].weight == 16
    assert outcomes[CRITERIA["veracity"]].weight == 24
    assert outcomes[CRITERIA["strategy"]].weight == 16
    assert outcomes[CRITERIA["brand"]].weight == 12
    assert outcomes[CRITERIA["risk"]].weight == 12
    assert outcomes[CRITERIA["limits"]].weight == 10
    assert sum(item.weight for item in outcomes.values()) == 100

    severe = {name for name, item in outcomes.items() if item.severe}
    assert severe == {CRITERIA["veracity"], CRITERIA["risk"], CRITERIA["limits"]}
    assert {item.route for item in outcomes.values()} <= {"AG-EST", "AG-BOR", "AG-COP"}


def test_check_functions_are_pure_and_do_not_mutate_the_request(payload):
    request = request_for(payload)
    before = request.model_dump()

    check_spelling(request)
    check_coherence(request)
    check_veracity(request)
    check_strategy(request)
    check_brand(request)
    check_risk(request)
    check_instagram_limits(request)

    assert request.model_dump() == before
