# -*- coding: utf-8 -*-
"""
RiskPilot locale registry 测试 (test_locale_registry.py)

阶段 2 目标：锁定 ``locales/manifest.json`` + ``utils/locale_registry.py``
这一唯一语言真相源。

测试是纯静态 / 纯内存的：
    * 不启动 Streamlit；
    * 不联网、不调用 AI；
    * 不写 data/projects.json；
    * 只在临时目录生成损坏的 manifest 以验证安全回落。

覆盖范围：
    A. registry schema（11 个 locale、唯一性、stable/experimental/planned、rtl、fallback）
    B. 文件一致性（enabled locale 必须有 JSON，planned 不需要）
    C. locale code 归一化与别名
    D. manifest 校验与安全回落（永不白屏、永不抛异常）
    E. fallback chain / direction / 显示名 / AI 语言名

运行方式：
    python test_locale_registry.py
"""

import json
import pathlib
import tempfile

from utils import locale_registry as registry


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


ROOT = pathlib.Path(__file__).resolve().parent
LOCALES_DIR = ROOT / "locales"

#: 阶段 2 要求注册的全部 locale（含 planned）。
EXPECTED_CODES = (
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

#: 阶段 2 正式可选的 UI 语言。
#: 阶段 2B：es 由 planned 升级为 experimental 并启用 UI。
#: 阶段 2C：fr 由 planned 升级为 experimental 并启用 UI。
#: 阶段 2D：pt-BR 由 planned 升级为 experimental 并启用 UI。
#: 阶段 2E：sw（Kiswahili）由 planned 升级为 experimental 并启用 UI。
EXPECTED_UI_CODES = ("en", "zh-CN", "es", "pt-BR", "fr", "sw")


# ============================================================
# A. registry schema
# ============================================================


def test_a_schema():
    print("\n--- A. registry schema ---")

    entries = registry.registry_summary()

    eq("A1 registry 校验无问题", registry.registry_problems(), [])
    eq("A2 注册 11 个 locale", len(entries), 11)
    eq(
        "A3 locale code 集合完全一致",
        tuple(e["code"] for e in entries),
        EXPECTED_CODES,
    )
    eq(
        "A4 code 唯一",
        len({e["code"] for e in entries}),
        len(entries),
    )

    by_code = {e["code"]: e for e in entries}

    for code in EXPECTED_CODES:
        entry = by_code.get(code)
        check(f"A5 {code} 存在", entry is not None)
        if not entry:
            continue
        check(f"A5 {code} native_name 非空", bool(entry["native_name"].strip()))
        check(f"A5 {code} english_name 非空", bool(entry["english_name"].strip()))
        check(f"A5 {code} ai_language_name 非空", bool(entry["ai_language_name"].strip()))
        check(
            f"A5 {code} status 合法",
            entry["status"] in registry.VALID_STATUSES,
            entry["status"],
        )
        check(
            f"A5 {code} direction 合法",
            entry["direction"] in registry.VALID_DIRECTIONS,
            entry["direction"],
        )

    eq("A6 en 为 stable", by_code["en"]["status"], "stable")
    eq("A7 en ui_enabled", by_code["en"]["ui_enabled"], True)
    eq("A8 zh-CN 为 stable", by_code["zh-CN"]["status"], "stable")
    eq("A9 zh-CN ui_enabled", by_code["zh-CN"]["ui_enabled"], True)
    eq("A9a es 为 experimental", by_code["es"]["status"], "experimental")
    eq("A9b es ui_enabled", by_code["es"]["ui_enabled"], True)
    eq("A9c fr 为 experimental", by_code["fr"]["status"], "experimental")
    eq("A9d fr ui_enabled", by_code["fr"]["ui_enabled"], True)

    planned = [c for c in EXPECTED_CODES if c not in EXPECTED_UI_CODES]
    eq("A10 planned locale 共 5 个", len(planned), 5)

    for code in planned:
        eq(f"A11 {code} status 为 planned", by_code[code]["status"], "planned")
        eq(f"A12 {code} 未启用", by_code[code]["ui_enabled"], False)

    eq("A13 ar 为 rtl", by_code["ar"]["direction"], "rtl")
    rtls = [c for c in EXPECTED_CODES if by_code[c]["direction"] == "rtl"]
    eq("A14 当前只有 ar 是 rtl", rtls, ["ar"])

    eq("A15 默认 locale 为 en", registry.default_locale(), "en")
    eq("A16 默认 locale 唯一", [e["is_default"] for e in entries].count(True), 1)

    # fallback 引用合法
    eq("A17 en 无 fallback", by_code["en"]["fallback"], None)
    for code in EXPECTED_CODES[1:]:
        eq(f"A18 {code} fallback 指向 en", by_code[code]["fallback"], "en")

    eq(
        "A19 所有 fallback 都指向已注册 locale",
        sorted({
            e["fallback"]
            for e in entries
            if e["fallback"] is not None
        } - set(EXPECTED_CODES)),
        [],
    )
    eq(
        "A20 fallback 无循环",
        registry._detect_fallback_cycle(
            [e["code"] for e in entries],
            {e["code"]: e["fallback"] for e in entries},
        ),
        None,
    )


# ============================================================
# B. 文件一致性
# ============================================================


def test_b_files():
    print("\n--- B. locale JSON 文件一致性 ---")

    eq("B1 enabled locale 都有 JSON", registry.missing_locale_files(), [])

    for code in EXPECTED_UI_CODES:
        check(
            f"B2 {code}.json 存在",
            (LOCALES_DIR / f"{code}.json").is_file(),
        )

    planned_without_json = [
        code
        for code in EXPECTED_CODES
        if code not in EXPECTED_UI_CODES
        and not (LOCALES_DIR / f"{code}.json").is_file()
    ]
    check(
        "B3 至少一个 planned locale 尚无 JSON（阶段 2 属预期）",
        len(planned_without_json) > 0,
        repr(planned_without_json),
    )
    eq(
        "B4 planned 缺 JSON 不产生问题",
        registry.registry_problems(),
        [],
    )
    eq(
        "B5 planned 不参与缺文件检查",
        [c for c in registry.missing_locale_files() if c not in EXPECTED_UI_CODES],
        [],
    )


# ============================================================
# C. 归一化
# ============================================================


def test_c_normalisation():
    print("\n--- C. locale 归一化与别名 ---")

    canonical_cases = {
        "en": "en",
        "EN": "en",
        "en-US": "en",
        "en-GB": "en",
        "zh": "zh-CN",
        "zh-CN": "zh-CN",
        "zh-cn": "zh-CN",
        "zh_CN": "zh-CN",
        "zh_Hans": "zh-CN",
        "zh-Hans": "zh-CN",
        "es": "es",
        "es-MX": "es",
        "es-419": "es",
        "pt-BR": "pt-BR",
        "pt_BR": "pt-BR",
        "pt-br": "pt-BR",
        "fr": "fr",
        "fr-CA": "fr",
        "sw": "sw",
        "sw-KE": "sw",
        "de": "de",
        "de-AT": "de",
        "ar": "ar",
        "ar-EG": "ar",
        "hi": "hi",
        "hi-IN": "hi",
        "ja": "ja",
        "ja-JP": "ja",
        "ko": "ko",
        "ko-KR": "ko",
    }

    for raw, expected in canonical_cases.items():
        eq(f"C1 {raw!r} -> {expected}", registry.canonicalize_locale(raw), expected)

    # 必须区分的边界
    eq("C2 pt-PT 不得变成 pt-BR", registry.canonicalize_locale("pt-PT"), None)
    eq("C3 pt-PT 回落 en（非 UI）", registry.normalize_locale("pt-PT"), "en")
    eq("C4 pt-PT 不是任何注册 locale", registry.is_registered_locale("pt-PT"), False)
    eq("C5 pt-PT 不是 UI locale", registry.is_ui_locale("pt-PT"), False)

    eq("C6 zh-Hant 不得变成 zh-CN", registry.canonicalize_locale("zh-Hant"), None)
    eq("C7 zh-TW 不得变成 zh-CN", registry.canonicalize_locale("zh-TW"), None)
    eq("C8 zh-HK 不得变成 zh-CN", registry.canonicalize_locale("zh-HK"), None)
    eq("C9 zh-Hant 回落 en", registry.normalize_locale("zh-Hant"), "en")

    # 非法 / 空 / 超长
    for bad in (None, "", "   ", "\n\t", "??", "!!", "1", "1234567890", "x" * 200):
        eq(
            f"C10 非法值 {bad!r} 回落 en",
            registry.normalize_locale(bad),
            "en",
        )

    eq("C11 未知语言回落 en", registry.normalize_locale("it"), "en")
    eq("C12 未知语言未注册", registry.is_registered_locale("it"), False)

    # registered / ui / planned 三个空间互相区分
    eq("C13 es 已注册", registry.is_registered_locale("es"), True)
    eq("C14 es 已启用为 UI locale", registry.is_ui_locale("es"), True)
    eq("C15 es 归一化保留 es", registry.normalize_locale("es"), "es")
    eq("C16 es 可进入 ui_lang", registry.to_ui_locale("es"), "es")
    eq("C17 es-MX 归一化到 es", registry.to_ui_locale("es-MX"), "es")
    eq("C18 en/zh-CN 可作为 UI", registry.to_ui_locale("zh-Hans"), "zh-CN")

    # planned locale 仍不得进入 ui_lang（es / fr / pt-BR / sw 启用后由 de 承担该安全断言）
    eq("C18a planned locale de 不可作为 UI", registry.is_ui_locale("de"), False)
    eq("C18b planned locale de 不能进入 ui_lang", registry.to_ui_locale("de"), None)

    eq(
        "C19 enabled 列表为 en/zh-CN/es/pt-BR/fr/sw",
        registry.enabled_ui_locales(),
        EXPECTED_UI_CODES,
    )
    eq(
        "C20 enabled 列表不含任何 planned locale",
        [c for c in registry.enabled_ui_locales() if c not in EXPECTED_UI_CODES],
        [],
    )
    eq(
        "C21 全部 code 包含 planned",
        registry.all_locale_codes(),
        EXPECTED_CODES,
    )


# ============================================================
# D. manifest 校验与安全回落
# ============================================================


def _broken_manifest_dir(payload, raw_text=None):
    """把 payload（或原始文本）写进临时 manifest，返回其路径。"""
    tmp = tempfile.mkdtemp(prefix="riskpilot-locale-")
    path = pathlib.Path(tmp) / "manifest.json"
    if raw_text is not None:
        path.write_text(raw_text, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _valid_entry(code, **overrides):
    entry = {
        "code": code,
        "native_name": code,
        "english_name": code,
        "fallback": None,
        "direction": "ltr",
        "ai_language_name": code,
        "status": "stable",
        "ui_enabled": True,
        "is_default": False,
    }
    entry.update(overrides)
    return entry


def test_d_validation_and_fallback():
    print("\n--- D. manifest 校验与安全回落 ---")

    # 真实 manifest 必须校验通过
    real = json.loads((LOCALES_DIR / "manifest.json").read_text(encoding="utf-8"))
    eq("D1 真实 manifest 校验通过", registry.validate_locale_entries(real), [])

    # --- validate_locale_entries 的纯函数校验能力 ---
    eq("D2 非数组被拒绝", bool(registry.validate_locale_entries({})), True)
    eq("D3 空数组被拒绝", bool(registry.validate_locale_entries([])), True)

    dupes = [_valid_entry("en", is_default=True), _valid_entry("en")]
    check("D4 重复 code 被检出", any("重复" in p for p in registry.validate_locale_entries(dupes)))

    cycle = [
        _valid_entry("en", is_default=True, fallback="zh-CN"),
        _valid_entry("zh-CN", fallback="en"),
    ]
    check(
        "D5 fallback 循环被检出",
        any("循环" in p for p in registry.validate_locale_entries(cycle)),
    )

    unknown_fb = [_valid_entry("en", is_default=True, fallback="xx")]
    check(
        "D6 fallback 指向未注册 locale 被检出",
        any("未注册" in p for p in registry.validate_locale_entries(unknown_fb)),
    )

    self_fb = [_valid_entry("en", is_default=True, fallback="en")]
    check(
        "D7 fallback 指向自身被检出",
        any("自身" in p for p in registry.validate_locale_entries(self_fb)),
    )

    planned_on = [
        _valid_entry("en", is_default=True),
        _valid_entry("es", status="planned", ui_enabled=True),
    ]
    check(
        "D8 planned 却 ui_enabled 被检出",
        any("planned" in p for p in registry.validate_locale_entries(planned_on)),
    )

    no_default = [_valid_entry("en")]
    check(
        "D9 缺少 is_default 被检出",
        any("is_default" in p for p in registry.validate_locale_entries(no_default)),
    )

    empty_name = [_valid_entry("en", is_default=True, native_name="  ")]
    check(
        "D10 空 native_name 被检出",
        any("native_name" in p for p in registry.validate_locale_entries(empty_name)),
    )

    bad_direction = [_valid_entry("en", is_default=True, direction="sideways")]
    check(
        "D11 非法 direction 被检出",
        any("direction" in p for p in registry.validate_locale_entries(bad_direction)),
    )

    bad_status = [_valid_entry("en", is_default=True, status="beta")]
    check(
        "D12 非法 status 被检出",
        any("status" in p for p in registry.validate_locale_entries(bad_status)),
    )

    # --- 加载器：任何损坏都安全回落到内置 en，绝不抛异常 ---
    broken_cases = {
        "D13 文件缺失": _broken_manifest_dir(None, raw_text=""),
        "D14 JSON 语法错误": _broken_manifest_dir(None, raw_text="{not json"),
        "D15 顶层是对象": _broken_manifest_dir({"locales": []}),
        "D16 重复 code": _broken_manifest_dir([_valid_entry("en", is_default=True), _valid_entry("en")]),
        "D17 fallback 循环": _broken_manifest_dir(cycle),
        "D18 fallback 未注册": _broken_manifest_dir(unknown_fb),
        "D19 planned 却启用": _broken_manifest_dir(planned_on),
        "D20 缺少默认": _broken_manifest_dir(no_default),
        "D21 空名称": _broken_manifest_dir(empty_name),
        "D22 非法 direction": _broken_manifest_dir(bad_direction),
    }

    for name, path in broken_cases.items():
        try:
            entries = registry.load_locale_registry(path)
            problems = registry.registry_problems(path)
            eq(f"{name}: 回落为仅 en", tuple(e.code for e in entries), ("en",))
            eq(f"{name}: enabled 仅 en", registry.enabled_ui_locales(path), ("en",))
            eq(f"{name}: 默认 en", registry.default_locale(path), "en")
            eq(f"{name}: 报告了问题", len(problems) > 0, True)
        except Exception as exc:  # pragma: no cover - 失败路径
            check(f"{name}: 不得抛异常", False, repr(exc))

    # 缺失的 manifest 路径
    missing = pathlib.Path(tempfile.mkdtemp(prefix="riskpilot-missing-")) / "nope.json"
    eq(
        "D23 路径不存在也安全回落",
        tuple(e.code for e in registry.load_locale_registry(missing)),
        ("en",),
    )

    # 合法但只有 en 的 manifest 可正常使用（例如阶段 1 之前的项目）
    only_en = _broken_manifest_dir([_valid_entry("en", is_default=True)])
    eq("D24 仅 en 的 manifest 可用", registry.enabled_ui_locales(only_en), ("en",))
    eq("D25 仅 en 时无问题", registry.registry_problems(only_en), [])

    # 全部 planned → 没有可用 UI locale → 回落
    all_planned = _broken_manifest_dir([
        _valid_entry("en", is_default=True, status="planned", ui_enabled=False),
    ])
    eq(
        "D26 没有任何可用 UI locale 时回落 en",
        registry.enabled_ui_locales(all_planned),
        ("en",),
    )

    # cache 不应污染真实 registry
    eq("D27 真实 registry 未被临时 manifest 污染", registry.enabled_ui_locales(), EXPECTED_UI_CODES)


# ============================================================
# E. 元数据查询
# ============================================================


def test_e_metadata():
    print("\n--- E. 元数据查询 ---")

    eq("E1 fallback_chain(zh-CN)", registry.fallback_chain("zh-CN"), ("zh-CN", "en"))
    eq("E2 fallback_chain(en)", registry.fallback_chain("en"), ("en",))
    eq("E3 fallback_chain(es)", registry.fallback_chain("es"), ("es", "en"))
    eq("E4 fallback_chain(未知) 收敛到默认", registry.fallback_chain("zz"), ("en",))
    eq("E5 fallback_chain(None)", registry.fallback_chain(None), ("en",))

    eq("E6 direction(ar)=rtl", registry.text_direction("ar"), "rtl")
    eq("E7 direction(ar-EG)=rtl", registry.text_direction("ar-EG"), "rtl")
    eq("E8 direction(zh-CN)=ltr", registry.text_direction("zh-CN"), "ltr")
    eq("E9 direction(未知)=ltr", registry.text_direction("zz"), "ltr")

    eq("E10 ai_language_name(zh-CN)", registry.ai_language_name("zh-CN"), "Simplified Chinese")
    eq("E11 ai_language_name(en)", registry.ai_language_name("en"), "English")
    eq("E12 ai_language_name(未知) 回落默认", registry.ai_language_name("zz"), "English")
    eq("E13 ai_language_name(pt-BR)", registry.ai_language_name("pt-BR"), "Brazilian Portuguese")

    eq("E14 显示名为原生名", registry.locale_display_name("zh-CN"), "简体中文")
    eq("E15 显示名(en)", registry.locale_display_name("en"), "English")
    eq("E16 显示名(planned 也可查)", registry.locale_display_name("ar"), "العربية")
    eq("E17 显示名(未知) 原样返回", registry.locale_display_name("zz"), "zz")
    eq("E18 显示名(None)", registry.locale_display_name(None), "")

    eq("E19 get_locale 别名容错", registry.get_locale("zh_Hans").code, "zh-CN")
    eq("E20 get_locale 未知返回 None", registry.get_locale("zz"), None)
    eq("E21 status(es)", registry.status("es"), "experimental")
    eq("E22 status(en)", registry.status("en"), "stable")
    eq("E22a status(planned locale)", registry.status("de"), "planned")

    eq(
        "E23 enabled map 为 en/zh-CN/es/pt-BR/fr/sw",
        registry.enabled_ui_locale_map(),
        {"en": "English", "zh-CN": "简体中文", "es": "Español",
         "pt-BR": "Português (Brasil)", "fr": "Français", "sw": "Kiswahili"},
    )
    eq(
        "E24 每个 locale 的 ai_language_name 非空",
        [c for c in EXPECTED_CODES if not registry.ai_language_name(c)],
        [],
    )


# ============================================================
# F. 输入清洗
# ============================================================


def test_f_sanitise():
    print("\n--- F. 输入清洗 ---")

    eq("F1 普通字符串", registry.sanitize_locale_candidate("zh-CN"), "zh-CN")
    eq("F2 去首尾空白", registry.sanitize_locale_candidate("  en  "), "en")
    eq("F3 去 BOM", registry.sanitize_locale_candidate("\ufeffzh-CN"), "zh-CN")
    eq("F4 去零宽字符", registry.sanitize_locale_candidate("zh\u200b-CN"), "zh-CN")
    eq("F5 去换行", registry.sanitize_locale_candidate("zh-CN\n"), "zh-CN")
    eq("F6 空字符串", registry.sanitize_locale_candidate(""), None)
    eq("F7 纯空白", registry.sanitize_locale_candidate("   "), None)
    eq("F8 纯控制字符", registry.sanitize_locale_candidate("\x00\x01"), None)
    eq("F9 None", registry.sanitize_locale_candidate(None), None)
    eq("F10 超长被拒绝", registry.sanitize_locale_candidate("x" * 500), None)
    eq("F11 单值列表", registry.sanitize_locale_candidate(["zh-CN"]), "zh-CN")
    eq(
        "F12 重复参数取第一个非空",
        registry.sanitize_locale_candidate(["", "zh-CN", "en"]),
        "zh-CN",
    )
    eq("F13 空列表", registry.sanitize_locale_candidate([]), None)
    eq("F14 全空列表", registry.sanitize_locale_candidate(["", ""]), None)
    eq("F15 元组", registry.sanitize_locale_candidate(("en",)), "en")
    eq("F16 数字强转", registry.sanitize_locale_candidate(123), "123")
    eq("F17 清洗后可归一化", registry.normalize_locale("\ufeff  zh_Hans "), "zh-CN")

    # 不得抛异常
    class _Weird:
        def __str__(self):
            raise RuntimeError("boom")

    try:
        eq("F18 异常对象安全返回 None", registry.sanitize_locale_candidate(_Weird()), None)
    except Exception as exc:  # pragma: no cover
        check("F18 异常对象安全返回 None", False, repr(exc))


# ============================================================
# 主入口
# ============================================================


def main():
    print("=" * 55)
    print("RiskPilot locale registry 测试")
    print("=" * 55)

    test_a_schema()
    test_b_files()
    test_c_normalisation()
    test_d_validation_and_fallback()
    test_e_metadata()
    test_f_sanitise()

    total = _PASSED + len(_FAILED)

    print("\n" + "=" * 55)
    print(f"结果：{_PASSED}/{total} 通过")

    if _FAILED:
        print("失败项：")
        for name in _FAILED:
            print(f"  - {name}")
        raise SystemExit(1)

    print("locale registry 测试全部通过 ✅")


if __name__ == "__main__":
    main()
