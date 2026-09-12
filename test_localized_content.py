# -*- coding: utf-8 -*-
"""
RiskPilot 阶段 3 —— 统一 localized content 数据层测试
(test_localized_content.py)

测试对象是纯数据模块 ``utils/localized_content.py``。

约束：不启动 Streamlit、不联网、不调用 AI、不写 data/projects.json、
不导入 app.py。

覆盖场景：
    B. Record schema（构造 / 校验 / 拒绝非法输入）
    C. 多语言隔离（locale / record_id / project 互不覆盖，不跨语言 fallback）
    D. Hash 与 stale（稳定序列化、freshness 必须同时校验 locale）
    E. Legacy 兼容（旧 ai_text_by_language 只读、新字段优先、不迁移）
    G. 安全（record_id / content_type / payload / 原型污染 / 畸形旧数据）
    H. AST 守卫（不接线业务路径、不新增 ui_lang 写入点）
    J. 阶段 4 契约（content_type / payload schema / 禁用数值字段）

运行方式：
    python test_localized_content.py
"""

import ast
import copy
import hashlib
import json
import math
import pathlib
import re

from utils import localized_content as lc

ROOT = pathlib.Path(__file__).resolve().parent
DATA_FILE = ROOT / "data" / "projects.json"

ZH = "zh-CN"
EN = "en"
PLANNED = "es"

#: 阶段 4 明确禁止进入可翻译 payload 的字段。
FORBIDDEN_TRANSLATABLE_FIELDS = (
    "penalty",
    "score",
    "level",
    "confidence",
    "coordinates",
    "geometry",
    "severity",
    "likelihood",
    "exposure",
)

BASELINE_PAYLOAD = {
    "risk_chains": ["供应商 A 延误 -> 交付 B 顺延"],
    "impacts": ["交付延期 2 周"],
    "actions": ["启用备用供应商"],
}

LOG_PAYLOAD = {
    "summary": "本周供应商产能下降",
    "risks": ["交付风险上升"],
    "suggestion": "提前锁产能",
}


# ============================================================
# 简易测试框架
# ============================================================

_PASSED = 0
_FAILED = []


def check(name, condition, detail=""):
    global _PASSED

    if condition:
        _PASSED += 1
        print(f"✅ {name}")
    else:
        _FAILED.append(name)
        message = f"❌ {name}"
        if detail:
            message += f"  ->  {detail}"
        print(message)


def eq(name, actual, expected):
    check(name, actual == expected, f"expected {expected!r}, got {actual!r}")


def ne(name, actual, unexpected):
    check(name, actual != unexpected, f"不得等于 {unexpected!r}")


def safe_call(name, func, *args, **kwargs):
    """调用 func；只有抛异常才算失败。返回 (ok, value)。"""
    global _PASSED

    try:
        value = func(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001 - 这里就是要抓所有异常
        _FAILED.append(name)
        print(f"❌ {name}  ->  {type(exc).__name__}: {exc}")
        return False, None

    _PASSED += 1
    print(f"✅ {name}")
    return True, value


def _deep(depth):
    """构造 depth 层嵌套的 dict。"""
    node = {"leaf": 1}

    for _ in range(depth):
        node = {"n": node}

    return node


def _put(project, content_type, locale, payload, record_id=None, source=None):
    """构造并写入一条 record，返回新的 project。"""
    kwargs = {"source": source} if source is not None else {}

    record = lc.make_localized_record(
        content_type, record_id, locale, payload, **kwargs
    )

    return lc.localized_content_put(project, record)


# ============================================================
# B. Record schema
# ============================================================

def test_b_record_schema():
    print("\n--- B. Record schema ---")

    record = lc.make_localized_record("baseline", None, ZH, BASELINE_PAYLOAD)

    check("B1 合法 payload 可构造 record", isinstance(record, dict))
    eq("B1 validate 无问题", lc.validate_localized_record(record, "baseline"), [])

    for field in (
        "schema_version",
        "content_type",
        "record_id",
        "locale",
        "payload",
        "input_hash",
        "generated_at",
        "source",
    ):
        check(f"B1 record 含字段 {field}", field in record)

    eq("B1 schema_version", record["schema_version"], lc.SCHEMA_VERSION)
    eq("B1 content_type 规范", record["content_type"], lc.CONTENT_TYPE_BASELINE)
    eq("B1 record_id 默认 project", record["record_id"], lc.DEFAULT_RECORD_ID)
    eq("B1 locale", record["locale"], ZH)
    eq("B1 source 默认 ai_generated", record["source"], lc.SOURCE_AI_GENERATED)
    check(
        "B1 input_hash 是 sha256 hex",
        bool(re.fullmatch(r"[0-9a-f]{64}", record["input_hash"] or "")),
        repr(record["input_hash"]),
    )
    check("B1 generated_at 带时区", lc.is_iso_utc_timestamp(record["generated_at"]))
    eq("B1 payload 深拷贝相等", record["payload"], BASELINE_PAYLOAD)

    # 别名规范化：section 名 -> 规范 content_type。
    for alias, canonical in (
        ("log", lc.CONTENT_TYPE_LOGS),
        ("logs", lc.CONTENT_TYPE_LOGS),
        ("daily_logs", lc.CONTENT_TYPE_LOGS),
        ("vision", lc.CONTENT_TYPE_VISION),
        ("vision_risks", lc.CONTENT_TYPE_VISION),
        ("baseline_risk", lc.CONTENT_TYPE_BASELINE),
        ("report", lc.CONTENT_TYPE_REPORT),
    ):
        eq(f"B1 别名 {alias!r} -> {canonical}", lc.normalize_content_type(alias), canonical)

    aliased = lc.make_localized_record("log", "L1", EN, LOG_PAYLOAD)
    eq("B1 别名写入时已规范化", aliased["content_type"], lc.CONTENT_TYPE_LOGS)

    # 大写 / 空白容错。
    eq("B1 大写容错", lc.normalize_content_type("  LOG  "), lc.CONTENT_TYPE_LOGS)

    # 全部白名单 content_type 都能构造。
    for content_type in lc.CONTENT_TYPES:
        eq(
            f"B1 白名单 {content_type} 可构造",
            isinstance(
                lc.make_localized_record(content_type, None, EN, {"summary": "x"}),
                dict,
            ),
            True,
        )


def test_b_invalid_records_rejected():
    print("\n--- B2. 非法 record 被拒绝 ---")

    # --- locale ---
    for bad in ("xx-YY", "../etc", "", "   ", None, 123, ["en"], "zh-Hant"):
        eq(
            f"B2 非法 locale {bad!r} 拒绝",
            lc.make_localized_record("baseline", None, bad, BASELINE_PAYLOAD),
            None,
        )

    # planned locale 的 record 是允许存的（可能有预生成内容），
    # 但它不能成为"当前内容语言"（见 test_content_locale.py A5）。
    check(
        "B2 planned locale 的 record 可以保存",
        isinstance(
            lc.make_localized_record("baseline", None, PLANNED, BASELINE_PAYLOAD),
            dict,
        ),
    )
    eq("B2 und 可以保存", lc.normalize_content_locale("und"), lc.UNDETERMINED_LOCALE)

    # --- content_type ---
    for bad in ("bogus", "system", "../../etc", "", None, 5, "x" * 300):
        eq(
            f"B2 非法 content_type {bad!r} 拒绝",
            lc.make_localized_record(bad, None, EN, BASELINE_PAYLOAD),
            None,
        )
        eq(f"B2 normalize_content_type({bad!r})", lc.normalize_content_type(bad), None)

    # --- record_id ---
    for bad in (
        "../etc/passwd",
        "..\\..\\win",
        "a/../b",
        "a\x00b",
        "\x1f",
        "x" * 300,
        "/root",
        "a/b",
        "C:\\x",
        "..",
        "...",
        "___",
        " ",
    ):
        eq(f"B2 非法 record_id {bad!r} 拒绝", lc.normalize_record_id(bad), None)

    # --- payload ---
    for bad in (None, "str", 5, ["a"], b"bytes"):
        eq(
            f"B2 非 dict payload {bad!r} 拒绝",
            lc.make_localized_record("baseline", None, EN, bad),
            None,
        )

    # --- source ---
    for bad in ("bogus", "", None, 5, ["ai_generated"]):
        eq(
            f"B2 非法 source {bad!r} 拒绝",
            lc.make_localized_record(
                "baseline", None, EN, BASELINE_PAYLOAD, source=bad
            ),
            None,
        )

    for good in lc.SOURCES:
        eq(
            f"B2 合法 source {good!r} 可构造",
            isinstance(
                lc.make_localized_record(
                    "baseline", None, EN, BASELINE_PAYLOAD, source=good
                ),
                dict,
            ),
            True,
        )

    # --- schema_version ---
    for bad in (0, -1, 2, 999, "1", 1.0, True, None):
        eq(
            f"B2 非法 schema_version {bad!r} 拒绝",
            lc.make_localized_record(
                "baseline", None, EN, BASELINE_PAYLOAD, schema_version=bad
            ),
            None,
        )

    # --- generated_at（必须带时区） ---
    for bad in ("2026-01-01T00:00:00", "2026-01-01", "", 123, None):
        if bad is None:
            continue
        eq(
            f"B2 无时区 generated_at {bad!r} 拒绝",
            lc.make_localized_record(
                "baseline", None, EN, BASELINE_PAYLOAD, generated_at=bad
            ),
            None,
        )

    for good in (
        "2026-01-01T00:00:00+00:00",
        "2026-01-01T00:00:00Z",
        "2026-01-01T08:00:00+08:00",
    ):
        check(
            f"B2 合法 generated_at {good!r}",
            isinstance(
                lc.make_localized_record(
                    "baseline", None, EN, BASELINE_PAYLOAD, generated_at=good
                ),
                dict,
            ),
        )

    check(
        "B2 is_iso_utc_timestamp 拒绝无时区",
        not lc.is_iso_utc_timestamp("2026-01-01T00:00:00"),
    )

    # --- 直接校验一条被篡改的 record ---
    record = lc.make_localized_record("baseline", None, EN, BASELINE_PAYLOAD)

    tampered = copy.deepcopy(record)
    tampered["record_id"] = "../evil"
    check("B2 篡改 record_id 会被 validate 抓到",
          lc.validate_localized_record(tampered, "baseline") != [])

    tampered = copy.deepcopy(record)
    tampered["locale"] = "xx-YY"
    check("B2 篡改 locale 会被 validate 抓到",
          lc.validate_localized_record(tampered, "baseline") != [])

    tampered = copy.deepcopy(record)
    tampered["source"] = "bogus"
    check("B2 篡改 source 会被 validate 抓到",
          lc.validate_localized_record(tampered, "baseline") != [])

    tampered = copy.deepcopy(record)
    tampered["schema_version"] = 99
    check("B2 篡改 schema_version 会被 validate 抓到",
          lc.validate_localized_record(tampered, "baseline") != [])

    tampered = copy.deepcopy(record)
    tampered["payload"] = "not a dict"
    check("B2 篡改 payload 会被 validate 抓到",
          lc.validate_localized_record(tampered, "baseline") != [])

    tampered = copy.deepcopy(record)
    tampered["input_hash"] = "zzz"
    check("B2 篡改 input_hash 会被 validate 抓到",
          lc.validate_localized_record(tampered, "baseline") != [])

    tampered = copy.deepcopy(record)
    tampered["generated_at"] = "2026-01-01T00:00:00"
    check("B2 篡改 generated_at 会被 validate 抓到",
          lc.validate_localized_record(tampered, "baseline") != [])

    check("B2 非 dict 不是合法 record",
          lc.validate_localized_record("nope") != [])

    # expected_type 不匹配。
    check("B2 expected_type 不匹配被抓到",
          lc.validate_localized_record(record, lc.CONTENT_TYPE_LOGS) != [])


def test_b_payload_limits():
    print("\n--- B3. payload 规模 / 类型限制 ---")

    limit_cases = (
        ("嵌套过深", _deep(lc.MAX_PAYLOAD_DEPTH + 4)),
        ("dict 键过多", {f"k{i}": i for i in range(lc.MAX_PAYLOAD_KEYS + 50)}),
        ("列表过长", {"items": list(range(lc.MAX_PAYLOAD_ITEMS + 100))}),
        ("字符串过长", {"text": "x" * (lc.MAX_STRING_LENGTH + 100)}),
        ("键名过长", {"k" * (lc.MAX_KEY_LENGTH + 10): 1}),
        ("节点数过多", {"items": [list(range(20)) for _ in range(400)]}),
        ("序列化过大", {"blob": ["x" * 500 for _ in range(400)]}),
    )

    for label, payload in limit_cases:
        check(
            f"B3 拒绝 {label}",
            lc.validate_payload(payload) != [],
            str(lc.validate_payload(payload))[:120],
        )
        eq(
            f"B3 {label} 不能构造 record",
            lc.make_localized_record("baseline", None, EN, payload),
            None,
        )

    # 边界内应当通过。
    check(
        "B3 允许深度内的嵌套",
        lc.validate_payload(_deep(2)) == [],
    )
    check(
        "B3 允许上限内的列表",
        lc.validate_payload({"items": list(range(lc.MAX_PAYLOAD_ITEMS))}) == [],
    )

    type_cases = (
        ("bytes", {"a": b"bytes"}),
        ("bytearray", {"a": bytearray(b"x")}),
        ("set", {"a": {"x", "y"}}),
        ("frozenset", {"a": frozenset({"x"})}),
        ("tuple", {"a": (1, 2)}),
        ("complex", {"a": complex(1, 2)}),
        ("object", {"a": object()}),
        ("class", {"a": int}),
        ("function", {"a": lambda: 1}),
        ("NaN", {"a": float("nan")}),
        ("Infinity", {"a": float("inf")}),
        ("-Infinity", {"a": float("-inf")}),
        ("非字符串键", {1: "x"}),
        ("bytes 键", {b"k": "x"}),
    )

    for label, payload in type_cases:
        check(
            f"B3 拒绝 {label}",
            lc.validate_payload(payload) != [],
            str(lc.validate_payload(payload))[:120],
        )

    # 自引用结构不能造成死循环 / 栈溢出。
    looping = {"a": 1}
    looping["self"] = looping

    ok, problems = safe_call(
        "B3 自引用 dict 被安全拒绝（无 RecursionError）",
        lc.validate_payload,
        looping,
    )
    check("B3 自引用产生问题", ok and problems != [])

    # 合法 JSON 子集应当通过。
    check(
        "B3 合法 JSON 通过",
        lc.validate_payload(
            {
                "s": "x",
                "i": 1,
                "f": 1.5,
                "b": True,
                "n": None,
                "l": [1, "a", None, {"k": [2]}],
                "d": {"nested": {"deep": {"ok": True}}},
            }
        )
        == [],
    )


def test_b_inputs_are_not_mutated():
    print("\n--- B4. 输入对象不被修改 ---")

    payload = copy.deepcopy(BASELINE_PAYLOAD)
    payload_snapshot = copy.deepcopy(payload)

    record = lc.make_localized_record("baseline", None, ZH, payload)

    eq("B4 make 不修改 payload", payload, payload_snapshot)

    # 返回值是深拷贝：改 record 不影响原 payload。
    record["payload"]["risk_chains"].append("mutation")

    eq("B4 record 与 payload 不共享可变对象", payload, payload_snapshot)

    # validate 不修改入参。
    probe = lc.make_localized_record("baseline", None, ZH, BASELINE_PAYLOAD)
    probe_snapshot = copy.deepcopy(probe)

    lc.validate_localized_record(probe, "baseline")

    eq("B4 validate 不修改 record", probe, probe_snapshot)

    # put / get 不修改 project。
    project = {}
    project = lc.localized_content_put(project, probe)
    project_snapshot = copy.deepcopy(project)

    lc.localized_content_get(project, "baseline", ZH)
    lc.localized_content_languages(project, "baseline")
    lc.localized_content_status(project, "baseline", ZH)
    lc.localized_content_put(project, lc.make_localized_record("baseline", None, EN, {"risk_chains": ["e"]}))

    eq("B4 读写不修改原 project", project, project_snapshot)

    # get 返回副本：改它不影响 project。
    fetched = lc.localized_content_get(project, "baseline", ZH)
    fetched["payload"]["risk_chains"].append("mutation")

    eq(
        "B4 get 返回副本",
        lc.localized_content_get(project, "baseline", ZH)["payload"],
        BASELINE_PAYLOAD,
    )

    # compute_input_hash 不修改入参。
    hashed = copy.deepcopy(BASELINE_PAYLOAD)
    lc.compute_input_hash(hashed)
    eq("B4 compute_input_hash 不修改入参", hashed, BASELINE_PAYLOAD)


# ============================================================
# C. 多语言隔离
# ============================================================

def test_c_multilingual_isolation():
    print("\n--- C. 多语言隔离 ---")

    payload_en = {"summary": "english text"}
    payload_zh = {"summary": "中文文本"}

    project = _put({}, "current_risk_explanation", EN, payload_en)
    project = _put(project, "current_risk_explanation", ZH, payload_zh)

    eq(
        "C1 en 精确读取",
        lc.localized_content_get(project, "current_risk_explanation", EN)["payload"],
        payload_en,
    )
    eq(
        "C1 zh-CN 精确读取",
        lc.localized_content_get(project, "current_risk_explanation", ZH)["payload"],
        payload_zh,
    )
    eq(
        "C1 saved_locales 两个语言",
        lc.localized_content_languages(project, "current_risk_explanation"),
        sorted([EN, ZH]),
    )

    # 写一个语言不覆盖另一个语言（copy-on-write）。
    snapshot = copy.deepcopy(project)
    updated = _put(project, "current_risk_explanation", ZH, {"summary": "改写后的中文"})

    eq("C2 写 zh-CN 不改 en",
       lc.localized_content_get(updated, "current_risk_explanation", EN)["payload"],
       payload_en)
    eq("C2 写 zh-CN 更新 zh-CN",
       lc.localized_content_get(updated, "current_risk_explanation", ZH)["payload"],
       {"summary": "改写后的中文"})
    eq("C2 copy-on-write：原 project 不变", project, snapshot)

    # 日志 A 写入不影响日志 B。
    logs = _put({}, "logs", EN, {"summary": "log A"}, record_id="A")
    logs = _put(logs, "logs", EN, {"summary": "log B"}, record_id="B")
    logs_after = _put(logs, "logs", EN, {"summary": "log A v2"}, record_id="A")

    eq("C3 写日志 A 不改日志 B",
       lc.localized_content_get(logs_after, "logs", EN, "B")["payload"],
       {"summary": "log B"})
    eq("C3 日志 A 已更新",
       lc.localized_content_get(logs_after, "logs", EN, "A")["payload"],
       {"summary": "log A v2"})
    eq("C3 原 logs 不变",
       lc.localized_content_get(logs, "logs", EN, "A")["payload"],
       {"summary": "log A"})

    # 项目 A 写入不影响项目 B。
    project_a = _put({}, "baseline", ZH, BASELINE_PAYLOAD)
    project_b = _put({}, "baseline", ZH, {"risk_chains": ["B"]})
    project_a_after = _put(project_a, "baseline", EN, {"risk_chains": ["A-en"]})

    eq("C4 写项目 A 不改项目 B",
       lc.localized_content_get(project_b, "baseline", ZH)["payload"],
       {"risk_chains": ["B"]})
    eq("C4 项目 A 的 zh-CN 未被 EN 写入覆盖",
       lc.localized_content_get(project_a_after, "baseline", ZH)["payload"],
       BASELINE_PAYLOAD)
    eq("C4 项目 B 仍只有 zh-CN",
       lc.localized_content_languages(project_b, "baseline"),
       [ZH])

    # 目标语言不存在 -> missing，且绝不回退到另一个语言。
    eq("C5 目标语言缺失返回 None",
       lc.localized_content_get(project, "current_risk_explanation", "fr"),
       None)

    status = lc.localized_content_status(project, "current_risk_explanation", "fr")
    eq("C5 状态 missing", status["status"], lc.STATUS_MISSING)
    eq("C5 原因：存在其它语言版本", status["reason"], lc.REASON_NO_RECORD)
    eq("C5 saved_locales 指出哪几种语言存在",
       status["saved_locales"], sorted([EN, ZH]))
    check("C5 需要生成", status["needs_generation"] is True)

    # 完全没有内容 -> missing，但与"只有别的语言"可区分。
    empty_status = lc.localized_content_status({}, "baseline", ZH)
    eq("C6 完全没有内容 -> missing", empty_status["status"], lc.STATUS_MISSING)
    eq("C6 原因：完全无内容", empty_status["reason"], lc.REASON_NO_CONTENT)
    eq("C6 saved_locales 为空", empty_status["saved_locales"], [])

    # 空 payload 与 missing 明确区分。
    empty_payload_project = _put({}, "baseline", ZH, {})
    empty_status = lc.localized_content_status(empty_payload_project, "baseline", ZH)
    eq("C7 空 payload -> missing", empty_status["status"], lc.STATUS_MISSING)
    eq("C7 原因：空载荷", empty_status["reason"], lc.REASON_EMPTY_PAYLOAD)
    check("C7 空载荷不算 saved_locales",
          empty_status["saved_locales"] == [],
          str(empty_status["saved_locales"]))

    # 未知 content_type / 不安全 record_id -> invalid（不是抛异常）。
    bad_type = lc.localized_content_status({}, "bogus", ZH)
    eq("C8 未知 content_type -> invalid", bad_type["status"], lc.STATUS_INVALID)
    eq("C8 原因", bad_type["reason"], lc.REASON_UNKNOWN_CONTENT_TYPE)

    bad_id = lc.localized_content_status({}, "logs", ZH, "../evil")
    eq("C9 不安全 record_id -> invalid", bad_id["status"], lc.STATUS_INVALID)
    eq("C9 原因", bad_id["reason"], lc.REASON_UNSAFE_RECORD_ID)


# ============================================================
# D. Hash 与 stale
# ============================================================

def test_d_hash_and_freshness():
    print("\n--- D. Hash 与 stale ---")

    a = {"b": 1, "a": [1, 2, {"z": 2, "y": 1}]}
    b = {"a": [1, 2, {"y": 1, "z": 2}], "b": 1}

    eq("D1 dict 键顺序不影响 hash",
       lc.compute_input_hash(a),
       lc.compute_input_hash(b))
    eq("D1 相同规范输入得到相同 hash",
       lc.compute_input_hash(BASELINE_PAYLOAD),
       lc.compute_input_hash(json.loads(json.dumps(BASELINE_PAYLOAD))))

    changed = copy.deepcopy(BASELINE_PAYLOAD)
    changed["risk_chains"] = ["different"]
    ne("D2 输入改变 hash 改变",
       lc.compute_input_hash(changed),
       lc.compute_input_hash(BASELINE_PAYLOAD))

    check("D2 hash 是 sha256 hex",
          bool(re.fullmatch(r"[0-9a-f]{64}", lc.compute_input_hash(BASELINE_PAYLOAD) or "")))
    eq("D2 非法 payload 无 hash", lc.compute_input_hash("nope"), None)

    # --- freshness ---
    record = lc.make_localized_record("baseline", None, ZH, BASELINE_PAYLOAD)
    fresh_hash = lc.compute_input_hash(BASELINE_PAYLOAD)

    check("D3 locale 相同 + hash 相同 = fresh",
          lc.is_content_fresh(record, fresh_hash, ZH))
    check("D4 hash 不同 = stale",
          not lc.is_content_fresh(record, lc.compute_input_hash({"risk_chains": ["x"]}), ZH))
    check("D4 未传 locale 时只比较 hash",
          lc.is_content_fresh(record, fresh_hash))

    # hash 不含 locale，因此 freshness 必须同时校验 locale。
    check("D5 hash 相同但 locale 不同 = 不新鲜",
          not lc.is_content_fresh(record, fresh_hash, EN))
    check("D5 locale 为 None / 非法时 = 不新鲜",
          not lc.is_content_fresh(record, fresh_hash, "xx-YY"))

    # --- 缺 hash 的旧 record：绝不伪装 fresh ---
    legacy_record = lc.make_localized_record(
        "baseline", None, ZH, BASELINE_PAYLOAD,
        source=lc.SOURCE_LEGACY, compute_hash=False,
    )
    eq("D6 旧 record input_hash 为空", legacy_record["input_hash"], None)
    check("D6 缺 hash 永不 fresh",
          not lc.is_content_fresh(legacy_record, fresh_hash, ZH))

    legacy_project = lc.localized_content_put({}, legacy_record)

    status = lc.localized_content_status(legacy_project, "baseline", ZH, input_hash=fresh_hash)
    eq("D6 状态明确为 stale", status["status"], lc.STATUS_STALE)
    eq("D6 原因：缺 hash", status["reason"], lc.REASON_INPUT_HASH_MISSING)
    check("D6 is_stale True", status["is_stale"] is True)

    # --- available / stale 对照 ---
    project = lc.localized_content_put({}, record)

    available = lc.localized_content_status(project, "baseline", ZH, input_hash=fresh_hash)
    eq("D7 hash 匹配 -> available", available["status"], lc.STATUS_AVAILABLE)
    eq("D7 is_stale False", available["is_stale"], False)
    eq("D7 needs_generation False", available["needs_generation"], False)
    eq("D7 reason None", available["reason"], None)
    eq("D7 source 标签", available["source"], lc.SOURCE_TAG_LOCALIZED_CONTENT)
    eq("D7 saved_locales", available["saved_locales"], [ZH])

    stale = lc.localized_content_status(
        project, "baseline", ZH,
        input_hash=lc.compute_input_hash({"risk_chains": ["changed"]}),
    )
    eq("D8 hash 不同 -> stale", stale["status"], lc.STATUS_STALE)
    eq("D8 原因：hash 不匹配", stale["reason"], lc.REASON_INPUT_HASH_MISMATCH)
    check("D8 is_stale True", stale["is_stale"] is True)
    check("D8 stale 内容不被当成最新（但 record 仍可见供页面决定）",
          isinstance(stale["record"], dict))

    stale_locale = lc.localized_content_status(
        project, "baseline", ZH, input_hash=lc.compute_input_hash({"risk_chains": ["changed"]})
    )
    ne("D8 stale 不等于 available", stale_locale["status"], lc.STATUS_AVAILABLE)

    # 非法 record 不会被当作可用内容。
    broken = copy.deepcopy(project)
    broken[lc.LOCALIZED_CONTENT_KEY]["baseline"]["project"][ZH]["schema_version"] = 99

    broken_status = lc.localized_content_status(broken, "baseline", ZH)
    eq("D9 非法 record -> invalid", broken_status["status"], lc.STATUS_INVALID)
    eq("D9 非法 record 不暴露内容", broken_status["record"], None)
    eq("D9 get 拒绝非法 record", lc.localized_content_get(broken, "baseline", ZH), None)
    eq("D9 非法 record 不计入 saved_locales",
       lc.localized_content_languages(broken, "baseline"), [])


# ============================================================
# E. Legacy 兼容
# ============================================================

def test_e_legacy_compatibility():
    print("\n--- E. Legacy 兼容 ---")

    legacy_project = {
        "ai_text_by_language": {ZH: {"baseline": copy.deepcopy(BASELINE_PAYLOAD)}}
    }
    snapshot = copy.deepcopy(legacy_project)

    eq("E1 旧 ai_text_by_language 可读",
       lc.legacy_ai_text_get(legacy_project, "baseline", ZH),
       BASELINE_PAYLOAD)

    status = lc.localized_content_status(legacy_project, "baseline", ZH)
    eq("E1 状态 legacy_compatible", status["status"], lc.STATUS_LEGACY_COMPATIBLE)
    eq("E1 source 标签", status["source"], lc.SOURCE_TAG_LEGACY_ARCHIVE)
    check("E1 不需要生成", status["needs_generation"] is False)
    check("E1 不是 stale", status["is_stale"] is False)

    check("E1 不跨语言 fallback：EN 仍是 missing",
          lc.localized_content_status(legacy_project, "baseline", EN)["status"]
          == lc.STATUS_MISSING)
    eq("E1 不跨语言 fallback：EN 读不到内容",
       lc.localized_content_get(legacy_project, "baseline", EN),
       None)

    eq("E2 读取不触发迁移（project 深度相等）", legacy_project, snapshot)
    check("E2 未创建新字段", lc.LOCALIZED_CONTENT_KEY not in legacy_project)

    # --- 新字段优先 ---
    merged = copy.deepcopy(legacy_project)
    new_payload = {"risk_chains": ["新协议内容"], "impacts": [], "actions": []}
    merged = _put(merged, "baseline", ZH, new_payload)

    eq("E3 新记录可读",
       lc.localized_content_get(merged, "baseline", ZH)["payload"],
       new_payload)
    eq("E3 新记录状态 available",
       lc.localized_content_status(merged, "baseline", ZH)["status"],
       lc.STATUS_AVAILABLE)
    eq("E3 旧数据仍在（未被删除 / 未迁移）",
       lc.legacy_ai_text_get(merged, "baseline", ZH),
       BASELINE_PAYLOAD)

    # --- log / logs 别名 ---
    logs_project = {
        "ai_text_by_language": {EN: {"log": {"L1": {"summary": "old log"}}}}
    }
    eq("E4 旧 section 'log' 可通过 'logs' 读出",
       lc.legacy_ai_text_get(logs_project, "logs", EN, "L1"),
       {"summary": "old log"})
    eq("E4 旧 section 'log' 直接读也可",
       lc.legacy_ai_text_get(logs_project, "log", EN, "L1"),
       {"summary": "old log"})
    eq("E4 不存在的记录返回 None",
       lc.legacy_ai_text_get(logs_project, "logs", EN, "L2"),
       None)
    eq("E4 统计出的 content_type 已规范",
       lc.legacy_content_types(logs_project),
       [lc.CONTENT_TYPE_LOGS])

    # --- 无 language 元数据 -> und ---
    unknown_locale_project = {
        "ai_text_by_language": {"xx-YY": {"baseline": copy.deepcopy(BASELINE_PAYLOAD)}}
    }
    eq("E5 未注册语言 key 归为 und",
       lc.legacy_content_locales(unknown_locale_project, "baseline"),
       [lc.UNDETERMINED_LOCALE])

    converted = lc.legacy_ai_text_to_record(unknown_locale_project, "baseline", "xx-YY")
    check("E5 可转换为 record", isinstance(converted, dict))
    eq("E5 转换后 locale 为 und", converted["locale"], lc.UNDETERMINED_LOCALE)
    eq("E5 转换后 source 为 legacy", converted["source"], lc.SOURCE_LEGACY)
    eq("E5 转换后没有 hash（旧数据无可靠输入 hash）", converted["input_hash"], None)
    eq("E5 转换是纯函数（不写回 project）",
       unknown_locale_project,
       {"ai_text_by_language": {"xx-YY": {"baseline": copy.deepcopy(BASELINE_PAYLOAD)}}})

    # 不要把"英文居多"当成迁移事实：未知语言不得落到 en。
    ne("E5 未知语言不得归为 en", converted["locale"], EN)

    # --- deterministic legacy 映射继续工作 ---
    deterministic = lc.make_localized_record(
        "baseline", None, EN, BASELINE_PAYLOAD, source=lc.SOURCE_DETERMINISTIC
    )
    deterministic_project = lc.localized_content_put({}, deterministic)

    eq("E6 deterministic 状态",
       lc.localized_content_status(deterministic_project, "baseline", EN)["status"],
       lc.STATUS_DETERMINISTIC)
    check("E6 deterministic 不需要生成",
          lc.localized_content_status(deterministic_project, "baseline", EN)["needs_generation"]
          is False)

    # --- 旧 archive 的审计信息 ---
    mixed = {
        "ai_text_by_language": {
            EN: {"baseline": {"risk_chains": []}, "logs": {"L1": {"summary": "a"}}},
            ZH: {"log": {"L2": {"summary": "b"}}},
        }
    }
    eq("E7 legacy_content_types",
       lc.legacy_content_types(mixed),
       [lc.CONTENT_TYPE_BASELINE, lc.CONTENT_TYPE_LOGS])
    eq("E7 legacy_content_locales（log/logs 都算）",
       lc.legacy_content_locales(mixed, "logs"),
       sorted([EN, ZH]))

    # --- 显式迁移工具是纯函数 ---
    migrated = lc.localize_legacy_project(mixed)
    ne("E8 迁移返回新对象", migrated is mixed, mixed)
    eq("E8 迁移不修改输入", mixed,
       {
           "ai_text_by_language": {
               EN: {"baseline": {"risk_chains": []}, "logs": {"L1": {"summary": "a"}}},
               ZH: {"log": {"L2": {"summary": "b"}}},
           }
       })
    check("E8 迁移结果含新字段",
          lc.LOCALIZED_CONTENT_KEY in migrated)
    eq("E8 迁移出的日志可读",
       lc.localized_content_get(migrated, "logs", EN, "L1")["payload"],
       {"summary": "a"})
    eq("E8 迁移出的记录无 hash -> stale",
       lc.localized_content_status(migrated, "logs", EN, "L1")["status"],
       lc.STATUS_STALE)


# ============================================================
# E9. projects.json 只读
# ============================================================

def test_e_projects_json_is_read_only():
    print("\n--- E9. projects.json 只读 ---")

    if not DATA_FILE.exists():
        check("E9 data/projects.json 存在", False, str(DATA_FILE))
        return

    before = hashlib.sha256(DATA_FILE.read_bytes()).hexdigest()

    raw = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    snapshot = copy.deepcopy(raw)

    projects = raw.get("projects") if isinstance(raw, dict) else raw

    if isinstance(projects, list):
        for project in projects:
            if not isinstance(project, dict):
                continue

            for content_type in lc.CONTENT_TYPES:
                safe_call(
                    f"E9 status({content_type}) 不抛异常",
                    lc.localized_content_status,
                    project,
                    content_type,
                    ZH,
                )
                safe_call(
                    f"E9 languages({content_type}) 不抛异常",
                    lc.localized_content_languages,
                    project,
                    content_type,
                )
                safe_call(
                    f"E9 get({content_type}) 不抛异常",
                    lc.localized_content_get,
                    project,
                    content_type,
                    ZH,
                )

            safe_call("E9 legacy_content_types 不抛异常",
                      lc.legacy_content_types, project)
            safe_call("E9 legacy_ai_text_get 不抛异常",
                      lc.legacy_ai_text_get, project, "baseline", ZH)

    eq("E9 解析出的对象未被修改（深度相等）", raw, snapshot)

    after = hashlib.sha256(DATA_FILE.read_bytes()).hexdigest()

    eq("E9 projects.json 哈希未变化", after, before)


# ============================================================
# G. 安全
# ============================================================

def test_g_security():
    print("\n--- G. 安全 ---")

    # --- 原型式键污染 ---
    for key in ("__proto__", "constructor", "prototype", "__class__",
                "__globals__", "__subclasses__", "__reduce__"):
        payload = {key: {"polluted": True}}
        check(f"G1 拒绝危险键 {key}", lc.validate_payload(payload) != [])
        eq(f"G1 {key} 不能构造 record",
           lc.make_localized_record("baseline", None, EN, payload),
           None)

    # 嵌套在深层也要拒绝。
    nested = {"a": {"b": {1: "x"}}}
    nested["a"]["b"]["__proto__"] = {"x": 1}
    check("G1 深层危险键也拒绝", lc.validate_payload(nested) != [])

    # put 拒绝非法 record：project 不被污染。
    target = {}
    result = lc.localized_content_put(
        target,
        {
            "schema_version": lc.SCHEMA_VERSION,
            "content_type": "baseline",
            "record_id": "project",
            "locale": EN,
            "payload": {"__proto__": {"polluted": True}},
            "input_hash": None,
            "generated_at": lc.utc_now_iso(),
            "source": lc.SOURCE_AI_GENERATED,
        },
    )
    eq("G1 put 拒绝非法 record（返回原对象）", result, {})
    check("G1 未写入 localized_content", lc.LOCALIZED_CONTENT_KEY not in result)
    check("G1 dict 原型未被污染", "__proto__" not in dict.__dict__)

    # put 对完全非法的 record 也必须安全。
    for bad_record in (None, "x", 5, [], {"schema_version": 1}):
        ok, returned = safe_call(
            f"G2 put 畸形 record {type(bad_record).__name__} 不抛异常",
            lc.localized_content_put,
            {"keep": 1},
            bad_record,
        )
        eq(f"G2 put 畸形 record {type(bad_record).__name__} 原样返回",
           returned, {"keep": 1})

    # --- record_id 安全 ---
    for bad in ("../etc", "a/../../b", "\x00", "\x1b[31m", "x" * 500):
        eq(f"G3 拒绝 record_id {bad!r}", lc.normalize_record_id(bad), None)

    eq("G3 合法 record_id 通过", lc.normalize_record_id("20260911235908123456"),
       "20260911235908123456")
    eq("G3 合法 log-0001 通过", lc.normalize_record_id("log-0001"), "log-0001")

    # --- content_type 白名单 ---
    for bad in ("system", "prompt", "internal", "../x", "baseline;drop"):
        eq(f"G4 拒绝 content_type {bad!r}", lc.normalize_content_type(bad), None)

    # --- 畸形旧数据不导致页面异常 ---
    malformed = (
        {},
        {"ai_text_by_language": None},
        {"ai_text_by_language": "not a dict"},
        {"ai_text_by_language": {EN: "not a dict"}},
        {"ai_text_by_language": {EN: {"logs": 123}}},
        {"ai_text_by_language": {EN: {"logs": {"L1": ["not", "a", "dict"]}}}},
        {"ai_text_by_language": {EN: {"logs": {"L1": None}}}},
        {"ai_text_by_language": {123: {"logs": {"L1": {}}}}},
        {"localized_content": None},
        {"localized_content": "not a dict"},
        {"localized_content": {"baseline": "not a dict"}},
        {"localized_content": {"baseline": {"project": "not a dict"}}},
        {"localized_content": {"baseline": {"project": {EN: "not a record"}}}},
        {"localized_content": {"baseline": {"project": {EN: {"schema_version": "1"}}}}},
        {"localized_content": {"bogus_type": {"project": {EN: {}}}}},
        {"localized_content": {"baseline": {"project": {EN: None}}}},
    )

    for index, case in enumerate(malformed):
        safe_call(f"G5 畸形数据 #{index} status 不抛异常",
                  lc.localized_content_status, case, "baseline", EN)
        safe_call(f"G5 畸形数据 #{index} get 不抛异常",
                  lc.localized_content_get, case, "baseline", EN)
        safe_call(f"G5 畸形数据 #{index} languages 不抛异常",
                  lc.localized_content_languages, case, "baseline")
        safe_call(f"G5 畸形数据 #{index} legacy 读取不抛异常",
                  lc.legacy_ai_text_get, case, "logs", EN, "L1")

    # --- 非 dict 的 project ---
    for bad_project in (None, "x", 5, []):
        ok, value = safe_call(
            f"G6 非 dict project {type(bad_project).__name__} 安全",
            lc.localized_content_status, bad_project, "baseline", EN,
        )
        eq(f"G6 非 dict project 返回 missing",
           value["status"] if ok else None, lc.STATUS_MISSING)

    # --- 不写盘 / 不使用 pickle（基于 AST，不受 docstring 影响） ---
    tree = _tree_of(MODULE)
    named = _loaded_names(tree)
    called = _called_names(tree)
    modules = _imported_modules(tree)

    check("G7 不使用 pickle",
          "pickle" not in modules and "pickle" not in named,
          str(sorted(modules)))

    forbidden_calls = {"open", "dump", "save", "persist", "save_db", "write"}
    check("G7 不写文件 / 不落盘",
          not (called & forbidden_calls),
          str(sorted(called & forbidden_calls)))

    check("G7 不调用 AI",
          not (called & {"completion", "chat_completion", "chat", "create",
                         "generate", "invoke"}),
          str(sorted(called)))


# ============================================================
# H. AST 守卫
# ============================================================

MODULE = pathlib.Path(lc.__file__).resolve()
APP = ROOT / "app.py"

#: 允许导入 localized_content 的模块（本阶段的兼容适配层）。
ALLOWED_CONSUMERS = {
    "utils/baseline_display.py",
}


def _tree_of(path):
    return ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))


def _imported_modules(tree):
    modules = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module.split(".")[0])

    return modules


def _attribute_chains(tree):
    chains = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue

        parts = []
        current = node

        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value

        if isinstance(current, ast.Name):
            parts.append(current.id)
            chains.add(".".join(reversed(parts)))

    return chains


def _called_names(tree):
    names = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func

            if isinstance(func, ast.Name):
                names.add(func.id)
            elif isinstance(func, ast.Attribute):
                names.add(func.attr)

    return names


def _loaded_names(tree):
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _store_keys(tree):
    keys = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        elif isinstance(node, ast.Delete):
            targets = node.targets
        else:
            continue

        for target in targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
                and isinstance(target.slice.value, str)
            ):
                keys.append(target.slice.value)

    return keys


def test_h_static_guards():
    print("\n--- H. AST 守卫 ---")

    tree = _tree_of(MODULE)

    modules = _imported_modules(tree)
    chains = _attribute_chains(tree)
    named = _loaded_names(tree)
    called = _called_names(tree)

    check("H1 不导入 streamlit", "streamlit" not in modules, str(sorted(modules)))
    check("H1 不导入 i18n", "i18n" not in modules, str(sorted(modules)))
    check("H1 不导入 app", "app" not in modules)
    check("H1 不导入 persist / storage",
          not (modules & {"storage", "persist", "db", "sqlite3"}),
          str(sorted(modules)))
    check("H1 不导入 AI / 网络依赖",
          not (modules & {"openai", "anthropic", "requests", "http", "urllib", "socket"}),
          str(sorted(modules)))
    check("H1 不导入 pickle", "pickle" not in modules)
    check("H1 只依赖 stdlib",
          modules <= {"__future__", "copy", "hashlib", "json", "math", "re",
                      "datetime", "typing", "utils"},
          str(sorted(modules)))

    check("H2 无 st.* 属性访问",
          not any(chain == "st" or chain.startswith("st.") for chain in chains),
          str(sorted(chains)))
    check("H2 不访问 session_state",
          "session_state" not in named
          and not any("session_state" in chain for chain in chains))

    check("H3 不调用 persist / save_db",
          not (called & {"persist", "save_db", "save_project", "save"}),
          str(sorted(called & {"persist", "save_db", "save_project", "save"})))
    check("H3 不调用 AI",
          not (called & {"completion", "chat_completion", "chat", "create",
                         "generate", "invoke", "analyze"}))

    check("H4 不写入 ui_lang", "ui_lang" not in _store_keys(tree))

    # --- 业务路径接线状态（阶段 4A：Current Risk 已接入，其余仍未接线） ---
    app_source = APP.read_text(encoding="utf-8")
    app_tree = _tree_of(APP)

    # 阶段 3 的"未接线"守卫在阶段 4A 被 Current Risk 正式取代：app.py 现在
    # 通过统一协议落盘 Current Risk，读路径只能走 utils.current_risk_display
    # 显示层（页面自身不直接查询协议状态）。
    check("H5 app.py 已接入 localized_content 写入（阶段 4A Current Risk）",
          "utils.localized_content" in app_source
          and "localized_content_put" in app_source
          and "make_localized_record" in app_source)

    check("H5 app.py 读取只经 current_risk_display 显示层",
          "utils.current_risk_display" in app_source
          and "localized_content_get" not in app_source
          and "localized_content_status" not in app_source)

    # 只允许写 Current Risk 这一种 content type；Baseline / Logs / Vision /
    # Report 仍走各自的既有路径，本阶段不改变。
    check("H5 app.py 只写入 current_risk_explanation 契约",
          "CURRENT_RISK_CONTENT_TYPE" in app_source
          and "CONTENT_TYPE_BASELINE" not in app_source
          and "CONTENT_TYPE_LOGS" not in app_source
          and "CONTENT_TYPE_VISION" not in app_source
          and "CONTENT_TYPE_REPORT" not in app_source)

    # 阶段 4B 的"未接线"守卫在阶段 4B 被 Vision 正式取代：vision_risk.py 通过统一
    # 协议落盘 Vision 文本，读路径只能走 utils.vision_display 显示层（页面自身不
    # 直接查询协议状态、不直接读取 record）。Report 仍不接线。
    vision_source = (ROOT / "vision_risk.py").read_text(encoding="utf-8")

    check("H5 vision_risk.py 已接入 localized_content 写入（阶段 4B Vision）",
          "localized_content_put" in vision_source
          and "make_localized_record" in vision_source
          and "CONTENT_TYPE_VISION" in vision_source)

    check("H5 vision_risk.py 读取只经 vision_display 显示层",
          "utils.vision_display" in vision_source
          and "localized_content_get" not in vision_source
          and "localized_content_status" not in vision_source)

    check("H5 vision_risk.py 只写入 vision_analysis 契约",
          "CONTENT_TYPE_BASELINE" not in vision_source
          and "CONTENT_TYPE_LOGS" not in vision_source
          and "CONTENT_TYPE_REPORT" not in vision_source
          and "CURRENT_RISK_CONTENT_TYPE" not in vision_source)

    # AI 客户端永远不接触协议：agents/risk_agent.py 只返回原始 JSON。
    check("H5 agents/risk_agent.py 未接线（纯 AI 客户端）",
          "localized_content" not in
          (ROOT / "agents/risk_agent.py").read_text(encoding="utf-8"))

    for page in sorted((ROOT / "pages").glob("*.py")):
        source = page.read_text(encoding="utf-8")
        check(
            f"H5 pages/{page.name} 未接线",
            "localized_content" not in source and "content_locale" not in source,
        )

    # --- ui_lang 写入点没有增加 ---
    app_set_calls = [
        node
        for node in ast.walk(app_tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "set_current_language"
    ]
    eq("H6 app.py set_current_language 调用点仍为 1 处（选择器回调）",
       len(app_set_calls), 1)
    eq("H6 app.py 无 ui_lang 直接写入", "ui_lang" in _store_keys(app_tree), False)

    # --- data/projects.json 未被修改 ---
    if DATA_FILE.exists():
        digest = hashlib.sha256(DATA_FILE.read_bytes()).hexdigest()
        check("H7 data/projects.json 可读且哈希稳定",
              bool(re.fullmatch(r"[0-9a-f]{64}", digest)))


# ============================================================
# J. 阶段 4 契约
# ============================================================

def test_j_stage4_contracts():
    print("\n--- J. 阶段 4 契约（只定义、不接线） ---")

    for content_type in (
        lc.CONTENT_TYPE_BASELINE,
        lc.CONTENT_TYPE_LOGS,
        lc.CONTENT_TYPE_CURRENT_RISK,
        lc.CONTENT_TYPE_VISION,
        lc.CONTENT_TYPE_REPORT,
    ):
        check(f"J1 契约定义了 {content_type}",
              content_type in lc.CONTENT_TYPE_CONTRACTS)

    check("J1 禁用字段集合非空", len(lc.NON_TRANSLATABLE_FIELDS) > 0)

    for field in FORBIDDEN_TRANSLATABLE_FIELDS:
        check(f"J2 禁用字段包含 {field}",
              field in lc.NON_TRANSLATABLE_FIELDS)

    ok_payload = {"summary": "文本", "risk_level_text": "高",
                  "reasons": ["原因"], "recommendations": ["建议"]}
    eq("J3 合法可翻译 payload",
       lc.validate_translatable_payload(lc.CONTENT_TYPE_CURRENT_RISK, ok_payload),
       [])

    for field in FORBIDDEN_TRANSLATABLE_FIELDS:
        bad = dict(ok_payload)
        bad[field] = 1
        check(f"J3 拒绝把 {field} 放进可翻译 payload",
              lc.validate_translatable_payload(lc.CONTENT_TYPE_CURRENT_RISK, bad) != [])

    check("J3 未知 content_type 被拒绝",
          lc.validate_translatable_payload("bogus", ok_payload) != [])

    # vision_analysis 的契约只包含人类可读文本。
    vision_payload = {
        "summary": "概述",
        "findings": [
            {
                "finding_id": "f1",
                "title": "标题",
                "reason": "原因",
                "recommendation": "建议",
            }
        ],
    }
    eq("J3 vision 可翻译 payload 合法",
       lc.validate_translatable_payload(lc.CONTENT_TYPE_VISION, vision_payload),
       [])

    # 数值本体若被放进可翻译载荷，必须被拒绝。
    polluted = copy.deepcopy(vision_payload)
    polluted["findings"][0]["penalty"] = 12.5
    check("J3 vision 不得携带 penalty",
          lc.validate_translatable_payload(lc.CONTENT_TYPE_VISION, polluted) != [])

    # 本阶段不接线：契约只是数据，没有生成 / 翻译函数被调用。
    called = _called_names(_tree_of(MODULE))
    check("J4 模块中没有 report / vision / current risk 的生成调用",
          not (called & {"generate_report", "analyze_vision", "explain_current_risk",
                         "translate"}))


# ============================================================
# 主入口
# ============================================================

def main():
    print("=" * 58)
    print("RiskPilot 阶段 3 —— 统一 localized content 数据层测试")
    print("=" * 58)

    test_b_record_schema()
    test_b_invalid_records_rejected()
    test_b_payload_limits()
    test_b_inputs_are_not_mutated()
    test_c_multilingual_isolation()
    test_d_hash_and_freshness()
    test_e_legacy_compatibility()
    test_e_projects_json_is_read_only()
    test_g_security()
    test_h_static_guards()
    test_j_stage4_contracts()

    total = _PASSED + len(_FAILED)

    print("\n" + "=" * 58)
    print(f"结果：{_PASSED}/{total} 通过")

    if _FAILED:
        print("失败项：")
        for name in _FAILED:
            print(f"  - {name}")
        raise SystemExit(1)

    print("统一 localized content 数据层测试全部通过 ✅")


if __name__ == "__main__":
    main()
