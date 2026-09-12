# -*- coding: utf-8 -*-
"""
RiskPilot Daily Log 显示层语言回归测试 (test_localized_log_display.py)

目标：中文界面不得把某条日志的旧英文 AI 文本（summary / risk title /
reason / suggestion）直接显示出来，英文界面也不得回退到旧中文文本。

测试对象是纯模块 ``utils/log_display.py``：
    * get_log_display_text()          当前语言内容选择（只读 project / log）
    * log_risk_display_fields()       单条风险的安全文本字段
    * summary_for_reasons()           Daily Risk reasons 的 summary 闸门
    * validate_log_text_version()     AI 语言版本返回值校验
    * build_log_text_version_prompt() 只改写文本的提示词
    * log_generate_button_key()       按钮 key 隔离

约束：不请求网络、不调用真实 AI、不写入 data/projects.json、
不导入 app.py（app.py 导入即执行 Streamlit 页面）。

覆盖场景：
    A. 当前语言 archive 优先（en / zh-CN 不混合）
    B. 只有英文 legacy → 中文界面隐藏并提示生成
    C. legacy 与当前语言一致 → 可以显示
    D. 旧日志缺少 language 字段 → 默认英文 legacy
    E. 生成新语言 archive → 另一语言 archive 不变
    F. 数值字段隔离（level / score / S / L / E / raw_risk / risk_count）
    G. Daily Risk summary 闸门
    H. 空文本 → source=empty，不误报「存在另一语言版本」
    I. AI 返回值校验纯函数
    J. AST 静态守卫

运行方式：
    python test_localized_log_display.py
"""

import ast
import copy
import json
import pathlib
import re
import sys
import tempfile
import types

from utils import content_locale, locale_registry
from utils import log_display as ld
from utils.baseline_display import ai_text_lookup, ai_text_store
from utils.log_display import (
    SOURCE_DETERMINISTIC_LEGACY,
    SOURCE_EMPTY,
    SOURCE_LEGACY_CURRENT_LANGUAGE,
    SOURCE_LOCALIZED_ARCHIVE,
    SOURCE_MISSING_CURRENT_LANGUAGE,
    build_log_text_version_prompt,
    generated_text_payload,
    get_log_display_text,
    is_system_summary,
    log_generate_button_key,
    log_risk_display_fields,
    prompt_language_name,
    summary_for_reasons,
    system_summary_label,
    validate_log_text_version,
)

ZH = "zh-CN"
EN = "en"

LOG_ID = "log-0001"

EN_SUMMARY = "Excavation work near the retaining wall without shoring."
EN_TITLE = "Unsupported excavation"
EN_REASON = "The log states that the trench wall was left unsupported."
EN_SUGGESTION = "Install shoring before workers re-enter the trench."

ZH_SUMMARY = "挡土墙附近开挖，未设置支护。"
ZH_TITLE = "开挖无支护"
ZH_REASON = "日志说明沟槽侧壁未做支护。"
ZH_SUGGESTION = "工人再次进入沟槽前先安装支护。"

# 另一语言（英文）自由文本不得出现在中文界面。
EN_FREE_TEXTS = (EN_SUMMARY, EN_TITLE, EN_REASON, EN_SUGGESTION)
ZH_FREE_TEXTS = (ZH_SUMMARY, ZH_TITLE, ZH_REASON, ZH_SUGGESTION)

NUMERIC_FIELDS = (
    "level",
    "score",
    "severity",
    "likelihood",
    "exposure",
    "raw_risk",
    "risk_count",
)


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


def dump(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


# ============================================================
# 测试数据
# ============================================================

def make_risk(
    title,
    reason,
    suggestion,
    severity=4,
    likelihood=3,
    exposure=3,
):
    """一条风险：文本与数值分开，数值必须与语言无关。"""
    calculation = {
        "severity": severity,
        "likelihood": likelihood,
        "exposure": exposure,
        "raw_risk": severity * likelihood * exposure,
        "score": min(100, severity * likelihood * exposure * 4),
        "level": "HIGH",
    }

    return {
        "title": title,
        "reason": reason,
        "suggestion": suggestion,
        **calculation,
    }


def make_log(
    language=EN,
    summary=EN_SUMMARY,
    risks=None,
    log_id=LOG_ID,
    include_language=True,
    title="Trench excavation",
    description="Excavation continued next to the retaining wall.",
):
    """一条日志；language 缺失时模拟旧日志（无 language 字段）。"""
    if risks is None:
        risks = [make_risk(EN_TITLE, EN_REASON, EN_SUGGESTION)]

    analysis = {
        "summary": summary,
        "risks": risks,
        "level": "HIGH",
        "score": 47,
        "risk_count": len(risks),
        "scoring_method": "S × L × E",
        "formula": "severity × likelihood × exposure",
        "score_type": "0-100 normalized",
    }

    if include_language:
        analysis["language"] = language

    return {
        "log_id": log_id,
        "date": "2026-03-04",
        "title": title,
        "description": description,
        "ai_analysis": analysis,
    }


def make_project(logs=None, name="Test Project"):
    return {
        "name": name,
        "baseline_score": 30,
        "daily_logs": logs if logs is not None else [],
        "ai_text_by_language": {},
    }


def store_log_text(project, payload, language, log_id=LOG_ID, section="logs"):
    """写入按语言隔离的 archive（纯函数，不触碰 analysis）。"""
    ai_text_store(project, section, payload, record_id=log_id, language=language)


# ============================================================
# A. 当前语言 archive 优先
# ============================================================

def test_a_current_language_archive_wins():
    print("\n--- A. 当前语言 archive 优先 ---")

    project = make_project([make_log()])
    log = project["daily_logs"][0]

    store_log_text(project, {"summary": ZH_SUMMARY, "risks": [
        {"title": ZH_TITLE, "reason": ZH_REASON, "suggestion": ZH_SUGGESTION},
    ]}, ZH)

    store_log_text(project, {"summary": EN_SUMMARY, "risks": [
        {"title": EN_TITLE, "reason": EN_REASON, "suggestion": EN_SUGGESTION},
    ]}, EN)

    zh_text = get_log_display_text(project, log, ZH)

    eq("A1 中文 source=localized_archive",
       zh_text["source"], SOURCE_LOCALIZED_ARCHIVE)
    eq("A2 中文 summary 取中文 archive", zh_text["summary"], ZH_SUMMARY)
    eq("A3 中文 risk 文本取中文 archive",
       zh_text["risks"][0], {
           "title": ZH_TITLE,
           "reason": ZH_REASON,
           "suggestion": ZH_SUGGESTION,
       })
    eq("A4 中文 needs_generation=False", zh_text["needs_generation"], False)

    for text in EN_FREE_TEXTS:
        check(f"A5 中文界面不含英文自由文本 {text[:24]!r}",
              text not in dump(zh_text))

    en_text = get_log_display_text(project, log, EN)

    eq("A6 英文 summary 取英文 archive", en_text["summary"], EN_SUMMARY)
    eq("A7 英文 risk 文本取英文 archive",
       en_text["risks"][0]["title"], EN_TITLE)

    for text in ZH_FREE_TEXTS:
        check(f"A8 英文界面不含中文自由文本 {text[:18]!r}",
              text not in dump(en_text))

    # 两语言 archive 同时存在时也必须各自独立。
    check("A9 en archive 仍存在",
          ai_text_lookup(project, "logs", LOG_ID, EN) is not None)
    check("A10 zh-CN archive 仍存在",
          ai_text_lookup(project, "logs", LOG_ID, ZH) is not None)


# ============================================================
# B. 只有英文 legacy
# ============================================================

def test_b_english_only_legacy():
    print("\n--- B. 只有英文 legacy ---")

    project = make_project([make_log(language=EN)])
    log = project["daily_logs"][0]

    before_project = dump(project)
    before_log = dump(log)

    zh_text = get_log_display_text(project, log, ZH)

    eq("B1 source=missing_current_language",
       zh_text["source"], SOURCE_MISSING_CURRENT_LANGUAGE)
    eq("B2 中文 summary 为空", zh_text["summary"], "")
    eq("B3 中文 risks 不返回自由文本", zh_text["risks"], [])
    eq("B4 needs_generation=True", zh_text["needs_generation"], True)
    eq("B5 saved_language=en", zh_text["saved_language"], EN)

    for text in EN_FREE_TEXTS:
        check(f"B6 中文界面不含英文自由文本 {text[:24]!r}",
              text not in dump(zh_text))

    check("B7 单条风险文本字段全部为空",
          log_risk_display_fields(zh_text, 0) == {
              "title": "",
              "reason": "",
              "suggestion": "",
          })
    check("B8 越界索引安全",
          log_risk_display_fields(zh_text, 5) == {
              "title": "",
              "reason": "",
              "suggestion": "",
          })

    eq("B9 project 深度相等（只读）", dump(project), before_project)
    eq("B10 log 深度相等（只读）", dump(log), before_log)
    check("B11 没有创建 archive",
          ai_text_lookup(project, "logs", LOG_ID, ZH) is None)

    # 英文界面仍可显示英文原文
    en_text = get_log_display_text(project, log, EN)
    eq("B12 英文界面 source=legacy_current_language",
       en_text["source"], SOURCE_LEGACY_CURRENT_LANGUAGE)
    eq("B13 英文界面显示英文 summary", en_text["summary"], EN_SUMMARY)


# ============================================================
# C. legacy 与当前语言一致
# ============================================================

def test_c_legacy_matches_current_language():
    print("\n--- C. legacy 与当前语言一致 ---")

    project = make_project([
        make_log(
            language=ZH,
            summary=ZH_SUMMARY,
            risks=[make_risk(ZH_TITLE, ZH_REASON, ZH_SUGGESTION)],
        ),
    ])
    log = project["daily_logs"][0]

    zh_text = get_log_display_text(project, log, ZH)

    eq("C1 source=legacy_current_language",
       zh_text["source"], SOURCE_LEGACY_CURRENT_LANGUAGE)
    eq("C2 显示 legacy 中文 summary", zh_text["summary"], ZH_SUMMARY)
    eq("C3 显示 legacy 中文 risk 标题",
       zh_text["risks"][0]["title"], ZH_TITLE)
    eq("C4 显示 legacy 中文 reason",
       zh_text["risks"][0]["reason"], ZH_REASON)
    eq("C5 显示 legacy 中文 suggestion",
       zh_text["risks"][0]["suggestion"], ZH_SUGGESTION)
    eq("C6 needs_generation=False", zh_text["needs_generation"], False)


# ============================================================
# D. 旧日志缺少 language 字段
# ============================================================

def test_d_legacy_without_language_field():
    print("\n--- D. 旧日志缺少 language 字段 ---")

    project = make_project([make_log(include_language=False)])
    log = project["daily_logs"][0]

    zh_text = get_log_display_text(project, log, ZH)
    en_text = get_log_display_text(project, log, EN)

    eq("D1 缺省判定为 en legacy（中文界面）",
       zh_text["source"], SOURCE_MISSING_CURRENT_LANGUAGE)
    eq("D2 缺省判定 saved_language=en", zh_text["saved_language"], EN)
    eq("D3 中文界面不显示英文 summary", zh_text["summary"], "")
    eq("D4 中文界面 needs_generation=True", zh_text["needs_generation"], True)

    for text in EN_FREE_TEXTS:
        check(f"D5 中文界面不含英文自由文本 {text[:24]!r}",
              text not in dump(zh_text))

    eq("D6 英文界面显示 legacy 英文 summary",
       en_text["summary"], EN_SUMMARY)
    eq("D7 英文界面 source=legacy_current_language",
       en_text["source"], SOURCE_LEGACY_CURRENT_LANGUAGE)


# ============================================================
# E. 生成新语言 archive
# ============================================================

def test_e_generate_other_language_archive():
    print("\n--- E. 生成新语言 archive ---")

    project = make_project([make_log()])
    log = project["daily_logs"][0]

    store_log_text(project, {"summary": EN_SUMMARY, "risks": [
        {"title": EN_TITLE, "reason": EN_REASON, "suggestion": EN_SUGGESTION},
    ]}, EN)

    en_before = copy.deepcopy(ai_text_lookup(project, "logs", LOG_ID, EN))
    analysis_before = dump(log["ai_analysis"])
    project_before = copy.deepcopy(project)

    generated = validate_log_text_version({
        "summary": ZH_SUMMARY,
        "risks": [{
            "title": ZH_TITLE,
            "reason": ZH_REASON,
            "suggestion": ZH_SUGGESTION,
        }],
    }, ld.log_risk_count(log["ai_analysis"]))

    store_log_text(project, generated, ZH)

    eq("E1 en archive 深度不变",
       ai_text_lookup(project, "logs", LOG_ID, EN), en_before)
    eq("E2 analysis 全部字段深度不变",
       dump(log["ai_analysis"]), analysis_before)
    eq("E3 原有日志对象不变",
       dump(project["daily_logs"][0]), dump(project_before["daily_logs"][0]))
    eq("E4 其他字段不变（baseline_score）",
       project["baseline_score"], project_before["baseline_score"])

    zh_text = get_log_display_text(project, log, ZH)
    eq("E5 中文界面改读 zh-CN archive",
       zh_text["source"], SOURCE_LOCALIZED_ARCHIVE)
    eq("E6 中文界面显示新生成 summary", zh_text["summary"], ZH_SUMMARY)

    en_text = get_log_display_text(project, log, EN)
    eq("E7 英文界面仍读 en archive", en_text["summary"], EN_SUMMARY)

    # 另一条日志完全不受影响
    other = make_log(log_id="log-0002")
    project2 = make_project([make_log(), other])
    snapshot_other = dump(project2["daily_logs"][1])
    store_log_text(project2, {"summary": ZH_SUMMARY, "risks": []}, ZH, LOG_ID)
    eq("E8 同一项目内其他日志不变",
       dump(project2["daily_logs"][1]), snapshot_other)

    # 兼容旧 section 名：只读、不覆盖 "logs"
    legacy_project = make_project([make_log()])
    ai_text_store(
        legacy_project, "log",
        {"summary": ZH_SUMMARY, "risks": []},
        record_id=LOG_ID, language=ZH,
    )
    eq("E9 只读兼容 log section",
       get_log_display_text(legacy_project, legacy_project["daily_logs"][0], ZH)["summary"],
       ZH_SUMMARY)

    store_log_text(legacy_project, {"summary": "新写入的 logs 内容", "risks": []}, ZH)
    eq("E10 logs section 优先于 log section",
       get_log_display_text(legacy_project, legacy_project["daily_logs"][0], ZH)["summary"],
       "新写入的 logs 内容")


# ============================================================
# F. 数值字段隔离
# ============================================================

def test_f_numeric_fields_isolated():
    print("\n--- F. 数值字段隔离 ---")

    project = make_project([make_log()])
    log = project["daily_logs"][0]

    def numbers():
        analysis = log["ai_analysis"]
        captured = {
            key: analysis.get(key) for key in NUMERIC_FIELDS
        }
        risk = analysis["risks"][0]
        captured.update({
            f"risk0.{key}": risk.get(key)
            for key in ("severity", "likelihood", "exposure", "raw_risk", "score", "level")
        })
        return captured

    before = numbers()

    store_log_text(project, {"summary": EN_SUMMARY, "risks": [
        {"title": EN_TITLE, "reason": EN_REASON, "suggestion": EN_SUGGESTION},
    ]}, EN)

    generated = validate_log_text_version({
        "summary": ZH_SUMMARY,
        "risks": [{
            "title": ZH_TITLE,
            "reason": ZH_REASON,
            "suggestion": ZH_SUGGESTION,
        }],
    }, ld.log_risk_count(log["ai_analysis"]))

    store_log_text(project, generated, ZH)

    get_log_display_text(project, log, ZH)
    get_log_display_text(project, log, EN)

    after = numbers()

    eq("F1 数值字段完全一致", after, before)

    for key in NUMERIC_FIELDS:
        check(f"F2 {key} 未被改写",
              after[key] == before[key],
              f"{before[key]!r} -> {after[key]!r}")

    check("F3 显示结果不含 score/level 字段",
          not any(
              key in get_log_display_text(project, log, ZH)
              for key in ("score", "level", "severity", "raw_risk")
          ))

    check("F4 校验结果只含 summary/risks",
          set(generated.keys()) == {"summary", "risks"})
    check("F5 校验后的风险项只含文本字段",
          set(generated["risks"][0].keys()) == {"title", "reason", "suggestion"})


# ============================================================
# G. Daily Risk summary 闸门
# ============================================================

def test_g_daily_risk_summary_gate():
    print("\n--- G. Daily Risk summary 闸门 ---")

    # 当前语言 archive 存在 → summary 可以进入 reasons
    project = make_project([make_log()])
    log = project["daily_logs"][0]
    store_log_text(project, {"summary": ZH_SUMMARY, "risks": []}, ZH)

    eq("G1 中文 archive 存在时中文 summary 可进入 reasons",
       summary_for_reasons(project, log, ZH), ZH_SUMMARY)

    # 只有英文 → 中文 reasons 不得包含英文 summary
    project2 = make_project([make_log()])
    log2 = project2["daily_logs"][0]
    store_log_text(project2, {"summary": EN_SUMMARY, "risks": []}, EN)

    eq("G2 只有英文 archive 时中文 reasons 为空",
       summary_for_reasons(project2, log2, ZH), "")
    eq("G3 只有英文 archive 时英文 reasons 可用",
       summary_for_reasons(project2, log2, EN), EN_SUMMARY)

    # legacy 英文 + 中文界面 → reasons 为空（不跨语言回退）
    project3 = make_project([make_log()])
    eq("G4 legacy 英文 + 中文界面 → 空",
       summary_for_reasons(project3, project3["daily_logs"][0], ZH), "")

    # 确定性系统文本（Migrated / fallback）可以本地化
    system_summary = "Imported from an earlier RiskPilot log."

    project4 = make_project([make_log(summary=system_summary, risks=[])])
    localized = summary_for_reasons(project4, project4["daily_logs"][0], ZH)

    check("G5 Migrated 系统文本本地化",
          bool(localized) and localized != system_summary)
    check("G6 Migrated 系统文本不含英文原文",
          system_summary not in localized)
    check("G7 Migrated 不要求重新生成",
          get_log_display_text(project4, project4["daily_logs"][0], ZH)["needs_generation"] is False)
    eq("G8 Migrated source=deterministic_legacy",
       get_log_display_text(project4, project4["daily_logs"][0], ZH)["source"],
       SOURCE_DETERMINISTIC_LEGACY)

    # 系统文本 + 另一语言自由文本 → 仍然按缺失当前语言处理
    project5 = make_project([make_log(summary=system_summary)])
    zh_text5 = get_log_display_text(project5, project5["daily_logs"][0], ZH)

    eq("G9 系统文本 + 英文自由文本 → missing_current_language",
       zh_text5["source"], SOURCE_MISSING_CURRENT_LANGUAGE)
    eq("G10 此时不显示任何自由文本", zh_text5["summary"], "")
    eq("G11 此时允许重新生成", zh_text5["needs_generation"], True)


# ============================================================
# H. 空文本
# ============================================================

def test_h_empty_text():
    print("\n--- H. 空文本 ---")

    project = make_project([make_log(summary="", risks=[])])
    log = project["daily_logs"][0]

    zh_text = get_log_display_text(project, log, ZH)

    eq("H1 source=empty", zh_text["source"], SOURCE_EMPTY)
    eq("H2 summary 为空", zh_text["summary"], "")
    eq("H3 risks 为空", zh_text["risks"], [])
    eq("H4 needs_generation=False（不误报另一语言版本）",
       zh_text["needs_generation"], False)

    eq("H5 无 AI 文本 → Daily Risk reasons 为空",
       summary_for_reasons(project, log, ZH), "")

    # 完全没有 ai_analysis 字段
    project2 = make_project([{"log_id": "log-9", "title": "t", "description": "d"}])
    log2 = project2["daily_logs"][0]
    eq("H6 缺少 ai_analysis → source=empty",
       get_log_display_text(project2, log2, ZH)["source"], SOURCE_EMPTY)
    eq("H7 缺少 ai_analysis → 不报另一语言",
       get_log_display_text(project2, log2, ZH)["needs_generation"], False)

    # 缺少 log_id 时不得误读整段 archive
    project3 = make_project([make_log()])
    project3["daily_logs"][0].pop("log_id")
    store_log_text(project3, {"summary": ZH_SUMMARY, "risks": []}, ZH, LOG_ID)
    eq("H8 缺少 log_id 时不误读 archive",
       get_log_display_text(project3, project3["daily_logs"][0], ZH)["source"],
       SOURCE_MISSING_CURRENT_LANGUAGE)

    # 空 archive（全空字符串）不得当成有效版本
    project4 = make_project([make_log()])
    store_log_text(project4, {"summary": "", "risks": []}, ZH)
    eq("H9 空 archive 回落到 legacy 判定",
       get_log_display_text(project4, project4["daily_logs"][0], ZH)["source"],
       SOURCE_MISSING_CURRENT_LANGUAGE)


# ============================================================
# I. AI 返回值校验
# ============================================================

def test_i_validation():
    print("\n--- I. AI 返回值校验 ---")

    good = {
        "summary": ZH_SUMMARY,
        "risks": [
            {"title": ZH_TITLE, "reason": ZH_REASON, "suggestion": ZH_SUGGESTION},
            {"title": "二", "reason": "二", "suggestion": "二"},
        ],
    }

    valid = validate_log_text_version(good, 2)

    eq("I1 正确风险数量通过", valid["summary"], ZH_SUMMARY)
    eq("I2 风险数量一致", len(valid["risks"]), 2)

    eq("I3 风险数量变化被拒绝",
       validate_log_text_version(good, 3), {})
    eq("I4 风险数量变化（少）被拒绝",
       validate_log_text_version({"summary": "s", "risks": []}, 1), {})

    eq("I5 非 dict 被拒绝", validate_log_text_version(["not", "a", "dict"], 2), {})
    eq("I6 risks 非 list 被拒绝",
       validate_log_text_version({"summary": "s", "risks": "x"}, 0), {})
    eq("I7 risks 为 dict 被拒绝",
       validate_log_text_version({"summary": "s", "risks": {"a": 1}}, 1), {})
    eq("I8 item 非 dict 被拒绝",
       validate_log_text_version({"summary": "s", "risks": ["x"]}, 1), {})
    eq("I9 summary 非字符串被拒绝",
       validate_log_text_version({"summary": 12, "risks": []}, 0), {})
    eq("I10 缺少 summary 被拒绝",
       validate_log_text_version({"risks": []}, 0), {})
    eq("I11 缺少 risks 被拒绝",
       validate_log_text_version({"summary": "s"}, 0), {})
    eq("I12 期望数量非法被拒绝",
       validate_log_text_version(good, None), {})

    # 校验失败 → 不得写 archive
    eq("I13 校验失败时 payload 为 None",
       generated_text_payload(validate_log_text_version(good, 5)), None)

    payload = generated_text_payload(valid)
    check("I14 校验成功时给出可写入 payload", payload is not None)

    mixed = validate_log_text_version(
        {"summary": "s", "risks": [{"title": None, "reason": 7, "suggestion": ""}]},
        1,
    )
    eq("I15 文本字段统一转成安全字符串",
       mixed["risks"][0], {"title": "", "reason": "7", "suggestion": ""})

    # 提示词约束
    log = make_log()
    prompt = build_log_text_version_prompt(log, ZH)

    check("I16 提示词要求保持一致的风险数量",
          "exactly 1 risk objects" in prompt)
    check("I17 提示词要求简体中文", "Simplified Chinese" in prompt)
    check("I18 提示词禁止增加/删除风险",
          "Do NOT add, remove, merge or split risks" in prompt)
    check("I19 提示词禁止输出分数与 S/L/E",
          "Do NOT output level, severity, likelihood, exposure or raw_risk" in prompt)
    check("I20 提示词禁止臆造事件",
          "Do NOT invent events" in prompt)
    check("I21 提示词只要求 summary 与 risks 文本字段",
          '"summary"' in prompt and '"title"' in prompt)
    check("I22 英文目标语言提示词",
          "English" in build_log_text_version_prompt(log, EN))
    check("I23 用户正文只作为上下文出现",
          "do not translate it" in prompt)

    # 按钮 key 隔离
    key_a = log_generate_button_key("项目 A", LOG_ID, ZH)
    key_b = log_generate_button_key("项目 A", LOG_ID, EN)
    key_c = log_generate_button_key("项目 B", LOG_ID, ZH)
    key_d = log_generate_button_key("项目 A", "log-0002", ZH)

    eq("I24 按钮 key 稳定", log_generate_button_key("项目 A", LOG_ID, ZH), key_a)
    check("I25 不同语言 key 不同", key_a != key_b)
    check("I26 不同项目 key 不同", key_a != key_c)
    check("I27 不同日志 key 不同", key_a != key_d)
    check("I28 key 不含项目名等特殊字符",
          "项目" not in key_a and "::" in key_a)


# ============================================================
# J. 静态守卫
# ============================================================

ROOT = pathlib.Path(__file__).resolve().parent
MODULE_PATH = ROOT / "utils" / "log_display.py"
APP_PATH = ROOT / "app.py"


def _code_references(path):

    tree = ast.parse(path.read_text(encoding="utf-8"))

    docstrings = set()

    for node in ast.walk(tree):

        if not isinstance(
            node,
            (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
        ):
            continue

        body = getattr(node, "body", [])
        first = body[0] if body else None

        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            docstrings.add(id(first.value))

    names = set()
    strings = set()

    for node in ast.walk(tree):

        if isinstance(node, ast.Name):
            names.add(node.id)

        elif isinstance(node, ast.Attribute):
            names.add(node.attr)

        elif isinstance(node, ast.alias):
            names.add(node.name)
            names.add(node.asname or "")

        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
        ):
            strings.add(node.value)

    return names, strings, tree


def _function_node(tree, name):
    return next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _written_keys(node):
    """节点内所有被写入的 dict 键名与属性名（不含普通局部变量）。"""
    keys = set()

    for item in ast.walk(node):

        if isinstance(item, ast.Assign):
            targets = item.targets

        elif isinstance(item, (ast.AugAssign, ast.AnnAssign)):
            targets = [item.target]

        else:
            continue

        for target in targets:

            if isinstance(target, ast.Attribute):
                keys.add(target.attr)

            elif isinstance(target, ast.Subscript):

                key = target.slice

                if isinstance(key, ast.Constant):
                    keys.add(str(key.value))

                elif isinstance(key, ast.Name):
                    keys.add(key.id)

    return keys


def _assigned_targets(fn_node):
    """函数体内所有赋值目标的名字与下标常量。"""
    names = set()

    for node in ast.walk(fn_node):

        if isinstance(node, ast.Assign):
            targets = node.targets

        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]

        else:
            continue

        for target in targets:

            if isinstance(target, ast.Name):
                names.add(target.id)

            elif isinstance(target, ast.Attribute):
                names.add(target.attr)

            elif isinstance(target, ast.Subscript):

                key = target.slice

                if isinstance(key, ast.Constant):
                    names.add(str(key.value))

                elif isinstance(key, ast.Name):
                    names.add(key.id)

    return names


def test_j_static_guards():
    print("\n--- J. 静态守卫 ---")

    app_src = APP_PATH.read_text(encoding="utf-8")
    app_tree = ast.parse(app_src)

    names, strings, tree = _code_references(MODULE_PATH)
    refs = names | strings

    # J1: 纯模块无 Streamlit / 状态 / 副作用 / 网络。
    check("J1 纯模块不导入 streamlit", "streamlit" not in refs)
    check("J1 纯模块不导入 requests", "requests" not in refs)

    for token in ("session_state", "persist", "ui_lang", "set_current_language"):
        check(f"J1 纯模块不引用 {token}", token not in refs)

    check("J1 纯模块不调用 AI",
          "RiskAgent" not in refs and "ai_analyze_daily_log" not in refs)
    check("J1 纯模块不读取环境/文件",
          "open" not in refs and "os" not in refs)

    # J2: 纯模块不写风险数值字段（只读引用可以，例如提示词里的上下文与禁令）。
    module_writes = _written_keys(tree)

    for token in ("level", "score", "severity", "likelihood", "exposure",
                  "raw_risk", "risk_count"):
        check(f"J2 纯模块不写风险字段 {token}",
              token not in module_writes,
              f"written={sorted(module_writes)}")

    check("J2 纯模块没有任何 dict 写操作",
          not any(
              isinstance(node, ast.Call)
              and getattr(node.func, "attr", None) in ("update", "setdefault", "pop")
              for node in ast.walk(tree)
          ))

    # J3: get_log_display_text 深度只读。
    fn_node = _function_node(tree, "get_log_display_text")

    writes = []

    for node in ast.walk(fn_node):

        if isinstance(node, ast.Assign):
            targets = node.targets

        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]

        else:
            continue

        for target in targets:

            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.value, ast.Name)
                and target.value.id in ("project", "log", "analysis")
            ):
                writes.append(node.lineno)

    eq("J3 get_log_display_text 不修改 project/log/analysis", writes, [])

    called = {
        getattr(call.func, "id", None) or getattr(call.func, "attr", None)
        for call in ast.walk(fn_node)
        if isinstance(call, ast.Call)
    }

    check("J3 get_log_display_text 不调用 AI/persist",
          not (
              called
              & {
                  "persist",
                  "RiskAgent",
                  "ai_analyze_daily_log",
                  "ai_text_store",
              }
          ),
          f"called={sorted(name for name in called if name)}")

    # J4: Logs 页面（daily logs 视图）没有跨语言 fallback。
    start = app_src.index('    if view == "logs":')
    end = app_src.index('    if view == "daily":')
    logs_block = app_src[start:end]

    check("J4 Logs 页面使用 get_log_display_text",
          "get_log_display_text(" in logs_block)
    check("J4 Logs 页面不再直接读 analysis['summary']",
          not re.search(r'analysis\.get\(\s*["\']summary["\']', logs_block)
          and 'analysis["summary"]' not in logs_block)
    check("J4 Logs 页面无 or analysis.get 跨语言 fallback",
          "or analysis.get(" not in logs_block)
    check("J4 Logs 页面不再使用旧 localized_log_risk",
          "localized_log_risk" not in logs_block)
    check("J4 Logs 页面不再使用 log_text 变量",
          re.search(r"\blog_text\b", logs_block) is None)
    check("J4 Logs 页面风险文本走 log_risk_display_fields",
          "log_risk_display_fields(" in logs_block)

    # J5: 生成按钮 key 含项目 / 日志 / 语言。
    check("J5 生成按钮使用 log_generate_button_key",
          "log_generate_button_key(" in logs_block)
    check("J5 按钮 key 传入项目标识",
          re.search(
              r"log_generate_button_key\(\s*\n?\s*selected", logs_block
          ) is not None)
    check("J5 按钮 key 传入 log_id",
          "log.get(\"log_id\")" in logs_block)
    check("J5 按钮 key 传入当前语言",
          "get_current_language()" in logs_block)

    # J6: 页面打开路径不自动调用 AI；AI 调用只在用户点击分支内。

    def _has_button_call(node):
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr in ("button", "form_submit_button")
            ):
                return True
        return False

    def _names_in(node):
        return {
            sub.id
            for sub in ast.walk(node)
            if isinstance(sub, ast.Name)
        }

    # 由按钮返回值产生的「点击标记」变量（例如 save_clicked）。
    click_flags = set()

    for node in ast.walk(app_tree):

        if not isinstance(node, ast.Assign):
            continue

        if "button" not in ast.dump(node.value):
            continue

        for target in node.targets:

            if isinstance(target, ast.Name):
                click_flags.add(target.id)

    check("J6 找到点击标记变量", bool(click_flags), sorted(click_flags))

    class _AiCallGuard(ast.NodeVisitor):

        def __init__(self):
            self.stack = []
            self.calls = []
            self.unguarded = []

        def visit_If(self, node):

            test_names = _names_in(node.test)

            guarded = _has_button_call(node.test) or (
                bool(test_names) and test_names <= click_flags
            )

            self.stack.append(guarded)

            for child in node.body:
                self.visit(child)

            self.stack.pop()

            for child in node.orelse:
                self.visit(child)

        def visit_Call(self, node):

            target = getattr(node.func, "id", None)

            if target in ("ai_generate_log_text_version",
                          "ai_analyze_daily_log"):

                self.calls.append((target, node.lineno))

                if not any(self.stack):
                    self.unguarded.append((target, node.lineno))

            self.generic_visit(node)

    guard = _AiCallGuard()
    guard.visit(app_tree)

    eq("J6 AI 调用只在用户点击分支内", guard.unguarded, [])

    check("J6 生成函数确实被按钮调用",
          any(
              target == "ai_generate_log_text_version"
              for target, _ in guard.calls
          ))

    check("J6 页面打开路径不调用 AI（无顶层 AI 调用）",
          len(guard.calls) >= 2)

    # J7: 生成语言版本的函数不写风险数值、不写 ui_lang。
    gen_node = _function_node(app_tree, "ai_generate_log_text_version")
    targets = _assigned_targets(gen_node)

    for token in ("level", "score", "severity", "likelihood", "exposure",
                  "raw_risk", "risk_count"):
        check(f"J7 ai_generate_log_text_version 不写 {token}",
              token not in targets)

    gen_src = ast.get_source_segment(app_src, gen_node)

    for token in ("title", "description", "log_id", "date", "ai_analysis"):
        check(f"J7 ai_generate_log_text_version 不写用户字段 {token}",
              token not in targets)

    check("J7 生成函数不引用 ui_lang", "ui_lang" not in gen_src)
    check("J7 生成函数不写 ai_analysis",
          "ai_analysis\"] =" not in gen_src and "'ai_analysis'] =" not in gen_src)
    check("J7 生成函数只做校验并返回",
          "validate_log_text_version(" in gen_src)

    # J8: Daily Risk 不跨语言回退。
    daily_node = _function_node(app_tree, "build_daily_risk")
    daily_src = ast.get_source_segment(app_src, daily_node)

    check("J8 build_daily_risk 接收显式 language 参数",
          "language=None" in daily_src)
    check("J8 build_daily_risk 使用 summary_for_reasons",
          "summary_for_reasons(" in daily_src)
    check("J8 build_daily_risk 不直接读 analysis['summary']",
          not re.search(r'analysis\.get\(\s*["\']summary["\']', daily_src)
          and 'analysis["summary"]' not in daily_src)
    check("J8 build_daily_risk 无 or analysis.get 跨语言 fallback",
          "or analysis.get(" not in daily_src)
    check("J8 build_daily_risk 不再使用 ai_text_lookup 读 summary",
          "ai_text_lookup(" not in daily_src)

    score_lines = [
        line for line in daily_src.splitlines()
        if "score = min(100" in line
    ]

    check("J8 build_daily_risk 的分数行不含 summary",
          bool(score_lines) and all("summary" not in line for line in score_lines))

    check("J8 build_daily_risk 调用处显式传入 language",
          app_src.count("language=get_current_language()") >= 2)

    # J9: ui_lang 状态逻辑未被修改。
    check("J9 app.py 未直接写 ui_lang",
          'session_state["ui_lang"]' not in app_src)
    check("J9 on_ui_language_change 仍调用 set_current_language",
          "set_current_language(chosen)" in app_src)

    # J10: 确定性系统文本在 zh-CN 全部有译文。
    missing = []

    for raw, key in ld.SYSTEM_SUMMARY_DISPLAY_KEYS.items():

        localized = system_summary_label(raw, ZH)

        if not localized or localized == raw:
            missing.append(key)

    eq("J10 系统文本均有 zh-CN 译文", missing, [])

    check("J10 未知文本不返回原文",
          system_summary_label("Some free text the AI wrote.", ZH) == "")
    check("J10 未知文本判定为自由文本",
          is_system_summary("Some free text the AI wrote.") is False)

    # J11: 新增 locale key 必须中英齐备、占位符一致且真正翻译过。
    en_locale = json.loads((ROOT / "locales" / "en.json").read_text(encoding="utf-8"))
    zh_locale = json.loads((ROOT / "locales" / "zh-CN.json").read_text(encoding="utf-8"))

    def flat(d, prefix=""):
        out = set()
        for key, value in d.items():
            path = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                out |= flat(value, path)
            else:
                out.add(path)
        return out

    def value_of(locale, key):
        node = locale
        for part in key.split("."):
            node = node[part]
        return node

    en_keys, zh_keys = flat(en_locale), flat(zh_locale)

    added_keys = [
        "dashboard.warning_log_text_language",
        "dashboard.info_log_text_missing",
        "dashboard.button_generate_log_text",
        "dashboard.spinner_generate_log_text",
        "dashboard.error_log_text_generation_failed",
        "dashboard.risk_suggestion",
    ] + sorted(ld.SYSTEM_SUMMARY_DISPLAY_KEYS.values())

    for key in added_keys:
        check(f"J11 新 key 中英齐备：{key}",
              key in en_keys and key in zh_keys)

    for key in added_keys:

        if key not in en_keys or key not in zh_keys:
            continue

        check(f"J11 新 key 已本地化：{key}",
              value_of(en_locale, key) != value_of(zh_locale, key))

    en_log = {key for key in en_keys if key.startswith("log.")}
    zh_log = {key for key in zh_keys if key.startswith("log.")}

    eq("J11 log.* 命名空间中英一致", en_log, zh_log)
    check("J11 系统文本 key 全部位于 log.summary.*",
          all(
              key.startswith("log.summary.")
              for key in ld.SYSTEM_SUMMARY_DISPLAY_KEYS.values()
          ))

    warning_en = en_locale["dashboard"]["warning_log_text_language"]
    warning_zh = zh_locale["dashboard"]["warning_log_text_language"]

    for placeholder in ("{saved}", "{current}"):
        check(f"J11 提示语占位符 {placeholder} 中英一致",
              placeholder in warning_en and placeholder in warning_zh)

    check("J11 提示语中英占位符数量一致",
          warning_en.count("{") == warning_zh.count("{"))

    check("J11 risk_suggestion 占位符一致",
          "{value}" in en_locale["dashboard"]["risk_suggestion"]
          and "{value}" in zh_locale["dashboard"]["risk_suggestion"])


# ============================================================
# K. 统一 localized_content 协议（阶段 3）
# ============================================================

def test_k_unified_protocol():
    print("\n--- K. 统一 localized_content 协议（阶段 3）---")

    from utils import localized_content as lc

    def en_only_display():
        """没有中文 archive 的英文日志在中文界面下的显示（基准）。"""
        base = make_project([make_log(language=EN)])
        return dump(get_log_display_text(base, base["daily_logs"][0], ZH))

    zh_payload = {
        "summary": ZH_SUMMARY,
        "risks": [
            {"title": ZH_TITLE, "reason": ZH_REASON, "suggestion": ZH_SUGGESTION},
        ],
    }
    en_payload = {
        "summary": EN_SUMMARY,
        "risks": [
            {"title": EN_TITLE, "reason": EN_REASON, "suggestion": EN_SUGGESTION},
        ],
    }

    # K1: 新协议记录可被当前语言读出。
    project = make_project([make_log(language=EN)])
    log = project["daily_logs"][0]

    store_log_text(project, zh_payload, ZH)

    zh_text = get_log_display_text(project, log, ZH)

    eq("K1 新协议 source=localized_archive",
       zh_text["source"], SOURCE_LOCALIZED_ARCHIVE)
    eq("K1 新协议 summary 被读出", zh_text["summary"], ZH_SUMMARY)
    eq("K1 新协议 risk 文本被读出", zh_text["risks"][0]["title"], ZH_TITLE)
    eq("K1 needs_generation=False", zh_text["needs_generation"], False)
    check("K1 已写入新协议容器", lc.LOCALIZED_CONTENT_KEY in project)
    eq("K1 旧 archive 同步写入（双写兼容）",
       project["ai_text_by_language"][ZH]["logs"][LOG_ID]["summary"], ZH_SUMMARY)

    # K2: 中文新协议记录不影响英文界面。
    en_text = get_log_display_text(project, log, EN)

    eq("K2 英文仍读英文 legacy",
       en_text["source"], SOURCE_LEGACY_CURRENT_LANGUAGE)
    eq("K2 英文 summary 仍是英文", en_text["summary"], EN_SUMMARY)

    for text in ZH_FREE_TEXTS:
        check(f"K2 英文界面不含中文 {text[:16]!r}", text not in dump(en_text))

    # K3: 同一语言下新协议记录优先于旧 archive。
    project = make_project([make_log(language=EN)])
    log = project["daily_logs"][0]
    project["ai_text_by_language"] = {ZH: {"logs": {LOG_ID: dict(zh_payload)}}}

    record = lc.make_localized_record(
        "logs", LOG_ID, ZH, {"summary": "新协议中文摘要"}
    )
    lc.localized_content_put(project, record, copy_on_write=False)

    eq("K3 新协议优先于旧 archive",
       get_log_display_text(project, log, ZH)["summary"], "新协议中文摘要")
    eq("K3 旧 archive 未被删除",
       project["ai_text_by_language"][ZH]["logs"][LOG_ID]["summary"], ZH_SUMMARY)

    # K4: 另一语言的新协议记录绝不是 fallback。
    expected = en_only_display()

    project = make_project([make_log(language=EN)])
    log = project["daily_logs"][0]

    store_log_text(project, en_payload, EN)

    actual = get_log_display_text(project, log, ZH)

    eq("K4 英文新协议记录不改变中文界面", dump(actual), expected)
    eq("K4 中文仍需要生成当前语言版本", actual["needs_generation"], True)

    for text in EN_FREE_TEXTS:
        check(f"K4 中文界面不含英文 {text[:20]!r}", text not in dump(actual))

    # K5: 写中文不覆盖英文新协议记录。
    project = make_project([make_log(language=EN)])

    store_log_text(project, en_payload, EN)
    store_log_text(project, zh_payload, ZH)

    eq("K5 英文新协议记录未被覆盖",
       ai_text_lookup(project, "logs", LOG_ID, EN)["summary"], EN_SUMMARY)
    eq("K5 中文新协议记录已写入",
       ai_text_lookup(project, "logs", LOG_ID, ZH)["summary"], ZH_SUMMARY)
    eq("K5 两语言共存",
       lc.localized_content_languages(project, "logs", LOG_ID), sorted([EN, ZH]))

    # K6: 日志 A 的新协议记录不影响日志 B。
    LOG_B = "log-0002"
    project = make_project([make_log(), make_log(log_id=LOG_B)])
    log_a = project["daily_logs"][0]
    log_b = project["daily_logs"][1]

    store_log_text(project, {"summary": "日志 A 摘要"}, ZH, log_id=LOG_ID)
    store_log_text(project, {"summary": "日志 B 摘要"}, ZH, log_id=LOG_B)

    eq("K6 日志 B 摘要独立",
       ai_text_lookup(project, "logs", LOG_B, ZH)["summary"], "日志 B 摘要")
    eq("K6 日志 B 显示取自己的新协议记录",
       get_log_display_text(project, log_b, ZH)["summary"], "日志 B 摘要")
    eq("K6 日志 A 显示取自己的新协议记录",
       get_log_display_text(project, log_a, ZH)["summary"], "日志 A 摘要")

    store_log_text(project, {"summary": "日志 A 摘要 v2"}, ZH, log_id=LOG_ID)

    eq("K6 更新 A 不改变 B",
       ai_text_lookup(project, "logs", LOG_B, ZH)["summary"], "日志 B 摘要")
    eq("K6 A 已更新",
       ai_text_lookup(project, "logs", LOG_ID, ZH)["summary"], "日志 A 摘要 v2")
    eq("K6 B 的显示仍未受影响",
       get_log_display_text(project, log_b, ZH)["summary"], "日志 B 摘要")

    # K7: 空 / 纯空白 payload 的新记录不产生假 archive。
    expected = en_only_display()

    project = make_project([make_log(language=EN)])
    log = project["daily_logs"][0]

    record = lc.make_localized_record(
        "logs", LOG_ID, ZH, {"summary": "   ", "risks": []}
    )
    lc.localized_content_put(project, record, copy_on_write=False)

    eq("K7 空新记录不改变显示",
       dump(get_log_display_text(project, log, ZH)), expected)

    # K8: 非法新协议记录被忽略。
    expected = en_only_display()

    project = make_project([make_log(language=EN)])
    log = project["daily_logs"][0]
    project["localized_content"] = {
        "logs": {
            LOG_ID: {
                ZH: {
                    "schema_version": 99,
                    "payload": {"summary": "坏记录"},
                }
            }
        }
    }

    actual = get_log_display_text(project, log, ZH)

    check("K8 非法新记录不被显示", "坏记录" not in dump(actual))
    eq("K8 非法新记录被忽略", dump(actual), expected)

    # K9: 畸形 localized_content 容器安全降级。
    expected = en_only_display()

    for malformed in (
        "x",
        5,
        [],
        {"logs": "x"},
        {"logs": {LOG_ID: "x"}},
        {"logs": {LOG_ID: {ZH: "x"}}},
        {"logs": {LOG_ID: {ZH: {"schema_version": 1}}}},
    ):
        project = make_project([make_log(language=EN)])
        project["localized_content"] = malformed

        try:
            actual = get_log_display_text(project, project["daily_logs"][0], ZH)
        except Exception as exc:  # noqa: BLE001
            check(f"K9 畸形容器 {malformed!r} 不抛异常",
                  False, f"{type(exc).__name__}: {exc}")
            continue

        eq(f"K9 畸形容器 {malformed!r} 安全降级", dump(actual), expected)

    # K10: 数值字段不得进入可翻译载荷（阶段 4 契约）。
    check("K10 数值字段不能进 logs 可翻译 payload",
          lc.validate_translatable_payload(
              "logs", {"summary": "x", "level": "HIGH", "score": 47}
          ) != [])
    check("K10 纯文本 logs payload 合法",
          lc.validate_translatable_payload(
              "logs",
              {
                  "summary": "x",
                  "risks": [{"title": "t", "reason": "r", "suggestion": "s"}],
              },
          ) == [])


# ============================================================
# L. 目标语言（registry 驱动，不再二元判断）
# ============================================================

#: manifest 中仍是 planned 的目标语言：本阶段不得出现在 UI 选择器。
FUTURE_LOCALES = ("de",)

#: 这些语言在 locale registry 里的 AI 语言名（fr 已启用，仍在此校验语言名）。
FUTURE_AI_NAMES = {
    "es": "Spanish",
    "fr": "French",
    "pt-BR": "Brazilian Portuguese",
    "sw": "Swahili",
}

#: 不允许再出现 locale 二元语言判断的源码。
LANGUAGE_SOURCES = (APP_PATH, ROOT / "utils" / "log_display.py")

#: 会被当作"语言分支"的常量。
ZH_LANGUAGE_CONSTANTS = ("zh-CN", "简体中文")
EN_LANGUAGE_CONSTANTS = ("English",)


def _contains_constant(node, values):
    """节点子树里是否出现指定字符串常量。"""
    return any(
        isinstance(item, ast.Constant) and item.value in values
        for item in ast.walk(node)
    )


def _binary_language_branches(path):
    """
    `locale == "zh-CN" ... else "English"` 形式的语言二元分支行号。

    只认"条件里出现 zh-CN / 简体中文，且某个分支产出 English"这一种形状，
    因此普通的中文显示判断不会被误报。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []

    for node in ast.walk(tree):

        if isinstance(node, ast.IfExp):
            branches = [node.body, node.orelse]
        elif isinstance(node, ast.If):
            branches = list(node.body) + list(node.orelse)
        else:
            continue

        if not _contains_constant(node.test, ZH_LANGUAGE_CONSTANTS):
            continue

        if any(
            _contains_constant(branch, EN_LANGUAGE_CONSTANTS)
            for branch in branches
        ):
            hits.append(node.lineno)

    return hits


def _load_app_function(name, extra_globals):
    """
    从 app.py 抽取单个顶层函数并独立执行。

    ``import app`` 会立刻执行整个 Streamlit 页面，所以这里只编译目标函数
    节点，并用调用方注入的依赖（纯模块 + 假 st）执行它。
    """
    tree = ast.parse(APP_PATH.read_text(encoding="utf-8"))

    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )

    module = ast.Module(body=[node], type_ignores=[])
    namespace = dict(extra_globals)

    exec(
        compile(ast.fix_missing_locations(module), str(APP_PATH), "exec"),
        namespace,
    )

    return namespace[name]


def _app_language_rules():
    """
    app.py 的 Baseline / Daily Log 通用语言规则函数 + 它读取的会话状态。

    注入真实的 ``utils.content_locale`` 与 registry ``ai_language_name``，
    只有一个可写的假 ``st.session_state``。
    """
    state = {}

    function = _load_app_function(
        "ai_output_language_rules",
        {
            "content_locale": content_locale,
            "ai_language_name": locale_registry.ai_language_name,
            "st": types.SimpleNamespace(session_state=state),
        },
    )

    return function, state


def _promote_future_locales():
    """
    临时把 FUTURE_LOCALES 提升为可选语言，返回恢复现场的闭包。

    只在临时目录里写一份 manifest **副本**，并把 locale_registry 的模块级
    ``MANIFEST_PATH`` 指过去：真实 ``locales/manifest.json`` 与
    ``locales/*.json`` 全程不变，所以本阶段这些语言依旧不会出现在 UI，
    退出时恢复现场。
    """
    entries = json.loads(
        (ROOT / "locales" / "manifest.json").read_text(encoding="utf-8")
    )

    for entry in entries:
        if entry.get("code") in FUTURE_LOCALES:
            entry["status"] = "stable"
            entry["ui_enabled"] = True

    tmp = tempfile.TemporaryDirectory()
    path = pathlib.Path(tmp.name) / "manifest.json"
    path.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")

    original = locale_registry.MANIFEST_PATH
    locale_registry.MANIFEST_PATH = path
    locale_registry.reload_registry()

    def restore():
        locale_registry.MANIFEST_PATH = original
        locale_registry.reload_registry()
        tmp.cleanup()

    return restore


def test_l_registry_driven_language():
    print("\n--- L. 目标语言（registry 驱动）---")

    # L1-L3: 现有两种语言的行为保持不变。
    eq("L1 en 目标语言为 English", prompt_language_name(EN), "English")
    check("L2 zh-CN 目标语言为简体中文",
          prompt_language_name(ZH) == "Simplified Chinese",
          prompt_language_name(ZH))
    eq("L3 zh 别名同样解析为简体中文",
       prompt_language_name("zh"), prompt_language_name(ZH))
    check("L3 繁体 zh-TW 不静默变成简体",
          prompt_language_name("zh-TW") == "English")

    # L4: 非法 / 空 / 非字符串一律安全回落，且不抛异常。
    for bad in ("xx-YY", "", "   ", "und", "not a locale", 123, None,
                ["ja"], {"locale": "es"}, True):
        eq(f"L4 非法目标 {bad!r} 回落 English",
           prompt_language_name(bad), "English")

    # L5: 仍是 planned 的语言当前安全回落，且不得进入 UI。
    for code in FUTURE_LOCALES:
        eq(f"L5 {code} registry 状态为 planned",
           locale_registry.status(code), "planned")
        check(f"L5 {code} 不在 UI 可选集合",
              code not in locale_registry.enabled_ui_locales())
        eq(f"L5 {code} 当前回落 English",
           prompt_language_name(code), "English")

    # L6: 临时提升为可选语言后，语言名完全来自 registry。
    restore = _promote_future_locales()

    try:
        eq("L6 临时 registry 中 es 已可选",
           locale_registry.to_ui_locale("es"), "es")
        eq("L6 临时 registry 中 pt-BR 已可选",
           locale_registry.to_ui_locale("pt-BR"), "pt-BR")

        for code, expected in FUTURE_AI_NAMES.items():
            eq(f"L6 registry AI 名 {code} -> {expected}",
               locale_registry.ai_language_name(code), expected)
            eq(f"L6 目标语言 {code} -> {expected}",
               prompt_language_name(code), expected)

        eq("L6 en 仍为 English", prompt_language_name(EN), "English")
        eq("L6 zh-CN 仍为简体中文",
           prompt_language_name(ZH), "Simplified Chinese")
        eq("L6 非法值仍回落 English",
           prompt_language_name("xx-YY"), "English")

        # L7: 提示词正文使用目标语言，文本/数值约束不变。
        log = make_log()
        prompt = build_log_text_version_prompt(log, "pt-BR")

        check("L7 提示词写入 Brazilian Portuguese",
              "text value in Brazilian Portuguese." in prompt)
        check("L7 提示词不再写死简体中文",
              "Simplified Chinese" not in prompt)
        check("L7 JSON 字段名保持英文",
              "Keep every JSON field name in English" in prompt
              and '"summary"' in prompt
              and '"title"' in prompt
              and '"reason"' in prompt
              and '"suggestion"' in prompt)
        check("L7 风险数量不变", "exactly 1 risk objects" in prompt)
        check("L7 数值字段只作只读上下文且禁止输出",
              "severity=" in prompt
              and "likelihood=" in prompt
              and "Do NOT output level, severity, likelihood, exposure "
                  "or raw_risk" in prompt)
        check("L7 用户正文只作上下文",
              "do not translate it" in prompt)

        # L8: 同一份规则在 app.py 侧（Baseline 评估 + Daily Log 分析）跟随新语言。
        rules, state = _app_language_rules()

        state["ui_lang"] = "es"
        check("L8 app.py 规则写入 Spanish",
              "in Spanish." in rules())
    finally:
        restore()

    # L9: app.py 规则由 content locale 决定，不再读界面语言分支。
    rules, state = _app_language_rules()

    state["ui_lang"] = EN
    check("L9 en 规则要求 English", "in English." in rules())

    state["ui_lang"] = ZH
    text_zh = rules()
    check("L9 zh-CN 规则要求简体中文",
          "in Simplified Chinese." in text_zh)
    check("L9 JSON 字段名保持英文",
          "Keep all JSON field names exactly as specified in English."
          in text_zh)
    check("L9 风险等级关键字保持英文",
          '"LOW", "MEDIUM" or "HIGH"' in text_zh)

    state["ui_lang"] = "xx-YY"
    check("L9 非法界面语言回落 English", "in English." in rules())

    # L10: 内容语言 override 优先于界面语言，且规则只读。
    state.clear()
    state["ui_lang"] = EN
    state["content_locale"] = ZH

    check("L10 内容语言 override 生效",
          "in Simplified Chinese." in rules())
    eq("L10 规则不写 session state",
       sorted(state.keys()), ["content_locale", "ui_lang"])
    eq("L10 规则不写 content locale override 之外的状态",
       state["content_locale"], ZH)

    # L11: 静态守卫 —— 两处 AI 语言分支都不得再有二元判断。
    for path in LANGUAGE_SOURCES:
        eq(f"L11 {path.name} 无 zh-CN/English 二元语言分支",
           _binary_language_branches(path), [])

    app_rules = _function_node(
        ast.parse(APP_PATH.read_text(encoding="utf-8")),
        "ai_output_language_rules",
    )

    app_names = {
        getattr(node, "attr", None) or getattr(node, "id", None)
        for node in ast.walk(app_rules)
        if isinstance(node, (ast.Attribute, ast.Name))
    }

    check("L11 app.py 语言规则读取 content locale",
          "get_content_locale" in app_names and "content_locale" in app_names)
    check("L11 app.py 语言规则使用 registry AI 语言名",
          "ai_language_name" in app_names)
    check("L11 app.py 语言规则不再读界面语言",
          "get_current_language" not in app_names)

    helper = _function_node(
        ast.parse(MODULE_PATH.read_text(encoding="utf-8")),
        "prompt_language_name",
    )

    helper_names = {
        getattr(node, "attr", None) or getattr(node, "id", None)
        for node in ast.walk(helper)
        if isinstance(node, (ast.Attribute, ast.Name))
    }

    check("L11 纯 helper 规范化 locale", "to_ui_locale" in helper_names)
    check("L11 纯 helper 使用 registry AI 语言名",
          "ai_language_name" in helper_names)
    check("L11 纯 helper 无 locale 二元分支",
          not any(
              isinstance(node, ast.IfExp)
              for node in ast.walk(helper)
          ))


# ============================================================
# 主入口
# ============================================================

# ============================================================
# M. 用户内容（项目名 / 日志标题正文）多语言化
# ============================================================

#: 用户输入的原文：测试期间必须始终一字不变。
NAME_ORIGINAL = "Mzuri Estate"
NAME_ZH = "姆祖里庄园"
NAME_SW = "Shamba la Mzuri"

LOG_TITLE_ORIGINAL = "Crane inspection at the north tower"
LOG_DESCRIPTION_ORIGINAL = (
    "Tower crane inspected after the morning briefing. "
    "No structural damage found, the load chart was updated."
)
LOG_TITLE_ZH = "北塔塔吊检查"
LOG_DESCRIPTION_ZH = "早班会后检查了塔吊。未发现结构损伤，已更新载荷表。"


def make_user_text_project():
    return {
        "city": "Nairobi",
        "country": "Kenya",
        "type": "Housing Construction",
        "risk": "HIGH",
        "baseline_score": 58,
    }


def make_user_text_log():
    return {
        "log_id": "log-0001",
        "date": "2026-09-10",
        "title": LOG_TITLE_ORIGINAL,
        "description": LOG_DESCRIPTION_ORIGINAL,
        "severity": "MEDIUM",
        "ai_analysis": {
            "level": "MEDIUM",
            "score": 58,
            "severity": "MEDIUM",
            "risk_count": 1,
            "risks": [{"title": "Crane", "reason": "reason", "suggestion": "fix"}],
        },
    }


class _FakeRiskAgent:
    """最小 AI 客户端替身：记录调用次数，返回预置结果。"""

    calls = []
    response = None
    error = None

    def analyze(self, prompt):
        _FakeRiskAgent.calls.append(prompt)

        if _FakeRiskAgent.error is not None:
            raise _FakeRiskAgent.error

        return copy.deepcopy(_FakeRiskAgent.response)

    @classmethod
    def reset(cls, response=None, error=None):
        cls.calls = []
        cls.response = response
        cls.error = error


class _FakeAgentModule:
    """临时把 ``agents.risk_agent`` 换成替身，退出时恢复现场。"""

    def __enter__(self):
        fake = types.ModuleType("agents.risk_agent")
        fake.RiskAgent = _FakeRiskAgent

        self.previous = sys.modules.get("agents.risk_agent")
        sys.modules["agents.risk_agent"] = fake

        return _FakeRiskAgent

    def __exit__(self, *exc_info):
        if self.previous is None:
            sys.modules.pop("agents.risk_agent", None)
        else:
            sys.modules["agents.risk_agent"] = self.previous

        return False


def _load_user_text_ai_function(name):
    """抽取 app.py 的翻译函数：只注入纯依赖，AI 客户端由调用方替换。"""
    return _load_app_function(
        name,
        {
            "build_project_name_translation_prompt":
                ld.build_project_name_translation_prompt,
            "build_log_content_translation_prompt":
                ld.build_log_content_translation_prompt,
            "validate_project_name_translation":
                ld.validate_project_name_translation,
            "validate_log_content_translation":
                ld.validate_log_content_translation,
        },
    )


def _load_user_text_write_function():
    """抽取 app.py 的用户内容写入函数（纯模块保持只读，写操作在页面层）。"""
    return _load_app_function(
        "store_user_text_translation",
        {
            "USER_TEXT_KEY": ld.USER_TEXT_KEY,
            "merge_user_text_translation": ld.merge_user_text_translation,
        },
    )


class _NullCtx:
    """最小上下文管理器替身（st.spinner 等）。"""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _make_stub(events):
    """最小 st 替身：记录 error / rerun。"""
    return types.SimpleNamespace(
        spinner=lambda *args, **kwargs: _NullCtx(),
        error=lambda message: events["errors"].append(message),
        rerun=lambda: events.__setitem__("reruns", events["reruns"] + 1),
    )


def test_m_user_text_translation():
    print("\n--- M. 用户内容（项目名 / 日志标题正文）多语言化 ---")

    store = _load_user_text_write_function()

    # ------------------------------------------------
    # M1 项目名称显示规则
    # ------------------------------------------------
    project = make_user_text_project()

    eq("M1 无译文显示原文",
       ld.project_display_name(project, "zh-CN", fallback=NAME_ORIGINAL),
       NAME_ORIGINAL)

    check("M1 无译文时提示可翻译",
          ld.project_needs_translation(project, "zh-CN"))

    eq("M1 旧数据不猜测原文语言",
       ld.user_text_source_locale(project),
       "")

    check("M1 旧数据仍允许主动翻译",
          ld.project_needs_translation(project, "sw"))

    check("M1 项目名写入当前语言",
          store(project, "zh-CN", {"name": NAME_ZH},
                (ld.PROJECT_NAME_FIELD,)))

    eq("M1 当前语言显示译文",
       ld.project_display_name(project, "zh-CN", fallback=NAME_ORIGINAL),
       NAME_ZH)

    eq("M1 原文语言显示原文",
       ld.project_display_name(project, "en", fallback=NAME_ORIGINAL),
       NAME_ORIGINAL)

    eq("M1 其它语言不跨语言读取",
       ld.project_display_name(project, "fr", fallback=NAME_ORIGINAL),
       NAME_ORIGINAL)

    check("M1 有译文后不再提示",
          not ld.project_needs_translation(project, "zh-CN"))

    check("M1 其它语言仍提示",
          ld.project_needs_translation(project, "fr"))

    # source_locale 等于当前语言：原文即目标语言，不提示、不翻译
    sourced = {"source_locale": "sw", "city": "Nairobi"}

    check("M1 source_locale 语言不需翻译",
          not ld.project_needs_translation(sourced, "sw"))

    check("M1 其它语言仍需翻译",
          ld.project_needs_translation(sourced, "zh-CN"))

    eq("M1 source_locale 读取（非法值不猜测）",
       ld.user_text_source_locale({"source_locale": "xx-YY"}),
       "")

    # ------------------------------------------------
    # M2 日志标题 / 正文显示规则
    # ------------------------------------------------
    log = make_user_text_log()

    missing = ld.log_display_text(log, "zh-CN")

    eq("M2 缺译文显示原文标题", missing["title"], LOG_TITLE_ORIGINAL)
    eq("M2 缺译文显示原文本正文", missing["description"], LOG_DESCRIPTION_ORIGINAL)
    check("M2 缺译文标记需要翻译", missing["needs_translation"])
    check("M2 缺译文标记显示原文", missing["showing_original"])
    check("M2 缺译文时译文列表为空", missing["translated_fields"] == [])
    eq("M2 逐字段回退（两个字段都在原文列表）",
       sorted(missing["original_fields"]),
       ["description", "title"])

    check("M2 写入当前语言译文",
          store(
              log, "zh-CN", {"title": LOG_TITLE_ZH,
                             "description": LOG_DESCRIPTION_ZH}))

    translated = ld.log_display_text(log, "zh-CN")

    eq("M2 有译文显示译文标题", translated["title"], LOG_TITLE_ZH)
    eq("M2 有译文显示译文正文", translated["description"], LOG_DESCRIPTION_ZH)
    check("M2 有译文不再提示", not translated["needs_translation"])
    check("M2 有译文标记 translated", translated["translated"])

    sw_view = ld.log_display_text(log, "sw")

    eq("M2 其它语言不跨语言读取标题",
       sw_view["title"], LOG_TITLE_ORIGINAL)
    eq("M2 其它语言不跨语言读取正文",
       sw_view["description"], LOG_DESCRIPTION_ORIGINAL)
    check("M2 其它语言仍提示需要翻译", sw_view["needs_translation"])

    # 逐字段：只有标题有译文时，正文继续显示原文
    partial = make_user_text_log()
    store(partial, "zh-CN", {"title": LOG_TITLE_ZH})
    partial_view = ld.log_display_text(partial, "zh-CN")

    eq("M2 标题用译文", partial_view["title"], LOG_TITLE_ZH)
    eq("M2 正文回退原文", partial_view["description"], LOG_DESCRIPTION_ORIGINAL)
    check("M2 部分缺失仍提示", partial_view["needs_translation"])

    # source_locale == 当前语言：原文即目标语言，不提示
    original_sw = make_user_text_log()
    original_sw["source_locale"] = "sw"

    check("M2 source_locale 语言不提示",
          not ld.log_display_text(original_sw, "sw")["needs_translation"])
    check("M2 其它语言仍提示",
          ld.log_display_text(original_sw, "zh-CN")["needs_translation"])

    # ------------------------------------------------
    # M3 写入隔离：原文、数值、其它语言一律不动
    # ------------------------------------------------
    isolated = make_user_text_log()
    before = dump(isolated)

    store(
        isolated, "zh-CN",
        {"title": LOG_TITLE_ZH, "description": LOG_DESCRIPTION_ZH})

    eq("M3 原文标题不变", isolated["title"], LOG_TITLE_ORIGINAL)
    eq("M3 原文正文不变", isolated["description"], LOG_DESCRIPTION_ORIGINAL)

    for field in ("date", "severity", "log_id"):
        eq(f"M3 {field} 不变", isolated[field], json.loads(before)[field])

    eq("M3 AI 风险分析不变",
       isolated["ai_analysis"], json.loads(before)["ai_analysis"])

    check("M3 只写入当前语言",
          sorted(ld.user_text_container(isolated)) == ["zh-CN"],
          sorted(ld.user_text_container(isolated)))

    store(isolated, "sw", {"title": NAME_SW})

    eq("M3 追加语言不覆盖已有语言",
       ld.user_text_value(isolated, "title", "zh-CN"),
       LOG_TITLE_ZH)

    check("M3 两个语言各自独立",
          sorted(ld.user_text_container(isolated)) == ["sw", "zh-CN"])

    eq("M3 写入语言不影响原文",
       isolated["title"], LOG_TITLE_ORIGINAL)

    # 旧数据不会被自动迁移
    legacy = {"title": "Legacy", "description": "Old"}
    store(legacy, "zh-CN", {"title": LOG_TITLE_ZH})

    check("M3 不自动写入 source_locale",
          "source_locale" not in legacy)

    # ------------------------------------------------
    # M4 校验：非法输入绝不写入
    # ------------------------------------------------
    guarded = make_user_text_log()
    snapshot = dump(guarded)

    rejections = {
        "空标题": {"title": "   "},
        "错误类型": {"title": 123},
        "超长标题": {"title": "x" * (ld.MAX_LOG_TITLE_LENGTH + 1)},
        "多行标题": {"title": "a\nb"},
        "超长正文": {"description": "y" * (ld.MAX_LOG_DESCRIPTION_LENGTH + 1)},
    }

    for label, values in rejections.items():
        check(f"M4 拒绝写入：{label}",
              store(guarded, "zh-CN", values) is False)

    eq("M4 拒绝后原数据不变", dump(guarded), snapshot)

    check("M4 非法 locale 拒绝写入",
          store(guarded, "xx-YY", {"title": "x"}) is False)

    check("M4 非法 locale 不留痕迹",
          "xx-YY" not in ld.user_text_container(guarded))

    check("M4 项目名同样拒绝超长",
          ld.validate_project_name_translation(
              {"name": "x" * (ld.MAX_PROJECT_NAME_LENGTH + 1)}) == {})

    check("M4 项目名校验去除首尾空格",
          ld.validate_project_name_translation({"name": f"  {NAME_ZH}  "})
          == {"name": NAME_ZH})

    check("M4 项目名拒绝非字典",
          ld.validate_project_name_translation("not a dict") == {})

    check("M4 日志校验拒绝非字符串字段",
          ld.validate_log_content_translation({"title": 5, "description": "d"})
          == {})

    check("M4 日志校验拒绝两字段全空",
          ld.validate_log_content_translation({"title": "", "description": ""})
          == {})

    check("M4 日志校验保留空字段（原文为空）",
          ld.validate_log_content_translation(
              {"title": "", "description": LOG_DESCRIPTION_ZH})
          == {"title": "", "description": LOG_DESCRIPTION_ZH})

    # ------------------------------------------------
    # M5 提示词：只翻译，不带风险数值
    # ------------------------------------------------
    name_prompt = ld.build_project_name_translation_prompt(
        make_user_text_project(), NAME_ORIGINAL, "zh-CN")

    check("M5 项目名提示词含原文", NAME_ORIGINAL in name_prompt)
    check("M5 项目名提示词含目标语言",
          locale_registry.ai_language_name("zh-CN") in name_prompt)
    check("M5 项目名提示词要求 JSON 契约", '"name"' in name_prompt)
    check("M5 项目名提示词禁止输出其它字段",
          "Do NOT output a score, level, date" in name_prompt)
    check("M5 项目名提示词不含风险数值", "58" not in name_prompt)

    log_prompt = ld.build_log_content_translation_prompt(
        make_user_text_log(), "sw")

    check("M5 日志提示词含原文标题", LOG_TITLE_ORIGINAL in log_prompt)
    check("M5 日志提示词含原文正文", LOG_DESCRIPTION_ORIGINAL in log_prompt)
    check("M5 日志提示词含目标语言",
          locale_registry.ai_language_name("sw") in log_prompt)
    check("M5 日志提示词要求两个字段",
          '"title"' in log_prompt and '"description"' in log_prompt)
    check("M5 日志提示词禁止输出风险数值",
          "Do NOT output a date, score, level, severity" in log_prompt)
    check("M5 日志提示词不含风险分数", "58/100" not in log_prompt)

    # ------------------------------------------------
    # M6 控件 key：绑定项目、日志与语言
    # ------------------------------------------------
    key_zh = ld.user_text_widget_key("translate_log", "Mzuri Estate", "log-0001",
                                     "zh-CN")
    key_sw = ld.user_text_widget_key("translate_log", "Mzuri Estate", "log-0001",
                                     "sw")
    key_other_log = ld.user_text_widget_key("translate_log", "Mzuri Estate",
                                           "log-0002", "zh-CN")
    key_other_project = ld.user_text_widget_key("translate_log", "Other Estate",
                                                "log-0001", "zh-CN")

    for token in ("Mzuri Estate", "log-0001", "zh-CN"):
        check(f"M6 key 含 {token}", token in key_zh)

    check("M6 不同语言 key 不同", key_zh != key_sw)
    check("M6 不同日志 key 不同", key_zh != key_other_log)
    check("M6 不同项目 key 不同", key_zh != key_other_project)
    check("M6 key 稳定",
          key_zh == ld.user_text_widget_key("translate_log", "Mzuri Estate",
                                            "log-0001", "zh-CN"))

    # ------------------------------------------------
    # M7 app 层：翻译函数（mock AI）
    # ------------------------------------------------
    app_src = APP_PATH.read_text(encoding="utf-8")
    app_tree = ast.parse(app_src)

    with _FakeAgentModule() as fake:

        translate_name = _load_user_text_ai_function("ai_translate_project_name")
        translate_log = _load_user_text_ai_function("ai_translate_log_content")

        # 项目名：恰好 1 次 AI 调用，返回值经过校验
        fake.reset({"name": f"  {NAME_ZH}  "})
        name_payload = translate_name(make_user_text_project(), "zh-CN",
                                      NAME_ORIGINAL)

        eq("M7 项目名翻译走校验", name_payload, {"name": NAME_ZH})
        eq("M7 项目名翻译恰好 1 次 AI 调用", len(fake.calls), 1)
        check("M7 项目名提示词带原文", NAME_ORIGINAL in fake.calls[0])

        # 日志：恰好 1 次 AI 调用
        fake.reset({"title": LOG_TITLE_ZH, "description": LOG_DESCRIPTION_ZH})
        log_payload = translate_log(make_user_text_log(), "zh-CN")

        eq("M7 日志翻译走校验", log_payload,
           {"title": LOG_TITLE_ZH, "description": LOG_DESCRIPTION_ZH})
        eq("M7 日志翻译恰好 1 次 AI 调用", len(fake.calls), 1)
        check("M7 日志提示词带原文", LOG_TITLE_ORIGINAL in fake.calls[0])

        # 失败与非法返回：返回空，绝不写空数据
        fake.reset({"name": ""})
        eq("M7 空项目名返回空", translate_name(make_user_text_project(),
                                               "zh-CN", NAME_ORIGINAL), {})

        fake.reset({"title": "a" * 400, "description": "d"})
        eq("M7 超长标题返回空", translate_log(make_user_text_log(), "zh-CN"), {})

        fake.reset("not a dict")
        eq("M7 非字典返回空", translate_name(make_user_text_project(), "zh-CN",
                                             NAME_ORIGINAL), {})

        fake.reset(None, error=RuntimeError("ai down"))
        eq("M7 AI 异常返回空", translate_name(make_user_text_project(), "zh-CN",
                                              NAME_ORIGINAL), {})
        eq("M7 AI 异常时日志同样返回空",
           translate_log(make_user_text_log(), "zh-CN"), {})

        # 端到端写入：AI 返回值 + 写入函数，只影响当前语言
        target_log = make_user_text_log()
        fake.reset({"title": LOG_TITLE_ZH, "description": LOG_DESCRIPTION_ZH})

        payload = translate_log(target_log, "zh-CN")
        check("M7 写入当前语言译文",
              store(target_log, "zh-CN", payload))

        eq("M7 写入后显示译文",
           ld.log_display_text(target_log, "zh-CN")["title"], LOG_TITLE_ZH)
        eq("M7 原文仍然不变", target_log["title"], LOG_TITLE_ORIGINAL)
        eq("M7 风险分数仍然不变",
           target_log["ai_analysis"]["score"], 58)
        eq("M7 只写当前语言",
           sorted(ld.user_text_container(target_log)), ["zh-CN"])

        # 按钮动作：点击一次 = 恰好 1 次 AI 调用 + 只写当前语言 + persist
        events = {"persist": 0, "errors": [], "reruns": 0}

        def reset_events():
            events["persist"] = 0
            events["errors"] = []
            events["reruns"] = 0

        st_stub = _make_stub(events)

        name_action = _load_app_function(
            "translate_project_name_action",
            {
                "st": st_stub,
                "t": lambda key, **kwargs: key,
                "get_current_language": lambda: "zh-CN",
                "persist": lambda: events.__setitem__(
                    "persist", events["persist"] + 1),
                "ai_translate_project_name": translate_name,
                "store_user_text_translation": store,
                "PROJECT_NAME_FIELD": ld.PROJECT_NAME_FIELD,
            },
        )

        action_project = make_user_text_project()
        fake.reset({"name": NAME_ZH})

        name_action(NAME_ORIGINAL, action_project)

        eq("M7 点击按钮恰好 1 次 AI 调用", len(fake.calls), 1)
        eq("M7 点击按钮写入当前语言",
           ld.project_display_name(action_project, "zh-CN",
                                   fallback=NAME_ORIGINAL), NAME_ZH)
        eq("M7 点击按钮只 persist 一次", events["persist"], 1)
        eq("M7 点击按钮只 rerun 一次", events["reruns"], 1)
        eq("M7 点击按钮无错误提示", events["errors"], [])
        check("M7 点击按钮不动英文原文",
              ld.project_display_name(action_project, "en",
                                      fallback=NAME_ORIGINAL) == NAME_ORIGINAL)

        # 失败：不写、不 persist、提示是本地化 key
        reset_events()
        failing = make_user_text_project()
        fake.reset(None, error=RuntimeError("ai down"))

        name_action(NAME_ORIGINAL, failing)

        check("M7 AI 失败不写入译文", ld.user_text_container(failing) == {})
        eq("M7 AI 失败不 persist", events["persist"], 0)
        eq("M7 AI 失败只提示一次", events["errors"],
           ["dashboard.error_project_name_translation_failed"])
        check("M7 AI 失败后仍显示原文",
              ld.project_display_name(failing, "zh-CN",
                                      fallback=NAME_ORIGINAL) == NAME_ORIGINAL)

        # 日志按钮动作：同样恰好 1 次调用，数值字段一律不动
        log_action = _load_app_function(
            "translate_log_content_action",
            {
                "st": st_stub,
                "t": lambda key, **kwargs: key,
                "get_current_language": lambda: "zh-CN",
                "persist": lambda: events.__setitem__(
                    "persist", events["persist"] + 1),
                "ai_translate_log_content": translate_log,
                "store_user_text_translation": store,
                "LOG_USER_TEXT_FIELDS": ld.LOG_USER_TEXT_FIELDS,
            },
        )

        reset_events()
        action_log = make_user_text_log()
        fake.reset({"title": LOG_TITLE_ZH, "description": LOG_DESCRIPTION_ZH})

        log_action(None, action_log)

        eq("M7 日志按钮恰好 1 次 AI 调用", len(fake.calls), 1)
        eq("M7 日志按钮写入译文标题",
           ld.log_display_text(action_log, "zh-CN")["title"], LOG_TITLE_ZH)
        eq("M7 日志按钮不改原文标题", action_log["title"], LOG_TITLE_ORIGINAL)
        eq("M7 日志按钮不改原文正文", action_log["description"],
           LOG_DESCRIPTION_ORIGINAL)
        eq("M7 日志按钮不改日期", action_log["date"], "2026-09-10")
        eq("M7 日志按钮不改等级/分数",
           (action_log["severity"], action_log["ai_analysis"]["score"]),
           ("MEDIUM", 58))
        eq("M7 日志按钮只 persist 一次", events["persist"], 1)

    # M8 静态守卫：翻译只能由按钮触发，显示路径不调用 AI
    def _has_button_call(node):
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr in ("button", "form_submit_button")
            ):
                return True
        return False

    class _UserTextAiGuard(ast.NodeVisitor):
        """用户内容翻译入口必须位于 ``if st.button(...)`` 分支内。"""

        targets = (
            "translate_project_name_action",
            "translate_log_content_action",
        )

        def __init__(self):
            self.stack = []
            self.calls = []
            self.unguarded = []

        def visit_If(self, node):
            self.stack.append(_has_button_call(node.test))

            for child in node.body:
                self.visit(child)

            self.stack.pop()

            for child in node.orelse:
                self.visit(child)

        def visit_Call(self, node):
            target = getattr(node.func, "id", None)

            if target in self.targets:
                self.calls.append((target, node.lineno))

                if not any(self.stack):
                    self.unguarded.append((target, node.lineno))

            self.generic_visit(node)

    user_text_guard = _UserTextAiGuard()
    user_text_guard.visit(app_tree)

    eq("M8 用户内容翻译入口无未受保护的调用",
       user_text_guard.unguarded, [])

    for function_name in _UserTextAiGuard.targets:
        check(f"M8 {function_name} 仅在按钮分支调用",
              any(target == function_name
                  for target, _ in user_text_guard.calls))

    # AI 只能被按钮动作包装函数调用：定义 1 次 + 调用 1 次
    ai_wrappers = {
        "ai_translate_project_name": "translate_project_name_action",
        "ai_translate_log_content": "translate_log_content_action",
    }

    for function_name, wrapper in ai_wrappers.items():
        eq(f"M8 {function_name} 只在包装函数内被调用",
           app_src.count(f"{function_name}("), 2)

        wrapper_source = ast.get_source_segment(
            app_src, _function_node(app_tree, wrapper))

        check(f"M8 {function_name} 由 {wrapper} 调用",
              f"{function_name}(" in wrapper_source)
        check(f"M8 {wrapper} 使用当前语言",
              "get_current_language()" in wrapper_source)
        check(f"M8 {wrapper} 只写当前语言",
              "locale," in wrapper_source)
        check(f"M8 {wrapper} 失败时不 persist",
              wrapper_source.index("return") < wrapper_source.index("persist()"))

    for function_name in ("ai_translate_project_name", "ai_translate_log_content"):
        node = _function_node(app_tree, function_name)
        targets = _assigned_targets(node)

        for token in ("level", "score", "severity", "likelihood", "exposure",
                      "raw_risk", "risk_count", "ai_analysis", "date",
                      "log_id", "day_risk"):
            check(f"M8 {function_name} 不写数值字段 {token}",
                  token not in targets)

        source = ast.get_source_segment(app_src, node)

        check(f"M8 {function_name} 不引用 st.session_state",
              "session_state" not in source)
        check(f"M8 {function_name} 只调用校验并返回",
              "validate_" in source)

    # 显示层纯读：渲染路径不出现翻译调用
    for function_name in ("render_project_card",):
        source = ast.get_source_segment(app_src, _function_node(app_tree,
                                                               function_name))
        check(f"M8 {function_name} 渲染时不调用 AI",
              "ai_translate_" not in source)

    # ------------------------------------------------
    # M9 覆盖范围与身份不变（静态检查）
    # ------------------------------------------------
    card_source = ast.get_source_segment(
        app_src, _function_node(app_tree, "render_project_card"))

    check("M9 项目卡使用 project_display_name",
          "project_display_name(" in card_source)
    check("M9 项目卡操作 key 仍用原始项目 id",
          "key=f\"star_{name}\"" in card_source)
    check("M9 项目卡翻译按钮 key 绑定项目与语言",
          "user_text_widget_key(" in card_source)

    check("M9 项目选择器只翻译标签",
          "format_func=lambda name: project_display_name(" in app_src
          and "list(projects.keys())" in app_src)
    check("M9 选择器仍写入原始 id",
          "st.session_state.selected_project = selected" in app_src)

    check("M9 Dashboard 标题使用译文",
          "st.title(\n        f'🏗️ {project_display_name(" in app_src)

    history_source = ast.get_source_segment(
        app_src, _function_node(app_tree, "history_page"))

    check("M9 History 复用同一张卡（译文一致）",
          "render_project_card(" in history_source)

    check("M9 地图 tooltip 使用译文",
          '"display_name": project_display_name(' in app_src)

    export_source = ast.get_source_segment(
        app_src, _function_node(app_tree, "export_report_to_word"))

    check("M9 报告导出按 report locale 取项目名",
          "project_display_name(" in export_source
          and "locale_info[\"valid\"]" in export_source)
    check("M9 报告导出保留原标题回退",
          "project_id" in export_source
          and 'project.get(\n            "name",' in export_source)
    check("M9 报告导出仍写入原始项目身份",
          "project_id=project_id," in app_src)

    start = app_src.index('    if view == "logs":')
    end = app_src.index('    if view == "daily":')
    logs_block = app_src[start:end]

    check("M9 日志卡使用 log_display_text",
          "user_text = log_display_text(" in logs_block)
    check("M9 日志卡显示译文标题", 'user_text["title"]' in logs_block)
    check("M9 日志卡显示译文本正文", 'user_text["description"]' in logs_block)
    check("M9 缺少译文时给出本地化提示",
          't("dashboard.info_user_text_original")' in logs_block)
    check("M9 缺少译文时提供翻译按钮",
          't("dashboard.button_translate_log")' in logs_block)
    check("M9 译文写入只使用当前语言",
          "store_user_text_translation(\n                                        log,\n                                        ui_locale," in logs_block)
    check("M9 编辑译文有独立入口",
          't("dashboard.expander_edit_translation")' in logs_block)
    check("M9 编辑表单仍编辑原文",
          'value=editing.get("title", "") if editing else ""' in logs_block
          and 'value=editing.get("description", "") if editing else ""' in logs_block)
    check("M9 新建日志记录 source_locale",
          '"source_locale": content_locale.get_content_locale(' in logs_block)
    check("M9 新建项目记录 source_locale",
          app_src.count('"source_locale": content_locale.get_content_locale(') == 2)


def main():

    print("=" * 58)
    print("RiskPilot Daily Log 显示层语言回归测试")
    print("=" * 58)

    test_a_current_language_archive_wins()
    test_b_english_only_legacy()
    test_c_legacy_matches_current_language()
    test_d_legacy_without_language_field()
    test_e_generate_other_language_archive()
    test_f_numeric_fields_isolated()
    test_g_daily_risk_summary_gate()
    test_h_empty_text()
    test_i_validation()
    test_j_static_guards()
    test_k_unified_protocol()
    test_l_registry_driven_language()
    test_m_user_text_translation()

    total = _PASSED + len(_FAILED)

    print("\n" + "=" * 58)
    print(f"结果：{_PASSED}/{total} 通过")

    if _FAILED:

        print("失败项：")

        for name in _FAILED:
            print(f"  - {name}")

        raise SystemExit(1)

    print("Daily Log 显示层语言回归测试全部通过 ✅")


if __name__ == "__main__":

    main()
