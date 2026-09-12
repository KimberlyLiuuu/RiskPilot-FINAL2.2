# -*- coding: utf-8 -*-
"""
RiskPilot locale registry.

Single source of truth for

    * which BCP 47 locale codes exist in the product,
    * which of them the UI may actually offer right now (``ui_enabled``),
    * their native / English / AI names, text direction and fallback chain.

Why a JSON manifest plus a Python module
----------------------------------------
``locales/manifest.json`` is the *data* (one place to add or promote a
language) and this module is the *behaviour* (validation, normalisation and
lookup). Keeping the list in one data file means the UI selector, ``i18n``,
the audit script and the tests can never drift apart, while all the parsing
and safety rules stay in ordinary testable Python.

Safety rules
------------
* Nothing here imports Streamlit, ``i18n`` or the session-state kernel, so it
  can be unit-tested with a plain ``dict`` and cannot create an import cycle.
* A malformed, missing or partially invalid manifest never raises and never
  blanks the page: :func:`load_locale_registry` degrades to a built-in
  English-only registry. The problems are still available through
  :func:`registry_problems` so CI (``scripts/i18n_audit.py``) can fail loudly.
* ``status="planned"`` locales are *registered* metadata only. They are never
  UI selectable and never loadable as a UI language, so a planned locale with
  no ``<code>.json`` yet is not an error.
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: Directory holding ``manifest.json`` and the per-language JSON files.
LOCALES_DIR = Path(__file__).resolve().parent.parent / "locales"

#: Registry data file.
MANIFEST_FILENAME = "manifest.json"
MANIFEST_PATH = LOCALES_DIR / MANIFEST_FILENAME

#: Built-in last-resort code. Used when nothing else can be resolved.
DEFAULT_LOCALE = "en"

#: Allowed ``status`` values.
VALID_STATUSES: Tuple[str, ...] = ("stable", "experimental", "planned")

#: Allowed ``direction`` values.
VALID_DIRECTIONS: Tuple[str, ...] = ("ltr", "rtl")

#: Anything longer than this is treated as hostile input and rejected.
MAX_LOCALE_TAG_LENGTH = 64

#: Regions whose ``zh`` variants are Traditional Chinese and therefore must
#: NOT be silently mapped to ``zh-CN`` (Simplified).
_TRADITIONAL_CHINESE_REGIONS = frozenset({"tw", "hk", "mo"})


# ============================================================
# DATA TYPE
# ============================================================


@dataclass(frozen=True)
class LocaleInfo:
    """One registry entry."""

    code: str
    native_name: str
    english_name: str
    fallback: Optional[str]
    direction: str
    ai_language_name: str
    status: str
    ui_enabled: bool
    is_default: bool = False

    @property
    def selectable(self) -> bool:
        """True when this locale may appear in the UI language selector."""
        return self.ui_enabled and self.status != "planned"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "native_name": self.native_name,
            "english_name": self.english_name,
            "fallback": self.fallback,
            "direction": self.direction,
            "ai_language_name": self.ai_language_name,
            "status": self.status,
            "ui_enabled": self.ui_enabled,
            "is_default": self.is_default,
        }


#: Built-in registry used when the manifest cannot be trusted. English only,
#: which is the only language guaranteed to be present in ``locales/``.
_FALLBACK_REGISTRY: Tuple[LocaleInfo, ...] = (
    LocaleInfo(
        code=DEFAULT_LOCALE,
        native_name="English",
        english_name="English",
        fallback=None,
        direction="ltr",
        ai_language_name="English",
        status="stable",
        ui_enabled=True,
        is_default=True,
    ),
)

#: Cached registry per manifest path.
_CACHE: Dict[str, Tuple[LocaleInfo, ...]] = {}

#: Validation problems of the last load, per manifest path.
_PROBLEMS: Dict[str, List[str]] = {}


# ============================================================
# MANIFEST READING / VALIDATION
# ============================================================


def _read_manifest(path: Path) -> Any:
    """Read the raw manifest; return ``None`` when unreadable."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return None


def _code_set(entries: Sequence[dict]) -> List[str]:
    codes: List[str] = []
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get("code"), str):
            codes.append(entry["code"].strip())
    return codes


def _detect_fallback_cycle(
    codes: Iterable[str],
    fallback_map: Dict[str, Optional[str]],
) -> Optional[Tuple[str, ...]]:
    """Return one fallback cycle as a tuple, or ``None`` when acyclic."""
    for start in codes:
        seen: List[str] = []
        current: Optional[str] = start
        while current is not None:
            if current in seen:
                return tuple(seen[seen.index(current):] + [current])
            seen.append(current)
            current = fallback_map.get(current)
    return None


def validate_locale_entries(entries: Any) -> List[str]:
    """
    Validate a decoded manifest. Returns a list of human readable problems;
    an empty list means the manifest is valid.

    Pure function (no filesystem access) so tests can feed it broken input.
    """
    problems: List[str] = []

    if not isinstance(entries, list):
        return ["manifest 顶层必须是数组"]

    if not entries:
        return ["manifest 不能为空"]

    codes = _code_set(entries)

    # --- per-entry field checks ---
    for index, entry in enumerate(entries):
        label = f"第 {index} 项"

        if not isinstance(entry, dict):
            problems.append(f"{label}: 必须是对象")
            continue

        code = entry.get("code")
        if not isinstance(code, str) or not code.strip():
            problems.append(f"{label}: code 缺失或为空")
            continue

        code = code.strip()
        label = f"{label} ({code})"

        for field in ("native_name", "english_name", "ai_language_name"):
            value = entry.get(field)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{label}: {field} 不得为空")

        direction = entry.get("direction")
        if direction not in VALID_DIRECTIONS:
            problems.append(
                f"{label}: direction 必须是 {list(VALID_DIRECTIONS)}，实际 {direction!r}"
            )

        status = entry.get("status")
        if status not in VALID_STATUSES:
            problems.append(
                f"{label}: status 必须是 {list(VALID_STATUSES)}，实际 {status!r}"
            )

        ui_enabled = entry.get("ui_enabled")
        if not isinstance(ui_enabled, bool):
            problems.append(f"{label}: ui_enabled 必须是布尔值")

        if status == "planned" and ui_enabled is True:
            problems.append(f"{label}: planned locale 不得 ui_enabled=true")

        if not isinstance(entry.get("is_default", False), bool):
            problems.append(f"{label}: is_default 必须是布尔值")

    # --- uniqueness ---
    seen: Dict[str, int] = {}
    for code in codes:
        seen[code] = seen.get(code, 0) + 1
    for code, count in seen.items():
        if count > 1:
            problems.append(f"code 重复: {code}（{count} 次）")

    unique_codes = [code for code in codes if seen.get(code) == 1]
    known = set(unique_codes)

    # --- fallback references ---
    fallback_map: Dict[str, Optional[str]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        code = entry.get("code")
        if not isinstance(code, str) or code.strip() not in known:
            continue
        fallback = entry.get("fallback")
        if fallback is None:
            fallback_map[code.strip()] = None
            continue
        if not isinstance(fallback, str) or not fallback.strip():
            problems.append(f"{code.strip()}: fallback 必须是 null 或非空字符串")
            fallback_map[code.strip()] = None
            continue
        fallback = fallback.strip()
        if fallback == code.strip():
            problems.append(f"{code.strip()}: fallback 不得指向自身")
            fallback_map[code.strip()] = None
            continue
        if fallback not in known:
            problems.append(f"{code.strip()}: fallback 引用了未注册的 locale {fallback!r}")
            fallback_map[code.strip()] = None
            continue
        fallback_map[code.strip()] = fallback

    # --- fallback cycles ---
    cycle = _detect_fallback_cycle(unique_codes, fallback_map)
    if cycle is not None:
        problems.append("fallback 形成循环: " + " -> ".join(cycle))

    # --- exactly one default, and it must be selectable ---
    defaults = [
        entry.get("code", "").strip()
        for entry in entries
        if isinstance(entry, dict) and entry.get("is_default") is True
    ]
    if len(defaults) > 1:
        problems.append(f"只能有一个 is_default，实际 {defaults}")
    elif len(defaults) == 0:
        problems.append("必须有一个 is_default=true 的 locale")
    else:
        default_code = defaults[0]
        default_entry = next(
            (e for e in entries if isinstance(e, dict) and e.get("code", "").strip() == default_code),
            None,
        )
        if isinstance(default_entry, dict) and not (
            default_entry.get("ui_enabled") is True
            and default_entry.get("status") != "planned"
        ):
            problems.append(f"默认 locale {default_code} 必须可用（ui_enabled 且非 planned）")

    return problems


def _to_info(entry: dict) -> LocaleInfo:
    fallback = entry.get("fallback")
    if isinstance(fallback, str):
        fallback = fallback.strip() or None
    else:
        fallback = None

    return LocaleInfo(
        code=str(entry["code"]).strip(),
        native_name=str(entry["native_name"]).strip(),
        english_name=str(entry["english_name"]).strip(),
        fallback=fallback,
        direction=str(entry["direction"]),
        ai_language_name=str(entry["ai_language_name"]).strip(),
        status=str(entry["status"]),
        ui_enabled=bool(entry["ui_enabled"]),
        is_default=entry.get("is_default") is True,
    )


# ============================================================
# LOADING
# ============================================================


def load_locale_registry(path: Optional[Any] = None) -> Tuple[LocaleInfo, ...]:
    """
    Load and cache the locale registry.

    Never raises: an unreadable or invalid manifest degrades to the built-in
    English-only registry so the application still renders.
    """
    manifest_path = Path(path) if path is not None else MANIFEST_PATH
    key = str(manifest_path)

    cached = _CACHE.get(key)
    if cached is not None:
        return cached

    raw = _read_manifest(manifest_path)
    problems = validate_locale_entries(raw)

    if problems:
        registry = _FALLBACK_REGISTRY
    else:
        registry = tuple(_to_info(entry) for entry in raw)
        if not any(info.selectable for info in registry):
            problems = ["manifest 中没有任何可用的 UI locale"]
            registry = _FALLBACK_REGISTRY

    _PROBLEMS[key] = list(problems)
    _CACHE[key] = registry
    return registry


def reload_registry(path: Optional[Any] = None) -> Tuple[LocaleInfo, ...]:
    """Drop the cache for ``path`` (or every path) and load again."""
    if path is None:
        _CACHE.clear()
        _PROBLEMS.clear()
    else:
        key = str(Path(path))
        _CACHE.pop(key, None)
        _PROBLEMS.pop(key, None)
    return load_locale_registry(path)


def registry_problems(path: Optional[Any] = None) -> List[str]:
    """
    Validation problems of the manifest backing this registry.

    An empty list means the manifest is valid. Callers that need to *report*
    registry damage (CI, audit, tests) use this instead of relying on the
    silent English-only degradation.
    """
    load_locale_registry(path)
    return list(_PROBLEMS.get(str(Path(path) if path is not None else MANIFEST_PATH), []))


def _enabled(registry: Sequence[LocaleInfo]) -> Tuple[LocaleInfo, ...]:
    return tuple(info for info in registry if info.selectable)


def default_locale(path: Optional[Any] = None) -> str:
    """The manifest's default locale code (built-in ``en`` as last resort)."""
    registry = load_locale_registry(path)
    for info in registry:
        if info.is_default and info.selectable:
            return info.code
    for info in _enabled(registry):
        return info.code
    return DEFAULT_LOCALE


def enabled_ui_locales(path: Optional[Any] = None) -> Tuple[str, ...]:
    """
    Codes the UI may offer right now, in manifest order.

    Planned locales never appear here, so the selector can never offer a
    language whose translations do not exist yet.
    """
    return tuple(info.code for info in _enabled(load_locale_registry(path)))


def enabled_ui_locale_map(path: Optional[Any] = None) -> Dict[str, str]:
    """``{code: native_name}`` for the UI selectable locales."""
    return {info.code: info.native_name for info in _enabled(load_locale_registry(path))}


def all_locale_codes(path: Optional[Any] = None) -> Tuple[str, ...]:
    """Every registered code, including planned ones."""
    return tuple(info.code for info in load_locale_registry(path))


def get_locale(code: Any, path: Optional[Any] = None) -> Optional[LocaleInfo]:
    """Registry entry for ``code`` (alias tolerant), or ``None``."""
    canonical = canonicalize_locale(code, path)
    if canonical is None:
        return None
    for info in load_locale_registry(path):
        if info.code == canonical:
            return info
    return None


# ============================================================
# TEXT SANITISATION
# ============================================================


def sanitize_locale_candidate(value: Any) -> Optional[str]:
    """
    Defensively turn an untrusted value (URL query param, browser locale,
    cookie, ...) into a candidate locale string.

    Accepts ``str`` and sequences of ``str`` (a repeated query parameter can
    surface as a list). Returns ``None`` for anything empty, blank, purely
    control / format characters, or longer than :data:`MAX_LOCALE_TAG_LENGTH`.
    Never raises.
    """
    if value is None:
        return None

    if isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            cleaned = sanitize_locale_candidate(item)
            if cleaned:
                return cleaned
        return None

    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", "ignore")
        except Exception:
            return None

    if not isinstance(value, str):
        try:
            value = str(value)
        except Exception:
            return None

    # Drop control / format / surrogate / private-use characters: this
    # removes the BOM, zero-width spaces and stray newlines.
    cleaned = "".join(
        char for char in value if unicodedata.category(char)[0] != "C"
    ).strip()

    if not cleaned:
        return None

    if len(cleaned) > MAX_LOCALE_TAG_LENGTH:
        return None

    return cleaned


# ============================================================
# CODE NORMALISATION
# ============================================================


def _is_alpha(value: str) -> bool:
    return bool(value) and value.isascii() and value.isalpha()


def _canonical_case(tag: str) -> str:
    """BCP 47 canonical casing: language lower, script Title, region UPPER."""
    parts = tag.split("-")
    out: List[str] = []

    for index, part in enumerate(parts):
        if index == 0:
            out.append(part.lower())
        elif len(part) == 4 and _is_alpha(part):
            out.append(part.capitalize())
        elif len(part) == 2 and _is_alpha(part):
            out.append(part.upper())
        elif len(part) == 3 and part.isdigit():
            out.append(part)
        else:
            out.append(part.lower())

    return "-".join(out)


def _is_wellformed_tag(tag: str) -> bool:
    parts = tag.split("-")

    if not parts or not (2 <= len(parts[0]) <= 8) or not _is_alpha(parts[0]):
        return False

    for part in parts[1:]:
        if not (1 <= len(part) <= 8) or not part.isalnum():
            return False

    return True


def _split_tag(tag: str) -> Tuple[str, Optional[str], Optional[str]]:
    """Split a canonical tag into ``(language, script, region)``."""
    parts = tag.split("-")
    language = parts[0]
    script: Optional[str] = None
    region: Optional[str] = None

    for part in parts[1:]:
        if script is None and len(part) == 4 and _is_alpha(part):
            script = part.lower()
            continue
        if region is None and (
            (len(part) == 2 and _is_alpha(part))
            or (len(part) == 3 and part.isdigit())
        ):
            region = part.lower()

    return language, script, region


def canonicalize_locale(code: Any, path: Optional[Any] = None) -> Optional[str]:
    """
    Resolve an alias / browser tag to a **registered** canonical BCP 47 code.

    Returns ``None`` ("und", language undetermined) when the input maps to no
    registered locale. Notable rules:

    * ``en``, ``en-US``, ``en-GB``      -> ``en``      (region-less entry)
    * ``zh``, ``zh-CN``, ``zh_Hans``    -> ``zh-CN``
    * ``zh-Hant``, ``zh-TW``, ``zh-HK`` -> ``None``    (Traditional, not zh-CN)
    * ``pt-BR``, ``pt_BR``              -> ``pt-BR``
    * ``pt-PT``                         -> ``None``    (never silently pt-BR)
    * ``es``, ``es-MX``                 -> ``es``
    * anything unknown                  -> ``None``
    """
    cleaned = sanitize_locale_candidate(code)
    if cleaned is None:
        return None

    tag = _canonical_case(cleaned.replace("_", "-"))
    if not _is_wellformed_tag(tag):
        return None

    registry = load_locale_registry(path)
    known = {info.code for info in registry}

    language, script, region = _split_tag(tag)

    # Chinese needs script / region awareness: Simplified and Traditional
    # share the "zh" language subtag but are different locales.
    if language == "zh":
        if script == "hant" or (region is not None and region in _TRADITIONAL_CHINESE_REGIONS):
            return None
        return "zh-CN" if "zh-CN" in known else None

    if tag in known:
        return tag

    # Same language, region-less registry entry: "en-US" -> "en".
    # A region-specific entry is never picked this way, so "pt-PT" can never
    # collapse into "pt-BR".
    for info in registry:
        base_language, base_script, base_region = _split_tag(info.code)
        if (
            base_language == language
            and base_region is None
            and base_script is None
        ):
            return info.code

    return None


def normalize_locale(code: Any, path: Optional[Any] = None) -> str:
    """
    Canonical registered code, falling back to the default locale.

    This is the *registered* space (``es`` is a valid result even while it is
    still planned). Use :func:`ui_locale_or_default` when the result must be
    safe to render.
    """
    return canonicalize_locale(code, path) or default_locale(path)


def is_registered_locale(code: Any, path: Optional[Any] = None) -> bool:
    """True when ``code`` resolves to a registered locale (planned included)."""
    return canonicalize_locale(code, path) is not None


def to_ui_locale(code: Any, path: Optional[Any] = None) -> Optional[str]:
    """
    Canonical code **only** when the UI may actually use it, else ``None``.

    This is the gate that keeps planned locales out of the selector and out
    of ``ui_lang``.
    """
    canonical = canonicalize_locale(code, path)
    if canonical is None:
        return None
    for info in _enabled(load_locale_registry(path)):
        if info.code == canonical:
            return canonical
    return None


def is_ui_locale(code: Any, path: Optional[Any] = None) -> bool:
    """True when ``code`` is a registered *and* currently selectable locale."""
    return to_ui_locale(code, path) is not None


def ui_locale_or_default(code: Any, path: Optional[Any] = None) -> str:
    """Canonical selectable locale, else the default locale (``en``)."""
    return to_ui_locale(code, path) or default_locale(path)


# ============================================================
# METADATA LOOKUPS
# ============================================================


def _entry(code: Any, path: Optional[Any]) -> Optional[LocaleInfo]:
    registry = load_locale_registry(path)
    canonical = canonicalize_locale(code, path)
    if canonical is None:
        return None
    for info in registry:
        if info.code == canonical:
            return info
    return None


def locale_display_name(code: Any, display_locale: Optional[str] = None) -> str:
    """
    Human readable label for ``code``.

    Stage 2 always returns the **native** name (``English``, ``简体中文``):
    a native name is recognizable to the user who needs it and must never be
    itself translated into another language. ``display_locale`` exists so
    call sites are already written for the localized labels stage 5 may add;
    it is intentionally unused today. Unregistered codes are returned as-is
    so legacy data keeps rendering.
    """
    info = _entry(code, None)
    if info is None:
        return "" if code is None else str(code)
    return info.native_name


def english_name(code: Any) -> str:
    """English name of a registered locale (useful for logs and tests)."""
    info = _entry(code, None)
    return info.english_name if info else ""


def ai_language_name(code: Any) -> str:
    """
    Name to put inside an AI prompt for ``code``.

    Falls back to the default locale's AI name so a prompt can always be
    built. Stage 2 does not change any prompt; this only exposes metadata.
    """
    info = _entry(code, None)
    if info is not None:
        return info.ai_language_name
    fallback = _entry(default_locale(), None)
    return fallback.ai_language_name if fallback else "English"


def text_direction(code: Any) -> str:
    """``"ltr"`` or ``"rtl"``. Unknown codes are treated as ``ltr``."""
    info = _entry(code, None)
    return info.direction if info else "ltr"


def fallback_chain(code: Any, path: Optional[Any] = None) -> Tuple[str, ...]:
    """
    The locale followed by its fallback chain.

    ``zh-CN -> en`` becomes ``("zh-CN", "en")``. Cycles are cut short and
    unknown codes collapse to ``(default,)``.
    """
    registry = load_locale_registry(path)
    by_code = {info.code: info for info in registry}

    start = canonicalize_locale(code, path) or default_locale(path)

    chain: List[str] = []
    current: Optional[str] = start

    while current is not None and current not in chain:
        chain.append(current)
        info = by_code.get(current)
        if info is None:
            break
        target = info.fallback
        current = target if target in by_code else None

    return tuple(chain)


def status(code: Any) -> str:
    """Registry status of ``code`` (``stable`` / ``experimental`` / ``planned``)."""
    info = _entry(code, None)
    return info.status if info else "unknown"


def missing_locale_files(locales_dir: Optional[Any] = None) -> List[str]:
    """
    Selectable locales whose ``<code>.json`` does not exist.

    Planned locales are not checked: they are registered metadata and are not
    expected to have translations yet.
    """
    directory = Path(locales_dir) if locales_dir is not None else LOCALES_DIR
    return [
        code
        for code in enabled_ui_locales()
        if not (directory / f"{code}.json").is_file()
    ]


def registry_summary(path: Optional[Any] = None) -> List[Dict[str, Any]]:
    """Compact, report friendly view of the registry (used by the audit)."""
    return [info.to_dict() for info in load_locale_registry(path)]
