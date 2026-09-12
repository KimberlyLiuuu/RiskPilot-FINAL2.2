# -*- coding: utf-8 -*-
"""
RiskPilot Daily Log AI text display layer (Streamlit-free).

This module owns the **per-language content selection** for the AI written
text of a daily log:

    * ``summary``
    * ``risks[i]["title"]``
    * ``risks[i]["reason"]``
    * ``risks[i]["suggestion"]``

plus the two pure helpers the page layer needs around it:

    * :func:`validate_log_text_version` - validates an AI response before it
      is allowed into the per-language archive;
    * :func:`build_log_text_version_prompt` - builds the "only rewrite the
      text, never the numbers" prompt.

Hard rules enforced here
------------------------
* A log never mixes two languages. When an archive for the current language
  exists, only that archive is read.
* A missing current-language archive never falls back to another language's
  **free text**. Unknown free text is hidden instead of being shown in the
  wrong language.
* Only the closed set of deterministic system sentences (``Migrated`` /
  fallback summaries produced by RiskPilot itself) may be localized locally
  through stable locale keys.
* This module is read-only: it never mutates ``project`` / ``log`` /
  ``analysis``, never calls ``persist()``, never touches ``st.session_state``,
  never reads ``ui_lang`` and never calls the AI.
* Risk numbers (``score`` / ``level`` / ``severity`` / ``likelihood`` /
  ``exposure`` / ``raw_risk`` / ``risk_count``) are never read or written by
  the selection logic. They belong to the language-independent analysis and
  are rendered directly from it by the page layer.
* User written content (``log["title"]`` / ``log["description"]`` /
  ``log["date"]``) is never overwritten: it is the user's own record. A
  per-language translation may live next to it in
  ``localized_user_text`` (see the USER WRITTEN CONTENT section), and only the
  current language's version is ever displayed - never another language's.
"""

from __future__ import annotations

import hashlib
from typing import Dict, List, Optional

import i18n

from utils import locale_registry
from utils.baseline_display import ai_text_lookup, estimated_text_language

#: Archive section used by the Daily Log text.
LOG_SECTION = "logs"

#: Read-only compatibility with an earlier section name.
LEGACY_LOG_SECTION = "log"

#: Text fields of one log risk that are AI written free text.
RISK_TEXT_FIELDS = ("title", "reason", "suggestion")

# --- selection sources -------------------------------------------------

#: Current-language archive exists and has content.
SOURCE_LOCALIZED_ARCHIVE = "localized_archive"

#: No archive, the legacy AI text is already in the current language.
SOURCE_LEGACY_CURRENT_LANGUAGE = "legacy_current_language"

#: No archive, the legacy text is only closed-set system text, which is
#: translated locally for display.
SOURCE_DETERMINISTIC_LEGACY = "deterministic_legacy"

#: No archive and the legacy text is another language's free text, so it
#: cannot be shown as-is.
SOURCE_MISSING_CURRENT_LANGUAGE = "missing_current_language"

#: The log has no AI written text at all.
SOURCE_EMPTY = "empty"


# ------------------------------------------------------------
# DETERMINISTIC SYSTEM TEXT (Migrated / fallback summaries)
# ------------------------------------------------------------
# These sentences are produced by RiskPilot itself (log migration and the
# AI-unavailable fallback), not by the model, so they are a closed set. They
# are localized locally for display instead of being hidden or regenerated.
# Anything that is not in this table is treated as AI free text and must
# NEVER be shown in another interface language.

SYSTEM_SUMMARY_DISPLAY_KEYS = {
    "Normal construction activity. No significant risk identified.":
        "log.summary.no_risk",
    "Minor construction risk identified. Routine monitoring is recommended.":
        "log.summary.low",
    "A meaningful construction risk requires management attention.":
        "log.summary.medium",
    "A significant construction risk requires priority control measures.":
        "log.summary.high",
    "AI analysis was temporarily unavailable. No automated risk "
    "classification was applied.":
        "log.summary.ai_unavailable",
    "AI analysis unavailable; temporary rule-based assessment was used.":
        "log.summary.rule_based_fallback",
    "Imported from an earlier RiskPilot log.":
        "log.summary.migrated",
}


def _translate(key: str, language=None) -> str:
    """Translate a key for an explicit language, or for the current UI one."""
    if language is None:
        return i18n.t(key)

    return i18n.load_locale(language).get(key, key)


def is_system_summary(text) -> bool:
    """True when the summary belongs to the closed system sentence set."""
    return _text(text) in SYSTEM_SUMMARY_DISPLAY_KEYS


def system_summary_label(text, language=None) -> str:
    """
    Display-only localization of a known system summary.

    Unknown text returns ``""`` so that a misuse of this helper can never
    leak free text written in another language.
    """
    raw = _text(text)

    key = SYSTEM_SUMMARY_DISPLAY_KEYS.get(raw)

    if not key:
        return ""

    localized = _translate(key, language)

    return localized if localized != key else raw


# ------------------------------------------------------------
# READ-ONLY VIEWS OF THE STORED DATA
# ------------------------------------------------------------

def _text(value) -> str:
    """Safe display string (never ``None``, never a non-string object)."""
    if value is None:
        return ""

    if isinstance(value, str):
        return value.strip()

    return str(value).strip()


def _empty_risk_text() -> Dict[str, str]:
    return {field: "" for field in RISK_TEXT_FIELDS}


def _risk_text(item) -> Dict[str, str]:
    """Text-only view of one risk item (index alignment is kept)."""
    if not isinstance(item, dict):
        return _empty_risk_text()

    return {field: _text(item.get(field)) for field in RISK_TEXT_FIELDS}


def _has_text(risk) -> bool:
    return bool(risk) and any(risk.get(field) for field in RISK_TEXT_FIELDS)


def _text_risks(values) -> List[Dict[str, str]]:
    if not isinstance(values, list):
        return []

    return [_risk_text(item) for item in values]


def log_risk_count(analysis) -> int:
    """
    Number of risk entries in a stored analysis (language independent).

    Read-only and type tolerant: the count is the length of the stored list
    so that a generated language version can be required to match it.
    """
    if not isinstance(analysis, dict):
        return 0

    risks = analysis.get("risks")

    return len(risks) if isinstance(risks, list) else 0


def log_analysis_text(analysis) -> Dict[str, object]:
    """
    Text-only view of a stored log analysis.

    Only the AI written text is returned; the numbers stay in the analysis.
    """
    if not isinstance(analysis, dict):
        return {"summary": "", "risks": []}

    return {
        "summary": _text(analysis.get("summary")),
        "risks": _text_risks(analysis.get("risks")),
    }


def _archive_payload(project, log_id, language):
    """
    Read-only per-language archive lookup for one log.

    Stage 3: this delegates to :func:`utils.baseline_display.ai_text_lookup`,
    which now resolves the **unified localized content protocol**
    (``project["localized_content"]``) first and only then the legacy
    ``project["ai_text_by_language"]`` archive. Both probes below therefore
    already see stage 3 records; the module needs no extra reader. The lookup
    stays language-exact, so another language version is still never used as a
    fallback.

    ``"logs"`` is the section written by the current code. ``"log"`` is only
    read as a compatibility safety net for data written by an earlier
    revision; it is never written and never overrides ``"logs"``.
    """
    payload = ai_text_lookup(project, LOG_SECTION, log_id, language)

    if isinstance(payload, dict):
        return payload

    return ai_text_lookup(project, LEGACY_LOG_SECTION, log_id, language)


def archive_log_text(project, log_id, language=None) -> Dict[str, object]:
    """Text stored in the archive for one log and one language (read-only)."""
    current = language or i18n.DEFAULT_LANGUAGE

    payload = _archive_payload(project, log_id, current)

    if not isinstance(payload, dict):
        return {"summary": "", "risks": []}

    return {
        "summary": _text(payload.get("summary")),
        "risks": _text_risks(payload.get("risks")),
    }


# ------------------------------------------------------------
# CURRENT-LANGUAGE CONTENT SELECTION
# ------------------------------------------------------------

def _result(
    source: str,
    summary: str,
    risks: List[Dict[str, str]],
    needs_generation: bool,
    saved_language: str,
) -> dict:
    return {
        "source": source,
        "summary": _text(summary),
        "risks": [dict(risk) for risk in risks],
        "needs_generation": bool(needs_generation),
        "saved_language": saved_language,
    }


def get_log_display_text(project, log, language=None) -> dict:
    """
    Return the AI written log text the current language may safely display.

    Pure and read-only: it never mutates ``project``, ``log`` or the stored
    analysis, never calls ``persist()``, never touches ``st.session_state``,
    never reads ``ui_lang`` and never calls the AI. Showing the notice and
    the "generate this language version" button is the page layer's job and
    is driven by ``needs_generation``.

    ``source`` is one of:

    * ``localized_archive``         current-language archive (verbatim)
    * ``legacy_current_language``   legacy AI text already in this language
    * ``deterministic_legacy``      legacy text is closed-set system text
    * ``missing_current_language``  another language's free text (hidden)
    * ``empty``                     no AI written text at all
    """
    current = language or i18n.DEFAULT_LANGUAGE

    project = project if isinstance(project, dict) else {}
    log = log if isinstance(log, dict) else {}

    analysis = log.get("ai_analysis")
    analysis = analysis if isinstance(analysis, dict) else {}

    log_id = log.get("log_id")

    # --- A. current-language archive wins, and is used alone ---------
    if log_id not in (None, ""):
        archived = archive_log_text(project, log_id, current)

        if archived["summary"] or any(
            _has_text(risk) for risk in archived["risks"]
        ):
            return _result(
                SOURCE_LOCALIZED_ARCHIVE,
                archived["summary"],
                archived["risks"],
                needs_generation=False,
                saved_language=current,
            )

    # --- legacy text stored inside the analysis ----------------------
    legacy = log_analysis_text(analysis)

    saved_language = estimated_text_language(analysis)

    legacy_has_text = bool(legacy["summary"]) or any(
        _has_text(risk) for risk in legacy["risks"]
    )

    if not legacy_has_text:
        # --- D. no AI written text at all ---------------------------
        return _result(
            SOURCE_EMPTY,
            "",
            [],
            needs_generation=False,
            saved_language=saved_language,
        )

    if saved_language == current:
        # --- C. legacy text already matches the interface language --
        return _result(
            SOURCE_LEGACY_CURRENT_LANGUAGE,
            legacy["summary"],
            legacy["risks"],
            needs_generation=False,
            saved_language=saved_language,
        )

    # --- B. legacy text belongs to another language -----------------
    unknown_free_text = (
        bool(legacy["summary"]) and not is_system_summary(legacy["summary"])
    ) or any(_has_text(risk) for risk in legacy["risks"])

    if unknown_free_text:
        # Another language's free text is hidden instead of being shown in
        # the wrong language; the notice and the button are shown instead.
        return _result(
            SOURCE_MISSING_CURRENT_LANGUAGE,
            "",
            [],
            needs_generation=True,
            saved_language=saved_language,
        )

    # Only closed-set system sentences: safe to translate locally.
    return _result(
        SOURCE_DETERMINISTIC_LEGACY,
        system_summary_label(legacy["summary"], current),
        [],
        needs_generation=False,
        saved_language=saved_language,
    )


def log_risk_display_fields(display_text, index: int) -> Dict[str, str]:
    """
    Safe text fields of one risk, by stored index.

    Returns empty strings when the current language has no safe version, so
    the caller can fall back to a localized placeholder title and never
    renders another language's text.
    """
    risks = display_text.get("risks") if isinstance(display_text, dict) else None

    if not isinstance(risks, list):
        return _empty_risk_text()

    if not isinstance(index, int) or index < 0 or index >= len(risks):
        return _empty_risk_text()

    item = risks[index]

    if not isinstance(item, dict):
        return _empty_risk_text()

    return {field: _text(item.get(field)) for field in RISK_TEXT_FIELDS}


def summary_for_reasons(project, log, language=None) -> str:
    """
    Summary that may be added to the Daily Risk reasons column.

    Only the current language's version is returned; missing text returns
    ``""`` so that another language's summary is never mixed into the table.
    """
    return get_log_display_text(project, log, language)["summary"]


def log_generate_button_key(project_name, log_id, language) -> str:
    """
    Streamlit widget key for the "generate this language version" button.

    Includes the project, the log and the interface language through a
    stable hash, so different projects, logs and languages can never share
    widget state.
    """
    payload = f"{project_name}|{log_id}|{language}"

    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()

    return f"generate_log_text::{digest}"


# ------------------------------------------------------------
# USER WRITTEN CONTENT (project name / daily log title + description)
# ------------------------------------------------------------
# The project name and the daily log title / description are the user's own
# record. The original values stay exactly where they are:
#
#     project["name"]        log["title"]        log["description"]
#
# A per-language translation may be stored *next to* them:
#
#     project["localized_user_text"] = {"zh-CN": {"name": "..."}, ...}
#     log["localized_user_text"]     = {"zh-CN": {"title": ..., "description": ...}, ...}
#
# Selection rules ("哪个语言显示什么"):
#   1. the current language has a translation -> the translation is displayed;
#   2. the current language equals ``source_locale`` -> the original is shown;
#   3. no translation for the current language -> the original is shown and the
#      caller may offer the explicit "translate for this language" action;
#   4. another language's translation is NEVER read (no cross-language
#      fallback);
#   5. nothing here calls the AI, writes ``st.session_state``, calls
#      ``persist()`` or reads ``ui_lang``. Generating a translation is always a
#      user action in the page layer, and it only ever writes the current
#      language of ``localized_user_text``.
# Legacy records without ``source_locale`` keep their original text; the
# original language is never guessed (the user may still translate on demand).

#: Per-language store of translated user written text.
USER_TEXT_KEY = "localized_user_text"

#: Language the original user text was written in (only new records carry it).
SOURCE_LOCALE_KEY = "source_locale"

#: Field name of the translated project name.
PROJECT_NAME_FIELD = "name"

#: Fields of a translated daily log.
LOG_USER_TEXT_FIELDS = ("title", "description")

#: Hard limits applied to a stored translation (AI output outside is rejected).
MAX_PROJECT_NAME_LENGTH = 120
MAX_LOG_TITLE_LENGTH = 200
MAX_LOG_DESCRIPTION_LENGTH = 4000


def user_text_container(container) -> Dict[str, dict]:
    """Read-only view of ``localized_user_text`` (never ``None``)."""
    raw = container.get(USER_TEXT_KEY) if isinstance(container, dict) else None

    if not isinstance(raw, dict):
        return {}

    return {code: values for code, values in raw.items() if isinstance(values, dict)}


def user_text_value(container, field, language=None) -> str:
    """
    Translation of one field for exactly one language.

    Missing, empty or malformed values return ``""``; another language's
    translation is never returned.
    """
    locale = language or i18n.DEFAULT_LANGUAGE

    values = user_text_container(container).get(locale)

    if not isinstance(values, dict):
        return ""

    value = values.get(field)

    return value.strip() if isinstance(value, str) else ""


def user_text_source_locale(container) -> str:
    """
    Language the original user text was written in.

    Returns ``""`` for legacy records that do not carry the field: the original
    language is never guessed.
    """
    raw = container.get(SOURCE_LOCALE_KEY) if isinstance(container, dict) else None

    if not isinstance(raw, str):
        return ""

    return locale_registry.to_ui_locale(raw) or ""


def project_display_name(project, language=None, fallback="") -> str:
    """
    Project name that may be displayed for one language.

    The stored translation wins when it exists, otherwise the original name is
    used. ``fallback`` is the original name as the caller knows it (the project
    id in the storage dictionary); ``project["name"]`` is only used when the
    caller has nothing. Read-only: no AI, no write, no other language.
    """
    translated = user_text_value(project, PROJECT_NAME_FIELD, language)

    if translated:
        return translated

    if fallback:
        return _text(fallback)

    return _text(project.get("name")) if isinstance(project, dict) else ""


def project_needs_translation(project, language=None) -> bool:
    """
    True when the current language has no project name translation yet.

    A language identical to ``source_locale`` never needs one (the original is
    already the correct language). Legacy records without ``source_locale``
    report ``True`` so the user can still translate on demand - the original
    language is never guessed here.
    """
    locale = language or i18n.DEFAULT_LANGUAGE

    if user_text_value(project, PROJECT_NAME_FIELD, locale):
        return False

    return user_text_source_locale(project) != locale


def log_display_text(log, language=None) -> dict:
    """
    Display title / description of a daily log for one language.

    Field by field:

    * the current language translation is used when it exists;
    * otherwise the original user text is used (never empty, never another
      language's translation);
    * ``needs_translation`` tells the page layer whether the explicit
      "translate this log for the current language" action should be offered.

    Read-only: the date, the severity and every risk number are not touched.
    """
    locale = language or i18n.DEFAULT_LANGUAGE

    log = log if isinstance(log, dict) else {}

    result = {
        "source_locale": user_text_source_locale(log),
        "translated_fields": [],
        "original_fields": [],
    }

    for field in LOG_USER_TEXT_FIELDS:
        translated = user_text_value(log, field, locale)

        if translated:
            result[field] = translated
            result["translated_fields"].append(field)
        else:
            result[field] = _text(log.get(field))
            result["original_fields"].append(field)

    result["translated"] = bool(result["translated_fields"])
    result["showing_original"] = bool(result["original_fields"])
    result["needs_translation"] = bool(
        result["original_fields"]
    ) and result["source_locale"] != locale

    return result


def normalize_user_text_translation(values, fields=None) -> dict:
    """
    Validate and normalize one translation payload (pure function).

    ``fields`` restricts which fields may be stored (the project name only has
    ``name``, a daily log has ``title`` and ``description``). Rules:

    * values must be strings: anything else rejects the whole payload ``{}``;
    * empty values are dropped, a value that is too long or a multi line single
      line field rejects the whole payload (no partial write);
    * an empty result returns ``{}`` as well, so a failed translation can never
      replace working content.

    Nothing is written here and no state is read: the page layer stores the
    result, which keeps this module read-only.
    """
    allowed = tuple(fields) if fields else LOG_USER_TEXT_FIELDS

    limits = {
        PROJECT_NAME_FIELD: (MAX_PROJECT_NAME_LENGTH, True),
        "title": (MAX_LOG_TITLE_LENGTH, True),
        "description": (MAX_LOG_DESCRIPTION_LENGTH, False),
    }

    if not isinstance(values, dict):
        return {}

    normalized = {}

    for field in allowed:
        if field not in values or field not in limits:
            continue

        value = values[field]

        if not isinstance(value, str):
            return {}

        limit, single_line = limits[field]

        text = value.strip()

        if not text:
            continue

        if len(text) > limit:
            return {}

        if single_line and ("\n" in text or "\r" in text):
            return {}

        normalized[field] = text

    return normalized


def merge_user_text_translation(store, language, values, fields=None) -> dict:
    """
    Merge one translation into a ``localized_user_text`` store (pure function).

    Returns a NEW top level store in which only ``language`` is replaced; every
    other language keeps its own dictionary untouched (the input arguments are
    never mutated). Returns ``{}`` when the language is not a registered locale
    or when the payload is empty / invalid, so the caller can simply refuse to
    write anything.
    """
    locale = locale_registry.to_ui_locale(language)

    if not locale:
        return {}

    normalized = normalize_user_text_translation(values, fields)

    if not normalized:
        return {}

    store = dict(store) if isinstance(store, dict) else {}

    current = store.get(locale)
    current = dict(current) if isinstance(current, dict) else {}

    return {**store, locale: {**current, **normalized}}


def user_text_widget_key(*parts) -> str:
    """
    Streamlit widget key for a user content translation control.

    The caller passes the identity it must be bound to (``project_id``, the
    optional ``log_id`` and the ``locale``), so two projects, two logs or two
    languages can never share widget state.
    """
    label = "::".join("" if part is None else str(part) for part in parts)

    return f"user_text::{label}"


# ------------------------------------------------------------
# TRANSLATION OF USER WRITTEN TEXT (AI, user triggered)
# ------------------------------------------------------------

def build_project_name_translation_prompt(
    project,
    project_name,
    target_language=None,
) -> str:
    """
    Prompt that translates ONLY the project name.

    Country / city / type are passed in as read-only context, so the model can
    keep proper nouns readable without ever returning another field.
    """
    language_line = prompt_language_name(target_language)

    project = project if isinstance(project, dict) else {}

    original = _text(project_name) or _text(project.get("name"))

    context = ", ".join(
        part
        for part in (
            _text(project.get("city")),
            _text(project.get("country")),
            _text(project.get("type")),
        )
        if part
    ) or "(none)"

    return f"""
You are RiskPilot's project name translation agent.

Translate exactly ONE construction project name into the target language.

TARGET LANGUAGE
---------------
Write the translated project name in {language_line}.

PROJECT NAME (user written, translate only this)
-----------------------------------------------
{original}

CONTEXT (read only, never output it)
------------------------------------
{context}

FORBIDDEN
---------
- Do NOT add explanations, quotes, notes, comments or parentheses.
- Do NOT invent a different project or a different city.
- Do NOT output a score, level, date or any other field.

REQUIRED
--------
- Return one single line, at most {MAX_PROJECT_NAME_LENGTH} characters.
- Keep proper nouns (company, site, city) recognisable.

OUTPUT
------
Return only this structured JSON:

{{
    "name": "Translated project name"
}}
"""


def build_log_content_translation_prompt(log, target_language=None) -> str:
    """
    Prompt that translates ONLY the user written log title and description.

    The date, the severity and every risk number are read-only context and are
    never part of the returned JSON.
    """
    language_line = prompt_language_name(target_language)

    log = log if isinstance(log, dict) else {}

    return f"""
You are RiskPilot's construction Daily Log translation agent.

Translate the user written title and description of ONE construction daily log
into the target language. You are NOT re-analyzing the construction site.

TARGET LANGUAGE
---------------
Write both values in {language_line}.

DAILY LOG (user written, translate it)
--------------------------------------
Title:
{_text(log.get("title"))}

Description:
{_text(log.get("description"))}

CONTEXT (read only, never output it)
------------------------------------
Date: {_text(log.get("date"))}
Severity: {_text(log.get("severity"))}

FORBIDDEN
---------
- Do NOT add events, injuries, equipment, weather or consequences that are
  not in the original text.
- Do NOT change the factual meaning of any sentence.
- Do NOT output a date, score, level, severity, likelihood or exposure.
- Do NOT translate or rename any JSON field name.

REQUIRED
--------
- Keep the title on one single line (at most {MAX_LOG_TITLE_LENGTH} characters).
- Keep the description at most {MAX_LOG_DESCRIPTION_LENGTH} characters.
- Keep the wording as close to the original meaning as possible.

OUTPUT
------
Return only this structured JSON:

{{
    "title": "Translated log title",
    "description": "Translated log description"
}}
"""


def validate_project_name_translation(raw) -> dict:
    """
    Validate one AI project name translation.

    Returns ``{"name": str}`` or ``{}``: a rejected response is never stored,
    so a failed translation can never overwrite the original name or another
    language version.
    """
    if not isinstance(raw, dict):
        return {}

    name = raw.get(PROJECT_NAME_FIELD)

    if not isinstance(name, str):
        return {}

    name = name.strip()

    if not name or len(name) > MAX_PROJECT_NAME_LENGTH:
        return {}

    if "\n" in name or "\r" in name:
        return {}

    return {PROJECT_NAME_FIELD: name}


def validate_log_content_translation(raw) -> dict:
    """
    Validate one AI log translation (title + description).

    Both fields must be strings; an over long value or a multi line title is
    rejected as a whole (``{}``) instead of being stored partially. A field the
    model left out, and a field that is empty because the original is empty, are
    both accepted as empty text.
    """
    if not isinstance(raw, dict):
        return {}

    limits = {
        "title": (MAX_LOG_TITLE_LENGTH, True),
        "description": (MAX_LOG_DESCRIPTION_LENGTH, False),
    }

    result = {}

    for field, (limit, single_line) in limits.items():
        if field not in raw:
            result[field] = ""
            continue

        value = raw.get(field)

        if not isinstance(value, str):
            return {}

        value = value.strip()

        if len(value) > limit:
            return {}

        if single_line and ("\n" in value or "\r" in value):
            return {}

        result[field] = value

    if not result["title"] and not result["description"]:
        # Nothing usable: never store an empty translation.
        return {}

    return result


# ------------------------------------------------------------
# AI TEXT VERSION (generation of another language)
# ------------------------------------------------------------

def prompt_language_name(target_locale=None) -> str:
    """
    AI language name of the prompt target locale.

    The value is normalised through the locale registry and must pass the same
    gate as the interface / content language (``ui_enabled`` and not
    ``planned``); a planned, unregistered or malformed value safely falls back
    to the default locale, whose registry AI name is then used. No language is
    hard coded here, so promoting a locale in the registry is enough for its
    language versions to be generated in that language.

    Pure function: no state is read or written, nothing is cached and no
    exception is raised for any input.
    """
    locale = locale_registry.to_ui_locale(target_locale)

    return locale_registry.ai_language_name(
        locale or locale_registry.default_locale()
    )


def build_log_text_version_prompt(log, target_language=None) -> str:
    """
    Prompt that rewrites ONLY the human-readable log text.

    The returned payload is restricted to ``summary`` and the ``risks`` text
    fields, in the original order and with the original count. Numbers are
    passed in as read-only context so the model can keep the meaning stable
    without ever changing them.

    The target language is resolved by :func:`prompt_language_name`, so the
    prompt never names a language through a hard coded ``locale -> language``
    branch.
    """
    language_line = prompt_language_name(target_language)

    log = log if isinstance(log, dict) else {}

    analysis = log.get("ai_analysis")
    analysis = analysis if isinstance(analysis, dict) else {}

    risk_count = log_risk_count(analysis)

    risk_lines = []
    raw_risks = analysis.get("risks")
    raw_risks = raw_risks if isinstance(raw_risks, list) else []

    for index, item in enumerate(raw_risks):
        item = item if isinstance(item, dict) else {}
        risk_lines.append(
            f"{index + 1}. "
            f"title={_text(item.get('title'))} | "
            f"reason={_text(item.get('reason'))} | "
            f"suggestion={_text(item.get('suggestion'))} | "
            f"severity={item.get('severity')} "
            f"likelihood={item.get('likelihood')} "
            f"exposure={item.get('exposure')}"
        )

    if not risk_lines:
        risk_lines.append("(none)")

    return f"""
You are RiskPilot's construction Daily Log translation and rewriting agent.

Rewrite the human-readable text of ONE stored daily log analysis into the
target language. You are NOT re-analyzing the construction site.

TARGET LANGUAGE
---------------
Write every human-readable text value in {language_line}.

FORBIDDEN
---------
- Do NOT add, remove, merge or split risks.
- Do NOT invent events, injuries, equipment failures, weather or
  consequences that are not in the original text.
- Do NOT change the factual meaning of any sentence.
- Do NOT output a 0-100 score.
- Do NOT output level, severity, likelihood, exposure or raw_risk.
- Do NOT translate or rename any JSON field name.

REQUIRED
--------
- Return exactly {risk_count} risk objects, in the same order.
- Keep every JSON field name in English.
- Keep the text as close to the original meaning as possible.

DAILY LOG (user written, shown for context only, do not translate it)
--------------------------------------------------------------------
Title:
{_text(log.get("title"))}

Description:
{_text(log.get("description"))}

STORED ANALYSIS TEXT (rewrite this)
-----------------------------------
Summary:
{_text(analysis.get("summary"))}

Risks ({risk_count}):
{chr(10).join(risk_lines)}

OUTPUT
------
Return only this structured JSON:

{{
    "summary": "Rewritten summary",
    "risks": [
        {{
            "title": "Rewritten risk name",
            "reason": "Rewritten evidence",
            "suggestion": "Rewritten risk control"
        }}
    ]
}}
"""


def validate_log_text_version(raw, expected_risk_count) -> dict:
    """
    Validate one AI written language version before it may be archived.

    Returns a safe ``{"summary": str, "risks": [...]}`` payload, or ``{}``
    when the response must be rejected. A rejected response is never
    written to the archive and never touches the stored analysis.
    """
    if not isinstance(raw, dict):
        return {}

    summary = raw.get("summary")

    if not isinstance(summary, str):
        return {}

    risks = raw.get("risks")

    if not isinstance(risks, list):
        return {}

    try:
        expected = int(expected_risk_count)
    except (TypeError, ValueError):
        return {}

    if len(risks) != expected:
        return {}

    normalized = []

    for item in risks:
        if not isinstance(item, dict):
            return {}

        normalized.append(_risk_text(item))

    return {"summary": summary.strip(), "risks": normalized}


def is_valid_log_text_version(raw, expected_risk_count) -> bool:
    """True when :func:`validate_log_text_version` would accept ``raw``."""
    return bool(validate_log_text_version(raw, expected_risk_count))


def generated_text_payload(validated) -> Optional[dict]:
    """
    Payload that may be handed to ``ai_text_store``.

    Returns ``None`` for an empty / invalid validation result so that no
    empty archive entry is ever created.
    """
    if not isinstance(validated, dict):
        return None

    if "summary" not in validated or "risks" not in validated:
        return None

    return validated
