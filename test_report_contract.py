# -*- coding: utf-8 -*-
"""
RiskPilot 报告契约测试（全球化阶段 4C-2 + 4C-5）。

覆盖 ``utils/report_contract.py``：

    A. report locale 规范化（含旧显示值兼容、未注册语言拒绝、planned 可用）
    B. report audience / report type 稳定内部 ID
    C. AI 可翻译文本 payload 校验
    D. report record_id
    E. 静态守卫（AST 纯度）：不导入 Streamlit / app / AI / DOCX，
       不访问 session_state、不调用 persist/save_db、不读写任何文件
    F. 报告中心接线（app.py）：六语言对齐、prompt 使用 AI language name、
       报告身份绑定（project/locale/audience/type）、身份不匹配即失效、
       只有点击生成才调用 AI、DOCX 使用目标语言与规范风险值、DOCX 固定标签
       与系统项目类型按报告语言显示且不回写用户数据

运行方式：

    .venv/bin/python test_report_contract.py

A–E 只导入纯模块并对纯函数做断言。

F 段用 stub 替换 ``streamlit`` 模块后导入 ``app.py``，把
``agents.risk_agent.RiskAgent`` 换成 mock：**不启动 Streamlit 服务、
不联网、不调用真实 AI、不写任何文件**（DOCX 只在内存生成再解包），
且只用内存中的项目字典，不读不写 ``data/projects.json``。

关于"本阶段未修改某些文件"（4C-2.1 测试稳定化）：

本脚本**不再冻结任何业务文件的字节哈希**。4C-2 曾用固定 SHA-256 断言
``app.py`` / ``agents/risk_agent.py`` / ``vision_risk.py`` / ``i18n.py`` /
``utils/localized_content.py`` / ``locales/*.json`` / ``pages/*.py`` /
``data/projects.json`` 未变化，会带来三类假失败：

* 后续阶段（4C-3 改共享 schema、4C-5 接线 ``app.py``、旧代码归档阶段删除
  ``pages/*``）**本来就需要合法修改**这些文件；
* 多人协作时，其他成员的合法修改会让无关测试失败；
* 文件变化已由 Git 追踪，单元测试不应复制 Git 的职责。

因此改为：**由一次性命令证明**（执行前后 ``shasum -a 256`` 对照、
``git status --short`` / ``git diff --stat``），且**不替换为新的硬编码哈希**。
永久测试只保留与文件字节无关的**行为与架构守卫**（A–E1）。
"""

import ast
import copy
import inspect
from pathlib import Path

from utils import content_locale as cl
from utils import localized_content as lc
from utils import locale_registry as registry
from utils import report_contract as rc


ROOT = Path(__file__).resolve().parent
MODULE_PATH = ROOT / "utils" / "report_contract.py"


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


def contains(name, haystack, needle):
    check(name, needle in haystack, f"{needle!r} not in {haystack!r}")


def _valid_payload():
    return {
        "title": "  Daily Risk Alert  ",
        "opening": "Opening paragraph.",
        "summary": "Summary paragraph.",
        "key_points": ["Point A", "Point B"],
        "actions": ["Action A"],
        "closing": "Closing paragraph.",
    }


# ============================================================
# A. report locale
# ============================================================

#: 阶段 4C 允许生成报告的 11 种语言（registry 顺序 = manifest 顺序）。
EXPECTED_REPORT_LOCALES = (
    "en",
    "zh-CN",
    "es",
    "pt-BR",
    "fr",
    "sw",
    "de",
    "ar",
    "hi",
    "ja",
    "ko",
)

#: 旧报告语言显示值 -> 规范 BCP 47（阶段 4C-2 的正式映射表）。
EXPECTED_DISPLAY_MAP = {
    "中文": "zh-CN",
    "Chinese": "zh-CN",
    "简体中文": "zh-CN",
    "English": "en",
    "Español": "es",
    "Português": "pt-BR",
    "Français": "fr",
    "Deutsch": "de",
    "日本語": "ja",
    "한국어": "ko",
    "Swahili": "sw",
    "Kiswahili": "sw",
    "Hindi": "hi",
    "हिन्दी": "hi",
    "العربية": "ar",
}

#: 必须被拒绝的模糊 / 未注册取值。
EXPECTED_REJECTED = (
    "Local Language",
    "Italiano",
    "Italian",
    "Bahasa Melayu",
    "Bahasa Indonesia",
    "pt-PT",
    "zh-TW",
    "zh-Hant",
    "it",
    "ms",
    "id",
    "zz-ZZ",
    "???",
    None,
    "",
    "   ",
    42,
    3.14,
    True,
    b"en",
    ["en"],
    {"locale": "en"},
    object(),
)


def test_report_locale_supported():
    print("\n--- A1. 允许的报告语言（registry 已注册 + 有明确 AI 名称）---")

    eq(
        "A1 supported_report_locales 为 11 种 registry 语言",
        rc.supported_report_locales(),
        EXPECTED_REPORT_LOCALES,
    )

    for code in EXPECTED_REPORT_LOCALES:
        result = rc.normalize_report_locale(code)
        check(f"A1 {code} 合法", result["valid"], result)
        eq(f"A1 {code} 规范值不变", result["locale"], code)
        check(
            f"A1 {code} AI 名称非空",
            bool(rc.report_locale_ai_name(code)),
            rc.report_locale_ai_name(code),
        )

    # planned locale 不是"报告语言不可用"的理由。
    planned = [
        code
        for code in EXPECTED_REPORT_LOCALES
        if registry.status(code) == "planned"
    ]
    # 阶段 2E：sw 启用后，报告语言中仍是 planned 的还剩 5 种。
    check("A1 存在 planned locale（用于验证 planned 可用）", len(planned) >= 5, planned)

    for code in planned:
        check(
            f"A1 planned {code} 可作为报告语言",
            rc.normalize_report_locale(code)["valid"],
            rc.normalize_report_locale(code),
        )

    eq("A1 AI 名称使用 registry 元数据（sw）", rc.report_locale_ai_name("sw"), "Swahili")
    eq("A1 AI 名称使用 registry 元数据（pt-BR）", rc.report_locale_ai_name("pt-BR"), "Brazilian Portuguese")


def test_report_locale_display_values():
    print("\n--- A2. 旧显示值 / native name / english name 兼容 ---")

    for display, expected in EXPECTED_DISPLAY_MAP.items():
        result = rc.normalize_report_locale(display)
        check(f"A2 {display!r} -> {expected}", result["valid"], result)
        eq(f"A2 {display!r} 规范值", result["locale"], expected)

    # 空白与大小写容错。
    eq("A2 空白容错", rc.normalize_report_locale("  English  ")["locale"], "en")
    eq("A2 大小写容错", rc.normalize_report_locale("english")["locale"], "en")
    eq("A2 下划线容错", rc.normalize_report_locale("zh_CN")["locale"], "zh-CN")


def test_report_locale_rejected():
    print("\n--- A3. 模糊 / 未注册取值一律 invalid + und ---")

    for value in EXPECTED_REJECTED:
        result = rc.normalize_report_locale(value)
        check(
            f"A3 {value!r} 被拒绝",
            result["valid"] is False,
            result,
        )
        eq(f"A3 {value!r} fallback 为 und", result["locale"], "und")

    # 明确的结构化 reason（便于调用方区分"不是字符串"与"不支持的语言"）。
    eq(
        "A3 None 的 reason",
        rc.normalize_report_locale(None)["reason"],
        rc.REASON_NOT_A_STRING,
    )
    eq(
        "A3 Local Language 的 reason",
        rc.normalize_report_locale("Local Language")["reason"],
        rc.REASON_UNSUPPORTED_REPORT_LOCALE,
    )
    eq(
        "A3 pt-PT 的 reason",
        rc.normalize_report_locale("pt-PT")["reason"],
        rc.REASON_UNSUPPORTED_REPORT_LOCALE,
    )

    # 不允许"悄悄映射成别的语言"。
    check(
        "A3 pt-PT 不得映射为 pt-BR",
        rc.normalize_report_locale("pt-PT")["locale"] != "pt-BR",
    )
    check(
        "A3 zh-TW 不得映射为 zh-CN",
        rc.normalize_report_locale("zh-TW")["locale"] != "zh-CN",
    )
    check(
        "A3 Italiano 不得映射为 en",
        rc.normalize_report_locale("Italiano")["locale"] != "en",
    )
    for value in ("Italiano", "Bahasa Melayu", "Bahasa Indonesia", "Local Language"):
        eq(
            f"A3 {value!r} 无 AI 名称",
            rc.report_locale_ai_name(value),
            None,
        )


def test_report_locale_purity():
    print("\n--- A4. 纯读取：不修改任何传入状态 ---")

    state = {
        "risk_report_language": "Kiswahili",
        "ui_lang": "en",
        "content_locale": "en",
    }
    snapshot = copy.deepcopy(state)

    rc.normalize_report_locale(state["risk_report_language"])
    rc.report_locale_ai_name(state["risk_report_language"])
    rc.supported_report_locales()
    rc.normalize_report_audience("Manager")
    rc.normalize_report_type("Risk Report")
    rc.validate_report_text_payload(_valid_payload())
    rc.make_report_record_id("manager", "risk_report")

    eq("A4 state 键集合未变化", set(state), set(snapshot))
    eq("A4 state 值未变化", state, snapshot)
    eq(
        "A4 未新增任何键（含 content_locale override）",
        set(state),
        {"risk_report_language", "ui_lang", "content_locale"},
    )

    # 传入的可变对象同样不被修改。
    payload = _valid_payload()
    payload_snapshot = copy.deepcopy(payload)
    rc.validate_report_text_payload(payload)
    eq("A4 传入 payload 未被修改", payload, payload_snapshot)

    # 反向映射（阶段 4C-2 补 ar）不会产生语言漂移。
    for code in EXPECTED_REPORT_LOCALES:
        display = cl.locale_to_report_language(code)
        check(
            f"A4 {code} 反向显示值非空",
            bool(display),
            display,
        )
        eq(
            f"A4 {code} 往返不漂移",
            rc.normalize_report_locale(display)["locale"],
            code,
        )


# ============================================================
# B. audience / report type
# ============================================================


def test_audience_normalization():
    print("\n--- B1. report audience 稳定内部 ID ---")

    for raw, expected in (
        ("worker", "worker"),
        ("Worker", "worker"),
        ("  MANAGER  ", "manager"),
        ("Manager", "manager"),
        ("client", "client"),
        ("Client", "client"),
    ):
        result = rc.normalize_report_audience(raw)
        check(f"B1 {raw!r} 合法", result["valid"], result)
        eq(f"B1 {raw!r} -> {expected}", result["audience"], expected)

    for raw in ("Boss", "工人", None, 1, b"worker", ["worker"], ""):
        result = rc.normalize_report_audience(raw)
        check(f"B1 {raw!r} 被拒绝", result["valid"] is False and result["audience"] is None, result)
        eq(
            f"B1 {raw!r} reason",
            result["reason"],
            rc.REASON_UNSUPPORTED_REPORT_AUDIENCE,
        )

    # 返回值是稳定 ID，绝不是"翻译后的显示文本"。
    eq("B1 不返回翻译文本", rc.normalize_report_audience("Worker")["audience"], "worker")
    check(
        "B1 拒绝翻译后的显示值",
        rc.normalize_report_audience("工人")["valid"] is False,
    )


def test_report_type_normalization():
    print("\n--- B2. report type 稳定内部 ID ---")

    for raw, expected in (
        ("Today's Risk Alert", "daily_risk_alert"),
        ("today's risk alert", "daily_risk_alert"),
        ("Today\u2019s Risk Alert", "daily_risk_alert"),
        ("  Risk   Report  ", "risk_report"),
        ("risk_report", "risk_report"),
        ("daily_risk_alert", "daily_risk_alert"),
    ):
        result = rc.normalize_report_type(raw)
        check(f"B2 {raw!r} 合法", result["valid"], result)
        eq(f"B2 {raw!r} -> {expected}", result["report_type"], expected)

    for raw in ("Alert", "报告", "risk report!", None, 7, b"Risk Report", ("x",), ""):
        result = rc.normalize_report_type(raw)
        check(
            f"B2 {raw!r} 被拒绝",
            result["valid"] is False and result["report_type"] is None,
            result,
        )
        eq(f"B2 {raw!r} reason", result["reason"], rc.REASON_UNSUPPORTED_REPORT_TYPE)


# ============================================================
# C. 可翻译文本 payload
# ============================================================


def test_payload_valid():
    print("\n--- C1. 合法 payload ---")

    payload = _valid_payload()
    snapshot = copy.deepcopy(payload)
    result = rc.validate_report_text_payload(payload)

    check("C1 合法 payload 通过", result["valid"], result)
    eq("C1 无错误", result["errors"], [])
    eq("C1 无 reason", result["reason"], None)
    eq("C1 字段集合严格", set(result["payload"]), set(rc.REPORT_TEXT_FIELDS))

    eq("C1 title 去首尾空白", result["payload"]["title"], "Daily Risk Alert")
    eq("C1 列表项去首尾空白", result["payload"]["key_points"], ["Point A", "Point B"])
    eq("C1 入参未被修改", payload, snapshot)
    check("C1 返回的是副本", result["payload"] is not payload)
    check(
        "C1 列表也是新对象",
        result["payload"]["key_points"] is not payload["key_points"],
    )


def test_payload_key_set():
    print("\n--- C2. key 集合严格 ---")

    payload = _valid_payload()
    del payload["closing"]
    result = rc.validate_report_text_payload(payload)
    check("C2 缺字段拒绝", result["valid"] is False and result["payload"] is None, result)
    contains("C2 缺字段错误", result["errors"], "missing_field:closing")

    payload = _valid_payload()
    payload["extra"] = "x"
    result = rc.validate_report_text_payload(payload)
    check("C2 额外字段拒绝", result["valid"] is False, result)
    contains("C2 额外字段错误", result["errors"], "unsupported_field:extra")

    payload = _valid_payload()
    payload[" "] = "x"
    result = rc.validate_report_text_payload(payload)
    check("C2 空白 key 拒绝", result["valid"] is False, result)
    contains("C2 空白 key 错误", result["errors"], "unsupported_field:")

    for raw in (None, [], "text", 3, b"{}", ("title",)):
        result = rc.validate_report_text_payload(raw)
        check(f"C2 非 dict 拒绝（{type(raw).__name__}）", result["valid"] is False, result)
        eq(
            f"C2 非 dict reason（{type(raw).__name__}）",
            result["reason"],
            rc.REASON_INVALID_REPORT_PAYLOAD,
        )


def test_payload_forbidden_fields():
    print("\n--- C3. 禁止进入 payload 的数值字段 ---")

    forbidden = (
        "risk_level",
        "score",
        "severity",
        "likelihood",
        "exposure",
        "raw_risk",
        "penalty",
        "confidence",
        "components",
        "weights",
        "risk_score",
        "risk_count",
        "visual_risk_penalty",
        "coordinates",
        "bbox",
        "geometry",
    )

    for field in forbidden:
        payload = _valid_payload()
        payload[field] = 1
        result = rc.validate_report_text_payload(payload)
        check(f"C3 {field} 被拒绝", result["valid"] is False, result)
        contains(f"C3 {field} 错误", result["errors"], f"forbidden_field:{field}")

    # 大小写 / 空白变体同样被拒绝。
    payload = _valid_payload()
    payload["  RISK_LEVEL  "] = 1
    result = rc.validate_report_text_payload(payload)
    check("C3 变体风险字段拒绝", result["valid"] is False, result)
    contains("C3 变体风险字段错误", result["errors"], "forbidden_field:RISK_LEVEL")

    # 嵌套数值字段同样不可能出现（列表元素必须是 str）。
    payload = _valid_payload()
    payload["key_points"] = [{"score": 90}]
    result = rc.validate_report_text_payload(payload)
    check("C3 嵌套 dict 拒绝", result["valid"] is False, result)
    contains("C3 嵌套 dict 错误", result["errors"], "not_a_string_item:key_points[0]:dict")

    # 契约本身保证：canonical facts 与可翻译文本字段不相交。
    eq(
        "C3 canonical facts 与文本字段不相交",
        set(rc.REPORT_CANONICAL_FACT_FIELDS) & set(rc.REPORT_TEXT_FIELDS),
        set(),
    )
    for field in ("risk_level", "risk_score", "components", "weights"):
        check(
            f"C3 canonical 数值字段 {field} 在禁止清单内",
            field in rc.FORBIDDEN_PAYLOAD_FIELDS,
        )


def test_payload_value_types():
    print("\n--- C4. 类型 / 空值 / 长度 ---")

    bad_values = (
        None,
        1,
        1.5,
        float("nan"),
        float("inf"),
        float("-inf"),
        True,
        b"bytes",
        bytearray(b"x"),
        {"a": 1},
        ["a"],
        ("a",),
        {"a"},
        frozenset({"a"}),
        object(),
    )

    for bad in bad_values:
        payload = _valid_payload()
        payload["title"] = bad
        result = rc.validate_report_text_payload(payload)
        check(
            f"C4 title={type(bad).__name__}/{bad!r} 被拒绝",
            result["valid"] is False,
            result,
        )
        check(
            f"C4 title={type(bad).__name__} 有错误条目",
            any(item.startswith("not_a_string:title") for item in result["errors"]),
            result["errors"],
        )

    payload = _valid_payload()
    payload["summary"] = "   "
    result = rc.validate_report_text_payload(payload)
    check("C4 空白文本拒绝", result["valid"] is False, result)
    contains("C4 空白文本错误", result["errors"], "empty_text:summary")

    payload = _valid_payload()
    payload["title"] = "x" * (rc.MAX_TITLE_LENGTH + 1)
    result = rc.validate_report_text_payload(payload)
    contains("C4 title 超长错误", result["errors"], "too_long:title")

    for field in ("opening", "summary", "closing"):
        payload = _valid_payload()
        payload[field] = "x" * (rc.MAX_TEXT_LENGTH + 1)
        result = rc.validate_report_text_payload(payload)
        contains(f"C4 {field} 超长错误", result["errors"], f"too_long:{field}")

    # 边界值：恰好等于上限必须通过。
    payload = _valid_payload()
    payload["title"] = "x" * rc.MAX_TITLE_LENGTH
    check("C4 title 等于上限通过", rc.validate_report_text_payload(payload)["valid"])


def test_payload_lists():
    print("\n--- C5. key_points / actions 列表规则 ---")

    for field in rc.REPORT_TEXT_LIST_FIELDS:
        payload = _valid_payload()
        payload[field] = "not a list"
        result = rc.validate_report_text_payload(payload)
        contains(f"C5 {field} 非列表错误", result["errors"], f"not_a_list:{field}:string")

        payload = _valid_payload()
        payload[field] = []
        result = rc.validate_report_text_payload(payload)
        check(f"C5 {field} 空列表拒绝", result["valid"] is False, result)
        contains(f"C5 {field} 空列表错误", result["errors"], f"list_size:{field}")

        payload = _valid_payload()
        payload[field] = ["ok"] * (rc.MAX_LIST_ITEMS + 1)
        result = rc.validate_report_text_payload(payload)
        check(f"C5 {field} 超长列表拒绝", result["valid"] is False, result)
        contains(f"C5 {field} 超长列表错误", result["errors"], f"list_size:{field}")

        payload = _valid_payload()
        payload[field] = [None]
        result = rc.validate_report_text_payload(payload)
        contains(
            f"C5 {field} 非字符串项错误",
            result["errors"],
            f"not_a_string_item:{field}[0]:null",
        )

        payload = _valid_payload()
        payload[field] = ["   "]
        result = rc.validate_report_text_payload(payload)
        contains(f"C5 {field} 空白项错误", result["errors"], f"empty_item:{field}[0]")

        payload = _valid_payload()
        payload[field] = ["x" * (rc.MAX_LIST_ITEM_LENGTH + 1)]
        result = rc.validate_report_text_payload(payload)
        contains(f"C5 {field} 超长项错误", result["errors"], f"too_long_item:{field}[0]")

        # 边界：8 项、每项 1000 字符必须通过。
        payload = _valid_payload()
        payload[field] = ["x" * rc.MAX_LIST_ITEM_LENGTH] * rc.MAX_LIST_ITEMS
        check(f"C5 {field} 等于上限通过", rc.validate_report_text_payload(payload)["valid"])


# ============================================================
# D. record_id
# ============================================================


def test_record_id():
    print("\n--- D1. report record_id ---")

    first = rc.make_report_record_id("manager", "risk_report")
    second = rc.make_report_record_id("Manager", "Risk Report")

    check("D1 合法输入通过", first["valid"] and second["valid"], first)
    eq("D1 稳定 ID", first["record_id"], "report::manager::risk_report")
    eq("D1 幂等（大小写/空白不影响）", second["record_id"], first["record_id"])

    ids = {
        rc.make_report_record_id(audience, report_type)["record_id"]
        for audience in rc.REPORT_AUDIENCES
        for report_type in rc.REPORT_TYPES
    }
    eq("D1 6 种组合互不相同", len(ids), 6)

    eq(
        "D1 worker + daily alert",
        rc.make_report_record_id("Worker", "Today's Risk Alert")["record_id"],
        "report::worker::daily_risk_alert",
    )
    eq(
        "D1 client + risk report",
        rc.make_report_record_id("Client", "Risk Report")["record_id"],
        "report::client::risk_report",
    )

    # locale 不进入 record_id（localized_content 已按 locale 分桶）：
    # 结构上证明 —— 函数签名里根本没有 locale 参数。
    eq(
        "D1 make_report_record_id 只接受 audience / report_type",
        tuple(inspect.signature(rc.make_report_record_id).parameters),
        ("audience", "report_type"),
    )

    # 生成的 id 必须能被共享协议接受（同一把尺子）。
    for record_id in sorted(ids):
        eq(
            f"D1 localized_content 接受 {record_id}",
            lc.normalize_record_id(record_id),
            record_id,
        )
        check(f"D1 is_safe_report_record_id({record_id})", rc.is_safe_report_record_id(record_id))


def test_record_id_rejected():
    print("\n--- D2. 非法输入与不安全 id ---")

    for audience, report_type, expected_reason in (
        ("boss", "risk_report", rc.REASON_UNSUPPORTED_REPORT_AUDIENCE),
        (None, "risk_report", rc.REASON_UNSUPPORTED_REPORT_AUDIENCE),
        ("worker", "boss", rc.REASON_UNSUPPORTED_REPORT_TYPE),
        ("worker", None, rc.REASON_UNSUPPORTED_REPORT_TYPE),
        ("工人", "报告", rc.REASON_UNSUPPORTED_REPORT_AUDIENCE),
    ):
        result = rc.make_report_record_id(audience, report_type)
        check(
            f"D2 make({audience!r}, {report_type!r}) 被拒绝",
            result["valid"] is False and result["record_id"] is None,
            result,
        )
        eq(f"D2 make({audience!r}, {report_type!r}) reason", result["reason"], expected_reason)

    for unsafe in (
        "",
        "../etc/passwd",
        "a/../b",
        "a\\b",
        "/root",
        "C:\\x",
        "report\x00manager",
        "report\x1bmanager",
        "..",
        "___",
        " report::manager::risk_report",
        "report::manager::risk_report ",
        None,
        42,
        b"report::manager::risk_report",
    ):
        check(
            f"D2 不安全 id 被拒绝：{unsafe!r}",
            rc.is_safe_report_record_id(unsafe) is False,
        )

    # 生成的 ``::`` 形式必须被共享协议原样接受（不被 strip / 不被改写）。
    generated = rc.make_report_record_id("manager", "risk_report")["record_id"]
    eq(
        "D2 共享协议原样接受生成的 id",
        lc.normalize_record_id(generated),
        generated,
    )
    check(
        "D2 共享协议字符集确实允许 ':'",
        lc.normalize_record_id("report::manager::risk_report") == generated,
    )


# ============================================================
# E. 静态守卫
# ============================================================

#: 本模块绝对不允许出现的导入根名字。
_FORBIDDEN_IMPORT_ROOTS = {
    "streamlit",
    "app",
    "docx",
    "openai",
    "anthropic",
    "requests",
    "urllib",
    "http",
    "socket",
    "subprocess",
    "pickle",
    "shutil",
    "sqlite3",
    "agents",
    "i18n",
}

#: 本模块绝对不允许出现的名字 / 属性名。
_FORBIDDEN_NAMES = {
    "st",
    "session_state",
    "Document",
    "OpenAI",
    "RiskAgent",
    "persist",
    "save_db",
    "save_data",
}

_FORBIDDEN_ATTRS = {
    "session_state",
    "persist",
    "save_db",
    "write_text",
    "write_bytes",
    "chat",
    "completions",
    "responses",
    "add_paragraph",
    "add_heading",
    "add_picture",
    "save",
    "dump",
}


def _module_tree():
    return ast.parse(MODULE_PATH.read_text(encoding="utf-8"))


def test_module_purity_ast():
    print("\n--- E1. AST 纯度守卫：utils/report_contract.py ---")

    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = _module_tree()

    import_roots = set()
    imported_symbols = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                import_roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                import_roots.add(node.module.split(".")[0])
            for alias in node.names:
                imported_symbols.add(alias.name)

    leaked = import_roots & _FORBIDDEN_IMPORT_ROOTS
    eq("E1 未导入 streamlit / app / AI / DOCX / 网络模块", leaked, set())

    names = set()
    attrs = set()
    calls = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            attrs.add(node.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            calls.add(node.func.id)

    eq("E1 无 st / session_state / persist 等名字", names & _FORBIDDEN_NAMES, set())
    eq("E1 无 session_state / AI / DOCX 等属性", attrs & _FORBIDDEN_ATTRS, set())
    check("E1 无 app.py 依赖", "app" not in import_roots and "app" not in imported_symbols)
    check("E1 无 docx / AI 依赖", not ({"docx", "openai", "anthropic"} & import_roots))

    # 不读写任何文件（因此也不可能读写 data/projects.json）：
    # 既无 open()，也无 Path.read_text / write_text / read_bytes / write_bytes / dump。
    file_io_calls = calls & {"open"}
    file_io_attrs = attrs & {
        "read_text",
        "read_bytes",
        "write_text",
        "write_bytes",
        "dump",
    }
    check(
        "E1 无任何文件读写调用（open / read_text|read_bytes|write_text|write_bytes / dump）",
        not file_io_calls and not file_io_attrs,
        sorted(file_io_calls | file_io_attrs),
    )

    # 字符串常量（忽略模块 docstring：它合法地"提到"这些名字）里不得出现落盘目标。
    docstring_node = None

    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        docstring_node = tree.body[0].value

    string_constants = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node is not docstring_node
    ]
    check(
        "E1 字符串常量不含 projects.json",
        all("projects.json" not in value for value in string_constants),
    )
    check(
        "E1 源码不含 json.dump / 落盘调用",
        "json.dump" not in source and "persist(" not in source,
    )

    # 允许的依赖（负面断言，防止把模块写成框架）。
    allowed_roots = {"__future__", "typing", "utils"}
    check(
        "E1 只依赖 stdlib 与 utils.*",
        import_roots <= allowed_roots,
        sorted(import_roots - allowed_roots),
    )


def test_version_constants():
    print("\n--- E2. 版本常量 ---")

    eq(
        "E2 契约版本",
        rc.report_contract_version(),
        {"schema_version": 1, "template_version": 1},
    )
    eq("E2 schema 版本常量", rc.REPORT_SCHEMA_VERSION, 1)
    eq("E2 模板版本常量", rc.REPORT_TEMPLATE_VERSION, 1)


def test_shared_schema_difference():
    print("\n--- E3. 与共享 localized_content risk_report schema 的差异（已记录）---")

    eq("E3 content_type 复用共享常量", rc.REPORT_CONTENT_TYPE, "risk_report")
    eq("E3 content_type 即共享契约值", rc.REPORT_CONTENT_TYPE, lc.CONTENT_TYPE_REPORT)

    shared_fields = set(lc.CONTENT_TYPE_CONTRACTS[lc.CONTENT_TYPE_REPORT]["text_fields"])
    eq("E3 共享 schema 当前声明 title/summary/body", shared_fields, {"title", "summary", "body"})
    eq(
        "E3 目标 payload 多出 opening/key_points/actions/closing",
        set(rc.REPORT_TEXT_FIELDS) - shared_fields,
        {"opening", "key_points", "actions", "closing"},
    )
    check(
        "E3 共享 schema 仍标记为单例（record_id 决策延后到 4C-3）",
        lc.CONTENT_TYPE_CONTRACTS[lc.CONTENT_TYPE_REPORT]["singleton"] is True,
    )

    # 目标 payload 结构本身必须能通过"可翻译"基础校验（不含任何数值字段）。
    result = rc.validate_report_text_payload(_valid_payload())
    check("E3 目标 payload 通过本契约", result["valid"], result)
    eq(
        "E3 目标 payload 通过共享的可翻译校验",
        lc.validate_translatable_payload(lc.CONTENT_TYPE_REPORT, result["payload"]),
        [],
    )

    # 而把数值字段塞进去时，两边都必须拒绝。
    bad = _valid_payload()
    bad["risk_level"] = "HIGH"
    eq("E3 共享校验拒绝数值字段", len(lc.validate_translatable_payload(lc.CONTENT_TYPE_REPORT, bad)) > 0, True)
    check("E3 本契约也拒绝数值字段", rc.validate_report_text_payload(bad)["valid"] is False)


# ============================================================
# E4.（4C-2.1 已删除：文件哈希冻结）
# ============================================================

# 4C-2 曾在此处用固定 SHA-256 冻结 app.py / agents/risk_agent.py /
# vision_risk.py / i18n.py / utils/localized_content.py / locales/*.json /
# pages/*.py / data/projects.json，并断言这些文件"本阶段未接线"。
#
# 已删除，且**不替换为新的硬编码哈希**。原因：
#   * 4C-3 需要改共享协议、4C-5 需要接线 app.py（合法修改）；
#   * 旧代码归档阶段可能合法删除 pages/*；
#   * 多人协作时其他成员的合法修改会导致无关测试失败；
#   * 文件变化由 Git 追踪，单元测试不应复制 Git 的职责。
#
# "本阶段未修改某个文件" 改用**一次性命令**证明（不写进永久测试）：
#     git status --short && git diff --stat
#     shasum -a 256 data/projects.json    # 执行前后各一次，仅在汇报中比较
#
# 永久测试保留的守卫：A–D（locale / audience / type / payload / record_id 行为）
# 与 E1（utils/report_contract.py 的 AST 纯度）、E2（版本常量）、E3（共享 schema 差异）。


# ============================================================
# F. 报告中心接线（app.py，stub Streamlit + mock AI）
# ============================================================
#
# F 段用 stub 替换 streamlit 模块后导入 app.py，直接调用报告中心函数：
# 不启动 Streamlit 服务、不联网、不调用真实 AI、不写任何文件
# （DOCX 只在内存里生成再解包）。

REPORT_UI_LOCALES = ("en", "zh-CN", "es", "pt-BR", "fr", "sw")

EXPECTED_NATIVE_NAMES = {
    "en": "English",
    "zh-CN": "简体中文",
    "es": "Español",
    "pt-BR": "Português (Brasil)",
    "fr": "Français",
    "sw": "Kiswahili",
}

EXPECTED_AI_NAMES = {
    "en": "English",
    "zh-CN": "Simplified Chinese",
    "es": "Spanish",
    "pt-BR": "Brazilian Portuguese",
    "fr": "French",
    "sw": "Swahili",
}

#: 每种报告语言的"标题 + 正文"（目标语言，用于 DOCX 解包断言）。
TARGET_TEXT = {
    "en": ("Flood Risk Alert", "Drainage is the main concern."),
    "zh-CN": ("洪水风险提示", "排水是当前主要风险。"),
    "es": ("Alerta de riesgo de inundación", "El drenaje es la principal preocupación."),
    "pt-BR": ("Alerta de risco de inundação", "A drenagem é a principal preocupação."),
    "fr": ("Alerte risque d'inondation", "Le drainage est la préoccupation principale."),
    "sw": ("Tahadhari ya hatari ya mafuriko", "Mifereji ya maji ndiyo wasiwasi mkuu."),
}

PROJECT_A = {
    "country": "Kenya",
    "city": "Nairobi",
    "baseline_risk": {"level": "LOW", "score": 37},
    "daily_logs": [],
}

CURRENT_RISK = {"level": "MEDIUM", "score": 58, "reasons": []}

#: 默认 mock AI 返回；F8 会临时替换它以模拟"AI 试图改写风险数值"。
MOCK_AI_PAYLOAD = {
    "title": "RiskPilot Risk Report",
    "opening": "MEDIUM RISK",
    "risk_summary": "Summary",
    "key_points": ["Point"],
    "actions": ["Action"],
    "closing": "Closing",
}

#: 每次 mock AI 调用的 prompt。
AI_CALLS = []

_APP = None
_FAKE_ST = None


class _FakeRiskAgent:
    """Mock AI：记录 prompt，返回固定 payload，绝不联网。"""

    def generate_report(self, prompt):
        AI_CALLS.append(prompt)
        return copy.deepcopy(MOCK_AI_PAYLOAD)


def _load_app():
    """
    Import ``app.py`` behind a stubbed Streamlit module.

    The stub replaces the whole UI surface, so nothing is rendered and no
    Streamlit server is started. ``agents.risk_agent.RiskAgent`` is
    replaced by the mock above, so no AI is ever called.
    """
    global _APP, _FAKE_ST

    if _APP is not None:
        return _APP

    import sys
    import types
    from unittest import mock

    fake_streamlit = mock.MagicMock()
    sys.modules["streamlit"] = fake_streamlit
    sys.modules["streamlit.components"] = mock.MagicMock()
    sys.modules["streamlit.components.v1"] = mock.MagicMock()

    fake_agent_module = types.ModuleType("agents.risk_agent")
    fake_agent_module.RiskAgent = _FakeRiskAgent
    sys.modules["agents.risk_agent"] = fake_agent_module

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    import app as app_module

    _APP = app_module
    _FAKE_ST = fake_streamlit

    return _APP


def _render(app, state, audience="Worker", report_type="Today's Risk Alert",
            report_locale="en", click=False, project_id="A"):
    """
    Render the report center once with a fake session and fake widgets.

    Returns the stubbed ``st.download_button`` so tests can tell whether a
    download was offered.
    """
    from unittest import mock

    _FAKE_ST.session_state = state
    _FAKE_ST.columns.return_value = [mock.MagicMock(), mock.MagicMock()]
    _FAKE_ST.selectbox.side_effect = [audience, report_type, report_locale]
    _FAKE_ST.button.return_value = click
    _FAKE_ST.text_area.return_value = "EDITED BODY"
    _FAKE_ST.download_button.reset_mock()

    app.render_risk_report_center(
        project=PROJECT_A,
        current=CURRENT_RISK,
        weather=None,
        project_id=project_id,
    )

    return _FAKE_ST.download_button


def _docx_text(word_bytes):
    """Unzip a DOCX in memory and return its visible text."""
    import io
    import re
    import zipfile

    with zipfile.ZipFile(io.BytesIO(word_bytes)) as archive:
        xml = archive.read("word/document.xml").decode("utf-8")

    return re.sub(r"<[^>]+>", "", xml)


def test_report_center_language_options():
    app = _load_app()

    print("\n--- F1. 报告语言选项（六种对齐 locale + 母语名称）---")

    eq("F1 报告语言为六种对齐 locale", tuple(app.risk_report_language_options()), REPORT_UI_LOCALES)

    for code in REPORT_UI_LOCALES:
        eq(
            f"F1 {code} 显示母语名称",
            app._report_language_display(code),
            EXPECTED_NATIVE_NAMES[code],
        )

    for code in REPORT_UI_LOCALES:
        eq(
            f"F1 {code} 对应 registry 的 AI language name",
            rc.report_locale_ai_name(code),
            EXPECTED_AI_NAMES[code],
        )


def test_report_center_prompt_language():
    app = _load_app()

    print("\n--- F2. 六种报告语言分别传入正确 target language ---")

    for code in REPORT_UI_LOCALES:
        AI_CALLS.clear()

        report = app.ai_generate_risk_report(
            project=PROJECT_A,
            current=CURRENT_RISK,
            weather=None,
            audience="Worker",
            report_type="Today's Risk Alert",
            language=code,
            project_id="A",
        )

        eq(f"F2 {code} 只调用 1 次 AI", len(AI_CALLS), 1)
        check(f"F2 {code} 生成成功", report["success"] is True, report.get("error"))

        prompt = AI_CALLS[0]

        check(
            f"F2 {code} prompt 要求用 AI language name 撰写",
            f"The report MUST be written in:\n{EXPECTED_AI_NAMES[code]}" in prompt,
        )
        check(
            f"F2 {code} prompt payload 使用 AI language name",
            f'"language": "{EXPECTED_AI_NAMES[code]}"' in prompt,
        )
        check(
            f"F2 {code} prompt 未把 locale code 当语言名",
            f'"language": "{code}"' not in prompt,
        )
        check(
            f"F2 {code} prompt 固定内部风险等级 token",
            "LOW, MEDIUM and HIGH are internal RiskPilot risk level values." in prompt,
        )

        eq(f"F2 {code} 结果绑定 report locale", report["identity"]["report_locale"], code)
        eq(
            f"F2 {code} 结果绑定 project/audience/type",
            (
                report["identity"]["project_id"],
                report["identity"]["audience"],
                report["identity"]["report_type"],
            ),
            ("A", "worker", "daily_risk_alert"),
        )
        eq(
            f"F2 {code} 结果 identity 只有四个字段",
            set(report["identity"]),
            {"project_id", "report_locale", "audience", "report_type"},
        )

        AI_CALLS.clear()


def test_report_center_identity_binding():
    app = _load_app()

    print("\n--- F3. 报告身份绑定（project / locale / audience / type）---")

    base = app.risk_report_identity("A", "en", "Worker", "Today's Risk Alert")

    eq(
        "F3 身份包含四个字段",
        set(base),
        {"project_id", "report_locale", "audience", "report_type"},
    )
    eq("F3 身份使用稳定内部 ID", (
        base["project_id"], base["report_locale"],
        base["audience"], base["report_type"],
    ), ("A", "en", "worker", "daily_risk_alert"))

    variants = (
        ("项目", "project_id", app.risk_report_identity("B", "en", "Worker", "Today's Risk Alert")),
        ("报告语言", "report_locale", app.risk_report_identity("A", "es", "Worker", "Today's Risk Alert")),
        ("受众", "audience", app.risk_report_identity("A", "en", "Manager", "Today's Risk Alert")),
        ("报告类型", "report_type", app.risk_report_identity("A", "en", "Worker", "Risk Report")),
    )

    for label, field, other in variants:
        check(f"F3 切换{label}后身份必须不同", other != base)
        eq(
            f"F3 切换{label}只改变对应字段",
            {key for key in base if base[key] != other[key]},
            {field},
        )

    for other_locale in ("es", "fr", "pt-BR", "sw"):
        check(
            f"F3 en 身份不等于 {other_locale} 身份",
            app.risk_report_identity("A", "en", "Worker", "Today's Risk Alert")
            != app.risk_report_identity("A", other_locale, "Worker", "Today's Risk Alert"),
        )

    eq(
        "F3 旧显示值归一为同一身份",
        app.risk_report_identity("A", "中文", "worker", "Risk Report"),
        app.risk_report_identity("A", "zh-CN", "Worker", "Risk Report"),
    )


def test_report_center_ai_only_on_click():
    app = _load_app()

    print("\n--- F4. 只有点击生成才调用 AI（打开/切换选项不调用）---")

    state = {}
    AI_CALLS.clear()

    opened = _render(app, state, report_locale="sw")
    switched_language = _render(app, state, report_locale="en")
    switched_audience = _render(app, state, audience="Manager")
    switched_type = _render(app, state, report_type="Risk Report")

    eq("F4 打开页面/切换选项不调用 AI", len(AI_CALLS), 0)
    eq(
        "F4 无报告时不显示下载按钮",
        opened.call_count + switched_language.call_count
        + switched_audience.call_count + switched_type.call_count,
        0,
    )

    clicked = _render(app, state, report_type="Risk Report", click=True)

    eq("F4 点击生成调用 1 次 AI", len(AI_CALLS), 1)
    eq("F4 生成后显示下载按钮", clicked.call_count, 1)

    _render(app, state, report_type="Risk Report")

    eq("F4 同一选择重跑不再次调用 AI", len(AI_CALLS), 1)


def test_report_center_language_widget_wiring():
    app = _load_app()

    print("\n--- F10. 报告语言控件接线（选项为 locale code，显示母语名称）---")

    state = {}
    _FAKE_ST.selectbox.reset_mock()
    _render(app, state, report_locale="sw")

    selectboxes = _FAKE_ST.selectbox.call_args_list

    eq("F10 报告中心有受众/类型/语言三个下拉", len(selectboxes), 3)

    language_widget = selectboxes[2]

    eq(
        "F10 报告语言选项即六种对齐 locale",
        list(language_widget.args[1]),
        list(REPORT_UI_LOCALES),
    )
    eq(
        "F10 报告语言使用独立 session key",
        language_widget.kwargs.get("key"),
        "risk_report_language",
    )
    check(
        "F10 报告语言显示母语名称",
        language_widget.kwargs.get("format_func") is app._report_language_display,
    )
    check(
        "F10 报告语言默认值与界面语言无关",
        "ui_lang" not in language_widget.kwargs,
    )

    eq(
        "F10 默认语言由项目国家推荐（Kenya -> sw）",
        language_widget.kwargs.get("index"),
        list(REPORT_UI_LOCALES).index("sw"),
    )


def test_report_center_stale_report_not_downloadable():
    app = _load_app()

    print("\n--- F5. 身份不匹配时旧报告不得继续下载 ---")

    cases = [
        ("项目", {"project_id": "B"}),
        ("报告语言 es", {"report_locale": "es"}),
        ("报告语言 fr", {"report_locale": "fr"}),
        ("报告语言 pt-BR", {"report_locale": "pt-BR"}),
        ("报告语言 sw", {"report_locale": "sw"}),
        ("受众", {"audience": "Manager"}),
        ("报告类型", {"report_type": "Risk Report"}),
    ]

    for label, change in cases:
        state = {"ui_lang": "zh-CN"}
        AI_CALLS.clear()

        selection = {
            "audience": "Worker",
            "report_type": "Today's Risk Alert",
            "report_locale": "en",
        }

        _render(app, state, click=True, **selection)

        eq(f"F5 切换{label}前已生成报告", len(AI_CALLS), 1)
        check(
            f"F5 切换{label}前下载可用",
            state.get("risk_report_identity") is not None,
        )

        download = _render(app, state, **{**selection, **change})

        check(
            f"F5 切换{label}后旧报告被清除",
            "risk_report_result" not in state
            and "risk_report_identity" not in state
            and "risk_report_editor" not in state,
        )
        eq(f"F5 切换{label}后不再提供下载", download.call_count, 0)
        eq(f"F5 切换{label}后不调用 AI（要求重新生成）", len(AI_CALLS), 1)
        eq(f"F5 切换{label}不改变 ui_lang", state["ui_lang"], "zh-CN")


def test_report_center_language_never_writes_ui_lang():
    app = _load_app()

    print("\n--- F6. 报告语言切换不改变界面语言 ---")

    state = {"ui_lang": "zh-CN"}

    for code in REPORT_UI_LOCALES:
        AI_CALLS.clear()
        _render(app, state, report_locale=code, click=True)

        eq(f"F6 生成 {code} 报告后 ui_lang 不变", state.get("ui_lang"), "zh-CN")
        eq(f"F6 生成 {code} 报告使用该 locale", state.get("risk_report_language_used"), code)


def test_report_center_unsupported_selection():
    app = _load_app()

    print("\n--- F7. 不支持的语言/受众/类型不得调用 AI ---")

    # 界面只提供六种对齐 locale（见 F1）；契约允许 planned locale，
    # 因此这里只验证未注册的选择（含旧显示值中的未注册语言）。
    for bad_locale in ("it", "Local Language", "", None):
        AI_CALLS.clear()

        report = app.ai_generate_risk_report(
            project=PROJECT_A,
            current=CURRENT_RISK,
            weather=None,
            audience="Worker",
            report_type="Today's Risk Alert",
            language=bad_locale,
            project_id="A",
        )

        check(f"F7 语言 {bad_locale!r} 不生成报告", report["success"] is False)
        eq(f"F7 语言 {bad_locale!r} 不调用 AI", len(AI_CALLS), 0)
        eq(
            f"F7 语言 {bad_locale!r} 风险等级仍取规范值",
            report["risk_level"],
            "MEDIUM",
        )

    for bad_audience, bad_type in (("Vendor", "Today's Risk Alert"), ("Worker", "Memo")):
        AI_CALLS.clear()

        report = app.ai_generate_risk_report(
            project=PROJECT_A,
            current=CURRENT_RISK,
            weather=None,
            audience=bad_audience,
            report_type=bad_type,
            language="en",
            project_id="A",
        )

        check(
            f"F7 受众/类型 {bad_audience}/{bad_type} 不生成报告",
            report["success"] is False,
        )
        eq(f"F7 受众/类型 {bad_audience}/{bad_type} 不调用 AI", len(AI_CALLS), 0)


def test_report_center_canonical_risk_not_rewritten():
    app = _load_app()
    global MOCK_AI_PAYLOAD

    print("\n--- F8. 内部风险数值与 LOW/MEDIUM/HIGH 不被 AI 改写 ---")

    original = MOCK_AI_PAYLOAD
    AI_CALLS.clear()

    try:
        MOCK_AI_PAYLOAD = {
            "title": "RiskPilot Risk Report",
            "risk_level": "HIGH",
            "score": 999,
            "opening": "HIGH RISK",
            "risk_summary": "The score is 999/100 and the level is HIGH.",
            "key_points": ["HIGH"],
            "actions": ["Do something"],
            "closing": "Closing",
        }

        for level, score in (("LOW", 12), ("MEDIUM", 58), ("HIGH", 88)):
            current = {"level": level, "score": score, "reasons": []}

            report = app.ai_generate_risk_report(
                project=PROJECT_A,
                current=current,
                weather=None,
                audience="Worker",
                report_type="Today's Risk Alert",
                language="en",
                project_id="A",
            )

            eq(f"F8 {level} 等级以规范值为准", report["risk_level"], level)

            word_bytes = app.export_report_to_word(
                report_text=app.risk_report_to_text(report, "en"),
                project=PROJECT_A,
                current=current,
                audience="Worker",
                report_type="Today's Risk Alert",
                language=app._report_language_display("en"),
                report_locale="en",
            )

            document = _docx_text(word_bytes)

            import re

            match = re.search(
                r"Current Risk:\s*\S+\s*(HIGH|MEDIUM|LOW)\s*·\s*(\d+)/100",
                document,
            )

            check(
                f"F8 {level} DOCX 规范风险行正确",
                match is not None
                and (match.group(1), match.group(2)) == (level, str(score)),
                match.group(0) if match else document[-120:],
            )
            check(
                f"F8 {level} DOCX 不存在 AI 改写的风险行",
                re.search(r"(HIGH|MEDIUM|LOW)\s*·\s*999/100", document) is None,
            )
    finally:
        MOCK_AI_PAYLOAD = original
        AI_CALLS.clear()


def test_report_center_docx_target_language():
    app = _load_app()

    print("\n--- F9. DOCX 标题与正文使用目标语言（解包验证）---")

    for code in REPORT_UI_LOCALES:
        title, body = TARGET_TEXT[code]

        word_bytes = app.export_report_to_word(
            report_text=f"{title}\n\n{body}",
            project=PROJECT_A,
            current=CURRENT_RISK,
            audience="Worker",
            report_type="Today's Risk Alert",
            language=app._report_language_display(code),
            report_locale=code,
        )

        document = _docx_text(word_bytes)

        check(f"F9 {code} DOCX 标题使用目标语言", title in document)
        check(f"F9 {code} DOCX 正文使用目标语言", body in document)
        check(
            f"F9 {code} DOCX 标注母语名称",
            EXPECTED_NATIVE_NAMES[code] in document,
        )

        level_label = app.report_risk_level_label(CURRENT_RISK["level"], code)
        canonical_line = f'{CURRENT_RISK["level"]} · {CURRENT_RISK["score"]}/100'

        check(
            f"F9 {code} DOCX 风险行使用目标语言等级",
            f'{level_label} · {CURRENT_RISK["score"]}/100' in document,
            level_label,
        )

        if code == "en":
            check(
                "F9 en DOCX 使用规范风险值",
                canonical_line in document,
            )
        else:
            check(
                f"F9 {code} DOCX 不显示内部英文等级",
                canonical_line not in document,
                document[-160:],
            )


#: 报告里必须按目标语言显示的固定标签（key 名 -> 用途）。
DOCX_LABEL_KEYS = (
    "title",
    "project",
    "project_type",
    "audience",
    "report_type",
    "language",
    "current_risk",
    "generated",
)

DOCX_SECTION_KEYS = (
    "risk_summary",
    "key_points",
    "actions",
)


def _locale_report(code):
    """读取某一报告语言的 report / enum.project_type 文案。"""

    import json

    data = json.loads(
        (ROOT / "locales" / f"{code}.json").read_text(encoding="utf-8")
    )

    return data["report"], data["enum"]["project_type"]


def test_report_center_docx_labels_localized():
    """F11. DOCX 固定标签与章节名按报告语言显示。"""
    app = _load_app()

    print("\n--- F11. DOCX 固定标签六语言本地化（解包验证）---")

    english, _ = _locale_report("en")

    check(
        "F11 label_value_separator：半角冒号补空格 / 全角冒号不补",
        app.label_value_separator("Projet :") == " "
        and app.label_value_separator("Hadhira:") == " "
        and app.label_value_separator("项目类型：") == ""
        and app.label_value_separator("Label ") == ""
        and app.label_value_separator("") == ""
        and app.label_value_separator(None) == "",
    )

    for code in REPORT_UI_LOCALES:
        labels, _ = _locale_report(code)

        title, body = TARGET_TEXT[code]

        report_text = "\n\n".join([
            title,
            labels["section"]["risk_summary"],
            body,
            labels["section"]["key_points"],
            f"• {body}",
            labels["section"]["actions"],
            f"• {body}",
        ])

        document = _docx_text(
            app.export_report_to_word(
                report_text=report_text,
                project=PROJECT_A,
                current=CURRENT_RISK,
                audience="Worker",
                report_type="Today's Risk Alert",
                language=app._report_language_display(code),
                report_locale=code,
            )
        )

        for name in DOCX_LABEL_KEYS:
            check(
                f"F11 {code} DOCX 标签本地化：report.docx.{name}",
                labels["docx"][name] in document,
                labels["docx"][name],
            )

        for name in DOCX_SECTION_KEYS:
            check(
                f"F11 {code} DOCX 章节名本地化：report.section.{name}",
                labels["section"][name] in document,
                labels["section"][name],
            )

        level_label = app.report_risk_level_label(CURRENT_RISK["level"], code)
        canonical_line = f'{CURRENT_RISK["level"]} · {CURRENT_RISK["score"]}/100'
        localized_line = f'{level_label} · {CURRENT_RISK["score"]}/100'

        check(
            f"F11 {code} DOCX 风险行按目标语言显示",
            localized_line in document,
            localized_line,
        )

        if code == "en":
            check(f"F11 {code} DOCX 保留规范风险值", canonical_line in document)
        else:
            check(
                f"F11 {code} DOCX 不显示内部英文等级",
                canonical_line not in document,
                document[-160:],
            )

        # ------------------------------------------------
        # 标签 + 值：空格必须符合各语言排版
        # ------------------------------------------------

        spaced_project = {
            "name": "Mzuri Estate",
            "type": "Housing Construction",
            "country": "Kenya",
        }

        spaced = _docx_text(
            app.export_report_to_word(
                report_text="Body",
                project=spaced_project,
                current=CURRENT_RISK,
                audience="Worker",
                report_type="Today's Risk Alert",
                language=app._report_language_display(code),
                report_locale=code,
            )
        )

        expected_pairs = (
            (labels["docx"]["project"], "Mzuri Estate"),
            (
                labels["docx"]["project_type"],
                app.project_type_label("Housing Construction", code),
            ),
            (
                labels["docx"]["audience"],
                app.report_audience_label("Worker", code),
            ),
            (
                labels["docx"]["report_type"],
                app.report_type_label("Today's Risk Alert", code),
            ),
            (labels["docx"]["language"], EXPECTED_NATIVE_NAMES[code]),
            (
                labels["docx"]["current_risk"],
                app.risk_report_line(CURRENT_RISK, code),
            ),
        )

        for label, value in expected_pairs:
            expected = f"{label}{app.label_value_separator(label)}{value}"

            check(
                f"F11 {code} 标签与值空格正确：{expected!r}",
                expected in spaced,
                spaced[-160:],
            )

        if code == "fr":
            check(
                "F11 fr 示例：Projet : Mzuri Estate",
                "Projet : Mzuri Estate" in spaced,
            )
            check(
                "F11 fr 示例：Type de projet : Construction de logements",
                "Type de projet : Construction de logements" in spaced,
            )
        elif code == "sw":
            check(
                "F11 sw 示例：Hadhira: Mfanyakazi",
                "Hadhira: Mfanyakazi" in spaced,
            )
        elif code == "zh-CN":
            check(
                "F11 zh-CN 示例：项目类型：住房建造",
                "项目类型：住房建造" in spaced,
            )

        # 语言字段显示"实际报告语言"的母语名称，与调用方传入的 label 无关
        mismatched = _docx_text(
            app.export_report_to_word(
                report_text="Body",
                project=spaced_project,
                current=CURRENT_RISK,
                audience="Worker",
                report_type="Today's Risk Alert",
                language="English",
                report_locale=code,
            )
        )

        language_label = labels["docx"]["language"]

        check(
            f"F11 {code} 语言字段显示实际报告语言母语名",
            f"{language_label}{app.label_value_separator(language_label)}"
            f"{EXPECTED_NATIVE_NAMES[code]}" in mismatched,
            mismatched[-160:],
        )

        if code == "en":
            continue

        english_labels = [
            english["docx"][name] for name in DOCX_LABEL_KEYS
        ]
        english_sections = [
            english["section"][name] for name in DOCX_SECTION_KEYS
        ]

        check(
            f"F11 {code} DOCX 未混入英文元数据标签",
            [text for text in english_labels if text in document] == [],
            repr([text for text in english_labels if text in document]),
        )
        check(
            f"F11 {code} DOCX 未混入英文章节名",
            [text for text in english_sections if text in document] == [],
            repr([text for text in english_sections if text in document]),
        )


def test_report_center_project_type_localized():
    """F12. 系统项目类型按报告语言显示，未知值原样回退。"""
    app = _load_app()

    print("\n--- F12. 系统项目类型（含 Housing Construction）---")

    expected_keys = {
        "Housing Construction": "housing_construction",
        "Housing": "housing",
        "Residential Construction": "residential",
        "Residential": "residential",
        "Infrastructure": "infrastructure",
        "Construction": "construction",
    }

    for code in REPORT_UI_LOCALES:
        _, project_types = _locale_report(code)

        for raw, key in expected_keys.items():
            check(
                f"F12 {code} 项目类型 {raw} -> enum.project_type.{key}",
                app.project_type_label(raw, code) == project_types[key],
                app.project_type_label(raw, code),
            )

        check(
            f"F12 {code} 未知项目类型原样回退",
            app.project_type_label("Underwater Dome", code) == "Underwater Dome",
            app.project_type_label("Underwater Dome", code),
        )
        eq(
            f"F12 {code} 空值不显示 key",
            app.project_type_label(None, code),
            "",
        )

        document = _docx_text(
            app.export_report_to_word(
                report_text="Body",
                project={
                    "name": "Mzuri Estate",
                    "type": "Housing Construction",
                    "country": "Kenya",
                },
                current=CURRENT_RISK,
                audience="Worker",
                report_type="Risk Report",
                language=app._report_language_display(code),
                report_locale=code,
            )
        )

        check(
            f"F12 {code} DOCX 项目类型使用本地化名称",
            project_types["housing_construction"] in document,
            project_types["housing_construction"],
        )
        check(
            f"F12 {code} DOCX 项目名保持原文",
            "Mzuri Estate" in document,
        )


def test_report_center_user_data_untouched():
    """F13. 用户数据不翻译、不写回。"""
    app = _load_app()

    print("\n--- F13. 用户输入保持原文（项目名 / 日志正文）---")

    project = copy.deepcopy(PROJECT_A)
    project.update({
        "name": "Mzuri Estate",
        "type": "Housing Construction",
        "daily_logs": [
            {
                "date": "2026-09-10",
                "title": "Crane inspection",
                "description": "Crane inspection done on the north tower.",
                "severity": "MEDIUM",
            },
        ],
    })

    before = copy.deepcopy(project)
    AI_CALLS.clear()

    try:
        app.ai_generate_risk_report(
            project=project,
            current=CURRENT_RISK,
            weather=None,
            audience="Worker",
            report_type="Today's Risk Alert",
            language="fr",
            project_id="A",
        )

        eq("F13 只调用 1 次 AI", len(AI_CALLS), 1)
        eq("F13 生成报告不改写项目数据", project, before)

        prompt = AI_CALLS[0]

        check("F13 prompt 保留项目名原文", "Mzuri Estate" in prompt)
        check("F13 prompt 保留系统项目类型原始值", "Housing Construction" in prompt)
        check(
            "F13 prompt 保留日志正文原文",
            "Crane inspection done on the north tower." in prompt,
        )
    finally:
        AI_CALLS.clear()


# ============================================================
# 主入口
# ============================================================


def main():
    print("=" * 55)
    print("RiskPilot 报告契约测试（阶段 4C-2 + 4C-5）")
    print("=" * 55)

    test_report_locale_supported()
    test_report_locale_display_values()
    test_report_locale_rejected()
    test_report_locale_purity()
    test_audience_normalization()
    test_report_type_normalization()
    test_payload_valid()
    test_payload_key_set()
    test_payload_forbidden_fields()
    test_payload_value_types()
    test_payload_lists()
    test_record_id()
    test_record_id_rejected()
    test_module_purity_ast()
    test_version_constants()
    test_shared_schema_difference()

    # F 段：报告中心接线（app.py，stub Streamlit + mock AI）
    test_report_center_language_options()
    test_report_center_prompt_language()
    test_report_center_identity_binding()
    test_report_center_ai_only_on_click()
    test_report_center_language_widget_wiring()
    test_report_center_stale_report_not_downloadable()
    test_report_center_language_never_writes_ui_lang()
    test_report_center_unsupported_selection()
    test_report_center_canonical_risk_not_rewritten()
    test_report_center_docx_target_language()
    test_report_center_docx_labels_localized()
    test_report_center_project_type_localized()
    test_report_center_user_data_untouched()

    total = _PASSED + len(_FAILED)

    print("\n" + "=" * 55)
    print(f"结果：{_PASSED}/{total} 通过")

    if _FAILED:
        print("失败项：")
        for name in _FAILED:
            print(f"  - {name}")
        raise SystemExit(1)

    print("报告契约测试全部通过 ✅")


if __name__ == "__main__":
    main()
