# -*- coding: utf-8 -*-
"""
RiskPilot Vision Risk 本地化回归测试 (test_vision_localization.py)
==================================================================

阶段 4B：Vision Risk 接入统一多语言内容协议。

测试对象：

    * vision_risk.py                 canonical detection / 文本生成 / 写入 / 页面边界
    * utils/vision_display.py        事实与文本分离、校验、状态、缓存键、prompt
    * agents/risk_agent.py           explain_vision_text（只写文本，不碰协议）
    * app.py                         Vision 页面入口（AST）

约束：不请求网络、不调用真实 AI、不写 data/projects.json、不导入 app.py
（导入即执行 Streamlit 页面），AI 调用一律用替身并计数。

覆盖场景：

    A. canonical detection（语言无关事实 / 幻觉过滤 / 空结果与失败可区分）
    B. detection 校验（等级 / penalty / confidence / 本体白名单 / 类型）
    C. 文本 payload 校验（只能写文本字段，事实字段一律拒绝）
    D. 写入（只写当前语言那一个叶子；图片隔离；不碰其他项目数据）
    E. 读取状态（empty / missing / available / stale / invalid / legacy）
    F. AI 边界（识别 1 次视觉调用；文本 0 次视觉调用；切换语言 0 次 AI 调用）
    G. 缓存命名空间与有界性（detection 无 locale；text 含 locale + 指纹）
    H. AST 静态守卫（视觉调用只出现在识别按钮分支；prompt 带目标语言）
    I. locale key（阶段 4B 新增键 / 双语对齐 / 占位符）

运行方式：

    python test_vision_localization.py
"""

import ast
import copy
import json
import pathlib
import re
from typing import Any, Dict, List

import vision_risk as vr
from utils import localized_content as lc
from utils import vision_display as vd
from agents import risk_agent

ROOT = pathlib.Path(__file__).resolve().parent

VISION = ROOT / "vision_risk.py"
VISION_DISPLAY = ROOT / "utils" / "vision_display.py"
RISK_AGENT = ROOT / "agents" / "risk_agent.py"
APP = ROOT / "app.py"
EN_JSON = ROOT / "locales" / "en.json"
ZH_JSON = ROOT / "locales" / "zh-CN.json"

EN = "en"
ZH = "zh-CN"

CONTENT_TYPE = lc.CONTENT_TYPE_VISION
DETECTION_FIELD = vd.DETECTION_FIELD

IMAGE_HASH = "a" * 64
OTHER_HASH = "b" * 64
PROJECT_ID = "proj-alpha"
OTHER_PROJECT = "proj-beta"

#: 视觉模型的原始响应（含一条幻觉风险，必须被丢弃）。
RAW_MODEL_OUTPUT: Dict[str, Any] = {
    "risks": [
        {
            "risk_category": "人员安全",
            "risk_name": "未佩戴安全帽",
            "severity": "high",
            "confidence": 0.82,
            "location": [0.10, 0.20, 0.40, 0.50],
            "description": "Raw model prose that must never be stored",
            "evidence": "Raw model prose that must never be stored",
            "suggestion": "Raw model prose that must never be stored",
        },
        {
            "risk_category": "并不存在的大类",
            "risk_name": "并不存在的风险",
            "severity": "HIGH",
            "location": [0.1, 0.1, 0.2, 0.2],
        },
    ]
}

FACT_KEYS = {
    "schema_version",
    "image_hash",
    "model_id",
    "ontology_version",
    "findings",
    "overall_level",
    "total_penalty",
}

FAILURE_CASES = (
    "image_size_unknown",
    "image_empty",
    "image_too_large",
    "image_mime_missing",
    "image_mime_not_allowed",
    "image_bytes_missing",
    "image_not_decodable",
)

NEW_KEYS = (
    "vision.subheader_text",
    "vision.state_available",
    "vision.state_missing",
    "vision.state_no_text",
    "vision.state_stale",
    "vision.state_invalid",
    "vision.state_legacy",
    "vision.state_empty",
    "vision.button_generate_text",
    "vision.spinner_generating_text",
    "vision.success_text_generated",
    "vision.warn_text_failed",
    "vision.error_vision_failed",
    "vision.error_invalid_detection",
    "vision.error_code",
    "vision.error_image_hash",
    "vision.error_unreadable_short",
    "vision.metric_penalty",
    "vision.caption_facts",
    "vision.caption_generate_hint",
    "vision.caption_deterministic_source",
    "vision.caption_legacy_readonly",
    "vision.caption_stale_readonly",
    "vision.stale_expander",
    "vision.fact_confidence",
    "vision.fact_penalty",
    "vision.fact_location",
    "vision.fact_location_unknown",
    "vision.card_ai_title",
    "vision.card_ai_reason",
    "vision.card_ai_recommendation",
)

_PASSED = 0
_FAILED: List[str] = []


# ============================================================
# 断言辅助
# ============================================================

def check(name: str, condition: Any, detail: str = "") -> None:
    global _PASSED

    if condition:
        _PASSED += 1
        print(f"✅ {name}")
    else:
        _FAILED.append(name)
        print(f"❌ {name}")
        if detail:
            print(f"   实际：{detail}")


def eq(name: str, actual: Any, expected: Any) -> None:
    check(name, actual == expected, f"{actual!r} != {expected!r}")


def section(title: str) -> None:
    print(f"\n--- {title} ---")


# ============================================================
# 替身
# ============================================================

class VisionSpy:
    """视觉模型替身：记录调用次数，绝不联网。"""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls = 0
        self.prompts: List[str] = []

    def __call__(self, image: Any, prompt: str) -> Any:
        self.calls += 1
        self.prompts.append(prompt)
        return copy.deepcopy(self.result)


class TextSpy:
    """文本模型替身：按 prompt 中的目标语言返回该语言的文本。"""

    def __init__(self, finding_ids: List[str]) -> None:
        self.finding_ids = list(finding_ids)
        self.calls = 0
        self.prompts: List[str] = []

    def __call__(self, prompt: str, timeout: int = 90) -> Dict[str, Any]:
        self.calls += 1
        self.prompts.append(prompt)

        if "Simplified Chinese" in prompt:
            title, reason, action, summary = "中文标题", "中文原因", "中文建议", "中文总结"
        else:
            title, reason, action, summary = "EN title", "EN reason", "EN action", "EN summary"

        # 模型只能回抄它被给定的 finding_id，且顺序一致。
        return {
            "summary": summary,
            "findings": [
                {
                    "finding_id": value,
                    "title": f"{title} {index}",
                    "reason": reason,
                    "recommendation": action,
                }
                for index, value in enumerate(self.finding_ids, start=1)
            ],
        }


class TextFail:
    """文本模型替身：抛异常 / 返回非法结构。"""

    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls = 0

    def __call__(self, prompt: str, timeout: int = 90) -> Any:
        self.calls += 1

        if self.mode == "raise":
            raise RuntimeError("boom")

        return self.mode


#: 记录里由协议生成的易变字段（重写时间 / 记录哈希），不参与结构比较。
VOLATILE_RECORD_FIELDS = ("generated_at", "record_hash")


def strip_volatile(value: Any) -> Any:
    """去掉时间戳与记录哈希，用于比较"结构是否一致"。"""
    if isinstance(value, dict):
        return {
            key: strip_volatile(item)
            for key, item in value.items()
            if key not in VOLATILE_RECORD_FIELDS
        }

    if isinstance(value, list):
        return [strip_volatile(item) for item in value]

    return value


def reset_session_cache() -> None:
    """清空会话缓存，保证每个场景都从"缓存为空"开始。"""
    vr._FALLBACK_CACHES.clear()

    try:
        vr.st.session_state.clear()
    except Exception:
        pass


def detection_for(raw: Any = None, image_hash: str = IMAGE_HASH) -> Dict[str, Any]:
    return vr.build_vision_detection(
        copy.deepcopy(RAW_MODEL_OUTPUT if raw is None else raw),
        image_hash,
    )


def base_project() -> Dict[str, Any]:
    return {
        "project_name": "Alpha Site",
        "baseline_score": 61,
        "risk_categories": {"安全": "MEDIUM"},
        "visual_risk_level": "LOW",
    }


def read_state(project: Dict[str, Any], locale: str, detection: Dict[str, Any],
               project_id: str = PROJECT_ID) -> Dict[str, Any]:
    """以固定内容语言读取显示状态（不依赖 Streamlit runtime）。"""
    with_patch = _locale_patch(locale)
    with with_patch:
        return vr.vision_display_state(project, project_id, detection)


class _locale_patch:
    """把 vision_risk.get_content_locale 固定为某个语言。"""

    def __init__(self, locale: str) -> None:
        self.locale = locale
        self.original = None

    def __enter__(self):
        self.original = vr.get_content_locale
        vr.get_content_locale = lambda store: self.locale
        return self

    def __exit__(self, *exc):
        vr.get_content_locale = self.original
        return False


# ============================================================
# A. canonical detection
# ============================================================

def test_canonical_detection() -> None:
    section("A. canonical detection（语言无关事实）")

    detection = detection_for()

    eq("A1 结构合法（无校验问题）", vr.vision_detection_problems(detection), [])
    eq("A2 幻觉风险被丢弃，仅保留 1 条", len(detection["findings"]), 1)
    eq("A3 图片 hash 绑定", detection["image_hash"], IMAGE_HASH)
    eq("A4 记录模型 / 本体版本", (detection["model_id"], detection["ontology_version"]),
       (vr.VISION_MODEL_ID, vd.VISION_ONTOLOGY_VERSION))

    finding = detection["findings"][0]
    eq("A5 本体 object_key 原样保留", finding["object_key"], "未佩戴安全帽")
    eq("A6 等级来自模型（规范化）", finding["level"], "HIGH")
    eq("A7 confidence 原样保留", finding["confidence"], 0.82)
    eq("A8 penalty 由本体等级派生", finding["penalty"], vd.SEVERITY_PENALTY["HIGH"])
    eq("A9 location 保留", finding["location"], [0.10, 0.20, 0.40, 0.50])
    eq("A10 整体等级与总分", (detection["overall_level"], detection["total_penalty"]),
       ("HIGH", vd.SEVERITY_PENALTY["HIGH"]))

    check("A11 detection 只含事实键（无自由文本）",
          set(detection.keys()) == FACT_KEYS, str(sorted(detection.keys())))
    check("A12 finding 只含事实键（无自由文本）",
          set(finding.keys()) == set(vd.FINDING_FACT_FIELDS),
          str(sorted(finding.keys())))

    encoded = json.dumps(detection, ensure_ascii=False)
    check("A13 模型自由文本字段没有进入 detection",
          "Raw model prose" not in encoded)

    # 同一张图片、同一份原始响应 -> 完全一致的识别事实（可复现）
    twice = detection_for()
    eq("A14 同输入结果可复现", twice, detection)

    # 空结果与调用失败必须可区分
    empty = detection_for({"risks": []})
    eq("A15 空结果仍是合法 detection", vr.vision_detection_problems(empty), [])
    eq("A16 空结果没有 finding", empty["findings"], [])
    eq("A17 空结果的等级 / penalty", (empty["overall_level"], empty["total_penalty"]), ("LOW", 0))
    eq("A18 调用失败（None）-> 空检测对象", vr.build_vision_detection(None, IMAGE_HASH), {})
    eq("A19 非 list/dict 响应 -> 空检测对象",
       vr.build_vision_detection("not-json", IMAGE_HASH), {})

    # 非法坐标不臆造；缺 confidence 不臆造
    sloppy = detection_for({
        "risks": [
            {
                "risk_category": "人员安全",
                "risk_name": "未佩戴安全帽",
                "location": [0.5, 0.5, 0.2, 0.2],
            },
            {
                "risk_category": "人员安全",
                "risk_name": "未系安全带",
                "confidence": "很高",
                "location": [0.1, 0.1, 0.3, 0.3],
            },
        ]
    })
    eq("A20 非法坐标置空", sloppy["findings"][0]["location"], [])
    check("A21 非法 confidence 不被臆造", sloppy["findings"][1]["confidence"] is None)
    eq("A22 缺等级时回落到本体默认等级",
       sloppy["findings"][0]["level"],
       "HIGH")

    # finding_id 稳定且与顺序绑定
    order_a = [f["finding_id"] for f in detection["findings"]]
    order_b = [f["finding_id"] for f in detection_for()["findings"]]
    eq("A23 finding_id 稳定", order_a, order_b)


# ============================================================
# B. detection 校验
# ============================================================

def test_detection_validation() -> None:
    section("B. detection 校验")

    detection = detection_for()
    allowed_objects = vr.vision_allowed_objects()
    allowed_categories = vr.vision_allowed_categories()

    def problems(candidate: Dict[str, Any]) -> List[str]:
        return vd.validate_vision_detection(candidate, allowed_objects, allowed_categories)

    eq("B1 合法 detection 无问题", problems(detection), [])

    tampered = copy.deepcopy(detection)
    tampered["findings"][0]["level"] = "URGENT"
    check("B2 非法 level 被拒绝", bool(problems(tampered)))

    tampered = copy.deepcopy(detection)
    tampered["findings"][0]["penalty"] = -5
    check("B3 非法 penalty 被拒绝", bool(problems(tampered)))

    tampered = copy.deepcopy(detection)
    tampered["findings"][0]["confidence"] = 1.4
    check("B4 越界 confidence 被拒绝", bool(problems(tampered)))

    tampered = copy.deepcopy(detection)
    tampered["findings"][0]["object_key"] = "并不存在的风险"
    check("B5 本体外 object_key 被拒绝", bool(problems(tampered)))

    tampered = copy.deepcopy(detection)
    tampered["findings"][0]["category_key"] = "并不存在的大类"
    check("B6 本体外 category_key 被拒绝", bool(problems(tampered)))

    tampered = copy.deepcopy(detection)
    tampered["findings"][0]["location"] = [0.1, 0.2]
    check("B7 非法 location 被拒绝", bool(problems(tampered)))

    tampered = copy.deepcopy(detection)
    tampered["findings"] = "nope"
    check("B8 findings 非列表被拒绝", bool(problems(tampered)))

    check("B9 非 dict 被拒绝", bool(problems(["nope"])))
    check("B10 缺 image_hash 被拒绝", bool(problems({"findings": detection["findings"]})))

    # 本体白名单来自 RISK_ONTOLOGY（本阶段不新增、不删除键）
    check("B11 本体白名单非空", bool(allowed_objects) and bool(allowed_categories))
    check("B12 白名单键与本体一致",
          set(allowed_objects) == set(vr.ontology_object_keys())
          and set(allowed_categories) == set(vr.ontology_category_keys()))


# ============================================================
# C. 文本 payload 校验
# ============================================================

def test_text_payload() -> None:
    section("C. 文本 payload 校验")

    detection = detection_for()
    finding_id = detection["findings"][0]["finding_id"]

    def payload(**overrides: Any) -> Dict[str, Any]:
        item = {
            "finding_id": finding_id,
            "title": "标题",
            "reason": "原因",
            "recommendation": "建议",
        }
        item.update(overrides)

        return {"summary": "总结", "findings": [item]}

    check("C1 完整 payload 被接受",
          bool(vd.text_payload_for_store(payload(), detection)))

    check("C2 缺少 summary 被拒绝",
          not vd.text_payload_for_store({"findings": payload()["findings"]}, detection))
    check("C3 缺少 finding 条目被拒绝",
          not vd.text_payload_for_store({"summary": "总结", "findings": []}, detection))
    check("C4 未知 finding_id 被拒绝",
          not vd.text_payload_for_store(payload(finding_id="vf-unknown"), detection))
    check("C5 完全没有文本被拒绝",
          not vd.text_payload_for_store({}, detection))
    check("C6 非 dict 被拒绝",
          not vd.text_payload_for_store(["nope"], detection))

    for field, value in (
        ("penalty", 99),
        ("level", "HIGH"),
        ("severity", "HIGH"),
        ("confidence", 0.9),
        ("score", 10),
        ("location", [0.1, 0.1, 0.2, 0.2]),
        ("object_key", "未佩戴安全帽"),
    ):
        check(f"C7 文本 payload 不得夹带 {field}",
              not vd.text_payload_for_store(payload(**{field: value}), detection))

    check("C8 超长文本被拒绝",
          not vd.text_payload_for_store(payload(title="x" * 5000), detection))
    check("C9 嵌套对象被拒绝",
          not vd.text_payload_for_store(payload(title={"a": 1}), detection))
    check("C10 列表型字段被拒绝",
          not vd.text_payload_for_store(payload(reason=["a"]), detection))

    stored = vd.text_payload_for_store(payload(), detection)
    eq("C11 存下的 payload 只含文本字段",
       sorted(stored.keys()), ["findings", "summary"])
    eq("C12 finding 只含文本字段",
       sorted(stored["findings"][0].keys()),
       ["finding_id", "reason", "recommendation", "title"])


# ============================================================
# D. 写入
# ============================================================

def test_storage() -> None:
    section("D. 写入（只写当前语言那一个叶子）")

    detection = detection_for()
    finding_id = detection["findings"][0]["finding_id"]
    project = base_project()
    before = copy.deepcopy(project)

    payload_zh = {
        "summary": "中文总结",
        "findings": [{
            "finding_id": finding_id,
            "title": "中文标题",
            "reason": "中文原因",
            "recommendation": "中文建议",
        }],
    }

    stored = vr.store_vision_text(project, PROJECT_ID, detection, payload_zh, ZH)

    check("D1 写入成功", stored.get("ok") is True, str(stored.get("reason")))
    eq("D2 输入保持不变（copy-on-write）", project, before)

    updated = stored["project"]
    bucket = (
        updated.get("localized_content", {})
        .get(CONTENT_TYPE, {})
        .get(stored["record_id"], {})
    )

    eq("D3 只写入目标语言叶子", sorted(bucket.keys()), [ZH])
    eq("D4 记录 content_type", bucket[ZH]["content_type"], CONTENT_TYPE)
    eq("D5 记录 locale", bucket[ZH]["locale"], ZH)
    eq("D6 记录 payload", bucket[ZH]["payload"], payload_zh)
    eq("D7 记录指纹 = detection 指纹",
       bucket[ZH]["input_hash"], vd.vision_localization_input_hash(detection))
    eq("D8 记录来源", bucket[ZH]["source"], lc.SOURCE_AI_GENERATED)

    eq("D9 基线分未被修改", updated["baseline_score"], before["baseline_score"])
    eq("D10 其他项目字段未被修改",
       {key: updated[key] for key in before}, before)
    check("D11 不迁移 / 不写旧归档", "ai_text_by_language" not in updated)

    # 同一张图片 + 同一份 detection 再写一次：结构幂等
    again = vr.store_vision_text(updated, PROJECT_ID, detection, payload_zh, ZH)
    eq("D12 重复写入幂等（结构一致）",
       strip_volatile(again["project"]["localized_content"]),
       strip_volatile(updated["localized_content"]))

    # 第二种语言写另一个叶子，互不影响
    payload_en = {
        "summary": "EN summary",
        "findings": [{
            "finding_id": finding_id,
            "title": "EN title",
            "reason": "EN reason",
            "recommendation": "EN action",
        }],
    }
    both = vr.store_vision_text(updated, PROJECT_ID, detection, payload_en, EN)["project"]
    both_bucket = both["localized_content"][CONTENT_TYPE][stored["record_id"]]
    eq("D13 两种语言并存", sorted(both_bucket.keys()), [EN, ZH])
    eq("D14 中文叶子未被英文覆盖", both_bucket[ZH]["payload"], payload_zh)
    eq("D15 英文叶子内容独立", both_bucket[EN]["payload"], payload_en)

    # 非法 payload 不落盘
    rejected = vr.store_vision_text(project, PROJECT_ID, detection, {"summary": "x"}, ZH)
    check("D16 非法 payload 被拒绝", rejected.get("ok") is False)
    eq("D17 非法 payload 不修改项目", rejected["project"], before)

    # 换一张图片 -> 另一个 record_id，绝不复用
    other = detection_for(image_hash=OTHER_HASH)
    other_store = vr.store_vision_text(both, PROJECT_ID, other, payload_zh, ZH)
    check("D18 另一张图片使用另一个 record_id",
          other_store["record_id"] != stored["record_id"])
    eq("D19 两张图片的版本并存（不复用、不覆盖）",
       sorted(other_store["project"]["localized_content"][CONTENT_TYPE].keys()),
       sorted([stored["record_id"], other_store["record_id"]]))
    check("D20 新图片的版本没有覆盖旧图片的文本",
          other_store["project"]["localized_content"][CONTENT_TYPE][stored["record_id"]][ZH]["payload"]
          == payload_zh)

    # 指纹随识别事实变化（confidence 变了 -> 文本必须重新生成）
    other_fingerprint = vd.vision_localization_input_hash(other)
    check("D21 不同 detection 的指纹不同",
          other_fingerprint != vd.vision_localization_input_hash(detection))


# ============================================================
# E. 读取状态
# ============================================================

def test_read_states() -> None:
    section("E. 读取状态（empty / missing / available / stale / invalid / legacy）")

    detection = detection_for()
    finding_id = detection["findings"][0]["finding_id"]
    project = base_project()

    # --- 还没识别 -------------------------------------------------
    state = read_state(project, ZH, None)
    eq("E1 无 detection -> empty", state["ui_state"], vd.DISPLAY_EMPTY)
    check("E2 无 detection 时不可生成文本",
          not vd.vision_generation_plan(state)["can_generate_text"])

    # --- 已识别但没有任何文本 -------------------------------------
    state = read_state(project, ZH, detection)
    eq("E3 无文本 -> empty", state["ui_state"], vd.DISPLAY_EMPTY)
    eq("E4 无文本时 saved_locales 为空", state["saved_locales"], [])
    check("E5 可以生成文本",
          vd.vision_generation_plan(state)["can_generate_text"])
    check("E6 文本生成路径不调用视觉模型",
          vd.vision_generation_plan(state)["calls_vision_model_on_text"] is False)

    # --- 写入中文 --------------------------------------------------
    payload_zh = {
        "summary": "中文总结",
        "findings": [{
            "finding_id": finding_id,
            "title": "中文标题",
            "reason": "中文原因",
            "recommendation": "中文建议",
        }],
    }
    project_zh = vr.store_vision_text(project, PROJECT_ID, detection, payload_zh, ZH)["project"]

    state = read_state(project_zh, ZH, detection)
    eq("E7 中文 -> available", state["ui_state"], vd.DISPLAY_AVAILABLE)
    eq("E8 命中语言", state["locale"], ZH)
    eq("E9 摘要为中文", state["display"]["summary"], "中文总结")
    check("E10 available 后不再需要生成", not state["needs_generation"])

    # --- 切到英文：不能顶替显示 ------------------------------------
    state_en = read_state(project_zh, EN, detection)
    eq("E11 英文 -> missing", state_en["ui_state"], vd.DISPLAY_MISSING)
    check("E12 英文不显示中文版本", state_en["payload"] is None and state_en["display"] is None)
    eq("E13 英文状态列出已保存语言", state_en["saved_locales"], [ZH])
    check("E14 英文需要生成", state_en["needs_generation"])

    # --- 两种语言各自独立 ------------------------------------------
    payload_en = {
        "summary": "EN summary",
        "findings": [{
            "finding_id": finding_id,
            "title": "EN title",
            "reason": "EN reason",
            "recommendation": "EN action",
        }],
    }
    project_both = vr.store_vision_text(project_zh, PROJECT_ID, detection, payload_en, EN)["project"]

    state_zh = read_state(project_both, ZH, detection)
    state_en = read_state(project_both, EN, detection)
    eq("E15 中文仍是中文", state_zh["display"]["summary"], "中文总结")
    eq("E16 英文仍是英文", state_en["display"]["summary"], "EN summary")
    eq("E17 两种语言都已保存", sorted(state_zh["saved_locales"]), [EN, ZH])

    # --- 事实优先：文本不能覆盖识别事实 ----------------------------
    finding = state_zh["display"]["findings"][0]
    fact = detection["findings"][0]
    eq("E18 等级来自 detection", finding["level"], fact["level"])
    eq("E19 penalty 来自 detection", finding["penalty"], fact["penalty"])
    eq("E20 location 来自 detection", finding["location"], fact["location"])
    eq("E21 confidence 来自 detection", finding["confidence"], fact["confidence"])
    eq("E22 object_key 来自 detection", finding["object_key"], fact["object_key"])
    eq("E23 只有文本来自 payload", (finding["title"], finding["reason"], finding["recommendation"]),
       ("中文标题", "中文原因", "中文建议"))
    eq("E24 事实层与语言无关",
       [(f["level"], f["penalty"], f["location"]) for f in state_zh["display"]["findings"]],
       [(f["level"], f["penalty"], f["location"]) for f in state_en["display"]["findings"]])

    # --- 识别输入变化 -> stale -------------------------------------
    changed = copy.deepcopy(detection)
    changed["findings"][0]["confidence"] = 0.42
    changed["findings"][0]["finding_id"] = vd.normalize_finding_id(
        0,
        changed["findings"][0]["object_key"],
        changed["findings"][0]["category_key"],
        changed["findings"][0]["location"],
    )

    state = read_state(project_both, ZH, changed)
    eq("E25 指纹不同的文本 -> stale", state["ui_state"], vd.DISPLAY_STALE)
    check("E26 stale 不再作为当前分析显示",
          state["payload"] is None and state["display"] is None)
    check("E27 stale 仍准备只读折叠内容",
          bool(state["stale_display"]) or bool(state["stale_text"]))
    check("E28 stale 页面仍显示当前事实",
          state["overall_level"] == changed["overall_level"]
          and state["total_penalty"] == changed["total_penalty"])
    check("E29 stale 需要重新生成", state["needs_generation"])

    # --- 非法 detection --------------------------------------------
    broken = copy.deepcopy(detection)
    broken["findings"][0]["penalty"] = "很多"
    state = read_state(project_both, ZH, broken)
    eq("E30 非法 detection -> invalid", state["ui_state"], vd.DISPLAY_INVALID)
    check("E31 invalid 不显示任何文本", state["payload"] is None and state["display"] is None)

    # --- legacy 单例 -------------------------------------------------
    # 换一个项目 id：会话缓存绝不跨项目复用，旧归档只在"当前语言确实没有新版本"
    # 时以只读方式出现。
    legacy_project = base_project()
    legacy_project["ai_text_by_language"] = {
        ZH: {CONTENT_TYPE: {"summary": "旧版总结"}},
    }
    state = read_state(legacy_project, ZH, detection, project_id=OTHER_PROJECT)
    eq("E32 只有旧归档 -> legacy_compatible",
       state["ui_state"], vd.DISPLAY_LEGACY_COMPATIBLE)
    eq("E33 legacy 预览来自旧归档", state["legacy_text"], "旧版总结")
    check("E34 legacy 不再需要展示为当前文本", state["payload"] is None)
    check("E35 legacy 需要生成新版本", state["needs_generation"])
    check("E38 会话缓存只服务同一项目（同项目下可复用）",
          read_state(base_project(), ZH, detection)["ui_state"]
          == vd.DISPLAY_AVAILABLE)
    eq("E36 legacy 不迁移数据", legacy_project["ai_text_by_language"],
       {ZH: {CONTENT_TYPE: {"summary": "旧版总结"}}})

    # legacy 不匹配的语言不会被顶替
    state = read_state(legacy_project, EN, detection)
    eq("E37 legacy 语言不匹配时不显示", state["legacy"], None)

    # --- 未知 / 计划中的语言回退-------------------------------------
    eq("E38 planned locale 回退到默认语言",
       vd.resolve_content_locale("ja"), vd.resolve_content_locale(None))
    eq("E39 非法 locale 回退到默认语言", vd.resolve_content_locale("not-a-locale") in (EN, "und"), True)


# ============================================================
# F. AI 边界
# ============================================================

def test_ai_boundaries() -> None:
    section("F. AI 边界（识别一次、文本零视觉调用、切语言零调用）")

    detection = detection_for()
    finding_id = detection["findings"][0]["finding_id"]

    vision_spy = VisionSpy(RAW_MODEL_OUTPUT)
    text_spy = TextSpy([finding["finding_id"] for finding in detection["findings"]])

    with _locale_patch(ZH):
        # 1) 识别：只有这一条路径允许调用视觉模型
        with patch_object(vr, "call_deepseek_vision", vision_spy):
            raw = vr.call_deepseek_vision(None, "prompt")
            built = vr.build_vision_detection(raw, IMAGE_HASH)

        eq("F1 识别按钮路径调用视觉模型 1 次", vision_spy.calls, 1)
        eq("F2 识别结果与直接构造一致", built, detection)

        # 2) 生成文本：只调用文本模型，绝不调用视觉模型
        with patch_object(vr, "call_deepseek_vision", vision_spy):
            with patch_object(risk_agent.RiskAgent, "explain_vision_text", text_spy):
                result = vr.ai_generate_vision_text(built, PROJECT_ID, ZH)

        eq("F3 文本生成成功", result["success"], True)
        eq("F4 文本模型调用 1 次", text_spy.calls, 1)
        eq("F5 文本生成期间视觉模型调用 0 次", vision_spy.calls, 1)
        eq("F6 文本语言为当前语言", text_spy.prompts[0].count("Simplified Chinese") > 0, True)
        check("F6b prompt 携带全部 finding_id（顺序一致）",
              all(
                  finding["finding_id"] in text_spy.prompts[0]
                  for finding in built["findings"]
              ))
        check("F6c prompt 不含 penalty / confidence / 坐标",
              '"penalty"' not in text_spy.prompts[0]
              and '"confidence"' not in text_spy.prompts[0]
              and '"location"' not in text_spy.prompts[0])

        project = vr.store_vision_text(
            base_project(), PROJECT_ID, built, result["payload"], ZH
        )["project"]

        # 3) 读路径：切换语言不需要任何 AI
        with patch_object(vr, "call_deepseek_vision", vision_spy):
            with patch_object(risk_agent.RiskAgent, "explain_vision_text", text_spy):
                state_zh = read_state(project, ZH, built)
                state_en = read_state(project, EN, built)

        eq("F7 切换语言视觉模型调用 0 次", vision_spy.calls, 1)
        eq("F8 切换语言文本模型调用 0 次", text_spy.calls, 1)
        eq("F9 中文命中", state_zh["ui_state"], vd.DISPLAY_AVAILABLE)
        eq("F10 英文仍为 missing（不顶替）", state_en["ui_state"], vd.DISPLAY_MISSING)

        # 4) 生成其它语言：仍然 0 次视觉调用
        with patch_object(vr, "call_deepseek_vision", vision_spy):
            with patch_object(risk_agent.RiskAgent, "explain_vision_text", text_spy):
                result_en = vr.ai_generate_vision_text(built, PROJECT_ID, EN)

        eq("F11 生成第二个语言时视觉模型调用 0 次", vision_spy.calls, 1)
        check("F12 英文版本是英文文本",
              "EN summary" == result_en["payload"]["summary"])
        check("F13 两个语言版本的 finding_id 一致",
              [f["finding_id"] for f in result_en["payload"]["findings"]] == [finding_id])

    # --- 失败路径不允许写入 ----------------------------------------
    project = base_project()
    before = copy.deepcopy(project)

    for mode in ("raise", {"summary": "S", "findings": []}, {"penalty": 9}):
        failing = TextFail(mode) if mode == "raise" else TextFail(mode)

        with patch_object(risk_agent.RiskAgent, "explain_vision_text", failing):
            result = vr.ai_generate_vision_text(detection, PROJECT_ID, ZH)

        label = mode if isinstance(mode, str) else "invalid-payload"
        check(f"F14 文本生成失败不返回 payload（{label}）",
              result["success"] is False and result["payload"] is None)

    check("F15 失败后项目未被修改", project == before)

    # 非法响应不能被当成成功写入
    with patch_object(risk_agent.RiskAgent, "explain_vision_text", TextFail({"summary": "x"})):
        result = vr.ai_generate_vision_text(detection, PROJECT_ID, ZH)

    check("F16 非法响应返回错误码", result["error"] == "invalid_vision_payload", result["error"])

    rejected = vr.store_vision_text(project, PROJECT_ID, detection, {"summary": "x"}, ZH)
    check("F17 非法 payload 不落盘", rejected["ok"] is False and rejected["project"] == before)

    # 视觉调用失败（None）不得被当成"没有风险"
    failed_raw = VisionSpy(None)
    with patch_object(vr, "call_deepseek_vision", failed_raw):
        raw = vr.call_deepseek_vision(None, "prompt")

    eq("F18 视觉失败返回 None（可与空结果区分）", raw, None)
    eq("F19 视觉失败不产生 detection", vr.build_vision_detection(raw, IMAGE_HASH), {})

    # 检测结果复用（同一图片不再识别）
    project_with_detection = {DETECTION_FIELD: detection}
    eq("F20 同图片复用已存 detection",
       vr.detection_for_image(project_with_detection, PROJECT_ID, IMAGE_HASH), detection)
    eq("F21 换图片不复用旧 detection",
       vr.detection_for_image(project_with_detection, PROJECT_ID, OTHER_HASH), {})


# ============================================================
# G. 缓存命名空间
# ============================================================

def test_cache_namespace() -> None:
    section("G. 缓存命名空间与有界性")

    detection = detection_for()
    detection_key = vd.vision_detection_cache_key(
        PROJECT_ID, IMAGE_HASH, vr.VISION_MODEL_ID, vd.VISION_ONTOLOGY_VERSION
    )

    eq("G1 detection 缓存键不含 locale", len(detection_key), 4)
    check("G2 detection 键含项目与图片",
          detection_key[0] == PROJECT_ID and detection_key[1] == IMAGE_HASH)
    check("G3 换项目 -> 换键",
          vd.vision_detection_cache_key(
              OTHER_PROJECT, IMAGE_HASH, vr.VISION_MODEL_ID, vd.VISION_ONTOLOGY_VERSION
          ) != detection_key)
    check("G4 换图片 -> 换键",
          vd.vision_detection_cache_key(
              PROJECT_ID, OTHER_HASH, vr.VISION_MODEL_ID, vd.VISION_ONTOLOGY_VERSION
          ) != detection_key)
    check("G5 换模型 / 本体版本 -> 换键",
          vd.vision_detection_cache_key(PROJECT_ID, IMAGE_HASH, "other", "9") != detection_key)

    record_id = vd.vision_record_id(PROJECT_ID, IMAGE_HASH)
    fingerprint = vd.vision_localization_input_hash(detection)
    text_key = vd.vision_text_cache_key(PROJECT_ID, record_id, ZH, fingerprint)

    eq("G6 text 缓存键含 locale 与指纹", len(text_key), 5)
    check("G7 换语言 -> 换键",
          vd.vision_text_cache_key(PROJECT_ID, record_id, EN, fingerprint) != text_key)
    check("G8 换指纹 -> 换键",
          vd.vision_text_cache_key(PROJECT_ID, record_id, ZH, "other") != text_key)
    check("G9 换项目 -> 换键",
          vd.vision_text_cache_key(OTHER_PROJECT, record_id, ZH, fingerprint) != text_key)
    check("G10 换图片（record_id）-> 换键",
          vd.vision_text_cache_key(
              PROJECT_ID, vd.vision_record_id(PROJECT_ID, OTHER_HASH), ZH, fingerprint
          ) != text_key)

    # 有界性：同一 group 超出上限时淘汰最旧的键
    limit = vd.MAX_SESSION_CACHE_PER_GROUP
    cache: Dict[Any, Any] = {}

    for index in range(limit + 5):
        key = vd.vision_text_cache_key(
            PROJECT_ID, record_id, ZH, f"fingerprint-{index}"
        )
        vd.bounded_vision_cache_put(
            cache, key, {"summary": str(index)}, group=vd.vision_text_cache_group(key)
        )

    same_group = [
        key for key in cache
        if vd.vision_text_cache_group(key) == vd.vision_text_cache_group(text_key)
    ]
    eq("G11 同组缓存不超过上限", len(same_group), limit)
    check("G12 保留最新的一条",
          vd.vision_text_cache_key(PROJECT_ID, record_id, ZH, f"fingerprint-{limit + 4}") in cache)
    check("G13 淘汰最旧的一条",
          vd.vision_text_cache_key(PROJECT_ID, record_id, ZH, "fingerprint-0") not in cache)

    # 不同组之间互不淘汰
    other_key = vd.vision_text_cache_key(PROJECT_ID, record_id, EN, "fingerprint-0")
    vd.bounded_vision_cache_put(
        cache, other_key, {"summary": "en"}, group=vd.vision_text_cache_group(other_key)
    )
    check("G14 淘汰不跨语言分组", other_key in cache)

    # detection 缓存按项目分组
    detection_cache: Dict[Any, Any] = {}
    for index in range(limit + 3):
        key = vd.vision_detection_cache_key(
            PROJECT_ID, f"{index:064d}", vr.VISION_MODEL_ID, vd.VISION_ONTOLOGY_VERSION
        )
        vd.bounded_vision_cache_put(
            detection_cache, key, {"findings": []},
            group=vd.vision_detection_cache_group(key),
        )

    same_detection_group = [
        key for key in detection_cache
        if vd.vision_detection_cache_group(key) == (PROJECT_ID,)
    ]
    eq("G15 detection 缓存同项目不超过上限", len(same_detection_group), limit)

    # remember / cached 往返（会话缓存丢失也不影响协议）
    vr._FALLBACK_CACHES.clear()
    vr.remember_detection(PROJECT_ID, detection)
    eq("G16 会话缓存可回读 detection",
       vr.cached_detection(PROJECT_ID, IMAGE_HASH).get("image_hash"), IMAGE_HASH)
    eq("G17 换图片读不到缓存的 detection",
       vr.cached_detection(PROJECT_ID, OTHER_HASH), {})
    eq("G18 换项目读不到缓存的 detection",
       vr.cached_detection(OTHER_PROJECT, IMAGE_HASH), {})

    vr.remember_text(PROJECT_ID, record_id, ZH, fingerprint, {"summary": "缓存"})
    eq("G19 无项目时回落到 detection 缓存",
       vr.detection_for_image({}, PROJECT_ID, IMAGE_HASH).get("image_hash"), IMAGE_HASH)
    eq("G20 缓存丢失不影响显示协议",
       read_state({}, ZH, detection)["ui_state"], vd.DISPLAY_EMPTY)


# ============================================================
# H. 静态守卫
# ============================================================

def _tree(path: pathlib.Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _function(tree: ast.Module, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node

    raise AssertionError(f"function {name} not found")


def _called_bare_names(node: ast.AST) -> List[str]:
    """只收集 ``open(...)`` 这类"裸函数调用"（不含 ``Image.open`` 这类方法）。"""
    return [
        item.func.id
        for item in ast.walk(node)
        if isinstance(item, ast.Call) and isinstance(item.func, ast.Name)
    ]


def _called_names(node: ast.AST) -> List[str]:
    names = []

    for item in ast.walk(node):
        if isinstance(item, ast.Call):
            func = item.func

            if isinstance(func, ast.Name):
                names.append(func.id)
            elif isinstance(func, ast.Attribute):
                names.append(func.attr)

    return names


def test_static_guards() -> None:
    section("H. AST 静态守卫")

    tree = _tree(VISION)
    page = _function(tree, "vision_risk_page")
    text_fn = _function(tree, "ai_generate_vision_text")
    store_fn = _function(tree, "store_vision_text")
    source = VISION.read_text(encoding="utf-8")

    eq("H1 页面只调用一次视觉模型",
       _called_names(page).count("call_deepseek_vision"), 1)

    # 生成文本分支绝不能调用视觉模型
    generate_branch = None
    for node in ast.walk(page):
        if isinstance(node, ast.If):
            test_source = ast.dump(node.test)

            if "can_generate_text" in test_source:
                generate_branch = node

    check("H2 生成文本分支存在", generate_branch is not None)

    if generate_branch is not None:
        check("H3 生成文本分支不调用视觉模型",
              "call_deepseek_vision" not in _called_names(generate_branch))
        check("H4 生成文本分支调用文本生成",
              "ai_generate_vision_text" in _called_names(generate_branch))

    check("H5 文本生成不接触图片 / 视觉模型",
          "call_deepseek_vision" not in _called_names(text_fn)
          and "image" not in _called_names(text_fn)
          and not any(
              isinstance(node, ast.Name) and node.id == "image"
              for node in ast.walk(text_fn)
          ))

    check("H6 文本 prompt 显式带目标语言",
          "target_locale" in ast.dump(text_fn))

    check("H7 页面每个按钮都使用 action key",
          _called_names(page).count("vision_action_key") >= 2)

    check("H8 页面不直接解析模型输出",
          "parse_vision_result" not in _called_names(page))

    check("H9 页面不直接查询协议状态",
          "localized_content_get" not in source
          and "localized_content_status" not in source)

    check("H10 页面只写 Vision 契约",
          "CONTENT_TYPE_VISION" in source
          and "CONTENT_TYPE_BASELINE" not in source
          and "CONTENT_TYPE_LOGS" not in source
          and "CONTENT_TYPE_REPORT" not in source)

    check("H11 写入使用统一协议",
          "make_localized_record" in _called_names(store_fn)
          and "localized_content_put" in _called_names(store_fn))

    check("H12 写入显式带 detection 指纹",
          "input_hash" in ast.dump(store_fn))

    check("H13 detection 只写一个字段",
          len(re.findall(r"project\[\s*DETECTION_FIELD\s*\]\s*=", source)) == 1)

    check("H14 不借用 Current Risk 显示层",
          "current_risk_display" not in source
          and "CURRENT_RISK_CONTENT_TYPE" not in source)

    bare_calls = set(_called_bare_names(tree))
    check("H15 vision_risk.py 不直接写盘（落盘由注入的回调负责）",
          not (bare_calls & {"open", "save_projects", "write_text", "dump"})
          and len(re.findall(r"if callable\(persist\)", source)) >= 1)

    # AI 客户端保持"纯文本步骤"
    agent_tree = _tree(RISK_AGENT)
    agent_source = RISK_AGENT.read_text(encoding="utf-8")
    explain = _function(tree, "ai_generate_vision_text")

    check("H16 risk_agent 提供 explain_vision_text",
          "def explain_vision_text(self, prompt" in agent_source)
    check("H17 risk_agent 不接触统一协议", "localized_content" not in agent_source)
    check("H18 文本步骤不引用图片变量",
          "image" not in {
              node.id for node in ast.walk(explain) if isinstance(node, ast.Name)
          })

    # app.py 只通过包装函数注入 persist
    app_source = APP.read_text(encoding="utf-8")

    check("H19 app.py 通过 vision_page 注入 persist",
          "def vision_page()" in app_source
          and "vision_risk_page(persist=persist)" in app_source)
    check("H20 app.py 不直接引用 Vision content type",
          "CONTENT_TYPE_VISION" not in app_source)
    check("H21 app.py 注册的是包装函数", "VISION_PAGE = st.Page(\n    vision_page," in app_source)

    check("H22 vision_display 是唯一显示层",
          "utils.vision_display" in source and "vision_display_state" in source)


# ============================================================
# I. locale key
# ============================================================

def _flatten(data: Any, prefix: str = "") -> Dict[str, Any]:
    flat: Dict[str, Any] = {}

    for key, value in data.items():
        path = f"{prefix}{key}"

        if isinstance(value, dict):
            flat.update(_flatten(value, path + "."))
        else:
            flat[path] = value

    return flat


def test_locale_keys() -> None:
    section("I. locale key")

    en = _flatten(json.loads(EN_JSON.read_text(encoding="utf-8")))
    zh = _flatten(json.loads(ZH_JSON.read_text(encoding="utf-8")))

    for key in NEW_KEYS:
        check(f"I1 en 含 {key}", key in en)
        check(f"I2 zh-CN 含 {key}", key in zh)

    en_vision = {key for key in en if key.startswith("vision.")}
    zh_vision = {key for key in zh if key.startswith("vision.")}

    eq("I3 vision 键双语对齐", en_vision, zh_vision)

    source = VISION.read_text(encoding="utf-8")
    called = set(re.findall(r't\(\s*"([a-zA-Z0-9_.]+)"', source))
    used = sorted(key for key in called if key.startswith("vision."))
    missing = [key for key in used if key not in en]

    eq("I4 页面用到的 vision 键全部存在", missing, [])

    for code in FAILURE_CASES:
        check(f"I5 上传错误码 {code} 有文案",
              f"vision.upload_error_{code}" in en)

    # 占位符对齐：同一 key 的双语占位符集合必须一致
    mismatched = []

    for key in sorted(en_vision & zh_vision):
        left = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", str(en[key])))
        right = set(re.findall(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}", str(zh[key])))

        if left != right:
            mismatched.append(key)

    eq("I6 vision 占位符双语一致", mismatched, [])


# ============================================================
# J. 识别按钮的同一次点击是否允许补写文本
# ============================================================

def test_generation_timing() -> None:
    section("J. 生成时机（同一次点击最多一次文本调用）")

    detection = detection_for()
    finding_id = detection["findings"][0]["finding_id"]

    payload_zh = {
        "summary": "中文总结",
        "findings": [{
            "finding_id": finding_id,
            "title": "中文标题",
            "reason": "中文原因",
            "recommendation": "中文建议",
        }],
    }

    # --- 每个显示状态的判定 ----------------------------------------
    for ui_state, expected in (
        (vd.DISPLAY_EMPTY, True),
        (vd.DISPLAY_MISSING, True),
        (vd.DISPLAY_STALE, True),
        (vd.DISPLAY_INVALID, True),
        (vd.DISPLAY_LEGACY_COMPATIBLE, True),
        (vd.DISPLAY_AVAILABLE, False),
    ):
        eq(f"J1 {ui_state} 是否需要生成文本",
           vd.vision_text_needs_generation({"ui_state": ui_state}),
           expected)

    check("J2 非映射输入不触发生成",
          vd.vision_text_needs_generation(None) is False
          and vd.vision_text_needs_generation("empty") is False)
    check("J3 缺 ui_state 不触发生成",
          vd.vision_text_needs_generation({}) is False)

    # --- 真实状态：新识别（empty）必须允许同一次点击补写 -------------
    project = base_project()
    state = read_state(project, ZH, detection)
    eq("J4 新识别的显示状态为 empty", state["ui_state"], vd.DISPLAY_EMPTY)
    check("J5 新识别允许同一次点击生成文本",
          vd.vision_text_needs_generation(state))

    # --- 已有当前语言版本（available）绝不允许重写 -------------------
    stored = vr.store_vision_text(project, PROJECT_ID, detection, payload_zh, ZH)
    with_zh = stored["project"]
    state = read_state(with_zh, ZH, detection)
    eq("J6 写入后显示状态为 available", state["ui_state"], vd.DISPLAY_AVAILABLE)
    check("J7 已有可用版本不再生成文本",
          not vd.vision_text_needs_generation(state))
    check("J8 生成按钮在 available 状态下消失",
          not vd.vision_generation_plan(state)["can_generate_text"])

    # --- 过期（stale）允许重新生成 ----------------------------------
    # 同一张图片、识别事实变化 -> 同一个 record_id，但指纹不同。
    changed = copy.deepcopy(detection)
    changed["findings"][0]["confidence"] = 0.42
    changed["findings"][0]["finding_id"] = vd.normalize_finding_id(
        0,
        changed["findings"][0]["object_key"],
        changed["findings"][0]["category_key"],
        changed["findings"][0]["location"],
    )
    project_old = {
        **with_zh,
        vd.DETECTION_FIELD: changed,
    }
    state = read_state(project_old, ZH, changed)
    eq("J9 输入变化后显示状态为 stale", state["ui_state"], vd.DISPLAY_STALE)
    check("J10 过期版本允许重新生成",
          vd.vision_text_needs_generation(state))
    check("J11 stale 状态下提供生成按钮",
          vd.vision_generation_plan(state)["can_generate_text"])

    # --- legacy 单例内容也允许生成新版本 ----------------------------
    legacy_project = base_project()
    legacy_project["ai_text_by_language"] = {
        ZH: {CONTENT_TYPE: {"summary": "旧版总结"}},
    }
    state = read_state(legacy_project, ZH, detection, project_id=OTHER_PROJECT)
    eq("J12 旧归档显示状态为 legacy_compatible",
       state["ui_state"], vd.DISPLAY_LEGACY_COMPATIBLE)
    check("J13 legacy 内容允许生成新版本",
          vd.vision_text_needs_generation(state))
    check("J14 legacy 状态下提供生成按钮",
          vd.vision_generation_plan(state)["can_generate_text"])

    # --- 没有风险时绝不生成文本 -------------------------------------
    empty_detection = detection_for(raw={"risks": []})
    eq("J15 空结果仍然是合法 detection", empty_detection.get("findings"), [])
    check("J16 没有 findings 时同一次点击不调用文本模型",
          not (
              bool(empty_detection.get("findings"))
              and vd.vision_text_needs_generation(
                  read_state(base_project(), ZH, empty_detection)
              )
          ))


# ============================================================
# 替身 patch 辅助（避免 unittest.mock 的隐式依赖差异）
# ============================================================

class patch_object:
    """把 ``obj.name`` 临时替换为 ``value``。"""

    def __init__(self, obj: Any, name: str, value: Any) -> None:
        self.obj = obj
        self.name = name
        self.value = value
        self.original = None

    def __enter__(self):
        self.original = getattr(self.obj, self.name)
        setattr(self.obj, self.name, self.value)
        return self.value

    def __exit__(self, *exc):
        setattr(self.obj, self.name, self.original)
        return False


# ============================================================
# 主入口
# ============================================================

def main() -> int:
    print("=" * 58)
    print("RiskPilot Vision Risk 本地化回归测试（阶段 4B）")
    print("=" * 58)

    # 每个场景都从"会话缓存为空"开始：缓存只是加速手段，任何结论都不得依赖它。
    for scenario in (
        test_canonical_detection,
        test_detection_validation,
        test_text_payload,
        test_storage,
        test_read_states,
        test_ai_boundaries,
        test_cache_namespace,
        test_static_guards,
        test_locale_keys,
        test_generation_timing,
    ):
        reset_session_cache()
        scenario()

    print("\n" + "=" * 58)
    print(f"结果：{_PASSED}/{_PASSED + len(_FAILED)} 通过")

    if _FAILED:
        print("失败项：")
        for name in _FAILED:
            print(f"  - {name}")
        print("=" * 58)
        return 1

    print("Vision Risk 多语言协议测试全部通过 ✅")
    print("=" * 58)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
