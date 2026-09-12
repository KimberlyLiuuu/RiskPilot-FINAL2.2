# -*- coding: utf-8 -*-
"""
RiskPilot 国际化审计脚本测试 (test_i18n_audit.py)
=================================================

覆盖 ``scripts/i18n_audit.py``（阶段 1 修订版 + 阶段 2 manifest 接入）：

* 纯函数（flatten / placeholder / 文案启发式）；
* AST 采集（t() 引用、映射表 key、硬编码候选）；
* **可达性分析**（import 图 → runtime / legacy / tests_and_tools）；
* **作用域语义**（--scope runtime / legacy / all 与退出码策略）；
* **locale manifest**（审计语言推导、planned 不要求 JSON、自洽性校验）；
* 真实仓库只读 smoke 审计。

特点：不导入 Streamlit、不导入 app.py、不联网、不写任何项目文件；
端到端用例在临时目录里构造最小 fixture。

运行方式：

    python test_i18n_audit.py

全部通过时打印汇总并返回退出码 0；任何一项失败则返回退出码 1。
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "scripts"))

import i18n_audit as audit  # noqa: E402  (路径注入后才能导入)


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


# ============================================================
# 纯函数
# ============================================================


def test_flatten_locale():
    flat = audit.flatten_locale({"a": {"b": {"c": "1"}}, "d": "2"})

    check("flatten 嵌套字典", flat == {"a.b.c": "1", "d": "2"}, repr(flat))
    check("flatten 空字典", audit.flatten_locale({}) == {})


def test_extract_placeholders():
    check("占位符：多个",
          audit.extract_placeholders("Ended {date} · {count} logs") == {"date", "count"})
    check("占位符：无", audit.extract_placeholders("plain text") == set())
    check("占位符：属性访问取根名", audit.extract_placeholders("{item.name}") == {"item"})
    check("占位符：非法花括号返回 None", audit.extract_placeholders("broken {") is None)
    check("位置占位符：{0}", audit.is_positional_placeholder("0") is True)
    check("位置占位符：命名参数", audit.is_positional_placeholder("name") is False)


def test_looks_like_ui_text():
    known = {"app.title", "common.language.label"}

    check("文案：含空格句子", audit.looks_like_ui_text("Hello world", known) is True)
    check("文案：首字母大写单词", audit.looks_like_ui_text("Cancel", known) is True)
    check("文案：中文字符", audit.looks_like_ui_text("取消", known) is True)
    check("文案：emoji + 文本", audit.looks_like_ui_text("➕ New Project", known) is True)
    check("文案：纯 emoji 排除", audit.looks_like_ui_text("🔴", known) is False)
    check("文案：配置值排除", audit.looks_like_ui_text("wide", known) is False)
    check("文案：locale key 排除", audit.looks_like_ui_text("app.title", known) is False)
    check("文案：URL 排除", audit.looks_like_ui_text("https://a.example", known) is False)
    check("文案：空串排除", audit.looks_like_ui_text("", known) is False)


# ============================================================
# AST 采集
# ============================================================


def test_analyze_source_translation_refs():
    source = (
        "from i18n import t\n"
        "import i18n\n"
        "\n"
        "t('app.title')\n"
        "t('app.caption', name='x')\n"
        "i18n.t('common.unknown')\n"
        "t(dynamic_key)\n"
    )

    result = audit.analyze_source(source, "sample.py", set())
    keys = sorted(call.key for call in result.literal_calls)

    check("采集字面量 t() key",
          keys == ["app.caption", "app.title", "common.unknown"], repr(keys))
    check("采集 t() kwargs", result.literal_calls[1].kwargs == ("name",),
          repr(result.literal_calls[1].kwargs))
    check("采集动态 t() 行号", result.dynamic_lines == [7], repr(result.dynamic_lines))
    check("字面量调用默认 scope=runtime",
          all(c.scope == audit.SCOPE_RUNTIME and c.reachable for c in result.literal_calls))


def test_analyze_source_mapping_keys():
    source = (
        "_PROJECT_TYPE_DISPLAY_KEYS = {\n"
        "    'Energy': 'enum.project_type.energy',\n"
        "    'Other': 'enum.project_type.other',\n"
        "}\n"
        "_NOT_A_KEY_CONTAINER = {'x': 'not a key'}\n"
    )

    result = audit.analyze_source(source, "sample.py", set())

    check("采集映射表 locale key",
          result.mapping_keys == {"enum.project_type.energy", "enum.project_type.other"},
          repr(result.mapping_keys))


def test_analyze_source_hardcoded():
    source = (
        "import streamlit as st\n"
        "\n"
        "st.markdown('Hello world')\n"
        "st.markdown(t('app.title'))\n"
        "st.metric(label='Countries', value='3')\n"
        "st.set_page_config(page_title='RiskPilot · Home', page_icon='➕')\n"
        "st.caption(f'Ended {date}')\n"
        "st.write('wide')\n"
        "st.switch_page('pages/x.py')\n"
    )

    result = audit.analyze_source(source, "sample.py", set())
    texts = sorted(candidate.text for candidate in result.hardcoded)

    check("硬编码候选文本集合",
          texts == ["Countries", "Ended", "Hello world", "RiskPilot · Home"], repr(texts))
    check("t() 包裹的文案不报硬编码", all("app.title" not in text for text in texts))
    check("f-string 片段被标记", any(c.is_fstring for c in result.hardcoded))
    check("set_page_config 只看 page_title",
          all(c.api != "st.set_page_config" or c.text == "RiskPilot · Home"
              for c in result.hardcoded))
    check("配置值与路径不报硬编码",
          "wide" not in texts and "pages/x.py" not in texts)


def test_analyze_source_scope_propagation():
    source = "import streamlit as st\n\nst.title('Legacy Title')\n"

    result = audit.analyze_source(source, "pages/old.py", set(), audit.SCOPE_LEGACY)

    check("legacy 作用域候选被标记 reachable=False",
          all(c.scope == audit.SCOPE_LEGACY and c.reachable is False
              for c in result.hardcoded),
          repr(result.hardcoded))


# ============================================================
# 可达性分析
# ============================================================


def locale_entry(code: str, **overrides) -> dict:
    """构造一条 manifest 记录（默认 stable + ui_enabled）。"""
    entry = {
        "code": code,
        "native_name": overrides.pop("native_name", code),
        "english_name": overrides.pop("english_name", code),
        "fallback": overrides.pop("fallback", None),
        "direction": overrides.pop("direction", "ltr"),
        "ai_language_name": overrides.pop("ai_language_name", code),
        "status": overrides.pop("status", "stable"),
        "ui_enabled": overrides.pop("ui_enabled", True),
    }
    entry.update(overrides)
    return entry


def write_manifest(root: Path, entries) -> None:
    """写入 ``<root>/locales/manifest.json``。"""
    (root / "locales").mkdir(parents=True, exist_ok=True)
    (root / "locales" / "manifest.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def _write_scope_fixture(root: Path, runtime_has_hardcoded: bool) -> None:
    """构造一个最小可达性 fixture：app.py -> helper.py，pages/ 不可达。"""
    (root / "locales").mkdir(parents=True, exist_ok=True)
    (root / "locales" / "en.json").write_text(
        json.dumps({"a": "Hello"}), encoding="utf-8")
    (root / "locales" / "zh-CN.json").write_text(
        json.dumps({"a": "你好"}), encoding="utf-8")
    write_manifest(root, [
        locale_entry("en", is_default=True),
        locale_entry("zh-CN", fallback="en"),
    ])

    (root / "app.py").write_text(
        "import helper\n"
        "import streamlit as st\n"
        "from i18n import t\n"
        "\n"
        "st.markdown(t('a'))\n",
        encoding="utf-8",
    )

    helper_body = "import streamlit as st\n"
    if runtime_has_hardcoded:
        helper_body += "\nst.markdown('Runtime Hardcoded')\n"
    (root / "helper.py").write_text(helper_body, encoding="utf-8")

    (root / "pages").mkdir(parents=True, exist_ok=True)
    (root / "pages" / "old_page.py").write_text(
        "import streamlit as st\n"
        "\n"
        "st.title('Legacy Hardcoded Title')\n",
        encoding="utf-8",
    )


def test_reachability_analysis():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_scope_fixture(root, runtime_has_hardcoded=False)

        reachable, index = audit.compute_reachable_modules(root, "app")

        check("可达性：app 自身可达", "app" in reachable)
        check("可达性：helper 通过 import 可达", "helper" in reachable)
        check("可达性：pages.old_page 不可达", "pages.old_page" not in reachable)
        check("模块索引包含 pages.old_page", "pages.old_page" in index)

        scopes = {fs.path: fs for fs in audit.classify_all_files(root, reachable, "app")}

        check("分类：app.py → runtime",
              scopes["app.py"].scope == audit.SCOPE_RUNTIME
              and scopes["app.py"].reachable is True)
        check("分类：helper.py → runtime",
              scopes["helper.py"].scope == audit.SCOPE_RUNTIME)
        check("分类：pages/old_page.py → legacy",
              scopes["pages/old_page.py"].scope == audit.SCOPE_LEGACY
              and scopes["pages/old_page.py"].reachable is False,
              scopes["pages/old_page.py"].reason)


# ============================================================
# 作用域语义（核心要求）
# ============================================================


def test_scope_runtime_not_blocked_by_legacy():
    """legacy 页面有硬编码时，默认 runtime 严格模式不得被它阻断。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_scope_fixture(root, runtime_has_hardcoded=False)

        report = audit.run_audit(root, scope=audit.SCOPE_RUNTIME)

        check("scope=runtime：只扫描可达文件",
              sorted(fs.path for fs in report.scanned_files) == ["app.py", "helper.py"],
              repr([fs.path for fs in report.scanned_files]))
        check("scope=runtime：硬编码候选为 0",
              len(report.candidates_in_scope(audit.SCOPE_RUNTIME)) == 0,
              repr([c.text for c in report.hardcoded]))
        check("scope=runtime：legacy 硬编码未计入 findings",
              report.count("warning", audit.SCOPE_LEGACY) == 0,
              repr(report.count("warning", audit.SCOPE_LEGACY)))
        check("scope=runtime：严格模式返回 0（未被 legacy 阻断）",
              audit.compute_exit_code(report, report_only=False, errors_only=False) == 0,
              f"errors={report.count('error')} warnings={report.count('warning')}")


def test_scope_legacy_reports_candidates():
    """--scope legacy 能报告旧页面里的硬编码候选。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_scope_fixture(root, runtime_has_hardcoded=False)

        report = audit.run_audit(root, scope=audit.SCOPE_LEGACY)
        legacy = report.candidates_in_scope(audit.SCOPE_LEGACY)

        check("scope=legacy：只扫描不可达文件",
              [fs.path for fs in report.scanned_files] == ["pages/old_page.py"],
              repr([fs.path for fs in report.scanned_files]))
        check("scope=legacy：报告硬编码候选",
              [c.text for c in legacy] == ["Legacy Hardcoded Title"],
              repr([c.text for c in legacy]))
        check("scope=legacy：候选 reachable=False",
              all(c.reachable is False for c in legacy))
        check("scope=legacy：legacy WARNING 不阻断发布",
              audit.compute_exit_code(report, report_only=False, errors_only=False) == 0,
              f"warnings={report.count('warning', audit.SCOPE_LEGACY)}")


def test_scope_all_groups_both():
    """--scope all 同时扫描并在报告中分组。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_scope_fixture(root, runtime_has_hardcoded=False)

        report = audit.run_audit(root, scope="all")
        text = audit.render_text(report)
        data = audit.report_to_dict(report)

        check("scope=all：扫描 runtime + legacy 共 3 个文件",
              len(report.scanned_files) == 3, repr([f.path for f in report.scanned_files]))
        check("scope=all：runtime 硬编码 0",
              data["hardcoded_by_scope"][audit.SCOPE_RUNTIME] == 0)
        check("scope=all：legacy 硬编码 1",
              data["hardcoded_by_scope"][audit.SCOPE_LEGACY] == 1,
              repr(data["hardcoded_by_scope"]))
        check("scope=all：文本报告含 RUNTIME 分组标题",
              "RUNTIME（当前产品真实运行的代码）" in text)
        check("scope=all：文本报告含 LEGACY 分组标题",
              "LEGACY（当前不会执行的旧代码" in text)
        check("scope=all：JSON 按作用域分组统计",
              set(data["severity_counts_by_scope"]) ==
              {audit.SCOPE_RUNTIME, audit.SCOPE_LEGACY, audit.SCOPE_TESTS_AND_TOOLS})


def test_runtime_hardcoded_blocks_strict():
    """runtime 文件中的硬编码仍会阻断严格模式。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_scope_fixture(root, runtime_has_hardcoded=True)

        report = audit.run_audit(root, scope=audit.SCOPE_RUNTIME)

        check("runtime 硬编码被识别",
              [c.text for c in report.candidates_in_scope(audit.SCOPE_RUNTIME)]
              == ["Runtime Hardcoded"],
              repr([c.text for c in report.hardcoded]))
        check("runtime 硬编码阻断严格模式",
              audit.compute_exit_code(report, report_only=False, errors_only=False) == 1)
        check("--errors-only 不受 runtime WARNING 影响",
              audit.compute_exit_code(report, report_only=False, errors_only=True) == 0)


def test_findings_carry_scope_and_reachable():
    """JSON 输出中每条 finding 必须包含 scope 与 reachable。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_scope_fixture(root, runtime_has_hardcoded=True)

        data = audit.report_to_dict(audit.run_audit(root, scope="all"))
        findings = data["findings"]

        check("findings 非空", len(findings) > 0, str(len(findings)))
        check("每条 finding 含 scope",
              all("scope" in f for f in findings))
        check("每条 finding 含 reachable",
              all("reachable" in f for f in findings))
        check("每条 finding 含 severity/category/location/detail",
              all({"severity", "category", "location", "detail"} <= set(f) for f in findings))
        check("hardcoded 候选含 scope 与 reachable",
              all({"scope", "reachable"} <= set(c) for c in data["hardcoded"]))
        check("JSON 可序列化",
              isinstance(json.loads(json.dumps(data)), dict))


def test_exit_code_policy():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_scope_fixture(root, runtime_has_hardcoded=True)

        report = audit.run_audit(root, scope="all")

        check("退出码：report-only 始终 0",
              audit.compute_exit_code(report, report_only=True, errors_only=False) == 0)
        check("退出码：严格模式遇到 runtime WARNING 返回 1",
              audit.compute_exit_code(report, report_only=False, errors_only=False) == 1)
        check("退出码：errors-only 无 runtime ERROR 返回 0",
              audit.compute_exit_code(report, report_only=False, errors_only=True) == 0)


def test_clean_fixture_all_modes_zero():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_scope_fixture(root, runtime_has_hardcoded=False)
        # 移除 legacy 页面里的硬编码，使 all 作用域也干净
        (root / "pages" / "old_page.py").write_text(
            "import streamlit as st\n\nst.title(t('a'))\n", encoding="utf-8")

        report = audit.run_audit(root, scope="all")

        check("干净 fixture：无 ERROR", report.count("error") == 0, repr(report.findings[:3]))
        check("干净 fixture：无 WARNING", report.count("warning") == 0,
              repr([(f.location, f.detail) for f in report.findings
                    if f.severity == "warning"][:3]))
        check("干净 fixture：严格模式返回 0",
              audit.compute_exit_code(report, report_only=False, errors_only=False) == 0)


# ============================================================
# locale manifest（阶段 2）
# ============================================================


def test_manifest_derives_audited_languages():
    """审计语言来自 manifest：planned 不参与审计，也不需要 JSON。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_scope_fixture(root, runtime_has_hardcoded=False)
        write_manifest(root, [
            locale_entry("en", is_default=True),
            locale_entry("zh-CN", fallback="en"),
            locale_entry("es", fallback="en", status="planned", ui_enabled=False),
            locale_entry("ar", fallback="en", status="planned",
                         ui_enabled=False, direction="rtl"),
        ])

        report = audit.run_audit(root, scope=audit.SCOPE_RUNTIME)

        check("manifest：审计语言取 UI 可选语言",
              report.languages == ["en", "zh-CN"], repr(report.languages))
        check("manifest：planned 不在 UI 可选集合",
              report.selectable_languages == ["en", "zh-CN"],
              repr(report.selectable_languages))
        check("manifest：planned 被识别",
              report.planned_languages == ["es", "ar"],
              repr(report.planned_languages))
        check("manifest：planned 缺少 JSON 不报错",
              report.missing_locale_files == [],
              repr(report.missing_locale_files))
        check("manifest：planned 不产生 manifest 问题",
              report.manifest_problems == [], repr(report.manifest_problems))
        check("manifest：direction 被读取",
              audit.report_to_dict(report)["locale_manifest"]["entries"][3]["direction"]
              == "rtl")
        check("manifest：默认 locale 被读取",
              report.manifest_default == "en", report.manifest_default)


def test_manifest_ui_locale_without_json_is_error():
    """UI 可选语言必须有对应 JSON；缺失记 ERROR 并阻断 runtime。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_scope_fixture(root, runtime_has_hardcoded=False)
        write_manifest(root, [
            locale_entry("en", is_default=True),
            locale_entry("zh-CN", fallback="en"),
            locale_entry("fr", fallback="en"),  # ui_enabled 但无 fr.json
        ])

        report = audit.run_audit(root, scope=audit.SCOPE_RUNTIME)

        check("manifest：UI 可选缺 JSON 被记录",
              report.missing_locale_files == ["fr"],
              repr(report.missing_locale_files))
        check("manifest：UI 可选缺 JSON 记 ERROR",
              report.count("error", audit.SCOPE_RUNTIME) > 0,
              repr([f.detail for f in report.findings
                    if f.category == "missing_locale_file"]))


def test_manifest_problems_are_errors():
    """manifest 自洽性：重复 code、fallback 悬空/成环、空名称均记 ERROR。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        # 重复 code + 空名称
        write_manifest(root, [
            locale_entry("en", is_default=True, native_name=""),
            locale_entry("en"),
        ])
        _entries, problems, _default = audit.load_locale_manifest(root / "locales")

        check("manifest：重复 code 被检出",
              any("重复" in p for p in problems), repr(problems))
        check("manifest：空 native_name 被检出",
              any("native_name" in p for p in problems), repr(problems))

        # fallback 成环（唯一 code）
        write_manifest(root, [
            locale_entry("en", is_default=True, fallback="zh-CN"),
            locale_entry("zh-CN", fallback="en"),
        ])
        _entries2, problems2, _default2 = audit.load_locale_manifest(root / "locales")

        check("manifest：fallback 成环被检出",
              any("循环" in p for p in problems2), repr(problems2))

        # fallback 指向未注册语言
        write_manifest(root, [
            locale_entry("en", is_default=True),
            locale_entry("zh-CN", fallback="xx"),
        ])
        _entries3, problems3, _default3 = audit.load_locale_manifest(root / "locales")

        check("manifest：fallback 悬空被检出",
              any("未注册" in p for p in problems3), repr(problems3))

        # 没有任何可用 UI locale
        write_manifest(root, [
            locale_entry("en", is_default=True, ui_enabled=False),
        ])
        _entries4, problems4, default_code = audit.load_locale_manifest(root / "locales")

        check("manifest：无可用 UI locale 被检出",
              any("可用" in p for p in problems4), repr(problems4))
        check("manifest：默认 locale 回退 en", default_code == "en", default_code)


def test_manifest_missing_file_is_error():
    """manifest 缺失：不改动退出码语义之外的行为，但必须显式记 ERROR。"""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "locales").mkdir(parents=True, exist_ok=True)
        (root / "locales" / "en.json").write_text(
            json.dumps({"a": "Hello"}), encoding="utf-8")
        (root / "app.py").write_text(
            "import streamlit as st\nfrom i18n import t\nst.markdown(t('a'))\n",
            encoding="utf-8")

        report = audit.run_audit(root, scope=audit.SCOPE_RUNTIME)

        check("manifest：缺失时记录问题",
              any("不存在" in p for p in report.manifest_problems),
              repr(report.manifest_problems))
        check("manifest：缺失时回退审计语言",
              report.languages == ["en", "zh-CN"], repr(report.languages))
        check("manifest：缺失记 ERROR",
              report.count("error", audit.SCOPE_RUNTIME) > 0)


def test_real_repository_manifest_is_valid():
    """真实仓库的 manifest 必须自洽，且审计语言由其推导。"""
    report = audit.run_audit(ROOT, scope="runtime")

    check("真实仓库：manifest 无问题",
          report.manifest_problems == [], repr(report.manifest_problems))
    check("真实仓库：UI 可选为 en / zh-CN / es / pt-BR / fr / sw",
          report.selectable_languages == ["en", "zh-CN", "es", "pt-BR", "fr", "sw"],
          repr(report.selectable_languages))
    check("真实仓库：planned 覆盖 5 种候选语言",
          sorted(report.planned_languages) ==
          sorted(["de", "ar", "hi", "ja", "ko"]),
          repr(report.planned_languages))
    check("真实仓库：planned locale 不要求 JSON",
          report.missing_locale_files == [], repr(report.missing_locale_files))
    check("真实仓库：ar 为 rtl",
          next(e.direction for e in report.manifest_entries if e.code == "ar") == "rtl")
    check("真实仓库：默认 locale 为 en",
          report.manifest_default == "en", report.manifest_default)


# ============================================================
# 真实仓库 smoke 审计（只读）
# ============================================================


def test_real_repository_reachability():
    report = audit.run_audit(ROOT, scope="all")
    scopes = {fs.path: fs for fs in report.file_scopes}

    check("真实仓库：app.py 为 runtime",
          scopes["app.py"].scope == audit.SCOPE_RUNTIME)
    check("真实仓库：i18n.py 为 runtime",
          scopes["i18n.py"].scope == audit.SCOPE_RUNTIME)
    check("真实仓库：vision_risk.py 为 runtime",
          scopes["vision_risk.py"].scope == audit.SCOPE_RUNTIME)
    check("真实仓库：agents/risk_agent.py 为 runtime（函数内延迟 import）",
          scopes["agents/risk_agent.py"].scope == audit.SCOPE_RUNTIME)
    check("真实仓库：utils/log_display.py 为 runtime",
          scopes["utils/log_display.py"].scope == audit.SCOPE_RUNTIME)
    check("真实仓库：utils/ui_language_state.py 为 runtime",
          scopes["utils/ui_language_state.py"].scope == audit.SCOPE_RUNTIME)

    # 阶段 3：统一内容协议必须属于产线可达代码，否则"数据协议"会变成死代码。
    for runtime_path in (
        "utils/locale_registry.py",
        "utils/content_locale.py",
        "utils/localized_content.py",
        "utils/baseline_display.py",
    ):
        check(f"真实仓库：{runtime_path} 为 runtime（阶段 3 协议可达）",
              scopes[runtime_path].scope == audit.SCOPE_RUNTIME,
              scopes[runtime_path].scope)

    for legacy_path in (
        "pages/baseline_risk.py",
        "pages/current_risk.py",
        "pages/new_project.py",
        "pages/project.py",
        "pages/project_dashboard.py",
        "pages/weather_terrain.py",
        "app_before_ui2.py",
        "agents/manager_agent.py",
        "utils/storage.py",
        "utils/weather.py",
    ):
        check(f"真实仓库：{legacy_path} 为 legacy/不可达",
              scopes[legacy_path].scope == audit.SCOPE_LEGACY,
              scopes[legacy_path].scope)

    check("真实仓库：test_*.py 归入 tests_and_tools",
          scopes["test_helpers.py"].scope == audit.SCOPE_TESTS_AND_TOOLS)
    check("真实仓库：审计脚本自身归入 tests_and_tools",
          scopes["scripts/i18n_audit.py"].scope == audit.SCOPE_TESTS_AND_TOOLS)


def test_real_repository_runtime_hardcoded_is_confined():
    """
    核心结论：当前产品可达代码不再有硬编码界面文案。

    * ``st.*`` 通道（页面 UI）硬编码为 0；
    * Word 导出的 docx ``add_run`` 通道硬编码为 0：标题 / 元数据标签 /
      章节名 / 系统枚举都通过 ``explicit_label`` 按报告语言解析；
    * runtime 剩余的 WARNING 只来自按设计使用动态 key 的 ``t()`` 调用；
    * 全部硬编码集中在 legacy 不可达代码。
    """
    report = audit.run_audit(ROOT, scope="all")

    runtime_candidates = report.candidates_in_scope(audit.SCOPE_RUNTIME)
    legacy_candidates = report.candidates_in_scope(audit.SCOPE_LEGACY)
    streamlit_runtime = [c for c in runtime_candidates if c.api.startswith("st.")]
    docx_runtime = [c for c in runtime_candidates if c.api.endswith("add_run")]
    dynamic_runtime = [
        call for call in report.dynamic_calls
        if call[2] == audit.SCOPE_RUNTIME
    ]

    check("真实仓库：runtime 的 st.* 通道硬编码为 0",
          streamlit_runtime == [],
          repr([(c.file, c.line, c.text) for c in streamlit_runtime[:5]]))
    check("真实仓库：runtime 硬编码候选为 0",
          runtime_candidates == [],
          repr([(c.file, c.line, c.api, c.text) for c in runtime_candidates[:8]]))
    check("真实仓库：DOCX 导出通道无固定英文标签",
          docx_runtime == [],
          repr([(c.file, c.line, c.text) for c in docx_runtime[:8]]))
    check("真实仓库：runtime WARNING 只来自动态 t() 调用",
          len(dynamic_runtime) == report.count("warning", audit.SCOPE_RUNTIME),
          f"dynamic={len(dynamic_runtime)} "
          f"warning={report.count('warning', audit.SCOPE_RUNTIME)}")
    check("真实仓库：legacy 硬编码候选 > 0（旧代码技术债）",
          len(legacy_candidates) > 0, str(len(legacy_candidates)))
    check("真实仓库：legacy 硬编码远多于 runtime",
          len(legacy_candidates) > len(runtime_candidates),
          f"legacy={len(legacy_candidates)} runtime={len(runtime_candidates)}")
    check("真实仓库：runtime ERROR 为 0",
          report.count("error", audit.SCOPE_RUNTIME) == 0,
          repr([f.location for f in report.findings
                if f.severity == "error" and f.scope == audit.SCOPE_RUNTIME][:5]))
    check("真实仓库：locale 双语言 key 数一致",
          report.key_sets.get("en") and report.key_sets.get("en") == report.key_sets.get("zh-CN"),
          f"en={len(report.key_sets.get('en', set()))} "
          f"zh={len(report.key_sets.get('zh-CN', set()))}")
    check("真实仓库：无跨语言占位符不一致",
          report.placeholder_mismatch == [], repr(report.placeholder_mismatch[:3]))
    check("真实仓库：runtime 引用 key 均存在",
          [m for m in report.missing_referenced_keys if m[3] == audit.SCOPE_RUNTIME] == [],
          repr([m[:3] for m in report.missing_referenced_keys][:5]))

    # 阶段 3：纯数据协议层不得引入任何硬编码界面文案。
    protocol_hardcoded = [
        c for c in runtime_candidates
        if c.file in ("utils/localized_content.py", "utils/content_locale.py")
    ]
    check("真实仓库：阶段 3 协议模块无硬编码字符串",
          protocol_hardcoded == [],
          repr([(c.file, c.line, c.text) for c in protocol_hardcoded[:5]]))


def test_real_repository_legacy_does_not_block():
    """旧代码的硬编码不得阻断当前产品发布。"""
    report = audit.run_audit(ROOT, scope="runtime")

    check("真实仓库：scope=runtime 不扫描 pages/*",
          not any(fs.path.startswith("pages/") for fs in report.scanned_files),
          repr([fs.path for fs in report.scanned_files]))
    check("真实仓库：scope=runtime 不扫描 app_before_ui2.py",
          not any(fs.path.endswith("app_before_ui2.py") for fs in report.scanned_files))
    check("真实仓库：scope=runtime 不扫描备份目录",
          not any(fs.path.startswith("backup-") for fs in report.scanned_files))
    check("真实仓库：legacy 字节不影响 runtime 阻断判定",
          report.count("warning", audit.SCOPE_LEGACY) == 0)
    check("真实仓库：runtime 结论由 runtime findings 决定（errors-only=0）",
          audit.compute_exit_code(report, report_only=False, errors_only=True) == 0)
    check("真实仓库：JSON 报告可序列化",
          isinstance(json.loads(json.dumps(audit.report_to_dict(report))), dict))


# ============================================================
# 主入口
# ============================================================


def main():
    test_flatten_locale()
    test_extract_placeholders()
    test_looks_like_ui_text()
    test_analyze_source_translation_refs()
    test_analyze_source_mapping_keys()
    test_analyze_source_hardcoded()
    test_analyze_source_scope_propagation()
    test_reachability_analysis()
    test_scope_runtime_not_blocked_by_legacy()
    test_scope_legacy_reports_candidates()
    test_scope_all_groups_both()
    test_runtime_hardcoded_blocks_strict()
    test_findings_carry_scope_and_reachable()
    test_exit_code_policy()
    test_clean_fixture_all_modes_zero()
    test_manifest_derives_audited_languages()
    test_manifest_ui_locale_without_json_is_error()
    test_manifest_problems_are_errors()
    test_manifest_missing_file_is_error()
    test_real_repository_manifest_is_valid()
    test_real_repository_reachability()
    test_real_repository_runtime_hardcoded_is_confined()
    test_real_repository_legacy_does_not_block()

    total = _PASSED + len(_FAILED)

    print("\n" + "=" * 55)
    print(f"结果：{_PASSED}/{total} 通过")

    if _FAILED:
        print("失败项：")
        for name in _FAILED:
            print(f"  - {name}")
        raise SystemExit(1)

    print("国际化审计脚本测试全部通过 ✅")


if __name__ == "__main__":
    main()
