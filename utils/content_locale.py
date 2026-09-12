# -*- coding: utf-8 -*-
"""
RiskPilot 三条语言轴中的 **content_locale** 轴（Streamlit-free）。

背景：RiskPilot 有三条彼此独立、**禁止反向互写**的语言轴
------------------------------------------------------------

1. ``ui_locale``      —— 界面语言。决定静态 UI 文案与确定性显示文本的语言。
   状态存于 ``st.session_state["ui_lang"]``（阶段 2 的 session key 兼容名），
   取值必须是 registry 中 ``ui_enabled`` 且非 ``planned`` 的 BCP 47 code。
   唯一写入口是阶段 2 的 ``utils/ui_language_state.py``（首次初始化 +
   界面语言选择器回调）。本模块**只读**它。

2. ``content_locale`` —— 页面 AI 动态内容的目标语言（本模块负责）。
   默认跟随 ``ui_locale``；只有存在**显式 override** 时才脱离它。
   本阶段不提供独立的选择器控件，但存取函数已经就位，未来可以让用户
   选择与界面不同的内容语言。

3. ``report_locale``  —— 报告导出的目标语言。与上面两条完全独立。
   当前报告中心仍以显示值（``English`` / ``中文`` / ``Swahili`` …）保存在
   ``st.session_state["risk_report_language"]``。本模块只提供**兼容转换
   函数**，不重写报告生成流程。

互不干扰（硬规则）
------------------
* :func:`set_content_locale` / :func:`clear_content_locale_override` 只写
  ``content_locale`` 这一个 key，绝不触碰 ``ui_lang``、选择器或报告语言。
* :func:`get_content_locale` / :func:`effective_content_locale` 是纯读取，
  绝不写任何 key（包括不创建 override）。
* 报告语言转换函数是纯函数，不读写任何状态。

安全规则
--------
* 不导入 Streamlit（可以脱离运行时单测）。
* 只操作调用方传入的 ``MutableMapping``。
* planned / 非法 locale **不能**成为当前内容语言：只接受 registry 里
  ``ui_enabled`` 且非 ``planned`` 的 code（与阶段 2 选择器同一把闸门）。
* 未知报告语言一律安全降级为 ``und``，永不抛异常。
* 本模块不写任何项目数据。
"""

from __future__ import annotations

from typing import Any, Dict, MutableMapping, Optional

from utils.locale_registry import (
    DEFAULT_LOCALE,
    canonicalize_locale,
    default_locale,
    to_ui_locale,
)

# ============================================================
# KEYS
# ============================================================

#: session key 保存显式的内容语言 override。
#: 只在用户显式选择与界面不同的内容语言时才存在；正常情况下该 key 不存在，
#: 内容语言始终跟随 ``ui_locale``。
CONTENT_LOCALE_KEY = "content_locale"

#: 界面语言 key（阶段 2 的 ``ui_language_state.LANGUAGE_KEY``）。
#: 这里重复声明而不是导入 ``ui_language_state``，是为了避免任何潜在的
#: 循环依赖，并保证本模块只依赖纯 registry。
UI_LOCALE_KEY = "ui_lang"

#: BCP 47 "und"（language undetermined，语言未确定）。
#: 用于语言元数据缺失或无法识别的旧数据 / 旧报告语言值。
UNDETERMINED_LOCALE = "und"

# ============================================================
# REPORT LOCALE —— 旧显示值 <-> BCP 47 兼容转换
# ============================================================
# 报告中心的 option **值** 从未被翻译，它会被直接放进 AI prompt，因此这里
# 只做"旧显示值 -> locale code"的兼容映射，不改变报告生成流程。
#
# 注意：映射表里的 code 允许是 **planned** locale（如 es / fr / sw），因为
# 报告语言是独立轴，不受 UI 可选语言的限制；但 content_locale 轴仍然
# 只接受 ui_enabled 的 locale。

#: 未知 / 空报告语言的安全默认显示值。
DEFAULT_REPORT_LANGUAGE = "English"

#: 报告语言显示值（app.py 的 ``LOCAL_LANGUAGE_MAP`` + 固定选项）-> BCP 47。
_REPORT_LANGUAGE_TO_LOCALE: Dict[str, str] = {
    # 固定选项 + 地图中的 native name
    "中文": "zh-CN",
    "English": "en",
    "Español": "es",
    "Português": "pt-BR",
    "Français": "fr",
    "Deutsch": "de",
    "Italiano": "it",
    "日本語": "ja",
    "한국어": "ko",
    "Swahili": "sw",
    "Kiswahili": "sw",
    "Bahasa Melayu": "ms",
    "Bahasa Indonesia": "id",
    "Hindi": "hi",
    # 本地 registry（``locales/manifest.json``）中的 native_name：这些是"语言自己
    # 的名字"，含义唯一、不存在猜测空间。阶段 4C-2 补齐它们，使报告语言轴可以直接
    # 接受 registry 的显示名（``locale_registry.locale_display_name``）。
    # 注意：这里**只**补明确别名，绝不把模糊名称猜成某种语言。
    "简体中文": "zh-CN",
    "Português (Brasil)": "pt-BR",
    "العربية": "ar",
    "हिन्दी": "hi",
    # ``LOCAL_LANGUAGE_MAP`` 里的 code 字段（英文语言名）
    "Chinese": "zh-CN",
    "Portuguese": "pt-BR",
    "French": "fr",
    "German": "de",
    "Spanish": "es",
    "Italian": "it",
    "Japanese": "ja",
    "Korean": "ko",
    "Malay": "ms",
    "Indonesian": "id",
    # registry 的 english_name（同样含义唯一）。
    "Arabic": "ar",
    "Portuguese (Brazil)": "pt-BR",
    # 旧版可能出现的写法
    "Simplified Chinese": "zh-CN",
    "Brazillian Portuguese": "pt-BR",
    "Brazilian Portuguese": "pt-BR",
}

#: locale code -> 报告语言显示值（逆映射，供 locale_to_report_language 使用）。
_LOCALE_TO_REPORT_LANGUAGE: Dict[str, str] = {
    "zh-CN": "中文",
    "en": "English",
    "es": "Español",
    "pt-BR": "Português",
    "fr": "Français",
    "de": "Deutsch",
    "it": "Italiano",
    "ja": "日本語",
    "ko": "한국어",
    "sw": "Swahili",
    "ms": "Bahasa Melayu",
    "id": "Bahasa Indonesia",
    "hi": "Hindi",
    # registry 的 11 种语言必须都能反向取回显示名：否则 ``locale_to_report_language``
    # 会把 ``ar`` 降级成 ``English``，再正推就变成 ``en`` —— 一次静默的语言漂移。
    "ar": "العربية",
}


def _report_language_index() -> Dict[str, str]:
    """``casefold`` 之后的显示值索引（中英文名大小写不敏感匹配）。"""
    index: Dict[str, str] = {}
    for display, code in _REPORT_LANGUAGE_TO_LOCALE.items():
        index.setdefault(display.strip().casefold(), code)
    return index


_REPORT_LANGUAGE_INDEX: Dict[str, str] = _report_language_index()


def report_language_to_locale(value: Any) -> str:
    """
    把报告中心的旧显示值转换成 BCP 47 locale。

    兼容 ``English`` / ``中文`` / ``Swahili`` / ``Español`` … 等旧显示值，
    也接受已经是 locale code 的输入（``"zh-CN"`` / ``"en"``）。

    无法识别时**安全返回** :data:`UNDETERMINED_LOCALE`（``"und"``），
    永不抛异常、永不返回 ``None``，因此调用方可以无条件使用返回值。
    """
    if value is None:
        return UNDETERMINED_LOCALE

    if not isinstance(value, str):
        try:
            value = str(value)
        except Exception:
            return UNDETERMINED_LOCALE

    cleaned = value.strip()

    if not cleaned:
        return UNDETERMINED_LOCALE

    mapped = _REPORT_LANGUAGE_INDEX.get(cleaned.casefold())

    if mapped:
        return mapped

    # 输入本身已经是合法的 BCP 47 code（含 registry 未注册但格式正确的，
    # 例如 "it"）：原样接受，避免把已经是 code 的值降级成 und。
    if _looks_like_locale_code(cleaned):
        canonical = canonicalize_locale(cleaned)
        return canonical if canonical else cleaned

    return UNDETERMINED_LOCALE


def locale_to_report_language(locale: Any) -> str:
    """
    把 locale 转换回报告中心的显示值。

    未知 / 非法 locale 返回 :data:`DEFAULT_REPORT_LANGUAGE`（``"English"``），
    保证报告选择器永远拿得到一个可用值，永不抛异常。
    """
    if locale is None:
        return DEFAULT_REPORT_LANGUAGE

    if not isinstance(locale, str):
        try:
            locale = str(locale)
        except Exception:
            return DEFAULT_REPORT_LANGUAGE

    cleaned = locale.strip()

    if not cleaned:
        return DEFAULT_REPORT_LANGUAGE

    canonical = canonicalize_locale(cleaned)

    for candidate in (canonical, cleaned):
        if candidate and candidate in _LOCALE_TO_REPORT_LANGUAGE:
            return _LOCALE_TO_REPORT_LANGUAGE[candidate]

    return DEFAULT_REPORT_LANGUAGE


def _looks_like_locale_code(value: str) -> bool:
    """轻量 BCP 47 形状检查（真正的校验由 locale_registry 负责）。"""
    if not value or len(value) > 64:
        return False

    parts = value.replace("_", "-").split("-")

    if not (2 <= len(parts[0]) <= 8) or not parts[0].isalpha():
        return False

    return all(1 <= len(part) <= 8 and part.isalnum() for part in parts[1:])


# ============================================================
# CONTENT LOCALE
# ============================================================


def _resolve_locale(value: Any) -> Optional[str]:
    """
    把值解析成可选 UI locale。

    只接受**字符串**。``locale_registry.to_ui_locale`` 为兼容重复的 URL
    查询参数（``?lang=en&lang=zh-CN``）会接受列表 / 元组，但语言轴的取值
    必须是单一 BCP 47 code，因此这里更严格：列表、数字、对象一律拒绝。
    """
    if not isinstance(value, str):
        return None

    return to_ui_locale(value)


def is_content_locale(code: Any) -> bool:
    """
    True 表示 ``code`` 可以作为**当前内容语言**。

    与界面语言使用同一把闸门：只有 registry 中 ``ui_enabled`` 且非
    ``planned`` 的 locale 才允许（planned / 非法 locale 一律拒绝）。
    非字符串取值（列表、数字、对象……）同样拒绝。
    """
    return _resolve_locale(code) is not None


def content_locale_override(state: MutableMapping) -> Optional[str]:
    """
    读取显式 override（仅在合法时返回），否则 ``None``。

    纯读取：不会写入 / 清理 ``state`` 中的任何 key。
    """
    if not isinstance(state, MutableMapping):
        return None

    return _resolve_locale(state.get(CONTENT_LOCALE_KEY))


def effective_content_locale(state: MutableMapping, ui_locale: Any = None) -> str:
    """
    计算当前生效的内容语言：显式 override 优先，否则跟随界面语言。

    ``ui_locale`` 显式传入时以它为准（便于纯函数式测试）；为 ``None`` 时
    从 ``state[UI_LOCALE_KEY]`` 读取。两者都无法解析时回落到默认 locale。

    纯读取：绝不写入 ``state``，因此"跟随 UI"是**推导**出来的，而不是把
    UI 语言复制成一个 override。
    """
    override = content_locale_override(state)

    if override is not None:
        return override

    candidate = ui_locale

    if candidate is None and isinstance(state, MutableMapping):
        candidate = state.get(UI_LOCALE_KEY)

    resolved = _resolve_locale(candidate)

    if resolved is not None:
        return resolved

    return default_locale()


def get_content_locale(state: MutableMapping, ui_locale: Any = None) -> str:
    """
    读取当前内容语言（显式 override 优先，否则跟随 ``ui_locale``）。

    这是业务模块**唯一**应当使用的读取入口，从而不必到处假设 ``ui_lang``。
    纯读取：不写任何 key。
    """
    return effective_content_locale(state, ui_locale)


def set_content_locale(state: MutableMapping, locale: Any) -> str:
    """
    显式设置内容语言 override。

    只接受可作为 UI 语言的 locale（planned / 非法值被**拒绝**，此时返回当前
    生效值且不写入任何 key）。只写 :data:`CONTENT_LOCALE_KEY`，绝不触碰
    ``ui_lang`` / 选择器 / 报告语言。

    本阶段 UI 不暴露该操作；它已经就位，供后续"界面语言与内容语言分离"
    的阶段使用。
    """
    if not isinstance(state, MutableMapping):
        return default_locale()

    resolved = _resolve_locale(locale)

    if resolved is None:
        # 拒绝：保持现状，不写入任何东西。
        return effective_content_locale(state)

    state[CONTENT_LOCALE_KEY] = resolved
    return resolved


def clear_content_locale_override(state: MutableMapping) -> Optional[str]:
    """
    清除显式 override，使内容语言重新跟随 ``ui_locale``。

    只删除 :data:`CONTENT_LOCALE_KEY` 这一个 key（不存在时不做任何改动），
    返回被清除的 override（原本就没有则返回 ``None``）。绝不触碰 ``ui_lang``。
    """
    if not isinstance(state, MutableMapping):
        return None

    previous = state.pop(CONTENT_LOCALE_KEY, None)

    return previous if is_content_locale(previous) else None


def content_locale_summary(
    state: MutableMapping,
    ui_locale: Any = None,
) -> Dict[str, Any]:
    """
    语言轴的只读快照（界面语言 / 内容语言 / override 状态），便于日志、审计
    与测试断言。

    注意：报告语言是**第三条独立轴**，它由 ``risk_report_language`` 自己
    保存，因此不出现在这份快照里（见 :func:`report_language_to_locale`）。

    返回::

        {
            "ui_locale": "en",          # 界面语言
            "content_locale": "en",     # 内容语言（可能被 override）
            "has_override": False,      # 是否存在显式 override
            "override": None,           # override 值本身
        }

    纯读取。
    """
    ui_candidate = ui_locale

    if ui_candidate is None and isinstance(state, MutableMapping):
        ui_candidate = state.get(UI_LOCALE_KEY)

    resolved_ui = _resolve_locale(ui_candidate)

    override = content_locale_override(state)

    return {
        "ui_locale": resolved_ui if resolved_ui is not None else default_locale(),
        "content_locale": effective_content_locale(state, ui_locale),
        "has_override": override is not None,
        "override": override,
    }


#: 便捷常量：内置默认 locale（与 registry 保持一致）。
DEFAULT_CONTENT_LOCALE = DEFAULT_LOCALE
