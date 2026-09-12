# -*- coding: utf-8 -*-
"""
RiskPilot interface-language state kernel.

This module is deliberately free of any Streamlit runtime dependency. Every
function takes the state mapping explicitly (``st.session_state`` in
``app.py``, a plain ``dict`` in tests) and only reads / writes the two keys
it owns:

    ui_lang               internal interface language (a canonical BCP 47 code
                          that is currently UI selectable, e.g. "en" / "zh-CN")
    ui_language_selector  the interface-language <selectbox> widget value

Single-writer rules (the reason this module exists):

    * ``ui_lang`` is written only by :func:`init_language` (first session) and
      by :func:`set_language`, which is reached only through the interface
      language selector callback (validated choice).
    * ``ui_language_selector`` is written only by :func:`restore_selector`,
      which copies ``ui_lang`` -> selector. It never writes the other way
      round, so a dropped / missing widget state can never reset the
      interface language.
    * :func:`set_active_project` moves project state only; it must never
      touch ``ui_lang`` or the selector.

Which codes are valid comes from ``utils/locale_registry.py`` (backed by
``locales/manifest.json``) so the language list, the selector and the tests
share one source of truth. A broken manifest degrades to English-only rather
than raising.
"""

from __future__ import annotations

from typing import Any, Dict, MutableMapping, Optional

from utils.locale_registry import (
    DEFAULT_LOCALE,
    default_locale,
    enabled_ui_locale_map,
    is_ui_locale,
    sanitize_locale_candidate,
    to_ui_locale,
    ui_locale_or_default,
)

#: Ordered map of UI selectable language codes to their native display names.
#: Derived from the locale registry - never hand written here.
SUPPORTED_LANGUAGES: Dict[str, str] = enabled_ui_locale_map()

#: Language used when nothing valid is stored or can be resolved.
DEFAULT_LANGUAGE = default_locale()

#: Built-in last-resort code (the registry default is normally the same).
BUILTIN_LANGUAGE = DEFAULT_LOCALE

#: session_state key that holds the interface language.
LANGUAGE_KEY = "ui_lang"

#: Backwards-compatible alias kept for i18n.py.
SESSION_KEY = LANGUAGE_KEY

#: session_state key that holds the language selector widget value.
SELECTOR_KEY = "ui_language_selector"


# ============================================================
# VALIDATION / NORMALISATION
# ============================================================


def is_supported_language(language_code: Any) -> bool:
    """True when ``language_code`` is a currently UI selectable language."""
    return is_ui_locale(language_code)


def normalize_language(language_code: Any) -> str:
    """Coerce any value to a UI selectable language code (default: ``en``)."""
    return ui_locale_or_default(language_code)


def resolve_initial_language(
    url_language: Any = None,
    browser_language: Any = None,
    fallback: Optional[str] = None,
) -> str:
    """
    First-session language priority: URL -> browser -> default.

    Both candidates are normalised through the registry, so a *planned* or
    unknown candidate is skipped instead of being accepted. Pure function:
    nothing is read from or written to any state mapping.
    """
    for candidate in (url_language, browser_language):
        resolved = to_ui_locale(candidate)
        if resolved is not None:
            return resolved

    if fallback is not None:
        resolved_fallback = to_ui_locale(fallback)
        if resolved_fallback is not None:
            return resolved_fallback

    return DEFAULT_LANGUAGE


# ============================================================
# READING
# ============================================================


def get_language(state: MutableMapping) -> str:
    """
    Read the current interface language. Never writes to ``state``.
    """
    current = state.get(LANGUAGE_KEY)

    if is_supported_language(current):
        return current

    return DEFAULT_LANGUAGE


# ============================================================
# WRITING (two call sites only)
# ============================================================


def init_language(
    state: MutableMapping,
    url_language: Any = None,
    browser_language: Any = None,
) -> str:
    """
    Initialise ``ui_lang`` for a new session.

    Idempotent: an already valid ``ui_lang`` is kept and never overwritten,
    so a later call (rerun, page switch, project change) cannot reset the
    language the user is already using - and the browser locale is therefore
    only ever consulted during the very first initialisation.
    """
    current = state.get(LANGUAGE_KEY)

    if is_supported_language(current):
        return current

    resolved = resolve_initial_language(url_language, browser_language)
    state[LANGUAGE_KEY] = resolved
    return resolved


def set_language(state: MutableMapping, language_code: Any) -> str:
    """
    Write ``ui_lang`` explicitly.

    Only a currently UI selectable locale is accepted. Anything else is
    *rejected*: the stored language is returned unchanged, so a stale widget,
    a bad URL or a planned locale can never downgrade the user's language.

    The only allowed caller is the interface language selector callback in
    ``app.py`` (enforced by the static guards in the test suite).
    """
    resolved = to_ui_locale(language_code)

    if resolved is None:
        return get_language(state)

    state[LANGUAGE_KEY] = resolved
    return resolved


# ============================================================
# WIDGET STATE (one-way: ui_lang -> selector)
# ============================================================


def restore_selector(state: MutableMapping) -> str:
    """
    Seed / repair the selector widget value from ``ui_lang``.

    Only ``ui_language_selector`` is ever written here. ``ui_lang`` is only
    read, so a missing or stale widget value can never change the interface
    language.
    """
    language = get_language(state)
    selector = state.get(SELECTOR_KEY)

    if not is_supported_language(selector):
        state[SELECTOR_KEY] = language

    return state[SELECTOR_KEY]


# ============================================================
# PROJECT STATE (must never touch the language)
# ============================================================


def set_active_project(
    state: MutableMapping,
    project_name: Any,
    view: str = "overview",
) -> MutableMapping:
    """
    Apply a project open / switch.

    Only ``selected_project`` and ``dashboard_view`` change. ``ui_lang``,
    the selector, the URL language and the report state are left untouched.
    """
    state["selected_project"] = project_name
    state["dashboard_view"] = view
    return state
