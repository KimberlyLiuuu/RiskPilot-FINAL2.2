# -*- coding: utf-8 -*-
"""
RiskPilot Baseline 显示层语言回归测试 (test_localized_baseline_display.py)

目标：中文界面不得把英文内容直接显示在「风险链 / 影响 / 预防措施」区域。

测试对象是纯模块 ``utils/baseline_display.py``：
    * get_baseline_display_text()  当前语言内容选择（只读 project）
    * risk_chain_label / impact_label / action_label  确定性模板显示层翻译
    * ai_text_lookup / ai_text_store  按语言隔离的 AI 文本归档

约束：不请求网络、不调用真实 AI、不写入 data/projects.json、
不导入 app.py（app.py 导入即执行 Streamlit 页面）。

覆盖场景：
    A. 中文显示确定性英文风险链
    B. 当前语言 archive 优先
    C. 当前语言 archive 缺失
    D. 旧版确定性数据兼容
    E. 未知自由文本安全处理
    F. 页面语言完整性

另有静态检查：确定性模板完整性、纯模块无副作用、Baseline 渲染路径
经过各显示函数、未触碰风险计算字段与 ui_lang。

运行方式：
    python test_localized_baseline_display.py
"""

import ast
import copy
import pathlib
import re
import types

from utils import baseline_display as bd
from utils import content_locale, locale_registry
from utils.baseline_display import (
    SOURCE_DETERMINISTIC_LEGACY,
    SOURCE_EMPTY,
    SOURCE_LEGACY_CURRENT_LANGUAGE,
    SOURCE_LOCALIZED_ARCHIVE,
    SOURCE_MISSING_CURRENT_LANGUAGE,
    action_label,
    ai_text_lookup,
    ai_text_store,
    estimated_text_language,
    get_baseline_display_text,
    impact_label,
    is_action_known,
    is_chain_known,
    is_impact_known,
    risk_chain_label,
)

ZH = "zh-CN"
EN = "en"

DETERMINISTIC_CHAIN = (
    "Heavy Rainfall → Increased Surface Runoff → Drainage Pressure "
    "→ Site Water Accumulation → Safety + Schedule + Quality Risks"
)

DETERMINISTIC_CHAIN_ZH = (
    "强降雨 → 地表径流增加 → 排水系统承压 → 施工区积水 → 安全、工期与质量风险"
)

DETERMINISTIC_ACTIONS = [
    "Monitor weather conditions and prepare temporary rain protection.",
    "Inspect drainage systems and prepare emergency pumping equipment.",
    "Add identified environmental risks to the project risk register.",
]

DETERMINISTIC_IMPACTS = [
    "Weather-sensitive outdoor work may be interrupted.",
    "Excavation areas may accumulate water.",
]


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


_LATIN = re.compile(r"[A-Za-z]+")
_ALLOWED_LATIN = {"AI"}


def latin_words(text):

    return [
        word
        for word in _LATIN.findall(text or "")
        if word.upper() not in _ALLOWED_LATIN
    ]


def assert_no_english(name, texts):

    plain = " | ".join(texts)
    found = sorted(set(latin_words(plain)))
    check(name, not found, f"仍含英文片段: {found}")


def assert_no_substring(name, texts, needles):

    plain = " | ".join(texts)
    hit = [needle for needle in needles if needle in plain]
    check(name, not hit, f"仍含 {hit}")


def _render(display, language):
    """
    模拟页面层：三段内容分别经过各自的显示函数。

    显式传入 language，与线上一致（线上取当前 ui_lang），但这里不依赖
    session 状态，保证测试确定性。
    """
    return {
        "risk_chains": [
            risk_chain_label(chain, language)
            for chain in display["risk_chains"]
        ],
        "impacts": [
            impact_label(item, language) for item in display["impacts"]
        ],
        "actions": [
            action_label(action, language) for action in display["actions"]
        ],
    }


RESULT_KEYS = [
    "actions",
    "impacts",
    "needs_generation",
    "risk_chains",
    "saved_language",
    "source",
]


# ============================================================
# A. 中文显示确定性英文风险链
# ============================================================

def test_a_deterministic_english_in_chinese():

    print("\n--- A. 中文显示确定性英文风险链 ---")

    project = {
        "baseline_risk": {
            "risk_chains": [DETERMINISTIC_CHAIN],
            "actions": list(DETERMINISTIC_ACTIONS),
        }
    }

    snapshot = copy.deepcopy(project)

    zh_display = get_baseline_display_text(project, ZH)

    eq("A1 zh-CN source = deterministic_legacy",
       zh_display["source"], SOURCE_DETERMINISTIC_LEGACY)
    eq("A2 zh-CN 不需要生成", zh_display["needs_generation"], False)
    eq("A3 zh-CN 返回原始字符串（未改写）",
       zh_display["risk_chains"], [DETERMINISTIC_CHAIN])

    rendered = _render(zh_display, ZH)

    eq("A4 风险链显示为中文",
       rendered["risk_chains"][0], DETERMINISTIC_CHAIN_ZH)

    assert_no_substring(
        "A5 风险链不含 Heavy Rainfall",
        rendered["risk_chains"],
        ["Heavy Rainfall"],
    )
    assert_no_substring(
        "A6 actions 不含 Monitor weather conditions",
        rendered["actions"],
        ["Monitor weather conditions"],
    )
    assert_no_substring(
        "A7 actions 不含 Inspect drainage systems",
        rendered["actions"],
        ["Inspect drainage systems"],
    )
    assert_no_substring(
        "A8 actions 不含 Add identified environmental risks",
        rendered["actions"],
        ["Add identified environmental risks"],
    )
    assert_no_english(
        "A9 中文渲染不含英文单词",
        rendered["risk_chains"] + rendered["actions"],
    )

    eq("A10 内部原始数据未被修改", project, snapshot)

    en_display = get_baseline_display_text(project, EN)
    en_rendered = _render(en_display, EN)

    eq("A11 en source = legacy_current_language",
       en_display["source"], SOURCE_LEGACY_CURRENT_LANGUAGE)
    eq("A12 en 显示英文风险链",
       en_rendered["risk_chains"][0], DETERMINISTIC_CHAIN)
    eq("A13 en 显示英文 actions",
       en_rendered["actions"], list(DETERMINISTIC_ACTIONS))
    eq("A14 内部原始数据仍然不变", project, snapshot)


# ============================================================
# B. 当前语言 archive 优先
# ============================================================

def test_b_current_language_archive_wins():

    print("\n--- B. 当前语言 archive 优先 ---")

    project = {
        "baseline_risk": {
            "risk_chains": [DETERMINISTIC_CHAIN],
            "actions": list(DETERMINISTIC_ACTIONS),
        },
        "ai_text_by_language": {
            EN: {
                "baseline": {
                    "risk_chains": ["English chain"],
                    "actions": ["English action"],
                }
            },
            ZH: {
                "baseline": {
                    "risk_chains": ["中文风险链"],
                    "actions": ["中文措施"],
                }
            },
        },
    }

    zh_display = get_baseline_display_text(project, ZH)

    eq("B1 zh-CN source = localized_archive",
       zh_display["source"], SOURCE_LOCALIZED_ARCHIVE)
    eq("B2 zh-CN 只读中文 archive",
       zh_display["risk_chains"], ["中文风险链"])
    eq("B3 zh-CN 只读中文 actions",
       zh_display["actions"], ["中文措施"])
    assert_no_substring(
        "B4 zh-CN 未混入英文 archive",
        zh_display["risk_chains"] + zh_display["actions"],
        ["English chain", "English action", "Heavy Rainfall"],
    )

    en_display = get_baseline_display_text(project, EN)

    eq("B5 en source = localized_archive",
       en_display["source"], SOURCE_LOCALIZED_ARCHIVE)
    eq("B6 en 只读英文 archive",
       en_display["risk_chains"], ["English chain"])
    eq("B7 en 只读英文 actions",
       en_display["actions"], ["English action"])
    eq("B8 en 未混入中文 archive",
       en_display["risk_chains"], ["English chain"])

    # 生成一个语言版本不得覆盖另一个版本。
    fresh = {"risk_chains": ["新中文风险链"], "actions": ["新中文措施"]}
    ai_text_store(project, "baseline", fresh, language=ZH)

    eq("B9 zh archive 已更新",
       ai_text_lookup(project, "baseline", None, ZH)["risk_chains"],
       ["新中文风险链"])
    eq("B10 en archive 未被覆盖",
       ai_text_lookup(project, "baseline", None, EN)["risk_chains"],
       ["English chain"])
    eq("B11 legacy baseline_risk 未被覆盖",
       project["baseline_risk"]["risk_chains"], [DETERMINISTIC_CHAIN])


# ============================================================
# C. 当前语言 archive 缺失
# ============================================================

def test_c_missing_current_language_archive():

    print("\n--- C. 当前语言 archive 缺失 ---")

    project = {
        "baseline_risk": {
            "risk_chains": ["An unexpected AI-generated English sentence"],
            "actions": ["Use the original AI recommendation"],
        },
        "ai_text_by_language": {
            EN: {
                "baseline": {
                    "risk_chains": ["English chain"],
                    "actions": ["English action"],
                }
            },
        },
    }

    snapshot = copy.deepcopy(project)

    display = get_baseline_display_text(project, ZH)

    eq("C1 zh-CN source = missing_current_language",
       display["source"], SOURCE_MISSING_CURRENT_LANGUAGE)
    eq("C2 需要生成当前语言版本", display["needs_generation"], True)
    eq("C3 记录已保存语言", display["saved_language"], EN)
    eq("C4 未读取英文 archive 的风险链", display["risk_chains"], [])
    eq("C5 未读取英文 archive 的 actions", display["actions"], [])

    rendered = _render(display, ZH)

    assert_no_english(
        "C6 中文区域不渲染英文",
        rendered["risk_chains"] + rendered["actions"],
    )

    eq("C7 未修改 project 数据", project, snapshot)
    eq("C8 返回结构固定（不含 ui_lang）", sorted(display), RESULT_KEYS)


# ============================================================
# D. 旧版确定性数据兼容
# ============================================================

def test_d_legacy_deterministic_data():

    print("\n--- D. 旧版确定性数据兼容 ---")

    project = {
        "baseline_risk": {
            "risk_chains": [DETERMINISTIC_CHAIN],
            "impacts": list(DETERMINISTIC_IMPACTS),
            "actions": list(DETERMINISTIC_ACTIONS),
        }
    }

    snapshot = copy.deepcopy(project)

    zh_display = get_baseline_display_text(project, ZH)
    zh_rendered = _render(zh_display, ZH)

    eq("D1 zh-CN source = deterministic_legacy",
       zh_display["source"], SOURCE_DETERMINISTIC_LEGACY)
    eq("D2 中文风险链", zh_rendered["risk_chains"][0], DETERMINISTIC_CHAIN_ZH)
    assert_no_english(
        "D3 中文 impacts 已翻译",
        zh_rendered["impacts"],
    )
    assert_no_english(
        "D4 中文 actions 已翻译",
        zh_rendered["actions"],
    )

    en_display = get_baseline_display_text(project, EN)
    en_rendered = _render(en_display, EN)

    eq("D5 en 显示英文风险链",
       en_rendered["risk_chains"][0], DETERMINISTIC_CHAIN)
    eq("D6 en 显示英文 impacts",
       en_rendered["impacts"], list(DETERMINISTIC_IMPACTS))

    eq("D7 未修改 project 原始数据", project, snapshot)

    # 不把翻译后的中文写回 project：原始数据仍是英文原文。
    stored = project["baseline_risk"]
    check("D8 未写回中文译文",
          stored["risk_chains"] == [DETERMINISTIC_CHAIN]
          and stored["actions"] == list(DETERMINISTIC_ACTIONS)
          and "强降雨" not in " ".join(stored["risk_chains"]))


# ============================================================
# E. 未知自由文本安全处理
# ============================================================

def test_e_unknown_free_text():

    print("\n--- E. 未知自由文本安全处理 ---")

    project = {
        "baseline_risk": {
            "risk_chains": ["An unexpected AI-generated English sentence"],
            "actions": ["Use the original AI recommendation"],
        }
    }

    snapshot = copy.deepcopy(project)

    display = get_baseline_display_text(project, ZH)

    eq("E1 source = missing_current_language",
       display["source"], SOURCE_MISSING_CURRENT_LANGUAGE)
    eq("E2 需要生成中文版本", display["needs_generation"], True)
    eq("E3 未知风险链被隐藏", display["risk_chains"], [])
    eq("E4 未知 actions 被隐藏", display["actions"], [])

    rendered = _render(display, ZH)

    assert_no_english(
        "E5 不把英文伪装成中文",
        rendered["risk_chains"] + rendered["actions"],
    )
    eq("E6 未修改 project", project, snapshot)

    check("E7 未知内容不判定为确定性模板",
          not is_chain_known(project["baseline_risk"]["risk_chains"][0])
          and not is_action_known(project["baseline_risk"]["actions"][0]))


# ============================================================
# F. 页面语言完整性
# ============================================================

def test_f_page_language_integrity():

    print("\n--- F. 页面语言完整性 ---")

    # F1: 中文 archive 存在时，三段内容全部来自中文 archive。
    project = {
        "baseline_risk": {
            "risk_chains": [DETERMINISTIC_CHAIN],
            "impacts": list(DETERMINISTIC_IMPACTS),
            "actions": list(DETERMINISTIC_ACTIONS),
        },
        "ai_text_by_language": {
            EN: {
                "baseline": {
                    "risk_chains": ["English chain"],
                    "impacts": ["English impact"],
                    "actions": ["English action"],
                }
            },
            ZH: {
                "baseline": {
                    "risk_chains": ["中文风险链"],
                    "impacts": ["中文影响"],
                    "actions": ["中文措施"],
                }
            },
        },
    }

    display = get_baseline_display_text(project, ZH)
    rendered = _render(display, ZH)

    eq("F1 风险链来自中文 archive",
       rendered["risk_chains"], ["中文风险链"])
    eq("F2 影响来自中文 archive",
       rendered["impacts"], ["中文影响"])
    eq("F3 actions 来自中文 archive",
       rendered["actions"], ["中文措施"])
    assert_no_substring(
        "F4 三段均无另一语言 archive 内容",
        rendered["risk_chains"] + rendered["impacts"] + rendered["actions"],
        ["English chain", "English impact", "English action"],
    )

    # F5: 无 archive 的确定性数据，三段都要经过显示函数并全部中文。
    legacy_project = {
        "baseline_risk": {
            "risk_chains": [DETERMINISTIC_CHAIN],
            "impacts": list(DETERMINISTIC_IMPACTS),
            "actions": list(DETERMINISTIC_ACTIONS),
        }
    }

    legacy_display = get_baseline_display_text(legacy_project, ZH)
    legacy_rendered = _render(legacy_display, ZH)

    eq("F5 三段均来自 deterministic_legacy",
       legacy_display["source"], SOURCE_DETERMINISTIC_LEGACY)
    check("F6 三段内容均非空",
          all(legacy_rendered[key] for key in legacy_rendered))
    assert_no_english(
        "F7 三段渲染后均无英文",
        legacy_rendered["risk_chains"]
        + legacy_rendered["impacts"]
        + legacy_rendered["actions"],
    )


# ============================================================
# 兼容规则：语言判定
# ============================================================

def test_language_detection_rules():

    print("\n--- 兼容规则：语言判定 ---")

    eq("R1 缺省判定为 en", estimated_text_language({}), EN)
    eq("R2 显式 language 生效",
       estimated_text_language({"language": ZH}), ZH)
    eq("R3 非法 language 回落 en",
       estimated_text_language({"language": "xx"}), EN)

    declared_zh = {
        "baseline_risk": {
            "language": ZH,
            "risk_chains": [DETERMINISTIC_CHAIN],
        }
    }
    display = get_baseline_display_text(declared_zh, ZH)
    eq("R4 声明为当前语言时按原语言显示",
       display["source"], SOURCE_LEGACY_CURRENT_LANGUAGE)

    empty = get_baseline_display_text({"baseline_risk": {}}, ZH)
    eq("R5 无任何文本时 source = empty",
       empty["source"], SOURCE_EMPTY)
    eq("R6 无文本且语言不一致时提示生成",
       empty["needs_generation"], True)

    empty_en = get_baseline_display_text({"baseline_risk": {}}, EN)
    eq("R7 无文本且语言一致时不提示",
       empty_en["needs_generation"], False)

    eq("R8 确定性判定：已知链为 True",
       is_chain_known(DETERMINISTIC_CHAIN), True)
    eq("R9 确定性判定：未知句为 False",
       is_chain_known("An unexpected AI-generated English sentence"), False)
    eq("R10 确定性判定 impact",
       is_impact_known(DETERMINISTIC_IMPACTS[0]), True)
    eq("R11 确定性判定 action",
       is_action_known(DETERMINISTIC_ACTIONS[0]), True)


# ============================================================
# 静态检查
# ============================================================

ROOT = pathlib.Path(__file__).resolve().parent
MODULE_PATH = ROOT / "utils" / "baseline_display.py"
APP_PATH = ROOT / "app.py"


def _code_references(path):
    """
    收集模块「代码里」出现的标识符与字符串常量（忽略 docstring）。

    这样文档里说明「本模块不碰 ui_lang」不会把自己判成违规。
    """
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


def test_static_checks():

    print("\n--- 静态检查 ---")

    names, strings, tree = _code_references(MODULE_PATH)
    refs = names | strings

    # G1: 纯模块没有 Streamlit / 状态 / 副作用。
    check("静态G1 不导入 streamlit", "streamlit" not in refs)

    for token in ("ui_lang", "set_current_language", "session_state",
                  "persist"):
        check(f"静态G1 不引用 {token}", token not in refs)

    check("静态G1 不调用 AI 流程",
          "ai_baseline_assessment" not in refs and "requests" not in refs)

    # G2: 风险计算字段不被引用。
    for token in ("baseline_score", "selected_hazards"):
        check(f"静态G2 不引用风险字段 {token}", token not in refs)

    for token in ("score", "level", "risks"):
        check(f"静态G2 不引用风险字段 \"{token}\"", token not in strings)

    # G3: get_baseline_display_text 只读 project / baseline。
    fn_node = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "get_baseline_display_text"
    )

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
                and target.value.id in ("project", "baseline")
            ):
                writes.append(node.lineno)

    eq("静态G3 get_baseline_display_text 不修改 project/baseline",
       writes, [])

    # G4: 确定性模板在 zh-CN 下必须全部可翻译。
    missing = []
    for table in (
        bd._CHAIN_NODE_DISPLAY_KEYS,
        bd._IMPACT_DISPLAY_KEYS,
        bd._ACTION_DISPLAY_KEYS,
    ):
        for raw, key in table.items():
            if (
                risk_chain_label(raw, ZH) == raw
                and impact_label(raw, ZH) == raw
                and action_label(raw, ZH) == raw
            ):
                missing.append(key)

    eq("静态G4 确定性模板均有 zh-CN 译文", missing, [])

    # G5: Baseline 渲染路径经过各显示函数，且没有跨语言 fallback。
    app_src = APP_PATH.read_text(encoding="utf-8")
    start = app_src.index('    if view == "baseline":')
    end = app_src.index('    if view == "risk_reports":')
    block = app_src[start:end]

    check("静态G5 使用 get_baseline_display_text",
          "get_baseline_display_text(" in block)
    check("静态G5 风险链经过 risk_chain_label",
          "risk_chain_label(" in block)
    check("静态G5 影响经过 impact_label",
          "impact_label(" in block)
    check("静态G5 actions 经过 action_label",
          "action_label(" in block)
    check("静态G5 无 or baseline.get( 跨语言 fallback",
          "or baseline.get(" not in block)
    check("静态G5 无 or baseline[ 跨语言 fallback",
          'or baseline["' not in block)
    check("静态G5 无旧的 ai_text_lookup fallback",
          'ai_text_lookup(project, "baseline")' not in block)
    check("静态G5 Baseline 区块不写 ui_lang",
          'session_state["ui_lang"]' not in block)

    # G6: ui_lang 状态逻辑保持不变（仍只由语言选择器回调写入）。
    check("静态G6 on_ui_language_change 仍调用 set_current_language",
          "set_current_language(chosen)" in app_src)
    check("静态G6 app.py 未直接写 ui_lang",
          'session_state["ui_lang"]' not in app_src)


# ============================================================
# G. 统一 localized_content 协议（阶段 3）
# ============================================================

def test_g_unified_protocol():

    print("\n--- G. 统一 localized_content 协议（阶段 3）---")

    from utils import localized_content as lc

    # G1: 写入后新协议记录可被当前语言读出。
    project = {"baseline_risk": {"risk_chains": [DETERMINISTIC_CHAIN]}}

    ai_text_store(
        project,
        "baseline",
        {"risk_chains": ["新协议中文链"], "actions": ["新协议中文措施"]},
        language=ZH,
    )

    display = get_baseline_display_text(project, ZH)

    eq("G1 新协议记录 source = localized_archive",
       display["source"], SOURCE_LOCALIZED_ARCHIVE)
    eq("G1 新协议风险链被读出",
       display["risk_chains"], ["新协议中文链"])
    eq("G1 新协议 actions 被读出",
       display["actions"], ["新协议中文措施"])
    eq("G1 不需要生成", display["needs_generation"], False)

    # G2: 双写——新协议与旧 archive 同时存在，旧读者不受影响。
    check("G2 已写入新协议容器",
          lc.LOCALIZED_CONTENT_KEY in project)
    eq("G2 旧 archive 同步写入",
       project["ai_text_by_language"][ZH]["baseline"]["risk_chains"],
       ["新协议中文链"])
    eq("G2 旧 archive 读法与过去一致",
       ai_text_lookup(project, "baseline", None, ZH)["risk_chains"],
       ["新协议中文链"])

    # G3: 同一语言下，新协议记录优先于旧 archive。
    project = {
        "baseline_risk": {},
        "ai_text_by_language": {
            ZH: {"baseline": {"risk_chains": ["旧 archive 链"]}},
        },
    }

    record = lc.make_localized_record(
        "baseline", None, ZH, {"risk_chains": ["新协议链"]}
    )
    lc.localized_content_put(project, record, copy_on_write=False)

    eq("G3 新协议优先于旧 archive",
       get_baseline_display_text(project, ZH)["risk_chains"],
       ["新协议链"])
    eq("G3 旧 archive 未被删除",
       project["ai_text_by_language"][ZH]["baseline"]["risk_chains"],
       ["旧 archive 链"])

    # G4: 另一语言的新协议记录绝不是 fallback。
    project = {"baseline_risk": {"risk_chains": [DETERMINISTIC_CHAIN]}}
    record = lc.make_localized_record(
        "baseline", None, ZH, {"risk_chains": ["只有中文"]}
    )
    lc.localized_content_put(project, record, copy_on_write=False)

    en_display = get_baseline_display_text(project, EN)

    eq("G4 英文不读中文新协议记录",
       en_display["risk_chains"], [DETERMINISTIC_CHAIN])
    eq("G4 英文 source = legacy_current_language",
       en_display["source"], SOURCE_LEGACY_CURRENT_LANGUAGE)
    check("G4 英文区域不含中文",
          "只有中文" not in " | ".join(en_display["risk_chains"]))

    # G5: 生成一个语言的新协议记录不覆盖另一个语言。
    project = {}
    ai_text_store(project, "baseline", {"risk_chains": ["English chain"]}, language=EN)
    ai_text_store(project, "baseline", {"risk_chains": ["中文链"]}, language=ZH)

    eq("G5 英文新协议记录未被覆盖",
       ai_text_lookup(project, "baseline", None, EN)["risk_chains"],
       ["English chain"])
    eq("G5 中文新协议记录已写入",
       ai_text_lookup(project, "baseline", None, ZH)["risk_chains"],
       ["中文链"])
    eq("G5 英文显示仍是英文",
       get_baseline_display_text(project, EN)["risk_chains"],
       ["English chain"])
    eq("G5 中文显示是中文",
       get_baseline_display_text(project, ZH)["risk_chains"],
       ["中文链"])

    # G6: 空 / 纯空白 payload 的新记录不产生假 archive。
    project = {"baseline_risk": {"risk_chains": [DETERMINISTIC_CHAIN]}}
    record = lc.make_localized_record(
        "baseline", None, ZH, {"risk_chains": ["   ", ""], "actions": []}
    )
    lc.localized_content_put(project, record, copy_on_write=False)

    display = get_baseline_display_text(project, ZH)

    check("G6 空记录不算 archive",
          display["source"] != SOURCE_LOCALIZED_ARCHIVE,
          f"source={display['source']!r}")
    eq("G6 回落到确定性翻译",
       display["source"], SOURCE_DETERMINISTIC_LEGACY)

    rendered = _render(display, ZH)

    assert_no_english("G6 翻译结果无英文残留", rendered["risk_chains"])

    # G7: 非法 / 被篡改的新记录被忽略，且不产生跨语言回退。
    project = {"baseline_risk": {"risk_chains": [DETERMINISTIC_CHAIN]}}
    project["localized_content"] = {
        "baseline": {
            "project": {
                ZH: {
                    "schema_version": 99,
                    "payload": {"risk_chains": ["坏记录"]},
                }
            }
        }
    }

    display = get_baseline_display_text(project, ZH)

    ne("G7 非法新记录不被显示", display["risk_chains"], ["坏记录"])
    eq("G7 非法新记录被忽略", display["source"], SOURCE_DETERMINISTIC_LEGACY)

    # G8: 畸形 localized_content 容器安全降级。
    for malformed in (
        "not a dict",
        5,
        [],
        {"baseline": "x"},
        {"baseline": {"project": "x"}},
        {"baseline": {"project": {ZH: "x"}}},
    ):
        project = {
            "baseline_risk": {"risk_chains": [DETERMINISTIC_CHAIN]},
            "localized_content": malformed,
        }

        display = get_baseline_display_text(project, ZH)

        eq(f"G8 畸形容器 {malformed!r} 安全降级",
           display["source"], SOURCE_DETERMINISTIC_LEGACY)

    # G9: 读取新协议不修改 project，也不创建容器。
    project = {"baseline_risk": {"risk_chains": [DETERMINISTIC_CHAIN]}}
    snapshot = copy.deepcopy(project)

    get_baseline_display_text(project, ZH)
    ai_text_lookup(project, "baseline", None, ZH)

    eq("G9 读取不修改 project", project, snapshot)
    check("G9 读取不创建 localized_content",
          lc.LOCALIZED_CONTENT_KEY not in project)

    # G10: Baseline 的可翻译载荷不得携带数值字段（阶段 4 契约）。
    check("G10 契约声明了数值字段",
          {"score", "level"} <= set(lc.NON_TRANSLATABLE_FIELDS))
    check("G10 数值字段不能进可翻译 payload",
          lc.validate_translatable_payload(
              "baseline", {"risk_chains": ["x"], "score": 90, "level": "HIGH"}
          ) != [])
    check("G10 纯文本 payload 合法",
          lc.validate_translatable_payload(
              "baseline", {"risk_chains": ["x"], "impacts": [], "actions": []}
          ) == [])


# ============================================================
# H. Baseline / Daily Log 通用 AI 语言规则（registry 驱动）
# ============================================================

#: registry 里的 AI 语言名（含仍是 planned 的语言）。
EXPECTED_AI_NAMES = {
    "en": "English",
    "zh-CN": "Simplified Chinese",
    "es": "Spanish",
    "fr": "French",
    "pt-BR": "Brazilian Portuguese",
    "sw": "Swahili",
}

#: Baseline 评估与 Daily Log 分析共用的语言规则函数。
LANGUAGE_RULES_FUNCTION = "ai_output_language_rules"

#: 使用该规则的 prompt 构建函数。
LANGUAGE_RULES_CALLERS = ("ai_baseline_assessment", "ai_analyze_daily_log")


def _app_tree():
    return ast.parse(APP_PATH.read_text(encoding="utf-8"))


def _app_function_node(tree, name):
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _app_language_rules():
    """
    抽取 app.py 的通用语言规则函数，注入纯依赖后独立执行。

    ``import app`` 会立刻执行整个 Streamlit 页面，因此这里只编译目标函数
    节点，用真实的 ``utils.content_locale`` 与 registry ``ai_language_name``
    加一个假 ``st.session_state`` 执行它。
    """
    node = _app_function_node(_app_tree(), LANGUAGE_RULES_FUNCTION)

    module = ast.Module(body=[node], type_ignores=[])
    state = {}
    namespace = {
        "content_locale": content_locale,
        "ai_language_name": locale_registry.ai_language_name,
        "st": types.SimpleNamespace(session_state=state),
    }

    exec(
        compile(ast.fix_missing_locations(module), str(APP_PATH), "exec"),
        namespace,
    )

    return namespace[LANGUAGE_RULES_FUNCTION], state


def _binary_language_branches(path):
    """`locale == "zh-CN" ... else "English"` 形式的语言二元分支行号。"""

    def has(node, values):
        return any(
            isinstance(item, ast.Constant) and item.value in values
            for item in ast.walk(node)
        )

    hits = []

    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):

        if isinstance(node, ast.IfExp):
            branches = [node.body, node.orelse]
        elif isinstance(node, ast.If):
            branches = list(node.body) + list(node.orelse)
        else:
            continue

        if not has(node.test, ("zh-CN", "简体中文")):
            continue

        if any(has(branch, ("English",)) for branch in branches):
            hits.append(node.lineno)

    return hits


def test_h_ai_language_rules():
    print("\n--- H. Baseline / Daily Log 通用 AI 语言规则 ---")

    # H1: 语言名来自 registry 元数据，且不提前启用 planned 语言。
    for code, expected in EXPECTED_AI_NAMES.items():
        eq(f"H1 registry AI 语言名 {code}",
           locale_registry.ai_language_name(code), expected)

    check("H1 de/ar 仍是 planned",
          all(locale_registry.status(code) == "planned"
              for code in ("de", "ar")))
    check("H1 planned 语言不出现在 UI",
          all(code not in locale_registry.enabled_ui_locales()
              for code in ("de", "ar")))

    # H2: 规则文本按内容语言生成。
    rules, state = _app_language_rules()

    state["ui_lang"] = EN
    check("H2 en -> English", "in English." in rules())

    state["ui_lang"] = ZH
    text_zh = rules()
    check("H2 zh-CN -> Simplified Chinese",
          "in Simplified Chinese." in text_zh)
    check("H2 JSON 字段名保持英文",
          "Keep all JSON field names exactly as specified in English."
          in text_zh)
    check("H2 风险等级关键字保持英文",
          '"LOW", "MEDIUM" or "HIGH"' in text_zh)
    check("H2 S/L/E 保持整数",
          "severity, likelihood and exposure as integers 1-5" in text_zh)

    state["ui_lang"] = "xx-YY"
    check("H2 非法语言回落 English", "in English." in rules())

    # H3: 未启用语言回落 English；已启用的 es / fr 使用各自的 AI 语言名。
    state["ui_lang"] = "de"
    check("H3 未启用的 de 回落 English",
          "Write every human-readable text value in English." in rules())

    for code, expected in (("es", "Spanish"), ("fr", "French")):
        state["ui_lang"] = code
        check(f"H3 已启用的 {code} 使用 {expected}",
              f"Write every human-readable text value in {expected}." in rules())

    # H4: 规则只读。
    state.clear()
    state["ui_lang"] = ZH
    before = dict(state)
    rules()
    eq("H4 规则不修改 session state", state, before)

    # H5: Baseline 评估与 Daily Log 分析都使用同一份语言规则。
    app_src = APP_PATH.read_text(encoding="utf-8")
    tree = ast.parse(app_src)

    for caller in LANGUAGE_RULES_CALLERS:
        node = _app_function_node(tree, caller)
        segment = ast.get_source_segment(app_src, node)
        check(f"H5 {caller} 使用通用语言规则",
              f"{{{LANGUAGE_RULES_FUNCTION}()}}" in (segment or ""))

    # H6: 静态守卫 —— 无 locale 二元语言判断，且规则走 content locale 轴。
    eq("H6 app.py 无 zh-CN/English 二元语言分支",
       _binary_language_branches(APP_PATH), [])

    rules_node = _app_function_node(tree, LANGUAGE_RULES_FUNCTION)
    names = {
        getattr(node, "attr", None) or getattr(node, "id", None)
        for node in ast.walk(rules_node)
        if isinstance(node, (ast.Attribute, ast.Name))
    }

    check("H6 规则读取 content locale 轴",
          "get_content_locale" in names and "content_locale" in names)
    check("H6 规则使用 registry AI 语言名", "ai_language_name" in names)
    check("H6 规则不再读界面语言", "get_current_language" not in names)
    check("H6 规则没有硬编码语言名",
          not any(
              isinstance(node, ast.Constant)
              and node.value in ("English", "简体中文", "Simplified Chinese")
              for node in ast.walk(rules_node)
          ))


# ============================================================
# 主入口
# ============================================================

def main():

    print("=" * 58)
    print("RiskPilot Baseline 显示层语言回归测试")
    print("=" * 58)

    test_a_deterministic_english_in_chinese()
    test_b_current_language_archive_wins()
    test_c_missing_current_language_archive()
    test_d_legacy_deterministic_data()
    test_e_unknown_free_text()
    test_f_page_language_integrity()
    test_language_detection_rules()
    test_static_checks()
    test_g_unified_protocol()
    test_h_ai_language_rules()

    total = _PASSED + len(_FAILED)

    print("\n" + "=" * 58)
    print(f"结果：{_PASSED}/{total} 通过")

    if _FAILED:

        print("失败项：")

        for name in _FAILED:
            print(f"  - {name}")

        raise SystemExit(1)

    print("Baseline 显示层语言回归测试全部通过 ✅")


if __name__ == "__main__":

    main()
