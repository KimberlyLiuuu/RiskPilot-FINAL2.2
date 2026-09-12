# -*- coding: utf-8 -*-
"""
RiskPilot 界面语言状态回归测试 (test_ui_language_state.py)

目标：
    1. 用户没有操作语言选择器时，打开 / 切换项目不得改变 ui_lang；
    2. 阶段 2 新增的「URL -> 浏览器 -> 默认」初始化优先级正确；
    3. 整页刷新后语言由 ``?lang=`` 恢复；
    4. 语言切换不影响项目、页面视图、报告语言等内容状态。

测试对象是纯状态内核 ``utils/ui_language_state.py`` 与 locale registry，
它们只操作传入的 MutableMapping / 普通字符串，因此不需要真实 Streamlit
运行时，也不会触发网络请求、地图或 AI。

覆盖场景：
    A. 首次初始化（空 session_state、二次初始化不覆盖）
    B. 中文项目切换
    C. 英文项目切换
    D. 报告语言独立（risk_report_language 不影响 ui_lang）
    E. selector 状态恢复（只从 ui_lang 恢复，不反向覆盖）
    F. 用户主动切换（仅此时 ui_lang 才变化）
    H. 初始化优先级（URL / 浏览器 / 默认）
    I. 刷新持久化模拟（?lang= 往返）
    J. 语言切换不改变项目、页面视图与报告语言
    K. 恶意 / 畸形 URL 输入安全

另有静态守卫（基于 ast，不把注释 / docstring 误判为调用）：
    G1. 业务代码不得写入 ui_lang
    G2. set_current_language 只能由语言选择器回调调用
    G3. 项目选择函数不得出现 ui_lang 写入或 set_current_language 调用
    G4. risk_report_language 逻辑不得调用 set_current_language
    G5. 没有用 ui_lang 作为 widget key
    G6. 项目切换不得触碰 query lang
    G7. 报告语言逻辑不得触碰 query lang
    G8. selector options 来自 registry（不是手写语言列表）
    G9. i18n.py 不得手写语言注册表
    G10. app.py 不得硬编码 planned locale code
    G11. pages/* 未被接入新的语言状态（仍为不可达旧代码）
    G12. 语言切换回调不得触碰业务状态

运行方式：
    python test_ui_language_state.py
"""

import ast
import pathlib

import i18n

from utils import locale_registry

from utils.ui_language_state import (
    DEFAULT_LANGUAGE,
    LANGUAGE_KEY,
    SELECTOR_KEY,
    SUPPORTED_LANGUAGES,
    get_language,
    init_language,
    is_supported_language,
    normalize_language,
    resolve_initial_language,
    restore_selector,
    set_active_project,
    set_language,
)

#: 与 app.py 保持一致的 URL query 参数名。
LANG_QUERY_KEY = "lang"


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

    check(
        name,
        actual == expected,
        f"expected {expected!r}, got {actual!r}",
    )


def _simulate_selector_callback(state):
    """
    忠实重放 app.on_ui_language_change() 的语义：

        chosen = state[SELECTOR_KEY]
        if is_supported_language(chosen):
            set_current_language(chosen)   # -> 内核 set_language

    i18n.set_current_language 只是把 st.session_state 传给内核
    set_language，这里用内核函数等价重放。
    """
    chosen = state.get(SELECTOR_KEY)

    if is_supported_language(chosen):
        set_language(state, chosen)


# ============================================================
# 常量约束
# ============================================================

def test_constants():

    print("\n--- 常量约束 ---")

    eq("LANGUAGE_KEY == 'ui_lang'", LANGUAGE_KEY, "ui_lang")
    eq(
        "SELECTOR_KEY == 'ui_language_selector'",
        SELECTOR_KEY,
        "ui_language_selector",
    )
    check(
        "两个状态键互相独立",
        SELECTOR_KEY != LANGUAGE_KEY,
    )
    eq("默认语言为 en", DEFAULT_LANGUAGE, "en")
    check(
        "同时支持 en 与 zh-CN",
        {"en", "zh-CN"} <= set(SUPPORTED_LANGUAGES),
    )

    # --- 阶段 2：语言列表必须由 registry 派生，不得在此手写 ---
    eq(
        "SUPPORTED_LANGUAGES 完全等于 registry 的 enabled map",
        SUPPORTED_LANGUAGES,
        locale_registry.enabled_ui_locale_map(),
    )
    eq(
        "SUPPORTED_LANGUAGES 只含 en/zh-CN/es/pt-BR/fr/sw",
        tuple(SUPPORTED_LANGUAGES),
        ("en", "zh-CN", "es", "pt-BR", "fr", "sw"),
    )
    eq(
        "DEFAULT_LANGUAGE 来自 registry",
        DEFAULT_LANGUAGE,
        locale_registry.default_locale(),
    )
    eq(
        "planned locale 不在 SUPPORTED_LANGUAGES",
        [c for c in SUPPORTED_LANGUAGES if not locale_registry.is_ui_locale(c)],
        [],
    )
    check(
        "const: es 已注册且可选",
        locale_registry.is_registered_locale("es")
        and is_supported_language("es"),
    )
    check(
        "const: fr 已注册且可选",
        locale_registry.is_registered_locale("fr")
        and is_supported_language("fr"),
    )
    for planned in ("de", "ar", "hi", "ja", "ko"):
        check(
            f"const: {planned} 已注册但不可选",
            locale_registry.is_registered_locale(planned)
            and not is_supported_language(planned),
        )
    eq(
        "normalize_language 也对齐 registry",
        [normalize_language(c) for c in ("zh_Hans", "es", "pt-PT", "nope")],
        ["zh-CN", "es", "en", "en"],
    )


# ============================================================
# A. 首次初始化
# ============================================================

def test_a_first_initialisation():

    print("\n--- A. 首次初始化 ---")

    state = {}

    eq(
        "A1 空状态 init('zh-CN') 返回 zh-CN",
        init_language(state, "zh-CN"),
        "zh-CN",
    )
    eq("A2 ui_lang == zh-CN", state[LANGUAGE_KEY], "zh-CN")

    eq(
        "A3 再次 init('en') 不覆盖",
        init_language(state, "en"),
        "zh-CN",
    )
    eq("A4 ui_lang 仍为 zh-CN", state[LANGUAGE_KEY], "zh-CN")

    empty = {}
    eq(
        "A5 空状态无参 init 默认 en",
        init_language(empty),
        "en",
    )

    existing = {LANGUAGE_KEY: "en"}
    eq(
        "A6 已存在合法 ui_lang 时 init('zh-CN') 不覆盖",
        init_language(existing, "zh-CN"),
        "en",
    )
    eq("A7 ui_lang 仍为 en", existing[LANGUAGE_KEY], "en")


# ============================================================
# B. 中文项目切换
# ============================================================

def test_b_chinese_project_switching():

    print("\n--- B. 中文项目切换 ---")

    state = {LANGUAGE_KEY: "zh-CN"}

    set_active_project(state, "项目A")
    eq("B1 打开项目A后 ui_lang 仍为 zh-CN", state[LANGUAGE_KEY], "zh-CN")

    set_active_project(state, "项目B")
    eq("B2 打开项目B后 ui_lang 仍为 zh-CN", state[LANGUAGE_KEY], "zh-CN")

    set_active_project(state, "项目A")
    eq("B3 回到项目A后 ui_lang 仍为 zh-CN", state[LANGUAGE_KEY], "zh-CN")

    eq("B4 selected_project == 项目A", state["selected_project"], "项目A")
    eq("B5 dashboard_view == overview", state["dashboard_view"], "overview")
    eq(
        "B6 切换项目只新增 project/view 两个键",
        set(state),
        {LANGUAGE_KEY, "selected_project", "dashboard_view"},
    )


# ============================================================
# C. 英文项目切换
# ============================================================

def test_c_english_project_switching():

    print("\n--- C. 英文项目切换 ---")

    state = {LANGUAGE_KEY: "en"}

    for name in ["Alpha", "Beta", "Gamma", "Alpha"]:

        set_active_project(state, name)

        eq(
            f"C 切换到 {name} 后 ui_lang 仍为 en",
            state[LANGUAGE_KEY],
            "en",
        )

    eq("C5 selected_project == Alpha", state["selected_project"], "Alpha")


# ============================================================
# D. 报告语言独立
# ============================================================

def test_d_report_language_independent():

    print("\n--- D. 报告语言独立 ---")

    state = {
        LANGUAGE_KEY: "zh-CN",
        "risk_report_language": "en",
        "risk_report_result": "draft",
        "risk_report_language_used": "en",
        "risk_report_identity": {
            "project_id": "A",
            "report_locale": "en",
            "audience": "worker",
            "report_type": "daily_risk_alert",
        },
    }

    set_active_project(state, "A")

    # 模拟 clear_risk_report_state() 清理报告显示状态。
    for key in (
        "risk_report_result",
        "risk_report_editor",
        "risk_report_editor_box",
        "risk_report_language_used",
        "risk_report_identity",
    ):
        state.pop(key, None)

    set_active_project(state, "B")

    eq("D1 ui_lang 仍为 zh-CN", state[LANGUAGE_KEY], "zh-CN")
    eq(
        "D2 报告语言仍为 en（locale code）",
        state["risk_report_language"],
        "en",
    )
    check(
        "D3 报告显示状态已清理",
        "risk_report_result" not in state
        and "risk_report_identity" not in state,
    )

    en_state = {LANGUAGE_KEY: "en", "risk_report_language": "zh-CN"}
    set_active_project(en_state, "C")
    eq("D4 英文会话 ui_lang 仍为 en", en_state[LANGUAGE_KEY], "en")
    eq(
        "D5 报告语言仍为 zh-CN",
        en_state["risk_report_language"],
        "zh-CN",
    )


# ============================================================
# E. selector 状态恢复
# ============================================================

def test_e_selector_restore():

    print("\n--- E. selector 状态恢复 ---")

    state = {LANGUAGE_KEY: "zh-CN", SELECTOR_KEY: "zh-CN"}

    # 模拟 Streamlit 清理了未重新注册的 widget state。
    del state[SELECTOR_KEY]

    eq(
        "E1 restore 返回 ui_lang 值",
        restore_selector(state),
        "zh-CN",
    )
    eq(
        "E2 selector 从 ui_lang 恢复为 zh-CN",
        state[SELECTOR_KEY],
        "zh-CN",
    )
    eq("E3 ui_lang 未变", state[LANGUAGE_KEY], "zh-CN")

    # 反向保护：selector 缺失时绝不创建 / 覆盖 ui_lang。
    guard = {SELECTOR_KEY: "en"}
    restore_selector(guard)
    check(
        "E4 restore_selector 不会创建 ui_lang",
        LANGUAGE_KEY not in guard,
    )
    eq("E5 selector 保留 en", guard[SELECTOR_KEY], "en")

    # 非法 selector 被修复为 ui_lang，而不是把 ui_lang 拉回默认。
    invalid = {LANGUAGE_KEY: "zh-CN", SELECTOR_KEY: "xx"}
    restore_selector(invalid)
    eq(
        "E6 非法 selector 修复为 zh-CN",
        invalid[SELECTOR_KEY],
        "zh-CN",
    )
    eq("E7 ui_lang 仍未变", invalid[LANGUAGE_KEY], "zh-CN")

    # 英文会话同样成立。
    en_state = {LANGUAGE_KEY: "en"}
    restore_selector(en_state)
    eq(
        "E8 英文会话 selector 恢复为 en",
        en_state[SELECTOR_KEY],
        "en",
    )


# ============================================================
# F. 用户主动切换
# ============================================================

def test_f_user_switch():

    print("\n--- F. 用户主动切换 ---")

    state = {LANGUAGE_KEY: "zh-CN", SELECTOR_KEY: "zh-CN"}
    eq("F1 初始 ui_lang 为 zh-CN", state[LANGUAGE_KEY], "zh-CN")

    # 用户把选择器改成 en。
    state[SELECTOR_KEY] = "en"
    eq("F2 回调前 ui_lang 未变", state[LANGUAGE_KEY], "zh-CN")

    _simulate_selector_callback(state)
    eq("F3 回调后 ui_lang 变为 en", state[LANGUAGE_KEY], "en")

    # 反向：en -> zh-CN。
    state[SELECTOR_KEY] = "zh-CN"
    _simulate_selector_callback(state)
    eq("F4 回调后 ui_lang 变为 zh-CN", state[LANGUAGE_KEY], "zh-CN")

    # 非法选择被忽略。
    bad = {LANGUAGE_KEY: "en", SELECTOR_KEY: "xx"}
    _simulate_selector_callback(bad)
    eq("F5 非法选择不改变 ui_lang", bad[LANGUAGE_KEY], "en")

    # 缺少 selector 时忽略。
    missing = {LANGUAGE_KEY: "en"}
    _simulate_selector_callback(missing)
    eq("F6 缺失 selector 不改变 ui_lang", missing[LANGUAGE_KEY], "en")


# ============================================================
# i18n 桥接校验（不固定 get_current_language 的返回值）
# ============================================================

class _FakeStreamlit:
    """最小替身：只提供 i18n / 内核用到的 session_state。"""

    def __init__(self):

        self.session_state = {}


def test_i18n_bridge():

    print("\n--- i18n 桥接校验 ---")

    real_st = i18n.st
    fake = _FakeStreamlit()
    i18n.st = fake

    try:

        eq(
            "桥接1 空 session 初始化为 en",
            i18n.init_ui_language(),
            "en",
        )
        eq(
            "桥接2 set_current_language('zh-CN')",
            i18n.set_current_language("zh-CN"),
            "zh-CN",
        )
        eq(
            "桥接3 get_current_language == zh-CN",
            i18n.get_current_language(),
            "zh-CN",
        )
        eq(
            "桥接4 再次 init 不覆盖",
            i18n.init_ui_language("en"),
            "zh-CN",
        )
        eq(
            "桥接5 ui_lang 仍为 zh-CN",
            fake.session_state[LANGUAGE_KEY],
            "zh-CN",
        )
        eq(
            "桥接6 非法值被拒绝，保持 zh-CN",
            i18n.set_current_language("xx"),
            "zh-CN",
        )
        eq(
            "桥接7 非法值不写入 ui_lang",
            fake.session_state[LANGUAGE_KEY],
            "zh-CN",
        )
        eq(
            "桥接8 planned locale 被拒绝",
            i18n.set_current_language("de"),
            "zh-CN",
        )
        eq(
            "桥接9 planned locale 不写入 ui_lang",
            fake.session_state[LANGUAGE_KEY],
            "zh-CN",
        )
        eq(
            "桥接10 合法别名仍被接受",
            i18n.set_current_language("zh_Hans"),
            "zh-CN",
        )

    finally:

        i18n.st = real_st

    # 独立的 URL / 浏览器初始化桥接
    url_fake = _FakeStreamlit()
    i18n.st = url_fake

    try:

        eq(
            "桥接11 URL lang 生效",
            i18n.init_ui_language(url_language="zh-CN"),
            "zh-CN",
        )
        eq(
            "桥接12 URL lang 写入 ui_lang",
            url_fake.session_state[LANGUAGE_KEY],
            "zh-CN",
        )

        browser_fake = _FakeStreamlit()
        i18n.st = browser_fake

        eq(
            "桥接13 非法 URL 时回落到浏览器 locale",
            i18n.init_ui_language(url_language="not a locale", browser_language="zh-CN"),
            "zh-CN",
        )

        planned_fake = _FakeStreamlit()
        i18n.st = planned_fake

        eq(
            "桥接14 planned URL 不生效",
            i18n.init_ui_language(url_language="de", browser_language="pt-PT"),
            "en",
        )

        broken_fake = _FakeStreamlit()
        i18n.st = broken_fake

        eq(
            "桥接15 缺失候选回落 en",
            i18n.init_ui_language(),
            "en",
        )

    finally:

        i18n.st = real_st


# ============================================================
# H. 初始化优先级（URL / 浏览器 / 默认）
# ============================================================

def test_h_initialisation_priority():

    print("\n--- H. 初始化优先级 ---")

    eq("H1 URL 优先", init_language({}, "zh-CN"), "zh-CN")
    eq("H2 URL 优先于浏览器", init_language({}, "zh-CN", "en"), "zh-CN")
    eq("H3 URL 缺失时用浏览器", init_language({}, None, "zh-CN"), "zh-CN")
    eq("H4 URL 非法时用浏览器", init_language({}, "??", "zh-CN"), "zh-CN")
    eq("H5 URL planned 时用浏览器", init_language({}, "de", "zh-CN"), "zh-CN")
    eq("H6 URL 与浏览器都 planned 时回落默认", init_language({}, "de", "pt-PT"), "en")
    eq("H7 都缺失回落默认", init_language({}), "en")
    eq("H8 URL 别名归一化", init_language({}, "zh_Hans"), "zh-CN")
    eq("H9 浏览器别名归一化", init_language({}, None, "zh-Hans"), "zh-CN")
    eq("H10 空值安全", init_language({}, "", "   "), "en")

    # session 已有合法值时，候选一律被忽略 => 浏览器只在首次生效
    session = {LANGUAGE_KEY: "zh-CN"}

    eq("H11 普通 rerun 保持 session 语言", init_language(session, "en", "en"), "zh-CN")
    eq("H12 保持后未改写状态", session[LANGUAGE_KEY], "zh-CN")

    fresh = {}

    eq("H13 首次使用浏览器 locale", init_language(fresh, None, "zh-CN"), "zh-CN")
    eq("H14 第二次忽略浏览器候选", init_language(fresh, None, "en"), "zh-CN")

    # 幂等
    idem = {}
    results = [init_language(idem, "zh-CN", "en") for _ in range(5)]

    eq("H15 初始化幂等", results, ["zh-CN"] * 5)
    eq("H16 幂等且状态稳定", idem[LANGUAGE_KEY], "zh-CN")

    # 非法 / planned 的既有 session 值会被修复
    eq(
        "H17 非法 session 值被修复",
        init_language({LANGUAGE_KEY: "xx"}, None, "zh-CN"),
        "zh-CN",
    )
    eq(
        "H18 planned session 值被修复",
        init_language({LANGUAGE_KEY: "de"}, "zh-CN"),
        "zh-CN",
    )

    # 纯函数 resolve_initial_language
    eq("H19 resolve: URL 优先", resolve_initial_language("zh-CN", "en"), "zh-CN")
    eq("H20 resolve: 浏览器次之", resolve_initial_language("de", "zh-CN"), "zh-CN")
    eq("H21 resolve: 默认兜底", resolve_initial_language("de", "pt-PT"), "en")
    eq("H22 resolve: 正常输入", resolve_initial_language("zh-CN"), "zh-CN")
    eq("H22a resolve: es 已启用不再回落", resolve_initial_language("es"), "es")
    eq("H23 resolve: 空输入回落默认", resolve_initial_language(None, None), "en")


# ============================================================
# I. 刷新持久化模拟（?lang= 往返）
# ============================================================

def _app_bootstrap(session, query_params, browser_language=None):
    """
    忠实重放 app.py 的引导顺序：

        init_ui_language(url_language=read_language_query_param(),
                         browser_language=read_browser_locale())
        sync_language_query_param()

    query 参数读写在这里用普通 dict 取代 st.query_params，
    核心逻辑（清洗 / 归一化 / 优先级）完全复用生产代码。
    """
    url_language = locale_registry.sanitize_locale_candidate(
        query_params.get(LANG_QUERY_KEY)
    )

    init_language(session, url_language, browser_language)

    language = get_language(session)
    query_params[LANG_QUERY_KEY] = normalize_language(language)

    return language


def _app_language_callback(session, query_params, chosen):
    """
    忠实重放 app.on_ui_language_change()：

        if is_supported_language(chosen):
            set_current_language(chosen)
            sync_language_query_param()
    """
    session[SELECTOR_KEY] = chosen

    if not is_supported_language(session.get(SELECTOR_KEY)):

        return get_language(session)

    set_language(session, session[SELECTOR_KEY])

    language = get_language(session)
    query_params[LANG_QUERY_KEY] = normalize_language(language)

    return language


def test_i_refresh_persistence():

    print("\n--- I. 刷新持久化（?lang=） ---")

    # 场景 1：默认英文 -> 切到中文 -> 刷新
    query = {}
    session_1 = {}

    eq("I1 首次引导（无 lang）", _app_bootstrap(session_1, query), "en")
    eq("I2 URL 被正规化为 en", query[LANG_QUERY_KEY], "en")

    eq(
        "I3 用户切到 zh-CN",
        _app_language_callback(session_1, query, "zh-CN"),
        "zh-CN",
    )
    eq("I4 URL 同步为 zh-CN", query[LANG_QUERY_KEY], "zh-CN")
    eq("I5 ui_lang 为 zh-CN", session_1[LANGUAGE_KEY], "zh-CN")

    # 整页刷新：新 session，同一个 URL
    session_2 = {}

    eq("I6 刷新后仍为 zh-CN", _app_bootstrap(session_2, query), "zh-CN")
    eq("I7 新 session ui_lang", session_2[LANGUAGE_KEY], "zh-CN")
    eq("I8 URL 未被改写", query[LANG_QUERY_KEY], "zh-CN")

    # 场景 2：反向 zh-CN -> en -> 刷新
    eq("I9 用户切回 en", _app_language_callback(session_2, query, "en"), "en")
    eq("I10 URL 同步为 en", query[LANG_QUERY_KEY], "en")

    session_3 = {}

    eq("I11 刷新后为 en", _app_bootstrap(session_3, query), "en")

    # 场景 3：直接带 ?lang=zh-CN 打开新会话
    shared = {LANG_QUERY_KEY: "zh-CN"}

    eq("I12 分享链接生效", _app_bootstrap({}, shared), "zh-CN")

    # 场景 4：planned / 非法 URL 被规范化
    planned_query = {LANG_QUERY_KEY: "de"}

    eq("I13 planned URL 回落 en", _app_bootstrap({}, planned_query), "en")
    eq("I14 planned URL 被改写为 en", planned_query[LANG_QUERY_KEY], "en")

    invalid_query = {LANG_QUERY_KEY: "\x00 ??? "}

    eq("I15 非法 URL 回落 en", _app_bootstrap({}, invalid_query), "en")
    eq("I16 非法 URL 被改写为 en", invalid_query[LANG_QUERY_KEY], "en")

    # 场景 5：session 已有语言时不被 URL 反向覆盖
    sticky = {LANGUAGE_KEY: "zh-CN"}
    conflicting = {LANG_QUERY_KEY: "en"}

    eq(
        "I17 普通 rerun 不被 URL 反向覆盖",
        _app_bootstrap(sticky, conflicting),
        "zh-CN",
    )
    eq("I18 URL 被纠正为 zh-CN", conflicting[LANG_QUERY_KEY], "zh-CN")

    # 场景 6：selector 丢失后仍由 ui_lang 恢复
    dropped = {LANGUAGE_KEY: "zh-CN"}

    eq("I19 selector 丢失后恢复", restore_selector(dropped), "zh-CN")
    eq("I20 恢复不影响 ui_lang", dropped[LANGUAGE_KEY], "zh-CN")

    # 场景 7：反复 rerun 不会漂移
    stable = {}
    stable_query = {LANG_QUERY_KEY: "zh-CN"}
    languages = [_app_bootstrap(stable, stable_query) for _ in range(4)]

    eq("I21 多次 rerun 语言稳定", languages, ["zh-CN"] * 4)
    eq("I22 多次 rerun URL 稳定", stable_query[LANG_QUERY_KEY], "zh-CN")


# ============================================================
# J. 语言切换不改变项目、页面视图与报告语言
# ============================================================

def _rich_state():

    return {
        LANGUAGE_KEY: "en",
        SELECTOR_KEY: "en",
        "selected_project": "Project A",
        "dashboard_view": "logs",
        "projects": {"Project A": {"name": "Project A"}, "Project B": {}},
        "risk_report_language": "English",
        "risk_report_language_used": "English",
        "ai_text_by_language": {
            "en": {"baseline": {"x": 1}},
            "zh-CN": {"baseline": {}},
        },
        "current_log_id": "log-1",
        "baseline_risk": {"risk_level": "High", "risk_score": 72},
        "visual_risk_penalty": 5,
        "visual_risks": [{"title": "t"}],
    }


def test_j_language_switch_preserves_state():

    print("\n--- J. 语言切换不改变业务状态 ---")

    state = _rich_state()

    protected = {
        key: value
        for key, value in state.items()
        if key not in (LANGUAGE_KEY, SELECTOR_KEY)
    }

    # 语言切换
    set_language(state, "zh-CN")

    eq("J1 ui_lang 切换到 zh-CN", state[LANGUAGE_KEY], "zh-CN")

    for key, value in protected.items():

        check(
            f"J2 {key} 未被语言切换改动",
            state[key] == value,
            f"{state[key]!r} != {value!r}",
        )

    eq("J3 报告语言未变", state["risk_report_language"], "English")
    eq(
        "J4 内容语言归档未变",
        state["ai_text_by_language"],
        protected["ai_text_by_language"],
    )
    eq("J5 dashboard_view 未变", state["dashboard_view"], "logs")
    eq("J6 风险数值未变", state["baseline_risk"], protected["baseline_risk"])
    eq("J7 视觉风险 penalty 未变", state["visual_risk_penalty"], 5)
    eq("J8 视觉风险列表未变", state["visual_risks"], protected["visual_risks"])
    eq("J9 当前日志未变", state["current_log_id"], "log-1")

    # 语言切换后项目仍可正常打开，且打开项目不改变语言
    selector_before = state[SELECTOR_KEY]

    set_active_project(state, "Project B")

    eq("J10 打开项目只改项目", state["selected_project"], "Project B")
    eq("J11 打开项目不改变语言", state[LANGUAGE_KEY], "zh-CN")
    eq("J12 打开项目不改变 selector", state[SELECTOR_KEY], selector_before)
    eq("J13 打开项目重置视图", state["dashboard_view"], "overview")

    # 打开项目后再切语言，项目保持
    set_active_project(state, "Project A", view="logs")
    set_language(state, "en")

    eq("J14 切换语言后项目保持", state["selected_project"], "Project A")
    eq("J15 切换语言后视图保持", state["dashboard_view"], "logs")
    eq("J16 语言已切回 en", state[LANGUAGE_KEY], "en")


# ============================================================
# K. 恶意 / 畸形 URL 输入
# ============================================================

def test_k_hostile_url_input():

    print("\n--- K. 恶意 / 畸形 URL 输入 ---")

    hostile = [
        None,
        "",
        "   ",
        "\x00",
        "\ufeff",
        "\u200b",
        "\u202e\u202d",
        "a" * 5000,
        "zh-CN" * 200,
        "../../etc/passwd",
        "<script>alert(1)</script>",
        "zh-CN%00",
        "en\n\nSet-Cookie: x=1",
        "?",
        "&",
        "lang=zh-CN",
        ["zh-CN", "en"],
        ["", ""],
        [],
        (),
        123,
        3.14,
        {"a": 1},
        True,
        False,
        b"zh-CN",
    ]

    for value in hostile:

        try:

            resolved = init_language({}, value)

            check(
                f"K1 {value!r:.26} 安全且结果合法",
                is_supported_language(resolved),
                repr(resolved),
            )

        except Exception as exc:

            check(f"K1 {value!r:.26} 不得抛异常", False, repr(exc))

    # 脏输入仍能正确归一化
    eq("K2 大小写 / 分隔符 / 空白混合", init_language({}, "  ZH_hans  "), "zh-CN")
    eq("K3 列表取首个可用值", init_language({}, ["", "zh-CN"]), "zh-CN")
    eq("K4 超长输入被拒绝", init_language({}, "x" * 5000), "en")
    eq("K5 注入内容被拒绝", init_language({}, "<script>alert(1)</script>"), "en")
    eq("K6 路径穿越被拒绝", init_language({}, "../../etc/passwd"), "en")
    eq("K7 空字节被拒绝", init_language({}, "\x00"), "en")
    eq("K8 数字被拒绝", init_language({}, 123), "en")
    eq("K9 pt-PT 不会被误判为 pt-BR", init_language({}, "pt-PT"), "en")

    # 状态永远不会被污染成非法值
    poisoned = {}

    for value in hostile:

        init_language(poisoned, value)

        check(
            f"K10 状态始终合法 {value!r:.20}",
            is_supported_language(poisoned[LANGUAGE_KEY]),
            repr(poisoned.get(LANGUAGE_KEY)),
        )


# ============================================================
# 静态守卫（ast）
# ============================================================

ROOT = pathlib.Path(__file__).resolve().parent

_EXCLUDED_DIRS = {
    "backup-before-teammate-update",
    ".venv",
    ".venv-from-teammate",
    "__pycache__",
    ".git",
    ".codebuddy",
}

# 语言状态的定义处不参与「业务代码」扫描。
_EXCLUDED_FILES = {
    "i18n.py",
    "ui_language_state.py",
}

_PROJECT_FUNCS = {
    "open_project",
    "reopen_project",
    "end_project",
    "project_dashboard_page",
}


def _business_files():

    files = []

    for path in ROOT.rglob("*.py"):

        relative = path.relative_to(ROOT)

        if set(relative.parts[:-1]) & _EXCLUDED_DIRS:
            continue

        if relative.name.startswith("test_"):
            continue

        if relative.name in _EXCLUDED_FILES:
            continue

        files.append(path)

    return sorted(files)


def _parse_module(path):

    return ast.parse(
        path.read_text(encoding="utf-8"),
        filename=str(path),
    )


def _selectbox_options_for_key(path, key_marker):
    """
    返回 ``key=<key_marker>`` 的 selectbox 的 ``options=`` 表达式源码。

    用于锁定「语言选项来自 locale registry」这一不变量，而不是靠字符串
    搜索整个文件（那样会被注释或别的 selectbox 误判）。
    """
    sources = []

    for node in ast.walk(_parse_module(path)):

        if not isinstance(node, ast.Call):
            continue

        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "selectbox"):
            continue

        marker = ""
        options = None

        for kw in node.keywords:

            if kw.arg == "key" and kw.value is not None:
                marker = ast.unparse(kw.value)

            elif kw.arg == "options":
                options = kw.value

        if key_marker in marker:
            sources.append(ast.unparse(options) if options is not None else "")

    return sources


def _module_assignments(path):
    """模块级赋值目标名（用于确认 i18n.py 没有第二份语言表）。"""
    names = []

    for node in _parse_module(path).body:

        if isinstance(node, ast.Assign):

            names.extend(
                target.id
                for target in node.targets
                if isinstance(target, ast.Name)
            )

        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):

            names.append(node.target.id)

    return names


def _string_literals(path):
    """文件中出现的全部字符串字面量。"""
    return {
        node.value
        for node in ast.walk(_parse_module(path))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _callee_name(node):

    if isinstance(node, ast.Name):
        return node.id

    if isinstance(node, ast.Attribute):
        return node.attr

    return None


def _is_session_state(node):

    return isinstance(node, ast.Attribute) and node.attr == "session_state"


#: 触碰这些名字就说明该函数参与了 URL 语言的读写。
_QUERY_NAMES = {
    "query_params",
    "LANG_QUERY_KEY",
    "sync_language_query_param",
    "read_language_query_param",
}

#: 界面语言回调绝对不允许触碰的业务状态。
_BUSINESS_NAMES = {
    "selected_project",
    "dashboard_view",
    "projects",
    "ai_text_by_language",
    "risk_report_language",
    "risk_report_language_used",
    "current_log_id",
    "set_active_project",
    "open_project",
    "reopen_project",
    "end_project",
}


class _LanguageStateScanner(ast.NodeVisitor):
    """收集 ui_lang 写入、set_current_language 调用与报告语言引用。"""

    def __init__(self):

        self.func_stack = []
        self.set_language_calls = []
        self.ui_lang_writes = []
        self.report_language_refs = set()
        self.string_widget_keys = []
        self.call_names = []
        self.query_refs = set()
        self.name_refs = {}

    def _record_name(self, name):

        if not name:
            return

        self.name_refs.setdefault(self._current, set()).add(name)

        if name in _QUERY_NAMES:
            self.query_refs.add(self._current)

    @property
    def _current(self):

        return self.func_stack[-1] if self.func_stack else "<module>"

    def _enter(self, node):

        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()

    def visit_FunctionDef(self, node):

        self._enter(node)

    def visit_AsyncFunctionDef(self, node):

        self._enter(node)

    def visit_Call(self, node):

        callee = _callee_name(node.func)

        if callee is not None:

            self.call_names.append((self._current, callee))

            if callee in _QUERY_NAMES:

                self.query_refs.add(self._current)

            self.name_refs.setdefault(self._current, set()).add(callee)

        if callee == "set_current_language":

            self.set_language_calls.append(
                (self._current, node.lineno)
            )

        for kw in node.keywords:

            if kw.arg == "key" and isinstance(kw.value, ast.Constant):

                self.string_widget_keys.append(
                    (self._current, kw.value.value, node.lineno)
                )

        self.generic_visit(node)

    def _record_target(self, target, lineno):

        if isinstance(target, ast.Subscript):

            if (
                _is_session_state(target.value)
                and isinstance(target.slice, ast.Constant)
                and target.slice.value == LANGUAGE_KEY
            ):

                self.ui_lang_writes.append((self._current, lineno))

        elif isinstance(target, ast.Attribute):

            if target.attr == LANGUAGE_KEY:

                self.ui_lang_writes.append((self._current, lineno))

    def visit_Assign(self, node):

        for target in node.targets:

            self._record_target(target, node.lineno)

        self.generic_visit(node)

    def visit_AugAssign(self, node):

        self._record_target(node.target, node.lineno)
        self.generic_visit(node)

    def visit_AnnAssign(self, node):

        if node.target is not None:

            self._record_target(node.target, node.lineno)

        self.generic_visit(node)

    def visit_Name(self, node):

        self._record_name(node.id)

        if node.id == "risk_report_language":

            self.report_language_refs.add(self._current)

        self.generic_visit(node)

    def visit_Attribute(self, node):

        self._record_name(node.attr)

        if node.attr == "risk_report_language":

            self.report_language_refs.add(self._current)

        self.generic_visit(node)

    def visit_Constant(self, node):

        if (
            isinstance(node.value, str)
            and "risk_report_language" in node.value
        ):

            self.report_language_refs.add(self._current)

        self.generic_visit(node)


def test_static_guards():

    print("\n--- 静态守卫（ast）---")

    files = _business_files()

    check("静态G0 找到业务文件", len(files) > 0, f"{len(files)} 个")

    all_calls = []
    all_writes = []
    all_keys = []
    report_funcs = set()
    query_funcs = set()
    func_names = {}

    for path in files:

        relative = path.relative_to(ROOT)

        try:

            tree = ast.parse(
                path.read_text(encoding="utf-8"),
                filename=str(path),
            )

        except SyntaxError as exc:

            check(f"静态G0 解析 {relative}", False, str(exc))
            continue

        scanner = _LanguageStateScanner()
        scanner.visit(tree)

        all_calls.extend(
            (str(relative), func, lineno)
            for func, lineno in scanner.set_language_calls
        )
        all_writes.extend(
            (str(relative), func, lineno)
            for func, lineno in scanner.ui_lang_writes
        )
        all_keys.extend(
            (str(relative), func, value, lineno)
            for func, value, lineno in scanner.string_widget_keys
        )
        report_funcs |= scanner.report_language_refs
        query_funcs |= scanner.query_refs

        for func, names in scanner.name_refs.items():

            func_names.setdefault(func, set()).update(names)

    # G1：业务代码不得直接写 ui_lang。
    eq("静态G1 业务代码无 ui_lang 写入", all_writes, [])

    # G2：set_current_language 只能由语言选择器回调调用。
    callers = {func for _, func, _ in all_calls}

    eq(
        "静态G2 set_current_language 仅由 on_ui_language_change 调用",
        callers,
        {"on_ui_language_change"},
    )

    # G3：项目选择函数不得调用 set_current_language。
    offenders = sorted({
        func for _, func, _ in all_calls
        if func in _PROJECT_FUNCS
    })

    eq(
        "静态G3 项目选择函数不调用 set_current_language",
        offenders,
        [],
    )

    # G4：risk_report_language 逻辑不得调用 set_current_language。
    report_offenders = sorted({
        func for _, func, _ in all_calls
        if func in report_funcs
    })

    eq(
        "静态G4 报告语言逻辑不调用 set_current_language",
        report_offenders,
        [],
    )

    # G5：没有用 ui_lang 作为 widget key。
    bad_keys = [
        (relative, func, lineno)
        for relative, func, value, lineno in all_keys
        if value == LANGUAGE_KEY
    ]

    eq("静态G5 未使用 ui_lang 作为 widget key", bad_keys, [])

    # G6：项目切换不得触碰 query lang。
    # 只看真实函数：模块级需要 LANG_QUERY_KEY / 适配器定义，属正常引导代码。
    function_query_refs = query_funcs - {"<module>"}

    eq(
        "静态G6 项目函数不读写 URL 语言",
        sorted(function_query_refs & _PROJECT_FUNCS),
        [],
    )

    # G7：报告语言逻辑不得触碰 query lang。
    eq(
        "静态G7 报告语言逻辑不读写 URL 语言",
        sorted(function_query_refs & (report_funcs - {"<module>"})),
        [],
    )

    # G8：语言选择器选项来自 locale registry。
    options_sources = _selectbox_options_for_key(
        ROOT / "app.py",
        "UI_LANGUAGE_SELECTOR_KEY",
    )

    eq("静态G8a 找到语言 selectbox", len(options_sources), 1)
    check(
        "静态G8b 语言 options 来自 registry",
        bool(options_sources) and "enabled_ui_locales" in options_sources[0],
        repr(options_sources),
    )
    check(
        "静态G8c 语言 label 走 t()",
        "t(" in (ROOT / "app.py").read_text(encoding="utf-8"),
    )

    # G9：i18n.py 不得手写语言注册表。
    eq(
        "静态G9 i18n.py 未手写语言列表",
        [
            name
            for name in _module_assignments(ROOT / "i18n.py")
            if name in ("SUPPORTED_LANGUAGES", "DEFAULT_LANGUAGE", "LANGUAGES", "LOCALES")
        ],
        [],
    )

    # G10：app.py 不得硬编码 planned locale code。
    planned_codes = [
        code
        for code in locale_registry.all_locale_codes()
        if not locale_registry.is_ui_locale(code)
    ]

    eq("静态G10a 存在 planned locale", len(planned_codes) > 0, True)
    eq(
        "静态G10b app.py 未硬编码 planned code",
        sorted(_string_literals(ROOT / "app.py") & set(planned_codes)),
        [],
    )

    # G11：pages/* 仍是不可达旧代码，未接入新的语言状态。
    page_offenders = []

    for page_path in sorted((ROOT / "pages").glob("*.py")):

        source = page_path.read_text(encoding="utf-8")

        for marker in (
            "locale_registry",
            "ui_language_state",
            "query_params",
            "set_current_language",
        ):

            if marker in source:

                page_offenders.append((page_path.name, marker))

    eq("静态G11 pages/* 未接入新的语言状态", page_offenders, [])

    # G12：语言切换回调不得触碰任何业务状态。
    callback_names = func_names.get("on_ui_language_change", set())

    eq(
        "静态G12a 语言回调不触碰业务状态",
        sorted(callback_names & _BUSINESS_NAMES),
        [],
    )
    check(
        "静态G12b 语言回调写入 ui_lang",
        "set_current_language" in callback_names,
    )
    check(
        "静态G12c 语言回调同步 URL",
        "sync_language_query_param" in callback_names,
    )

    # G13：语言引导必须在页面配置之前完成（否则页面标题会滞后一轮）。
    app_source = (ROOT / "app.py").read_text(encoding="utf-8")
    check(
        "静态G13 init_ui_language 早于 set_page_config",
        app_source.find("init_ui_language(") < app_source.find("st.set_page_config("),
    )
    check(
        "静态G13b app.py 传入 URL 候选",
        "url_language=read_language_query_param()" in app_source,
    )
    check(
        "静态G13c app.py 传入浏览器候选",
        "browser_language=read_browser_locale()" in app_source,
    )
    check(
        "静态G13d app.py 回写 URL 语言",
        "sync_language_query_param()" in app_source,
    )
    check(
        "静态G13e 使用正式 st.context.locale API",
        "st.context.locale" in app_source,
    )


# ============================================================
# L. 三条语言轴集成（阶段 3）
# ============================================================

def test_l_three_language_axes():

    print("\n--- L. 三条语言轴集成（阶段 3）---")

    from utils import content_locale as cl

    # L1: 未显式设置时，内容语言由界面语言**推导**得出，不落盘。
    for ui in SUPPORTED_LANGUAGES:

        state = {LANGUAGE_KEY: ui}

        eq(f"L1 {ui}: 内容语言跟随界面语言",
           cl.get_content_locale(state), ui)
        check(f"L1 {ui}: 不创建 override",
              cl.CONTENT_LOCALE_KEY not in state)
        check(f"L1 {ui}: 不创建 ui_lang 之外的键",
              set(state) == {LANGUAGE_KEY},
              str(sorted(state)))

    # L2: 切换界面语言不会产生 / 改变 content_locale override。
    state = {LANGUAGE_KEY: "zh-CN"}

    set_active_project(state, "A")
    set_language(state, "en")

    eq("L2 切换界面语言后内容语言跟随", cl.get_content_locale(state), "en")
    check("L2 仍无 override", cl.CONTENT_LOCALE_KEY not in state)
    eq("L2 语言键只有 ui_lang",
       sorted(key for key in state if key in (LANGUAGE_KEY, cl.CONTENT_LOCALE_KEY)),
       [LANGUAGE_KEY])

    # L3: 显式设置内容语言只写 content_locale，绝不改 ui_lang / 报告语言。
    state = {LANGUAGE_KEY: "zh-CN", "risk_report_language": "English"}
    set_active_project(state, "A")

    returned = cl.set_content_locale(state, "en")

    eq("L3 返回生效内容语言", returned, "en")
    eq("L3 写入 override", state[cl.CONTENT_LOCALE_KEY], "en")
    eq("L3 内容语言已与界面语言分离", cl.get_content_locale(state), "en")
    eq("L3 ui_lang 未被改动", state[LANGUAGE_KEY], "zh-CN")
    eq("L3 报告语言未被改动", state["risk_report_language"], "English")

    # L4: 清除 override 后重新跟随界面语言。
    eq("L4 清除返回被删除的 override",
       cl.clear_content_locale_override(state), "en")
    check("L4 override 已删除", cl.CONTENT_LOCALE_KEY not in state)
    eq("L4 重新跟随界面语言", cl.get_content_locale(state), "zh-CN")
    eq("L4 ui_lang 仍未被改动", state[LANGUAGE_KEY], "zh-CN")
    eq("L4 报告语言仍未被改动", state["risk_report_language"], "English")

    # L5: planned / 非法 / 非字符串 override 一律拒绝，且不写任何键。
    for bad in ("xx-YY", "de", "und", "", "   ", None, 123, ["en"], {"a": 1}):

        state = {LANGUAGE_KEY: "zh-CN"}

        result = cl.set_content_locale(state, bad)

        check(f"L5 拒绝 override {bad!r}", cl.CONTENT_LOCALE_KEY not in state,
              f"state={state!r}")
        eq(f"L5 拒绝后返回当前生效值 {bad!r}", result, "zh-CN")
        check(f"L5 拒绝后 state 只有 ui_lang", set(state) == {LANGUAGE_KEY})

    # L6: 三条轴的完整矩阵——彼此互不干扰。
    for ui in ("zh-CN", "en"):
        for override in (None, "zh-CN", "en"):
            for report in ("中文", "English"):

                state = {LANGUAGE_KEY: ui, "risk_report_language": report}
                set_active_project(state, "A")

                if override is not None:
                    cl.set_content_locale(state, override)

                expected_content = override or ui
                label = f"ui={ui} override={override} report={report}"

                eq(f"L6 {label}: 内容语言",
                   cl.get_content_locale(state), expected_content)
                eq(f"L6 {label}: 界面语言未变", state[LANGUAGE_KEY], ui)
                eq(f"L6 {label}: 报告语言未变",
                   state["risk_report_language"], report)
                eq(f"L6 {label}: 报告轴只由报告语言决定",
                   cl.report_language_to_locale(state["risk_report_language"]),
                   "zh-CN" if report == "中文" else "en")

    # L7: 只读快照与单独取值一致，且不修改 state。
    state = {LANGUAGE_KEY: "zh-CN"}
    cl.set_content_locale(state, "en")

    summary = cl.content_locale_summary(state)

    eq("L7 ui_locale", summary["ui_locale"], "zh-CN")
    eq("L7 content_locale", summary["content_locale"], "en")
    eq("L7 has_override", summary["has_override"], True)
    eq("L7 override", summary["override"], "en")

    before = dict(state)
    cl.content_locale_summary(state)
    eq("L7 summary 是纯读取", state, before)

    # L8: 项目切换 / selector 恢复都不触碰内容语言 override。
    state = {LANGUAGE_KEY: "en", SELECTOR_KEY: "en"}

    set_active_project(state, "A")
    set_active_project(state, "B")
    restore_selector(state)

    check("L8 项目切换不创建 override", cl.CONTENT_LOCALE_KEY not in state)
    eq("L8 ui_lang 未被改动", state[LANGUAGE_KEY], "en")

    # L9: 静态守卫——内容语言轴不得写 ui_lang / 不得依赖 Streamlit。
    path = pathlib.Path(cl.__file__).resolve()
    tree = ast.parse(path.read_text(encoding="utf-8"))

    store_keys = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        else:
            continue

        for target in targets:
            if (
                isinstance(target, ast.Subscript)
                and isinstance(target.slice, ast.Constant)
                and isinstance(target.slice.value, str)
            ):
                store_keys.append(target.slice.value)

    check("L9 content_locale 不写 ui_lang",
          LANGUAGE_KEY not in store_keys, str(sorted(set(store_keys))))
    check("L9 content_locale 只写自己的 key",
          set(store_keys) <= {cl.CONTENT_LOCALE_KEY},
          str(sorted(set(store_keys))))

    names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    }
    modules = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])

    check("L9 content_locale 不导入 streamlit", "streamlit" not in modules)
    check("L9 content_locale 不导入 app", "app" not in modules)
    check("L9 content_locale 不访问 session_state",
          "session_state" not in names
          and not any(
              isinstance(node, ast.Attribute)
              and node.attr == "session_state"
              for node in ast.walk(tree)
          ))


# ============================================================
# 主入口
# ============================================================

def main():

    print("=" * 55)
    print("RiskPilot 界面语言状态回归测试")
    print("=" * 55)

    test_constants()
    test_a_first_initialisation()
    test_b_chinese_project_switching()
    test_c_english_project_switching()
    test_d_report_language_independent()
    test_e_selector_restore()
    test_f_user_switch()
    test_h_initialisation_priority()
    test_i_refresh_persistence()
    test_j_language_switch_preserves_state()
    test_k_hostile_url_input()
    test_i18n_bridge()
    test_static_guards()
    test_l_three_language_axes()

    total = _PASSED + len(_FAILED)

    print("\n" + "=" * 55)
    print(f"结果：{_PASSED}/{total} 通过")

    if _FAILED:

        print("失败项：")

        for name in _FAILED:

            print(f"  - {name}")

        raise SystemExit(1)

    print("界面语言状态回归测试全部通过 ✅")


if __name__ == "__main__":

    main()
