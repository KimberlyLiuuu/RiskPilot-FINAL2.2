
import json
from copy import deepcopy
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from io import BytesIO
from docx import Document
from docx.shared import Pt
from docx.enum.text import WD_ALIGN_PARAGRAPH

import pandas as pd
import pycountry
import pydeck as pdk
import requests
import math
import streamlit as st
import streamlit.components.v1 as components

from i18n import (
    SUPPORTED_LANGUAGES,
    get_current_language,
    init_ui_language,
    is_supported_language,
    load_locale,
    mapped_label,
    risk_label,
    set_current_language,
    t,
)

# Locale registry: the single source of truth for which languages exist and
# which of them the UI may offer right now. app.py never hard codes a
# language list or a language display name.
from utils.locale_registry import (
    ai_language_name,
    enabled_ui_locales,
    locale_display_name,
    normalize_locale,
    sanitize_locale_candidate,
)

from utils.ui_language_state import (
    SELECTOR_KEY as UI_LANGUAGE_SELECTOR_KEY,
    restore_selector,
    set_active_project,
)

# Baseline display layer: deterministic legacy translation + per-language
# content selection. Kept Streamlit-free so it can be unit-tested.
from utils import (
    baseline_display,
    content_locale,
    log_display,
    report_contract,
)
from utils.baseline_display import (
    action_label,
    baseline_ai_text,
    get_baseline_display_text,
    impact_label,
    risk_chain_label,
)

# Daily Log display layer: per-language selection of the AI written log text
# (summary / risk title / reason / suggestion) plus the validation of a newly
# generated language version. Streamlit-free, read-only.
from utils.log_display import (
    LOG_USER_TEXT_FIELDS,
    PROJECT_NAME_FIELD,
    SOURCE_EMPTY,
    USER_TEXT_KEY,
    build_log_content_translation_prompt,
    build_log_text_version_prompt,
    build_project_name_translation_prompt,
    generated_text_payload,
    get_log_display_text,
    log_display_text,
    log_generate_button_key,
    log_risk_display_fields,
    merge_user_text_translation,
    project_display_name,
    project_needs_translation,
    summary_for_reasons,
    user_text_value,
    user_text_widget_key,
    validate_log_content_translation,
    validate_log_text_version,
    validate_project_name_translation,
)

# Current Risk display layer (stage 4A): the input fingerprint, the read-only
# per-language content selection, the AI response validation and the prompt
# builder. Streamlit-free, read-only, never calls the AI and never persists.
from utils import current_risk_display
from utils.current_risk_display import (
    CONTENT_TYPE as CURRENT_RISK_CONTENT_TYPE,
    RECORD_ID as CURRENT_RISK_RECORD_ID,
    bounded_cache_put,
    build_current_risk_context,
    build_current_risk_text_prompt,
    current_risk_cache_key,
    current_risk_generate_button_key,
    get_current_risk_display,
    validate_current_risk_text_payload,
)

# Stage 3 protocol primitives used to store one language version without
# touching any other language and without recomputing any risk number.
from utils.localized_content import (
    SOURCE_AI_GENERATED,
    make_localized_record,
    localized_content_put,
)
from utils.field_condition_model import ABSENCE_LEVELS, assess_field_conditions

# ============================================================
# UI LANGUAGE STATE
# ============================================================
# ui_lang controls the interface language only: page text, charts, risk
# names and the dynamic content rendered on the page.
#
# The pure state logic lives in utils/ui_language_state.py so it can be
# unit-tested without a Streamlit runtime. The single-writer rules are:
#   * ui_lang is written ONLY by the first-session initialisation below
#     and by the interface language selector callback.
#   * Loading a project, switching projects, creating / deleting projects,
#     running an analysis, exporting a report and reading historical data
#     must never write it.
#   * risk_report_language is a separate axis (exported report only). It
#     never changes ui_lang, and changing ui_lang never changes it.
#
# The selector widget deliberately uses its OWN session_state key
# (ui_language_selector). If the widget reused "ui_lang", a widget remount
# (for example after st.switch_page when opening a project) could drop or
# shadow the value and silently reset the interface language, because
# Streamlit cleans up the state of widgets that are not re-registered in a
# run. Here the widget is seeded once from ui_lang and only writes back
# through its on_change callback.
#
# Which languages may be offered comes from utils/locale_registry.py
# (locales/manifest.json). Only locales the registry marks as UI enabled are
# selectable, so a locale that is merely "planned" can never be listed and
# its (not yet existing) JSON is never loaded.
#
# Refresh persistence
# -------------------
# A full page reload builds a brand new Streamlit Session, so session_state
# alone cannot remember the language. The choice is therefore mirrored into
# the URL as "?lang=<bcp47>", which survives a reload and can be shared.
# After initialisation the data flow is strictly one-way:
#
#     ui_lang  ->  URL lang
#
# The URL is *read* only while ui_lang is still unset (first run of a
# session) and is otherwise only *written*, so a rerun can never push a stale
# URL value back over the language the user just picked.
#
# First-run priority: valid URL lang -> browser locale -> "en".

#: Query parameter that carries the interface language.
LANG_QUERY_KEY = "lang"


def read_language_query_param():
    """
    Candidate interface language from the URL, or ``None``.

    Defensive on purpose: the value may be missing, blank, a repeated
    parameter, contain control characters or be absurdly long. Never raises.
    """
    try:
        raw = st.query_params.get(LANG_QUERY_KEY)
    except Exception:
        return None

    return sanitize_locale_candidate(raw)


def read_browser_locale():
    """
    Candidate interface language from the browser, or ``None``.

    ``st.context.locale`` is the official Streamlit API (1.45+, verified on
    the pinned 1.63) exposing ``navigator.language``. It returns ``None``
    outside a script run. Using it avoids injecting custom JavaScript, which
    could flicker or rerun-loop a multipage ``st.navigation`` app.
    """
    try:
        raw = st.context.locale
    except Exception:
        return None

    return sanitize_locale_candidate(raw)


def sync_language_query_param(language=None) -> str:
    """
    Mirror the effective interface language into the URL.

    Only ``ui_lang -> URL`` is ever written, and only when the value really
    differs, so this cannot fight the selector. Writing a query parameter
    updates the browser URL without triggering a rerun.
    """
    target = normalize_locale(
        language if language is not None else get_current_language()
    )

    try:
        if st.query_params.get(LANG_QUERY_KEY) != target:
            st.query_params[LANG_QUERY_KEY] = target
    except Exception:
        # A URL that cannot be updated must never break the page.
        pass

    return target


def on_ui_language_change() -> None:
    """
    Interface language selector callback (channel: user input only).

    Validates the choice, stores it and mirrors it into the URL. It touches
    no other state on purpose: the selected project, the dashboard view and
    the report language all stay exactly as they were.
    """
    chosen = st.session_state.get(UI_LANGUAGE_SELECTOR_KEY)

    if not is_supported_language(chosen):
        return

    set_current_language(chosen)
    sync_language_query_param(get_current_language())


# First-session initialisation MUST run before any t() call, including the
# page title inside st.set_page_config. It only touches session_state, so it
# is still safe before the first page command. Idempotent: reruns, page
# switches and project changes keep the language that is already stored, so
# the URL and the browser locale are only consulted once per session.
init_ui_language(
    url_language=read_language_query_param(),
    browser_language=read_browser_locale(),
)

# ============================================================
# PAGE CONFIG
# ============================================================

st.set_page_config(
    page_title=t("app.page_title"),
    page_icon="🌍",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Normalise the URL: a missing, unknown or not-yet-enabled ?lang is replaced
# by the language actually in effect, so a reload always restores the same
# interface language and a shared link always says the truth. No exception is
# raised for garbage input - it simply ends up as "?lang=en".
sync_language_query_param()

# Seed the widget once, and re-seed it whenever Streamlit dropped the widget
# state. The value always comes from ui_lang, never the other way, so a lost
# widget can never reset the interface language.
restore_selector(st.session_state)

with st.sidebar:
    # Options and labels both come from the locale registry: planned locales
    # are not selectable, and labels are always the native language names.
    st.selectbox(
        t("common.language.label"),
        options=list(enabled_ui_locales()),
        format_func=locale_display_name,
        key=UI_LANGUAGE_SELECTOR_KEY,
        on_change=on_ui_language_change,
    )

# ============================================================
# STORAGE
# ============================================================

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
PROJECTS_FILE = DATA_DIR / "projects.json"

RISK_TO_SCORE = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def risk_icon(risk):
    return {"HIGH": "🔴", "MEDIUM": "🟠", "LOW": "🟢"}.get(
        str(risk).upper(), "⚪"
    )


def level_from_score(score):
    if score >= 70:
        return "HIGH"
    if score >= 45:
        return "MEDIUM"
    return "LOW"


def risk_score(risk):
    return RISK_TO_SCORE.get(str(risk).upper(), 1)


# ============================================================
# DISPLAY-ONLY LABEL MAPPINGS
# ============================================================
# Everything below only affects what is rendered on screen. The internal
# values stored in projects.json (project type, project scale, risk level,
# environment descriptors, hazard keys, reason strings) stay exactly as
# they are, and unknown legacy values fall back to the raw text instead of
# breaking the page.

_PROJECT_TYPE_DISPLAY_KEYS = {
    "Residential": "enum.project_type.residential",
    "Infrastructure": "enum.project_type.infrastructure",
    "Commercial": "enum.project_type.commercial",
    "Hospital": "enum.project_type.hospital",
    "School": "enum.project_type.school",
    "Industrial": "enum.project_type.industrial",
    "Transportation": "enum.project_type.transportation",
    "Energy": "enum.project_type.energy",
    "Other": "enum.project_type.other",
    "Construction": "enum.project_type.construction",
    "Residential Construction": "enum.project_type.residential",
    "Housing": "enum.project_type.housing",
    "Housing Construction": "enum.project_type.housing_construction",
}

_PROJECT_SCALE_DISPLAY_KEYS = {
    "Small": "enum.project_scale.small",
    "Medium": "enum.project_scale.medium",
    "Large": "enum.project_scale.large",
    "Mega Project": "enum.project_scale.mega",
}

_ENVIRONMENT_DISPLAY_KEYS = {
    "Urban": "enum.env.urban",
    "Suburban": "enum.env.suburban",
    "Rural": "enum.env.rural",
    "Flat": "enum.env.flat",
    "Hilly": "enum.env.hilly",
    "Mountainous": "enum.env.mountainous",
    "Coastal": "enum.env.coastal",
    "Good": "enum.env.good",
    "Moderate": "enum.env.moderate",
    "Poor": "enum.env.poor",
    "Low": "enum.env.low",
    "Medium": "enum.env.medium",
    "High": "enum.env.high",
    "Very High": "enum.env.very_high",
    "Unknown": "enum.env.unknown",
}

#: ``_ENVIRONMENT_DISPLAY_KEYS`` 的大小写不敏感索引：项目数据里同一个等级
#: 可能写成 ``Low`` / ``LOW`` / ``low``，显示层按枚举名匹配，不改写数据。
_ENVIRONMENT_DISPLAY_KEYS_BY_CASE = {
    name.casefold(): name for name in _ENVIRONMENT_DISPLAY_KEYS
}

_HAZARD_DISPLAY_KEYS = {
    "heavy_rainfall": "hazard.heavy_rainfall",
    "flooding": "hazard.flooding",
    "landslide": "hazard.landslide",
    "earthquake": "hazard.earthquake",
    "extreme_heat": "hazard.extreme_heat",
    "strong_wind": "hazard.strong_wind",
    "drought": "hazard.drought",
    "wildfire": "hazard.wildfire",
    "other": "hazard.other",
}

_REASON_DISPLAY_KEYS = {
    "Heavy precipitation": "reason.heavy_precipitation",
    "Elevated precipitation": "reason.elevated_precipitation",
    "Strong wind": "reason.strong_wind",
    "Elevated wind": "reason.elevated_wind",
    "Extreme heat": "reason.extreme_heat",
    "High temperature": "reason.high_temperature",
    "Heavy rain": "reason.heavy_rain",
    "Rain": "reason.rain",
    "Safety / incident signal": "reason.safety_signal",
    "Schedule / supply signal": "reason.schedule_signal",
    "Weather / water signal": "reason.weather_signal",
    "Equipment signal": "reason.equipment_signal",
    "Baseline only": "reason.baseline_only",
}

_CONTINENT_DISPLAY_KEYS = {
    "Africa": "geo.continent.africa",
    "Asia": "geo.continent.asia",
    "Europe": "geo.continent.europe",
    "North America": "geo.continent.north_america",
    "South America": "geo.continent.south_america",
    "Oceania": "geo.continent.oceania",
    "Other": "geo.continent.other",
}

# Current Risk component names are localized at render time (see
# ``_current_risk_component_labels``). They come from the locale files, never
# from the AI: the fusion weights and the contribution numbers are
# language-independent risk data.
_CURRENT_RISK_COMPONENT_NAMES = ("Baseline", "Weather", "Daily Logs", "Vision", "Financial")


# ------------------------------------------------------------
# LEGACY DETERMINISTIC CONTENT (risk chains / impacts / actions)
# ------------------------------------------------------------
# The deterministic display tables, the display-only label functions
# (chain_node_label / risk_chain_label / impact_label / action_label) and the
# per-language content selection now live in utils/baseline_display.py so
# they can be unit-tested without a Streamlit runtime. Nothing there is ever
# written back to project data.


# ------------------------------------------------------------
# AI TEXT ARCHIVE (per interface language)
# ------------------------------------------------------------
# Regenerable AI text is stored per interface language so that generating
# the Chinese version never overwrites the English one (and vice versa):
#
#   project["ai_text_by_language"][language][section]           = payload
#   project["ai_text_by_language"][language][section][record_id] = payload
#
# Risk values, levels, scores and the ontology keys of the visual risk
# detection are NOT part of this archive.

def ai_text_store(
    project,
    section,
    payload,
    record_id=None,
    language=None,
) -> None:
    """
    Store regenerable AI text under the current **content** language.

    Writing one language never removes another one, so regenerating the
    Chinese version can never overwrite the English version (and vice versa).

    The language is resolved through the dedicated content-language axis
    (``utils.content_locale``) instead of reading ``ui_lang`` here, so a later
    stage can let the user choose a content language that differs from the
    interface language without touching this call site.
    """
    baseline_display.ai_text_store(
        project,
        section,
        payload,
        record_id,
        language or content_locale.get_content_locale(st.session_state),
    )


def ai_text_lookup(project, section, record_id=None, language=None):
    """Return the AI text stored for a language, or None when missing."""
    return baseline_display.ai_text_lookup(
        project,
        section,
        record_id,
        language or content_locale.get_content_locale(st.session_state),
    )


def ai_generate_log_text_version(log, target_language):
    """
    Generate the AI written text of one daily log in ``target_language``.

    This never re-analyzes the site: the prompt may only rewrite the human
    readable ``summary`` / ``title`` / ``reason`` / ``suggestion`` values.
    The returned payload holds text only, so the stored

        level / score / severity / likelihood / exposure / raw_risk

    of the original analysis can never be recomputed, overwritten or
    migrated by a language version.

    Returns a validated ``{"summary": ..., "risks": [...]}`` payload, or an
    empty dict when the AI failed or the response did not match the stored
    risk count. Nothing is written here; the caller decides to archive.
    """
    analysis = log.get("ai_analysis") if isinstance(log, dict) else None
    analysis = analysis if isinstance(analysis, dict) else {}

    expected = log_display.log_risk_count(analysis)

    prompt = build_log_text_version_prompt(log, target_language)

    try:

        from agents.risk_agent import RiskAgent

        result = RiskAgent().analyze(prompt)

    except Exception:

        return {}

    return validate_log_text_version(result, expected)


def store_user_text_translation(container, language, values, fields=None) -> bool:
    """
    Write one user content translation for exactly one language.

    The validation lives in the read-only display module; this function only
    performs the write, so the page layer never builds ``localized_user_text``
    by itself:

    * the language must pass the locale registry, otherwise nothing is written;
    * only ``localized_user_text[language]`` is replaced: every other language
      version, the original text, the date, the severity, the S / L / E values
      and all risk numbers stay untouched;
    * an empty or invalid payload is never written, so a failed translation
      cannot replace working content.

    Returns ``True`` only when something was stored.
    """
    if not isinstance(container, dict):
        return False

    merged = merge_user_text_translation(
        container.get(USER_TEXT_KEY),
        language,
        values,
        fields,
    )

    if not merged:
        return False

    container[USER_TEXT_KEY] = merged

    return True


def ai_translate_project_name(project, target_locale, project_id=""):
    """
    Translate a project name into ``target_locale`` (user triggered only).

    Called exclusively from the "translate the project name into the current
    language" button: opening a page, refreshing, switching project or
    switching language never reaches this function.

    Returns a validated ``{"name": ...}``, or an empty dict when the AI failed
    or the response was not usable. Nothing is stored here: the caller decides.
    """
    prompt = build_project_name_translation_prompt(
        project,
        project_id,
        target_locale,
    )

    try:

        from agents.risk_agent import RiskAgent

        result = RiskAgent().analyze(prompt)

    except Exception:

        return {}

    return validate_project_name_translation(result)


def ai_translate_log_content(log, target_locale):
    """
    Translate the user written title / description of one daily log.

    The prompt carries the date, the severity and no risk number at all, and
    the validated payload contains text only, so the original log, its date and
    every stored risk value can never be changed by a translation. Returns an
    empty dict when the AI failed or the response was not usable.
    """
    prompt = build_log_content_translation_prompt(log, target_locale)

    try:

        from agents.risk_agent import RiskAgent

        result = RiskAgent().analyze(prompt)

    except Exception:

        return {}

    return validate_log_content_translation(result)


def translate_project_name_action(project_id, project) -> None:
    """
    User action: translate one project name into the current language.

    Only ``localized_user_text[current language]`` is written. The original
    name, the storage key, every other language version and all risk values
    stay exactly as they are.
    """
    locale = get_current_language()

    with st.spinner(
        t("dashboard.spinner_translate_project_name")
    ):

        translated = ai_translate_project_name(project, locale, project_id)

    if not translated or not store_user_text_translation(
        project,
        locale,
        translated,
        (PROJECT_NAME_FIELD,),
    ):

        st.error(
            t("dashboard.error_project_name_translation_failed")
        )

        return

    persist()

    st.rerun()


def translate_log_content_action(project, log) -> None:
    """
    User action: translate one daily log (title + description) into the
    current language.

    Only ``log["localized_user_text"][current language]`` is written: the
    original title / description, the date, the severity, the S / L / E values,
    the risk scores and the AI risk analysis are not touched.
    """
    locale = get_current_language()

    with st.spinner(
        t("dashboard.spinner_translate_log")
    ):

        translated = ai_translate_log_content(log, locale)

    if not translated or not store_user_text_translation(
        log,
        locale,
        translated,
        LOG_USER_TEXT_FIELDS,
    ):

        st.error(
            t("dashboard.error_log_translation_failed")
        )

        return

    persist()

    st.rerun()


def log_ai_text(analysis) -> dict:
    """
    Text-only part of a daily log analysis.

    Levels, scores and the S / L / E values stay untouched: regenerating
    the text in another language must never change the risk numbers.
    """
    if not isinstance(analysis, dict):
        return {}

    payload = {"summary": str(analysis.get("summary", ""))}

    risks = analysis.get("risks")

    if isinstance(risks, list):
        payload["risks"] = [
            {
                "title": str(item.get("title", "")),
                "reason": str(item.get("reason", "")),
                "suggestion": str(item.get("suggestion", "")),
            }
            for item in risks
            if isinstance(item, dict)
        ]

    return payload


def log_risk_display_item(risk_item, index, display_text) -> dict:
    """
    One stored log risk with its text fields set from the current language.

    ``display_text`` must come from ``utils.log_display`` so an unavailable
    language yields empty text instead of the other language's wording. The
    stored numbers (severity / likelihood / exposure / raw_risk / score /
    level) are always kept as they are.
    """
    item = dict(risk_item) if isinstance(risk_item, dict) else {}

    safe = log_risk_display_fields(display_text, index)

    for field in ("title", "reason", "suggestion"):
        item[field] = safe[field]

    return item


# Session bucket of the Current Risk explanation. The bucket is a plain dict
# keyed by a fully namespaced tuple (project, content_type, record_id, locale,
# input_hash) so a result generated for one project / language / risk input can
# never be shown for another. Keys are bounded per (project, locale) group
# instead of being cleared on project switch.
CURRENT_RISK_SESSION_CACHE_KEY = "current_risk_ai_cache"


def current_risk_session_cache() -> dict:
    """The bounded session cache of validated Current Risk explanations."""
    cache = st.session_state.get(CURRENT_RISK_SESSION_CACHE_KEY)

    if not isinstance(cache, dict):
        cache = {}
        st.session_state[CURRENT_RISK_SESSION_CACHE_KEY] = cache

    return cache


def explicit_label(key, language=None) -> str:
    """
    Deterministic label resolved against an explicit locale.

    This is the explicit-locale equivalent of ``t(key)`` and follows the
    same helper pattern as ``utils/baseline_display._translate``: the
    report language is an independent axis, so report labels are resolved
    against the report locale instead of the interface language.
    ``language=None`` keeps the interface language, and a missing key
    falls back to the raw key text (never to another language).
    """

    if language:

        return load_locale(language).get(key, key)

    return load_locale().get(key, key)


def explicit_mapped(value, mapping, language=None) -> str:
    """
    Display-only localization of one internal value through ``mapping``.

    The internal value itself is never rewritten: values that are missing
    from the mapping (or locales that do not translate the key) are
    returned unchanged.
    """

    raw = "" if value is None else str(value).strip()

    if not raw:

        return raw

    key = mapping.get(raw)

    if key:

        text = explicit_label(key, language)

        if text != key:

            return text

    return raw


def project_type_label(value, language=None) -> str:
    """
    System project type in a locale (``None`` -> interface language).

    Unknown project types are returned raw, so legacy data keeps working.
    """

    return explicit_mapped(value, _PROJECT_TYPE_DISPLAY_KEYS, language)


def project_scale_label(value) -> str:
    return mapped_label(value, _PROJECT_SCALE_DISPLAY_KEYS)


def environment_label(value) -> str:
    """
    环境等级（地形 / 排水 / 降雨 / 地震）在界面语言下的显示文本。

    枚举名匹配忽略大小写，``Low`` / ``LOW`` / ``low`` 都能得到
    ``enum.env.low`` 的译文；映射不认识的值原样返回。只作用于显示层，
    存储值不会被改写。
    """
    raw = "" if value is None else str(value).strip()
    enum_name = _ENVIRONMENT_DISPLAY_KEYS_BY_CASE.get(raw.casefold())

    if enum_name is None:
        return mapped_label(value, _ENVIRONMENT_DISPLAY_KEYS)

    return mapped_label(enum_name, _ENVIRONMENT_DISPLAY_KEYS)


def hazard_label(value) -> str:
    return mapped_label(value, _HAZARD_DISPLAY_KEYS)


def continent_label(value) -> str:
    return mapped_label(value, _CONTINENT_DISPLAY_KEYS)


def reason_label(reason) -> str:
    """
    Display-only label for one computed risk reason.

    The stored reason strings stay English (they are also fed into the AI
    report prompt); only the rendered text is localized here.
    """
    text = "" if reason is None else str(reason).strip()

    if not text:
        return text

    if text in _REASON_DISPLAY_KEYS:
        return mapped_label(text, _REASON_DISPLAY_KEYS)

    prefix = "AI log risk: "

    if text.startswith(prefix):
        label = t("reason.ai_log_risk", level=risk_label(text[len(prefix):]))
        if label != "reason.ai_log_risk":
            return label

    # AI generated text, already written in the interface language.
    return text


def reason_text_label(reason_text) -> str:
    """Display-only localization of a joined reasons cell (" · " separated)."""
    parts = [reason_label(part) for part in str(reason_text or "").split(" · ")]
    return " · ".join(part for part in parts if part)


def ai_output_language_rules() -> str:
    """
    Prompt fragment that keeps every JSON key English while asking the model
    to write the user-facing text in the current **content** language.

    The locale is read from the content language axis (``content_locale``,
    which follows ``ui_lang`` unless an override exists) and its prompt name
    comes from the locale registry (``ai_language_name``), so no language is
    hard coded here: enabling a locale in the registry is enough for both the
    Baseline assessment and the Daily Log analysis prompt to ask for it.

    A locale the registry does not currently offer as a content language is
    rejected by the content-locale gate and falls back to the default locale,
    whose AI name ``ai_language_name`` returns as its own last resort. This
    function writes nothing: ``ui_lang``, the content language override and
    the report language are all left untouched.
    """
    locale = content_locale.get_content_locale(st.session_state)

    language = ai_language_name(locale)

    return (
        "\nOUTPUT LANGUAGE RULES\n"
        "---------------------\n"
        f"- Write every human-readable text value in {language}.\n"
        "- Keep all JSON field names exactly as specified in English.\n"
        '- Keep risk levels exactly "LOW", "MEDIUM" or "HIGH".\n'
        "- Keep severity, likelihood and exposure as integers 1-5.\n"
        "- Do not translate field names or the level keywords.\n"
    )


DEFAULT_PROJECTS = {
    "Nairobi Affordable Housing": {
        "country": "Kenya", "city": "Nairobi", "address": "Nairobi, Kenya",
        "type": "Residential Construction", "workers": 320,
        "project_scale": "Large", "start_date": "2026-01-01",
        "end_date": "2027-12-31", "baseline_score": 58,
        "risk": "Medium", "daily_logs": [],
        "baseline_risk": {
            "level": "MEDIUM", "score": 58, "terrain": "Urban",
            "drainage": "Moderate", "rainfall": "High",
            "earthquake": "Low",
            "risks": {
                "heavy_rainfall": "MEDIUM", "flooding": "MEDIUM",
                "landslide": "LOW", "earthquake": "LOW",
                "extreme_heat": "LOW", "strong_wind": "LOW"
            },
            "selected_hazards": {
                "heavy_rainfall": True, "flooding": True,
                "landslide": False, "earthquake": False,
                "extreme_heat": False, "strong_wind": False,
                "drought": False, "wildfire": False, "other": False
            },
            "risk_chains": [
                "Heavy Rainfall → Increased Surface Runoff → Drainage Pressure → Site Water Accumulation → Safety + Schedule + Quality Risks"
            ],
            "impacts": [
                "Weather-sensitive outdoor work may be interrupted.",
                "Excavation areas may accumulate water."
            ],
            "actions": [
                "Monitor weather conditions and prepare temporary rain protection.",
                "Inspect drainage systems and prepare emergency pumping equipment.",
                "Add identified environmental risks to the project risk register."
            ]
        }
    },
    "Nairobi Infrastructure": {
        "country": "Kenya", "city": "Nairobi", "address": "Nairobi, Kenya",
        "type": "Infrastructure", "workers": 180,
        "project_scale": "Medium", "start_date": "2026-01-01",
        "end_date": "2027-12-31", "baseline_score": 38,
        "risk": "Low", "daily_logs": [],
        "baseline_risk": {
            "level": "LOW", "score": 38, "terrain": "Urban",
            "drainage": "Good", "rainfall": "Moderate", "earthquake": "Low",
            "risks": {
                "heavy_rainfall": "LOW", "flooding": "LOW",
                "landslide": "LOW", "earthquake": "LOW",
                "extreme_heat": "LOW", "strong_wind": "LOW"
            },
            "selected_hazards": {}, "risk_chains": [],
            "impacts": ["No major environmental construction impact identified."],
            "actions": [
                "Continue routine environmental monitoring.",
                "Add identified environmental risks to the project risk register."
            ]
        }
    },
    "São Paulo Infrastructure": {
        "country": "Brazil", "city": "São Paulo", "address": "São Paulo, Brazil",
        "type": "Infrastructure", "workers": 210,
        "project_scale": "Large", "start_date": "2026-01-01",
        "end_date": "2027-12-31", "baseline_score": 52,
        "risk": "Medium", "daily_logs": [],
        "baseline_risk": {
            "level": "MEDIUM", "score": 52, "terrain": "Urban",
            "drainage": "Moderate", "rainfall": "High", "earthquake": "Low",
            "risks": {
                "heavy_rainfall": "MEDIUM", "flooding": "MEDIUM",
                "landslide": "LOW", "earthquake": "LOW",
                "extreme_heat": "LOW", "strong_wind": "LOW"
            },
            "selected_hazards": {}, "risk_chains": [
                "Heavy Rainfall → Increased Surface Runoff → Drainage Pressure → Site Water Accumulation → Safety + Schedule Risks"
            ],
            "impacts": ["Weather-sensitive outdoor work may be interrupted."],
            "actions": ["Monitor weather conditions.", "Inspect drainage systems regularly."]
        }
    },
    "Brazil Housing Project": {
        "country": "Brazil", "city": "São Paulo", "address": "São Paulo, Brazil",
        "type": "Housing Construction", "workers": 150,
        "project_scale": "Medium", "start_date": "2026-01-01",
        "end_date": "2027-12-31", "baseline_score": 35,
        "risk": "Low", "daily_logs": [],
        "baseline_risk": {
            "level": "LOW", "score": 35, "terrain": "Urban",
            "drainage": "Good", "rainfall": "Moderate", "earthquake": "Low",
            "risks": {}, "selected_hazards": {}, "risk_chains": [],
            "impacts": [],
            "actions": ["Continue routine environmental monitoring."]
        }
    },
    "Johannesburg Project": {
        "country": "South Africa", "city": "Johannesburg", "address": "Johannesburg, South Africa",
        "type": "Construction", "workers": 260,
        "project_scale": "Large", "start_date": "2026-01-01",
        "end_date": "2027-12-31", "baseline_score": 49,
        "risk": "Medium", "daily_logs": [],
        "baseline_risk": {
            "level": "MEDIUM", "score": 49, "terrain": "Hilly",
            "drainage": "Moderate", "rainfall": "High", "earthquake": "Low",
            "risks": {
                "heavy_rainfall": "MEDIUM", "flooding": "MEDIUM",
                "landslide": "MEDIUM", "earthquake": "LOW",
                "extreme_heat": "LOW", "strong_wind": "LOW"
            },
            "selected_hazards": {}, "risk_chains": [], "impacts": [],
            "actions": ["Inspect drainage systems.", "Monitor slope stability."]
        }
    },
    "Cape Town Housing": {
        "country": "South Africa", "city": "Cape Town", "address": "Cape Town, South Africa",
        "type": "Housing", "workers": 140,
        "project_scale": "Medium", "start_date": "2026-01-01",
        "end_date": "2027-12-31", "baseline_score": 34,
        "risk": "Low", "daily_logs": [],
        "baseline_risk": {
            "level": "LOW", "score": 34, "terrain": "Coastal",
            "drainage": "Good", "rainfall": "Low", "earthquake": "Low",
            "risks": {}, "selected_hazards": {}, "risk_chains": [],
            "impacts": [],
            "actions": ["Continue routine environmental monitoring."]
        }
    }
}


def save_projects(projects):
    ensure_data_dir()
    PROJECTS_FILE.write_text(
        json.dumps(projects, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def load_projects():
    ensure_data_dir()
    if not PROJECTS_FILE.exists():
        projects = deepcopy(DEFAULT_PROJECTS)
        save_projects(projects)
        return projects

    try:
        projects = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
        if not isinstance(projects, dict) or not projects:
            projects = deepcopy(DEFAULT_PROJECTS)
    except Exception:
        projects = deepcopy(DEFAULT_PROJECTS)

    changed = False
    for project in projects.values():
        if "daily_logs" not in project:
            project["daily_logs"] = []
            changed = True
        if "address" not in project:
            project["address"] = f'{project.get("city","")}, {project.get("country","")}'
            changed = True
        if "starred" not in project:
            project["starred"] = False
            changed = True
        if "pinned" not in project:
            project["pinned"] = False
            changed = True
        if "archived" not in project:
            project["archived"] = False
            changed = True
        if "ended_at" not in project:
            project["ended_at"] = None
            changed = True
        for log in project.get("daily_logs", []):
            if "log_id" not in log:
                log["log_id"] = datetime.now().strftime("%Y%m%d%H%M%S%f")
                changed = True
            if "ai_analysis" not in log:
                level = {"LOW":"LOW","MEDIUM":"MEDIUM","HIGH":"HIGH","Low":"LOW","Medium":"MEDIUM","High":"HIGH"}.get(str(log.get("severity","LOW")), "LOW")
                log["ai_analysis"] = {
                    "level": level,
                    "score": {"LOW":25,"MEDIUM":55,"HIGH":80}.get(level,25),
                    "summary": "Imported from an earlier RiskPilot log.",
                    "risks": [],
                    "source": "Migrated",
                }
                changed = True

    if changed:
        save_projects(projects)

    return projects


if "projects" not in st.session_state:
    st.session_state.projects = load_projects()

if "selected_project" not in st.session_state:
    st.session_state.selected_project = None

if "dashboard_view" not in st.session_state:
    st.session_state.dashboard_view = "overview"


def persist():
    save_projects(st.session_state.projects)


# ============================================================
# COUNTRY DATA
# ============================================================

COUNTRIES = sorted(
    [country.name for country in pycountry.countries],
    key=str.lower
)



# ============================================================
# CONTINENT CLASSIFICATION
# ============================================================

CONTINENT_CODES = {
    "Africa": set(
        "DZ AO BJ BW BF BI CV CM CF TD KM CG CD CI DJ EG GQ ER SZ ET GA GM GH GN GW KE LS LR LY MG MW ML MR MU MA MZ NA NE NG RW ST SN SC SL SO ZA SS SD TZ TG TN UG ZM ZW".split()
    ),
    "Asia": set(
        "AF AM AZ BH BD BT BN KH CN CY GE IN ID IR IQ IL JP JO KZ KW KG LA LB MY MV MN MM NP KP OM PK PS PH QA SA SG KR LK SY TJ TH TL TR TM AE UZ VN YE".split()
    ),
    "Europe": set(
        "AL AD AT BY BE BA BG HR CZ DK EE FI FR DE GR HU IS IE IT LV LI LT LU MT MD MC ME NL MK NO PL PT RO RU SM RS SK SI ES SE CH UA GB VA".split()
    ),
    "North America": set(
        "CA US MX AG BS BB BZ CR CU DM DO SV GD GT HT HN JM NI PA KN LC VC TT".split()
    ),
    "South America": set(
        "AR BO BR CL CO EC GY PY PE SR UY VE".split()
    ),
    "Oceania": set(
        "AU FJ KI MH FM NR NZ PW PG WS SB TO TV VU".split()
    ),
}


def get_continent(country_name):
    try:
        country = pycountry.countries.get(name=country_name)
        code = country.alpha_2 if country else ""
    except Exception:
        code = ""

    for continent, codes in CONTINENT_CODES.items():
        if code in codes:
            return continent

    # Useful fallbacks for common names / territories.
    normalized = str(country_name).strip().lower()
    fallback = {
        "russia": "Europe",
        "taiwan": "Asia",
        "kosovo": "Europe",
        "western sahara": "Africa",
        "réunion": "Africa",
        "reunion": "Africa",
    }
    return fallback.get(normalized, "Other")


CONTINENT_ORDER = [
    "Africa",
    "Asia",
    "Europe",
    "North America",
    "South America",
    "Oceania",
    "Other",
]

CONTINENT_ICONS = {
    "Africa": "🌍",
    "Asia": "🌏",
    "Europe": "🇪🇺",
    "North America": "🌎",
    "South America": "🌎",
    "Oceania": "🌊",
    "Other": "🗺️",
}



# ============================================================
# PROJECT LIFECYCLE / AI DAILY LOGS
# ============================================================

def normalize_ai_level(value):
    text = str(value or "").strip().upper()
    mapping = {
        "低": "LOW", "低风险": "LOW", "LOW": "LOW",
        "中": "MEDIUM", "中风险": "MEDIUM", "MEDIUM": "MEDIUM",
        "高": "HIGH", "高风险": "HIGH", "HIGH": "HIGH",
    }
    return mapping.get(text, "LOW")

def ai_baseline_assessment(
    country,
    city,
    address,
    project_type,
    project_scale,
    workers,
    start_date,
    end_date,
):
    """
    Build the project's initial environmental baseline.

    The assessment uses the existing RiskPilot AI risk agent.
    It returns the structure expected by New Project and
    Project Dashboard.
    """

    prompt = f"""
You are RiskPilot's pre-project environmental risk assessment agent.

Assess the construction project's baseline environmental risk
before construction begins.

PROJECT INFORMATION
Country: {country}
City: {city}
Project Address: {address}
Project Type: {project_type}
Project Scale: {project_scale}
Workers: {workers}
Planned Start Date: {start_date}
Planned End Date: {end_date}

Analyze the project location and construction context.

Assess:

1. Terrain
2. Drainage exposure
3. Heavy rainfall
4. Flooding
5. Landslide
6. Earthquake
7. Extreme heat
8. Strong wind
9. Drought
10. Wildfire

For each hazard, classify the risk as exactly:
LOW, MEDIUM, or HIGH.

Then identify realistic environmental risk chains,
potential construction impacts, and preventive actions.

Return ONLY valid JSON in this exact structure:

{{
    "level": "LOW",
    "score": 35,
    "terrain": "Urban",
    "drainage": "Good",
    "rainfall": "Moderate",
    "earthquake": "Low",

    "risks": {{
        "heavy_rainfall": "LOW",
        "flooding": "LOW",
        "landslide": "LOW",
        "earthquake": "LOW",
        "extreme_heat": "LOW",
        "strong_wind": "LOW",
        "drought": "LOW",
        "wildfire": "LOW"
    }},

    "selected_hazards": {{
        "heavy_rainfall": false,
        "flooding": false,
        "landslide": false,
        "earthquake": false,
        "extreme_heat": false,
        "strong_wind": false,
        "drought": false,
        "wildfire": false,
        "other": false
    }},

    "risk_chains": [],

    "impacts": [],

    "actions": []
}}

RULES:

- score must be an integer from 0 to 100.
- LOW = 0-44
- MEDIUM = 45-69
- HIGH = 70-100
- level must match score.
- Do not invent precise measurements that are unavailable.
- Use reasonable environmental evidence for the stated location.
- selected_hazards should be true only for meaningful hazards.
- risk_chains must contain concise cause → effect chains.
- impacts must describe realistic construction consequences.
- actions must be practical construction risk controls.
{ai_output_language_rules()}"""

    try:
        # ----------------------------------------------------
        # Use the same AI risk agent already used by Daily Logs.
        # ----------------------------------------------------

        from agents.risk_agent import RiskAgent

        agent = RiskAgent()

        result = agent.analyze(prompt)

        if not isinstance(result, dict):
            raise ValueError(
                "AI baseline returned an invalid response."
            )

        # ----------------------------------------------------
        # Normalize score
        # ----------------------------------------------------

        raw_score = result.get(
            "score",
            result.get("overall_score", 0),
        )

        try:
            score = int(float(raw_score))
        except (TypeError, ValueError):
            score = 0

        score = max(
            0,
            min(100, score),
        )

        # ----------------------------------------------------
        # Normalize level
        # ----------------------------------------------------

        raw_level = result.get(
            "level",
            result.get("overall_level"),
        )

        if raw_level:
            level = normalize_ai_level(
                raw_level
            )
        else:
            level = level_from_score(score)

        # Make sure score and level agree.
        if score >= 70:
            level = "HIGH"
        elif score >= 45:
            level = "MEDIUM"
        else:
            level = "LOW"

        # ----------------------------------------------------
        # Environmental fields
        # ----------------------------------------------------

        risks = result.get(
            "risks",
            {},
        )

        if not isinstance(risks, dict):
            risks = {}

        hazard_names = [
            "heavy_rainfall",
            "flooding",
            "landslide",
            "earthquake",
            "extreme_heat",
            "strong_wind",
            "drought",
            "wildfire",
        ]

        normalized_risks = {}

        for hazard in hazard_names:
            value = risks.get(
                hazard,
                "LOW",
            )

            normalized_risks[hazard] = normalize_ai_level(
                value
            )

        # ----------------------------------------------------
        # Selected hazards
        # ----------------------------------------------------

        selected_hazards = result.get(
            "selected_hazards",
            {},
        )

        if not isinstance(
            selected_hazards,
            dict,
        ):
            selected_hazards = {}

        normalized_selected_hazards = {}

        for hazard in hazard_names:
            normalized_selected_hazards[hazard] = bool(
                selected_hazards.get(
                    hazard,
                    normalized_risks[hazard] != "LOW",
                )
            )

        normalized_selected_hazards["other"] = bool(
            selected_hazards.get(
                "other",
                False,
            )
        )

        # ----------------------------------------------------
        # Lists
        # ----------------------------------------------------

        risk_chains = result.get(
            "risk_chains",
            [],
        )

        impacts = result.get(
            "impacts",
            [],
        )

        actions = result.get(
            "actions",
            [],
        )

        if not isinstance(risk_chains, list):
            risk_chains = []

        if not isinstance(impacts, list):
            impacts = []

        if not isinstance(actions, list):
            actions = []

        risk_chains = [
            str(item).strip()
            for item in risk_chains
            if str(item).strip()
        ]

        impacts = [
            str(item).strip()
            for item in impacts
            if str(item).strip()
        ]

        actions = [
            str(item).strip()
            for item in actions
            if str(item).strip()
        ]

        # ----------------------------------------------------
        # Return structure expected by the current app.
        # ----------------------------------------------------

        return {
            "success": True,

            "level": level,

            "score": score,

            "terrain": str(
                result.get(
                    "terrain",
                    "Unknown",
                )
            ),

            "drainage": str(
                result.get(
                    "drainage",
                    "Unknown",
                )
            ),

            "rainfall": str(
                result.get(
                    "rainfall",
                    "Unknown",
                )
            ),

            "earthquake": str(
                result.get(
                    "earthquake",
                    normalized_risks.get(
                        "earthquake",
                        "LOW",
                    ),
                )
            ),

            "risks": normalized_risks,

            "selected_hazards": normalized_selected_hazards,

            "risk_chains": risk_chains,

            "impacts": impacts,

            "actions": actions,

            "source": "AI",
        }

    except Exception as exc:

        # ----------------------------------------------------
        # Safe fallback
        #
        # If the AI agent/API is unavailable, the project can
        # still be created rather than crashing the application.
        # ----------------------------------------------------

        return {
            "success": True,

            "level": "LOW",

            "score": 25,

            "terrain": "Unknown",

            "drainage": "Unknown",

            "rainfall": "Unknown",

            "earthquake": "Unknown",

            "risks": {
                "heavy_rainfall": "LOW",
                "flooding": "LOW",
                "landslide": "LOW",
                "earthquake": "LOW",
                "extreme_heat": "LOW",
                "strong_wind": "LOW",
                "drought": "LOW",
                "wildfire": "LOW",
            },

            "selected_hazards": {
                "heavy_rainfall": False,
                "flooding": False,
                "landslide": False,
                "earthquake": False,
                "extreme_heat": False,
                "strong_wind": False,
                "drought": False,
                "wildfire": False,
                "other": False,
            },

            "risk_chains": [],

            "impacts": [
                "AI environmental assessment was unavailable."
            ],

            "actions": [
                "Verify environmental conditions manually "
                "before construction begins."
            ],

            "source": "Fallback",

            "error": str(exc),
        }

# ============================================================
# DAILY LOG S × L × E RISK MODEL
# ============================================================

def clamp_risk_dimension(value, default=1):
    """
    Force Severity / Likelihood / Exposure into 1-5.
    """
    try:
        value = int(float(value))
    except (TypeError, ValueError):
        value = default

    return max(1, min(5, value))


def sle_raw_to_score(raw_risk):
    """
    Convert S × L × E raw risk (1-125)
    into RiskPilot's 0-100 risk index.

    Raw risk:
        1-20   -> LOW    -> 10-44
        21-50  -> MEDIUM -> 45-69
        51-125 -> HIGH   -> 70-95

    The score is a relative RiskPilot risk index,
    NOT an accident probability.
    """

    raw_risk = max(1, min(125, int(raw_risk)))

    # LOW
    if raw_risk <= 20:
        score = 10 + ((raw_risk - 1) / 19) * 34

    # MEDIUM
    elif raw_risk <= 50:
        score = 45 + ((raw_risk - 21) / 29) * 24

    # HIGH
    else:
        score = 70 + ((raw_risk - 51) / 74) * 25

    return round(score)


def calculate_sle_risk(severity, likelihood, exposure):
    """
    Calculate one risk using:

        Raw Risk = Severity × Likelihood × Exposure

    Each dimension is 1-5.
    """

    severity = clamp_risk_dimension(severity)
    likelihood = clamp_risk_dimension(likelihood)
    exposure = clamp_risk_dimension(exposure)

    raw_risk = (
        severity
        * likelihood
        * exposure
    )

    score = sle_raw_to_score(raw_risk)

    return {
        "severity": severity,
        "likelihood": likelihood,
        "exposure": exposure,
        "raw_risk": raw_risk,
        "score": score,
        "level": level_from_score(score),
    }


def normalize_daily_log_risks(ai_result):
    """
    Validate AI-extracted Daily Log risks and calculate
    S × L × E scores deterministically.
    """

    raw_risks = ai_result.get("risks", [])

    if not isinstance(raw_risks, list):
        raw_risks = []

    normalized = []

    for item in raw_risks:

        if not isinstance(item, dict):
            continue

        title = str(
            item.get(
                "title",
                item.get(
                    "risk_name",
                    "Construction Risk",
                ),
            )
        ).strip()

        reason = str(
            item.get(
                "reason",
                item.get(
                    "evidence",
                    item.get(
                        "description",
                        "",
                    ),
                ),
            )
        ).strip()

        suggestion = str(
            item.get(
                "suggestion",
                item.get(
                    "action",
                    "",
                ),
            )
        ).strip()

        # ----------------------------------------------------
        # AI provides dimensions only.
        # Python controls all calculations.
        # ----------------------------------------------------

        severity = clamp_risk_dimension(
            item.get("severity", 1)
        )

        likelihood = clamp_risk_dimension(
            item.get("likelihood", 1)
        )

        exposure = clamp_risk_dimension(
            item.get("exposure", 1)
        )

        calculation = calculate_sle_risk(
            severity,
            likelihood,
            exposure,
        )

        normalized.append({
            "title": title,
            "reason": reason,
            "suggestion": suggestion,

            "severity": calculation["severity"],
            "likelihood": calculation["likelihood"],
            "exposure": calculation["exposure"],

            "raw_risk": calculation["raw_risk"],
            "score": calculation["score"],
            "level": calculation["level"],
        })

    return normalized


# ============================================================
# AI DAILY LOG ANALYSIS
# ============================================================

def ai_analyze_daily_log(title, description):
    """
    Daily Log architecture:

    Construction Log
        ↓
    AI extracts risk facts
        ↓
    AI evaluates S / L / E (1-5)
        ↓
    Python calculates S × L × E
        ↓
    RiskPilot determines score + level

    AI never determines the final 0-100 score.
    """

    prompt = f"""
You are RiskPilot's construction Daily Log Risk Analysis Agent.

Analyze ONLY the facts explicitly stated in the construction log.

DAILY LOG

Title:
{title}

Description:
{description}

Your task is to identify actual construction risks and evaluate
three dimensions for EACH identified risk.

============================================================
S — SEVERITY
============================================================

1 = Negligible
Minor inconvenience with almost no safety, schedule,
equipment, quality, or operational consequence.

2 = Minor
Limited consequence. Small delay, minor operational issue,
or easily controlled condition.

3 = Moderate
Meaningful consequence requiring management attention.

4 = Serious
Could cause injury, major equipment disruption,
significant construction delay, or major operational impact.

5 = Critical
Could cause fatal/very serious injury, structural failure,
major fire/flooding, catastrophic equipment event,
or severe project disruption.


============================================================
L — LIKELIHOOD
============================================================

1 = Very unlikely
The risk is unlikely to develop based on the log.

2 = Unlikely
Possible, but conditions are mostly controlled.

3 = Possible
There is a realistic possibility of the risk occurring
or worsening.

4 = Likely
Conditions strongly indicate the risk may occur
or continue.

5 = Very likely / occurring
The hazardous event is already occurring or is highly likely
to continue without intervention.


============================================================
E — EXPOSURE
============================================================

1 = Minimal
Almost no workers, equipment, or active construction
activities are exposed.

2 = Low
Limited number of workers/equipment or short exposure.

3 = Moderate
Normal active construction exposure.

4 = High
Multiple workers, important equipment, or continuous
construction activities are exposed.

5 = Very high
Large number of workers, major equipment, or widespread
continuous exposure.


============================================================
IMPORTANT RULES
============================================================

1. Do NOT output a 0-100 risk score.

2. Do NOT calculate the final risk level.

3. RiskPilot's Python scoring engine performs all numerical
   risk calculations.

4. Evaluate Severity, Likelihood and Exposure separately.

5. Each must be an INTEGER from 1 to 5.

6. Do not exaggerate normal construction activities.

7. Minor delays without safety consequences should generally
   have low Severity.

8. "No accident occurred" means NO accident occurred.

9. "No equipment failure occurred" means there was NO
   equipment failure.

10. Statements describing normal operation should not
    automatically create a risk.

11. HIGH values (4-5) must have clear evidence from the log.

12. Do not invent workers, equipment failures, injuries,
    weather conditions, or consequences not stated.

============================================================
OUTPUT
============================================================

Return structured JSON in this form:

{{
    "summary": "Short factual assessment",
    "risks": [
        {{
            "title": "Risk name",
            "reason": "Evidence directly from the log",
            "severity": 1,
            "likelihood": 1,
            "exposure": 1,
            "suggestion": "Practical risk control"
        }}
    ]
}}

If the log describes normal work and contains no meaningful
risk:

{{
    "summary": "Normal construction activity. No significant risk identified.",
    "risks": []
}}

Return only the structured result.
{ai_output_language_rules()}"""

    try:

        from agents.risk_agent import RiskAgent

        result = RiskAgent().analyze(prompt)

        if not isinstance(result, dict):
            raise ValueError(
                "Daily Log AI returned invalid data."
            )

        # ----------------------------------------------------
        # Normalize AI-extracted risks
        # ----------------------------------------------------

        risks = normalize_daily_log_risks(
            result
        )

        # ----------------------------------------------------
        # Overall Daily Log score
        #
        # We use the MOST CRITICAL identified risk.
        #
        # This prevents multiple small LOW risks from
        # artificially becoming HIGH.
        # ----------------------------------------------------

        if risks:

            most_critical = max(
                risks,
                key=lambda item: item["score"],
            )

            score = most_critical["score"]

        else:

            # Normal construction day.
            score = 10

        # ----------------------------------------------------
        # Python decides level.
        # ----------------------------------------------------

        level = level_from_score(
            score
        )

        summary = str(
            result.get(
                "summary",
                "",
            )
        ).strip()

        if not summary:

            if not risks:
                summary = (
                    "Normal construction activity. "
                    "No significant risk identified."
                )

            elif level == "LOW":
                summary = (
                    "Minor construction risk identified. "
                    "Routine monitoring is recommended."
                )

            elif level == "MEDIUM":
                summary = (
                    "A meaningful construction risk requires "
                    "management attention."
                )

            else:
                summary = (
                    "A significant construction risk requires "
                    "priority control measures."
                )

        return {
            "level": level,
            "score": score,
            "summary": summary,
            "risks": risks,

            "risk_count": len(risks),

            "scoring_method":
                "RiskPilot S × L × E Risk Model",

            "formula":
                "Risk = Severity × Likelihood × Exposure",

            "score_type":
                "Relative Risk Index",

            "source":
                "AI Risk Extraction + Deterministic Scoring Engine",
        }

    except Exception as exc:

        # ----------------------------------------------------
        # Safe fallback
        # ----------------------------------------------------

        return {
            "level": "LOW",
            "score": 10,

            "summary": (
                "AI analysis was temporarily unavailable. "
                "No automated risk classification was applied."
            ),

            "risks": [],

            "risk_count": 0,

            "scoring_method":
                "RiskPilot S × L × E Risk Model",

            "formula":
                "Risk = Severity × Likelihood × Exposure",

            "score_type":
                "Relative Risk Index",

            "source":
                "Fallback",

            "error":
                str(exc),
        }

def ai_explain_current_risk(project, current, weather=None, target_locale=None, project_id=None):
    """
    Generate the Current Risk explanation in ``target_locale``.

    Only ever reached from the user-triggered button: opening the page,
    rerunning, switching project or switching language never calls this.

    The model receives a prompt whose human-readable values must be written in
    the target language (never hard coded English). Its response is validated
    by ``validate_current_risk_text_payload`` before the caller may store it,
    so a response that carries a score / level / S / L / E field, a wrong type
    or an over-long value is rejected and persisted nowhere.

    Returns ``{"success": bool, "payload": {...} or None, "error": str}``.
    Nothing is written here.
    """
    context = build_current_risk_context(
        project,
        current,
        weather,
        project_id=project_id,
    )

    prompt = build_current_risk_text_prompt(context, target_locale)

    try:
        from agents.risk_agent import RiskAgent

        result = RiskAgent().explain_current_risk(prompt)

    except Exception as exc:
        return {"success": False, "payload": None, "error": str(exc)}

    payload = validate_current_risk_text_payload(result)

    if not payload:
        return {
            "success": False,
            "payload": None,
            "error": "invalid_current_risk_payload",
        }

    return {"success": True, "payload": payload, "error": ""}


def render_current_risk_text(payload) -> None:
    """
    Render one validated Current Risk payload.

    Only the four text fields are shown, in the order they were generated
    for the current content language. No other language is ever substituted.
    """
    if not isinstance(payload, dict):
        return

    st.markdown(t("dashboard.ai_explanation_header"))

    summary = str(payload.get("summary", "")).strip()

    if summary:
        st.write(summary)

    risk_level_text = str(payload.get("risk_level_text", "")).strip()

    if risk_level_text:
        st.markdown(t("dashboard.current_risk_level_text_header"))
        st.write(risk_level_text)

    reasons = payload.get("reasons")

    if isinstance(reasons, list) and reasons:
        st.markdown(t("dashboard.current_risk_reasons_header"))

        for item in reasons:
            st.write(f"- {item}")

    recommendations = payload.get("recommendations")

    if isinstance(recommendations, list) and recommendations:
        st.markdown(t("dashboard.recommended_focus"))

        for item in recommendations:
            st.info(item)


def enrich_log(log):
    """Migrate old logs while keeping their original information."""
    changed = False
    if not log.get("log_id"):
        log["log_id"] = datetime.now().strftime("%Y%m%d%H%M%S%f")
        changed = True

    if "ai_analysis" not in log:
        old_level = normalize_ai_level(log.get("severity", "LOW"))
        log["ai_analysis"] = {
            "level": old_level,
            "score": {"LOW": 25, "MEDIUM": 55, "HIGH": 80}.get(old_level, 25),
            "summary": "Imported from an earlier RiskPilot log.",
            "risks": [],
            "source": "Migrated",
        }
        changed = True

    # Severity is now derived from AI, not selected by the user.
    ai_level = normalize_ai_level(
        log.get("ai_analysis", {}).get("level", log.get("severity", "LOW"))
    )
    if log.get("severity") != ai_level:
        log["severity"] = ai_level
        changed = True

    return changed


def ensure_project_flags(project):
    changed = False
    defaults = {
        "starred": False,
        "pinned": False,
        "archived": False,
        "ended_at": None,
    }

    for key, value in defaults.items():
        if key not in project:
            project[key] = value
            changed = True

    if "daily_logs" not in project:
        project["daily_logs"] = []
        changed = True

    for log in project.get("daily_logs", []):
        if enrich_log(log):
            changed = True

    return changed


def toggle_project_flag(project_name, flag):
    project = st.session_state.projects.get(project_name)
    if not project:
        return
    project[flag] = not bool(project.get(flag, False))
    persist()
    st.rerun()


def end_project(project_name):
    project = st.session_state.projects.get(project_name)
    if not project:
        return
    project["archived"] = True
    project["ended_at"] = str(date.today())
    project["pinned"] = False
    persist()
    st.session_state.selected_project = None
    st.session_state.dashboard_view = "overview"


def reopen_project(project_name):
    project = st.session_state.projects.get(project_name)
    if not project:
        return
    project["archived"] = False
    project["ended_at"] = None
    persist()
    st.rerun()


def project_sort_key(item):
    name, project = item
    return (
        -int(bool(project.get("starred", False))),
        name.lower(),
    )


def render_project_card(name, project, compact=False, historical=False):
    ensure_project_flags(project)

    risk = str(project.get("risk", "LOW")).upper()
    starred = bool(project.get("starred", False))
    pinned = bool(project.get("pinned", False))

    # Display only: the card shows the current language version when one
    # exists and the original name otherwise. The storage key, the widget keys
    # and every action below keep using the unchanged project id ``name``.
    ui_locale = get_current_language()
    display_name = project_display_name(project, ui_locale, fallback=name)

    title_prefix = ""
    if pinned and not historical:
        title_prefix += "📌 "
    if starred:
        title_prefix += "⭐ "

    with st.container(border=True):
        left, mid, actions = st.columns([4, 3, 2])

        with left:
            st.markdown(f"### {title_prefix}{risk_icon(risk)} {display_name}")
            st.caption(
                t(
                    "card.location",
                    city=project.get("city", ""),
                    country=project.get("country", ""),
                    type=project_type_label(
                        project.get("type", "Construction")
                    ),
                )
            )
            if historical:
                st.caption(
                    t(
                        "card.historical_logs",
                        date=project.get("ended_at") or "—",
                        count=len(project.get("daily_logs", [])),
                    )
                )

        with mid:
            st.write(t("card.risk", risk=risk_label(risk)))
            st.write(
                t(
                    "card.workers",
                    workers=(
                        f'{int(project.get("workers", 0) or 0):,}'
                    ),
                )
            )
            st.write(
                t(
                    "card.daily_logs",
                    count=len(project.get("daily_logs", [])),
                )
            )

        with actions:
            if not historical:
                if st.button(
                    t("card.starred") if starred else t("card.star"),
                    key=f"star_{name}",
                    use_container_width=True,
                ):
                    toggle_project_flag(name, "starred")

                if st.button(
                    t("card.pinned") if pinned else t("card.pin"),
                    key=f"pin_{name}",
                    use_container_width=True,
                ):
                    toggle_project_flag(name, "pinned")

                # Compact entry point only: the accurate notice lives on the
                # project page, so a card never carries a long hint.
                if project_needs_translation(project, ui_locale):
                    if st.button(
                        t("card.translate_name"),
                        key=user_text_widget_key(
                            "translate_project_name",
                            name,
                            ui_locale,
                        ),
                        use_container_width=True,
                    ):
                        translate_project_name_action(name, project)

            if st.button(
                t("card.view_history") if historical else t("card.open"),
                key=f"open_{'history' if historical else 'active'}_{name}",
                use_container_width=True,
            ):
                open_project(name)


def historical_projects():
    return {
        name: project
        for name, project in st.session_state.projects.items()
        if bool(project.get("archived", False))
    }


def active_projects():
    return {
        name: project
        for name, project in st.session_state.projects.items()
        if not bool(project.get("archived", False))
    }


# ============================================================
# BASELINE
# ============================================================

def calculate_baseline(
    country, city, project_type, project_scale,
    start_date, end_date, terrain, drainage, rainfall,
    earthquake, heavy_rain, flooding, landslide,
    earthquake_hazard, extreme_heat, strong_wind,
    drought, wildfire, other_hazard
):
    score = 20

    score += {"Moderate": 5, "High": 12, "Extreme": 20}.get(rainfall, 0)
    score += {"Moderate": 5, "Poor": 15}.get(drainage, 0)
    score += {
        "Hilly": 8, "Mountainous": 12,
        "River Valley": 10, "Coastal": 6
    }.get(terrain, 0)
    score += {"Moderate": 8, "High": 15}.get(earthquake, 0)

    for enabled, points in [
        (heavy_rain, 8), (flooding, 12), (landslide, 12),
        (earthquake_hazard, 10), (extreme_heat, 5),
        (strong_wind, 5), (drought, 4), (wildfire, 5),
        (other_hazard, 5)
    ]:
        if enabled:
            score += points

    score = min(score, 100)

    rainfall_level = (
        "HIGH" if rainfall == "Extreme" or heavy_rain
        else "MEDIUM" if rainfall == "High"
        else "LOW"
    )
    flooding_level = (
        "HIGH" if flooding or drainage == "Poor"
        else "MEDIUM" if drainage == "Moderate"
        else "LOW"
    )
    landslide_level = (
        "HIGH"
        if landslide and terrain in ["Hilly", "Mountainous", "River Valley"]
        else "MEDIUM"
        if landslide or terrain in ["Hilly", "Mountainous"]
        else "LOW"
    )
    earthquake_level = (
        "HIGH" if earthquake == "High" or earthquake_hazard
        else "MEDIUM" if earthquake == "Moderate"
        else "LOW"
    )

    risks = {
        "heavy_rainfall": rainfall_level,
        "flooding": flooding_level,
        "landslide": landslide_level,
        "earthquake": earthquake_level,
        "extreme_heat": "MEDIUM" if extreme_heat else "LOW",
        "strong_wind": "MEDIUM" if strong_wind else "LOW",
    }

    chains, impacts, actions = [], [], []

    if heavy_rain or rainfall in ["High", "Extreme"]:
        chains.append(
            "Heavy Rainfall → Increased Surface Runoff → Drainage Pressure → "
            "Site Water Accumulation → Safety + Schedule + Quality Risks"
        )
        impacts.append("Weather-sensitive outdoor work may be interrupted.")
        actions.append("Monitor weather conditions and prepare temporary rain protection.")

    if flooding or drainage == "Poor":
        chains.append(
            "Flooding → Construction Area Inundation → Equipment / Material Damage → "
            "Work Interruption → Schedule Delay"
        )
        impacts.append("Excavation areas may accumulate water.")
        actions.append("Inspect drainage systems and prepare emergency pumping equipment.")

    if landslide:
        chains.append(
            "Heavy Rainfall → Soil Saturation → Slope Instability → Landslide → "
            "Worker + Equipment Safety Risk"
        )
        impacts.append("Excavation and slope stability may require additional monitoring.")
        actions.append("Inspect slopes and excavation edges after heavy rainfall.")

    if earthquake_hazard:
        chains.append(
            "Earthquake → Ground Movement → Structural / Equipment Damage → "
            "Work Interruption → Safety + Schedule Risk"
        )
        impacts.append("Structural and equipment safety may be affected by ground movement.")
        actions.append("Review seismic emergency procedures and structural requirements.")

    if extreme_heat:
        impacts.append("Extreme heat may increase worker fatigue and reduce productivity.")
        actions.append("Plan appropriate rest periods and monitor worker heat exposure.")

    if strong_wind:
        impacts.append("Strong winds may affect lifting operations and temporary structures.")
        actions.append("Establish wind-related suspension criteria for lifting operations.")

    if drought:
        impacts.append("Construction water availability may require additional planning.")
        actions.append("Review construction water availability and emergency supply plans.")

    if wildfire:
        impacts.append("Emergency evacuation and fire response planning may be required.")
        actions.append("Review emergency evacuation and fire response procedures.")

    if not impacts:
        impacts.append(
            "No major environmental construction impact has been identified "
            "from the selected conditions."
        )

    actions.append("Add identified environmental risks to the project risk register.")

    return {
        "score": score,
        "level": level_from_score(score),
        "country": country,
        "city": city.strip(),
        "project_type": project_type,
        "project_scale": project_scale,
        "start_date": str(start_date),
        "end_date": str(end_date),
        "terrain": terrain,
        "drainage": drainage,
        "rainfall": rainfall,
        "earthquake": earthquake,
        "risks": risks,
        "selected_hazards": {
            "heavy_rainfall": heavy_rain,
            "flooding": flooding,
            "landslide": landslide,
            "earthquake": earthquake_hazard,
            "extreme_heat": extreme_heat,
            "strong_wind": strong_wind,
            "drought": drought,
            "wildfire": wildfire,
            "other": other_hazard,
        },
        "risk_chains": chains,
        "impacts": impacts,
        "actions": actions,
    }


# ============================================================
# LOCATION / WEATHER / TERRAIN
# ============================================================

@st.cache_data(ttl=86400, show_spinner=False)
def geocode_location(address, city, country):
    query = (address or "").strip() or f"{city}, {country}"

    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={
                "q": query,
                "format": "json",
                "limit": 1,
                "addressdetails": 1,
            },
            headers={"User-Agent": "RiskPilot Construction Risk Manager/1.0"},
            timeout=15,
        )
        r.raise_for_status()
        results = r.json()

        if not results:
            r = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={
                    "q": f"{city}, {country}",
                    "format": "json",
                    "limit": 1,
                },
                headers={"User-Agent": "RiskPilot Construction Risk Manager/1.0"},
                timeout=15,
            )
            r.raise_for_status()
            results = r.json()

        if not results:
            return None

        result = results[0]
        return {
            "latitude": float(result["lat"]),
            "longitude": float(result["lon"]),
            "display_name": result.get("display_name", query),
        }

    except Exception:
        return None


@st.cache_data(ttl=1800, show_spinner=False)
def get_weather(latitude, longitude):
    try:
        latitude = float(latitude)
        longitude = float(longitude)

        r = requests.get(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "current": (
                    "temperature_2m,"
                    "relative_humidity_2m,"
                    "precipitation,"
                    "weather_code,"
                    "wind_speed_10m"
                ),
                "daily": (
                    "temperature_2m_max,"
                    "temperature_2m_min,"
                    "precipitation_sum,"
                    "weather_code,"
                    "wind_speed_10m_max"
                ),
                "forecast_days": 7,
                "timezone": "auto",
                "wind_speed_unit": "ms",
            },
            headers={
                "User-Agent": "RiskPilot/2.2"
            },
            timeout=30,
        )

        r.raise_for_status()
        data = r.json()

        if not data.get("current") or not data.get("daily"):
            raise ValueError("Weather response is missing current or daily data")

        return data

    except Exception as exc:
        st.error(f"天气数据获取失败：{exc}")
        return None



@st.cache_data(ttl=1800, show_spinner=False)
def get_historical_weather(latitude, longitude, start_day, end_day):
    """Fetch past weather so logs dated before project creation still get a risk."""
    try:
        r = requests.get(
            "https://archive-api.open-meteo.com/v1/archive",
            params={
                "latitude": latitude,
                "longitude": longitude,
                "start_date": str(start_day),
                "end_date": str(end_day),
                "daily": (
                    "temperature_2m_max,temperature_2m_min,"
                    "precipitation_sum,wind_speed_10m_max"
                ),
                "timezone": "auto",
            },
            timeout=25,
        )
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def weather_range_dataframe(latitude, longitude, log_dates=None):
    """Combine historical observations and the current/future forecast."""
    frames = []
    dates = sorted([str(x) for x in (log_dates or []) if x])

    if dates and min(dates) <= str(date.today()):
        historical_end = min(max(dates), str(date.today()))
        if min(dates) <= historical_end:
            historical = get_historical_weather(
                latitude, longitude, min(dates), historical_end
            )
            if historical:
                frames.append(weather_dataframe(historical))

    forecast = get_weather(latitude, longitude)
    if forecast:
        frames.append(weather_dataframe(forecast))

    if not frames:
        return pd.DataFrame(
            columns=[
                "date",
                "Temperature Max (°C)",
                "Temperature Min (°C)",
                "Precipitation (mm)",
                "Wind Max (km/h)",
            ]
        )

    df = pd.concat(frames, ignore_index=True)
    df["date"] = pd.to_datetime(df["date"])
    return (
        df.drop_duplicates(subset=["date"])
        .sort_values("date")
        .reset_index(drop=True)
    )


@st.cache_data(ttl=86400, show_spinner=False)
def get_elevation_grid(latitude, longitude, span=0.04, grid=9):
    try:
        offsets = [
            -span + 2 * span * i / (grid - 1)
            for i in range(grid)
        ]

        lats = [latitude + x for x in offsets]
        lons = [longitude + x for x in offsets]

        lat_list = []
        lon_list = []

        for lat in lats:
            for lon in lons:
                lat_list.append(lat)
                lon_list.append(lon)

        r = requests.get(
            "https://api.open-meteo.com/v1/elevation",
            params={
                "latitude": ",".join(map(str, lat_list)),
                "longitude": ",".join(map(str, lon_list)),
            },
            timeout=20,
        )
        r.raise_for_status()

        elevations = r.json().get("elevation", [])

        if len(elevations) != len(lat_list):
            return None

        rows = []

        for i, lat in enumerate(lats):
            for j, lon in enumerate(lons):
                rows.append({
                    "latitude": lat,
                    "longitude": lon,
                    "elevation": float(elevations[i * grid + j]),
                })

        return pd.DataFrame(rows)

    except Exception:
        return None


def weather_dataframe(weather):
    daily = weather.get("daily", {})

    return pd.DataFrame({
        "date": pd.to_datetime(daily.get("time", [])),
        "Temperature Max (°C)": daily.get("temperature_2m_max", []),
        "Temperature Min (°C)": daily.get("temperature_2m_min", []),
        "Precipitation (mm)": daily.get("precipitation_sum", []),
        "Wind Max (km/h)": daily.get("wind_speed_10m_max", []),
    })


def weather_signal(weather):
    if not weather:
        return 0, "LOW", []

    current = weather.get("current", {})
    daily = weather.get("daily", {})

    current_precip = float(current.get("precipitation") or 0)

    max_precip = max(
        [float(x or 0) for x in daily.get("precipitation_sum", [])] or [0]
    )

    max_wind = max(
        [float(x or 0) for x in daily.get("wind_speed_10m_max", [])] or [0]
    )

    max_temp = max(
        [float(x or 0) for x in daily.get("temperature_2m_max", [])] or [0]
    )

    points = 0
    reasons = []

    if current_precip >= 10 or max_precip >= 20:
        points += 10
        reasons.append("Heavy precipitation")
    elif current_precip >= 2 or max_precip >= 8:
        points += 5
        reasons.append("Elevated precipitation")

    if max_wind >= 50:
        points += 8
        reasons.append("Strong wind")
    elif max_wind >= 30:
        points += 4
        reasons.append("Elevated wind")

    if max_temp >= 35:
        points += 6
        reasons.append("Extreme heat")
    elif max_temp >= 30:
        points += 2
        reasons.append("High temperature")

    level = (
        "HIGH" if points >= 12
        else "MEDIUM" if points >= 5
        else "LOW"
    )

    return points, level, reasons


# ============================================================
# CURRENT RISK + DAILY RISK
# ============================================================

def log_signal(log):
    text = (
        f'{log.get("title", "")} {log.get("description", "")}'
    ).lower()

    points = 0
    reasons = []

    groups = [
        (
            ["accident", "injury", "unsafe", "collapse", "failure", "leak", "fire"],
            12,
            "Safety / incident signal",
        ),
        (
            ["delay", "late", "shortage", "missing", "supply", "delivery"],
            7,
            "Schedule / supply signal",
        ),
        (
            ["rain", "flood", "water", "mud", "storm"],
            6,
            "Weather / water signal",
        ),
        (
            ["crane", "equipment", "machine", "malfunction"],
            8,
            "Equipment signal",
        ),
    ]

    for words, value, label in groups:
        if any(word in text for word in words):
            points += value
            reasons.append(label)

    severity = str(log.get("severity", "LOW")).upper()
    points += {"MEDIUM": 3, "HIGH": 8}.get(severity, 0)

    return points, reasons


def compute_current_risk(project, weather):

    """
    RiskPilot Risk Fusion Engine

    Combines multiple risk agents:

    1. Baseline Risk
    2. Weather Risk
    3. Daily Log Risk
    4. Vision Risk
    5. Financial Risk

    Current version:
    Vision and Financial default to 0
    until their agents are connected.
    """


    # -----------------------------
    # 1. Baseline Risk
    # -----------------------------

    baseline_score = project.get(
        "baseline_score",
        0
    )


    # -----------------------------
    # 2. Weather Risk
    # -----------------------------

    weather_points = 0

    if weather:

        weather_points, _, _ = weather_signal(
            weather
        )


    # Stable, auditable field-condition score: live weather plus attendance.
    current_weather = weather.get("current", {}) if weather else {}
    daily_weather = weather.get("daily", {}) if weather else {}
    daily_rain = daily_weather.get("precipitation_sum", [])
    precipitation_24h = daily_rain[0] if daily_rain else None
    if weather:
        field_assessment = assess_field_conditions(
            temperature_c=current_weather.get("temperature_2m"),
            precipitation_mm_24h=precipitation_24h,
            wind_speed_m_s=current_weather.get("wind_speed_10m"),
            absence=project.get("absence_level", ABSENCE_LEVELS[0]),
        )
        weather_score = field_assessment["score"]
    else:
        field_assessment = {
            "score": 0,
            "level": "UNKNOWN",
            "confidence": 0.0,
            "distribution": {"LOW": 0, "MEDIUM": 0, "HIGH": 0},
            "query": {},
            "evidence": [],
            "method": "nearest_case_retrieval_v1",
            "available": False,
        }
        weather_score = 0


    # -----------------------------
    # 3. Daily Log Risk
    # -----------------------------

    logs = project.get(
        "daily_logs",
        []
    )


    daily_scores = []


    for log in logs[-7:]:

        analysis = log.get(
            "ai_analysis",
            {}
        )

        score = analysis.get(
            "score"
        )


        if score is not None:

            daily_scores.append(
                score
            )


    if daily_scores:

        daily_score = max(
            daily_scores
        )

    else:

        daily_score = 0



    # -----------------------------
    # 4. Vision Risk
    # Future Agent
    # -----------------------------

    visual_resolved = project.get(
        "visual_risk_resolved",
        False
    )
    


    if visual_resolved:

        vision_score = 0


    else:

        visual_penalty = project.get(
            "visual_risk_penalty",
            0
        )

        vision_score = min(
            visual_penalty * 3,
            100
        )

        
    # -----------------------------
    # 5. Financial Risk
    # Future Agent
    # -----------------------------

    financial_score = project.get(
        "financial_risk_score",
        0
    )



    # -----------------------------
    # Fusion
    # -----------------------------

    current_score = (

        baseline_score * 0.25

        +

        weather_score * 0.25

        +

        daily_score * 0.35

        +

        vision_score * 0.10

        +

        financial_score * 0.05

    )


    current_score = round(
        current_score
    )


    level = level_from_score(
        current_score
    )


    return {

        "score": current_score,

        "level": level,


        "components": {

            "Baseline":
                baseline_score,

            "Weather":
                weather_score,

            "Daily Logs":
                daily_score,

            "Vision":
                vision_score,

            "Financial":
                financial_score,

        },


        "weights": {

            "Baseline": "25%",

            "Weather": "25%",

            "Daily Logs": "35%",

            "Vision": "10%",

            "Financial": "5%",

        },

        # Evidence stays outside components to preserve existing report and
        # localization contracts.
        "field_condition_model": field_assessment,

    }


def build_daily_risk(project, weather_df, language=None):
    """
    Build a risk timeline from BOTH weather dates and every log date.

    ``language`` selects which log AI summaries may join the display-only
    ``reasons`` column. It defaults to the current interface language and is
    passed explicitly by the callers. The signal and the score below never
    depend on it, so switching language cannot change a risk value.

    The deterministic reasons ("Heavy rain", "Strong wind",
    "AI log risk: MEDIUM", ...) stay language independent here and are
    translated for display by ``reason_text_label``.
    """
    language = language or get_current_language()

    logs = project.get("daily_logs", [])
    logs_by_date = {}

    for log in logs:
        day = str(log.get("date", ""))
        if day:
            logs_by_date.setdefault(day, []).append(log)

    weather_by_date = {}
    if weather_df is not None and not weather_df.empty:
        for _, row in weather_df.iterrows():
            day = pd.to_datetime(row["date"]).strftime("%Y-%m-%d")
            weather_by_date[day] = row

    all_dates = sorted(set(weather_by_date) | set(logs_by_date))
    baseline = float(project.get("baseline_score", 0))
    rows = []

    for day in all_dates:
        row = weather_by_date.get(day)

        precipitation = (
            float(row.get("Precipitation (mm)", 0) or 0)
            if row is not None else 0
        )
        temperature = (
            float(row.get("Temperature Max (°C)", 0) or 0)
            if row is not None else 0
        )
        wind = (
            float(row.get("Wind Max (km/h)", 0) or 0)
            if row is not None else 0
        )

        signal = 0
        reasons = []

        if precipitation >= 20:
            signal += 10
            reasons.append("Heavy rain")
        elif precipitation >= 8:
            signal += 5
            reasons.append("Rain")

        if wind >= 50:
            signal += 8
            reasons.append("Strong wind")
        elif wind >= 30:
            signal += 4
            reasons.append("Elevated wind")

        if temperature >= 35:
            signal += 6
            reasons.append("Extreme heat")
        elif temperature >= 30:
            signal += 2
            reasons.append("High temperature")

        for log in logs_by_date.get(day, []):
            analysis = log.get("ai_analysis", {})
            ai_score = analysis.get("score")
            ai_level = normalize_ai_level(
                analysis.get("level", log.get("severity", "LOW"))
            )

            if ai_score is not None:
                # AI score is converted into a daily contribution without
                # replacing the project's baseline.
                signal += round(float(ai_score) * 0.22)
            else:
                p, log_reasons = log_signal(log)
                signal += p
                reasons.extend(log_reasons)

            if ai_level != "LOW":
                reasons.append(
                    f'AI log risk: {ai_level}'
                )

            # Only the summary of the current interface language may join
            # the reasons column. When this language has no safe version the
            # other language's summary is dropped entirely instead of being
            # mixed into the table.
            summary = summary_for_reasons(project, log, language)

            if summary:
                reasons.append(summary[:120])

        score = min(100, round(baseline + min(signal, 40)))

        rows.append({
            "date": pd.to_datetime(day),
            "risk": level_from_score(score),
            "risk_score": score,
            "reasons": " · ".join(dict.fromkeys(reasons)) or "Baseline only",
        })

    return pd.DataFrame(rows)



def render_contour_map(elevation_df, location):
    """Render an elevation heat/contour map without matplotlib or numpy."""
    if elevation_df is None or elevation_df.empty:
        return

    pivot = elevation_df.pivot(
        index="latitude",
        columns="longitude",
        values="elevation",
    ).sort_index()

    lats = [float(x) for x in pivot.index.tolist()]
    lons = [float(x) for x in pivot.columns.tolist()]
    grid = [
        [float(v) for v in row]
        for row in pivot.values.tolist()
    ]

    if len(grid) < 2 or len(grid[0]) < 2:
        st.warning(t("dashboard.elevation_warning"))
        return

    width = 900
    height = 480
    pad = 48
    plot_w = width - 2 * pad
    plot_h = height - 2 * pad

    zmin = min(min(row) for row in grid)
    zmax = max(max(row) for row in grid)
    zrange = max(zmax - zmin, 1.0)

    def sx(j):
        return pad + j * plot_w / (len(lons) - 1)

    def sy(i):
        return pad + (len(grid) - 1 - i) * plot_h / (len(grid) - 1)

    def hex_color(value):
        # Neutral professional elevation ramp: low -> high.
        t = max(0.0, min(1.0, (value - zmin) / zrange))
        r = int(236 - 120 * t)
        g = int(244 - 75 * t)
        b = int(238 - 20 * t)
        return f"#{r:02x}{g:02x}{b:02x}"

    svg = [
        f'<svg viewBox="0 0 {width} {height}" '
        'xmlns="http://www.w3.org/2000/svg" '
        'style="width:100%;height:auto;border-radius:12px;'
        'background:#f8fafc;">'
    ]

    # Background elevation cells.
    for i in range(len(grid) - 1):
        for j in range(len(grid[0]) - 1):
            x1, x2 = sx(j), sx(j + 1)
            y1, y2 = sy(i + 1), sy(i)
            avg = (
                grid[i][j]
                + grid[i + 1][j]
                + grid[i][j + 1]
                + grid[i + 1][j + 1]
            ) / 4
            svg.append(
                f'<rect x="{x1:.2f}" y="{y1:.2f}" '
                f'width="{x2-x1:.2f}" height="{y2-y1:.2f}" '
                f'fill="{hex_color(avg)}"/>'
            )

    # Marching-squares style contour segments.
    contour_count = 8
    for level_index in range(1, contour_count + 1):
        level = zmin + zrange * level_index / (contour_count + 1)

        for i in range(len(grid) - 1):
            for j in range(len(grid[0]) - 1):
                a = grid[i][j]
                b = grid[i][j + 1]
                c = grid[i + 1][j + 1]
                d = grid[i + 1][j]

                edges = []

                def interp(v1, v2, x1, y1, x2, y2):
                    if v1 == v2:
                        t = 0.5
                    else:
                        t = (level - v1) / (v2 - v1)
                    t = max(0.0, min(1.0, t))
                    return (
                        x1 + (x2 - x1) * t,
                        y1 + (y2 - y1) * t,
                    )

                # Top
                if (a < level) != (b < level):
                    edges.append(
                        interp(
                            a, b,
                            sx(j), sy(i),
                            sx(j + 1), sy(i),
                        )
                    )

                # Right
                if (b < level) != (c < level):
                    edges.append(
                        interp(
                            b, c,
                            sx(j + 1), sy(i),
                            sx(j + 1), sy(i + 1),
                        )
                    )

                # Bottom
                if (c < level) != (d < level):
                    edges.append(
                        interp(
                            c, d,
                            sx(j + 1), sy(i + 1),
                            sx(j), sy(i + 1),
                        )
                    )

                # Left
                if (d < level) != (a < level):
                    edges.append(
                        interp(
                            d, a,
                            sx(j), sy(i + 1),
                            sx(j), sy(i),
                        )
                    )

                for k in range(0, len(edges) - 1, 2):
                    x1, y1 = edges[k]
                    x2, y2 = edges[k + 1]
                    svg.append(
                        f'<line x1="{x1:.2f}" y1="{y1:.2f}" '
                        f'x2="{x2:.2f}" y2="{y2:.2f}" '
                        'stroke="#64748b" stroke-width="1.4" '
                        'stroke-opacity="0.72"/>'
                    )

    # Project location marker.
    lon = float(location["longitude"])
    lat = float(location["latitude"])

    lon_min, lon_max = min(lons), max(lons)
    lat_min, lat_max = min(lats), max(lats)

    if lon_max != lon_min:
        marker_x = pad + (lon - lon_min) / (lon_max - lon_min) * plot_w
    else:
        marker_x = width / 2

    if lat_max != lat_min:
        marker_y = pad + (lat_max - lat) / (lat_max - lat_min) * plot_h
    else:
        marker_y = height / 2

    svg.append(
        f'<circle cx="{marker_x:.2f}" cy="{marker_y:.2f}" r="7" '
        'fill="#dc2626" stroke="white" stroke-width="3"/>'
    )

    chart_font = "Arial, 'PingFang SC', 'Microsoft YaHei', sans-serif"

    elevation_title = t(
        "dashboard.elevation_map_title",
        min=f"{zmin:.0f}",
        max=f"{zmax:.0f}",
    )
    elevation_low = t("dashboard.elevation_low")
    elevation_high = t("dashboard.elevation_high")

    svg.append(
        f'<text x="{pad}" y="24" font-size="16" '
        f'font-family="{chart_font}" font-weight="600" '
        f'fill="#334155">{elevation_title}</text>'
    )

    svg.append(
        f'<text x="{pad}" y="{height-12}" font-size="12" '
        f'font-family="{chart_font}" fill="#64748b">'
        f'{elevation_low}</text>'
    )
    svg.append(
        f'<text x="{width-pad-95}" y="{height-12}" font-size="12" '
        f'font-family="{chart_font}" fill="#64748b">'
        f'{elevation_high}</text>'
    )

    svg.append("</svg>")

    components.html(
        "".join(svg),
        height=height + 8,
        scrolling=False,
    )

    st.caption(
        t(
            "dashboard.elevation_caption",
            location=location["display_name"],
            min=f"{zmin:.0f}",
            max=f"{zmax:.0f}",
        )
    )


# ============================================================
# COMMON UI
# ============================================================

def open_project(project_name):
    # Project switching only moves the project / view state. It must never
    # touch ui_lang (enforced by utils.ui_language_state.set_active_project).
    set_active_project(st.session_state, project_name)
    st.switch_page(DASHBOARD_PAGE)


def module_card(icon, title, description, key, caption=""):
    with st.container(border=True):
        st.markdown(f"### {icon} {title}")
        st.write(description)

        if caption:
            st.caption(caption)

        if st.button(
            t("dashboard.module_open", title=title),
            key=f"module_{key}",
            use_container_width=True,
        ):
            st.session_state.dashboard_view = key
            st.rerun()

# ============================================================
# RISK REPORT CENTER
# ============================================================

LOCAL_LANGUAGE_MAP = {
    "Kenya": {
        "name": "Swahili",
        "code": "Swahili",
    },
    "Tanzania": {
        "name": "Swahili",
        "code": "Swahili",
    },
    "Uganda": {
        "name": "Swahili",
        "code": "Swahili",
    },
    "Brazil": {
        "name": "Português",
        "code": "Portuguese",
    },
    "Portugal": {
        "name": "Português",
        "code": "Portuguese",
    },
    "France": {
        "name": "Français",
        "code": "French",
    },
    "Germany": {
        "name": "Deutsch",
        "code": "German",
    },
    "Spain": {
        "name": "Español",
        "code": "Spanish",
    },
    "Mexico": {
        "name": "Español",
        "code": "Spanish",
    },
    "Italy": {
        "name": "Italiano",
        "code": "Italian",
    },
    "Japan": {
        "name": "日本語",
        "code": "Japanese",
    },
    "South Korea": {
        "name": "한국어",
        "code": "Korean",
    },
    "China": {
        "name": "中文",
        "code": "Chinese",
    },
    "Taiwan": {
        "name": "中文",
        "code": "Chinese",
    },
    "Malaysia": {
        "name": "Bahasa Melayu",
        "code": "Malay",
    },
    "Indonesia": {
        "name": "Bahasa Indonesia",
        "code": "Indonesian",
    },
    "India": {
        "name": "Hindi",
        "code": "Hindi",
    },
}


def get_local_language(country):
    """Return the recommended local language for a project country."""

    country = str(country or "").strip()

    data = LOCAL_LANGUAGE_MAP.get(country)

    if data:
        return data["name"]

    return "Local Language"


def risk_report_language_options():
    """
    Report language choices: the six aligned locales, in registry order.

    The option value is the locale code itself. It identifies the report
    language in the report identity and resolves the AI language name that
    goes into the prompt. The UI label is only the native display name.
    """

    return list(
        enabled_ui_locales()
    )


def get_report_risk_level(current):
    """Normalize current risk level."""

    level = str(
        current.get("level", "LOW")
    ).upper()

    if level not in ["LOW", "MEDIUM", "HIGH"]:
        level = "LOW"

    return level


def get_report_risk_focus(current):
    """Generate a concise practical risk focus."""

    reasons = current.get("reasons", [])

    if not reasons:
        return "Continue routine site monitoring and follow standard safety procedures."

    cleaned = []

    for reason in reasons:
        text = str(reason).strip()

        if text and text not in cleaned:
            cleaned.append(text)

    return " ".join(cleaned[:3])


def build_risk_report_prompt(
    project,
    current,
    weather,
    audience,
    report_type,
    language,
    project_name="",
):
    """
    Build the AI prompt for the Risk Report Center.

    ``language`` is the AI language name of the report locale, taken from
    the locale registry (for example ``Swahili``), never a locale code.

    ``project_name`` is the report language version of the project name when
    one exists (it never triggers a translation by itself); the original
    name is used when the caller has nothing.

    The AI does not recalculate risk.
    It explains the risk already determined by RiskPilot.
    """

    baseline = project.get(
        "baseline_risk",
        {},
    )

    recent_logs = project.get(
        "daily_logs",
        []
    )[-5:]

    logs_for_ai = []

    for log in recent_logs:

        analysis = log.get(
            "ai_analysis",
            {}
        )

        logs_for_ai.append({
            "date": log.get(
                "date",
                ""
            ),
            "title": log.get(
                "title",
                ""
            ),
            "description": log.get(
                "description",
                ""
            ),
            "risk": analysis.get(
                "level",
                log.get(
                    "severity",
                    "LOW"
                )
            ),
            "score": analysis.get(
                "score",
                0
            ),
        })

    weather_current = {}

    if weather:
        weather_current = weather.get(
            "current",
            {}
        )

    payload = {
        "project": {
            "name": project_name or project.get(
                "name",
                ""
            ),
            "country": project.get(
                "country",
                ""
            ),
            "city": project.get(
                "city",
                ""
            ),
            "type": project.get(
                "type",
                ""
            ),
            "scale": project.get(
                "project_scale",
                ""
            ),
            "workers": project.get(
                "workers",
                0
            ),
        },

        "current_risk": {
            "level": current.get(
                "level",
                "LOW"
            ),
            "score": current.get(
                "score",
                0
            ),
            "baseline_component": current.get(
                "baseline_component",
                0
            ),
            "weather_signal": current.get(
                "weather_signal",
                0
            ),
            "log_signal": current.get(
                "log_signal",
                0
            ),
            "reasons": current.get(
                "reasons",
                []
            ),
        },

        "baseline": {
            "level": baseline.get(
                "level",
                "LOW"
            ),
            "score": baseline.get(
                "score",
                0
            ),
            "terrain": baseline.get(
                "terrain",
                "Unknown"
            ),
            "drainage": baseline.get(
                "drainage",
                "Unknown"
            ),
            "rainfall": baseline.get(
                "rainfall",
                "Unknown"
            ),
            "risks": baseline.get(
                "risks",
                {}
            ),
        },

        "weather": weather_current,

        "recent_logs": logs_for_ai,

        "audience": audience,
        "report_type": report_type,
        "language": language,
    }

    # --------------------------------------------------------
    # Audience-specific instructions
    # --------------------------------------------------------

    if audience == "Worker":

        audience_instruction = """
The report is for construction workers.

Be extremely concise and practical.

The FIRST LINE must clearly state:

HIGH RISK
or
MEDIUM RISK
or
LOW RISK

Immediately after that, state what workers should pay attention to.

Do not discuss technical risk scoring.
Do not write long explanations.
Use short sentences and direct instructions.

The worker should understand the message in a few seconds.
"""

    elif audience == "Manager":

        audience_instruction = """
The report is for a construction project manager.

Explain the current project risk honestly and practically.

Focus on:
1. What is happening.
2. Why the current risk exists.
3. Which factors are contributing.
4. What should be addressed first.
5. What should be monitored next.

Do not exaggerate risks.
Do not hide active problems.
Do not invent information.
"""

    else:

        audience_instruction = """
The report is for the project client.

Write in a sincere, professional and transparent tone.

Clearly explain:
1. The current project risk.
2. What is causing the risk.
3. What the project team is doing about it.
4. What the client should understand about the current situation.

Do not pretend everything is perfect.

The report should end by communicating that:
- the project team will address the identified issues as quickly as possible;
- project progress will continue to be communicated truthfully;
- RiskPilot will continue monitoring the project.

Do not promise that there will be no delays or problems.
"""

    # --------------------------------------------------------
    # Report type
    # --------------------------------------------------------

    if report_type == "Today's Risk Alert":

        report_instruction = """
Generate a short daily risk alert.

Focus only on the most important risks that matter TODAY.

Avoid unnecessary historical information.
"""

    else:

        report_instruction = """
Generate a structured project risk report.

Include the project's current situation, major contributing factors,
recommended actions and the expected focus for the next stage.
"""

    prompt = f"""
You are RiskPilot, an AI construction risk intelligence assistant.

Your job is to generate a risk communication report.

IMPORTANT:

You MUST NOT calculate a new risk score.

You MUST NOT change the current risk level.

LOW, MEDIUM and HIGH are internal RiskPilot risk level values.

Reproduce them exactly as they are.

Never translate them, never reword them and never replace them.

Never state a risk score different from the supplied one.

The current risk level and score supplied below were already calculated
by RiskPilot's risk engine.

Your job is to explain and communicate that existing assessment.

{audience_instruction}

{report_instruction}

The report MUST be written in:
{language}

Only use information contained in the supplied project data.

Do not invent:
- incidents
- weather conditions
- injuries
- delays
- equipment failures
- financial losses
- project outcomes

If the data does not support a statement, do not make that statement.

Project data:

{json.dumps(
    payload,
    ensure_ascii=False,
    indent=2
)}

Return JSON only.

Use exactly this structure:

{{
    "title": "Report title",
    "risk_level": "HIGH/MEDIUM/LOW",
    "opening": "Opening statement",
    "risk_summary": "Main explanation of current risk",
    "key_points": [
        "Key point 1",
        "Key point 2",
        "Key point 3"
    ],
    "actions": [
        "Recommended action 1",
        "Recommended action 2"
    ],
    "closing": "Closing statement"
}}


"""

    return prompt


def ai_generate_risk_report(
    project,
    current,
    weather,
    audience,
    report_type,
    language,
    project_id="",
):
    """
    Generate a RiskPilot AI risk report.

    ``language`` is a report locale code. The prompt receives the AI
    language name of that locale, and the returned report carries the
    identity it was generated for.
    """

    identity = risk_report_identity(
        project_id=project_id,
        report_locale=language,
        audience=audience,
        report_type=report_type,
    )

    ai_language = report_contract.report_locale_ai_name(
        identity["report_locale"]
    )

    if (
        ai_language is None
        or identity["audience"] is None
        or identity["report_type"] is None
    ):

        # Unsupported selection: the AI is never called for it.
        return {
            "success": False,
            "error": "Unsupported report selection.",
            "title": "RiskPilot Risk Report",
            "risk_level": get_report_risk_level(
                current
            ),
            "opening": "",
            "risk_summary": "",
            "key_points": [],
            "actions": [],
            "closing": "",
            "audience": audience,
            "report_type": report_type,
            "language": language,
            "identity": identity,
        }

    try:

        prompt = build_risk_report_prompt(
            project=project,
            current=current,
            weather=weather,
            audience=audience,
            report_type=report_type,
            language=ai_language,
            # The report language version of the name wins, the original name
            # is the fallback (the project id is the original name when the
            # record does not carry a separate one). Reading it never triggers
            # a translation.
            project_name=project_display_name(
                project,
                identity["report_locale"],
                fallback=str(project.get("name") or project_id or ""),
            ),
        )

        from agents.risk_agent import RiskAgent

        result = RiskAgent().generate_report(prompt)

        if not isinstance(result, dict):
            raise ValueError(
                "AI returned an invalid response."
            )

        return {
            "success": True,
            "title": str(
                result.get(
                    "title",
                    "RiskPilot Risk Report"
                )
            ).strip(),

            "risk_level": get_report_risk_level(
                current
            ),

            "opening": str(
                result.get(
                    "opening",
                    ""
                )
            ).strip(),

            "risk_summary": str(
                result.get(
                    "risk_summary",
                    ""
                )
            ).strip(),

            "key_points": (
                result.get(
                    "key_points",
                    []
                )
                if isinstance(
                    result.get(
                        "key_points",
                        []
                    ),
                    list
                )
                else []
            ),

            "actions": (
                result.get(
                    "actions",
                    []
                )
                if isinstance(
                    result.get(
                        "actions",
                        []
                    ),
                    list
                )
                else []
            ),

            "closing": str(
                result.get(
                    "closing",
                    ""
                )
            ).strip(),

            "audience": audience,
            "report_type": report_type,
            "language": language,
            "identity": identity,
        }

    except Exception as exc:

        return {
            "success": False,
            "error": str(exc),
            "title": "RiskPilot Risk Report",
            "risk_level": get_report_risk_level(
                current
            ),
            "opening": "",
            "risk_summary": "",
            "key_points": [],
            "actions": [],
            "closing": "",
            "audience": audience,
            "report_type": report_type,
            "language": language,
            "identity": identity,
        }


#: Full-width punctuation that already separates a label from its value, so
#: no extra space is inserted after it (``项目类型：住房建造``).
_LABEL_TIGHT_ENDINGS = "：，。、；！？）》】"


def label_value_separator(label) -> str:
    """
    Separator between a fixed label and its value.

    Locale labels keep their own typography: French writes ``Projet :`` and
    Chinese ``项目类型：``. A space is added only when the label does not
    already provide one, so ``Projet :`` + ``x`` never renders as
    ``Projet :x`` and ``项目类型：`` never renders as ``项目类型： x``.
    """

    text = "" if label is None else str(label)

    if not text:

        return ""

    if text[-1].isspace():

        return ""

    if text[-1] in _LABEL_TIGHT_ENDINGS:

        return ""

    return " "


#: Fixed labels of the generated report. They are deterministic text (never
#: user data) and are resolved with ``explicit_label`` against the report
#: language, so an exported report never mixes in another language's label.
_REPORT_LABEL_KEYS = {
    "docx_title": "report.docx.title",
    "docx_project": "report.docx.project",
    "docx_project_type": "report.docx.project_type",
    "docx_audience": "report.docx.audience",
    "docx_report_type": "report.docx.report_type",
    "docx_language": "report.docx.language",
    "docx_current_risk": "report.docx.current_risk",
    "docx_generated": "report.docx.generated",
    "section_risk_summary": "report.section.risk_summary",
    "section_key_points": "report.section.key_points",
    "section_actions": "report.section.actions",
}


def risk_report_to_text(report, report_locale=None):
    """
    Convert report JSON into editable plain text.

    Section headings are deterministic labels, so they follow the report
    language (``report_locale``); ``None`` keeps the interface language.
    """

    sections = []

    title = str(
        report.get(
            "title"
        ) or ""
    ).strip()

    if not title:

        title = explicit_label(
            _REPORT_LABEL_KEYS["docx_title"],
            report_locale
        )

    sections.append(title)

    sections.append("")

    opening = report.get(
        "opening",
        ""
    )

    if opening:
        sections.append(
            opening
        )
        sections.append("")

    risk_summary = report.get(
        "risk_summary",
        ""
    )

    if risk_summary:

        sections.append(
            explicit_label(
                _REPORT_LABEL_KEYS["section_risk_summary"],
                report_locale
            )
        )

        sections.append(
            risk_summary
        )

        sections.append("")

    key_points = report.get(
        "key_points",
        []
    )

    if key_points:

        sections.append(
            explicit_label(
                _REPORT_LABEL_KEYS["section_key_points"],
                report_locale
            )
        )

        for point in key_points:

            text = str(
                point
            ).strip()

            if text:
                sections.append(
                    f"• {text}"
                )

        sections.append("")

    actions = report.get(
        "actions",
        []
    )

    if actions:

        sections.append(
            explicit_label(
                _REPORT_LABEL_KEYS["section_actions"],
                report_locale
            )
        )

        for action in actions:

            text = str(
                action
            ).strip()

            if text:
                sections.append(
                    f"• {text}"
                )

        sections.append("")

    closing = report.get(
        "closing",
        ""
    )

    if closing:

        sections.append(
            closing
        )

    return "\n".join(
        sections
    ).strip()


def export_report_to_word(
    report_text,
    project,
    current,
    audience,
    report_type,
    language,
    report_locale=None,
    project_id="",
):
    """
    Create an editable .docx risk report.

    Every fixed label (document title, metadata names, section headings and
    deterministic enumerations such as audience / report type / project
    type) is rendered in the report language via ``explicit_label``, and
    each metadata line is written as ``label`` + :func:`label_value_separator`
    + value. The canonical risk level / score stay exactly as stored, and the
    language field names the report language actually used.

    The project name is the report language version when one exists;
    otherwise it is the original project name (``project_id``), used
    unchanged. Exporting a report never translates anything.
    """

    document = Document()

    heading_labels = [
        explicit_label(
            "report.section.risk_summary",
            report_locale
        ),
        explicit_label(
            "report.section.key_points",
            report_locale
        ),
        explicit_label(
            "report.section.actions",
            report_locale
        ),
    ]

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    title = document.add_paragraph()

    title.alignment = WD_ALIGN_PARAGRAPH.CENTER

    run = title.add_run(
        explicit_label(
            _REPORT_LABEL_KEYS["docx_title"],
            report_locale
        )
    )

    run.bold = True
    run.font.size = Pt(20)

    # --------------------------------------------------------
    # Metadata
    # --------------------------------------------------------

    metadata = document.add_paragraph()

    def add_field(label, value, first=False):
        """One metadata line: bold localized label + its value."""

        metadata.add_run(
            ("" if first else "\n") + label
        ).bold = True

        metadata.add_run(
            f"{label_value_separator(label)}{value}"
        )

    # The language field always names the report language actually used:
    # the caller's label is only a fallback for a missing/unknown locale.
    locale_info = report_contract.normalize_report_locale(
        report_locale
    )

    language_value = (
        _report_language_display(
            locale_info["locale"]
        )
        if locale_info["valid"]
        else str(language or "")
    )

    # The report language version of the project name when it exists, the
    # original project name otherwise (the project id IS the original name
    # when the record does not carry a separate one). Nothing is translated
    # while exporting.
    original_project_name = str(
        project.get(
            "name",
            ""
        ) or project_id or ""
    )

    project_name_value = (
        project_display_name(
            project,
            locale_info["locale"],
            fallback=original_project_name,
        )
        if locale_info["valid"]
        else original_project_name
    )

    add_field(
        explicit_label(
            _REPORT_LABEL_KEYS["docx_project"],
            report_locale
        ),
        project_name_value,
        first=True,
    )

    add_field(
        explicit_label(
            _REPORT_LABEL_KEYS["docx_project_type"],
            report_locale
        ),
        project_type_label(
            project.get(
                "type",
                ""
            ),
            report_locale
        ),
    )

    add_field(
        explicit_label(
            _REPORT_LABEL_KEYS["docx_audience"],
            report_locale
        ),
        report_audience_label(
            audience,
            report_locale
        ),
    )

    add_field(
        explicit_label(
            _REPORT_LABEL_KEYS["docx_report_type"],
            report_locale
        ),
        report_type_label(
            report_type,
            report_locale
        ),
    )

    add_field(
        explicit_label(
            _REPORT_LABEL_KEYS["docx_language"],
            report_locale
        ),
        language_value,
    )

    add_field(
        explicit_label(
            _REPORT_LABEL_KEYS["docx_current_risk"],
            report_locale
        ),
        risk_report_line(
            current,
            report_locale
        ),
    )

    add_field(
        explicit_label(
            _REPORT_LABEL_KEYS["docx_generated"],
            report_locale
        ),
        datetime.now().strftime(
            "%Y-%m-%d %H:%M"
        ),
    )

    document.add_paragraph()

    # --------------------------------------------------------
    # Editable report content
    # --------------------------------------------------------

    for line in str(
        report_text
    ).splitlines():

        stripped = line.strip()

        if not stripped:
            document.add_paragraph()
            continue

        if stripped.startswith("• "):

            paragraph = document.add_paragraph(
                style="List Bullet"
            )

            paragraph.add_run(
                stripped[2:]
            )

        elif stripped in heading_labels:

            heading = document.add_paragraph()

            run = heading.add_run(
                stripped
            )

            run.bold = True
            run.font.size = Pt(14)

        else:

            paragraph = document.add_paragraph()

            paragraph.add_run(
                stripped
            )

    output = BytesIO()

    document.save(
        output
    )

    output.seek(0)

    return output.getvalue()


def _report_language_display(locale_code) -> str:
    """
    Native display name of a report language (for display only).

    The option value itself is never translated: it stays the locale code,
    because that code identifies the report and resolves the AI language
    name used in the prompt.
    """

    return locale_display_name(locale_code)


def report_audience_label(value, language=None) -> str:
    """
    Report audience in a locale (``None`` -> interface language).

    Accepts the stable internal id (``worker``) as well as the legacy
    display value (``Worker``); anything unrecognised stays raw.
    """

    audience = report_contract.normalize_report_audience(
        value
    )["audience"]

    if audience == "Worker":

       audience_instruction = """
AUDIENCE MODE: WORKER SAFETY NOTICE

This must look completely different from a client report.

Requirements:
- Maximum 120 words.
- Use simple, direct language.
- Do not mention risk scores, data sources, project strategy or management.
- Do not explain the entire project.
- Focus only on hazards workers may face and what they must do now.
- Use short commands.
- The opening must begin with the supplied risk level.
- key_points must contain visible hazards or precautions.
- actions must contain immediate worker actions.
- closing must be one short safety reminder.
"""

    elif audience == "Manager":

         audience_instruction = """
AUDIENCE MODE: PROJECT MANAGER REPORT

Requirements:
- Use an operational and analytical tone.
- Explain contributing factors and priorities.
- Include monitoring, coordination and mitigation actions.
- Keep the supplied risk level and score unchanged.
- Do not invent incidents or project conditions.
"""

    else:

        audience_instruction = """
AUDIENCE MODE: CLIENT UPDATE

This must look completely different from a worker notice.

Requirements:
- Write 250 to 400 words.
- Use a professional, transparent and reassuring tone.
- Explain the overall project situation and its possible effect on delivery.
- Explain what the project team is doing to control the identified risks.
- Do not give workers direct commands.
- Do not overload the client with detailed site-safety instructions.
- key_points must describe project-level concerns.
- actions must describe actions being taken by the project team.
- closing must confirm continued monitoring and truthful progress reporting.
- Do not guarantee that delays or problems cannot occur.
"""

    return "" if value is None else str(value)


def report_type_label(value, language=None) -> str:
    """
    Report type in a locale (``None`` -> interface language).

    Accepts the stable internal id (``risk_report``) as well as the legacy
    display value (``Risk Report``); anything unrecognised stays raw.
    """

    report_type = report_contract.normalize_report_type(
        value
    )["report_type"]

    if report_type == "daily_risk_alert":

        return explicit_label(
            "report.type.alert",
            language
        )

    if report_type == "risk_report":

        return explicit_label(
            "report.type.report",
            language
        )

    return "" if value is None else str(value)


def report_risk_level_label(level, language=None) -> str:
    """
    Display-only label of an internal risk level in a locale.

    The internal value (``LOW`` / ``MEDIUM`` / ``HIGH``) is never changed:
    unknown levels are returned raw, and when the locale has no label the
    canonical token is returned unchanged.
    """

    raw = "" if level is None else str(level).strip()

    if not raw:

        return raw

    token = raw.upper()

    if token == "HIGH":

        key = "risk.level.high"

    elif token == "MEDIUM":

        key = "risk.level.medium"

    elif token == "LOW":

        key = "risk.level.low"

    else:

        return raw

    text = explicit_label(
        key,
        language
    )

    if text == key:

        return raw

    return text


def risk_report_line(current, language=None) -> str:
    """
    Risk line of the report, displayed in the report language.

    The internal level (``LOW`` / ``MEDIUM`` / ``HIGH``) and the score stay
    the canonical values the AI can never rewrite; only the rendered level
    name follows the report language (``HIGH`` -> ``高风险``). A level with
    no label in that language keeps the canonical token, so the line is
    never empty and never leaks a translation key.
    """

    level = str(
        (current or {}).get(
            "level",
            "LOW"
        )
    ).strip()

    score = (current or {}).get(
        "score",
        0
    )

    display_level = report_risk_level_label(
        level,
        language
    ) or level

    return f"{risk_icon(level)} {display_level} · {score}/100"


def risk_report_identity(
    project_id,
    report_locale,
    audience,
    report_type,
):
    """
    Cache identity of a generated report.

    A generated report belongs to exactly one project, report language,
    audience and report type. Every value is normalized to its stable
    internal form, so the same selection always yields the same identity
    and a different selection can never reuse the report on screen.
    """

    locale_info = report_contract.normalize_report_locale(
        report_locale
    )

    audience_info = report_contract.normalize_report_audience(
        audience
    )

    type_info = report_contract.normalize_report_type(
        report_type
    )

    return {
        "project_id": str(project_id or ""),
        "report_locale": locale_info["locale"],
        "audience": audience_info["audience"],
        "report_type": type_info["report_type"],
    }


#: Session keys that hold the report currently shown on screen.
#: They are display state only: project data is never touched.
RISK_REPORT_SESSION_KEYS = (
    "risk_report_result",
    "risk_report_editor",
    "risk_report_editor_box",
    "risk_report_language_used",
    "risk_report_identity",
)


def clear_risk_report_state():
    """Drop the report currently displayed in the Risk Report Center."""

    for key in RISK_REPORT_SESSION_KEYS:

        st.session_state.pop(
            key,
            None,
        )


def has_ai_api_key() -> bool:
    """
    True when an AI API key is configured.

    Only the presence of the key is checked: its value is never read
    into the interface, logged or displayed.
    """

    import os

    try:

        from dotenv import load_dotenv

        load_dotenv()

    except Exception:

        pass

    return bool(
        os.getenv("DEEPSEEK_API_KEY")
    )


def render_risk_report_center(
    project,
    current,
    weather,
    project_id="",
):
    """
    Risk Report Center UI.

    The report content language ("risk_report_language") is fully
    independent from the interface language ("ui_lang"): it is a locale
    code, and nothing here writes ui_lang.

    A generated report is bound to the identity it was generated for
    (project, report language, audience, report type). The AI is called
    only from the generate button.
    """

    st.markdown("---")

    st.subheader(t("report.title"))

    st.caption(t("report.caption"))

    # ========================================================
    # REPORT CONTROLS
    # ========================================================

    c1, c2 = st.columns(2)

    with c1:

        audience = st.selectbox(
            t("report.audience_label"),
            [
                "Worker",
                "Manager",
                "Client",
            ],
            format_func=report_audience_label,
            key="risk_report_audience",
        )

    with c2:

        report_type = st.selectbox(
            t("report.type_label"),
            [
                "Today's Risk Alert",
                "Risk Report",
            ],
            format_func=report_type_label,
            key="risk_report_type",
        )

    # --------------------------------------------------------
    # Report language (independent from the interface language)
    # --------------------------------------------------------

    country = project.get(
        "country",
        ""
    )

    try:

        language_options = risk_report_language_options()

    except Exception:

        language_options = []

    if not language_options:

        language_options = list(
            enabled_ui_locales()
        )

    recommended_language = get_local_language(
        country
    )

    # The recommendation is a display value ("Kiswahili"); the option list
    # holds locale codes. Unsupported recommendations simply fall back to
    # the first option instead of being mapped to another language.
    recommended_locale = report_contract.normalize_report_locale(
        recommended_language
    )["locale"]

    # A leftover session value that is no longer part of the options
    # (for example after switching to a project in another country)
    # would make st.selectbox raise and hide the whole report center.
    stored_language = st.session_state.get(
        "risk_report_language"
    )

    if (
        stored_language is not None
        and stored_language not in language_options
    ):

        st.session_state.pop(
            "risk_report_language",
            None,
        )

    if recommended_locale in language_options:

        default_language_index = (
            language_options.index(
                recommended_locale
            )
        )

    else:

        default_language_index = 0

    report_language = st.selectbox(
        t("report.language_label"),
        language_options,
        index=default_language_index,
        format_func=_report_language_display,
        key="risk_report_language",
        help=t("report.language_help"),
    )

    st.caption(
        t(
            "report.language_recommended",
            recommended=recommended_language,
        )
    )

    # --------------------------------------------------------
    # A generated report belongs to one project, report
    # language, audience and report type. As soon as the user
    # selects something else, the report on screen is stale: it
    # is dropped, its download button disappears and the user has
    # to generate a new report.
    # --------------------------------------------------------

    identity = risk_report_identity(
        project_id=project_id,
        report_locale=report_language,
        audience=audience,
        report_type=report_type,
    )

    generated_identity = st.session_state.get(
        "risk_report_identity"
    )

    if (
        generated_identity is not None
        and generated_identity != identity
    ):

        clear_risk_report_state()

        if generated_identity.get(
            "report_locale"
        ) != identity["report_locale"]:

            st.info(
                t(
                    "report.status_cleared",
                    language=_report_language_display(
                        report_language
                    ),
                )
            )

        else:

            st.info(
                t("report.status_stale")
            )

    st.caption(
        t(
            "report.status_language",
            language=_report_language_display(
                report_language
            ),
        )
    )

    # ========================================================
    # GENERATE
    # ========================================================

    is_generating = st.session_state.get(
        "risk_report_generating",
        False,
    )

    if st.button(
        t("report.button_generate"),
        type="primary",
        use_container_width=True,
        disabled=is_generating,
        key="risk_report_generate_button",
    ):

        st.session_state[
            "risk_report_generating"
        ] = True

        try:

            with st.spinner(
                t("report.spinner_generating")
            ):

                report = ai_generate_risk_report(
                    project=project,
                    current=current,
                    weather=weather,
                    audience=audience,
                    report_type=report_type,
                    language=report_language,
                    project_id=identity["project_id"],
                )

            st.session_state[
                "risk_report_result"
            ] = report

            st.session_state[
                "risk_report_language_used"
            ] = report_language

            # The report is valid for exactly this selection and for
            # nothing else.
            st.session_state[
                "risk_report_identity"
            ] = identity

            st.session_state[
                "risk_report_editor"
            ] = risk_report_to_text(
                report,
                report_language
            )

            # The editor widget owns its own state, so the new text
            # must be written to the widget key itself. Otherwise the
            # editor keeps showing the previous report.
            st.session_state[
                "risk_report_editor_box"
            ] = st.session_state[
                "risk_report_editor"
            ]

        finally:

            st.session_state[
                "risk_report_generating"
            ] = False

    # ========================================================
    # REPORT EDITOR
    # ========================================================

    report = st.session_state.get(
        "risk_report_result"
    )

    if report:

        if not report.get(
            "success",
            False,
        ):

            st.error(
                t("report.error_generation_failed")
            )

            error_message = str(
                report.get(
                    "error",
                    ""
                )
            ).strip()

            if error_message:

                st.code(
                    error_message,
                    language="text",
                )

            st.caption(
                t(
                    "report.error_hint",
                    language=_report_language_display(
                        report_language
                    ),
                )
            )

            if not has_ai_api_key():

                st.warning(
                    t("report.warning_no_api_key")
                )

            return

        st.markdown("---")

        st.success(
            t(
                "report.status_generated",
                language=_report_language_display(
                    report_language
                ),
            )
        )

        st.markdown(
            t("report.editor_header")
        )

        st.caption(
            t("report.editor_caption")
        )

        if "risk_report_editor_box" not in st.session_state:

            st.session_state[
                "risk_report_editor_box"
            ] = st.session_state.get(
                "risk_report_editor",
                risk_report_to_text(
                    report,
                    report_language
                ),
            )

        edited_report = st.text_area(
            t("report.editor_label"),
            height=500,
            key="risk_report_editor_box",
            label_visibility="collapsed",
        )

        st.session_state[
            "risk_report_editor"
        ] = edited_report

        # ====================================================
        # EXPORT
        # ====================================================

        st.markdown("---")

        st.markdown(
            t("report.export_header")
        )

        word_bytes = export_report_to_word(
            report_text=edited_report,
            project=project,
            current=current,
            audience=audience,
            report_type=report_type,
            language=_report_language_display(
                report_language
            ),
            report_locale=report_language,
            project_id=project_id,
        )

        safe_project_name = (
            str(
                project.get(
                    "name",
                    "RiskPilot_Project"
                )
            )
            .replace(
                "/",
                "-"
            )
            .replace(
                "\\",
                "-"
            )
        )

        filename = (
            f"RiskPilot_{safe_project_name}_"
            f"{report_type.replace(' ', '_')}.docx"
        )

        st.download_button(
            t("report.button_download"),
            data=word_bytes,
            file_name=filename,
            mime=(
                "application/vnd.openxmlformats-"
                "officedocument.wordprocessingml.document"
            ),
            use_container_width=True,
        )

# ============================================================
# APP PAGE
# ============================================================

def app_page():
    """Global portfolio homepage. Only active projects live here."""

    projects = active_projects()

    changed = False

    for project in st.session_state.projects.values():
        if ensure_project_flags(project):
            changed = True

    if changed:
        persist()

    # ========================================================
    # PAGE HEADER
    # ========================================================

    st.title(t("app.title"))
    st.caption(t("app.caption"))

    if st.button(
        t("app.button_new_project"),
        type="primary",
        use_container_width=True,
    ):
        st.switch_page(NEW_PROJECT_PAGE)

    # ========================================================
    # PORTFOLIO METRICS
    # ========================================================

    countries_count = len({
        p.get("country", "Unknown")
        for p in projects.values()
    })

    projects_at_risk = sum(
        str(p.get("risk", "LOW")).upper()
        in ["MEDIUM", "HIGH"]
        for p in projects.values()
    )

    total_workers = sum(
        int(p.get("workers", 0) or 0)
        for p in projects.values()
    )

    c1, c2, c3, c4 = st.columns(4)

    c1.metric(
        t("app.metric_countries"),
        countries_count,
    )

    c2.metric(
        t("app.metric_active_projects"),
        len(projects),
    )

    c3.metric(
        t("app.metric_to_monitor"),
        projects_at_risk,
    )

    c4.metric(
        t("app.metric_workers"),
        f"{total_workers:,}",
    )

    # ========================================================
    # GLOBAL PROJECT MAP
    # ========================================================
        # ========================================================
    # GLOBAL PROJECT MAP
    # One marker per location.
    # Marker color = highest risk at that location.
    # Hover = all projects at that location.
    # ========================================================

    st.markdown("---")
    st.header(t("app.header_map"))
    st.caption(t("app.map_caption"))

    # --------------------------------------------------------
    # Risk ranking
    # --------------------------------------------------------

    risk_priority = {
        "LOW": 1,
        "MEDIUM": 2,
        "HIGH": 3,
    }

    risk_colors = {
        "HIGH": [220, 50, 47, 235],
        "MEDIUM": [245, 158, 11, 235],
        "LOW": [34, 197, 94, 235],
    }

    risk_radii = {
        "HIGH": 26000,
        "MEDIUM": 22000,
        "LOW": 18000,
    }

    # --------------------------------------------------------
    # First collect all projects by city + country
    # --------------------------------------------------------

    location_groups = {}

    for project_name, project in projects.items():

        location = geocode_location(
            project.get("address", ""),
            project.get("city", ""),
            project.get("country", ""),
        )

        if not location:
            continue

        # Calculate live risk
        live_weather = get_weather(
            location["latitude"],
            location["longitude"],
        )

        live_current = compute_current_risk(
            project,
            live_weather,
        )

        live_risk = str(
            live_current.get("level", "LOW")
        ).upper()

        # Keep project risk synchronized
        if project.get("risk") != live_risk.title():

            project["risk"] = live_risk.title()
            project["current_risk_score"] = (
                live_current.get("score", 0)
            )
            project["current_risk_updated_at"] = (
                live_current.get("updated_at", "")
            )

        city = str(
            project.get("city", "")
        ).strip()

        country = str(
            project.get("country", "")
        ).strip()

        # Same city + same country = same map location
        location_key = (
            city.lower(),
            country.lower(),
        )

        if location_key not in location_groups:

            location_groups[location_key] = {
                "city": city,
                "country": country,
                "latitude": float(
                    location["latitude"]
                ),
                "longitude": float(
                    location["longitude"]
                ),
                "projects": [],
            }

        location_groups[location_key]["projects"].append({
            "name": project_name,
            "display_name": project_display_name(
                project,
                get_current_language(),
                fallback=project_name,
            ),
            "risk": live_risk,
            "score": live_current.get(
                "score",
                project.get(
                    "baseline_score",
                    0,
                ),
            ),
            "workers": int(
                project.get(
                    "workers",
                    0,
                ) or 0
            ),
            "logs": len(
                project.get(
                    "daily_logs",
                    [],
                )
            ),
        })

    # --------------------------------------------------------
    # Build ONE marker per location
    # --------------------------------------------------------

    map_rows = []

    for location_data in location_groups.values():

        location_projects = location_data["projects"]

        if not location_projects:
            continue

        # Highest risk determines marker
        highest_project = max(
            location_projects,
            key=lambda item: risk_priority.get(
                item["risk"],
                1,
            ),
        )

        highest_risk = highest_project["risk"]

        # Sort projects so HIGH appears first,
        # then MEDIUM, then LOW.
        sorted_projects = sorted(
            location_projects,
            key=lambda item: (
                -risk_priority.get(
                    item["risk"],
                    1,
                ),
                item["name"].lower(),
            ),
        )

        # ----------------------------------------------------
        # Build project summary
        # ----------------------------------------------------

        project_names = []

        for item in sorted_projects:
            project_names.append(
                f'{item.get("display_name") or item["name"]} '
                f'({risk_label(item["risk"])})'
            )

        project_list = " | ".join(project_names)

        # ----------------------------------------------------
        # Add location to map
        # ----------------------------------------------------

        map_rows.append({
            "city": location_data["city"],
            "country": location_data["country"],
            "latitude": location_data["latitude"],
            "longitude": location_data["longitude"],
            "risk": highest_risk,
            "risk_display": risk_label(highest_risk),
            "project_count": len(location_projects),
            "project_list": project_list,
            "radius": risk_radii.get(
                highest_risk,
                18000,
            ),
            "color": risk_colors.get(
                highest_risk,
                [100, 116, 139, 220],
            ),
        })
    # --------------------------------------------------------
    # Draw map
    # --------------------------------------------------------

    if map_rows:

        map_df = pd.DataFrame(
            map_rows
        )

        scatter = pdk.Layer(
            "ScatterplotLayer",
            data=map_df,
            get_position=[
                "longitude",
                "latitude",
            ],
            get_radius="radius",
            radius_min_pixels=8,
            radius_max_pixels=30,
            get_fill_color="color",
            get_line_color=[
                255,
                255,
                255,
                255,
            ],
            line_width_min_pixels=2,
            opacity=0.90,
            stroked=True,
            filled=True,
            pickable=True,
            auto_highlight=True,
        )

        labels = pdk.Layer(
            "TextLayer",
            data=map_df,
            get_position=[
                "longitude",
                "latitude",
            ],
            get_text="city",
            get_size=13,
            get_color=[
                30,
                30,
                30,
                255,
            ],
            get_text_anchor="middle",
            get_alignment_baseline="bottom",
            get_pixel_offset=[
                0,
                -18,
            ],
            pickable=False,
        )

        deck = pdk.Deck(
            layers=[
                scatter,
                labels,
            ],
            initial_view_state=pdk.ViewState(
                latitude=20,
                longitude=0,
                zoom=1.1,
            ),
            tooltip={
                "text": t("app.map_tooltip"),
                "style": {
                    "backgroundColor": "white",
                    "color": "#111827",
                    "fontSize": "14px",
                },
            },
        )

        st.pydeck_chart(
            deck,
            use_container_width=True,
        )

        l1, l2, l3 = st.columns(3)

        l1.markdown(
            t("app.legend_high")
        )

        l2.markdown(
            t("app.legend_medium")
        )

        l3.markdown(
            t("app.legend_low")
        )

    else:

        st.info(
            t("app.info_no_locations")
        )


    # ========================================================
    # PORTFOLIO
    # ========================================================
    st.markdown("---")
    st.header(t("app.header_active"))
    st.caption(t("app.active_caption"))

    pinned = [
        item for item in projects.items()
        if bool(item[1].get("pinned", False))
    ]
    pinned.sort(key=project_sort_key)

    if pinned:
        st.subheader(t("app.subheader_pinned"))
        for i in range(0, len(pinned), 2):
            cols = st.columns(min(2, len(pinned[i:i+2])))
            for col, item in zip(cols, pinned[i:i+2]):
                with col:
                    render_project_card(item[0], item[1], compact=True)

    grouped = {continent: [] for continent in CONTINENT_ORDER}
    for item in projects.items():
        if bool(item[1].get("pinned", False)):
            continue
        grouped.setdefault(
            get_continent(item[1].get("country", "")), []
        ).append(item)

    for continent in CONTINENT_ORDER:
        items = grouped.get(continent, [])
        if not items:
            continue

        items.sort(key=project_sort_key)
        st.subheader(
            f'{CONTINENT_ICONS.get(continent, "🗺️")} '
            f'{continent_label(continent)}'
        )

        for i in range(0, len(items), 2):
            row = items[i:i+2]
            cols = st.columns(len(row))
            for col, item in zip(cols, row):
                with col:
                    render_project_card(item[0], item[1])

    if not projects:
        st.info(t("app.info_no_active"))
# ============================================================
# HISTORY PROJECTS PAGE
# ============================================================

def history_page():
    projects = historical_projects()

    st.title(t("history.title"))
    st.caption(t("history.caption"))

    if not projects:
        st.info(t("history.empty"))
        return

    grouped = {continent: [] for continent in CONTINENT_ORDER}
    for item in projects.items():
        grouped.setdefault(
            get_continent(item[1].get("country", "")), []
        ).append(item)

    st.metric(t("history.metric_completed"), len(projects))
    st.markdown("---")

    for continent in CONTINENT_ORDER:
        items = grouped.get(continent, [])
        if not items:
            continue

        items.sort(key=lambda x: (
            str(x[1].get("ended_at", "")),
            x[0].lower()
        ), reverse=True)

        st.subheader(
            f'{CONTINENT_ICONS.get(continent, "🗺️")} '
            f'{continent_label(continent)}'
        )

        for i in range(0, len(items), 2):
            row = items[i:i+2]
            cols = st.columns(len(row))
            for col, item in zip(cols, row):
                with col:
                    render_project_card(
                        item[0], item[1], historical=True
                    )


# ============================================================
# NEW PROJECT PAGE
# ============================================================

def new_project_page():

    projects = st.session_state.projects

    st.title(t("new.title"))

    st.caption(t("new.caption"))

    with st.container(border=True):

        st.subheader(t("new.subheader_information"))

        c1, c2 = st.columns(2)

        with c1:

            country = st.selectbox(
                t("new.field_country"),
                COUNTRIES,
                index=None,
                placeholder=t("new.placeholder_country"),
            )

            city = st.text_input(
                t("new.field_city"),
                placeholder=t("new.placeholder_city"),
            )

            address = st.text_input(
                t("new.field_address"),
                placeholder=t("new.placeholder_address"),
                help=t("new.help_address"),
            )

            project_type = st.selectbox(
                t("new.field_type"),
                [
                    "Residential",
                    "Infrastructure",
                    "Commercial",
                    "Hospital",
                    "School",
                    "Industrial",
                    "Transportation",
                    "Energy",
                    "Other",
                ],
                format_func=project_type_label,
            )

            project_scale = st.selectbox(
                t("new.field_scale"),
                [
                    "Small",
                    "Medium",
                    "Large",
                    "Mega Project",
                ],
                format_func=project_scale_label,
            )

        with c2:

            project_name = st.text_input(
                t("new.field_name"),
                placeholder=t("new.placeholder_name"),
            )

            workers = st.number_input(
                t("new.field_workers"),
                min_value=0,
                max_value=100000,
                value=100,
                step=10,
            )

            start_date = st.date_input(
                t("new.field_start"),
                value=date.today(),
            )

            end_date = st.date_input(
                t("new.field_end"),
                value=date.today(),
            )

    # --------------------------------------------------------
    # AI ENVIRONMENTAL BASELINE
    # --------------------------------------------------------

    st.markdown("---")

    with st.container(border=True):

        st.subheader(
            t("new.subheader_ai_baseline")
        )

        st.write(
            t("new.ai_baseline_hint")
        )

        st.caption(
            t("new.ai_baseline_caption")
        )

        st.markdown(
            t("new.evaluate_markdown")
        )

    st.markdown("---")

    if st.button(
        t("new.button_create"),
        type="primary",
        use_container_width=True,
    ):

        errors = []

        if not country:
            errors.append(
                t("new.error_country")
            )

        if not city.strip():
            errors.append(
                t("new.error_city")
            )

        if not project_name.strip():
            errors.append(
                t("new.error_name")
            )

        if project_name.strip() in projects:
            errors.append(
                t("new.error_duplicate")
            )

        if end_date < start_date:
            errors.append(
                t("new.error_dates")
            )

        if errors:

            for error in errors:
                st.error(error)

            return

        # ----------------------------------------------------
        # AI BASELINE ASSESSMENT
        # ----------------------------------------------------

        with st.spinner(
            t("new.spinner_baseline")
        ):

            baseline = ai_baseline_assessment(
                country=country,
                city=city,
                address=address.strip(),
                project_type=project_type,
                project_scale=project_scale,
                workers=int(workers),
                start_date=start_date,
                end_date=end_date,
            )

        if isinstance(baseline, dict) and baseline.get("success"):
            # Remember which interface language produced the stored text so
            # the dashboard can tell it apart from a later language version.
            baseline["language"] = get_current_language()

        if not baseline.get("success"):

            st.error(
                baseline.get(
                    "error",
                    t("new.error_baseline_failed"),
                )
            )

            return

        name = project_name.strip()

        # ----------------------------------------------------
        # SAVE PROJECT
        # ----------------------------------------------------

        projects[name] = {

            "country": country,

            "city": city.strip(),

            "address": (
                address.strip()
                or f"{city.strip()}, {country}"
            ),

            "type": project_type,

            "workers": int(workers),

            "project_scale": project_scale,

            "start_date": str(start_date),

            "end_date": str(end_date),

            # -----------------------------------------------
            # AI BASELINE
            # -----------------------------------------------

            "baseline_score": baseline["score"],

            "baseline_risk": baseline,

            # Regenerable AI text is archived per interface language so a
            # later regeneration in another language can never overwrite
            # this version.
            "ai_text_by_language": {
                get_current_language(): baseline_ai_text(baseline),
            },

            "risk": baseline["level"].title(),

            # -----------------------------------------------
            # DAILY HISTORY
            # -----------------------------------------------

            "daily_logs": [],

            # -----------------------------------------------
            # PROJECT ORGANIZATION
            # -----------------------------------------------

            "starred": False,

            "pinned": False,

            "archived": False,

            "ended_at": None,

            # Language the name and the address were typed in. Only used to
            # know that no translation is missing for that language.
            "source_locale": content_locale.get_content_locale(
                st.session_state
            ),
        }

        persist()

        st.session_state.selected_project = name

        st.session_state.dashboard_view = "overview"

        st.success(
            t(
                "new.success_created",
                name=project_display_name(
                    projects.get(name),
                    get_current_language(),
                    fallback=name,
                ),
            )
        )

        st.info(
            t(
                "new.info_baseline",
                icon=risk_icon(baseline["level"]),
                level=risk_label(baseline["level"]),
                score=baseline["score"],
            )
        )

        st.switch_page(
            DASHBOARD_PAGE
        )

    if st.button(
        t("new.button_back"),
        use_container_width=True,
    ):
        st.switch_page(
            APP_PAGE
        )


# ============================================================
# PROJECT DASHBOARD
# ============================================================

def project_dashboard_page():
    projects = st.session_state.projects

    if not projects:
        st.info(t("dashboard.no_projects_created"))

        if st.button(
            t("dashboard.button_new_project"),
            type="primary",
        ):
            st.switch_page(NEW_PROJECT_PAGE)

        return

    selected = st.session_state.get(
        "selected_project"
    )

    if selected not in projects:
        selected = st.selectbox(
            t("dashboard.select_project"),
            list(projects.keys()),
            # Only the label is translated; the option value stays the project
            # id, so selection, storage and widget keys are unaffected.
            format_func=lambda name: project_display_name(
                projects.get(name),
                get_current_language(),
                fallback=name,
            ),
        )
        st.session_state.selected_project = selected

    project = projects[selected]

    baseline = project.get(
        "baseline_risk",
        {}
    )

    location = geocode_location(
        project.get("address", ""),
        project.get("city", ""),
        project.get("country", ""),
    )

    weather = (
        get_weather(
            location["latitude"],
            location["longitude"],
        )
        if location
        else None
    )

    log_dates = [
        log.get("date")
        for log in project.get("daily_logs", [])
        if log.get("date")
    ]

    weather_range_df = (
        weather_range_dataframe(
            location["latitude"],
            location["longitude"],
            log_dates,
        )
        if location
        else pd.DataFrame()
    )

    current = compute_current_risk(
        project,
        weather,
    )

    # Keep the portfolio's live risk state synced without rewriting the file
    # on every page render.
    new_risk = current["level"].title()
    if project.get("risk") != new_risk:
        project["risk"] = new_risk
        persist()

    view = st.session_state.get(
        "dashboard_view",
        "overview",
    )

    # ========================================================
    # MODULE VIEW: BASELINE
    # ========================================================

    if view == "baseline":
        if st.button(t("dashboard.back")):
            st.session_state.dashboard_view = "overview"
            st.rerun()

        st.title(t("dashboard.view_baseline_title"))
        st.caption(t("dashboard.view_baseline_caption"))

        # Content is selected per interface language by a pure helper:
        #   * a current-language archive is used alone (never mixed);
        #   * deterministic legacy templates are localised for display;
        #   * other-language free text is NOT shown - the notice and the
        #     regeneration button below let the user generate this language
        #     explicitly (never automatically).
        baseline_display_text = get_baseline_display_text(
            project,
            get_current_language(),
        )

        if baseline_display_text["needs_generation"]:
            st.warning(
                t(
                    "dashboard.warning_text_language",
                    saved=SUPPORTED_LANGUAGES.get(
                        baseline_display_text["saved_language"],
                        baseline_display_text["saved_language"],
                    ),
                    current=SUPPORTED_LANGUAGES[get_current_language()],
                )
            )

            if st.button(
                t("dashboard.button_regenerate_baseline"),
                key="regenerate_baseline",
            ):
                with st.spinner(t("new.spinner_baseline")):
                    fresh_text = baseline_ai_text(
                        ai_baseline_assessment(
                            country=project.get("country", ""),
                            city=project.get("city", ""),
                            address=project.get("address", ""),
                            project_type=project.get("type", ""),
                            project_scale=project.get("project_scale", ""),
                            workers=project.get("workers", 0),
                            start_date=project.get("start_date", ""),
                            end_date=project.get("end_date", ""),
                        )
                    )

                if fresh_text:
                    ai_text_store(project, "baseline", fresh_text)
                    persist()
                    st.rerun()
                else:
                    st.error(t("dashboard.error_regenerate_failed"))

        a, b, c = st.columns(3)

        a.metric(
            t("dashboard.metric_baseline_score"),
            f'{project.get("baseline_score", 0)}/100',
        )

        b.metric(
            t("dashboard.metric_baseline_level"),
            f'{risk_icon(baseline.get("level", "LOW"))} '
            f'{risk_label(baseline.get("level", "LOW"))}',
        )

        c.metric(
            t("dashboard.metric_project_scale"),
            project_scale_label(project.get("project_scale", "—")),
        )

        st.markdown("---")

        x, y = st.columns(2)

        with x:
            st.subheader(t("dashboard.subheader_environment"))
            _unknown_value = t("common.unknown")

            st.write(
                t(
                    "dashboard.env_terrain",
                    value=environment_label(
                        baseline.get("terrain", _unknown_value)
                    ),
                )
            )
            st.write(
                t(
                    "dashboard.env_drainage",
                    value=environment_label(
                        baseline.get("drainage", _unknown_value)
                    ),
                )
            )
            st.write(
                t(
                    "dashboard.env_rainfall",
                    value=environment_label(
                        baseline.get("rainfall", _unknown_value)
                    ),
                )
            )
            st.write(
                t(
                    "dashboard.env_earthquake",
                    value=environment_label(
                        baseline.get("earthquake", _unknown_value)
                    ),
                )
            )

        with y:
            st.subheader(t("dashboard.subheader_hazard"))

            for name, value in baseline.get(
                "risks",
                {}
            ).items():
                st.write(
                    f'**{hazard_label(name)}** · '
                    f'{risk_icon(value)} {risk_label(value)}'
                )

        st.markdown("---")
        st.subheader(t("dashboard.subheader_chains"))

        # Display layer only: chains / impacts / actions always go through
        # their label functions, and the content already belongs to the
        # current interface language (no cross-language fallback happens).
        if baseline_display_text["risk_chains"]:
            for chain in baseline_display_text["risk_chains"]:
                st.info(risk_chain_label(chain))
        else:
            st.success(
                t("dashboard.success_no_chain")
            )

        if baseline_display_text["impacts"]:
            st.subheader(t("dashboard.subheader_impacts"))

            for item in baseline_display_text["impacts"]:
                st.write(f"• {impact_label(item)}")

        st.subheader(t("dashboard.subheader_actions"))

        for action in baseline_display_text["actions"]:
            st.write(f"• {action_label(action)}")

        return

    # ========================================================
    # MODULE VIEW: RISK REPORTS
    # ========================================================

    if view == "risk_reports":

        if st.button(
            t("dashboard.back"),
            use_container_width=True,
        ):
            st.session_state.dashboard_view = "overview"
            st.rerun()

        render_risk_report_center(
            project=project,
            current=current,
            weather=weather,
            project_id=selected,
        )

        return

        # ========================================================
    # MODULE VIEW: CURRENT RISK
    # ========================================================

    if view == "current":

        if st.button(
            t("dashboard.back"),
            use_container_width=True,
        ):
            st.session_state.dashboard_view = "overview"
            st.rerun()


        st.title(t("dashboard.view_current_title"))

        st.caption(t("dashboard.view_current_caption"))

        absence_labels = {
            "人员全部到岗": "All personnel present",
            "少量缺勤": "Minor absence",
            "中等缺勤": "Moderate absence",
            "大量缺勤": "Severe absence",
        }
        saved_absence = project.get("absence_level", ABSENCE_LEVELS[0])
        selected_absence = st.selectbox(
            "Personnel attendance / 人员出勤",
            options=list(ABSENCE_LEVELS),
            index=(list(ABSENCE_LEVELS).index(saved_absence)
                   if saved_absence in ABSENCE_LEVELS else 0),
            format_func=lambda value: f"{value} / {absence_labels[value]}",
            key=f"field_absence_{selected}",
            help=("Used with live temperature, rainfall and wind to match "
                  "similar historical cases."),
        )
        if selected_absence != saved_absence:
            project["absence_level"] = selected_absence
            persist()
            st.rerun()


        # ----------------------------------------------------
        # CURRENT RISK METRICS
        # ----------------------------------------------------

        a, b, c, d = st.columns(4)

        a.metric(
            t("dashboard.metric_current"),
            f'{risk_icon(current["level"])} '
            f'{risk_label(current["level"])}',
        )

        b.metric(
            t("dashboard.metric_score"),
            f'{current["score"]}/100',
        )

        c.metric(
            t("dashboard.metric_weather_risk"),
            f'{current.get("components", {}).get("Weather", 0)}/100',
        )

        d.metric(
            t("dashboard.metric_log_risk"),
            f'{current.get("components", {}).get("Daily Logs", 0)}/100',
        )

        field_model = current.get("field_condition_model", {})
        with st.expander("Field-condition model evidence / 现场风险模型依据"):
            query = field_model.get("query", {})
            st.write(
                f"Temperature {query.get('temperature_c', '—')} °C · "
                f"Rainfall {query.get('precipitation_mm_24h', '—')} mm · "
                f"Wind {query.get('wind_speed_m_s', '—')} m/s · "
                f"Attendance {query.get('absence', '—')}"
            )
            distribution = field_model.get("distribution", {})
            st.write(
                f"8 nearest cases: LOW {distribution.get('LOW', 0)}, "
                f"MEDIUM {distribution.get('MEDIUM', 0)}, "
                f"HIGH {distribution.get('HIGH', 0)} · "
                f"average similarity {field_model.get('confidence', 0):.1%}"
            )
            evidence_rows = field_model.get("evidence", [])
            if evidence_rows:
                st.dataframe(pd.DataFrame(evidence_rows), use_container_width=True)


                # ----------------------------------------------------
        # VISUAL RISK RESOLUTION
        # ----------------------------------------------------

        visual_risks = project.get(
            "visual_risks",
            []
        )

        if visual_risks:

            visual_resolved = project.get(
                "visual_risk_resolved",
                False
            )

            if visual_resolved:
                st.success(
                    t("dashboard.success_visual_resolved")
                )
            else:
                st.warning(
                    t("dashboard.warning_visual_open")
                )

            resolved_now = st.checkbox(
                t("dashboard.checkbox_visual_fixed"),
                value=visual_resolved,
                key=f"visual_fix_{selected}"
            )

            if resolved_now != visual_resolved:

                project[
                    "visual_risk_resolved"
                ] = resolved_now

                persist()

                st.rerun()

        st.markdown("---")


        # ----------------------------------------------------
        # WHY CURRENT RISK LOOKS LIKE THIS (deterministic, never AI)
        # ----------------------------------------------------

        st.subheader(
            t("dashboard.subheader_why")
        )


        st.markdown(
            t("dashboard.components_header")
        )


        risk_components = current.get(
            "components",
            {}
        )


        risk_components = (
            risk_components
            if isinstance(risk_components, dict)
            else {}
        )


        risk_weights = current.get(
            "weights",
            {}
        )


        risk_weights = (
            risk_weights
            if isinstance(risk_weights, dict)
            else {}
        )


        # Localized component names are resolved with literal ``t()`` keys so
        # the i18n audit keeps full static reachability (no dynamic key).
        component_labels = {
            "Baseline": t("dashboard.component_baseline"),
            "Weather": t("dashboard.component_weather"),
            "Daily Logs": t("dashboard.component_daily_logs"),
            "Vision": t("dashboard.component_vision"),
            "Financial": t("dashboard.component_financial"),
        }

        for component_name in _CURRENT_RISK_COMPONENT_NAMES:

            st.write(
                t(
                    "dashboard.component_line",
                    name=component_labels.get(component_name, component_name),
                    value=risk_components.get(component_name, 0),
                    weight=risk_weights.get(component_name, "—"),
                )
            )


        st.caption(
            t(
                "dashboard.caption_component",
                baseline_component=risk_components.get("Baseline", 0),
            )
        )


        # ----------------------------------------------------
        # AI EXPLANATION (localized content protocol, stage 4A)
        # ----------------------------------------------------

        st.markdown("---")


        st.subheader(
            t("dashboard.subheader_ai")
        )


        st.caption(
            t("dashboard.caption_ai")
        )


        # Read-only: this never calls the AI and never writes anything. It
        # only decides what may be shown for the current content language and
        # the current risk input.
        risk_display = get_current_risk_display(
            project,
            current,
            content_locale.get_content_locale(st.session_state),
            project_id=selected,
            session_cache=current_risk_session_cache(),
        )


        risk_state = risk_display["ui_state"]


        generate_label = (
            t("dashboard.button_analyze")
            if risk_state in ("available", "stale", "legacy_compatible")
            else t("dashboard.button_generate_current_risk")
        )


        # The AI is ONLY ever called from this user-triggered button. Opening
        # the page, rerunning, switching project or switching language all
        # skip it. The widget key carries the project, the content locale and
        # the input hash, so one combination can never trigger another.
        if st.button(
            generate_label,
            type="primary",
            use_container_width=True,
            key=current_risk_generate_button_key(
                selected,
                risk_display["locale"],
                risk_display["input_hash"],
            ),
        ):

            with st.spinner(
                t("dashboard.spinner_analyze")
            ):

                risk_result = ai_explain_current_risk(
                    project=project,
                    current=current,
                    weather=weather,
                    target_locale=risk_display["locale"],
                    project_id=selected,
                )


            risk_payload = (
                risk_result.get("payload")
                if risk_result.get("success")
                else None
            )


            # Build the immutable record on a copy BEFORE any write: a
            # rejected or invalid response never reaches storage or disk.
            risk_record = (
                make_localized_record(
                    CURRENT_RISK_CONTENT_TYPE,
                    CURRENT_RISK_RECORD_ID,
                    risk_display["locale"],
                    risk_payload,
                    source=SOURCE_AI_GENERATED,
                    input_hash=risk_display["input_hash"],
                )
                if risk_payload
                else None
            )


            if risk_record is None:

                st.error(
                    t("dashboard.error_current_risk_generation_failed")
                )

            else:

                localized_content_put(
                    project,
                    risk_record,
                    copy_on_write=False,
                )


                bounded_cache_put(
                    current_risk_session_cache(),
                    current_risk_cache_key(
                        selected,
                        risk_display["locale"],
                        risk_display["input_hash"],
                    ),
                    risk_payload,
                )


                persist()

                st.rerun()



        # ----------------------------------------------------
        # DISPLAY THE VERSION STORED FOR THIS LANGUAGE
        # ----------------------------------------------------
        # Only the current content language is ever shown. A version written
        # in another language is never substituted, and an outdated version is
        # never presented as the current analysis.

        if risk_state == "available":

            with st.container(border=True):

                render_current_risk_text(risk_display["payload"])

        elif risk_state == "legacy_compatible":

            with st.container(border=True):

                render_current_risk_text(risk_display["payload"])

            st.info(
                t("dashboard.current_risk_legacy_note")
            )

        elif risk_state == "stale":

            st.warning(
                t("dashboard.warning_current_risk_stale")
            )

            if risk_display["payload"]:

                with st.expander(
                    t("dashboard.current_risk_old_version")
                ):

                    render_current_risk_text(risk_display["payload"])

        elif risk_state == "invalid":

            st.error(
                t("dashboard.error_current_risk_invalid_stored")
            )

        elif risk_state == "missing":

            st.info(
                t(
                    "dashboard.info_current_risk_missing",
                    current=locale_display_name(risk_display["locale"]),
                    saved=", ".join(
                        locale_display_name(code)
                        for code in risk_display["saved_locales"]
                    ),
                )
            )

        else:

            st.info(
                t("dashboard.info_current_risk_empty")
            )


        # ----------------------------------------------------
        # RISK STATUS
        # ----------------------------------------------------

        st.markdown("---")


        if current["level"] == "HIGH":

            st.error(
                t("dashboard.risk_high_message")
            )


        elif current["level"] == "MEDIUM":

            st.warning(
                t("dashboard.risk_medium_message")
            )


        else:

            st.success(
                t("dashboard.risk_low_message")
            )


        return
    # ========================================================
    # MODULE VIEW: WEATHER + TERRAIN
    # ========================================================

    if view == "weather":
        if st.button(t("dashboard.back")):
            st.session_state.dashboard_view = "overview"
            st.rerun()

        st.title(t("dashboard.view_weather_title"))

        if not location or not weather:
            st.error(
                t("dashboard.error_weather")
            )
            return

        weather_df = weather_range_df

        current_weather = weather.get(
            "current",
            {}
        )

        weather_points, weather_level, _ = weather_signal(
            weather
        )

        a, b, c = st.columns(3)

        a.metric(
            t("dashboard.metric_temperature"),
            f'{float(current_weather.get("temperature_2m", 0)):.1f} °C',
        )

        b.metric(
            t("dashboard.metric_precipitation"),
            f'{float(current_weather.get("precipitation", 0)):.1f} mm',
        )

        c.metric(
            t("dashboard.metric_weather_signal"),
            f'+{weather_points} · {weather_level}',
        )

        st.markdown("---")

        w1, w2 = st.columns(2)

        with w1:
            st.subheader(t("dashboard.subheader_temperature"))

            st.line_chart(
                weather_df.set_index("date")[
                    [
                        "Temperature Max (°C)",
                        "Temperature Min (°C)",
                    ]
                ].rename(columns={
                    "Temperature Max (°C)": t("dashboard.chart_temp_max"),
                    "Temperature Min (°C)": t("dashboard.chart_temp_min"),
                }),
                height=300,
            )

        with w2:
            st.subheader(t("dashboard.subheader_precipitation"))

            st.line_chart(
                weather_df.set_index("date")[
                    ["Precipitation (mm)"]
                ].rename(columns={
                    "Precipitation (mm)": t("dashboard.chart_precipitation"),
                }),
                height=300,
            )

        st.markdown("---")
        st.subheader(t("dashboard.subheader_elevation"))

        elevation = get_elevation_grid(
            location["latitude"],
            location["longitude"],
        )

        if elevation is None:
            st.warning(
                t("dashboard.warning_elevation")
            )
        else:
            render_contour_map(
                elevation,
                location,
            )

        return

    # ========================================================
    # MODULE VIEW: DAILY LOGS
    # ========================================================

    if view == "logs":
        if st.button(t("dashboard.back")):
            st.session_state.dashboard_view = "overview"
            st.rerun()

        st.title(t("dashboard.view_logs_title"))
        st.caption(t("dashboard.caption_logs"))

        logs = project.setdefault("daily_logs", [])

        editing_id = st.session_state.get("editing_log_id")

        if editing_id:
            editing = next(
                (x for x in logs if x.get("log_id") == editing_id),
                None,
            )
        else:
            editing = None

        with st.container(border=True):
            st.subheader(
                t("dashboard.logs_edit_title")
                if editing else t("dashboard.logs_new_title")
            )

            default_date = (
                pd.to_datetime(editing.get("date")).date()
                if editing and editing.get("date")
                else date.today()
            )

            work_date = st.date_input(
                t("dashboard.field_work_date"),
                value=default_date,
                key="log_work_date",
            )

            title = st.text_input(
                t("dashboard.field_log_title"),
                value=editing.get("title", "") if editing else "",
                placeholder=t("dashboard.placeholder_log_title"),
                key="log_title",
            )

            description = st.text_area(
                t("dashboard.field_log_description"),
                value=editing.get("description", "") if editing else "",
                placeholder=t("dashboard.placeholder_log_description"),
                height=150,
                key="log_description",
            )

            st.info(
                t("dashboard.info_ai_level_hint")
            )

            c1, c2 = st.columns(2)

            with c1:
                save_label = (
                    t("dashboard.button_update")
                    if editing
                    else t("dashboard.button_submit")
                )
                save_clicked = st.button(
                    save_label,
                    type="primary",
                    use_container_width=True,
                )

            with c2:
                cancel_clicked = (
                    st.button(
                        t("dashboard.button_cancel_edit"),
                        use_container_width=True,
                    )
                    if editing
                    else False
                )

            if cancel_clicked:
                st.session_state.editing_log_id = None
                for key in [
                    "log_work_date", "log_title", "log_description"
                ]:
                    st.session_state.pop(key, None)
                st.rerun()

            if save_clicked:
                if not title.strip() and not description.strip():
                    st.error(
                        t("dashboard.error_empty_log")
                    )
                else:
                    with st.spinner(
                        t("dashboard.spinner_analyze_log")
                    ):
                        analysis = ai_analyze_daily_log(
                            title.strip(),
                            description.strip(),
                        )

                    if editing:
                        target = editing
                        target.update({
                            "date": str(work_date),
                            "title": title.strip(),
                            "description": description.strip(),
                            "severity": analysis["level"],
                            "ai_analysis": analysis,
                            "updated_at": datetime.now().isoformat(
                                timespec="minutes"
                            ),
                        })
                        message = t(
                            "dashboard.msg_log_updated",
                            icon=risk_icon(analysis["level"]),
                            level=risk_label(analysis["level"]),
                        )
                    else:
                        target = {
                            "log_id": datetime.now().strftime(
                                "%Y%m%d%H%M%S%f"
                            ),
                            "date": str(work_date),
                            "title": title.strip(),
                            "description": description.strip(),
                            "severity": analysis["level"],
                            "ai_analysis": analysis,
                            # Language the log was typed in. Only used to know
                            # that no translation is missing for that language.
                            "source_locale": content_locale.get_content_locale(
                                st.session_state
                            ),
                            "created_at": datetime.now().isoformat(
                                timespec="minutes"
                            ),
                        }
                        logs.append(target)
                        message = t(
                            "dashboard.msg_log_saved",
                            icon=risk_icon(analysis["level"]),
                            level=risk_label(analysis["level"]),
                        )

                    # Archive the regenerable AI text for the current
                    # interface language without touching the other one.
                    analysis["language"] = get_current_language()

                    if target.get("log_id"):
                        ai_text_store(
                            project,
                            "logs",
                            log_ai_text(analysis),
                            record_id=target["log_id"],
                        )

                    logs.sort(key=lambda x: str(x.get("date", "")))
                    st.session_state.editing_log_id = None

                    # Clear the form state after successful save.
                    for key in [
                        "log_work_date", "log_title", "log_description"
                    ]:
                        st.session_state.pop(key, None)

                    persist()
                    st.success(message)
                    st.rerun()

        st.markdown("---")

        if not logs:
            st.info(t("dashboard.info_no_logs"))
        else:
            for log in reversed(logs):
                analysis = log.get("ai_analysis", {})

                ui_locale = get_current_language()

                # Language-safe AI text: only the current interface language
                # may be shown. A missing version never falls back to the
                # other language's wording; the notice and the generate
                # button below take over instead.
                display_text = get_log_display_text(
                    project,
                    log,
                    ui_locale,
                )

                # User written title / description: the current language
                # version when one exists, the original otherwise. Reading is
                # pure: nothing is translated or stored while rendering.
                user_text = log_display_text(log, ui_locale)

                level = normalize_ai_level(
                    analysis.get(
                        "level",
                        log.get("severity", "LOW"),
                    )
                )

                with st.container(border=True):
                    top1, top2 = st.columns([5, 1])

                    with top1:
                        st.markdown(
                            f'### {log.get("date", "")} · '
                            f'{risk_icon(level)} {risk_label(level)}'
                        )
                        st.write(
                            f'**{user_text["title"] or t("common.untitled")}**'
                        )
                        st.write(
                            user_text["description"]
                        )

                        log_id = log.get("log_id")

                        if user_text["needs_translation"]:

                            # The original wording is on screen: say so, and
                            # translate for this language only when the user
                            # asks for it.
                            st.caption(
                                t("dashboard.info_user_text_original")
                            )

                            if st.button(
                                t("dashboard.button_translate_log"),
                                key=user_text_widget_key(
                                    "translate_log",
                                    selected,
                                    log_id,
                                    ui_locale,
                                ),
                            ):
                                translate_log_content_action(project, log)

                        elif user_text["translated"] and log_id:

                            # Separate entry point: editing a translation
                            # never writes into the original title or
                            # description.
                            with st.expander(
                                t("dashboard.expander_edit_translation")
                            ):

                                translated_title = st.text_input(
                                    t("dashboard.field_log_title"),
                                    value=user_text_value(
                                        log,
                                        "title",
                                        ui_locale,
                                    ),
                                    key=user_text_widget_key(
                                        "translation_title",
                                        selected,
                                        log_id,
                                        ui_locale,
                                    ),
                                )

                                translated_description = st.text_area(
                                    t("dashboard.field_log_description"),
                                    value=user_text_value(
                                        log,
                                        "description",
                                        ui_locale,
                                    ),
                                    height=150,
                                    key=user_text_widget_key(
                                        "translation_description",
                                        selected,
                                        log_id,
                                        ui_locale,
                                    ),
                                )

                                st.caption(
                                    t("dashboard.caption_translation_safe")
                                )

                                if st.button(
                                    t("dashboard.button_save_translation"),
                                    key=user_text_widget_key(
                                        "save_translation",
                                        selected,
                                        log_id,
                                        ui_locale,
                                    ),
                                ):
                                    if store_user_text_translation(
                                        log,
                                        ui_locale,
                                        {
                                            "title": translated_title,
                                            "description": translated_description,
                                        },
                                        LOG_USER_TEXT_FIELDS,
                                    ):
                                        persist()
                                        st.rerun()
                                    else:
                                        st.error(
                                            t(
                                                "dashboard.error_translation_invalid"
                                            )
                                        )

                    with top2:
                        if st.button(
                            t("dashboard.button_edit_log"),
                            key=f'edit_log_{log.get("log_id")}',
                            use_container_width=True,
                        ):
                            st.session_state.editing_log_id = log.get(
                                "log_id"
                            )
                            for key in [
                                "log_work_date",
                                "log_title",
                                "log_description",
                            ]:
                                st.session_state.pop(key, None)
                            st.rerun()

                    score = analysis.get("score")

                    if score is not None:

                        st.markdown(
                            t("dashboard.logs_risk_summary")
                        )

                        c1, c2, c3 = st.columns(3)

                        with c1:
                            st.metric(
                                t("dashboard.metric_risk_score"),
                                f"{int(score)}/100",
                            )

                        with c2:
                            st.metric(
                                t("dashboard.metric_risk_level"),
                                risk_label(
                                    analysis.get(
                                        "level",
                                        "LOW",
                                    )
                                ),
                            )

                        with c3:
                            st.metric(
                                t("dashboard.metric_detected_risks"),
                                analysis.get(
                                    "risk_count",
                                    len(
                                        analysis.get(
                                            "risks",
                                            [],
                                        )
                                    ),
                                ),
                            )


                    if display_text["needs_generation"]:

                        # The AI text only exists in another language: show
                        # the accurate notice instead of that text, and let
                        # the user generate this language version.
                        st.warning(
                            t(
                                "dashboard.warning_log_text_language",
                                saved=SUPPORTED_LANGUAGES.get(
                                    display_text["saved_language"],
                                    display_text["saved_language"],
                                ),
                                current=SUPPORTED_LANGUAGES[
                                    get_current_language()
                                ],
                            )
                        )

                        # A log without an id cannot archive the result, so it
                        # only gets the notice.
                        if log.get("log_id") and st.button(
                            t("dashboard.button_generate_log_text"),
                            key=log_generate_button_key(
                                selected,
                                log.get("log_id"),
                                get_current_language(),
                            ),
                        ):

                            with st.spinner(
                                t("dashboard.spinner_generate_log_text")
                            ):
                                fresh_text = ai_generate_log_text_version(
                                    log,
                                    get_current_language(),
                                )

                            payload = generated_text_payload(fresh_text)

                            if not payload:

                                # Nothing is archived and nothing is
                                # overwritten when the AI fails.
                                st.error(
                                    t(
                                        "dashboard.error_log_text_generation_failed"
                                    )
                                )

                            else:

                                ai_text_store(
                                    project,
                                    "logs",
                                    payload,
                                    record_id=log.get("log_id"),
                                    language=get_current_language(),
                                )

                                persist()
                                st.rerun()

                    elif display_text["source"] == SOURCE_EMPTY:

                        st.caption(
                            t("dashboard.info_log_text_missing")
                        )

                    elif display_text["summary"]:

                        with st.expander(
                            t("dashboard.expander_ai_assessment"),
                            expanded=False,
                        ):
                            st.write(display_text["summary"])



                    risks = analysis.get(
                        "risks",
                        [],
                    )


                    if risks:

                        st.markdown(
                            t("dashboard.logs_assessment_basis")
                        )

                        st.caption(
                            t("dashboard.caption_sle_model")
                        )


                        for index, item in enumerate(risks[:3]):

                            if not isinstance(
                                item,
                                dict,
                            ):
                                continue

                            # Text comes only from the current language; the
                            # numbers below stay the stored ones.
                            safe_text = log_risk_display_fields(
                                display_text,
                                index,
                            )

                            with st.container(
                                border=True
                            ):

                                st.write(
                                    f'**{safe_text["title"] or t("dashboard.default_risk_title")}**'
                                )


                                if safe_text["reason"]:

                                    st.write(
                                        safe_text["reason"]
                                    )


                                a,b,c,d = st.columns(4)


                                a.metric(
                                    t("dashboard.metric_severity"),
                                    f'{item.get("severity",1)}/5'
                                )

                                b.metric(
                                    t("dashboard.metric_likelihood"),
                                    f'{item.get("likelihood",1)}/5'
                                )

                                c.metric(
                                    t("dashboard.metric_exposure"),
                                    f'{item.get("exposure",1)}/5'
                                )

                                d.metric(
                                    t("dashboard.metric_risk_index"),
                                    f'{item.get("score",10)}/100'
                                )


                                st.caption(
                                    t(
                                        "dashboard.formula_sle",
                                        severity=item.get("severity", 1),
                                        likelihood=item.get("likelihood", 1),
                                        exposure=item.get("exposure", 1),
                                        raw=item.get("raw_risk", 1),
                                    )
                                )


                                if safe_text["suggestion"]:

                                    st.caption(
                                        t(
                                            "dashboard.risk_suggestion",
                                            value=safe_text["suggestion"],
                                        )
                                    )


                    else:

                        st.success(
                            t("dashboard.success_no_risk_factors")
                        )


                    st.caption(
                        t("dashboard.caption_scoring_method")
                    )            


    # ========================================================
    # MODULE VIEW: DAILY RISK
    # ========================================================

    if view == "daily":

        if st.button(t("dashboard.back")):
            st.session_state.dashboard_view = "overview"
            st.rerun()


        st.title(t("dashboard.view_daily_title"))

        st.caption(t("dashboard.caption_daily"))


        if not weather:

            st.warning(
                t("dashboard.warning_no_weather")
            )

            return


        weather_df = weather_range_df

        daily_df = build_daily_risk(
            project,
            weather_df,
            language=get_current_language(),
        )


        if daily_df.empty:

            st.info(
                t("dashboard.info_no_daily_data")
            )

            return


        display_df = daily_df[
            [
                "date",
                "risk",
                "risk_score",
                "reasons",
            ]
        ].copy()


        display_df["status"] = display_df[
            "risk"
        ].map({
            "HIGH": "🔴",
            "MEDIUM": "🟠",
            "LOW": "🟢",
        })


        # Display-only: the stored reason strings stay unchanged.
        display_df["reasons"] = display_df["reasons"].map(
            reason_text_label,
            na_action="ignore",
        )


        st.dataframe(
            display_df[
                [
                    "date",
                    "status",
                    "risk_score",
                    "reasons",
                ]
            ].rename(columns={
                "date": t("dashboard.table_date"),
                "status": t("dashboard.table_status"),
                "risk_score": t("dashboard.table_risk_score"),
                "reasons": t("dashboard.table_reasons"),
            }),
            use_container_width=True,
            hide_index=True,
        )


        st.markdown("---")

        st.subheader(
            t("dashboard.subheader_trend")
        )


        st.line_chart(
            daily_df.set_index("date")[
                ["risk_score"]
            ].rename(columns={
                "risk_score": t("dashboard.chart_risk_score"),
            }),
            height=300,
        )

        return
    # ========================================================
    # MAIN DASHBOARD
    # ========================================================

    # Display only: the current language version of the project name when one
    # exists, the original name otherwise. ``selected`` stays the project id
    # used for storage, widget keys and the report identity.
    ui_locale = get_current_language()

    st.title(
        f'🏗️ {project_display_name(project, ui_locale, fallback=selected)}'
    )

    if project_needs_translation(project, ui_locale):

        st.info(
            t("dashboard.info_user_text_original")
        )

        if st.button(
            t("dashboard.button_translate_project_name"),
            key=user_text_widget_key(
                "translate_project_name_detail",
                selected,
                ui_locale,
            ),
        ):
            translate_project_name_action(selected, project)

    st.caption(
        t(
            "card.location",
            city=project.get("city", ""),
            country=project.get("country", ""),
            type=project_type_label(project.get("type", "Construction")),
        )
    )

    st.markdown("---")

    st.header(t("dashboard.header_overview"))

    a, b, c = st.columns(3)

    a.metric(
        t("dashboard.metric_overall_risk"),
        f'{risk_icon(current["level"])} '
        f'{risk_label(current["level"])}',
    )

    b.metric(
        t("dashboard.metric_workers"),
        f'{int(project.get("workers", 0) or 0):,}',
    )

    c.metric(
        t("dashboard.metric_logs"),
        len(project.get("daily_logs", [])),
    )

    st.caption(
        t(
            "dashboard.caption_score",
            score=current["score"],
            baseline=project.get("baseline_score", 0),
        )
    )

    st.markdown("---")

    st.header(t("dashboard.header_attention"))
    st.caption(t("dashboard.caption_attention"))

    row1 = st.columns(2)

    with row1[0]:
        module_card(
            "🧱",
            t("dashboard.card_baseline_title"),
            t("dashboard.card_baseline_desc"),
            "baseline",
            f'{risk_icon(baseline.get("level", "LOW"))} '
            f'{risk_label(baseline.get("level", "LOW"))} · '
            f'{project.get("baseline_score", 0)}/100',
        )

    with row1[1]:
        weather_points, weather_level, _ = weather_signal(
            weather
        )

        module_card(
            "🌦️",
            t("dashboard.card_weather_title"),
            t("dashboard.card_weather_desc"),
            "weather",
            t(
                "dashboard.card_weather_caption",
                icon=risk_icon(weather_level),
                points=weather_points,
            ),
        )

    row2 = st.columns(2)

    with row2[0]:
        module_card(
            "⚠️",
            t("dashboard.card_current_title"),
            t("dashboard.card_current_desc"),
            "current",
            f'{risk_icon(current["level"])} '
            f'{risk_label(current["level"])} · '
            f'{current["score"]}/100',
        )

    with row2[1]:
        module_card(
            "📝",
            t("dashboard.card_logs_title"),
            t("dashboard.card_logs_desc"),
            "logs",
            t("dashboard.card_logs_caption",
              count=len(project.get("daily_logs", []))),
        )

    row3 = st.columns(2)

    with row3[0]:
        module_card(
            "📅",
            t("dashboard.card_daily_title"),
            t("dashboard.card_daily_desc"),
            "daily",
            t("dashboard.card_daily_caption"),
        )

    with row3[1]:
        module_card(
            "📨",
            t("dashboard.card_reports_title"),
            t("dashboard.card_reports_desc"),
            "risk_reports",
            t("dashboard.card_reports_caption"),
        )

    # --------------------------------------------------------
    # RISK TIMELINE
    # --------------------------------------------------------

    st.markdown("---")
    st.header(t("dashboard.header_timeline"))

    if weather:
        weather_df = weather_range_df

        daily_df = build_daily_risk(
            project,
            weather_df,
            language=get_current_language(),
        )

        if not daily_df.empty:
            timeline = daily_df[
                [
                    "date",
                    "risk",
                    "risk_score",
                ]
            ].copy()

            timeline["status"] = timeline[
                "risk"
            ].map({
                "HIGH": "🔴",
                "MEDIUM": "🟠",
                "LOW": "🟢",
            })

            st.dataframe(
                timeline[
                    [
                        "date",
                        "status",
                        "risk_score",
                    ]
                ].rename(columns={
                    "date": t("dashboard.table_date"),
                    "status": t("dashboard.table_status"),
                    "risk_score": t("dashboard.table_risk_score"),
                }),
                use_container_width=True,
                hide_index=True,
            )

    else:
        st.info(
            t("dashboard.timeline_weather_info")
        )

    # --------------------------------------------------------
    # PROJECT ACTIONS
    # --------------------------------------------------------

    st.markdown("---")

    if not bool(project.get("archived", False)):

        x, y = st.columns(2)

        with x:
            if st.button(
                t("dashboard.button_add_log"),
                use_container_width=True,
            ):
                st.session_state.dashboard_view = "logs"
                st.rerun()

        with y:
            if st.button(
                t("dashboard.button_end_project"),
                use_container_width=True,
                type="secondary",
            ):
                st.session_state.confirm_end_project = True
                st.rerun()

        if st.session_state.get(
            "confirm_end_project",
            False,
        ):

            st.warning(
                t("dashboard.end_warning")
            )

            e1, e2 = st.columns(2)

            with e1:
                if st.button(
                    t("dashboard.confirm_end"),
                    type="primary",
                    use_container_width=True,
                ):
                    end_project(selected)
                    st.session_state.confirm_end_project = False
                    st.switch_page(APP_PAGE)

            with e2:
                if st.button(
                    t("dashboard.button_cancel"),
                    use_container_width=True,
                ):
                    st.session_state.confirm_end_project = False
                    st.rerun()

    else:

        st.info(
            t(
                "dashboard.historical_info",
                date=project.get("ended_at") or "—",
            )
        )

        x, y = st.columns(2)

        with x:
            if st.button(
                t("dashboard.button_back_history"),
                use_container_width=True,
            ):
                st.session_state.selected_project = None
                st.switch_page(HISTORY_PAGE)

        with y:
            if st.button(
                t("dashboard.button_reopen"),
                use_container_width=True,
            ):
                reopen_project(selected)



# ============================================================
# NAVIGATION
# ============================================================

from vision_risk import vision_risk_page
from supplier_risk import supplier_risk_page


def vision_page():
    """
    Vision Risk 页面入口。

    ``persist`` 只在页面确实改动了 session 中的 project 之后被调用；页面自身不
    直接接触磁盘。
    """
    vision_risk_page(persist=persist)


HISTORY_PAGE = st.Page(
    history_page,
    title=t("nav.title.history"),
    icon="🗃️",
    url_path="history",
)


NEW_PROJECT_PAGE = st.Page(
    new_project_page,
    title=t("nav.title.new"),
    icon="➕",
    url_path="new-project",
)


APP_PAGE = st.Page(
    app_page,
    title=t("nav.title.app"),
    icon="🌍",
    url_path="app",
    default=True,
)


DASHBOARD_PAGE = st.Page(
    project_dashboard_page,
    title=t("nav.title.dashboard"),
    icon="🏗️",
    url_path="dashboard",
)


VISION_PAGE = st.Page(
    vision_page,
    title=t("nav.title.vision"),
    icon="🖼️",
    url_path="vision-risk",
)


SUPPLIER_PAGE = st.Page(
    supplier_risk_page,
    title=t("nav.title.supplier"),
    icon="🔍",
    url_path="supplier-risk",
)


current_page = st.navigation(
    [
        NEW_PROJECT_PAGE,
        APP_PAGE,
        DASHBOARD_PAGE,
        VISION_PAGE,
        HISTORY_PAGE,
        SUPPLIER_PAGE,
    ],
    position="sidebar",
    expanded=True,
)


current_page.run()
