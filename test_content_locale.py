# -*- coding: utf-8 -*-
"""
RiskPilot 阶段 3 —— 三条语言轴测试 (test_content_locale.py)

测试对象是纯模块 ``utils/content_locale.py``：

    ui_locale     界面语言（只读，写入点只有阶段 2 的 session kernel）
    content_locale 页面 AI 动态内容的目标语言（本模块负责）
    report_locale  报告导出语言（本模块只提供兼容转换）

约束：不启动 Streamlit、不联网、不调用 AI、不写 data/projects.json、
不导入 app.py。

覆盖场景：
    A1. ui_locale 默认决定 content_locale
    A2. 显式 override 独立于 ui_locale
    A3. 清除 override 后重新跟随 UI
    A4. 界面语言改变不覆盖 explicit override
    A5. 非法 / planned locale 被拒绝
    A6. report 语言改变不影响另外两条轴
    A7. 状态字典其他字段完全不变（纯读取）
    A8. 禁止语言轴反向互写
    A9. 静态守卫：不导入 Streamlit、不访问 session_state、不写项目数据

运行方式：
    python test_content_locale.py
"""

import ast
import copy
import pathlib

from utils import content_locale as cl
from utils import locale_registry as registry

MODULE_PATH = pathlib.Path(cl.__file__).resolve()

UI_KEY = "ui_lang"
CONTENT_KEY = "content_locale"

ZH = "zh-CN"
EN = "en"


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


# ============================================================
# 辅助
# ============================================================

#: 注册表里确实是 planned（不可选）的 locale，用来验证闸门。
PLANNED_LOCALE = "de"

#: 完全未注册的 locale 写法。
UNREGISTERED_LOCALE = "xx-YY"


def _state(**kwargs):
    return dict(kwargs)


def _selectable_locale():
    """一个确定可选的 locale（来自 registry，而不是硬编码表）。"""
    locales = registry.enabled_ui_locales()
    return locales[0] if locales else EN


# ============================================================
# A1. ui_locale 默认决定 content_locale
# ============================================================

def test_ui_locale_decides_content_locale():
    print("\n--- A1. ui_locale 默认决定 content_locale ---")

    for code in registry.enabled_ui_locales():
        state = _state(**{UI_KEY: code})
        eq(f"A1 ui={code} -> content={code}", cl.get_content_locale(state), code)
        eq(
            f"A1 effective(ui={code}) 一致",
            cl.effective_content_locale(state, code),
            code,
        )
        check(
            f"A1 ui={code} 没有创建 override",
            CONTENT_KEY not in state,
            repr(state),
        )

    # 空状态 -> registry 默认 locale（不是硬编码的 "en"）。
    empty = _state()
    eq("A1 空状态 -> registry 默认", cl.get_content_locale(empty), registry.default_locale())

    # 非法 ui_lang -> 默认，而不是抛异常。
    broken = _state(**{UI_KEY: UNREGISTERED_LOCALE})
    eq(
        "A1 非法 ui_lang -> 默认",
        cl.get_content_locale(broken),
        registry.default_locale(),
    )


# ============================================================
# A2 / A3 / A4. override 生命周期
# ============================================================

def test_override_lifecycle():
    print("\n--- A2/A3/A4. override 生命周期 ---")

    locales = registry.enabled_ui_locales()
    check("A2 至少两个可选 locale 才能验证独立性", len(locales) >= 2, str(locales))

    if len(locales) < 2:
        return

    ui, other = locales[0], locales[1]

    state = _state(**{UI_KEY: ui})

    returned = cl.set_content_locale(state, other)

    eq("A2 set 返回新的内容语言", returned, other)
    eq("A2 content_locale 已切换", cl.get_content_locale(state), other)
    eq("A2 ui_locale 未被修改", state.get(UI_KEY), ui)
    check("A2 override 已写入", state.get(CONTENT_KEY) == other, repr(state))

    # A4：界面语言改变不得覆盖 explicit override。
    state[UI_KEY] = ui
    eq("A4 界面语言改变后 override 保持", cl.get_content_locale(state), other)

    state[UI_KEY] = other
    state[UI_KEY] = ui
    eq("A4 反复切换 UI 后 override 仍保持", cl.get_content_locale(state), other)

    # A3：清除 override 后重新跟随 UI。
    removed = cl.clear_content_locale_override(state)

    eq("A3 返回被清除的 override", removed, other)
    eq("A3 重新跟随 ui_locale", cl.get_content_locale(state), ui)
    check("A3 override key 已移除", CONTENT_KEY not in state, repr(state))
    eq("A3 ui_locale 未被触碰", state.get(UI_KEY), ui)

    # 再清除一次：无副作用、不抛异常。
    snapshot = copy.deepcopy(state)
    eq("A3 重复清除返回 None", cl.clear_content_locale_override(state), None)
    eq("A3 重复清除不改动状态", state, snapshot)


# ============================================================
# A5. 非法 / planned locale 被拒绝
# ============================================================

def test_invalid_content_locale_rejected():
    print("\n--- A5. 非法 / planned locale 被拒绝 ---")

    eq("A5 sw 在注册表中是 planned", registry.status(PLANNED_LOCALE), "planned")
    check(
        "A5 sw 不是可选 UI locale",
        PLANNED_LOCALE not in registry.enabled_ui_locales(),
        str(registry.enabled_ui_locales()),
    )
    check("A5 planned 不能作为内容语言", not cl.is_content_locale(PLANNED_LOCALE))

    for candidate in (
        PLANNED_LOCALE,
        UNREGISTERED_LOCALE,
        "",
        "   ",
        "../etc/passwd",
        "en-US<script>",
        None,
        123,
        ["en"],
        {"locale": EN},
        "x" * 500,
    ):
        state = _state(**{UI_KEY: EN})
        before = copy.deepcopy(state)

        result = cl.set_content_locale(state, candidate)

        eq(
            f"A5 拒绝 {candidate!r}（保持当前值）",
            result,
            EN,
        )
        eq(f"A5 拒绝 {candidate!r}（不写 key）", state, before)

    # planned locale 也不允许通过 set 变成当前内容语言。
    state = _state(**{UI_KEY: ZH})
    cl.set_content_locale(state, PLANNED_LOCALE)
    eq("A5 planned 被拒后仍是 ui_locale", cl.get_content_locale(state), ZH)
    check("A5 planned 未写入", CONTENT_KEY not in state, repr(state))


# ============================================================
# A6. report locale 独立
# ============================================================

def test_report_axis_is_independent():
    print("\n--- A6. report 语言轴独立 ---")

    state = _state(**{UI_KEY: ZH, "risk_report_language": "English"})
    before = copy.deepcopy(state)

    # 报告语言变化只发生在它自己的 key 上。
    state["risk_report_language"] = "中文"

    eq("A6 内容语言不受报告语言影响", cl.get_content_locale(state), ZH)
    eq("A6 界面语言不受报告语言影响", state.get(UI_KEY), ZH)

    # 反向：设置内容语言不得修改报告语言。
    cl.set_content_locale(state, EN)
    eq("A6 设置内容语言不改报告语言", state.get("risk_report_language"), "中文")

    cl.clear_content_locale_override(state)

    # 报告语言转换是纯函数：不接触任何状态字典。
    frozen = copy.deepcopy(state)
    cl.report_language_to_locale("中文")
    cl.locale_to_report_language("zh-CN")
    eq("A6 转换函数不修改状态", state, frozen)

    # 只读快照包含三条轴信息。
    summary = cl.content_locale_summary(_state(**{UI_KEY: ZH}))
    eq("A6 快照 ui_locale", summary["ui_locale"], ZH)
    eq("A6 快照 content_locale", summary["content_locale"], ZH)
    eq("A6 快照无 override", summary["has_override"], False)
    eq("A6 快照 override 值", summary["override"], None)

    eq("A6 报告语言旧值保留在 state", before["risk_report_language"], "English")


# ============================================================
# A7. 纯读取：状态字典其他字段完全不变
# ============================================================

def test_state_mapping_is_never_mutated_by_reads():
    print("\n--- A7. 状态字典其他字段完全不变 ---")

    state = _state(
        **{
            UI_KEY: ZH,
            "selected_project": "Demo",
            "dashboard_view": "logs",
            "risk_report_language": "中文",
            "ui_language_selector": ZH,
        }
    )
    snapshot = copy.deepcopy(state)

    cl.get_content_locale(state)
    cl.effective_content_locale(state, EN)
    cl.content_locale_override(state)
    cl.content_locale_summary(state)

    eq("A7 读取后状态完全不变", state, snapshot)

    # 只有显式 override 才增加一个 key，而且只是那一个。
    cl.set_content_locale(state, EN)
    eq("A7 写入后仅多一个 key", set(state) - set(snapshot), {CONTENT_KEY})
    eq(
        "A7 其他字段值不变",
        {k: v for k, v in state.items() if k != CONTENT_KEY},
        snapshot,
    )


# ============================================================
# A8. 报告语言兼容映射
# ============================================================

def test_report_language_conversion():
    print("\n--- A8. report 语言兼容转换 ---")

    expected = {
        "中文": ZH,
        "English": EN,
        "Swahili": "sw",
        "Kiswahili": "sw",
        "Español": "es",
        "Portuguese": "pt-BR",
        "Português": "pt-BR",
        "Français": "fr",
        "Deutsch": "de",
        "Italiano": "it",
        "日本語": "ja",
        "한국어": "ko",
        "Bahasa Melayu": "ms",
        "Bahasa Indonesia": "id",
        "Hindi": "hi",
        # 已经是 locale code 的输入直接接受。
        "zh-CN": ZH,
        "en": EN,
        "sw": "sw",
    }

    for display, locale in expected.items():
        eq(f"A8 {display!r} -> {locale}", cl.report_language_to_locale(display), locale)

    # 大小写 / 空白容错。
    eq("A8 空白容错", cl.report_language_to_locale("  English  "), EN)
    eq("A8 大小写容错", cl.report_language_to_locale("english"), EN)

    # 未知 / 空值安全降级为 und，永不抛异常。
    for bad in (None, "", "   ", "???", "Local Language", 42, object()):
        eq(
            f"A8 未知 {bad!r} -> und",
            cl.report_language_to_locale(bad),
            cl.UNDETERMINED_LOCALE,
        )

    # 逆映射回到旧显示值。
    eq("A8 zh-CN -> 中文", cl.locale_to_report_language(ZH), "中文")
    eq("A8 en -> English", cl.locale_to_report_language(EN), "English")
    eq("A8 sw -> Swahili", cl.locale_to_report_language("sw"), "Swahili")
    eq("A8 未知 locale -> 默认显示值",
       cl.locale_to_report_language("zz-ZZ"), cl.DEFAULT_REPORT_LANGUAGE)
    eq("A8 None -> 默认显示值",
       cl.locale_to_report_language(None), cl.DEFAULT_REPORT_LANGUAGE)

    # 往返一致性（对固定选项）。
    for display, locale in (("中文", ZH), ("English", EN), ("Swahili", "sw")):
        eq(
            f"A8 往返 {display}",
            cl.locale_to_report_language(cl.report_language_to_locale(display)),
            display,
        )


# ============================================================
# A9. 静态守卫
# ============================================================

def _module_source():
    return MODULE_PATH.read_text(encoding="utf-8")


def _imported_modules(tree):
    """顶层模块名集合（只看 import 语句，不看注释 / docstring）。"""
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
    """所有点号属性链，例如 {'st.session_state', 'os.path.join'}。"""
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


def _loaded_names(tree):
    return {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}


def _called_names(tree):
    names = set()

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue

        func = node.func

        if isinstance(func, ast.Name):
            names.add(func.id)
        elif isinstance(func, ast.Attribute):
            names.add(func.attr)

    return names


def _store_target_keys(tree):
    """赋值 / 删除目标里出现的常量字符串 key（``state["x"] = ...``）。"""
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


def test_static_guards():
    print("\n--- A9. 静态守卫 ---")

    tree = ast.parse(_module_source())

    modules = _imported_modules(tree)
    chains = _attribute_chains(tree)
    names = _loaded_names(tree)
    called = _called_names(tree)

    check("A9 不导入 streamlit", "streamlit" not in modules, str(sorted(modules)))
    check("A9 不导入 app", "app" not in modules)
    check(
        "A9 不导入 i18n（避免 Streamlit 传递依赖）",
        "i18n" not in modules,
        str(sorted(modules)),
    )
    check(
        "A9 只依赖 stdlib + locale_registry",
        modules <= {"__future__", "typing", "utils"},
        str(sorted(modules)),
    )
    check(
        "A9 不导入重型 / 网络依赖",
        not (modules & {"pickle", "requests", "openai", "anthropic", "streamlit"}),
        str(sorted(modules)),
    )

    # 不访问任何 Streamlit 对象 / session_state。
    check(
        "A9 无 st.* 属性访问",
        not any(chain == "st" or chain.startswith("st.") for chain in chains),
        str(sorted(chains)),
    )
    check(
        "A9 不出现 session_state",
        "session_state" not in names
        and not any("session_state" in chain for chain in chains),
    )

    # 不调用持久化 / AI / 文件写入。
    forbidden_calls = {
        "persist",
        "save_db",
        "save_project",
        "save",
        "open",
        "completion",
        "chat_completion",
        "create",
        "post",
    }
    check(
        "A9 不调用 persist / 文件写入 / AI",
        not (called & forbidden_calls),
        str(sorted(called & forbidden_calls)),
    )

    # ui_lang 绝不被本模块写入。
    check(
        "A9 不以 ui_lang 作为写入目标",
        UI_KEY not in _store_target_keys(tree),
        str(_store_target_keys(tree)),
    )


# ============================================================
# 主入口
# ============================================================

def main():
    print("=" * 58)
    print("RiskPilot 阶段 3 —— 三条语言轴测试")
    print("=" * 58)

    test_ui_locale_decides_content_locale()
    test_override_lifecycle()
    test_invalid_content_locale_rejected()
    test_report_axis_is_independent()
    test_state_mapping_is_never_mutated_by_reads()
    test_report_language_conversion()
    test_static_guards()

    total = _PASSED + len(_FAILED)

    print("\n" + "=" * 58)
    print(f"结果：{_PASSED}/{total} 通过")

    if _FAILED:
        print("失败项：")
        for name in _FAILED:
            print(f"  - {name}")
        raise SystemExit(1)

    print("三条语言轴测试全部通过 ✅")


if __name__ == "__main__":
    main()
