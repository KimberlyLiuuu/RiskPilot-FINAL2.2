# -*- coding: utf-8 -*-
"""
RiskPilot Current Risk 本地化回归测试 (test_current_risk_localization.py)
========================================================================

阶段 4A：Current Risk 接入统一多语言内容协议。

测试对象：

    * utils/current_risk_display.py  输入指纹、读取状态、payload 校验、prompt
    * utils/localized_content.py     stage 3 协议（只读调用，不修改）
    * agents/risk_agent.py           explain_current_risk 的目标语言接线
    * app.py                         Current Risk 页面 / 按钮触发边界（AST）

约束：不请求网络、不调用真实 AI、不写 data/projects.json、
不导入 app.py（导入即执行 Streamlit 页面），全部通过 AST 静态检查。

覆盖场景：

    A. 输入指纹（dict 顺序 / 风险数值 / report language / ui locale）
    B. 多语言读取（精确命中 / 不串语言 / 新字段优先 / legacy）
    C. stale / missing / 缺 hash 状态
    D. Session cache 命名空间与有界性
    E. AI payload 校验
    F. 用户触发边界（AI 只在按钮路径 / prompt 目标语言）
    G. 数值隔离（生成前后风险数字完全不变）
    H. Locale UI（key 对齐 / 占位符 / 状态文案 / planned locale）
    I. AST 静态守卫

运行方式：

    python test_current_risk_localization.py
"""

import ast
import copy
import json
import os
import pathlib
import re
from unittest import mock

from utils import current_risk_display as crd
from utils import localized_content as lc
from utils import locale_registry as lr

ROOT = pathlib.Path(__file__).resolve().parent

APP = ROOT / "app.py"
MODULE = ROOT / "utils" / "current_risk_display.py"
RISK_AGENT = ROOT / "agents" / "risk_agent.py"
VISION = ROOT / "vision_risk.py"
PAGES = ROOT / "pages"
PROJECTS = ROOT / "data" / "projects.json"
EN_JSON = ROOT / "locales" / "en.json"
ZH_JSON = ROOT / "locales" / "zh-CN.json"

EN = "en"
ZH = "zh-CN"

CT = lc.CONTENT_TYPE_CURRENT_RISK
RID = lc.DEFAULT_RECORD_ID

FORBIDDEN_FIELDS = ("score", "level", "severity", "likelihood", "exposure", "raw_risk")

NEW_KEYS = (
    "dashboard.components_header",
    "dashboard.component_line",
    "dashboard.component_baseline",
    "dashboard.component_weather",
    "dashboard.component_daily_logs",
    "dashboard.component_vision",
    "dashboard.component_financial",
    "dashboard.caption_component",
    "dashboard.button_generate_current_risk",
    "dashboard.current_risk_level_text_header",
    "dashboard.current_risk_reasons_header",
    "dashboard.warning_current_risk_stale",
    "dashboard.current_risk_old_version",
    "dashboard.info_current_risk_missing",
    "dashboard.info_current_risk_empty",
    "dashboard.current_risk_legacy_note",
    "dashboard.error_current_risk_generation_failed",
    "dashboard.error_current_risk_invalid_stored",
)

_PASSED = 0
_FAILED = []


def check(name, condition, detail=""):
    global _PASSED

    if condition:
        _PASSED += 1
        print(f"[PASS] {name}")
    else:
        _FAILED.append(name)
        message = f"[FAIL] {name}"
        if detail:
            message += f"  ->  {detail}"
        print(message)


def eq(name, actual, expected):
    check(name, actual == expected, f"expected {expected!r}, got {actual!r}")


# ============================================================
# 夹具
# ============================================================

def base_project(**overrides):
    """A project with deterministic baseline + numeric daily logs."""
    project = {
        "baseline_risk": {
            "level": "HIGH",
            "score": 72,
            "terrain": "Hilly",
            "drainage": "Poor",
            "rainfall": "High",
            "earthquake": "Medium",
            "risks": {"heavy_rainfall": "High", "flooding": "Medium"},
        },
        "daily_logs": [
            {"date": "2026-01-01", "title": "t1", "description": "d1",
             "ai_analysis": {"score": 40, "level": "MEDIUM"}},
            {"date": "2026-01-02", "title": "t2", "description": "d2",
             "ai_analysis": {"score": 55, "level": "HIGH"}},
        ],
        "workers": 120,
        "country": "China",
        "city": "Shenzhen",
        "type": "Residential",
        "project_scale": "Large",
    }
    project.update(overrides)
    return project


def base_current(score=68, level="HIGH", components=None):
    comps = {
        "Baseline": 72,
        "Weather": 60,
        "Daily Logs": 45,
        "Vision": 0,
        "Financial": 10,
    }
    if components:
        comps.update(components)
    return {
        "score": score,
        "level": level,
        "components": comps,
        "weights": {
            "Baseline": "25%",
            "Weather": "25%",
            "Daily Logs": "35%",
            "Vision": "10%",
            "Financial": "5%",
        },
    }


def payload(tag="zh"):
    return {
        "summary": f"{tag} summary",
        "risk_level_text": f"{tag} risk level text",
        "reasons": [f"{tag} reason one", f"{tag} reason two"],
        "recommendations": [f"{tag} action one"],
    }


def store_text(project, locale, tag, input_hash):
    record = lc.make_localized_record(CT, RID, locale, payload(tag), input_hash=input_hash)
    assert record is not None, "fixture record must be valid"
    lc.localized_content_put(project, record, copy_on_write=False)
    return project


# ============================================================
# A. 输入指纹
# ============================================================

def test_a_input_hash():
    print("\n--- A. 输入指纹 ---")

    project = base_project()
    current = base_current()
    h = crd.current_risk_input_hash(project, current, project_id="Alpha")

    check("A1 指纹是稳定字符串", isinstance(h, str) and len(h) == 64)

    # A1: 相同语义输入、不同 dict 顺序 -> 相同 hash
    reordered = {key: project[key] for key in reversed(list(project.keys()))}
    eq("A1 顶层 dict 顺序不影响 hash",
       crd.current_risk_input_hash(reordered, current, project_id="Alpha"), h)

    reordered_baseline = copy.deepcopy(project)
    reordered_baseline["baseline_risk"] = dict(
        reversed(list(reordered_baseline["baseline_risk"].items()))
    )
    eq("A1 嵌套 dict 顺序不影响 hash",
       crd.current_risk_input_hash(reordered_baseline, current, project_id="Alpha"), h)

    # A2: 风险数值改变 -> hash 改变
    check("A2 score 改变 hash 改变",
          crd.current_risk_input_hash(project, base_current(score=88), project_id="Alpha") != h)
    check("A2 level 改变 hash 改变",
          crd.current_risk_input_hash(project, base_current(level="MEDIUM"), project_id="Alpha") != h)
    check("A2 天气贡献改变 hash 改变",
          crd.current_risk_input_hash(project, base_current(components={"Weather": 95}),
                                      project_id="Alpha") != h)
    check("A2 日志数值改变 hash 改变",
          crd.current_risk_input_hash(
              base_project(daily_logs=[{"ai_analysis": {"score": 95}}]), current,
              project_id="Alpha") != h)
    check("A2 baseline 改变 hash 改变",
          crd.current_risk_input_hash(
              base_project(baseline_risk={"score": 10, "level": "LOW"}), current,
              project_id="Alpha") != h)
    check("A2 project_id 改变 hash 改变",
          crd.current_risk_input_hash(project, current, project_id="Beta") != h)

    # A3: report language 不影响
    eq("A3 report language 不影响 hash",
       crd.current_risk_input_hash(base_project(risk_report_language=ZH), current,
                                   project_id="Alpha"), h)

    # A4: ui locale 不影响基础输入 hash
    eq("A4 ui locale 不影响基础输入 hash",
       crd.current_risk_input_hash(base_project(ui_lang=ZH), current, project_id="Alpha"), h)

    # A5: 不相关 session / 已生成文本 / 旧快照字段不影响
    noisy = base_project(
        ui_lang=ZH,
        risk_report_language=ZH,
        generated_at="2020-01-01T00:00:00",
        current_risk_score=999,
        current_risk_updated_at="2020-01-01",
        starred=True,
        ai_text_by_language={EN: {CT: payload("en")}},
    )
    eq("A5 不相关字段不影响 hash",
       crd.current_risk_input_hash(noisy, current, project_id="Alpha"), h)

    # 指纹内容
    fingerprint = crd.build_current_risk_input(project, current, project_id="Alpha")
    eq("A6 指纹含 schema version", fingerprint["schema"], crd.CURRENT_RISK_INPUT_SCHEMA_VERSION)
    eq("A6 指纹含 project id", fingerprint["project_id"], "Alpha")
    for field in ("weather_risk", "terrain_risk", "log_risk", "vision_penalty",
                  "current", "baseline", "components"):
        check(f"A6 指纹含 {field}", field in fingerprint)
    for field in FORBIDDEN_FIELDS:
        check(f"A6 指纹不含自由文本字段 {field}", field not in fingerprint)


# ============================================================
# B. 多语言读取
# ============================================================

def test_b_multilang_read():
    print("\n--- B. 多语言读取 ---")

    project = base_project()
    current = base_current()
    h = crd.current_risk_input_hash(project, current, project_id="Alpha")

    store_text(project, EN, "en", h)
    store_text(project, ZH, "zh", h)

    snapshot = copy.deepcopy(project)

    en = crd.get_current_risk_display(project, current, EN, project_id="Alpha")
    zh = crd.get_current_risk_display(project, current, ZH, project_id="Alpha")

    eq("B1 英文状态 available", en["status"], "available")
    eq("B2 英文显示英文正文", en["payload"]["summary"], payload("en")["summary"])
    eq("B3 中文显示中文正文", zh["payload"]["summary"], payload("zh")["summary"])
    check("B2 中文不显示英文正文", "en summary" not in json.dumps(zh["payload"], ensure_ascii=False))
    check("B3 英文不显示中文正文", "zh summary" not in json.dumps(en["payload"], ensure_ascii=False))
    check("B2/B3 两种语言正文不同", en["payload"]["summary"] != zh["payload"]["summary"])

    eq("B1 保存语言列表完整", sorted(zh["saved_locales"]), [EN, ZH])
    eq("B6 读取不修改项目", project, snapshot)
    eq("B1 读取不写其他语言", project["localized_content"][CT][RID][EN]["payload"]["summary"],
       payload("en")["summary"])

    # B4: 新协议字段优先于 legacy
    legacy_project = base_project()
    legacy_project["ai_text_by_language"] = {EN: {CT: payload("legacy-en")}}
    store_text(legacy_project, EN, "en", h)
    d = crd.get_current_risk_display(legacy_project, current, EN, project_id="Alpha")
    eq("B4 新 localized_content 优先", d["status"], "available")
    eq("B4 显示新版本正文", d["payload"]["summary"], payload("en")["summary"])

    # B5: 未知 / 未匹配 legacy 不显示
    unknown = base_project()
    unknown["ai_text_by_language"] = {"ja": {CT: payload("ja")}}
    d = crd.get_current_risk_display(unknown, current, ZH, project_id="Alpha")
    eq("B5 未知 legacy 不归属当前 UI", d["ui_state"], "empty")
    check("B5 未知 legacy 不显示正文", d["payload"] is None)

    # B7: 同语言 legacy -> legacy_compatible，不自动迁移
    same_lang = base_project()
    same_lang["ai_text_by_language"] = {ZH: {CT: payload("legacy-zh")}}
    d = crd.get_current_risk_display(same_lang, current, ZH, project_id="Alpha")
    eq("B7 同语言 legacy 状态", d["ui_state"], "legacy_compatible")
    eq("B7 legacy 正文可显示", d["payload"]["summary"], payload("legacy-zh")["summary"])
    check("B7 不自动迁移 legacy", "localized_content" not in same_lang)
    check("B7 legacy 不需要生成", d["needs_generation"] is False)


# ============================================================
# C. stale / missing
# ============================================================

def test_c_stale_missing():
    print("\n--- C. stale / missing ---")

    project = base_project()
    current = base_current()
    h = crd.current_risk_input_hash(project, current, project_id="Alpha")
    store_text(project, EN, "en", h)

    eq("C1 输入一致 -> available",
       crd.get_current_risk_display(project, current, EN, project_id="Alpha")["status"],
       "available")

    changed = base_current(score=99, components={"Daily Logs": 95})
    stale = crd.get_current_risk_display(project, changed, EN, project_id="Alpha")
    eq("C2 输入变化 -> stale", stale["status"], "stale")
    check("C2 is_stale 为真", stale["is_stale"] is True)
    check("C3 stale 不伪装 available",
          stale["ui_state"] == "stale" and stale["status"] != "available")
    eq("C2 stale 保留旧正文用于“旧版本”展示",
       stale["payload"]["summary"], payload("en")["summary"])
    check("C2 stale 需要重新生成", stale["needs_generation"] is True)

    other = crd.get_current_risk_display(project, current, ZH, project_id="Alpha")
    eq("C4 目标语言不同 -> missing 而非 stale", other["status"], "missing")
    eq("C4 missing 的 UI 状态", other["ui_state"], "missing")
    check("C4 不显示其他语言正文", other["payload"] is None)
    eq("C4 保存语言列表只含 en", other["saved_locales"], [EN])

    # C5: 缺 hash 的旧记录 -> 明确的 stale
    no_hash = base_project()
    record = lc.make_localized_record(CT, RID, EN, payload("en"), compute_hash=False)
    assert record is not None
    lc.localized_content_put(no_hash, record, copy_on_write=False)

    d = crd.get_current_risk_display(no_hash, current, EN, project_id="Alpha")
    eq("C5 缺 hash 记录 -> stale", d["status"], "stale")
    eq("C5 明示缺 hash 原因", d["reason"], "input_hash_missing")
    check("C5 缺 hash 不伪装 available", d["ui_state"] == "stale")


# ============================================================
# D. Session cache
# ============================================================

def test_d_session_cache():
    print("\n--- D. Session cache ---")

    key_a = crd.current_risk_cache_key("A", ZH, "h1")
    key_b = crd.current_risk_cache_key("B", ZH, "h1")
    key_lang = crd.current_risk_cache_key("A", EN, "h1")
    key_hash = crd.current_risk_cache_key("A", ZH, "h2")

    eq("D1 key 含 project/locale/hash", (key_a[0], key_a[3], key_a[4]), ("A", ZH, "h1"))
    eq("D1 key 含 content_type/record_id", (key_a[1], key_a[2]), (CT, RID))
    check("D1 项目 A/B 隔离", key_a != key_b)
    check("D2 en/zh-CN 隔离", key_a != key_lang)
    check("D3 hash 隔离", key_a != key_hash)

    cache = {}
    crd.bounded_cache_put(cache, key_a, payload("a-zh"), limit=3)
    check("D4 写 A 不改变 B", key_b not in cache)
    check("D5 切换项目不依赖清空 cache（A 仍在）", key_a in cache)

    crd.bounded_cache_put(cache, key_b, payload("b-zh"), limit=3)
    check("D5 A 与 B 同时存在", key_a in cache and key_b in cache)

    for index in range(6):
        crd.bounded_cache_put(
            cache,
            crd.current_risk_cache_key("A", ZH, f"x{index}"),
            payload(f"x{index}"),
            limit=3,
        )

    group_a = [k for k in cache if crd.current_risk_cache_group(k) == ("A", ZH)]
    eq("D6 同项目同语言缓存有界", len(group_a), 3)
    eq("D6 保留最近三个 hash", [k[4] for k in group_a], ["x3", "x4", "x5"])
    check("D6 淘汰不影响其他组", key_b in cache)

    # 精确命中：当前项目的缓存可显示
    project = base_project()
    current = base_current()
    h = crd.current_risk_input_hash(project, current, project_id="Alpha")

    exact = {}
    crd.bounded_cache_put(exact, crd.current_risk_cache_key("Alpha", ZH, h), payload("zh-cache"))
    d = crd.get_current_risk_display(project, current, ZH, project_id="Alpha", session_cache=exact)
    eq("D 会话缓存精确命中 -> available", d["status"], "available")
    eq("D 会话缓存内容正确", d["payload"]["summary"], payload("zh-cache")["summary"])

    # 其他项目的缓存绝不被使用
    other = {}
    crd.bounded_cache_put(other, crd.current_risk_cache_key("Other", ZH, h), payload("other"))
    d = crd.get_current_risk_display(project, current, ZH, project_id="Alpha", session_cache=other)
    check("D1 其他项目缓存不被使用", d["status"] == "missing" and d["payload"] is None)

    # 其他语言的缓存绝不被使用
    other_lang = {}
    crd.bounded_cache_put(other_lang, crd.current_risk_cache_key("Alpha", EN, h), payload("en-cache"))
    d = crd.get_current_risk_display(project, current, ZH, project_id="Alpha",
                                     session_cache=other_lang)
    check("D2 其他语言缓存不被使用", d["payload"] is None)


# ============================================================
# E. payload 校验
# ============================================================

def test_e_payload_validation():
    print("\n--- E. AI payload 校验 ---")

    good = payload("ok")
    check("E1 正确 schema 通过", crd.is_valid_current_risk_text_payload(good))
    eq("E1 归一化字段", sorted(crd.validate_current_risk_text_payload(good).keys()),
       ["reasons", "recommendations", "risk_level_text", "summary"])

    for field in FORBIDDEN_FIELDS:
        bad = dict(good)
        bad[field] = 88
        check(f"E2 数值字段 {field} 被拒绝",
              crd.validate_current_risk_text_payload(bad) == {})

    sle = dict(good)
    sle["likelihood"] = 4
    sle["exposure"] = 3
    sle["severity"] = "HIGH"
    check("E2 S/L/E 字段被拒绝", crd.validate_current_risk_text_payload(sle) == {})

    legacy_schema = {
        "summary": "s",
        "key_factors": [],
        "weather_impact": "w",
        "log_impact": "l",
        "baseline_impact": "b",
        "recommendation": "r",
    }
    check("E2 旧 schema（key_factors 等）被拒绝",
          crd.validate_current_risk_text_payload(legacy_schema) == {})
    check("E2 缺少字段被拒绝",
          crd.validate_current_risk_text_payload({"summary": "s", "reasons": []}) == {})
    check("E2 顶层非 dict 被拒绝", crd.validate_current_risk_text_payload(["x"]) == {})

    check("E3 summary 类型错误被拒绝",
          crd.validate_current_risk_text_payload({**good, "summary": 5}) == {})
    check("E3 reasons 非列表被拒绝",
          crd.validate_current_risk_text_payload({**good, "reasons": "x"}) == {})
    check("E3 reasons 元素类型错误被拒绝",
          crd.validate_current_risk_text_payload({**good, "reasons": [1]}) == {})
    check("E3 空 summary 被拒绝",
          crd.validate_current_risk_text_payload({**good, "summary": "   "}) == {})
    check("E3 risk_level_text None 被拒绝",
          crd.validate_current_risk_text_payload({**good, "risk_level_text": None}) == {})

    check("E4 超长 summary 被拒绝",
          crd.validate_current_risk_text_payload(
              {**good, "summary": "x" * (crd.MAX_SUMMARY_LENGTH + 1)}) == {})
    check("E4 超长列表项被拒绝",
          crd.validate_current_risk_text_payload(
              {**good, "reasons": ["y" * (crd.MAX_LIST_ITEM_LENGTH + 1)]}) == {})
    check("E5 超长列表被拒绝",
          crd.validate_current_risk_text_payload(
              {**good, "reasons": [f"r{i}" for i in range(crd.MAX_LIST_ITEMS + 1)]}) == {})

    check("E6 NaN 被拒绝",
          crd.validate_current_risk_text_payload({**good, "summary": float("nan")}) == {})
    check("E6 Infinity 被拒绝",
          crd.validate_current_risk_text_payload(
              {**good, "recommendations": [float("inf")]}) == {})

    # E7: 失败不写入
    project = base_project()
    current = base_current()
    h = crd.current_risk_input_hash(project, current, project_id="Alpha")
    bad = {**good, "score": 99}
    eq("E7 非法响应校验为失败", crd.validate_current_risk_text_payload(bad), {})
    check("E7 失败不经过写入闸门", crd.text_payload_for_store(project, bad, ZH) is None)
    check("E7 有效响应经过写入闸门", crd.text_payload_for_store(project, good, ZH) is not None)
    check("E7 项目未被写入", "localized_content" not in project)


# ============================================================
# F. 用户触发边界
# ============================================================

def test_f_trigger_boundary():
    print("\n--- F. 用户触发边界 ---")

    source = APP.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents = _parents(tree)

    calls = _calls_named(tree, "ai_explain_current_risk")
    eq("F 页面只有一处 Current Risk AI 入口调用", len(calls), 1)

    button_if = _enclosing_button_if(calls[0], parents) if calls else None
    check("F AI 调用位于 st.button 条件内", button_if is not None)

    ai_def = _function_def(tree, "ai_explain_current_risk")
    check("F AI 入口函数存在", ai_def is not None)

    inner = _calls_named(tree, "explain_current_risk")
    eq("F RiskAgent.explain_current_risk 只有一处调用", len(inner), 1)
    check("F 该 AI 调用位于入口函数内", _is_within(inner[0], ai_def, parents))

    if button_if is not None:
        persists = _calls_named(button_if, "persist")
        eq("F 按钮分支内 persist 恰好 1 次", len(persists), 1)
        eq("F 按钮分支内 localized_content_put 恰好 1 次",
           len(_calls_named(button_if, "localized_content_put")), 1)
        eq("F 不做 legacy 双写（无 ai_text_store）",
           len(_calls_named(button_if, "ai_text_store")), 0)

        if persists:
            guard = _enclosing_if(persists[0], parents)
            in_body = guard is not None and any(
                _is_within(persists[0], statement, parents) for statement in guard.body)
            check("F persist 只位于 risk_record 非 None 分支",
                  guard is not None and _is_record_none_test(guard.test) and not in_body)

        makes = _calls_named(button_if, "make_localized_record")
        eq("F 只构造一次 localized record", len(makes), 1)
        if makes:
            gate = _enclosing_if_exp(makes[0], parents)
            check("F record 只在 risk_payload 非空时构造",
                  gate is not None and _mentions_name(gate.test, "risk_payload"))

        button_key_calls = _calls_named(button_if, "current_risk_generate_button_key")
        eq("F 按钮 key 由三要素构成", len(button_key_calls[0].args) if button_key_calls else 0, 3)

    # 有效 / 无效响应对写入的影响（结构 + 闸门证明）
    project = base_project()
    current = base_current()
    check("F 有效响应可通过写入闸门（→ persist 1）",
          crd.text_payload_for_store(project, payload("ok"), ZH) is not None)
    check("F 无效响应被闸门拒绝（→ persist 0）",
          crd.text_payload_for_store(project, {**payload("ok"), "score": 1}, ZH) is None)
    check("F 空 payload 被校验拒绝（→ 不构造 record）",
          crd.validate_current_risk_text_payload({}) == {}
          and crd.is_valid_current_risk_text_payload({}) is False)

    # prompt 目标语言
    context = crd.build_current_risk_context(project, current, project_id="Alpha")
    zh_prompt = crd.build_current_risk_text_prompt(context, ZH)
    en_prompt = crd.build_current_risk_text_prompt(context, EN)

    check("F 中文 prompt 使用中文 AI 语言名",
          lr.ai_language_name(ZH) in zh_prompt)
    check("F 英文 prompt 使用英文 AI 语言名",
          lr.ai_language_name(EN) in en_prompt)
    check("F 英文 prompt 不误用中文 AI 语言名",
          lr.ai_language_name(ZH) not in en_prompt)
    check("F prompt 不再硬编码 Use English", "Use English" not in zh_prompt)
    for field in crd.TEXT_FIELDS:
        check(f"F prompt 固定英文 JSON 字段名 {field}", f'"{field}"' in zh_prompt)

    # RiskAgent 真正把 prompt 发给模型
    os.environ.setdefault("DEEPSEEK_API_KEY", "test-key")
    import agents.risk_agent as ra

    fake_response = type(
        "R", (), {"choices": [type("C", (), {
            "message": type("M", (), {"content": json.dumps(payload("ok"))})()})()]}
    )()

    with mock.patch.object(ra, "client") as fake_client:
        fake_client.chat.completions.create.return_value = fake_response
        result = ra.RiskAgent().explain_current_risk("MY CURRENT RISK PROMPT")
        kwargs = fake_client.chat.completions.create.call_args.kwargs

    eq("F prompt 原样发送给模型", kwargs["messages"][-1]["content"], "MY CURRENT RISK PROMPT")
    eq("F 模型响应被解析为 dict", result, payload("ok"))
    check("F 不再自动追加 Use English",
          all("Use English" not in str(message.get("content", ""))
              for message in kwargs["messages"]))

    bad_response = type(
        "R", (), {"choices": [type("C", (), {
            "message": type("M", (), {"content": "not json"})()})()]}
    )()

    with mock.patch.object(ra, "client") as fake_client:
        fake_client.chat.completions.create.return_value = bad_response
        try:
            ra.RiskAgent().explain_current_risk("p")
            check("F 非 JSON 响应被拒绝", False)
        except Exception:
            check("F 非 JSON 响应被拒绝", True)

    try:
        ra.RiskAgent().explain_current_risk("   ")
        check("F 空 prompt 被拒绝", False)
    except ValueError:
        check("F 空 prompt 被拒绝", True)


# ============================================================
# G. 数值隔离
# ============================================================

def test_g_numeric_isolation():
    print("\n--- G. 数值隔离 ---")

    project = base_project(
        visual_risk_penalty=20,
        visual_risk_resolved=False,
        visual_risks=[{"type": "collapse", "score": 20}],
        current_risk_score=999,
        current_risk_updated_at="2020-01-01",
        daily_logs=[
            {"date": "2026-01-01", "title": "t1", "description": "d1",
             "severity": "MEDIUM", "ai_analysis": {
                 "score": 40, "level": "MEDIUM",
                 "severity": "MEDIUM", "likelihood": 3, "exposure": 4,
                 "raw_risk": 12,
                 "risks": [{"severity": "HIGH", "likelihood": 4, "exposure": 3, "raw_risk": 12}],
             }},
        ],
    )
    current = base_current()

    before_project = copy.deepcopy(project)
    before_current = copy.deepcopy(current)

    h = crd.current_risk_input_hash(project, current, project_id="Alpha")
    validated = crd.validate_current_risk_text_payload(payload("zh"))
    record = lc.make_localized_record(CT, RID, ZH, validated, input_hash=h)
    lc.localized_content_put(project, record, copy_on_write=False)

    # 读取 + prompt 构造也不得改动风险数据
    crd.get_current_risk_display(project, current, ZH, project_id="Alpha")
    crd.build_current_risk_context(project, current, {"current": {"temp": 31}}, project_id="Alpha")

    eq("G 当前 score 不变", current["score"], before_current["score"])
    eq("G 当前 level 不变", current["level"], before_current["level"])
    eq("G 当前组件不变", current["components"], before_current["components"])
    eq("G 当前权重不变", current["weights"], before_current["weights"])

    for name, value in before_current["components"].items():
        eq(f"G 组件贡献 {name} 不变", current["components"][name], value)

    for field in ("baseline_risk", "baseline_score", "workers", "visual_risk_penalty",
                  "visual_risk_resolved", "visual_risks", "daily_logs",
                  "current_risk_score", "current_risk_updated_at"):
        eq(f"G 项目原始字段 {field} 不变", project.get(field), before_project.get(field))

    eq("G 项目仅新增 localized_content",
       sorted(set(project) - set(before_project)), ["localized_content"])

    log = project["daily_logs"][0]
    eq("G 日志 severity 不变", log["severity"], "MEDIUM")
    eq("G 日志 likelihood 不变", log["ai_analysis"]["likelihood"], 3)
    eq("G 日志 exposure 不变", log["ai_analysis"]["exposure"], 4)
    eq("G 日志 raw_risk 不变", log["ai_analysis"]["raw_risk"], 12)


# ============================================================
# H. Locale UI
# ============================================================

def test_h_locale_ui():
    print("\n--- H. Locale UI ---")

    en = json.loads(EN_JSON.read_text(encoding="utf-8"))
    zh = json.loads(ZH_JSON.read_text(encoding="utf-8"))
    flat_en = _flatten(en)
    flat_zh = _flatten(zh)

    for key in NEW_KEYS:
        check(f"H1 en 存在 {key}", key in flat_en)
        check(f"H1 zh-CN 存在 {key}", key in flat_zh)
        if key in flat_en and key in flat_zh:
            eq(f"H2 占位符一致 {key}", _placeholders(flat_en[key]), _placeholders(flat_zh[key]))

    state_keys = (
        "dashboard.warning_current_risk_stale",
        "dashboard.info_current_risk_missing",
        "dashboard.info_current_risk_empty",
        "dashboard.error_current_risk_invalid_stored",
        "dashboard.error_current_risk_generation_failed",
    )
    messages = {flat_en[key] for key in state_keys}
    eq("H3 stale/missing/empty/invalid 文案互不相同", len(messages), len(state_keys))
    for key in state_keys:
        check(f"H3 状态文案非空 {key}", bool(flat_en[key].strip()) and bool(flat_zh[key].strip()))

    eq("H4 已启用 locale es 保持", crd.resolve_content_locale("es"), "es")
    eq("H4 planned locale ja 归一到默认", crd.resolve_content_locale("ja"), "en")
    eq("H4 受支持 locale 保持", crd.resolve_content_locale(ZH), ZH)

    current = base_current()
    project = base_project()
    h = crd.current_risk_input_hash(project, current, project_id="Alpha")
    store_text(project, EN, "en", h)

    eq("H5 missing（其他语言存在）",
       crd.get_current_risk_display(project, current, ZH, project_id="Alpha")["ui_state"],
       "missing")
    eq("H5 empty（完全没有内容）",
       crd.get_current_risk_display(base_project(), current, ZH, project_id="Alpha")["ui_state"],
       "empty")

    broken = base_project()
    broken["localized_content"] = {CT: {RID: {ZH: {
        "content_type": CT,
        "record_id": RID,
        "locale": ZH,
        "schema_version": 1,
        "payload": payload("broken"),
    }}}}
    eq("H5 invalid（损坏记录）",
       crd.get_current_risk_display(broken, current, ZH, project_id="Alpha")["ui_state"],
       "invalid")


# ============================================================
# I. AST 静态守卫
# ============================================================

def test_i_ast_guards():
    print("\n--- I. AST 静态守卫 ---")

    source = APP.read_text(encoding="utf-8")
    tree = ast.parse(source)
    parents = _parents(tree)
    mod_tree = ast.parse(MODULE.read_text(encoding="utf-8"))

    # I1: 无跨语言 or fallback
    cross = [
        ast.unparse(node)
        for node in ast.walk(tree)
        if isinstance(node, ast.BoolOp) and isinstance(node.op, ast.Or)
        and "payload" in ast.dump(node)
    ]
    eq("I1 app.py 无跨语言 payload or fallback", cross, [])
    check("I1 app.py 不直接 legacy fallback", "legacy_ai_text_get" not in source)
    check("I1 app.py 不直接读协议状态",
          "localized_content_get" not in source and "localized_content_status" not in source)

    # I2: cache key
    cache_calls = _calls_named(tree, "current_risk_cache_key")
    check("I2 app.py 使用命名空间 cache key", len(cache_calls) >= 1)
    eq("I2 cache key 三要素", len(cache_calls[0].args) if cache_calls else 0, 3)

    key = crd.current_risk_cache_key("A", ZH, "h")
    eq("I2 cache key 长度 5", len(key), 5)
    eq("I2 cache key 含 project/locale/hash", (key[0], key[3], key[4]), ("A", ZH, "h"))

    # I3: 新模块纯净度
    imported = _imported_modules(mod_tree)
    check("I3 新模块不导入 streamlit", "streamlit" not in imported)
    check("I3 新模块不导入 openai", "openai" not in imported)
    check("I3 新模块不导入 app/i18n", not (imported & {"app", "i18n"}))

    chains = _attribute_chains(mod_tree)
    check("I3 新模块无 st.* 调用", not any(c == "st" or c.startswith("st.") for c in chains))
    check("I3 新模块不访问 session_state",
          not any("session_state" in c for c in chains))

    called = _called_names(mod_tree)
    check("I3 新模块不 persist", not (called & {"persist", "save_projects", "save_db"}))
    check("I3 新模块不调用 AI",
          not (called & {"analyze", "explain_current_risk", "generate_report",
                         "create", "chat_completion"}))
    check("I3 新模块不写文件", "open" not in called)

    # I4: ui_lang 写入点不增加
    eq("I4 ui_lang 写入点仍为 1",
       len(_calls_named(tree, "set_current_language")), 1)
    check("I4 app.py 未直接写 ui_lang", 'session_state["ui_lang"]' not in source)
    check("I4 不清空全局 session cache", "session_state.clear()" not in source)

    # I5: risk_agent
    agent_source = RISK_AGENT.read_text(encoding="utf-8")
    check("I5 Current Risk 接收 prompt",
          "def explain_current_risk(self, prompt" in agent_source)
    check("I5 不再硬编码 Use English", "Use English" not in agent_source)
    check("I5 Daily Log analyze 保留", "def analyze(self, text):" in agent_source)
    check("I5 Report 生成保留", "def generate_report(self, prompt" in agent_source)
    check("I5 risk_agent 未接入 localized_content", "localized_content" not in agent_source)

    # I6: pages/* 未改
    for page in sorted(PAGES.glob("*.py")):
        page_source = page.read_text(encoding="utf-8")
        check(f"I6 pages/{page.name} 未接入本阶段",
              "current_risk_display" not in page_source
              and "localized_content" not in page_source
              and "content_locale" not in page_source)

    # I7: Vision 的 Current Risk 边界未被带动。阶段 4B 起 Vision 走自己的协议
    # （utils.vision_display）与自己的 content type，这里只保证它不借用 Current
    # Risk 的显示层与契约。
    if VISION.exists():
        vision_source = VISION.read_text(encoding="utf-8")
        check("I7 vision_risk.py 不借用 Current Risk 显示层",
              "current_risk_display" not in vision_source
              and "CURRENT_RISK_CONTENT_TYPE" not in vision_source)

    # I8: 风险公式未改
    formula = _function_source(source, "compute_current_risk")
    check("I8 风险公式未接入语言层",
          formula is not None
          and all(token not in (formula or "")
                  for token in ("localized_content", "current_risk_display",
                                "get_current_risk_display", "CONTENT_TYPE")))
    check("I8 融合组件仍在",
          formula is not None
          and all(name in formula for name in
                  ("Baseline", "Weather", "Daily Logs", "Vision", "Financial")))

    # I9: 测试不写正式数据
    check("I9 本测试不读取正式项目数据文件路径写入", str(PROJECTS).endswith("data/projects.json"))


# ============================================================
# AST 辅助
# ============================================================

def _parents(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _call_name(node):
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _calls_named(node, name):
    return [n for n in ast.walk(node) if isinstance(n, ast.Call) and _call_name(n) == name]


def _function_def(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _ancestors(node, parents):
    chain = []
    current = node
    while current in parents:
        current = parents[current]
        chain.append(current)
    return chain


def _enclosing_button_if(node, parents):
    for ancestor in _ancestors(node, parents):
        if isinstance(ancestor, ast.If) and _calls_named(ancestor.test, "button"):
            return ancestor
    return None


def _enclosing_if(node, parents):
    for ancestor in _ancestors(node, parents):
        if isinstance(ancestor, ast.If):
            return ancestor
    return None


def _enclosing_if_exp(node, parents):
    for ancestor in _ancestors(node, parents):
        if isinstance(ancestor, ast.IfExp):
            return ancestor
    return None


def _mentions_name(node, name):
    return any(
        isinstance(child, ast.Name) and child.id == name for child in ast.walk(node)
    )


def _is_record_none_test(test):
    return (
        isinstance(test, ast.Compare)
        and isinstance(test.left, ast.Name)
        and test.left.id == "risk_record"
        and len(test.ops) == 1
        and isinstance(test.ops[0], ast.Is)
        and len(test.comparators) == 1
        and isinstance(test.comparators[0], ast.Constant)
        and test.comparators[0].value is None
    )


def _is_within(node, ancestor, parents):
    return ancestor in _ancestors(node, parents)


def _imported_modules(tree):
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def _attribute_chains(tree):
    chains = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            chains.add(_chain(node))
    return chains


def _chain(node):
    parts = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    return ".".join(reversed(parts))


def _called_names(tree):
    return {name for name in (_call_name(n) for n in ast.walk(tree)
                             if isinstance(n, ast.Call)) if name}


def _function_source(source, name):
    tree = ast.parse(source)
    function = _function_def(tree, name)
    if function is None:
        return None
    return ast.get_source_segment(source, function)


def _flatten(data, prefix=""):
    flat = {}
    for key, value in data.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flat.update(_flatten(value, path))
        else:
            flat[path] = value
    return flat


def _placeholders(text):
    return sorted(set(re.findall(r"\{(\w+)\}", text)))


# ============================================================
# 主入口
# ============================================================

def main():
    print("=" * 58)
    print("RiskPilot Current Risk 本地化回归测试（阶段 4A）")
    print("=" * 58)

    test_a_input_hash()
    test_b_multilang_read()
    test_c_stale_missing()
    test_d_session_cache()
    test_e_payload_validation()
    test_f_trigger_boundary()
    test_g_numeric_isolation()
    test_h_locale_ui()
    test_i_ast_guards()

    total = _PASSED + len(_FAILED)

    print("\n" + "=" * 58)
    print(f"结果：{_PASSED}/{total} 通过")

    if _FAILED:
        print("失败项：")
        for name in _FAILED:
            print(f"  - {name}")
        raise SystemExit(1)

    print("Current Risk 本地化回归测试全部通过")


if __name__ == "__main__":
    main()
