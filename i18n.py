# -*- coding: utf-8 -*-
"""
RiskPilot lightweight UI localization layer.

The set of UI languages is NOT defined here. It comes from
``utils/locale_registry.py`` (backed by ``locales/manifest.json``); this
module only re-exports it as ``SUPPORTED_LANGUAGES`` for existing importers.
At the time of writing the registry marks ``en``, ``zh-CN``, ``es``,
``pt-BR``, ``fr`` and ``sw`` as ``ui_enabled``; ``de``, ``ar``, ``hi``,
``ja`` and ``ko`` are registered as ``planned`` and are therefore never
selectable and never loaded.

Design notes
------------
* The UI language state lives in ``st.session_state["ui_lang"]`` and always
  holds a canonical BCP 47 code.
* It is fully independent from the AI report content language
  (``risk_report_language`` in app.py) and from project data.
* ``ui_lang`` is written in exactly two places: the first-session
  initialisation (:func:`init_ui_language`) and the interface language
  selector callback in app.py. Business code only reads it.
* ``ui_lang`` is intentionally NOT used as a Streamlit widget key.
  Streamlit cleans up widget state for elements that are not
  re-registered in a run (a page switch remounts them under a new element
  id), which would let a widget default silently reset the language.
* Risk levels LOW / MEDIUM / HIGH are intentionally NOT translated in
  this module: they are stored as-is in project data. UI code may show
  translated labels separately when it is safe to do so.
* Missing keys never crash the page. Lookup order is:
  current locale -> English (en) -> the raw key itself.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

try:
    import streamlit as st
except Exception:  # pragma: no cover - allow import outside Streamlit
    st = None

# The interface-language state itself lives in a Streamlit-free module so it
# can be unit-tested with a plain mapping. i18n only exposes it to the UI and
# layers the translation on top.
#
# SUPPORTED_LANGUAGES / DEFAULT_LANGUAGE are NOT re-declared here: they are
# derived from utils/locale_registry.py (backed by locales/manifest.json), so
# the language list exists in exactly one place. Adding a language is a
# manifest edit, never a code edit.
from utils.ui_language_state import (
    DEFAULT_LANGUAGE,
    SESSION_KEY,
    SUPPORTED_LANGUAGES,
    get_language as _get_language_state,
    init_language as _init_language_state,
    is_supported_language as _is_supported_language,
    normalize_language as _normalize,
    set_language as _set_language_state,
)

#: Public alias used by app.py for the first-session initialisation.
_DEFAULT_LANGUAGE = DEFAULT_LANGUAGE

#: session_state key that holds the interface language.
_SESSION_KEY = SESSION_KEY

LOCALES_DIR = Path(__file__).resolve().parent / "locales"

_cache: Dict[str, Dict[str, str]] = {}


def _read_json(path: Path) -> dict:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return data if isinstance(data, dict) else {}
    except Exception:
        # A malformed or missing locale file must never crash the page.
        return {}


def _flatten(data: dict, prefix: str = "") -> Dict[str, str]:
    flat: Dict[str, str] = {}
    for key, value in data.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(_flatten(value, dotted))
        else:
            flat[dotted] = str(value)
    return flat


def load_locale(language_code=None) -> Dict[str, str]:
    """
    Load and cache the flattened translation dict for a language.

    English entries are always merged in as fallback so missing
    translations degrade gracefully instead of raising.
    """
    code = _normalize(
        language_code if language_code is not None
        else get_current_language()
    )

    if code not in _cache:
        merged: Dict[str, str] = {}

        if code != _DEFAULT_LANGUAGE:
            merged.update(
                _flatten(_read_json(LOCALES_DIR / f"{_DEFAULT_LANGUAGE}.json"))
            )

        merged.update(_flatten(_read_json(LOCALES_DIR / f"{code}.json")))

        _cache[code] = merged

    return _cache[code]


def get_current_language() -> str:
    """Return the active UI language code (default: ``"en"``)."""
    if st is not None:
        return _get_language_state(st.session_state)
    return _DEFAULT_LANGUAGE


def is_supported_language(language_code) -> bool:
    """True when ``language_code`` is one of the supported UI languages."""
    return _is_supported_language(language_code)


def set_current_language(language_code: str) -> str:
    """
    Store the UI language code in session state and return it.

    Only a currently UI selectable locale is accepted; anything else is
    rejected and the stored language is returned unchanged.

    The only allowed caller is the interface language selector callback in
    ``app.py``. Everything else must treat ``ui_lang`` as read-only, so that
    opening a project, switching projects, generating a report, running an
    AI analysis or reading historical data can never change the interface
    language.
    """
    if st is not None:
        return _set_language_state(st.session_state, language_code)
    return _normalize(language_code)


def init_ui_language(url_language=None, browser_language=None) -> str:
    """
    Initialise the UI language exactly once per session.

    Priority on the very first run: validated URL ``lang`` -> browser locale
    -> default (``en``). Both candidates are normalised through the locale
    registry, so a planned or unknown value is skipped rather than accepted.

    Idempotent: once ``ui_lang`` holds a selectable locale, reruns, page
    switches and project changes keep it and the candidates are ignored -
    which is also what limits browser detection to the first initialisation.
    """
    if st is not None:
        return _init_language_state(
            st.session_state,
            url_language,
            browser_language,
        )
    return _DEFAULT_LANGUAGE


def t(key: str, **kwargs) -> str:
    """
    Translate ``key`` with the active UI language.

    Fallback order: current locale -> English -> the raw key text.
    Optional ``kwargs`` are applied with ``str.format`` when present.
    Formatting failures never raise.
    """
    text = load_locale().get(key, key)

    if kwargs:
        try:
            text = text.format(**kwargs)
        except Exception:
            # Keep the raw text if placeholder formatting fails.
            pass

    return text


#: Internal risk level constants. They are stored unchanged in project data.
_RISK_LEVELS = ("LOW", "MEDIUM", "HIGH")


def risk_label(level, fallback: str = "") -> str:
    """
    Display-only label for an internal risk level.

    ``LOW`` / ``MEDIUM`` / ``HIGH`` remain the internal values used by the
    risk calculations and by projects.json. Only the rendered text is
    localized here, and unknown values are returned unchanged so legacy
    data keeps working.
    """
    raw = "" if level is None else str(level).strip()

    if raw:
        code = raw.upper()
        if code in _RISK_LEVELS:
            key = f"risk.level.{code.lower()}"
            text = t(key)
            if text != key:
                return text
        return raw

    return fallback or str(level or "")


def mapped_label(value, mapping, fallback: str = "") -> str:
    """
    Display-only localization of an internal value through a mapping.

    ``mapping`` maps an internal value (for example a project type or a
    hazard name) to a translation key. Values that are not present in the
    mapping are returned unchanged, so older project data that still holds
    the original English values keeps rendering correctly.
    """
    raw = "" if value is None else str(value).strip()

    if raw:
        key = mapping.get(raw)
        if key:
            text = t(key)
            if text != key:
                return text
        return raw

    return fallback or str(value or "")
