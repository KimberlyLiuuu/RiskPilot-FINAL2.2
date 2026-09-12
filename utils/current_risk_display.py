# -*- coding: utf-8 -*-
"""
RiskPilot Current Risk display layer (Streamlit-free).
=====================================================

This module owns everything the Current Risk page needs to turn the
*deterministic* risk fusion result into *localized*, regenerable AI text
through the stage 3 unified content protocol:

1. The **input fingerprint** of the explanation
   (:func:`build_current_risk_input` / :func:`current_risk_input_hash`).
   Only stable, language-independent inputs enter it, so the same risk
   numbers always produce the same hash while another interface language,
   another report language or unrelated session state never does.

2. The **read-only content selection**
   (:func:`get_current_risk_display`). It decides, for one project and one
   *content* locale, whether the stored explanation is ``available`` /
   ``stale`` / ``missing`` / ``empty`` / ``invalid`` / ``legacy_compatible``.
   A stale explanation is never presented as current, and a version written
   in another language is never shown as a fallback.

3. The **payload validation** of an AI response
   (:func:`validate_current_risk_text_payload`). The payload may only carry
   the four human-readable text fields; scores, levels and the S / L / E
   inputs are rejected outright.

4. The **prompt builder** (:func:`build_current_risk_text_prompt`), which
   writes every human-readable value in the requested target language while
   keeping the JSON field names in English.

5. The **session cache namespace** helpers
   (:func:`current_risk_cache_key` / :func:`bounded_cache_put`) so a result
   for one project / locale / input can never be reused for another.

Hard rules enforced here
------------------------
* This module imports no Streamlit, never touches ``st.session_state``,
  never calls ``persist()``, never writes a file and never calls the AI.
* Every read function is pure: it never mutates the project it is given.
* Risk numbers (``score`` / ``level`` / ``severity`` / ``likelihood`` /
  ``exposure`` / ``raw_risk`` / component contributions and the environment
  descriptors) are never produced by this module. They are read from the
  deterministic result and rendered directly by the page layer.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from utils import locale_registry
from utils.localized_content import (
    CONTENT_TYPE_CURRENT_RISK,
    DEFAULT_RECORD_ID,
    REASON_NO_CONTENT,
    SCHEMA_VERSION as CONTENT_SCHEMA_VERSION,
    SOURCE_AI_GENERATED,
    STATUS_AVAILABLE,
    STATUS_DETERMINISTIC,
    STATUS_INVALID,
    STATUS_LEGACY_COMPATIBLE,
    STATUS_MISSING,
    STATUS_STALE,
    compute_input_hash,
    legacy_ai_text_get,
    localized_content_status,
)

# ============================================================
# CONSTANTS
# ============================================================

#: content_type of the Current Risk AI explanation inside the protocol.
CONTENT_TYPE = CONTENT_TYPE_CURRENT_RISK

#: Current Risk is a singleton, so it always uses the fixed record_id.
RECORD_ID = DEFAULT_RECORD_ID

#: Version of the *rules* that turn risk numbers into the explanation input.
#: Bumping it invalidates every previously generated explanation on purpose.
CURRENT_RISK_INPUT_SCHEMA_VERSION = 1

#: The only four keys an AI explanation payload may contain.
TEXT_FIELDS: Tuple[str, ...] = (
    "summary",
    "risk_level_text",
    "reasons",
    "recommendations",
)

#: Soft caps that reject an abusive or malformed AI response.
MAX_SUMMARY_LENGTH = 2000
MAX_RISK_LEVEL_TEXT_LENGTH = 400
MAX_LIST_ITEMS = 8
MAX_LIST_ITEM_LENGTH = 500

#: How many recent input hashes are kept per (project, locale) in the session.
MAX_SESSION_CACHE_PER_GROUP = 3

#: Risk components of the fusion engine, in render order.
COMPONENT_ORDER: Tuple[str, ...] = (
    "Baseline",
    "Weather",
    "Daily Logs",
    "Vision",
    "Financial",
)

# --- statuses exposed to the page --------------------------------------

#: Current-language content exists and matches the current input.
DISPLAY_AVAILABLE = "available"

#: Content exists but the risk input changed -> show the "old version" state.
DISPLAY_STALE = "stale"

#: Current locale has no content while another locale does.
DISPLAY_MISSING = "missing"

#: No AI explanation exists at all.
DISPLAY_EMPTY = "empty"

#: A stored record is structurally invalid -> never render it.
DISPLAY_INVALID = "invalid"

#: A legacy archive entry already carries the requested language.
DISPLAY_LEGACY_COMPATIBLE = "legacy_compatible"


# ============================================================
# SMALL, PURE PRIMITIVES
# ============================================================


def _text(value: Any) -> str:
    """Display-stable string (never ``None``), stripped but not reworded."""
    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    if isinstance(value, bool):
        return str(value)

    if isinstance(value, (int, float)):
        return str(value)

    return ""


def _number(value: Any) -> Any:
    """
    JSON-stable number for the fingerprint.

    Booleans and non-numeric values collapse to ``0`` so that a missing
    score and a zero score are indistinguishable (both mean "no contribution").
    """
    if isinstance(value, bool):
        return 0

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return value if value == value and value not in (float("inf"), float("-inf")) else 0

    return 0


def _stable_project_id(project: Any, project_id: Any = None) -> str:
    """
    Stable identifier of a project.

    The page passes the project's dict key explicitly because the stored
    project object itself does not carry its own name. When no key is given
    the function falls back to a stable field, then to an empty string.
    """
    if isinstance(project_id, str) and project_id.strip():
        return project_id.strip()

    if isinstance(project, dict):
        for field in ("name", "id", "project_id"):
            value = project.get(field)

            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def _recent_log_scores(project: Any) -> List[Any]:
    """
    Numeric AI scores of the seven most recent logs (the fusion window).

    Only the numbers are read: the user written log text is deliberately kept
    out of the fingerprint, so editing a description does not silently keep an
    outdated explanation marked as fresh while the score is unchanged.
    """
    if not isinstance(project, dict):
        return []

    logs = project.get("daily_logs")

    if not isinstance(logs, list):
        return []

    scores: List[Any] = []

    for log in logs[-7:]:
        if not isinstance(log, dict):
            continue

        analysis = log.get("ai_analysis")
        analysis = analysis if isinstance(analysis, dict) else {}

        score = analysis.get("score")

        if isinstance(score, (int, float)) and not isinstance(score, bool):
            scores.append(score)

    return scores


def _vision_penalty(project: Any, components: Dict[str, Any]) -> Any:
    """
    Vision contribution actually used by the fusion engine.

    A resolved visual risk contributes ``0`` no matter what penalty is still
    stored, so the fingerprint follows the engine instead of the raw field.
    """
    if not isinstance(project, dict):
        return 0

    if project.get("visual_risk_resolved"):
        return 0

    penalty = project.get("visual_risk_penalty")

    if isinstance(penalty, (int, float)) and not isinstance(penalty, bool):
        return penalty

    return components.get("Vision", 0)


# ============================================================
# INPUT FINGERPRINT
# ============================================================


def build_current_risk_input(
    project: Any,
    current_risk_data: Any,
    project_id: Any = None,
) -> Dict[str, Any]:
    """
    Stable, language-independent input of the Current Risk explanation.

    Contains only the inputs that actually shape the explanation:

    * the project's stable id;
    * the baseline risk result (score / level / environment / hazards);
    * the current weather risk (the ``Weather`` contribution);
    * the terrain risk (baseline terrain);
    * the numeric daily-log risk (the ``Daily Logs`` contribution);
    * the vision penalty actually applied;
    * the fused score and level;
    * the rules / schema version.

    Deliberately excluded: UI and report language, selector state, session
    state, ``generated_at``, already generated AI text and any dict ordering
    (the hash canonicalises keys).
    """
    project = project if isinstance(project, dict) else {}
    current = current_risk_data if isinstance(current_risk_data, dict) else {}

    baseline = project.get("baseline_risk")
    baseline = baseline if isinstance(baseline, dict) else {}

    components = current.get("components")
    components = components if isinstance(components, dict) else {}

    field_model = current.get("field_condition_model")
    field_model = field_model if isinstance(field_model, dict) else {}

    hazards = baseline.get("risks")
    hazards = hazards if isinstance(hazards, dict) else {}

    normalized_hazards = {
        str(name): _text(value) for name, value in hazards.items()
    }

    normalized_components = {
        name: _number(components.get(name)) for name in COMPONENT_ORDER
    }

    return {
        "schema": CURRENT_RISK_INPUT_SCHEMA_VERSION,
        "content_schema": CONTENT_SCHEMA_VERSION,
        "project_id": _stable_project_id(project, project_id),
        "baseline": {
            "score": _number(baseline.get("score", project.get("baseline_score"))),
            "level": _text(baseline.get("level")),
            "terrain": _text(baseline.get("terrain")),
            "drainage": _text(baseline.get("drainage")),
            "rainfall": _text(baseline.get("rainfall")),
            "earthquake": _text(baseline.get("earthquake")),
            "hazards": normalized_hazards,
        },
        "current": {
            "score": _number(current.get("score")),
            "level": _text(current.get("level")),
        },
        "components": normalized_components,
        "weather_risk": normalized_components["Weather"],
        "terrain_risk": _text(baseline.get("terrain")),
        "log_risk": normalized_components["Daily Logs"],
        "vision_penalty": _vision_penalty(project, components),
        "recent_log_scores": _recent_log_scores(project),
    }


def current_risk_input_hash(
    project: Any,
    current_risk_data: Any,
    project_id: Any = None,
) -> Optional[str]:
    """
    Stable SHA-256 of :func:`build_current_risk_input`, or ``None``.

    Dict order never changes the result; a different risk number always does.
    """
    return compute_input_hash(
        build_current_risk_input(project, current_risk_data, project_id)
    )


def build_current_risk_context(
    project: Any,
    current_risk_data: Any,
    weather: Any = None,
    project_id: Any = None,
) -> Dict[str, Any]:
    """
    Read-only facts the AI is allowed to explain.

    This is the *prompt* context, not the fingerprint: it may carry the recent
    log titles/descriptions (user written, shown as evidence) and the current
    weather values, because the model must be able to reference them. It still
    contains no UI text and no previously generated AI text.
    """
    project = project if isinstance(project, dict) else {}
    current = current_risk_data if isinstance(current_risk_data, dict) else {}

    baseline = project.get("baseline_risk")
    baseline = baseline if isinstance(baseline, dict) else {}

    components = current.get("components")
    components = components if isinstance(components, dict) else {}
    
    field_model = current.get("field_condition_model")
    field_model = field_model if isinstance(field_model, dict) else {}

    logs = project.get("daily_logs")
    logs = logs if isinstance(logs, list) else []

    recent_logs: List[Dict[str, Any]] = []

    for log in logs[-5:]:
        if not isinstance(log, dict):
            continue

        analysis = log.get("ai_analysis")
        analysis = analysis if isinstance(analysis, dict) else {}

        recent_logs.append(
            {
                "date": _text(log.get("date")),
                "title": _text(log.get("title")),
                "description": _text(log.get("description")),
                "risk": _text(analysis.get("level", log.get("severity"))),
                "score": _number(analysis.get("score")),
            }
        )

    weather_current: Dict[str, Any] = {}

    if isinstance(weather, dict):
        candidate = weather.get("current")
        weather_current = candidate if isinstance(candidate, dict) else {}

    hazards = baseline.get("risks")
    hazards = hazards if isinstance(hazards, dict) else {}

    return {
        "project": {
            "id": _stable_project_id(project, project_id),
            "country": _text(project.get("country")),
            "city": _text(project.get("city")),
            "type": _text(project.get("type")),
            "project_scale": _text(project.get("project_scale")),
            "workers": _number(project.get("workers")),
        },
        "baseline": {
            "level": _text(baseline.get("level")),
            "score": _number(baseline.get("score", project.get("baseline_score"))),
            "terrain": _text(baseline.get("terrain")),
            "drainage": _text(baseline.get("drainage")),
            "rainfall": _text(baseline.get("rainfall")),
            "earthquake": _text(baseline.get("earthquake")),
            "risks": {str(k): _text(v) for k, v in hazards.items()},
        },
        "current_risk": {
            "level": _text(current.get("level")),
            "score": _number(current.get("score")),
            "components": {name: _number(components.get(name)) for name in COMPONENT_ORDER},
        },
        "weather": weather_current,
        "field_condition_model": {
            "score": _number(field_model.get("score")),
            "level": _text(field_model.get("level")),
            "confidence": _number(field_model.get("confidence")),
            "distribution": field_model.get("distribution", {}),
            "query": field_model.get("query", {}),
            "method": _text(field_model.get("method")),
        },
        "recent_logs": recent_logs,
    }


# ============================================================
# AI RESPONSE VALIDATION
# ============================================================


def _bounded_text(value: Any, max_length: int) -> Optional[str]:
    """
    Return a stripped string within ``max_length``, or ``None`` to reject.

    Rejects anything that is not a string (so a numeric ``score`` / ``level``
    or a ``NaN`` / ``Infinity`` float can never slip through as text) and any
    value longer than the cap.
    """
    if not isinstance(value, str):
        return None

    text = value.strip()

    if len(text) > max_length:
        return None

    return text


def _bounded_list(value: Any, max_items: int, max_item_length: int) -> Optional[List[str]]:
    """Return a list of bounded, non-empty strings, or ``None`` to reject."""
    if not isinstance(value, list):
        return None

    if len(value) > max_items:
        return None

    items: List[str] = []

    for item in value:
        text = _bounded_text(item, max_item_length)

        if text is None or not text:
            return None

        items.append(text)

    return items


def validate_current_risk_text_payload(payload: Any) -> Dict[str, Any]:
    """
    Validate one AI explanation response before it may be stored.

    Returns a normalized ``{"summary", "risk_level_text", "reasons",
    "recommendations"}`` payload, or ``{}`` when the response must be
    rejected. A rejected response is never written to ``localized_content``,
    never persisted and never touches the risk numbers.

    Rejects: a non-dict, a missing or extra key (so ``score`` / ``level`` /
    ``severity`` / ``likelihood`` / ``exposure`` / ``raw_risk`` cannot appear),
    a wrong value type, an over-long string, an over-long list, an empty list
    item and any ``NaN`` / ``Infinity`` number.
    """
    if not isinstance(payload, dict):
        return {}

    if set(payload.keys()) != set(TEXT_FIELDS):
        return {}

    # Any non-string value (including bool / int / float / NaN / Infinity)
    # is rejected by the field validators below.
    summary = _bounded_text(payload.get("summary"), MAX_SUMMARY_LENGTH)

    if summary is None or not summary:
        return {}

    risk_level_text = _bounded_text(
        payload.get("risk_level_text"),
        MAX_RISK_LEVEL_TEXT_LENGTH,
    )

    if risk_level_text is None:
        return {}

    reasons = _bounded_list(
        payload.get("reasons"),
        MAX_LIST_ITEMS,
        MAX_LIST_ITEM_LENGTH,
    )

    if reasons is None:
        return {}

    recommendations = _bounded_list(
        payload.get("recommendations"),
        MAX_LIST_ITEMS,
        MAX_LIST_ITEM_LENGTH,
    )

    if recommendations is None:
        return {}

    return {
        "summary": summary,
        "risk_level_text": risk_level_text,
        "reasons": reasons,
        "recommendations": recommendations,
    }


def is_valid_current_risk_text_payload(payload: Any) -> bool:
    """True when :func:`validate_current_risk_text_payload` would accept it."""
    return bool(validate_current_risk_text_payload(payload))


# ============================================================
# PROMPT
# ============================================================


def build_current_risk_text_prompt(
    context: Any,
    target_locale: Any = None,
) -> str:
    """
    Prompt that explains the existing Current Risk assessment.

    The human-readable values must be written in ``target_locale`` (its AI
    language name comes from the locale registry); every JSON field name stays
    in English. The model may not recompute the score, may not output
    severity / likelihood / exposure and may not invent any fact.
    """
    import json

    language_name = locale_registry.ai_language_name(resolve_content_locale(target_locale))

    facts = context if isinstance(context, dict) else {}

    return f"""
You are RiskPilot's construction risk intelligence assistant.

Explain WHY the project's CURRENT RISK is at its existing level. You are NOT
re-assessing the site and you are NOT recalculating anything.

TARGET LANGUAGE
---------------
Write every human-readable text value in {language_name}. Keep every JSON field
name in English.

FORBIDDEN
---------
- Do NOT output a risk score, level, severity, likelihood or exposure.
- Do NOT change or re-judge the provided risk level or score.
- Do NOT invent weather, incidents, equipment or site facts.
- Do NOT invent new reasons that are not supported by the provided data.
- Do NOT translate or rename any JSON field name.
- Do NOT wrap the JSON in Markdown fences.

REQUIRED
--------
- Return ONLY valid JSON with EXACTLY these four fields:
  "summary", "risk_level_text", "reasons", "recommendations".
- "summary" is a concise explanation of why the current risk is at this level.
- "risk_level_text" localizes the provided level for humans; the internal
  level stays whatever the program already decided.
- "reasons" may only explain the provided facts (baseline, weather, logs).
- "recommendations" must target the provided risks, one action each.
- If there is no recent log evidence, say so instead of inventing any.

PROVIDED DATA (read-only):
{json.dumps(facts, ensure_ascii=False, indent=2)}

OUTPUT
------
Return only this structured JSON:

{{
    "summary": "Why the current risk is at this level.",
    "risk_level_text": "Human readable name of the provided risk level.",
    "reasons": ["One explanation of a provided fact."],
    "recommendations": ["One concrete next action."]
}}
"""


# ============================================================
# SESSION CACHE NAMESPACE
# ============================================================


def current_risk_cache_key(
    project_id: Any,
    locale: Any,
    input_hash: Any,
) -> Tuple[str, str, str, str, str]:
    """
    Namespaced session-cache key of one explanation.

    Includes the project, the content_type, the record_id, the content locale
    and the input hash, so a result generated for one combination can never be
    displayed for another.
    """
    return (
        str(project_id or ""),
        CONTENT_TYPE,
        RECORD_ID,
        str(locale or ""),
        str(input_hash or ""),
    )


def current_risk_cache_group(key: Any) -> Optional[Tuple[str, str]]:
    """The (project, locale) group a cache key belongs to, or ``None``."""
    if isinstance(key, tuple) and len(key) == 5:
        return (key[0], key[3])

    return None


def bounded_cache_put(
    cache: Any,
    key: Any,
    payload: Any,
    limit: int = MAX_SESSION_CACHE_PER_GROUP,
) -> Any:
    """
    Store ``payload`` under ``key`` and evict the oldest keys of the same
    (project, locale) group so the session cache stays bounded.

    Pure: it mutates only the ``cache`` dict it is handed and returns it. It
    never removes a cached value belonging to another project or locale.
    """
    if not isinstance(cache, dict):
        return cache

    cache.pop(key, None)
    cache[key] = payload

    group = current_risk_cache_group(key)

    same_group = [existing for existing in cache if current_risk_cache_group(existing) == group]

    while len(same_group) > limit:
        oldest = same_group.pop(0)
        cache.pop(oldest, None)

    return cache


def current_risk_generate_button_key(
    project_id: Any,
    locale: Any,
    input_hash: Any,
) -> str:
    """
    Streamlit widget key of the generate / regenerate button.

    The project, the content locale and the input hash all enter a stable
    digest, so different projects, languages or risk inputs can never share
    widget state (and therefore can never cross-trigger each other).
    """
    import hashlib

    payload = f"{project_id}|{CONTENT_TYPE}|{RECORD_ID}|{locale}|{input_hash}"

    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()

    return f"generate_current_risk_text::{digest}"


# ============================================================
# READ-ONLY CONTENT SELECTION
# ============================================================


def resolve_content_locale(locale: Any) -> str:
    """
    Canonical content locale.

    Uses exactly the same gate as the interface language: registered,
    ``ui_enabled`` and not ``planned``. Anything else falls back to the
    default locale, so a planned locale can never become a content locale.
    """
    return locale_registry.to_ui_locale(locale) or locale_registry.default_locale()


def _ui_state(status: Dict[str, Any]) -> str:
    """Map a protocol status onto the state the page renders."""
    value = status.get("status")
    saved = status.get("saved_locales") or []

    if value == STATUS_AVAILABLE or value == STATUS_DETERMINISTIC:
        return DISPLAY_AVAILABLE

    if value == STATUS_STALE:
        return DISPLAY_STALE

    if value == STATUS_INVALID:
        return DISPLAY_INVALID

    if value == STATUS_LEGACY_COMPATIBLE:
        return DISPLAY_LEGACY_COMPATIBLE

    if value == STATUS_MISSING:
        return DISPLAY_MISSING if saved else DISPLAY_EMPTY

    return DISPLAY_EMPTY


def get_current_risk_display(
    project: Any,
    current_risk_data: Any,
    locale: Any = None,
    project_id: Any = None,
    session_cache: Any = None,
) -> Dict[str, Any]:
    """
    Explain, for one project and one content locale, what may be displayed.

    Pure and read-only: it never mutates ``project``, never calls ``persist()``
    and never calls the AI. ``session_cache`` (an in-memory ``dict`` keyed by
    :func:`current_risk_cache_key`) is only consulted when the project itself
    has no usable record for the exact current input.

    Returns a dict with:

    * ``status``        protocol status (``available`` / ``stale`` / ...);
    * ``ui_state``      the state the page renders (adds ``empty``);
    * ``payload``       validated text payload, or ``None``;
    * ``saved_locales`` locales that do have a stored version;
    * ``needs_generation`` whether the user must generate this language;
    * ``is_stale``      whether an existing version is outdated;
    * ``reason``        protocol reason of a missing / invalid state;
    * ``input_hash``    the fingerprint of the current risk input;
    * ``locale`` / ``record_id`` / ``content_type``.
    """
    target = resolve_content_locale(locale)
    input_hash = current_risk_input_hash(project, current_risk_data, project_id)

    status = localized_content_status(
        project,
        CONTENT_TYPE,
        target,
        RECORD_ID,
        input_hash=input_hash,
    )

    payload: Optional[Dict[str, Any]] = None

    if status.get("status") == STATUS_LEGACY_COMPATIBLE:
        payload = validate_current_risk_text_payload(
            legacy_ai_text_get(project, CONTENT_TYPE, target, None)
        )

        if not payload:
            # Legacy free text in a shape we cannot validate: treat it as
            # missing rather than rendering unverified text.
            status = {
                **status,
                "status": STATUS_INVALID,
                "reason": status.get("reason") or REASON_NO_CONTENT,
            }

    elif status.get("status") in (
        STATUS_AVAILABLE,
        STATUS_STALE,
        STATUS_DETERMINISTIC,
    ):
        record = status.get("record")
        record = record if isinstance(record, dict) else {}

        payload = validate_current_risk_text_payload(record.get("payload"))

        if not payload:
            status = {**status, "status": STATUS_INVALID}

        elif status.get("status") == STATUS_DETERMINISTIC:
            status = {**status, "status": STATUS_AVAILABLE}

    if payload is None and status.get("status") == STATUS_MISSING and isinstance(session_cache, dict):
        cached = session_cache.get(
            current_risk_cache_key(
                _stable_project_id(project, project_id),
                target,
                input_hash,
            )
        )

        cached_payload = validate_current_risk_text_payload(cached)

        if cached_payload:
            payload = cached_payload
            status = {**status, "status": STATUS_AVAILABLE}

    ui_state = _ui_state(status)

    return {
        "status": status.get("status"),
        "ui_state": ui_state,
        "payload": payload,
        "saved_locales": list(status.get("saved_locales") or []),
        "needs_generation": ui_state not in (DISPLAY_AVAILABLE, DISPLAY_LEGACY_COMPATIBLE),
        "is_stale": bool(status.get("is_stale")),
        "reason": status.get("reason"),
        "input_hash": input_hash,
        "locale": target,
        "record_id": RECORD_ID,
        "content_type": CONTENT_TYPE,
        "source": status.get("source"),
    }


def text_payload_for_store(project: Any, payload: Any, locale: Any = None) -> Optional[Dict[str, Any]]:
    """
    Validated payload that may be handed to ``localized_content_put``.

    Returns ``None`` for anything that would create an invalid or empty
    record, so a failed generation can never reach storage.
    """
    validated = validate_current_risk_text_payload(payload)

    if not validated:
        return None

    if not resolve_content_locale(locale):
        return None

    return validated


# Re-exported for callers that only need the protocol source tag.
SOURCE_AI = SOURCE_AI_GENERATED
