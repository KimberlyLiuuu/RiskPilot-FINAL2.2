# -*- coding: utf-8 -*-
"""
RiskPilot Vision Risk display layer (Streamlit-free).
=====================================================

Globalization stage 4B splits the Vision Risk result into **two layers**:

1. ``vision_detection`` —— the language-independent *canonical detection*
   (:func:`normalize_vision_detection` / :func:`validate_vision_detection`).
   It carries only recognition facts: stable ontology keys, normalized
   locations, confidence, the derived internal level / penalty and the
   detection fingerprint. It is stored on the project itself
   (:data:`DETECTION_FIELD`) and is **never** duplicated per language, never
   inside ``localized_content``.

2. ``vision_analysis`` —— the *human readable* text layer stored through the
   stage 3 unified content protocol (``localized_content``) with one record
   per (project, image) and one payload per content locale. It carries only
   natural language (``summary`` plus per finding ``title`` / ``reason`` /
   ``recommendation``).

Hard rules enforced here
------------------------
* This module imports no Streamlit, never touches ``st.session_state``,
  never reads an image path, never calls the AI, never calls ``persist()``
  and never writes a file.
* Every read function is pure: it never mutates the detection or the project
  it is given, and it always returns freshly built objects.
* Recognition facts (``confidence`` / ``penalty`` / ``level`` / ``location``
  / object and category keys / finding count and order / image hash / model
  id) can never be produced, changed or overwritten by a text payload.
  :func:`validate_vision_text_payload` rejects any payload that tries.
* The **detection cache key never contains a locale** (recognition facts do
  not depend on language); the **text cache key always contains one**.

Identity
--------
* image hash        : SHA-256 of the uploaded image bytes
                      (:func:`compute_image_hash`).
* record id         : :func:`vision_record_id` binds the project stable id
                      **and** the image hash (plus the detection schema
                      version), so two projects or two images can never
                      share one vision text record.
* localization input
  hash              : :func:`vision_localization_input_hash` fingerprints the
                      canonical detection only. Changing any recognition fact
                      changes it (an existing text becomes ``stale``); the UI
                      language, the report language, dictionary key order and
                      any timestamp never do.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from utils import locale_registry
from utils.localized_content import (
    CONTENT_TYPE_VISION,
    REASON_NO_CONTENT,
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
# CONSTANTS — canonical detection
# ============================================================

#: content_type of the localized Vision text inside the protocol.
CONTENT_TYPE = CONTENT_TYPE_VISION

#: Project field that carries the canonical detection (language-independent).
DETECTION_FIELD = "vision_detection"

#: Version of the detection schema itself.
VISION_DETECTION_SCHEMA_VERSION = 1

#: Version of the recognition rules (ontology key space).
VISION_ONTOLOGY_VERSION = "1"

#: Internal, language-independent levels (the display layer localizes them).
LEVELS: Tuple[str, ...] = ("LOW", "MEDIUM", "HIGH")

#: Rank used to derive ``overall_level``. Identical to the existing product
#: rule (``vision_risk.get_overall_visual_level``): the highest level wins,
#: an empty detection is ``LOW``.
LEVEL_RANK: Dict[str, int] = {"LOW": 1, "MEDIUM": 2, "HIGH": 3}

#: Penalty of a level. Kept byte-for-byte identical to
#: ``vision_risk.SEVERITY_PENALTY`` so the Current Risk fusion result — and
#: therefore the risk formula — does not change.
SEVERITY_PENALTY: Dict[str, int] = {"LOW": 5, "MEDIUM": 15, "HIGH": 30}

#: Soft caps that keep a malformed or abusive detection out of the project.
MAX_FINDINGS = 24
MAX_KEY_LENGTH = 80
MAX_MODEL_ID_LENGTH = 200
MAX_FINDING_ID_LENGTH = 64
MAX_LOCATION_VALUES = 4

#: Internal key space of a generated finding id.
FINDING_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")

#: Internal key space of an image hash (SHA-256 hex digest).
IMAGE_HASH_RE = re.compile(r"^[0-9a-f]{64}$")

#: Order of the four numeric values of a bounding box.
LOCATION_FIELDS: Tuple[str, ...] = ("x_min", "y_min", "x_max", "y_max")

# ============================================================
# CONSTANTS — canonical detection: keys a finding may carry
# ============================================================

#: Canonical (language-independent) keys of one finding.
FINDING_FACT_FIELDS: Tuple[str, ...] = (
    "finding_id",
    "object_key",
    "category_key",
    "location",
    "confidence",
    "penalty",
    "level",
)

#: Accepted aliases of the canonical keys, so a model answer written with the
#: legacy field names is still parsed without inventing a second ontology.
_FINDING_KEY_ALIASES: Dict[str, Tuple[str, ...]] = {
    "object_key": ("object_key", "risk_name", "object", "name"),
    "category_key": ("category_key", "risk_category", "category"),
    "level": ("level", "severity", "risk_level"),
    "confidence": ("confidence", "score", "probability"),
    "location": ("location", "bbox", "box", "coordinates"),
}

# ============================================================
# CONSTANTS — localized text payload
# ============================================================

#: The only three per-finding keys a text payload may carry (plus the id).
TEXT_FINDING_FIELDS: Tuple[str, ...] = ("title", "reason", "recommendation")

#: The only top level keys a text payload may carry.
TEXT_PAYLOAD_FIELDS: Tuple[str, ...] = ("summary", "findings")

#: Soft caps that reject an abusive or malformed AI text response.
MAX_SUMMARY_LENGTH = 2000
MAX_TEXT_LENGTH = 800

#: Numeric identifiers a text payload must never contain. They belong to the
#: canonical detection (the only source of truth for numbers).
FORBIDDEN_TEXT_NUMERIC_KEYS = frozenset(
    {
        "confidence",
        "penalty",
        "score",
        "level",
        "severity",
        "risk_level",
        "location",
        "bbox",
        "box",
        "coordinates",
        "x_min",
        "y_min",
        "x_max",
        "y_max",
        "object_key",
        "category_key",
        "risk_name",
        "risk_category",
        "finding_count",
        "findings_count",
        "total_penalty",
        "overall_level",
        "image_hash",
        "model_id",
    }
)

# ============================================================
# CONSTANTS — session cache
# ============================================================

#: How many recent entries are kept per cache group in the session.
MAX_SESSION_CACHE_PER_GROUP = 3

# ============================================================
# CONSTANTS — upload guard rails
# ============================================================

#: MIME types the uploader is allowed to hand over.
ALLOWED_IMAGE_MIME: Tuple[str, ...] = (
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/bmp",
    "image/x-ms-bmp",
)

#: Hard byte cap of one uploaded image.
MAX_IMAGE_BYTES = 8 * 1024 * 1024

#: Magic numbers used to confirm the decoded bytes really are an image. The
#: sniffing needs no Pillow import, so it can be unit tested offline.
_IMAGE_MAGIC: Tuple[Tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"BM", "image/bmp"),
)

# ============================================================
# CONSTANTS — button operations
# ============================================================

#: User triggered operations of the Vision page.
OPERATION_ANALYZE = "analyze"
OPERATION_GENERATE_TEXT = "generate_text"

VISION_OPERATIONS: Tuple[str, ...] = (OPERATION_ANALYZE, OPERATION_GENERATE_TEXT)

# ============================================================
# CONSTANTS — statuses exposed to the page
# ============================================================

DISPLAY_AVAILABLE = "available"
DISPLAY_STALE = "stale"
DISPLAY_MISSING = "missing"
DISPLAY_EMPTY = "empty"
DISPLAY_INVALID = "invalid"
DISPLAY_LEGACY_COMPATIBLE = "legacy_compatible"

#: 当前语言还没有"针对当前输入的可用版本"的显示状态。
#:
#: * ``empty``             识别结果存在，但当前语言一个版本都没有；
#: * ``missing``           别的语言有版本，当前语言没有；
#: * ``stale``             当前语言的版本基于旧输入，已过期；
#: * ``invalid``           当前语言的版本没通过校验，永远不能当作可用内容；
#: * ``legacy_compatible`` 只有旧版单例内容（只读，且未绑定当前图片）。
#:
#: 只有这些状态允许写文本；``available`` 表示当前语言已有可用版本，绝不重写。
DISPLAY_STATES_NEEDING_TEXT: Tuple[str, ...] = (
    DISPLAY_EMPTY,
    DISPLAY_MISSING,
    DISPLAY_STALE,
    DISPLAY_INVALID,
    DISPLAY_LEGACY_COMPATIBLE,
)

#: BCP 47 "und": language undetermined (legacy buckets we cannot identify).
UNDETERMINED_LOCALE = "und"

# ============================================================
# SMALL PURE HELPERS
# ============================================================


def _clean_text(value: Any, max_length: int) -> Optional[str]:
    """Non-empty stripped string within ``max_length``, else ``None``."""
    if not isinstance(value, str):
        return None

    text = value.strip()

    if not text or len(text) > max_length:
        return None

    return text


def _finite_number(value: Any) -> Optional[float]:
    """
    A real, finite number.

    ``bool`` is rejected on purpose (``True`` is not a measurement) and
    ``NaN`` / ``Infinity`` never pass :func:`math.isfinite`.
    """
    if isinstance(value, bool):
        return None

    if not isinstance(value, (int, float)):
        return None

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(number):
        return None

    return number


def _bounded_int(value: Any) -> Optional[int]:
    """A finite non-negative integer, else ``None``."""
    number = _finite_number(value)

    if number is None:
        return None

    if number != int(number) or number < 0:
        return None

    return int(number)


def coerce_confidence(value: Any) -> Optional[float]:
    """
    Recognition confidence in ``0..1``, or ``None`` when unknown.

    A value that is present but not a real number in range is reported as
    ``None`` (never guessed), so a detection built from it is rejected by
    :func:`validate_vision_detection` instead of carrying an invented number.
    """
    if value is None:
        return None

    number = _finite_number(value)

    if number is None or number < 0.0 or number > 1.0:
        return None

    return number


def normalize_location(location: Any) -> List[float]:
    """
    Normalized bounding box ``[x_min, y_min, x_max, y_max]`` in ``0..1``.

    Same lenient rule as the recognition layer: a missing or unusable box is
    reported as an empty list ("location unknown") instead of rejecting an
    otherwise valid finding. Coordinates are clamped into range and a
    degenerate box (``x_max <= x_min``) is dropped.
    """
    if isinstance(location, Mapping):
        location = [location.get(field) for field in LOCATION_FIELDS]

    if not isinstance(location, (list, tuple)):
        return []

    if len(location) != MAX_LOCATION_VALUES:
        return []

    normalized: List[float] = []

    for value in location:
        number = _finite_number(value)

        if number is None:
            return []

        normalized.append(max(0.0, min(1.0, number)))

    x_min, y_min, x_max, y_max = normalized

    if x_max <= x_min or y_max <= y_min:
        return []

    return normalized


def overall_level_for(levels: Sequence[Any]) -> str:
    """
    Highest internal level of a finding set.

    Mirrors ``vision_risk.get_overall_visual_level`` exactly: the highest of
    ``LOW`` / ``MEDIUM`` / ``HIGH`` wins and an empty set is ``LOW``, so the
    canonical detection cannot change the value the existing product stores
    in ``visual_risk_level``.
    """
    best = "LOW"
    best_rank = 0

    for value in levels:
        level = str(value or "").strip().upper()

        rank = LEVEL_RANK.get(level, 0)

        if rank > best_rank:
            best = level
            best_rank = rank

    return best


def level_key(level: Any) -> Optional[str]:
    """
    Stable locale key of an internal level (``risk.level.low`` …).

    Display only: the internal level stays ``LOW`` / ``MEDIUM`` / ``HIGH``.
    Unknown values return ``None`` so the caller can fall back to the raw
    value instead of showing a broken key.
    """
    value = str(level or "").strip().upper()

    if value not in LEVELS:
        return None

    return f"risk.level.{value.lower()}"


def is_valid_image_hash(value: Any) -> bool:
    """True when ``value`` looks like a SHA-256 hex digest."""
    return isinstance(value, str) and bool(IMAGE_HASH_RE.match(value))


def _stable_project_id(project: Any, project_id: Any = None) -> str:
    """
    Stable identifier of a project.

    The page passes the project's dict key explicitly because the stored
    project object does not carry its own name.
    """
    if isinstance(project_id, str) and project_id.strip():
        return project_id.strip()

    if isinstance(project, dict):
        for field in ("name", "id", "project_id"):
            value = project.get(field)

            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


# ============================================================
# IMAGE HASH / UPLOAD GUARD RAILS
# ============================================================


def compute_image_hash(image_bytes: Any) -> Optional[str]:
    """
    SHA-256 of the uploaded image bytes (lowercase hex).

    Returns ``None`` for anything that is not a non-empty bytes-like object,
    so a caller can never build an identity out of a path, a filename or an
    empty upload. The filename is deliberately **not** part of the hash: two
    different files that share a name must not share an identity.
    """
    if isinstance(image_bytes, (bytearray, memoryview)):
        image_bytes = bytes(image_bytes)

    if not isinstance(image_bytes, bytes) or not image_bytes:
        return None

    return hashlib.sha256(image_bytes).hexdigest()


def sniff_image_mime(header: Any) -> Optional[str]:
    """
    MIME type of an image read from its leading bytes, or ``None``.

    Uses magic numbers only (no Pillow import) so the upload guard can be
    tested offline and cannot be fooled by a lying file extension.
    """
    if isinstance(header, (bytearray, memoryview)):
        header = bytes(header)

    if not isinstance(header, bytes):
        return None

    for magic, mime in _IMAGE_MAGIC:
        if header.startswith(magic):
            return mime

    return None


def validate_image_upload(
    filename: Any = None,
    mime_type: Any = None,
    size_bytes: Any = None,
    header: Any = None,
    allowed_mime: Sequence[str] = ALLOWED_IMAGE_MIME,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> List[str]:
    """
    Problems of one uploaded image, as stable codes (empty list = accepted).

    Rejects an oversized file, a MIME type outside the allow-list and bytes
    whose magic number is not an image. The filename is only used for the
    human readable error message of the page — never for identity, never sent
    to the AI and never logged together with the image content.
    """
    problems: List[str] = []

    size = _bounded_int(size_bytes)

    if size is None:
        problems.append("image_size_unknown")
    elif size <= 0:
        problems.append("image_empty")
    elif size > max_bytes:
        problems.append("image_too_large")

    if not isinstance(mime_type, str) or not mime_type.strip():
        problems.append("image_mime_missing")
    elif mime_type.strip().lower() not in {value.lower() for value in allowed_mime}:
        problems.append("image_mime_not_allowed")

    if header is None:
        problems.append("image_bytes_missing")
    elif sniff_image_mime(header) is None:
        problems.append("image_not_decodable")

    return problems


# ============================================================
# CANONICAL DETECTION
# ============================================================


def normalize_finding_id(
    index: int,
    object_key: Any,
    category_key: Any,
    location: Any,
) -> str:
    """
    Stable, collision free id of one finding.

    The id is derived from the position, the stable ontology keys and the
    location. It never uses the image hash or the locale, so the same
    detection always produces the same ids.
    """
    payload = "|".join(
        (
            str(index),
            str(object_key or ""),
            str(category_key or ""),
            json.dumps(location or [], ensure_ascii=False, sort_keys=True),
        )
    )

    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()

    return f"vf{digest[:14]}"


def _finding_value(item: Mapping[str, Any], field: str) -> Any:
    """First present alias of a canonical finding field."""
    for alias in _FINDING_KEY_ALIASES.get(field, (field,)):
        if alias in item:
            return item.get(alias)

    return None


def _normalize_finding(
    item: Any,
    index: int,
) -> Optional[Dict[str, Any]]:
    """
    Structurally normalize one finding, or ``None`` when it is unusable.

    Only the *shape* is repaired here (aliases, clamping, coercion). Values
    that are structurally present but semantically wrong (an unknown level, a
    non-numeric confidence) are carried through unchanged so that
    :func:`validate_vision_detection` can reject them instead of silently
    accepting a repaired fact.
    """
    if not isinstance(item, Mapping):
        return None

    object_key = _clean_text(_finding_value(item, "object_key"), MAX_KEY_LENGTH)
    category_key = _clean_text(_finding_value(item, "category_key"), MAX_KEY_LENGTH)

    if not object_key or not category_key:
        return None

    location = normalize_location(_finding_value(item, "location"))

    raw_level = _finding_value(item, "level")
    level = str(raw_level).strip().upper() if raw_level is not None else ""
    known_level = level if level in LEVELS else ""

    raw_confidence = _finding_value(item, "confidence")
    confidence = None

    if raw_confidence is not None:
        number = _finite_number(raw_confidence)

        if number is None or number < 0.0 or number > 1.0:
            # Keep the unusable value so validation rejects the detection.
            confidence = raw_confidence
        else:
            confidence = number

    existing_id = _clean_text(item.get("finding_id"), MAX_FINDING_ID_LENGTH)

    if existing_id and FINDING_ID_RE.match(existing_id):
        finding_id = existing_id
    else:
        finding_id = normalize_finding_id(index, object_key, category_key, location)

    return {
        "finding_id": finding_id,
        "object_key": object_key,
        "category_key": category_key,
        "location": location,
        "confidence": confidence,
        "penalty": SEVERITY_PENALTY.get(known_level),
        "level": level,
    }


def normalize_vision_detection(
    raw: Any,
    image_hash: Any = None,
    model_id: Any = None,
    ontology_version: Any = None,
    allowed_objects: Any = None,
    allowed_categories: Any = None,
) -> Dict[str, Any]:
    """
    Build the canonical, language-independent detection from a raw answer.

    Accepts either a raw model answer (``{"findings": [...]}`` /
    ``{"risks": [...]}`` / a bare list) or an already normalized detection.
    Object / category keys outside the ontology allow-list are dropped, which
    is the same hallucination filter the recognition layer applies.

    Returns ``{}`` when nothing usable is left. Pure: the input is only read,
    never modified, and every returned object is freshly built.
    """
    if isinstance(raw, (list, tuple)):
        items: Any = list(raw)
        source: Mapping[str, Any] = {}
        findings_raw = items
    elif isinstance(raw, Mapping):
        source = raw
        for key in ("findings", "risks", "detections", "results", "items"):
            value = source.get(key)

            if isinstance(value, (list, tuple)):
                findings_raw = value
                break
        else:
            return {}
    else:
        return {}

    resolved_hash = _clean_text(image_hash, 64) or _clean_text(
        source.get("image_hash"), 64
    )
    resolved_model = _clean_text(model_id, MAX_MODEL_ID_LENGTH) or _clean_text(
        source.get("model_id"), MAX_MODEL_ID_LENGTH
    )
    resolved_ontology = _clean_text(
        ontology_version, MAX_KEY_LENGTH
    ) or _clean_text(source.get("ontology_version"), MAX_KEY_LENGTH)

    if resolved_ontology is None:
        resolved_ontology = VISION_ONTOLOGY_VERSION

    if not isinstance(findings_raw, (list, tuple)):
        return {}

    objects = _as_key_set(allowed_objects)
    categories = _as_key_set(allowed_categories)

    findings: List[Dict[str, Any]] = []

    for index, item in enumerate(findings_raw[:MAX_FINDINGS]):
        finding = _normalize_finding(item, index)

        if finding is None:
            continue

        if objects is not None and finding["object_key"] not in objects:
            continue

        if categories is not None and finding["category_key"] not in categories:
            continue

        findings.append(finding)

    if findings_raw and not findings:
        return {}

    overall_level = overall_level_for([finding.get("level") for finding in findings])

    return {
        "schema_version": VISION_DETECTION_SCHEMA_VERSION,
        "image_hash": resolved_hash,
        "model_id": resolved_model,
        "ontology_version": resolved_ontology,
        "findings": findings,
        "overall_level": overall_level,
        # No finding means no visual penalty, even though the overall level of
        # an empty detection is LOW (same rule as the recognition layer).
        "total_penalty": SEVERITY_PENALTY[overall_level] if findings else 0,
    }


def _as_key_set(value: Any) -> Optional[frozenset]:
    """Optional allow-list of stable keys, or ``None`` when unconstrained."""
    if value is None:
        return None

    if isinstance(value, Mapping):
        value = list(value.keys())

    if isinstance(value, (set, frozenset, list, tuple)):
        cleaned = {
            text
            for text in (_clean_text(item, MAX_KEY_LENGTH) for item in value)
            if text
        }

        return frozenset(cleaned) if cleaned else None

    return None


def validate_vision_detection(
    data: Any,
    allowed_objects: Any = None,
    allowed_categories: Any = None,
) -> List[str]:
    """
    Problems of a canonical detection, as stable codes (empty = valid).

    Rejects an unknown schema version, a missing or malformed image hash, a
    missing model id, an unknown level, a non-numeric or out-of-range
    confidence, a duplicated or malformed finding id, a penalty that does not
    match its level, an unknown object / category key (when an allow-list is
    given) and any missing / extra structure.
    """
    problems: List[str] = []

    if not isinstance(data, Mapping):
        return ["detection_not_dict"]

    if data.get("schema_version") != VISION_DETECTION_SCHEMA_VERSION:
        problems.append("schema_version_invalid")

    if not is_valid_image_hash(data.get("image_hash")):
        problems.append("image_hash_invalid")

    if _clean_text(data.get("model_id"), MAX_MODEL_ID_LENGTH) is None:
        problems.append("model_id_invalid")

    if _clean_text(data.get("ontology_version"), MAX_KEY_LENGTH) is None:
        problems.append("ontology_version_invalid")

    findings = data.get("findings")

    if not isinstance(findings, (list, tuple)):
        problems.append("findings_not_list")
        return problems

    if len(findings) > MAX_FINDINGS:
        problems.append("too_many_findings")

    objects = _as_key_set(allowed_objects)
    categories = _as_key_set(allowed_categories)

    seen_ids: List[str] = []
    levels: List[str] = []

    for index, finding in enumerate(findings):
        if not isinstance(finding, Mapping):
            problems.append("finding_not_dict")
            continue

        finding_id = finding.get("finding_id")

        if not isinstance(finding_id, str) or not FINDING_ID_RE.match(finding_id):
            problems.append("finding_id_invalid")
        elif finding_id in seen_ids:
            problems.append("finding_id_duplicate")
        else:
            seen_ids.append(finding_id)

        object_key = _clean_text(finding.get("object_key"), MAX_KEY_LENGTH)

        if object_key is None:
            problems.append("object_key_invalid")
        elif objects is not None and object_key not in objects:
            problems.append("object_not_in_ontology")

        category_key = _clean_text(finding.get("category_key"), MAX_KEY_LENGTH)

        if category_key is None:
            problems.append("category_key_invalid")
        elif categories is not None and category_key not in categories:
            problems.append("category_not_in_ontology")

        level = finding.get("level")

        if not isinstance(level, str) or level not in LEVELS:
            problems.append("level_invalid")
        else:
            levels.append(level)

            if finding.get("penalty") != SEVERITY_PENALTY[level]:
                problems.append("penalty_invalid")

        location = finding.get("location")

        if not isinstance(location, (list, tuple)) or len(location) != (
            MAX_LOCATION_VALUES
        ):
            # ``[]`` is the documented "location unknown".
            if location not in ([], (), None):
                problems.append("location_invalid")
        else:
            for value in location:
                number = _finite_number(value)

                if number is None or number < 0.0 or number > 1.0:
                    problems.append("location_invalid")
                    break

        confidence = finding.get("confidence")

        if confidence is not None:
            number = _finite_number(confidence)

            if number is None or number < 0.0 or number > 1.0:
                problems.append("confidence_invalid")

    overall_level = data.get("overall_level")

    if not isinstance(overall_level, str) or overall_level not in LEVELS:
        problems.append("overall_level_invalid")
    else:
        # An empty detection has no penalty at all, otherwise the total is the
        # penalty of the overall level (the value the existing product stores
        # in ``visual_risk_penalty``).
        expected_total = SEVERITY_PENALTY[overall_level] if findings else 0

        if overall_level != overall_level_for(levels):
            problems.append("overall_level_mismatch")

        if data.get("total_penalty") != expected_total:
            problems.append("total_penalty_invalid")

    return problems


def is_valid_vision_detection(
    data: Any,
    allowed_objects: Any = None,
    allowed_categories: Any = None,
) -> bool:
    """True when :func:`validate_vision_detection` finds no problem."""
    return not validate_vision_detection(data, allowed_objects, allowed_categories)


def detection_findings(data: Any) -> List[Dict[str, Any]]:
    """Fresh copies of the findings of a detection (never the originals)."""
    if not isinstance(data, Mapping):
        return []

    findings = data.get("findings")

    if not isinstance(findings, (list, tuple)):
        return []

    return [dict(finding) for finding in findings if isinstance(finding, Mapping)]


def detection_level_counts(data: Any) -> Dict[str, int]:
    """Number of findings per internal level (canonical facts)."""
    counts = {level: 0 for level in LEVELS}

    for finding in detection_findings(data):
        level = finding.get("level")

        if isinstance(level, str) and level in counts:
            counts[level] += 1

    return counts


# ============================================================
# IDENTITY
# ============================================================


def vision_record_id(
    project_id: Any,
    image_hash: Any,
) -> Optional[str]:
    """
    record_id of one (project, image) vision text record.

    Binds the project stable id, the image hash and the detection schema
    version, so a text written for one project or one image can never be read
    for another — not even when both files share the same filename. Returns
    ``None`` when the identity is incomplete, which makes the caller refuse to
    store anything instead of falling back to a shared record.
    """
    identifier = _clean_text(project_id, MAX_KEY_LENGTH * 2)

    if identifier is None:
        return None

    if not is_valid_image_hash(image_hash):
        return None

    payload = "|".join(
        (identifier, str(image_hash), f"v{VISION_DETECTION_SCHEMA_VERSION}")
    )

    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()

    return f"vis-{digest[:32]}"


def build_vision_localization_input(detection: Any) -> Dict[str, Any]:
    """
    Language-independent input the text layer is generated from.

    Only recognition facts enter it (schema, model, ontology version, image
    hash, per finding id / keys / level / penalty / location / confidence, the
    overall level and the penalty total). No locale, no UI state, no report
    language and no timestamp ever does, which is what makes the fingerprint
    stable for one detection and different for another.
    """
    if not isinstance(detection, Mapping):
        return {}

    findings = []

    for finding in detection_findings(detection):
        findings.append(
            {
                "finding_id": finding.get("finding_id"),
                "object_key": finding.get("object_key"),
                "category_key": finding.get("category_key"),
                "level": finding.get("level"),
                "penalty": finding.get("penalty"),
                "location": list(finding.get("location") or []),
                "confidence": finding.get("confidence"),
            }
        )

    return {
        "schema_version": detection.get("schema_version"),
        "ontology_version": detection.get("ontology_version"),
        "model_id": detection.get("model_id"),
        "image_hash": detection.get("image_hash"),
        "overall_level": detection.get("overall_level"),
        "total_penalty": detection.get("total_penalty"),
        "findings_count": len(findings),
        "findings": findings,
    }


def vision_localization_input_hash(detection: Any) -> Optional[str]:
    """
    Fingerprint of the canonical detection.

    Uses the protocol's own canonical JSON hash writer, so dictionary key
    order can never influence it. Returns ``None`` for an unusable detection.
    """
    payload = build_vision_localization_input(detection)

    if not payload:
        return None

    if payload.get("findings") is None:
        return None

    if not is_valid_image_hash(payload.get("image_hash")):
        return None

    return compute_input_hash(payload)


# ============================================================
# LOCALIZED TEXT PAYLOAD
# ============================================================


def _payload_problems(payload: Any, detection: Any) -> List[str]:
    """Stable problem codes of one AI text payload (see the validator)."""
    problems: List[str] = []

    if not isinstance(payload, Mapping):
        return ["payload_not_dict"]

    extra_keys = {
        str(key)
        for key in payload.keys()
        if key not in TEXT_PAYLOAD_FIELDS
    }

    if extra_keys:
        problems.append("payload_unknown_keys")

    summary = payload.get("summary")

    if not isinstance(summary, str) or not summary.strip():
        problems.append("summary_missing")
    elif len(summary.strip()) > MAX_SUMMARY_LENGTH:
        problems.append("summary_too_long")

    findings = payload.get("findings")

    if not isinstance(findings, (list, tuple)):
        problems.append("findings_not_list")
        return problems

    expected = [
        str(finding.get("finding_id"))
        for finding in detection_findings(detection)
        if finding.get("finding_id")
    ]

    if len(findings) != len(expected):
        problems.append("findings_count_mismatch")

    seen: List[str] = []

    for item in findings:
        if not isinstance(item, Mapping):
            problems.append("finding_not_dict")
            continue

        if any(key not in TEXT_FINDING_FIELDS + ("finding_id",) for key in item.keys()):
            problems.append("finding_unknown_keys")

        for key, value in item.items():
            if (
                str(key) in FORBIDDEN_TEXT_NUMERIC_KEYS
                or isinstance(value, bool)
                or (isinstance(value, (int, float)) and not isinstance(value, bool))
            ):
                problems.append("finding_numeric_injection")
                break

        finding_id = item.get("finding_id")

        if not isinstance(finding_id, str) or not finding_id.strip():
            problems.append("finding_id_missing")
            continue

        if finding_id not in expected:
            problems.append("finding_id_unknown")

        if finding_id in seen:
            problems.append("finding_id_duplicate")

        seen.append(finding_id)

        for field in TEXT_FINDING_FIELDS:
            value = item.get(field)

            if value is None:
                problems.append("finding_text_missing")
                break

            if not isinstance(value, str):
                problems.append("finding_text_not_string")
                break

            if not value.strip():
                problems.append("finding_text_empty")
                break

            if len(value.strip()) > MAX_TEXT_LENGTH:
                problems.append("finding_text_too_long")
                break

    if sorted(seen) != sorted(expected):
        problems.append("findings_mismatch")

    return problems


def validate_vision_text_payload(
    payload: Any,
    detection: Any,
) -> Dict[str, Any]:
    """
    Validate one AI text response before it may be stored.

    The payload may only carry ``summary`` and, per finding, the three human
    readable fields ``title`` / ``reason`` / ``recommendation``. It is
    accepted only when its ``finding_id`` set is **exactly** the finding id
    set of the canonical detection: no missing, no extra, no duplicate and no
    reordering-sensitive finding. Numbers (score / penalty / confidence /
    level / coordinates …) are rejected outright, and a rejected payload is
    never written to ``localized_content``, never cached and never persisted.

    Returns a normalized payload (findings re-ordered to the canonical order,
    text trimmed) or ``{}`` when the response must be rejected.
    """
    if _payload_problems(payload, detection):
        return {}

    expected = [
        str(finding.get("finding_id"))
        for finding in detection_findings(detection)
        if finding.get("finding_id")
    ]

    index: Dict[str, Mapping[str, Any]] = {}

    for item in payload.get("findings") or []:
        index[str(item.get("finding_id"))] = item

    normalized_findings = []

    for finding_id in expected:
        item = index.get(finding_id, {})

        normalized_findings.append(
            {
                "finding_id": finding_id,
                "title": str(item.get("title")).strip(),
                "reason": str(item.get("reason")).strip(),
                "recommendation": str(item.get("recommendation")).strip(),
            }
        )

    return {
        "summary": str(payload.get("summary")).strip(),
        "findings": normalized_findings,
    }


def vision_text_payload_problems(payload: Any, detection: Any) -> List[str]:
    """Exposed problem codes of :func:`validate_vision_text_payload`."""
    return _payload_problems(payload, detection)


def is_valid_vision_text_payload(payload: Any, detection: Any) -> bool:
    """True when :func:`validate_vision_text_payload` would accept it."""
    return bool(validate_vision_text_payload(payload, detection))


def text_payload_for_store(
    payload: Any,
    detection: Any,
) -> Optional[Dict[str, Any]]:
    """
    Validated payload that may be handed to ``localized_content_put``.

    Returns ``None`` for anything that would create an invalid, mismatched or
    empty record, so a failed generation can never reach storage.
    """
    validated = validate_vision_text_payload(payload, detection)

    if not validated:
        return None

    if not validated.get("summary"):
        return None

    if not validated.get("findings"):
        return None

    return validated


# ============================================================
# PROMPT
# ============================================================


def build_vision_text_prompt(
    detection: Any,
    context: Any = None,
    target_locale: Any = None,
) -> str:
    """
    Prompt that writes the human readable Vision text in ``target_locale``.

    The canonical detection is passed in as read-only facts and is the only
    source of numbers: the model may not add, remove or reorder a finding, may
    not output a penalty / level / confidence / box and may not re-assess the
    site. ``finding_id`` values are echoed back so the response can be matched
    against the canonical detection.
    """
    language_name = locale_registry.ai_language_name(
        resolve_content_locale(target_locale)
    )

    facts = detection if isinstance(detection, Mapping) else {}

    described: List[Dict[str, Any]] = []

    for finding in detection_findings(facts):
        described.append(
            {
                "finding_id": finding.get("finding_id"),
                "object_key": finding.get("object_key"),
                "category_key": finding.get("category_key"),
                "level": finding.get("level"),
            }
        )

    page_context = context if isinstance(context, Mapping) else {}

    return f"""
You are RiskPilot's construction safety writing assistant.

Explain the ALREADY COMPLETED visual detection of one construction site photo
in {language_name}. You are NOT looking at the photo, you are NOT re-assessing
the site and you are NOT recalculating anything: the detection below is the
only source of truth.

TARGET LANGUAGE
---------------
Write every human readable value in {language_name}. Keep every JSON field name
in English, and keep every "finding_id" exactly as provided.

FORBIDDEN
---------
- Do NOT output a score, penalty, confidence, level, severity or bounding box.
- Do NOT add, remove, merge, split or reorder findings.
- Do NOT invent findings, people, equipment or site facts that are not listed.
- Do NOT translate or rename any JSON field name or "finding_id".
- Do NOT wrap the JSON in Markdown fences.

REQUIRED
--------
- Return ONLY valid JSON with EXACTLY these fields: "summary" and "findings".
- "summary" is a concise {language_name} overview of the detected risks.
- One entry in "findings" per provided finding, in the same order, with
  exactly the fields "finding_id", "title", "reason", "recommendation".
- "title" is a short {language_name} title of that observation.
- "reason" explains why it is a risk, only from the provided facts.
- "recommendation" is one concrete corrective action for that finding.

DETECTED FINDINGS (read-only):
{json.dumps(described, ensure_ascii=False, indent=2)}

PROJECT CONTEXT (read-only):
{json.dumps(page_context, ensure_ascii=False, indent=2)}

OUTPUT
------
Return only this structured JSON:

{{
    "summary": "Short overview of the detected risks.",
    "findings": [
        {{
            "finding_id": "the provided finding_id",
            "title": "Short title of this finding.",
            "reason": "Why this finding is a risk.",
            "recommendation": "One concrete corrective action."
        }}
    ]
}}
"""


# ============================================================
# SESSION CACHE NAMESPACE
# ============================================================


def vision_detection_cache_key(
    project_id: Any,
    image_hash: Any,
    model_id: Any,
    ontology_version: Any,
) -> Tuple[str, str, str, str]:
    """
    Namespaced session-cache key of one canonical detection.

    Recognition facts do not depend on language, so this key has **no locale**
    component: switching the interface language reuses the very same
    detection instead of re-running the vision model.
    """
    return (
        str(project_id or ""),
        str(image_hash or ""),
        str(model_id or ""),
        str(ontology_version or ""),
    )


def vision_text_cache_key(
    project_id: Any,
    record_id: Any,
    locale: Any,
    input_hash: Any,
) -> Tuple[str, str, str, str, str]:
    """
    Namespaced session-cache key of one localized Vision text.

    Includes the project, the content_type, the record id (which binds the
    image), the content locale and the detection fingerprint, so a text
    generated for one combination can never be displayed for another.
    """
    return (
        str(project_id or ""),
        CONTENT_TYPE,
        str(record_id or ""),
        str(locale or ""),
        str(input_hash or ""),
    )


def vision_detection_cache_group(key: Any) -> Optional[Tuple[str]]:
    """The project a detection cache key belongs to, or ``None``."""
    if isinstance(key, tuple) and len(key) == 4:
        return (key[0],)

    return None


def vision_text_cache_group(key: Any) -> Optional[Tuple[str, str, str]]:
    """The (project, record, locale) group a text cache key belongs to."""
    if isinstance(key, tuple) and len(key) == 5:
        return (key[0], key[2], key[3])

    return None


def bounded_vision_cache_put(
    cache: Any,
    key: Any,
    payload: Any,
    group: Any = None,
    limit: int = MAX_SESSION_CACHE_PER_GROUP,
) -> Any:
    """
    Store ``payload`` under ``key`` and evict the oldest keys of the same
    group so the session cache stays bounded.

    Pure: it mutates only the ``cache`` dict it is handed and returns it. It
    never removes a cached value belonging to another project, image or
    locale.
    """
    if not isinstance(cache, dict):
        return cache

    cache.pop(key, None)
    cache[key] = payload

    if group is None:
        group = vision_text_cache_group(key)

    same_group = [existing for existing in cache if group_of(existing, group) == group]

    while len(same_group) > limit:
        oldest = same_group.pop(0)
        cache.pop(oldest, None)

    return cache


def group_of(key: Any, group: Any) -> Any:
    """
    Group of a cache key, using the same reader as ``group``.

    A detection group is a 1-tuple and a text group a 3-tuple, so the shape of
    ``group`` tells which reader to apply.
    """
    if isinstance(group, tuple):
        if len(group) == 1:
            return vision_detection_cache_group(key)

        if len(group) == 3:
            return vision_text_cache_group(key)

    return None


def vision_action_key(
    project_id: Any,
    image_hash: Any,
    locale: Any,
    operation: Any,
    record_id: Any = None,
) -> Optional[str]:
    """
    Streamlit widget key of one Vision action button.

    The project, the image hash, the content locale and the operation type all
    enter a stable digest, so different projects, images, languages or
    operations can never share widget state (and can therefore never
    cross-trigger each other).
    """
    action = str(operation or "").strip()

    if action not in VISION_OPERATIONS:
        return None

    if not is_valid_image_hash(image_hash):
        return None

    payload = "|".join(
        (
            str(project_id or ""),
            str(image_hash),
            str(record_id or ""),
            str(locale or ""),
            action,
        )
    )

    digest = hashlib.sha1(payload.encode("utf-8")).hexdigest()

    return f"vision_{action}::{digest}"


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


def _ui_state(status: Mapping[str, Any]) -> str:
    """Map a protocol status onto the state the page renders."""
    value = status.get("status")
    saved = status.get("saved_locales") or []

    if value in (STATUS_AVAILABLE, STATUS_DETERMINISTIC):
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


def merge_vision_display(
    detection: Any,
    payload: Any = None,
    locale: Any = None,
    stale: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Merge the canonical detection with one validated text payload.

    The result is display ready but **facts first**: every numeric value, the
    object / category keys, the location, the finding count and the finding
    order come from the detection. The payload can only add the three text
    fields, keyed by ``finding_id``; a payload field for an unknown id is
    ignored, and the payload can never overwrite a fact.

    Returns ``None`` when the detection itself is unusable.
    """
    if not isinstance(detection, Mapping):
        return None

    if validate_vision_detection(detection):
        return None

    validated: Dict[str, Any] = {}

    if isinstance(payload, Mapping):
        validated = validate_vision_text_payload(payload, detection)

    texts = {
        item.get("finding_id"): item for item in (validated.get("findings") or [])
    }

    findings = []

    for finding in detection_findings(detection):
        level = finding.get("level")
        text = texts.get(finding.get("finding_id"), {})

        findings.append(
            {
                **finding,
                "level_key": level_key(level),
                # Compatibility aliases used by the existing renderers: the
                # canonical key stays the source of truth, the alias only
                # mirrors it for display helpers.
                "risk_name": finding.get("object_key"),
                "risk_category": finding.get("category_key"),
                "severity": level,
                "title": text.get("title"),
                "reason": text.get("reason"),
                "recommendation": text.get("recommendation"),
            }
        )

    return {
        "schema_version": detection.get("schema_version"),
        "image_hash": detection.get("image_hash"),
        "model_id": detection.get("model_id"),
        "ontology_version": detection.get("ontology_version"),
        "overall_level": detection.get("overall_level"),
        "overall_level_key": level_key(detection.get("overall_level")),
        "total_penalty": detection.get("total_penalty"),
        "findings_count": len(findings),
        "level_counts": detection_level_counts(detection),
        "findings": findings,
        "summary": validated.get("summary"),
        "has_text": bool(validated),
        "stale": bool(stale),
        "locale": resolve_content_locale(locale),
    }


def stale_text_blocks(payload: Any) -> Optional[Dict[str, Any]]:
    """
    Outdated text of a stored payload, prepared for a read-only expander.

    When the Detection fingerprint changed, the stored finding ids may no
    longer match the current canonical detection, so the outdated text cannot
    be merged into the current findings. It is still shown exactly as stored
    (structure only, no numeric value, no re-ordering) and stays labelled as
    outdated. Returns ``None`` when there is nothing safe to show.
    """
    if not isinstance(payload, Mapping):
        return None

    summary = _clean_text(payload.get("summary"), MAX_SUMMARY_LENGTH)
    blocks: List[Dict[str, str]] = []

    findings = payload.get("findings")

    if isinstance(findings, (list, tuple)):
        for item in findings:
            if not isinstance(item, Mapping):
                continue

            block = {
                field: _clean_text(item.get(field), MAX_TEXT_LENGTH) or ""
                for field in TEXT_FINDING_FIELDS
            }

            if not any(block.values()):
                continue

            blocks.append({"finding_id": str(item.get("finding_id") or ""), **block})

    if not summary and not blocks:
        return None

    return {"summary": summary or "", "findings": blocks}


def legacy_vision_text(project: Any, locale: Any = None) -> Optional[Dict[str, Any]]:
    """
    Legacy ``ai_text_by_language`` Vision entry, read-only.

    The legacy archive stored Vision text as a **singleton** without any image
    binding, so it can never be presented as the current image's analysis. It
    is only returned when the archived bucket carries *exactly* the requested
    language: an unidentifiable bucket counts as ``und`` and never matches, and
    nothing is migrated or rewritten.
    """
    if not isinstance(project, Mapping):
        return None

    archive = project.get("ai_text_by_language")

    if not isinstance(archive, Mapping):
        return None

    target = resolve_content_locale(locale)

    for key in archive.keys():
        if not isinstance(key, str):
            continue

        bucket_locale = locale_registry.to_ui_locale(key) or UNDETERMINED_LOCALE

        if bucket_locale != target:
            continue

        payload = legacy_ai_text_get(project, CONTENT_TYPE, key, None)

        if isinstance(payload, Mapping) and payload:
            return {"locale_key": key, "payload": dict(payload)}

    return None


def legacy_text_preview(payload: Any) -> Optional[str]:
    """
    Bounded read-only preview of a legacy Vision payload.

    Legacy payloads do not follow the stage 3 contract, so only a plain
    ``summary`` string is rendered (never a finding list, never a number).
    Returns ``None`` when there is nothing safe to show.
    """
    if not isinstance(payload, Mapping):
        return None

    summary = payload.get("summary")

    if not isinstance(summary, str):
        return None

    text = summary.strip()

    if not text:
        return None

    return text[:MAX_SUMMARY_LENGTH]


def get_vision_display(
    project: Any,
    detection: Any = None,
    locale: Any = None,
    project_id: Any = None,
    session_cache: Any = None,
    allowed_objects: Any = None,
    allowed_categories: Any = None,
) -> Dict[str, Any]:
    """
    Explain, for one project, one image and one content locale, what may be
    displayed.

    Pure and read-only: it never mutates ``project`` or ``detection``, never
    calls ``persist()``, never re-reads an image and never calls the AI. The
    ``session_cache`` (keyed by :func:`vision_text_cache_key`) is only
    consulted when the project itself has no usable record for the exact
    current detection.

    Returns a dict with:

    * ``status``         protocol status (``available`` / ``stale`` / ...);
    * ``ui_state``       the state the page renders (adds ``empty``);
    * ``detection``      the canonical detection (a fresh copy);
    * ``payload``        validated text payload of the current locale, or ``None``;
    * ``display``        merged display structure when it may be shown;
    * ``stale_display``  merged structure of an outdated text (expander only);
    * ``saved_locales``  locales that do have a stored text;
    * ``needs_generation`` whether the user must generate this language;
    * ``is_stale`` / ``reason`` / ``input_hash`` / ``record_id``;
    * ``image_hash`` / ``overall_level`` / ``total_penalty`` / ``level_counts``;
    * ``legacy``         read-only legacy archive entry, when it exists.
    """
    target = resolve_content_locale(locale)

    resolved = detection

    if resolved is None and isinstance(project, Mapping):
        resolved = project.get(DETECTION_FIELD)

    fresh_detection = dict(resolved) if isinstance(resolved, Mapping) else None

    if fresh_detection is not None and isinstance(resolved, Mapping):
        fresh_detection["findings"] = detection_findings(resolved)

    base: Dict[str, Any] = {
        "detection": fresh_detection,
        "payload": None,
        "display": None,
        "stale_display": None,
        "stale_text": None,
        "saved_locales": [],
        "reason": None,
        "input_hash": None,
        "record_id": None,
        "locale": target,
        "content_type": CONTENT_TYPE,
        "image_hash": None,
        "overall_level": None,
        "total_penalty": None,
        "level_counts": {level: 0 for level in LEVELS},
        "source": None,
        "legacy": None,
        "legacy_text": None,
    }

    if fresh_detection is None:
        return {
            **base,
            "status": STATUS_MISSING,
            "ui_state": DISPLAY_EMPTY,
            "needs_generation": False,
            "is_stale": False,
        }

    if not detection_findings(fresh_detection):
        # A structurally broken detection (not an empty photo).
        if validate_vision_detection(
            fresh_detection, allowed_objects, allowed_categories
        ):
            return {
                **base,
                "status": STATUS_INVALID,
                "ui_state": DISPLAY_INVALID,
                "needs_generation": False,
                "is_stale": False,
            }

        # No finding at all: nothing to describe.
        return {
            **base,
            "status": STATUS_MISSING,
            "ui_state": DISPLAY_EMPTY,
            "needs_generation": False,
            "is_stale": False,
        }

    problems = validate_vision_detection(
        fresh_detection, allowed_objects, allowed_categories
    )

    image_hash = fresh_detection.get("image_hash")

    facts = {
        "image_hash": image_hash,
        "overall_level": fresh_detection.get("overall_level"),
        "total_penalty": fresh_detection.get("total_penalty"),
        "level_counts": detection_level_counts(fresh_detection),
    }

    identifier = _stable_project_id(project, project_id)

    if problems:
        return {
            **base,
            **facts,
            "status": STATUS_INVALID,
            "ui_state": DISPLAY_INVALID,
            "needs_generation": False,
            "is_stale": False,
            "reason": problems[0],
        }

    record_id = vision_record_id(identifier, image_hash)

    if record_id is None:
        return {
            **base,
            **facts,
            "status": STATUS_INVALID,
            "ui_state": DISPLAY_INVALID,
            "needs_generation": False,
            "is_stale": False,
            "reason": "record_id_unavailable",
        }

    input_hash = vision_localization_input_hash(fresh_detection)

    if input_hash is None:
        # Without a fingerprint no stored text can be proven fresh, so nothing
        # may be displayed as current.
        return {
            **base,
            **facts,
            "status": STATUS_INVALID,
            "ui_state": DISPLAY_INVALID,
            "needs_generation": False,
            "is_stale": False,
            "reason": "input_hash_unavailable",
        }

    status = localized_content_status(
        project,
        CONTENT_TYPE,
        target,
        record_id,
        input_hash=input_hash,
    )

    reason = status.get("reason")

    if status.get("status") == STATUS_LEGACY_COMPATIBLE:
        legacy = legacy_vision_text(project, target)

        if legacy is None:
            status = {
                **status,
                "status": STATUS_INVALID,
                "reason": reason or REASON_NO_CONTENT,
            }
        else:
            return {
                **base,
                **facts,
                "status": STATUS_LEGACY_COMPATIBLE,
                "ui_state": DISPLAY_LEGACY_COMPATIBLE,
                "needs_generation": True,
                "is_stale": False,
                "reason": reason,
                "input_hash": input_hash,
                "record_id": record_id,
                "source": status.get("source"),
                "legacy": legacy,
                "legacy_text": legacy_text_preview(legacy.get("payload")),
            }

    payload: Optional[Dict[str, Any]] = None
    stale_display: Optional[Dict[str, Any]] = None
    stale_text: Optional[Dict[str, Any]] = None

    if status.get("status") in (
        STATUS_AVAILABLE,
        STATUS_STALE,
        STATUS_DETERMINISTIC,
    ):
        record = status.get("record")
        record = record if isinstance(record, Mapping) else {}

        payload = validate_vision_text_payload(record.get("payload"), fresh_detection)

        if not payload:
            status = {**status, "status": STATUS_INVALID}
            reason = status.get("reason")
        else:
            if status.get("status") == STATUS_DETERMINISTIC:
                status = {**status, "status": STATUS_AVAILABLE}

            if status.get("status") == STATUS_STALE:
                # Outdated text is never shown as the current analysis: it is
                # only prepared for the read-only expander.
                stale_display = merge_vision_display(
                    fresh_detection, payload, target, stale=True
                )
                stale_text = stale_text_blocks(payload)
                payload = None

    if (
        payload is None
        and stale_display is None
        and stale_text is None
        and status.get("status") == STATUS_MISSING
        and isinstance(session_cache, Mapping)
    ):
        cached = session_cache.get(
            vision_text_cache_key(identifier, record_id, target, input_hash)
        )

        cached_payload = validate_vision_text_payload(cached, fresh_detection)

        if cached_payload:
            payload = cached_payload
            status = {**status, "status": STATUS_AVAILABLE}

    ui_state = _ui_state(status)

    legacy = legacy_vision_text(project, target)
    legacy_preview = (
        legacy_text_preview(legacy.get("payload"))
        if legacy
        else None
    )

    # 旧版 ai_text_by_language 是按语言归档的单例内容：在多记录（按图片）协议下它
    # 不可能成为"当前版本"，只能在当前语言确实没有新版本时以只读方式呈现，并明确
    # 说明它未绑定到当前图片。绝不迁移、绝不覆盖。
    if (
        ui_state in (DISPLAY_EMPTY, DISPLAY_MISSING)
        and legacy_preview
    ):
        ui_state = DISPLAY_LEGACY_COMPATIBLE

    display = None

    if payload is not None:
        display = merge_vision_display(fresh_detection, payload, target)

    return {
        **base,
        **facts,
        "status": status.get("status"),
        "ui_state": ui_state,
        "payload": payload,
        "display": display,
        "stale_display": stale_display,
        "stale_text": stale_text,
        "saved_locales": list(status.get("saved_locales") or []),
        "needs_generation": ui_state not in (DISPLAY_AVAILABLE,),
        "is_stale": bool(status.get("is_stale")),
        "reason": reason,
        "input_hash": input_hash,
        "record_id": record_id,
        "source": status.get("source"),
        "legacy": legacy,
        "legacy_text": legacy_preview,
    }


def vision_text_needs_generation(display: Mapping[str, Any]) -> bool:
    """
    当前语言是否还没有"可用于当前输入的版本"。

    识别按钮的同一次点击可以顺带补写当前语言版本：只在这个函数为真、且
    canonical detection 有 risks 时才允许（最多一次文本调用）。``available``
    永远返回 False，因此已存在的可用版本绝不会被悄悄重写。
    """
    if not isinstance(display, Mapping):
        return False

    return display.get("ui_state") in DISPLAY_STATES_NEEDING_TEXT


def vision_generation_plan(display: Mapping[str, Any]) -> Dict[str, Any]:
    """
    What the two Vision buttons are allowed to do, given a display state.

    ``analyze`` always re-runs the recognition model (that is the user asking
    for a new detection). ``generate_text`` is only offered when a canonical
    detection already exists and the current language has no usable version
    yet, and it may never call the recognition model.
    """
    state = display.get("ui_state") if isinstance(display, Mapping) else None
    has_detection = bool(display.get("detection")) if isinstance(display, Mapping) else False

    return {
        "can_analyze": True,
        "can_generate_text": has_detection and state in DISPLAY_STATES_NEEDING_TEXT,
        "calls_vision_model_on_text": False,
        "needs_detection_first": not has_detection,
    }


__all__ = [
    "ALLOWED_IMAGE_MIME",
    "CONTENT_TYPE",
    "DETECTION_FIELD",
    "DISPLAY_AVAILABLE",
    "DISPLAY_EMPTY",
    "DISPLAY_INVALID",
    "DISPLAY_LEGACY_COMPATIBLE",
    "DISPLAY_MISSING",
    "DISPLAY_STALE",
    "DISPLAY_STATES_NEEDING_TEXT",
    "FINDING_FACT_FIELDS",
    "LEVELS",
    "MAX_FINDINGS",
    "MAX_IMAGE_BYTES",
    "MAX_SESSION_CACHE_PER_GROUP",
    "OPERATION_ANALYZE",
    "OPERATION_GENERATE_TEXT",
    "SEVERITY_PENALTY",
    "TEXT_FINDING_FIELDS",
    "TEXT_PAYLOAD_FIELDS",
    "VISION_DETECTION_SCHEMA_VERSION",
    "VISION_ONTOLOGY_VERSION",
    "bounded_vision_cache_put",
    "build_vision_localization_input",
    "build_vision_text_prompt",
    "coerce_confidence",
    "compute_image_hash",
    "detection_findings",
    "detection_level_counts",
    "get_vision_display",
    "is_valid_image_hash",
    "is_valid_vision_detection",
    "is_valid_vision_text_payload",
    "legacy_text_preview",
    "legacy_vision_text",
    "level_key",
    "merge_vision_display",
    "normalize_finding_id",
    "normalize_location",
    "normalize_vision_detection",
    "overall_level_for",
    "resolve_content_locale",
    "sniff_image_mime",
    "stale_text_blocks",
    "text_payload_for_store",
    "validate_image_upload",
    "validate_vision_detection",
    "validate_vision_text_payload",
    "vision_action_key",
    "vision_detection_cache_group",
    "vision_detection_cache_key",
    "vision_generation_plan",
    "vision_localization_input_hash",
    "vision_record_id",
    "vision_text_cache_group",
    "vision_text_cache_key",
    "vision_text_needs_generation",
    "vision_text_payload_problems",
]
