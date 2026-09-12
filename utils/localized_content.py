# -*- coding: utf-8 -*-
"""
RiskPilot 统一 localized content 数据层（纯数据，Streamlit-free）。

这个模块是**数据协议**，不是页面逻辑。它只做三件事：

1. 定义唯一的 localized content record schema（``SCHEMA_VERSION = 1``）；
2. 提供读写 / 状态 / freshness 的纯函数 API；
3. 兼容读取旧结构 ``project["ai_text_by_language"]``（只读，从不迁移）。

硬性约束（全部由测试与 AST 守卫强制）
------------------------------------
* **不导入 Streamlit**、不访问 ``st.session_state``、不调用 ``persist`` /
  ``save_db``、不调用任何 AI、不写任何文件。
* **读取函数绝不修改 project**（返回的对象是副本）。
* **写入函数默认 copy-on-write**（返回新的 project），不自动 persist。
* 一个 locale 的写入不得覆盖其他 locale；一个 record_id 不得覆盖其他记录；
  一个项目的写入不得影响其他项目。
* 非法 / 不完整 record **不得**存储（原样返回，不抛异常）。
* 不可信 payload 不得造成原型式键污染、自引用死循环、路径穿越或任意深层对象。
* 只使用 ``json`` 序列化，**不使用 pickle**。

存储形态
--------
新协议（唯一公共 API）::

    project["localized_content"] = {
        "baseline": {
            "project": {                 # record_id（单例内容固定为 "project"）
                "en":    {record...},
                "zh-CN": {record...},
            }
        },
        "logs": {
            "<log_id>": {
                "en": {record...},
            }
        },
    }

record::

    {
        "schema_version": 1,
        "content_type": "baseline",
        "record_id": "project",
        "locale": "zh-CN",
        "payload": {...},
        "input_hash": "<sha256 hex>",
        "generated_at": "2026-09-11T00:00:00+00:00",
        "source": "ai_generated",
    }

旧协议（兼容读取，本阶段不迁移、不改写）::

    project["ai_text_by_language"] = {
        "<locale>": {"<section>": payload},
        "<locale>": {"<section>": {"<record_id>": payload}},
    }
"""

from __future__ import annotations

import copy as _copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from utils.locale_registry import canonicalize_locale

# ============================================================
# SCHEMA 常量
# ============================================================

#: 当前支持的 schema 版本。读取时高于该值的 record 视为「不支持」。
SCHEMA_VERSION = 1

#: 新协议在 project 上的顶层 key。
LOCALIZED_CONTENT_KEY = "localized_content"

#: 旧协议在 project 上的顶层 key（只读兼容）。
LEGACY_ARCHIVE_KEY = "ai_text_by_language"

#: BCP 47 "und"：语言元数据缺失 / 无法识别。
UNDETERMINED_LOCALE = "und"

#: 单例内容（baseline / current risk / vision / report）的固定 record_id。
DEFAULT_RECORD_ID = "project"

# --- content_type -------------------------------------------------

CONTENT_TYPE_BASELINE = "baseline"
CONTENT_TYPE_LOGS = "logs"
CONTENT_TYPE_CURRENT_RISK = "current_risk_explanation"
CONTENT_TYPE_VISION = "vision_analysis"
CONTENT_TYPE_REPORT = "risk_report"

#: 本阶段定义的**全部** content_type（白名单，非白名单一律拒绝）。
CONTENT_TYPES: Tuple[str, ...] = (
    CONTENT_TYPE_BASELINE,
    CONTENT_TYPE_LOGS,
    CONTENT_TYPE_CURRENT_RISK,
    CONTENT_TYPE_VISION,
    CONTENT_TYPE_REPORT,
)

#: 向后兼容别名 -> 规范 content_type。
#: ``"log"`` 是早期 revision 写过 section 名，必须继续可读（但不再写入）。
CONTENT_TYPE_ALIASES: Dict[str, str] = {
    "log": CONTENT_TYPE_LOGS,
    "daily_log": CONTENT_TYPE_LOGS,
    "daily_logs": CONTENT_TYPE_LOGS,
    "logs": CONTENT_TYPE_LOGS,
    "baseline": CONTENT_TYPE_BASELINE,
    "baseline_risk": CONTENT_TYPE_BASELINE,
    "current_risk": CONTENT_TYPE_CURRENT_RISK,
    "current_risk_explanation": CONTENT_TYPE_CURRENT_RISK,
    "vision": CONTENT_TYPE_VISION,
    "vision_analysis": CONTENT_TYPE_VISION,
    "vision_risks": CONTENT_TYPE_VISION,
    "report": CONTENT_TYPE_REPORT,
    "risk_report": CONTENT_TYPE_REPORT,
}

#: 单例内容的默认 record_id。
#:
#: 阶段 4B：``vision_analysis`` 已从单例集合移除——一个项目可以有多张图片，
#: 每张图片各自持有一份文本，record_id 由 ``utils.vision_display`` 生成的
#: ``vis-<digest>`` 显式传入（绑定 project + image hash）。``vision_analysis``
#: 不再有默认 record_id，但 ``resolve_record_id(type, None)`` 的通用回退值仍为
#: "project"，因此读取历史单例 archive 的兼容路径保持不变。
DEFAULT_RECORD_IDS: Dict[str, str] = {
    CONTENT_TYPE_BASELINE: DEFAULT_RECORD_ID,
    CONTENT_TYPE_CURRENT_RISK: DEFAULT_RECORD_ID,
    CONTENT_TYPE_REPORT: DEFAULT_RECORD_ID,
}

#: 单例内容（一个项目只有一份，record_id 固定为 "project"）。
SINGLETON_CONTENT_TYPES = frozenset(DEFAULT_RECORD_IDS)

#: 旧 archive 中"按单例形态存储"的 content_type（迁移工具专用）。
#:
#: 阶段 4B 之前 ``vision_analysis`` 也是单例，因此历史 archive 里它是一段整
#: payload 而不是按 record_id 分组的字典；读取旧数据时必须沿用单例语义，
#: 才能把历史内容识别成 legacy 条目而不是一组 record_id。
LEGACY_SINGLETON_CONTENT_TYPES = frozenset(
    set(DEFAULT_RECORD_IDS) | {CONTENT_TYPE_VISION}
)

# --- source -------------------------------------------------------

SOURCE_AI_GENERATED = "ai_generated"
SOURCE_DETERMINISTIC = "deterministic"
SOURCE_TRANSLATED = "translated"
SOURCE_MIGRATED = "migrated"
SOURCE_LEGACY = "legacy"
SOURCE_USER_AUTHORED = "user_authored"

#: 白名单 source 集合。
SOURCES: Tuple[str, ...] = (
    SOURCE_AI_GENERATED,
    SOURCE_DETERMINISTIC,
    SOURCE_TRANSLATED,
    SOURCE_MIGRATED,
    SOURCE_LEGACY,
    SOURCE_USER_AUTHORED,
)

# --- 统一状态 -----------------------------------------------------

STATUS_AVAILABLE = "available"
STATUS_STALE = "stale"
STATUS_MISSING = "missing"
STATUS_LEGACY_COMPATIBLE = "legacy_compatible"
STATUS_DETERMINISTIC = "deterministic"
STATUS_INVALID = "invalid"

#: 全部合法状态。
STATUSES: Tuple[str, ...] = (
    STATUS_AVAILABLE,
    STATUS_STALE,
    STATUS_MISSING,
    STATUS_LEGACY_COMPATIBLE,
    STATUS_DETERMINISTIC,
    STATUS_INVALID,
)

# --- 状态来源 -----------------------------------------------------

#: record 来自新协议。
SOURCE_TAG_LOCALIZED_CONTENT = "localized_content"

#: 内容来自旧 ``ai_text_by_language``。
SOURCE_TAG_LEGACY_ARCHIVE = "ai_text_by_language"

# --- reason（missing 与 empty、以及各种失败原因的明确区分） ---------

REASON_NO_RECORD = "no_record_for_locale"
REASON_NO_CONTENT = "no_content"
REASON_EMPTY_PAYLOAD = "empty_payload"
REASON_INPUT_HASH_MISSING = "input_hash_missing"
REASON_INPUT_HASH_MISMATCH = "input_hash_mismatch"
REASON_LOCALE_MISMATCH = "locale_mismatch"
REASON_UNSUPPORTED_SCHEMA = "unsupported_schema_version"
REASON_INVALID_RECORD = "invalid_record"
REASON_UNKNOWN_CONTENT_TYPE = "unknown_content_type"
REASON_UNSAFE_RECORD_ID = "unsafe_record_id"

# ============================================================
# 安全限制（避免项目文件无限膨胀 / 恶意结构）
# ============================================================

#: content_type 最大长度。
MAX_CONTENT_TYPE_LENGTH = 64

#: record_id 最大长度。
MAX_RECORD_ID_LENGTH = 128

#: locale 最大长度。
MAX_LOCALE_LENGTH = 64

#: payload 最大嵌套深度（根为 0 层）。
MAX_PAYLOAD_DEPTH = 8

#: 单个 dict 的最大键数。
MAX_PAYLOAD_KEYS = 200

#: 单个 list 的最大项数。
MAX_PAYLOAD_ITEMS = 500

#: 单个字符串的最大长度。
MAX_STRING_LENGTH = 20000

#: 单个键名的最大长度。
MAX_KEY_LENGTH = 128

#: payload 的递归节点总数上限。
MAX_PAYLOAD_NODES = 5000

#: payload 序列化后的最大字节数。
MAX_PAYLOAD_BYTES = 200000

#: 允许的 schema_version 上限（防御性，避免巨大整数）。
MAX_SCHEMA_VERSION = 1000

#: 原型式键污染 / Python 反射型危险键名。
UNSAFE_KEYS = frozenset(
    {
        "__proto__",
        "constructor",
        "prototype",
        "__class__",
        "__dict__",
        "__globals__",
        "__bases__",
        "__mro__",
        "__subclasses__",
        "__reduce__",
        "__reduce_ex__",
        "__getattribute__",
        "__setattr__",
        "__init__",
        "__del__",
    }
)

#: record_id 允许的字符集（避免路径穿越与控制字符）。
_SAFE_RECORD_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]+$")

#: 控制字符。
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")

#: 十六进制 sha256。
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# ============================================================
# 契约：可翻译字段 / 绝不可翻译字段（阶段 4 接入时使用）
# ============================================================

#: 这些**数值 / 几何 / 分类**字段绝不允许进入可翻译 payload：
#: 翻译结果一旦回写就会污染风险计算与视觉检测结果。
NON_TRANSLATABLE_FIELDS = frozenset(
    {
        "penalty",
        "score",
        "level",
        "confidence",
        "coordinates",
        "bbox",
        "box",
        "geometry",
        "severity",
        "likelihood",
        "exposure",
        "raw_risk",
        "risk_level",
        "risk_score",
        "risk_count",
        "visual_risk_penalty",
    }
)

#: 每个 content_type 允许 / 期望的可翻译字段契约（阶段 4 接线前的声明）。
CONTENT_TYPE_CONTRACTS: Dict[str, Dict[str, Any]] = {
    CONTENT_TYPE_BASELINE: {
        "singleton": True,
        "text_fields": ("risk_chains", "impacts", "actions"),
        "text_list_fields": ("risk_chains", "impacts", "actions"),
    },
    CONTENT_TYPE_LOGS: {
        "singleton": False,
        "text_fields": ("summary",),
        "text_list_fields": (),
        "nested_text_fields": ("title", "reason", "suggestion"),
    },
    CONTENT_TYPE_CURRENT_RISK: {
        "singleton": True,
        "text_fields": ("summary", "risk_level_text"),
        "text_list_fields": ("reasons", "recommendations"),
    },
    CONTENT_TYPE_VISION: {
        # 阶段 4B：一图一记录（record_id = vis-<digest>，绑定 project + image
        # hash），不再是单例；payload 只携带人类可读文本。
        "singleton": False,
        "text_fields": ("summary",),
        "text_list_fields": (),
        "nested_text_fields": ("title", "reason", "recommendation"),
    },
    CONTENT_TYPE_REPORT: {
        "singleton": True,
        "text_fields": ("title", "summary", "body"),
        "text_list_fields": (),
    },
}


# ============================================================
# 规范化
# ============================================================


def normalize_content_type(value: Any) -> Optional[str]:
    """
    规范 content_type；非白名单 / 非法输入返回 ``None``。

    别名（``"log"`` / ``"daily_logs"`` / ``"vision"`` …）会映射到规范值，
    因此旧 section 名仍然可读，但新写入一律使用规范值。
    """
    if not isinstance(value, str):
        return None

    cleaned = value.strip()

    if not cleaned or len(cleaned) > MAX_CONTENT_TYPE_LENGTH:
        return None

    if _CONTROL_CHARS_RE.search(cleaned):
        return None

    lowered = cleaned.casefold()

    if lowered in CONTENT_TYPE_ALIASES:
        return CONTENT_TYPE_ALIASES[lowered]

    if lowered in CONTENT_TYPES:
        return lowered

    return None


def normalize_record_id(value: Any) -> Optional[str]:
    """
    规范 record_id；不安全 / 非法输入返回 ``None``。

    ``None`` 映射到单例内容的固定 id :data:`DEFAULT_RECORD_ID`（``"project"``）。

    拒绝：空值、超长、控制字符、路径分隔符、``..``、纯标点、非白名单字符。
    """
    if value is None:
        return DEFAULT_RECORD_ID

    if not isinstance(value, str):
        return None

    cleaned = value.strip()

    if not cleaned or len(cleaned) > MAX_RECORD_ID_LENGTH:
        return None

    if _CONTROL_CHARS_RE.search(cleaned):
        return None

    if not _SAFE_RECORD_ID_RE.match(cleaned):
        return None

    # 路径穿越 / 相对路径。
    if ".." in cleaned:
        return None

    # 纯标点（"." / "./" / "___" …）没有信息量。
    if not any(char.isalnum() for char in cleaned):
        return None

    return cleaned


def normalize_content_locale(value: Any) -> Optional[str]:
    """
    规范 record 的 locale。

    接受 registry 中**已注册**的 locale（含 planned：它们可能有预生成内容），
    以及显式的 :data:`UNDETERMINED_LOCALE`（``"und"``）。
    其他一律返回 ``None``。
    """
    if not isinstance(value, str):
        return None

    cleaned = value.strip()

    if not cleaned or len(cleaned) > MAX_LOCALE_LENGTH:
        return None

    if cleaned.casefold() == UNDETERMINED_LOCALE:
        return UNDETERMINED_LOCALE

    return canonicalize_locale(cleaned)


def resolve_record_id(content_type: Any, record_id: Any = None) -> Optional[str]:
    """按 content_type 解析出实际的 record_id（``None`` -> 单例 id）。"""
    canonical_type = normalize_content_type(content_type)

    if canonical_type is None:
        return None

    if record_id is None:
        return DEFAULT_RECORD_IDS.get(canonical_type, DEFAULT_RECORD_ID)

    return normalize_record_id(record_id)


# ============================================================
# payload 校验（JSON-safe + 规模限制 + 无原型污染 / 无自引用）
# ============================================================


def _walk_payload(
    node: Any,
    path: str,
    depth: int,
    problems: List[str],
    counters: Dict[str, int],
    seen: set,
) -> None:
    """递归检查一个 payload 节点，把问题追加到 ``problems``。"""
    if depth > MAX_PAYLOAD_DEPTH:
        problems.append(f"{path}: 嵌套超过 {MAX_PAYLOAD_DEPTH} 层")
        return

    counters["nodes"] += 1

    if counters["nodes"] > MAX_PAYLOAD_NODES:
        if not counters["overflow"]:
            counters["overflow"] = 1
            problems.append(f"payload: 节点数超过 {MAX_PAYLOAD_NODES}")
        return

    if node is None or isinstance(node, bool):
        return

    if isinstance(node, str):
        if len(node) > MAX_STRING_LENGTH:
            problems.append(f"{path}: 字符串超过 {MAX_STRING_LENGTH} 字符")
        return

    if isinstance(node, int):
        return

    if isinstance(node, float):
        if not math.isfinite(node):
            problems.append(f"{path}: 非法浮点值（NaN / Infinity）")
        return

    if isinstance(node, dict):
        marker = id(node)

        if marker in seen:
            problems.append(f"{path}: 存在自引用结构")
            return

        if len(node) > MAX_PAYLOAD_KEYS:
            problems.append(f"{path}: dict 超过 {MAX_PAYLOAD_KEYS} 个键")

        seen.add(marker)

        for key, value in node.items():
            if not isinstance(key, str):
                problems.append(
                    f"{path}: 键必须是字符串（实际 {type(key).__name__}）"
                )
                continue

            if len(key) > MAX_KEY_LENGTH:
                problems.append(f"{path}: 键名超过 {MAX_KEY_LENGTH} 字符")
                continue

            if _CONTROL_CHARS_RE.search(key):
                problems.append(f"{path}: 键名含控制字符")
                continue

            if key.strip().casefold() in UNSAFE_KEYS:
                problems.append(f"{path}.{key}: 不安全的键名（原型污染风险）")
                continue

            _walk_payload(
                value,
                f"{path}.{key}",
                depth + 1,
                problems,
                counters,
                seen,
            )

        seen.discard(marker)
        return

    if isinstance(node, (list, tuple)):
        if isinstance(node, tuple):
            problems.append(f"{path}: 不支持 tuple（JSON 往返会变成 list）")
            return

        marker = id(node)

        if marker in seen:
            problems.append(f"{path}: 存在自引用结构")
            return

        if len(node) > MAX_PAYLOAD_ITEMS:
            problems.append(f"{path}: 列表超过 {MAX_PAYLOAD_ITEMS} 项")

        seen.add(marker)

        for index, value in enumerate(node):
            _walk_payload(
                value,
                f"{path}[{index}]",
                depth + 1,
                problems,
                counters,
                seen,
            )

        seen.discard(marker)
        return

    # bytes / bytearray / set / frozenset / complex / 自定义对象 …
    problems.append(f"{path}: 不支持的类型 {type(node).__name__}")


def validate_payload(payload: Any) -> List[str]:
    """
    校验 payload。返回问题列表；**空列表表示合法**。

    合法意味着：顶层是 dict、只含 JSON 原生类型、规模在限制内、
    键名安全、无自引用、序列化后不超过 :data:`MAX_PAYLOAD_BYTES`。
    """
    if not isinstance(payload, dict):
        return ["payload 必须是 JSON-compatible dict"]

    problems: List[str] = []
    counters = {"nodes": 0, "overflow": 0}

    _walk_payload(payload, "payload", 0, problems, counters, set())

    if problems:
        return problems

    try:
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except Exception as exc:
        return [f"payload 无法序列化为 JSON: {type(exc).__name__}"]

    if len(serialized.encode("utf-8")) > MAX_PAYLOAD_BYTES:
        return [f"payload 序列化超过 {MAX_PAYLOAD_BYTES} 字节"]

    return []


def _collect_forbidden_payload_keys(
    node: Any,
    path: str,
    found: List[str],
    depth: int = 0,
) -> None:
    """递归收集出现在可翻译载荷里的、禁止翻译的数值字段路径。"""
    if depth > MAX_PAYLOAD_DEPTH:
        return

    if isinstance(node, dict):
        for key, value in node.items():
            if not isinstance(key, str):
                continue

            location = f"{path}.{key}" if path else key

            if key.strip().casefold() in NON_TRANSLATABLE_FIELDS:
                found.append(location)

            _collect_forbidden_payload_keys(value, location, found, depth + 1)

        return

    if isinstance(node, list):
        for index, value in enumerate(node):
            _collect_forbidden_payload_keys(
                value, f"{path}[{index}]", found, depth + 1
            )


def validate_translatable_payload(content_type: Any, payload: Any) -> List[str]:
    """
    校验「可翻译 payload」是否符合 content_type 契约。

    与 :func:`validate_payload` 的区别：这里**额外拒绝**把
    :data:`NON_TRANSLATABLE_FIELDS`（penalty / score / level / confidence /
    coordinates / geometry / severity / likelihood / exposure …）放进可翻译
    载荷——**任意深度**都不允许，因为它们必须留在语言无关的分析结果里，
    一旦被翻译结果回写就会污染风险计算与视觉检测数值。

    本阶段只做契约校验，不接线任何页面。
    """
    problems = validate_payload(payload)

    if problems:
        return problems

    canonical = normalize_content_type(content_type)

    if canonical is None:
        return [f"未知 content_type: {content_type!r}"]

    offenders: List[str] = []

    _collect_forbidden_payload_keys(payload, "", offenders)

    for location in offenders:
        problems.append(f"可翻译 payload 不得包含数值字段 {location!r}")

    return problems


# ============================================================
# HASH / FRESHNESS
# ============================================================


def _canonical_json(payload: Any) -> Optional[str]:
    """稳定序列化：key 排序、紧凑分隔符、非 ASCII 原样保留。"""
    try:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except Exception:
        return None


def compute_input_hash(payload: Any) -> Optional[str]:
    """
    输入载荷的稳定 SHA-256（十六进制）。非法 payload 返回 ``None``。

    **为什么 hash 不含 locale**：hash 描述的是「生成这段文字所依据的输入」
    （项目条件、分析数值、日志原文……），这些输入与目标语言无关；目标语言
    单独保存在 ``record["locale"]``。

    因此 freshness 比较**必须同时**验证 hash 与 locale：
    :func:`is_content_fresh` 会一并检查 ``record["locale"]``，避免「同一份
    输入、另一种语言」的旧记录被误判为最新。

    dict 的键顺序不影响结果（``sort_keys=True``）。
    """
    if validate_payload(payload):
        return None

    canonical = _canonical_json(payload)

    if canonical is None:
        return None

    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def is_content_fresh(
    record: Any,
    input_hash: Any,
    locale: Any = None,
) -> bool:
    """
    判断 record 相对给定输入是否仍然新鲜。

    返回 ``False``（即 stale）当且仅当：
    * record 不是 dict；
    * record 自身**没有** input_hash（旧 record 缺 hash —— 绝不伪装 fresh）；
    * 传入的 input_hash 缺失或非法；
    * 两者不相等；
    * 显式传入 ``locale`` 且与 ``record["locale"]`` 不一致。
    """
    if not isinstance(record, dict):
        return False

    stored = record.get("input_hash")

    if not isinstance(stored, str) or not stored:
        return False

    if not isinstance(input_hash, str) or not input_hash:
        return False

    if stored != input_hash:
        return False

    if locale is not None:
        expected = normalize_content_locale(locale)
        actual = normalize_content_locale(record.get("locale"))

        if expected is None or actual is None or expected != actual:
            return False

    return True


def utc_now_iso() -> str:
    """带时区的 ISO 8601 UTC 时间戳（例如 ``2026-09-11T00:00:00+00:00``）。"""
    return datetime.now(timezone.utc).isoformat()


def is_iso_utc_timestamp(value: Any) -> bool:
    """True 表示 ``value`` 是**带时区**的 ISO 8601 时间戳。"""
    if not isinstance(value, str):
        return False

    text = value.strip()

    if not text:
        return False

    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"

    try:
        parsed = datetime.fromisoformat(text)
    except Exception:
        return False

    return parsed.tzinfo is not None and parsed.utcoffset() is not None


# ============================================================
# RECORD 构造与校验
# ============================================================


def make_localized_record(
    content_type: Any,
    record_id: Any,
    locale: Any,
    payload: Any,
    source: Any = SOURCE_AI_GENERATED,
    input_hash: Any = None,
    generated_at: Any = None,
    schema_version: Any = SCHEMA_VERSION,
    compute_hash: bool = True,
) -> Optional[Dict[str, Any]]:
    """
    构造一个合法的 localized content record；不合法返回 ``None``。

    * ``locale`` 必须已注册（或显式 ``"und"``）；
    * ``payload`` 必须是受限的 JSON-compatible dict；
    * ``input_hash`` 省略时按 ``payload`` 自动计算；``compute_hash=False``
      表示"这份内容没有可靠输入 hash"（例如 legacy 转换），此时显式留空；
    * ``generated_at`` 省略时使用当前 UTC 时间。
    """
    canonical_type = normalize_content_type(content_type)

    if canonical_type is None:
        return None

    resolved_id = resolve_record_id(canonical_type, record_id)

    if resolved_id is None:
        return None

    canonical_locale = normalize_content_locale(locale)

    if canonical_locale is None:
        return None

    if not isinstance(source, str) or source not in SOURCES:
        return None

    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or not (1 <= schema_version <= MAX_SCHEMA_VERSION)
    ):
        return None

    if validate_payload(payload):
        return None

    if input_hash is None:
        resolved_hash = compute_input_hash(payload) if compute_hash else None
    elif isinstance(input_hash, str) and input_hash.strip():
        resolved_hash = input_hash.strip()
    else:
        return None

    timestamp = generated_at if generated_at is not None else utc_now_iso()

    record: Dict[str, Any] = {
        "schema_version": schema_version,
        "content_type": canonical_type,
        "record_id": resolved_id,
        "locale": canonical_locale,
        "payload": _copy.deepcopy(payload),
        "input_hash": resolved_hash,
        "generated_at": timestamp,
        "source": source,
    }

    if validate_localized_record(record, canonical_type):
        return None

    return record


def validate_localized_record(
    record: Any,
    expected_type: Any = None,
) -> List[str]:
    """
    校验一条 record。返回问题列表；**空列表表示合法**。

    ``expected_type`` 非空时，还会检查 ``record["content_type"]`` 是否匹配。
    """
    problems: List[str] = []

    if not isinstance(record, dict):
        return ["record 必须是 dict"]

    # --- schema_version ---
    version = record.get("schema_version")

    if (
        not isinstance(version, int)
        or isinstance(version, bool)
        or version < 1
        or version > MAX_SCHEMA_VERSION
    ):
        problems.append(f"schema_version 非法: {version!r}")
    elif version != SCHEMA_VERSION:
        problems.append(
            f"不支持的 schema_version: {version!r}（当前支持 {SCHEMA_VERSION}）"
        )

    # --- content_type ---
    canonical_type = normalize_content_type(record.get("content_type"))

    if canonical_type is None:
        problems.append(f"content_type 非法: {record.get('content_type')!r}")
    elif record.get("content_type") != canonical_type:
        problems.append(
            f"content_type 必须是规范值 {canonical_type!r}，"
            f"实际 {record.get('content_type')!r}"
        )

    if expected_type is not None:
        expected = normalize_content_type(expected_type)

        if expected is None:
            problems.append(f"expected_type 非法: {expected_type!r}")
        elif canonical_type is not None and canonical_type != expected:
            problems.append(
                f"content_type 不匹配: 期望 {expected!r}，实际 {canonical_type!r}"
            )

    # --- record_id ---
    raw_id = record.get("record_id")
    normalized_id = resolve_record_id(canonical_type, raw_id)

    if normalized_id is None:
        problems.append(f"record_id 非法或不安全: {raw_id!r}")
    elif raw_id != normalized_id:
        problems.append(
            f"record_id 必须是规范值 {normalized_id!r}，实际 {raw_id!r}"
        )

    # --- locale ---
    raw_locale = record.get("locale")
    normalized_locale = normalize_content_locale(raw_locale)

    if normalized_locale is None:
        problems.append(f"locale 非法或未注册: {raw_locale!r}")
    elif raw_locale != normalized_locale:
        problems.append(
            f"locale 必须是规范值 {normalized_locale!r}，实际 {raw_locale!r}"
        )

    # --- payload ---
    payload = record.get("payload")

    if not isinstance(payload, dict):
        problems.append("payload 必须是 dict")
    else:
        problems.extend(validate_payload(payload))

    # --- source ---
    source = record.get("source")

    if not isinstance(source, str) or source not in SOURCES:
        problems.append(f"source 非法: {source!r}")

    # --- input_hash（可选；有则必须是 sha256 hex） ---
    stored_hash = record.get("input_hash")

    if stored_hash is not None:
        if not isinstance(stored_hash, str) or not _SHA256_RE.match(stored_hash):
            problems.append(f"input_hash 非法: {stored_hash!r}")

    # --- generated_at（必须带时区） ---
    timestamp = record.get("generated_at")

    if not is_iso_utc_timestamp(timestamp):
        problems.append(f"generated_at 必须是带时区的 ISO 8601: {timestamp!r}")

    return problems


# ============================================================
# 读取（永不修改 project）
# ============================================================


def _raw_record(
    project: Any,
    content_type: str,
    record_id: str,
    locale: str,
) -> Optional[Dict[str, Any]]:
    """直接取出存储中的 record（不做校验、不复制）。"""
    if not isinstance(project, dict):
        return None

    container = project.get(LOCALIZED_CONTENT_KEY)

    if not isinstance(container, dict):
        return None

    bucket = container.get(content_type)

    if not isinstance(bucket, dict):
        return None

    records = bucket.get(record_id)

    if not isinstance(records, dict):
        return None

    record = records.get(locale)

    return record if isinstance(record, dict) else None


def localized_content_get(
    project: Any,
    content_type: Any,
    locale: Any,
    record_id: Any = None,
) -> Optional[Dict[str, Any]]:
    """
    读取一条 record（合法才返回；返回**副本**，不暴露内部对象）。

    绝不修改 ``project``。非法 record 返回 ``None``（其状态可由
    :func:`localized_content_status` 描述为 ``invalid``）。
    """
    canonical_type = normalize_content_type(content_type)

    if canonical_type is None:
        return None

    resolved_id = resolve_record_id(canonical_type, record_id)

    if resolved_id is None:
        return None

    canonical_locale = normalize_content_locale(locale)

    if canonical_locale is None:
        return None

    record = _raw_record(project, canonical_type, resolved_id, canonical_locale)

    if record is None:
        return None

    if validate_localized_record(record, canonical_type):
        return None

    return _copy.deepcopy(record)


def localized_content_languages(
    project: Any,
    content_type: Any,
    record_id: Any = None,
) -> List[str]:
    """
    该 content_type / record_id 下**实际存在且有效**的 locale，已排序。

    只统计校验通过且 payload 非空的记录，因此该列表可以安全地用来区分
    「存在另一个语言版本」与「完全没有内容」。
    """
    canonical_type = normalize_content_type(content_type)

    if canonical_type is None:
        return []

    resolved_id = resolve_record_id(canonical_type, record_id)

    if resolved_id is None or not isinstance(project, dict):
        return []

    container = project.get(LOCALIZED_CONTENT_KEY)

    if not isinstance(container, dict):
        return []

    bucket = container.get(canonical_type)

    if not isinstance(bucket, dict):
        return []

    records = bucket.get(resolved_id)

    if not isinstance(records, dict):
        return []

    found: List[str] = []

    for locale, record in records.items():
        if not isinstance(record, dict):
            continue

        if validate_localized_record(record, canonical_type):
            continue

        payload = record.get("payload")

        if not isinstance(payload, dict) or not payload:
            continue

        if isinstance(locale, str) and locale not in found:
            found.append(locale)

    return sorted(found)


# ============================================================
# 写入（默认 copy-on-write）
# ============================================================


def _path_copy(
    project: Dict[str, Any],
    content_type: str,
    record_id: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    复制**完整写入路径**上的每一个容器（结构共享 + 写时复制）。

    必须逐层复制 ``project -> localized_content -> content_type -> record_id``，
    否则会共享内层 dict，写一个新 locale 就会"穿透"污染调用方（以及其他
    locale / record）。

    返回 ``(新的 project, 已脱离原对象的 locale 层 records 字典)``：
    调用方只需要往 ``records[locale]`` 写一个叶子。
    """
    target = dict(project)

    container = project.get(LOCALIZED_CONTENT_KEY)
    container = dict(container) if isinstance(container, dict) else {}
    target[LOCALIZED_CONTENT_KEY] = container

    bucket = container.get(content_type)
    bucket = dict(bucket) if isinstance(bucket, dict) else {}
    container[content_type] = bucket

    records = bucket.get(record_id)
    records = dict(records) if isinstance(records, dict) else {}
    bucket[record_id] = records

    return target, records


def _ensure_path(
    project: Dict[str, Any],
    content_type: str,
    record_id: str,
) -> Dict[str, Any]:
    """**就地**在 project 上确保写入路径存在，返回 locale 层 records 字典。"""
    container = project.get(LOCALIZED_CONTENT_KEY)

    if not isinstance(container, dict):
        container = {}
        project[LOCALIZED_CONTENT_KEY] = container

    bucket = container.get(content_type)

    if not isinstance(bucket, dict):
        bucket = {}
        container[content_type] = bucket

    records = bucket.get(record_id)

    if not isinstance(records, dict):
        records = {}
        bucket[record_id] = records

    return records


def localized_content_put(
    project: Any,
    record: Any,
    copy_on_write: bool = True,
) -> Any:
    """
    写入一条 record。

    * 非法 / 不完整 record **不写入**，原样返回 ``project``（不抛异常）；
    * ``copy_on_write=True``（默认）返回**新的** project，只复制路径上的容器，
      输入 ``project`` 保持不变；
    * ``copy_on_write=False`` 就地写入并返回同一个对象（供 app.py 的
      ``ai_text_store`` 保持既有"就地修改 + persist()"行为）；
    * **不自动 persist、不调用 AI、不迁移旧数据**。
    """
    if not isinstance(project, dict):
        return project

    if validate_localized_record(record):
        return project

    canonical_type = record["content_type"]
    resolved_id = record["record_id"]
    canonical_locale = record["locale"]

    if copy_on_write:
        target, records = _path_copy(project, canonical_type, resolved_id)
    else:
        target = project
        records = _ensure_path(target, canonical_type, resolved_id)

    # 只覆盖这一个叶子；其他 locale / record_id / 项目完全不受影响。
    records[canonical_locale] = _copy.deepcopy(record)

    return target


def localized_content_remove_from_copy(
    project: Any,
    content_type: Any,
    locale: Any,
    record_id: Any = None,
) -> Any:
    """
    返回一个**删除了指定 record** 的 project 副本（供测试 / 迁移工具使用）。

    * 输入 ``project`` 永远不被修改；
    * 删除后为空的容器会被清理，避免留下空 dict；
    * 不存在的目标：返回等值副本。
    """
    if not isinstance(project, dict):
        return project

    canonical_type = normalize_content_type(content_type)

    if canonical_type is None:
        return _copy.deepcopy(project)

    resolved_id = resolve_record_id(canonical_type, record_id)

    if resolved_id is None:
        return _copy.deepcopy(project)

    canonical_locale = normalize_content_locale(locale)

    if canonical_locale is None:
        return _copy.deepcopy(project)

    target = dict(project)

    container = project.get(LOCALIZED_CONTENT_KEY)

    if not isinstance(container, dict):
        return target

    container = dict(container)
    target[LOCALIZED_CONTENT_KEY] = container

    bucket = container.get(canonical_type)

    if not isinstance(bucket, dict):
        return target

    bucket = dict(bucket)
    container[canonical_type] = bucket

    records = bucket.get(resolved_id)

    if not isinstance(records, dict):
        return target

    records = dict(records)
    bucket[resolved_id] = records

    records.pop(canonical_locale, None)

    if not records:
        bucket.pop(resolved_id, None)

    if not bucket:
        container.pop(canonical_type, None)

    if not container:
        target.pop(LOCALIZED_CONTENT_KEY, None)

    return target


# ============================================================
# 状态（统一结果对象）
# ============================================================


def _status(
    status: str,
    *,
    content_type: Optional[str],
    record_id: Optional[str],
    locale: Optional[str],
    saved_locales: Optional[List[str]] = None,
    source: Optional[str] = None,
    record: Optional[Dict[str, Any]] = None,
    needs_generation: bool = False,
    is_stale: bool = False,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """构造统一的状态对象。"""
    return {
        "status": status,
        "content_type": content_type,
        "record_id": record_id,
        "source": source,
        "locale": locale,
        "saved_locales": list(saved_locales or []),
        "record": record,
        "needs_generation": bool(needs_generation),
        "is_stale": bool(is_stale),
        "reason": reason,
    }


def localized_content_status(
    project: Any,
    content_type: Any,
    locale: Any,
    record_id: Any = None,
    input_hash: Any = None,
) -> Dict[str, Any]:
    """
    统一状态查询。页面不再各自猜测"有 / 没有 / 是别的语言 / 过期"。

    状态取值见 :data:`STATUSES`：

    * ``available``          当前 locale 有合法且非空的内容，且（若给了
                             ``input_hash``）hash 与 locale 都匹配；
    * ``stale``              内容存在但已过期（hash 不同 / 缺 hash）；
    * ``missing``            当前 locale 没有内容（``reason`` 区分
                             "仅缺这个语言" 与 "完全没内容"，以及空载荷）；
    * ``legacy_compatible``  只有旧 ``ai_text_by_language`` 里有内容；
    * ``deterministic``      record 的 source 是确定性模板（可在显示层本地翻译）；
    * ``invalid``            存了但结构不合法（永不当作可用内容）。

    ``saved_locales`` 只来自实际存在且有效的记录。
    ``needs_generation`` 表示需要用户主动生成当前语言版本。
    """
    canonical_type = normalize_content_type(content_type)

    if canonical_type is None:
        return _status(
            STATUS_INVALID,
            content_type=None,
            record_id=None,
            locale=None,
            needs_generation=True,
            reason=REASON_UNKNOWN_CONTENT_TYPE,
        )

    resolved_id = resolve_record_id(canonical_type, record_id)

    if resolved_id is None:
        return _status(
            STATUS_INVALID,
            content_type=canonical_type,
            record_id=None,
            locale=None,
            needs_generation=True,
            reason=REASON_UNSAFE_RECORD_ID,
        )

    canonical_locale = normalize_content_locale(locale) or UNDETERMINED_LOCALE

    saved = localized_content_languages(project, canonical_type, resolved_id)

    record = _raw_record(project, canonical_type, resolved_id, canonical_locale)

    if record is not None:
        problems = validate_localized_record(record, canonical_type)

        if problems:
            return _status(
                STATUS_INVALID,
                content_type=canonical_type,
                record_id=resolved_id,
                locale=canonical_locale,
                saved_locales=saved,
                source=SOURCE_TAG_LOCALIZED_CONTENT,
                record=None,
                needs_generation=True,
                reason=problems[0],
            )

        payload = record.get("payload")

        if not isinstance(payload, dict) or not payload:
            return _status(
                STATUS_MISSING,
                content_type=canonical_type,
                record_id=resolved_id,
                locale=canonical_locale,
                saved_locales=saved,
                source=SOURCE_TAG_LOCALIZED_CONTENT,
                record=_copy.deepcopy(record),
                needs_generation=True,
                reason=REASON_EMPTY_PAYLOAD,
            )

        if record.get("source") == SOURCE_DETERMINISTIC:
            return _status(
                STATUS_DETERMINISTIC,
                content_type=canonical_type,
                record_id=resolved_id,
                locale=canonical_locale,
                saved_locales=saved,
                source=SOURCE_TAG_LOCALIZED_CONTENT,
                record=_copy.deepcopy(record),
                needs_generation=False,
            )

        stored_hash = record.get("input_hash")

        if not isinstance(stored_hash, str) or not stored_hash:
            # 旧 record 缺 hash：状态明确为 stale，绝不伪装 fresh。
            return _status(
                STATUS_STALE,
                content_type=canonical_type,
                record_id=resolved_id,
                locale=canonical_locale,
                saved_locales=saved,
                source=SOURCE_TAG_LOCALIZED_CONTENT,
                record=_copy.deepcopy(record),
                is_stale=True,
                reason=REASON_INPUT_HASH_MISSING,
            )

        if input_hash is not None and not is_content_fresh(
            record,
            input_hash,
            canonical_locale,
        ):
            reason = REASON_INPUT_HASH_MISMATCH

            expected_locale = normalize_content_locale(canonical_locale)

            if expected_locale is not None and record.get("locale") != expected_locale:
                reason = REASON_LOCALE_MISMATCH

            return _status(
                STATUS_STALE,
                content_type=canonical_type,
                record_id=resolved_id,
                locale=canonical_locale,
                saved_locales=saved,
                source=SOURCE_TAG_LOCALIZED_CONTENT,
                record=_copy.deepcopy(record),
                is_stale=True,
                reason=reason,
            )

        return _status(
            STATUS_AVAILABLE,
            content_type=canonical_type,
            record_id=resolved_id,
            locale=canonical_locale,
            saved_locales=saved,
            source=SOURCE_TAG_LOCALIZED_CONTENT,
            record=_copy.deepcopy(record),
            needs_generation=False,
        )

    # --- 回退：旧 ai_text_by_language（只读，绝不迁移 / 绝不跨语言） ---
    legacy_record_id = None if resolved_id == DEFAULT_RECORD_ID else resolved_id
    legacy_payload = legacy_ai_text_get(
        project,
        canonical_type,
        canonical_locale,
        legacy_record_id,
    )

    if isinstance(legacy_payload, dict) and legacy_payload:
        return _status(
            STATUS_LEGACY_COMPATIBLE,
            content_type=canonical_type,
            record_id=resolved_id,
            locale=canonical_locale,
            saved_locales=saved,
            source=SOURCE_TAG_LEGACY_ARCHIVE,
            needs_generation=False,
        )

    return _status(
        STATUS_MISSING,
        content_type=canonical_type,
        record_id=resolved_id,
        locale=canonical_locale,
        saved_locales=saved,
        needs_generation=True,
        # saved 非空 -> 只是缺这个语言版本；saved 为空 -> 完全没有内容。
        reason=REASON_NO_RECORD if saved else REASON_NO_CONTENT,
    )


# ============================================================
# 旧结构兼容（只读）
# ============================================================

#: 旧 section 名的兼容读取顺序（``logs`` 优先，``log`` 仅作安全网）。
_LEGACY_SECTION_ALIASES: Dict[str, Tuple[str, ...]] = {
    CONTENT_TYPE_LOGS: ("logs", "log"),
}

_LEGACY_ARCHIVE_ALIASES: Dict[str, Tuple[str, ...]] = {
    "logs": ("logs", "log"),
    "log": ("log", "logs"),
}


def legacy_ai_text_get(
    project: Any,
    section: Any,
    locale: Any,
    record_id: Any = None,
) -> Optional[Any]:
    """
    只读读取旧 ``project["ai_text_by_language"]``。

    形态：
    ``project["ai_text_by_language"][locale][section]``             （单例）
    ``project["ai_text_by_language"][locale][section][record_id]`` （多记录）

    * 绝不修改 ``project``、绝不迁移、绝不写回；
    * 只读**目标 locale 的桶**，因此不会跨语言 fallback；
    * ``"log"`` / ``"logs"`` 互为安全网（``"logs"`` 优先）。
    """
    if not isinstance(project, dict):
        return None

    archive = project.get(LEGACY_ARCHIVE_KEY)

    if not isinstance(archive, dict):
        return None

    if not isinstance(locale, str):
        return None

    raw_locale = locale.strip()

    if not raw_locale:
        return None

    canonical_locale = normalize_content_locale(raw_locale)

    # 1) 规范 key；2) 原样 key（旧数据可能用未注册写法）；3) 近似 key 扫描。
    bucket = archive.get(canonical_locale) if canonical_locale else None

    if not isinstance(bucket, dict) and raw_locale != canonical_locale:
        bucket = archive.get(raw_locale)

    if not isinstance(bucket, dict) and canonical_locale:
        for key, value in archive.items():
            if isinstance(key, str) and canonicalize_locale(key) == canonical_locale:
                bucket = value
                break

    if not isinstance(bucket, dict):
        return None

    if not isinstance(section, str):
        return None

    cleaned = section.strip()

    if not cleaned:
        return None

    candidates = _LEGACY_ARCHIVE_ALIASES.get(cleaned.casefold(), (cleaned,))

    for name in candidates:
        payload = bucket.get(name)

        if payload is None:
            continue

        if record_id is None:
            return payload

        if isinstance(payload, dict):
            found = payload.get(str(record_id))

            if found is not None:
                return found

    return None


def legacy_ai_text_to_record(
    project: Any,
    section: Any,
    locale: Any,
    record_id: Any = None,
    source: Any = SOURCE_LEGACY,
    generated_at: Any = None,
) -> Optional[Dict[str, Any]]:
    """
    把旧 archive 里的一条内容**转换成** record（纯函数，不写回 project）。

    转换出的 record 的 ``input_hash`` 为 ``None``：旧数据没有可靠的输入 hash，
    因此 :func:`is_content_fresh` 对它恒为 ``False``，状态会明确显示为
    ``stale``，绝不会伪装成最新内容。

    本阶段不调用它做任何自动迁移——它只供显式迁移工具与测试使用。
    """
    canonical_type = normalize_content_type(section)

    if canonical_type is None:
        return None

    resolved_id = resolve_record_id(canonical_type, record_id)

    if resolved_id is None:
        return None

    legacy_record_id = None if resolved_id == DEFAULT_RECORD_ID else resolved_id

    payload = legacy_ai_text_get(project, canonical_type, locale, legacy_record_id)

    if not isinstance(payload, dict) or not payload:
        return None

    # 语言元数据缺失 / 无法识别 -> ``und``。
    # 不把"英文居多"启发式当作迁移事实，也绝不归属到当前界面语言。
    resolved_locale = normalize_content_locale(locale)

    if resolved_locale is None:
        resolved_locale = UNDETERMINED_LOCALE

    return make_localized_record(
        canonical_type,
        resolved_id,
        resolved_locale,
        payload,
        source=source,
        generated_at=generated_at,
        compute_hash=False,
    )


def legacy_content_types(project: Any) -> List[str]:
    """
    旧 archive 中出现过的 content_type（只读，供审计使用）。

    无法映射到已知 content_type 的 section 名会被忽略。
    """
    if not isinstance(project, dict):
        return []

    archive = project.get(LEGACY_ARCHIVE_KEY)

    if not isinstance(archive, dict):
        return []

    found = set()

    for bucket in archive.values():
        if not isinstance(bucket, dict):
            continue

        for section in bucket:
            canonical = normalize_content_type(section)

            if canonical is not None:
                found.add(canonical)

    return sorted(found)


def legacy_content_locales(project: Any, content_type: Any) -> List[str]:
    """
    旧 archive 中该 content_type 出现过的 locale（已规范 / 去重 / 排序）。
    """
    canonical_type = normalize_content_type(content_type)

    if canonical_type is None or not isinstance(project, dict):
        return []

    archive = project.get(LEGACY_ARCHIVE_KEY)

    if not isinstance(archive, dict):
        return []

    names = _LEGACY_SECTION_ALIASES.get(canonical_type, (canonical_type,))

    found = set()

    for locale, bucket in archive.items():
        if not isinstance(locale, str) or not isinstance(bucket, dict):
            continue

        if not any(name in bucket for name in names):
            continue

        canonical_locale = normalize_content_locale(locale) or UNDETERMINED_LOCALE

        found.add(canonical_locale)

    return sorted(found)


# ============================================================
# 迁移辅助（仅测试 / 显式迁移工具使用，不自动调用）
# ============================================================


def localize_legacy_project(
    project: Any,
    content_type: Any = None,
    generated_at: Any = None,
) -> Dict[str, Any]:
    """
    返回一个**新的** project，把旧 archive 的内容物化成新协议的 record。

    这是显式迁移工具：本阶段**不会被任何运行时路径调用**，也不会自动写盘。
    没有可靠 hash 的旧内容会以 ``input_hash = None``（stale）落地。
    """
    if not isinstance(project, dict):
        return project

    target = _copy.deepcopy(project)

    archive = project.get(LEGACY_ARCHIVE_KEY)

    if not isinstance(archive, dict):
        return target

    types = (
        [normalize_content_type(content_type)]
        if content_type is not None
        else legacy_content_types(project)
    )

    for canonical_type in types:
        if canonical_type is None:
            continue

        for locale, bucket in archive.items():
            if not isinstance(locale, str) or not isinstance(bucket, dict):
                continue

            # 用**原始** locale key 读取（未注册写法会在转换时归为 und）。
            for record_id in _legacy_record_ids(canonical_type, bucket):
                record = legacy_ai_text_to_record(
                    project,
                    canonical_type,
                    locale,
                    record_id,
                    generated_at=generated_at,
                )

                if record is not None:
                    localized_content_put(target, record, copy_on_write=False)

    return target


def _legacy_record_ids(content_type: str, bucket: Dict[Any, Any]) -> List[Any]:
    """
    旧 archive 某个 locale 桶中该 content_type 的 record_id 列表。

    单例内容返回 ``[None]``（表示"整段 payload"）；多记录内容返回 payload 的
    字符串键。找不到任何内容返回 ``[]``。
    """
    names = _LEGACY_SECTION_ALIASES.get(content_type, (content_type,))

    for name in names:
        payload = bucket.get(name)

        if payload is None:
            continue

        if content_type in LEGACY_SINGLETON_CONTENT_TYPES:
            return [None]

        if isinstance(payload, dict):
            ids = [key for key in payload if isinstance(key, str)]

            if ids:
                return ids

        return []

    return []
