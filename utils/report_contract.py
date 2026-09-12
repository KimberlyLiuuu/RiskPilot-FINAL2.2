# -*- coding: utf-8 -*-
"""
RiskPilot 导出报告（Report）的语言与文本契约 —— 全球化阶段 4C-2（纯数据层）。

本模块只回答四类问题，别的一律不做：

1. 这个"报告语言"取值能不能用于导出报告？规范 BCP 47 code 是什么？
   （:func:`normalize_report_locale` / :func:`report_locale_ai_name`）
2. 报告受众 / 报告类型 的**稳定内部 ID** 是什么？
   （:func:`normalize_report_audience` / :func:`normalize_report_type`）
3. AI 返回的可翻译文本是否符合阶段 4C 的 payload 契约？
   （:func:`validate_report_text_payload`）
4. 一份报告在 ``localized_content`` 中的 record_id 是什么？
   （:func:`make_report_record_id`）

硬边界（本模块的"不做什么"）
----------------------------
* 不导入 Streamlit、不访问 ``session_state``、不依赖 ``app.py``；
* 不调用 AI、不生成 DOCX、不读写 ``projects.json``、不 ``persist`` / 不写库；
* 纯函数：**不修改任何入参**（校验通过时返回**副本**）；
* **不做语言检测**：这里只回答"字段与语言标识是否合法"，不判断"这段文字
  真的是目标语言吗" —— 那属于后续阶段（翻译/校验）的能力；
* **不跨语言猜测**：``it`` / ``ms`` / ``id`` 未进入 registry，就返回 invalid，
  绝不悄悄映射成 ``en`` 或别的语言。

两个必须区分的数据对象（阶段 4C-3 才会真正实现快照与 hash）
----------------------------------------------------------
A. **canonical report facts**（语言无关，风险数值的唯一来源）
   项目标识与元数据、canonical risk level / score、Current Risk 的
   components / weights、Baseline / Weather / Logs / Vision 的数值事实、
   audience ID、report type ID、report locale、template version。
   本模块只用 :data:`REPORT_CANONICAL_FACT_FIELDS` **声明**字段名，不生成快照。

B. **localized report text**（可翻译文本，本模块唯一校验的 payload）
   ``title`` / ``opening`` / ``summary`` / ``key_points`` / ``actions`` / ``closing``。
   **不得**包含任何风险数值或内部等级。

用户编辑后的整篇正文既不是 A 也不是 B：它属于单独的 editable/export 状态，
不得反向覆盖 canonical facts，也不得再被当作原始 AI payload 使用。

与 ``utils/localized_content`` 的关系
------------------------------------
* 本模块**只读**复用它的 ``CONTENT_TYPE_REPORT``、``NON_TRANSLATABLE_FIELDS``
  与 ``normalize_record_id``（record_id 字符集），不修改共享协议；
* ``CONTENT_TYPE_CONTRACTS["risk_report"]`` 目前声明的是
  ``title / summary / body``，与本模块的 6 字段目标结构**不同**；
  共享 schema 的最终调整延后到阶段 4C-3（见 ``docs/localized-content-schema.md``）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from utils import content_locale as _content_locale
from utils import locale_registry as _locale_registry
from utils import localized_content as _localized_content

# ============================================================
# 版本
# ============================================================

#: 结构化 report text payload 的版本（阶段 4C-3 起随契约一起写入 record）。
REPORT_SCHEMA_VERSION: int = 1

#: 报告模板版本（渲染格式 / 段落结构变化时递增；本阶段只定义，不接入缓存或 DOCX）。
REPORT_TEMPLATE_VERSION: int = 1


def report_contract_version() -> Dict[str, int]:
    """本契约的版本快照（供 4C-3 的 cache key 与 record 使用）。"""
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "template_version": REPORT_TEMPLATE_VERSION,
    }


# ============================================================
# 常量
# ============================================================

#: 报告在 localized_content 中的 content_type（复用阶段 3 的规范值）。
REPORT_CONTENT_TYPE: str = _localized_content.CONTENT_TYPE_REPORT

#: BCP 47 "und"：语言未确定。**invalid 的 report locale 一律返回它**。
UNDETERMINED_LOCALE: str = _content_locale.UNDETERMINED_LOCALE

#: 结构化结果里的 ``reason`` 取值（``valid=True`` 时为 ``None``）。
REASON_NOT_A_STRING = "not_a_string"
REASON_UNSUPPORTED_REPORT_LOCALE = "unsupported_report_locale"
REASON_MISSING_AI_LANGUAGE_NAME = "missing_ai_language_name"
REASON_UNSUPPORTED_REPORT_AUDIENCE = "unsupported_report_audience"
REASON_UNSUPPORTED_REPORT_TYPE = "unsupported_report_type"
REASON_INVALID_REPORT_PAYLOAD = "invalid_report_text_payload"
REASON_INVALID_RECORD_ID = "invalid_report_record_id"

#: 受众的稳定内部 ID（顺序即展示顺序；**不是**翻译后的文本）。
REPORT_AUDIENCES: Tuple[str, ...] = ("worker", "manager", "client")

#: 报告类型的稳定内部 ID。
REPORT_TYPES: Tuple[str, ...] = ("daily_risk_alert", "risk_report")

#: 可翻译文本 payload 的**严格**字段集合（多一个少一个都不行）。
REPORT_TEXT_FIELDS: Tuple[str, ...] = (
    "title",
    "opening",
    "summary",
    "key_points",
    "actions",
    "closing",
)

#: 其中必须是"字符串列表"的字段。
REPORT_TEXT_LIST_FIELDS: Tuple[str, ...] = ("key_points", "actions")

#: 规模上限（软上限，超出即 invalid；防止一次生成就写出超大文档）。
MAX_TITLE_LENGTH = 300
MAX_TEXT_LENGTH = 4000
MAX_LIST_ITEMS = 8
MIN_LIST_ITEMS = 1
MAX_LIST_ITEM_LENGTH = 1000

#: 绝不允许进入可翻译 payload 的字段名。
#: 基础集合来自阶段 3 的 :data:`_localized_content.NON_TRANSLATABLE_FIELDS`
#: （penalty / score / level / confidence / coordinates / bbox / severity /
#: likelihood / exposure / raw_risk / risk_level / risk_score / risk_count /
#: visual_risk_penalty …），这里再补上报告链路特有的风险数值字段。
FORBIDDEN_PAYLOAD_FIELDS = frozenset(_localized_content.NON_TRANSLATABLE_FIELDS) | frozenset({
    "components",
    "weights",
    "risk_components",
    "risk_weights",
    "baseline_component",
    "weather_signal",
    "log_signal",
})

#: canonical report facts 的字段名（阶段 4C-3 的快照才会填充）。
#: 本阶段只声明，用于守卫"数值事实不得进入可翻译 payload"。
REPORT_CANONICAL_FACT_FIELDS: Tuple[str, ...] = (
    "project_id",
    "project_metadata",
    "risk_level",
    "risk_score",
    "components",
    "weights",
    "baseline_facts",
    "weather_facts",
    "log_facts",
    "vision_facts",
    "audience",
    "report_type",
    "report_locale",
    "template_version",
)

#: record_id 的构造规则：``report::<audience>::<report_type>``。
#: 共享协议 ``localized_content._SAFE_RECORD_ID_RE`` 的字符集是
#: ``^[A-Za-z0-9_.:\-]+$``（含冒号），因此 ``::`` 合法且不会与
#: 内部 ID 中的 ``_`` 混淆。生成后仍必须通过 ``normalize_record_id`` 校验。
REPORT_RECORD_ID_PREFIX = "report"
REPORT_RECORD_ID_SEPARATOR = "::"

#: 旧"报告类型"显示值 -> 稳定内部 ID（key 已是 :func:`_display_key` 的规范形）。
_REPORT_TYPE_ALIASES: Dict[str, str] = {
    "today's risk alert": "daily_risk_alert",
    "risk report": "risk_report",
    # 内部 ID 本身也接受（幂等）。
    "daily_risk_alert": "daily_risk_alert",
    "risk_report": "risk_report",
}

#: 旧的撇号写法（AI/用户/旧数据可能给出任意一种）。
_APOSTROPHES: Tuple[str, ...] = ("\u2019", "\u2018", "\u02bc", "\u00b4", "`")


# ============================================================
# 通用小工具
# ============================================================


def _display_key(value: Any) -> Optional[str]:
    """
    把"显示值"归一化成查表 key：撇号统一、空白折叠、去首尾、``casefold``。

    非字符串返回 ``None``（调用方据此给出 invalid，而不是猜一个语言）。
    """
    if not isinstance(value, str):
        return None

    cleaned = value

    for apostrophe in _APOSTROPHES:
        cleaned = cleaned.replace(apostrophe, "'")

    cleaned = " ".join(cleaned.split()).casefold()

    return cleaned or None


def _value_kind(value: Any) -> str:
    """给非法值一个可读的类型标签（错误信息里用，便于定位）。"""
    if isinstance(value, str):
        return "string"
    if isinstance(value, float):
        if value != value:
            return "nan"
        if value in (float("inf"), float("-inf")):
            return "infinity"
        return "number"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "number"
    if isinstance(value, (bytes, bytearray)):
        return "bytes"
    if isinstance(value, (set, frozenset)):
        return "set"
    if isinstance(value, tuple):
        return "tuple"
    if isinstance(value, dict):
        return "dict"
    if isinstance(value, list):
        return "list"
    if value is None:
        return "null"
    return "object"


def _registry_entry(locale: Any):
    """已注册 locale 的 registry 条目，否则 ``None``。"""
    try:
        return _locale_registry.get_locale(locale)
    except Exception:
        return None


# ============================================================
# 1) report locale
# ============================================================


def supported_report_locales() -> Tuple[str, ...]:
    """
    允许作为报告语言的全部 locale（registry 顺序）。

    规则：**已注册**且具有**明确** ``ai_language_name``。

    报告语言**允许** ``planned`` locale（不要求已开放为 UI 语言）：
    ``ui_enabled=False`` 不等于"报告语言不可用"。当前为
    ``en, zh-CN, es, pt-BR, fr, sw, de, ar, hi, ja, ko``。
    """
    locales: List[str] = []

    for entry in _locale_registry.load_locale_registry():
        if not entry.ai_language_name.strip():
            continue
        locales.append(entry.code)

    return tuple(locales)


def _resolve_report_locale(value: Any) -> Optional[str]:
    """
    把取值解析成**已注册**的规范 code；无法解析返回 ``None``。

    接受两类输入，顺序固定、都不做"猜测"：
    1. BCP 47 code / 别名（``"sw"``、``"pt-BR"``、``"zh_CN"`` …）——
       由 :func:`locale_registry.canonicalize_locale` 判定；
    2. 旧报告语言**显示值**（``"中文"``、``"Kiswahili"``、``"简体中文"`` …）——
       由 :func:`content_locale.report_language_to_locale` 判定。
    """
    canonical = _locale_registry.canonicalize_locale(value)

    if canonical is not None:
        return canonical

    if not isinstance(value, str):
        return None

    mapped = _content_locale.report_language_to_locale(value)

    if not mapped or mapped == UNDETERMINED_LOCALE:
        return None

    # 显示值可能映射到一个 registry 未注册的 code（如 ``it`` / ``ms`` / ``id``），
    # 此时必须 invalid：绝不改写成别的语言。
    return _locale_registry.canonicalize_locale(mapped)


def normalize_report_locale(value: Any) -> Dict[str, Any]:
    """
    规范化报告语言取值。

    返回::

        {"valid": True,  "locale": "sw",  "reason": None}
        {"valid": False, "locale": "und", "reason": "unsupported_report_locale"}

    非法输入（含 ``None`` / 空值 / 非字符串 / ``"Local Language"`` /
    未注册的 ``it`` ``ms`` ``id`` ``pt-PT`` ``zh-TW``）一律
    ``valid=False`` + ``locale="und"``，**永不抛异常、永不猜测**。

    本函数不读也不写任何 session 状态（模块内无状态）。
    """
    if not isinstance(value, str) or not value.strip():
        return {
            "valid": False,
            "locale": UNDETERMINED_LOCALE,
            "reason": REASON_NOT_A_STRING,
        }

    canonical = _resolve_report_locale(value)

    if canonical is None:
        return {
            "valid": False,
            "locale": UNDETERMINED_LOCALE,
            "reason": REASON_UNSUPPORTED_REPORT_LOCALE,
        }

    entry = _registry_entry(canonical)

    if entry is None or not entry.ai_language_name.strip():
        return {
            "valid": False,
            "locale": UNDETERMINED_LOCALE,
            "reason": REASON_MISSING_AI_LANGUAGE_NAME,
        }

    return {"valid": True, "locale": canonical, "reason": None}


def report_locale_ai_name(locale: Any) -> Optional[str]:
    """
    放进 AI prompt 的语言名；**只对有效报告语言**返回，否则 ``None``。

    注意不要用 ``locale_registry.ai_language_name()`：它会对未注册 code
    回退成默认语言的 AI 名（``English``），那会把"不可用"伪装成"可用"。
    这里走 :func:`normalize_report_locale` 的校验路径。
    """
    normalized = normalize_report_locale(locale)

    if not normalized["valid"]:
        return None

    entry = _registry_entry(normalized["locale"])

    if entry is None:
        return None

    name = entry.ai_language_name.strip()

    return name or None


# ============================================================
# 2) report audience / report type
# ============================================================


def normalize_report_audience(value: Any) -> Dict[str, Any]:
    """
    规范化报告受众，返回**稳定内部 ID**（绝不返回翻译后的显示文本）。

    合法值：``worker`` / ``manager`` / ``client``（大小写与首尾空白容错，
    因此旧显示值 ``Worker`` / ``Manager`` / ``Client`` 仍然可读）。

    返回::

        {"valid": True,  "audience": "manager", "reason": None}
        {"valid": False, "audience": None,      "reason": "unsupported_report_audience"}
    """
    key = _display_key(value)

    if key is None or key not in REPORT_AUDIENCES:
        return {
            "valid": False,
            "audience": None,
            "reason": REASON_UNSUPPORTED_REPORT_AUDIENCE,
        }

    return {"valid": True, "audience": key, "reason": None}


def normalize_report_type(value: Any) -> Dict[str, Any]:
    """
    规范化报告类型，返回**稳定内部 ID**。

    旧显示值兼容：``Today's Risk Alert`` -> ``daily_risk_alert``、
    ``Risk Report`` -> ``risk_report``（大小写、空白、撇号写法容错）。

    返回::

        {"valid": True,  "report_type": "risk_report", "reason": None}
        {"valid": False, "report_type": None,          "reason": "unsupported_report_type"}
    """
    key = _display_key(value)

    if key is None:
        return {
            "valid": False,
            "report_type": None,
            "reason": REASON_UNSUPPORTED_REPORT_TYPE,
        }

    canonical = _REPORT_TYPE_ALIASES.get(key)

    if canonical is None:
        return {
            "valid": False,
            "report_type": None,
            "reason": REASON_UNSUPPORTED_REPORT_TYPE,
        }

    return {"valid": True, "report_type": canonical, "reason": None}


# ============================================================
# 3) 可翻译文本 payload
# ============================================================


def _invalid_payload(errors: List[str]) -> Dict[str, Any]:
    """结构化失败结果：**绝不**同时返回半成品 payload。"""
    return {
        "valid": False,
        "payload": None,
        "errors": errors,
        "reason": REASON_INVALID_REPORT_PAYLOAD,
    }


def _check_text_field(field: str, value: Any) -> List[str]:
    """单个文本字段的类型 / 空值 / 长度校验。"""
    if not isinstance(value, str):
        return [f"not_a_string:{field}:{_value_kind(value)}"]

    text = value.strip()

    if not text:
        return [f"empty_text:{field}"]

    limit = MAX_TITLE_LENGTH if field == "title" else MAX_TEXT_LENGTH

    if len(text) > limit:
        return [f"too_long:{field}"]

    return []


def _check_list_field(field: str, value: Any) -> List[str]:
    """字符串列表字段的类型 / 规模 / 元素校验（只检查前 MAX_LIST_ITEMS 项）。"""
    if not isinstance(value, list):
        return [f"not_a_list:{field}:{_value_kind(value)}"]

    errors: List[str] = []

    if not (MIN_LIST_ITEMS <= len(value) <= MAX_LIST_ITEMS):
        errors.append(f"list_size:{field}")

    for index, item in enumerate(value[:MAX_LIST_ITEMS]):
        if not isinstance(item, str):
            errors.append(
                f"not_a_string_item:{field}[{index}]:{_value_kind(item)}"
            )
        elif not item.strip():
            errors.append(f"empty_item:{field}[{index}]")
        elif len(item.strip()) > MAX_LIST_ITEM_LENGTH:
            errors.append(f"too_long_item:{field}[{index}]")

    return errors


def validate_report_text_payload(payload: Any) -> Dict[str, Any]:
    """
    校验 AI 可翻译文本 payload。**严格** key 集合：

    ``title / opening / summary / key_points / actions / closing``

    返回::

        {"valid": True,  "payload": {...标准化副本...}, "errors": [], "reason": None}
        {"valid": False, "payload": None, "errors": [...], "reason": "invalid_report_text_payload"}

    规则：
    * 顶层必须是 ``dict``；
    * key 集合严格（缺字段、额外字段、禁止字段一律 invalid）；
    * 所有字符串去首尾空白后不得为空；
    * ``key_points`` / ``actions`` 必须是**字符串列表**（1–8 项、每项 ≤ 1000 字符）；
    * ``title`` ≤ 300 字符，``opening`` / ``summary`` / ``closing`` ≤ 4000 字符；
    * 只有 ``str`` 被接受 —— 因此 ``bytes`` / ``set`` / ``tuple`` / 自定义对象 /
      ``NaN`` / ``Infinity`` 等非 JSON 值**结构上**不可能通过；
    * **不修改入参**：通过时返回的是去掉首尾空白的**副本**；
    * 本阶段**不**检查"文本是否真的是目标语言"（后续限制）。
    """
    if not isinstance(payload, dict):
        return _invalid_payload([f"payload_not_a_dict:{_value_kind(payload)}"])

    errors: List[str] = []

    # --- key 集合 ---
    for key in sorted(payload, key=lambda item: str(item)):
        if not isinstance(key, str):
            errors.append(f"invalid_key_type:{_value_kind(key)}")
            continue

        normalized_key = key.strip()

        if normalized_key.lower() in FORBIDDEN_PAYLOAD_FIELDS:
            errors.append(f"forbidden_field:{normalized_key}")
        elif normalized_key not in REPORT_TEXT_FIELDS:
            errors.append(f"unsupported_field:{normalized_key}")

    for field in REPORT_TEXT_FIELDS:
        if field not in payload:
            errors.append(f"missing_field:{field}")

    # --- 字段内容 ---
    for field in REPORT_TEXT_FIELDS:
        if field not in payload:
            continue

        if field in REPORT_TEXT_LIST_FIELDS:
            errors.extend(_check_list_field(field, payload[field]))
        else:
            errors.extend(_check_text_field(field, payload[field]))

    if errors:
        return _invalid_payload(errors)

    return {
        "valid": True,
        "payload": {
            "title": payload["title"].strip(),
            "opening": payload["opening"].strip(),
            "summary": payload["summary"].strip(),
            "key_points": [item.strip() for item in payload["key_points"]],
            "actions": [item.strip() for item in payload["actions"]],
            "closing": payload["closing"].strip(),
        },
        "errors": [],
        "reason": None,
    }


# ============================================================
# 4) record_id
# ============================================================


def is_safe_report_record_id(value: Any) -> bool:
    """
    ``value`` 能否作为 ``localized_content`` 的 record_id。

    直接委托阶段 3 的 :func:`localized_content.normalize_record_id`
    （字符集 ``^[A-Za-z0-9_.:\\-]+$``、拒绝路径穿越与控制字符），
    因此本契约与共享协议**永远**是同一把尺子。
    """
    if not isinstance(value, str) or not value:
        return False

    try:
        normalized = _localized_content.normalize_record_id(value)
    except Exception:
        return False

    return normalized == value


def make_report_record_id(audience: Any, report_type: Any) -> Dict[str, Any]:
    """
    报告在 ``localized_content`` 中的 record_id。

    规则：``report::<audience>::<report_type>``，例如
    ``report::manager::risk_report``。

    * **locale 不进入 id**：``localized_content`` 已按 locale 分桶；
    * **project id 不进入 id**：记录本来就位于具体 project 之下；
    * 分隔符 ``::`` 落在共享 record_id 字符集 ``[A-Za-z0-9_.:\-]`` 内
      （``utils/localized_content.py`` 的 ``_SAFE_RECORD_ID_RE``），
      生成后还要再过一遍 :func:`is_safe_report_record_id`。

    返回::

        {"valid": True,  "record_id": "report::manager::risk_report", "reason": None}
        {"valid": False, "record_id": None, "reason": "unsupported_report_audience"}
    """
    normalized_audience = normalize_report_audience(audience)

    if not normalized_audience["valid"]:
        return {
            "valid": False,
            "record_id": None,
            "reason": REASON_UNSUPPORTED_REPORT_AUDIENCE,
        }

    normalized_type = normalize_report_type(report_type)

    if not normalized_type["valid"]:
        return {
            "valid": False,
            "record_id": None,
            "reason": REASON_UNSUPPORTED_REPORT_TYPE,
        }

    record_id = REPORT_RECORD_ID_SEPARATOR.join(
        (
            REPORT_RECORD_ID_PREFIX,
            normalized_audience["audience"],
            normalized_type["report_type"],
        )
    )

    if not is_safe_report_record_id(record_id):
        return {
            "valid": False,
            "record_id": None,
            "reason": REASON_INVALID_RECORD_ID,
        }

    return {"valid": True, "record_id": record_id, "reason": None}
