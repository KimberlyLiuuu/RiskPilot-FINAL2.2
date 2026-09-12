#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
RiskPilot i18n 静态审计脚本 (scripts/i18n_audit.py)
====================================================

全球化阶段 1（修订版）的只读审计工具。它只做静态分析，绝不运行应用：

    * 不导入 / 不执行 Streamlit app（app.py、vision_risk.py）
    * 不请求网络、不调用任何 AI
    * 不写 data/projects.json，也不写任何文件（结果只打印到 stdout）

核心：可达性优先（reachability-first）
-------------------------------------
唯一启动命令是 ``streamlit run app.py``，且 ``app.py`` 使用
``st.navigation(...)`` 注册页面，因此传统 ``pages/`` 自动发现被禁用。
本脚本先从 ``app.py`` 出发构建 AST import 图，据此判定每个文件的运行时可
达性，而不是按文件名猜测：

runtime
    从 ``app.py`` 出发（含函数体内的延迟 import）可达的生产代码。
legacy
    当前不会执行的代码：旧版 ``pages/*``、``app_before_ui2.py``、
    备份目录，以及未被入口导入的孤立模块。
tests_and_tools
    测试、审计脚本与开发工具。

默认 ``--scope runtime``：只审计真实可达的生产代码。旧代码的硬编码不会
阻断当前产品发布，而是单独列为 legacy debt。

审计内容
--------
1. locale key 集合：各语言之间缺失 / 多余的 key。
2. 占位符一致性：跨语言、以及 ``t("key", **kwargs)`` 调用与 locale 占位符。
3. ``t()`` 引用：字面量 key、映射表 key、动态 key、"代码引用但 locale 缺失"。
4. 硬编码候选：``st.*`` 调用中直接传入的字符串字面量 / f-string 片段。

运行方式
--------
    python scripts/i18n_audit.py --scope runtime                 # 严格（默认）
    python scripts/i18n_audit.py --scope runtime --report-only
    python scripts/i18n_audit.py --scope legacy --report-only
    python scripts/i18n_audit.py --scope all --json --report-only

退出码
------
* 严格模式（默认）：仅当 **runtime** 作用域存在 ERROR 或 WARNING 时返回 1。
  legacy 的 WARNING **不会**阻断当前产品发布。
* ``--errors-only``：仅当 runtime 存在 ERROR 时返回 1。
* ``--report-only``：始终返回 0。

仅使用 Python 3.9+ 标准库。
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from string import Formatter
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

# ============================================================
# 常量
# ============================================================

#: 唯一启动入口模块（``streamlit run app.py``）。
DEFAULT_ENTRY_MODULE = "app"

#: 内置回退语言，也是 key 集合的兜底基准语言。
DEFAULT_LANGUAGE = "en"

#: manifest 不可用时的回退审计语言（保持阶段 1 的行为）。
FALLBACK_AUDITED_LANGUAGES: Tuple[str, ...] = ("en", "zh-CN")

#: 兼容旧导入：默认审计语言。实际值在运行时由 locales/manifest.json 决定。
AUDITED_LANGUAGES: Tuple[str, ...] = FALLBACK_AUDITED_LANGUAGES

#: locale registry manifest 文件名。
MANIFEST_FILENAME = "manifest.json"

#: manifest 允许的 status 取值。
VALID_LOCALE_STATUSES: Tuple[str, ...] = ("stable", "experimental", "planned")

#: manifest 允许的 direction 取值。
VALID_TEXT_DIRECTIONS: Tuple[str, ...] = ("ltr", "rtl")

#: 作用域取值。
SCOPE_RUNTIME = "runtime"
SCOPE_LEGACY = "legacy"
SCOPE_TESTS_AND_TOOLS = "tests_and_tools"

SCOPE_CHOICES: Tuple[str, ...] = (SCOPE_RUNTIME, SCOPE_LEGACY, "all")

#: 阻断发布的严重度（仅对 runtime 生效）。
BLOCKING_SEVERITIES = ("error", "warning")

#: 目录名精确匹配即跳过（虚拟环境等，不属于任何作用域）。
SKIPPED_DIR_NAMES = frozenset(
    {
        "__pycache__",
        "node_modules",
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        "site-packages",
    }
)

#: 判定为"测试文件"的路径特征。
TEST_FILE_PREFIXES = ("test_",)

#: 判定为"开发工具"的目录前缀。
TOOL_DIR_PREFIXES = ("scripts/", "tools/")

#: 形如 "app.header_map" / "risk.level.high" 的 key 模式。
KEY_PATTERN = re.compile(r"^[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+$")

#: 视作"文案参数"的关键字（位置参数之外额外检查的 kwargs）。
TEXT_KEYWORDS = frozenset(
    {
        "label",
        "help",
        "placeholder",
        "caption",
        "title",
        "page_title",
        "body",
        "text",
        "message",
        "header",
        "subheader",
        "content",
        "description",
        "tooltip",
    }
)

#: 第一个位置参数是列表（每项都是文案）的方法。
LIST_TEXT_METHODS = frozenset({"tabs", "segmented_control", "pills"})

#: 除 ``st.*`` 之外、同样承载"用户可见文案"的方法名（任意接收者）。
#: ``add_run`` 是 python-docx 的文本出口（导出报告的元数据标签）。
EXTRA_TEXT_SINK_METHODS = frozenset({"add_run"})

#: 这些 st.* 方法不检查（参数不是用户文案）。
SKIPPED_ST_METHODS = frozenset(
    {
        "session_state",
        "rerun",
        "stop",
        "switch_page",
        "experimental_rerun",
        "cache_data",
        "cache_resource",
    }
)

#: 明显是配置值而非文案的字符串，避免误报。
CONFIG_VALUES = frozenset(
    {
        "utf-8",
        "utf8",
        "ascii",
        "rb",
        "wb",
        "rt",
        "wt",
        "wide",
        "centered",
        "auto",
        "always",
        "never",
        "hidden",
        "collapsed",
        "expanded",
        "horizontal",
        "vertical",
        "left",
        "right",
        "center",
        "top",
        "bottom",
        "small",
        "medium",
        "large",
        "repeat",
        "ignore",
        "first",
        "last",
        "json",
        "csv",
        "png",
        "jpg",
    }
)

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


# ============================================================
# 数据结构
# ============================================================


@dataclass
class FileScope:
    """一个源文件的运行时可达性分类。"""

    path: str
    module: str
    scope: str
    reachable: bool
    reason: str
    is_test: bool = False
    is_tool: bool = False


@dataclass
class Finding:
    """一条审计发现。"""

    severity: str  # "error" | "warning" | "info"
    category: str
    location: str
    detail: str
    scope: str = SCOPE_RUNTIME
    reachable: bool = True


@dataclass
class HardcodedCandidate:
    """一处疑似未国际化文案。"""

    file: str
    line: int
    api: str
    text: str
    is_fstring: bool = False
    scope: str = SCOPE_RUNTIME
    reachable: bool = True


@dataclass
class LiteralCall:
    """一次 t("key", **kwargs) 字面量调用。"""

    file: str
    line: int
    key: str
    kwargs: Tuple[str, ...] = ()
    scope: str = SCOPE_RUNTIME
    reachable: bool = True


@dataclass
class AuditReport:
    """审计结果汇总。"""

    root: str
    locales_dir: str
    scope: str
    entry_module: str
    file_scopes: List[FileScope] = field(default_factory=list)
    reachable_modules: List[str] = field(default_factory=list)
    scanned_files: List[FileScope] = field(default_factory=list)
    excluded_files: List[FileScope] = field(default_factory=list)
    languages: List[str] = field(default_factory=list)
    key_sets: Dict[str, Set[str]] = field(default_factory=dict)
    missing_in_language: Dict[str, List[str]] = field(default_factory=dict)
    extra_in_language: Dict[str, List[str]] = field(default_factory=dict)
    placeholder_mismatch: List[Tuple[str, str, Set[str], Set[str]]] = field(default_factory=list)
    malformed_placeholders: List[Tuple[str, str, str]] = field(default_factory=list)
    format_call_mismatch: List[Tuple[str, int, str, Set[str], Set[str]]] = field(default_factory=list)
    literal_calls: List[LiteralCall] = field(default_factory=list)
    dynamic_calls: List[Tuple[str, int, str]] = field(default_factory=list)
    mapping_keys: Set[str] = field(default_factory=set)
    missing_referenced_keys: List[Tuple[str, int, str, str]] = field(default_factory=list)
    unused_keys: List[str] = field(default_factory=list)
    hardcoded: List[HardcodedCandidate] = field(default_factory=list)
    manifest_path: str = ""
    manifest_default: str = DEFAULT_LANGUAGE
    manifest_entries: List[LocaleEntry] = field(default_factory=list)
    manifest_problems: List[str] = field(default_factory=list)
    missing_locale_files: List[str] = field(default_factory=list)

    # ---------- 派生统计 ----------

    @property
    def planned_languages(self) -> List[str]:
        """已注册但本阶段不开放的语言。"""
        return [e.code for e in self.manifest_entries if e.status == "planned"]

    @property
    def selectable_languages(self) -> List[str]:
        """manifest 中允许出现在 UI 的语言。"""
        return [e.code for e in self.manifest_entries if e.selectable]

    @property
    def referenced_keys(self) -> Set[str]:
        return {c.key for c in self.literal_calls} | set(self.mapping_keys)

    def files_in_scope(self, scope: str) -> List[FileScope]:
        return [fs for fs in self.file_scopes if fs.scope == scope]

    def candidates_in_scope(self, scope: str) -> List[HardcodedCandidate]:
        return [c for c in self.hardcoded if c.scope == scope]

    @property
    def findings(self) -> List[Finding]:
        out: List[Finding] = []

        # --- locale manifest 层（阶段 2）：语言清单本身必须自洽 ---
        for problem in self.manifest_problems:
            out.append(
                Finding("error", "manifest_invalid",
                        f"locales/{MANIFEST_FILENAME}", problem,
                        SCOPE_RUNTIME, True)
            )
        for code in self.missing_locale_files:
            out.append(
                Finding("error", "missing_locale_file",
                        f"locales/{code}.json",
                        f"UI 可用的 locale {code} 缺少对应 JSON",
                        SCOPE_RUNTIME, True)
            )

        # --- locale 层的共享发现：locale/*.json 是 runtime 资产 ---
        for lang, keys in self.missing_in_language.items():
            for key in keys:
                out.append(
                    Finding("error", "locale_missing_key", f"locales/{lang}.json",
                            f"缺少 key: {key}", SCOPE_RUNTIME, True)
                )
        for lang, keys in self.extra_in_language.items():
            for key in keys:
                out.append(
                    Finding("warning", "locale_extra_key", f"locales/{lang}.json",
                            f"多余 key: {key}", SCOPE_RUNTIME, True)
                )
        for key, lang, expected, actual in self.placeholder_mismatch:
            out.append(
                Finding("error", "placeholder_mismatch", f"locales/{lang}.json:{key}",
                        f"期望 {sorted(expected)}，实际 {sorted(actual)}", SCOPE_RUNTIME, True)
            )
        for key, lang, value in self.malformed_placeholders:
            out.append(
                Finding("error", "malformed_placeholder", f"locales/{lang}.json:{key}",
                        f"无法解析: {value!r}", SCOPE_RUNTIME, True)
            )

        # --- 代码层发现：带作用域 ---
        for file, line, key, scope in self.missing_referenced_keys:
            out.append(
                Finding("error", "missing_referenced_key", f"{file}:{line}",
                        f"代码引用但 locale 缺失: {key}", scope, scope == SCOPE_RUNTIME)
            )
        for file, line, key, missing, extra in self.format_call_mismatch:
            parts = []
            if missing:
                parts.append(f"缺少参数 {sorted(missing)}")
            if extra:
                parts.append(f"多余参数 {sorted(extra)}")
            out.append(
                Finding("error", "placeholder_call_mismatch", f"{file}:{line}",
                        f"t({key!r}) -> " + "，".join(parts), SCOPE_RUNTIME, True)
            )
        for file, line, scope in self.dynamic_calls:
            out.append(
                Finding("warning", "dynamic_translation_key", f"{file}:{line}",
                        "无法静态校验的动态 t() 调用", scope, scope == SCOPE_RUNTIME)
            )
        for candidate in self.hardcoded:
            out.append(
                Finding("warning", "hardcoded_string",
                        f"{candidate.file}:{candidate.line}",
                        f"{candidate.api} -> {candidate.text!r}",
                        candidate.scope, candidate.reachable)
            )
        for key in self.unused_keys:
            out.append(
                Finding("info", "unused_locale_key", "locales",
                        f"未被静态引用: {key}", SCOPE_RUNTIME, True)
            )

        return out

    def count(self, severity: str, scope: Optional[str] = None) -> int:
        findings = self.findings
        if scope is not None:
            findings = [f for f in findings if f.scope == scope]
        return sum(1 for f in findings if f.severity == severity)


# ============================================================
# 可达性分析（AST import 图）
# ============================================================


def _is_skipped_dir(name: str) -> bool:
    return name in SKIPPED_DIR_NAMES or (name.startswith(".") and name not in {".", ".."})


def iter_python_files(root: Path) -> List[Path]:
    """剪枝遍历仓库内所有 .py 文件（跳过虚拟环境与隐藏目录）。"""
    files: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(name for name in dirnames if not _is_skipped_dir(name))
        for filename in sorted(filenames):
            if filename.endswith(".py"):
                files.append(Path(dirpath) / filename)
    return files


def module_name_for(path: Path, root: Path) -> str:
    """把文件路径转换为点号模块名（``utils/a.py`` -> ``utils.a``）。"""
    rel = path.relative_to(root).with_suffix("")
    parts = list(rel.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def build_module_index(root: Path) -> Dict[str, Path]:
    """建立 ``模块名 -> 文件路径`` 索引。"""
    index: Dict[str, Path] = {}
    for path in iter_python_files(root):
        name = module_name_for(path, root)
        if name:
            index[name] = path
    return index


def extract_import_targets(tree: ast.AST) -> Set[str]:
    """
    提取文件中所有 import 目标（含函数体内的延迟 import）。

    相对导入在本仓库中不存在，直接忽略。
    """
    targets: Set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                targets.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                continue
            if not node.module:
                continue
            targets.add(node.module)
            for alias in node.names:
                targets.add(f"{node.module}.{alias.name}")

    return targets


def compute_reachable_modules(
    root: Path,
    entry_module: str = DEFAULT_ENTRY_MODULE,
) -> Tuple[Set[str], Dict[str, Path]]:
    """
    从入口模块出发做 import 图 BFS，返回 ``(可达模块集合, 模块索引)``。

    这决定了 runtime / legacy 的划分，而不是按文件名猜测。
    """
    index = build_module_index(root)
    reachable: Set[str] = set()

    if entry_module not in index:
        return reachable, index

    queue: List[str] = [entry_module]

    while queue:
        module = queue.pop()
        if module in reachable:
            continue

        reachable.add(module)
        path = index[module]

        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except Exception:
            continue

        for target in extract_import_targets(tree):
            if target in index and target not in reachable:
                queue.append(target)

    return reachable, index


def classify_file(
    path: Path,
    root: Path,
    reachable_modules: Set[str],
    entry_module: str = DEFAULT_ENTRY_MODULE,
) -> FileScope:
    """判定单个文件的作用域。"""
    relative = str(path.relative_to(root))
    name = path.name
    module = module_name_for(path, root)

    is_test = name.startswith(TEST_FILE_PREFIXES) or "/tests/" in f"/{relative}"
    is_tool = relative.startswith(TOOL_DIR_PREFIXES)

    if is_test:
        return FileScope(relative, module, SCOPE_TESTS_AND_TOOLS, False,
                         "测试文件", is_test=True, is_tool=is_tool)
    if is_tool:
        return FileScope(relative, module, SCOPE_TESTS_AND_TOOLS, False,
                         "开发工具 / 审计脚本", is_test=is_test, is_tool=True)
    if module in reachable_modules:
        return FileScope(relative, module, SCOPE_RUNTIME, True,
                         f"从 {entry_module}.py 可达", is_test=is_test, is_tool=is_tool)

    return FileScope(relative, module, SCOPE_LEGACY, False,
                     f"未被 {entry_module}.py 导入（当前不会执行）",
                     is_test=is_test, is_tool=is_tool)


def classify_all_files(
    root: Path,
    reachable_modules: Set[str],
    entry_module: str = DEFAULT_ENTRY_MODULE,
) -> List[FileScope]:
    return [
        classify_file(path, root, reachable_modules, entry_module)
        for path in iter_python_files(root)
    ]


# ============================================================
# locale 读取
# ============================================================


def flatten_locale(data: dict, prefix: str = "") -> Dict[str, str]:
    """把嵌套 locale 字典拍平成点号 key（与 i18n.py 的 _flatten 行为一致）。"""
    flat: "OrderedDict[str, str]" = OrderedDict()

    for key, value in data.items():
        dotted = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            flat.update(flatten_locale(value, dotted))
        else:
            flat[dotted] = str(value)

    return dict(flat)


def load_locale_file(path: Path) -> Dict[str, str]:
    """读取并拍平一个 locale JSON 文件；失败时返回空字典（不抛异常）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    return flatten_locale(data)


def load_locales(locales_dir: Path, languages: Sequence[str]) -> Dict[str, Dict[str, str]]:
    """读取所有目标语言的 locale。"""
    return {lang: load_locale_file(locales_dir / f"{lang}.json") for lang in languages}


# ============================================================
# locale manifest（阶段 2）
# ============================================================


@dataclass
class LocaleEntry:
    """``locales/manifest.json`` 中的一条 locale 记录。"""

    code: str
    native_name: str = ""
    english_name: str = ""
    fallback: Optional[str] = None
    direction: str = "ltr"
    ai_language_name: str = ""
    status: str = "unknown"
    ui_enabled: bool = False
    is_default: bool = False

    @property
    def selectable(self) -> bool:
        """是否允许出现在 UI 语言选择器中。"""
        return self.ui_enabled and self.status != "planned"

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "native_name": self.native_name,
            "english_name": self.english_name,
            "fallback": self.fallback,
            "direction": self.direction,
            "ai_language_name": self.ai_language_name,
            "status": self.status,
            "ui_enabled": self.ui_enabled,
            "is_default": self.is_default,
        }


def _detect_manifest_cycle(
    fallback_map: Dict[str, Optional[str]],
) -> Optional[Tuple[str, ...]]:
    """返回一个 fallback 环，或 ``None``。"""
    for start in fallback_map:
        seen: List[str] = []
        current: Optional[str] = start
        while current is not None:
            if current in seen:
                return tuple(seen[seen.index(current):] + [current])
            seen.append(current)
            current = fallback_map.get(current)
    return None


def load_locale_manifest(locales_dir: Path) -> Tuple[List[LocaleEntry], List[str], str]:
    """
    读取并校验 ``locales/manifest.json``。

    返回 ``(entries, problems, default_code)``，永不抛异常。

    这是审计侧**独立**的校验实现（只读 JSON，不导入应用代码），与
    ``utils/locale_registry.py`` 互为交叉验证：registry 负责运行时的安全
    回落，审计负责把问题升级为 ERROR 以阻断发布。两者刻意保持独立，
    避免同一处逻辑写错而两边都漏过。
    """
    path = locales_dir / MANIFEST_FILENAME
    problems: List[str] = []

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], [f"manifest 不存在: {path}"], DEFAULT_LANGUAGE
    except Exception as exc:
        return [], [f"manifest 无法解析: {type(exc).__name__}"], DEFAULT_LANGUAGE

    if not isinstance(raw, list):
        return [], ["manifest 顶层必须是数组"], DEFAULT_LANGUAGE

    if not raw:
        return [], ["manifest 不能为空"], DEFAULT_LANGUAGE

    entries: List[LocaleEntry] = []
    codes: List[str] = []

    for index, item in enumerate(raw):

        if not isinstance(item, dict):
            problems.append(f"第 {index} 项不是对象")
            continue

        code = item.get("code")
        if not isinstance(code, str) or not code.strip():
            problems.append(f"第 {index} 项 code 缺失或为空")
            continue

        code = code.strip()
        codes.append(code)

        fallback = item.get("fallback")
        if isinstance(fallback, str):
            fallback = fallback.strip() or None
        elif fallback is not None:
            problems.append(f"{code}: fallback 必须是 null 或字符串")
            fallback = None

        entry = LocaleEntry(
            code=code,
            native_name=str(item.get("native_name") or "").strip(),
            english_name=str(item.get("english_name") or "").strip(),
            fallback=fallback,
            direction=str(item.get("direction") or ""),
            ai_language_name=str(item.get("ai_language_name") or "").strip(),
            status=str(item.get("status") or ""),
            ui_enabled=item.get("ui_enabled") is True,
            is_default=item.get("is_default") is True,
        )

        for field_name in ("native_name", "english_name", "ai_language_name"):
            if not getattr(entry, field_name):
                problems.append(f"{code}: {field_name} 不得为空")

        if entry.direction not in VALID_TEXT_DIRECTIONS:
            problems.append(f"{code}: direction 非法 {entry.direction!r}")
        if entry.status not in VALID_LOCALE_STATUSES:
            problems.append(f"{code}: status 非法 {entry.status!r}")
        if not isinstance(item.get("ui_enabled"), bool):
            problems.append(f"{code}: ui_enabled 必须是布尔值")
        if entry.status == "planned" and entry.ui_enabled:
            problems.append(f"{code}: planned locale 不得 ui_enabled=true")

        entries.append(entry)

    # --- code 唯一性 ---
    counts: Dict[str, int] = {}
    for code in codes:
        counts[code] = counts.get(code, 0) + 1
    for code, count in counts.items():
        if count > 1:
            problems.append(f"code 重复: {code}（{count} 次）")

    known = {code for code, count in counts.items() if count == 1}

    # --- fallback 引用与循环 ---
    fallback_map: Dict[str, Optional[str]] = {}
    for entry in entries:
        if entry.code not in known:
            continue
        if entry.fallback is None:
            fallback_map[entry.code] = None
        elif entry.fallback == entry.code:
            problems.append(f"{entry.code}: fallback 不得指向自身")
            fallback_map[entry.code] = None
        elif entry.fallback not in known:
            problems.append(
                f"{entry.code}: fallback 引用了未注册的 locale {entry.fallback!r}"
            )
            fallback_map[entry.code] = None
        else:
            fallback_map[entry.code] = entry.fallback

    cycle = _detect_manifest_cycle(fallback_map)
    if cycle is not None:
        problems.append("fallback 形成循环: " + " -> ".join(cycle))

    # --- 默认 locale ---
    defaults = [entry.code for entry in entries if entry.is_default]
    default_code = DEFAULT_LANGUAGE

    if len(defaults) > 1:
        problems.append(f"只能有一个 is_default，实际 {defaults}")
    elif not defaults:
        problems.append("必须有一个 is_default=true 的 locale")
    else:
        default_code = defaults[0]
        default_entry = next((e for e in entries if e.code == default_code), None)
        if default_entry is not None and not default_entry.selectable:
            problems.append(
                f"默认 locale {default_code} 必须可用（ui_enabled 且非 planned）"
            )

    if not any(entry.selectable for entry in entries):
        problems.append("manifest 中没有任何可用的 UI locale")

    return entries, problems, default_code


# ============================================================
# 占位符
# ============================================================


def extract_placeholders(text: str) -> Optional[Set[str]]:
    """
    返回 ``{placeholder}`` 名称集合；无法解析时返回 ``None``。

    使用 ``string.Formatter``，与 ``str.format`` 的真实语义保持一致。
    """
    if not isinstance(text, str):
        return None

    names: Set[str] = set()

    try:
        for _, field_name, _, _ in Formatter().parse(text):
            if field_name is None:
                continue
            root = re.split(r"[.\[]", field_name, maxsplit=1)[0]
            names.add(root)
    except Exception:
        return None

    return names


def is_positional_placeholder(name: str) -> bool:
    """``{0}`` / ``{}`` 这类位置占位符无法用 kwargs 填充。"""
    return name == "" or name.isdigit()


# ============================================================
# AST 采集
# ============================================================


def _call_name(node: ast.Call) -> Optional[str]:
    """返回 ``st.markdown`` 这样的调用名；无法解析时返回 None。"""
    func = node.func

    if isinstance(func, ast.Attribute):
        if isinstance(func.value, ast.Name):
            return f"{func.value.id}.{func.attr}"
        return func.attr

    if isinstance(func, ast.Name):
        return func.id

    return None


def _is_translate_call(node: ast.Call) -> bool:
    """是否形如 ``t(...)`` 或 ``i18n.t(...)``。"""
    return _call_name(node) in {"t", "i18n.t"}


def _literal_texts(node: ast.AST) -> List[Tuple[str, bool]]:
    """
    提取节点中的字面量文本片段。

    返回值元素为 ``(text, is_fstring)``。f-string 只取其中的静态片段。
    """
    out: List[Tuple[str, bool]] = []

    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        out.append((node.value, False))
    elif isinstance(node, ast.JoinedStr):
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                out.append((value.value, True))
    elif isinstance(node, (ast.List, ast.Tuple)):
        for element in node.elts:
            out.extend(_literal_texts(element))

    return out


def _looks_like_key_container(name: str) -> bool:
    """变量名是否像一张 "值 -> locale key" 映射表。"""
    upper = name.upper()
    return upper.endswith("KEYS") or upper.endswith("_KEY")


def looks_like_ui_text(text: str, known_keys: Set[str]) -> bool:
    """
    判断字面量是否像"用户可见文案"。

    过滤纯符号 / 纯数字、配置值、URL、locale key 自身，降低硬编码候选的误报。
    """
    stripped = text.strip()

    if len(stripped) < 2:
        return False
    if stripped.lower() in CONFIG_VALUES:
        return False
    if stripped in known_keys:
        return False
    if stripped.startswith(("http://", "https://", "www.")):
        return False
    if not any(char.isalpha() for char in stripped):
        return False

    has_space = any(char.isspace() for char in stripped)
    has_non_ascii = any(ord(char) > 127 for char in stripped)
    starts_upper = stripped[0].isupper()

    return has_space or has_non_ascii or starts_upper


@dataclass
class FileFindings:
    literal_calls: List[LiteralCall] = field(default_factory=list)
    dynamic_lines: List[int] = field(default_factory=list)
    mapping_keys: Set[str] = field(default_factory=set)
    hardcoded: List[HardcodedCandidate] = field(default_factory=list)


def analyze_source(
    source: str,
    filename: str,
    known_keys: Set[str],
    scope: str = SCOPE_RUNTIME,
) -> FileFindings:
    """
    分析单个源码文件的文本内容。

    独立于文件系统，便于测试。``filename`` / ``scope`` 仅用于报告展示。
    """
    findings = FileFindings()
    reachable = scope == SCOPE_RUNTIME

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return findings

    for node in ast.walk(tree):
        # --- 映射表 key：X_KEYS = {"value": "some.key"} ---
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            for target in node.targets:
                name = None
                if isinstance(target, ast.Name):
                    name = target.id
                elif isinstance(target, ast.Attribute):
                    name = target.attr
                if name and _looks_like_key_container(name):
                    for value in node.value.values:
                        if isinstance(value, ast.Constant) and isinstance(value.value, str):
                            if KEY_PATTERN.match(value.value):
                                findings.mapping_keys.add(value.value)

        if not isinstance(node, ast.Call):
            continue

        # --- t() 引用 ---
        if _is_translate_call(node):
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                kwargs = tuple(kw.arg for kw in node.keywords if kw.arg)
                findings.literal_calls.append(
                    LiteralCall(filename, node.lineno, node.args[0].value, kwargs, scope, reachable)
                )
            else:
                findings.dynamic_lines.append(node.lineno)
            continue

        # --- 硬编码候选（多通道文案出口） ---
        api = _call_name(node)
        if not api:
            continue

        receiver, _, method = api.rpartition(".")
        is_streamlit = receiver == "st"
        is_extra_sink = method in EXTRA_TEXT_SINK_METHODS

        if is_streamlit and method in SKIPPED_ST_METHODS:
            continue
        if not is_streamlit and not is_extra_sink:
            continue

        candidates: List[Tuple[str, bool]] = []

        if is_streamlit and method == "set_page_config":
            for keyword in node.keywords:
                if keyword.arg == "page_title":
                    candidates.extend(_literal_texts(keyword.value))
        else:
            if node.args:
                candidates.extend(_literal_texts(node.args[0]))
            if is_streamlit and method in LIST_TEXT_METHODS:
                for extra in node.args[1:]:
                    candidates.extend(_literal_texts(extra))
            for keyword in node.keywords:
                if keyword.arg in TEXT_KEYWORDS:
                    candidates.extend(_literal_texts(keyword.value))

        for text, is_fstring in candidates:
            if not looks_like_ui_text(text, known_keys):
                continue
            findings.hardcoded.append(
                HardcodedCandidate(filename, node.lineno, api, text.strip(),
                                   is_fstring, scope, reachable)
            )

    return findings


# ============================================================
# 主审计流程
# ============================================================


def select_files(
    all_files: Sequence[FileScope],
    scope: str,
    include_tests: bool = False,
    include_tools: bool = False,
) -> Tuple[List[FileScope], List[FileScope]]:
    """按作用域挑选待扫描文件，返回 ``(selected, excluded)``。"""

    def is_wanted(fs: FileScope) -> bool:
        if scope == SCOPE_RUNTIME:
            return fs.scope == SCOPE_RUNTIME
        if scope == SCOPE_LEGACY:
            return fs.scope == SCOPE_LEGACY
        return fs.scope in (SCOPE_RUNTIME, SCOPE_LEGACY)

    def is_extra(fs: FileScope) -> bool:
        return (fs.is_test and include_tests) or (fs.is_tool and include_tools)

    selected = [fs for fs in all_files if is_wanted(fs) or is_extra(fs)]
    excluded = [fs for fs in all_files if fs not in selected]

    return selected, excluded


def run_audit(
    root: Path,
    locales_dir: Optional[Path] = None,
    languages: Optional[Sequence[str]] = None,
    scope: str = SCOPE_RUNTIME,
    entry_module: str = DEFAULT_ENTRY_MODULE,
    include_tests: bool = False,
    include_tools: bool = False,
) -> AuditReport:
    """
    执行一次完整审计，返回 :class:`AuditReport`。

    纯只读：不写文件、不联网、不导入应用代码。

    阶段 2 起，审计语言不再硬编码：默认取 ``locales/manifest.json`` 中
    ``ui_enabled`` 且非 ``planned`` 的语言。manifest 不可用时回退到
    :data:`FALLBACK_AUDITED_LANGUAGES`，并把问题记为 ERROR。
    """
    root = Path(root).resolve()
    locales_dir = Path(locales_dir).resolve() if locales_dir else root / "locales"

    reachable_modules, _index = compute_reachable_modules(root, entry_module)
    all_files = classify_all_files(root, reachable_modules, entry_module)
    selected, excluded = select_files(all_files, scope, include_tests, include_tools)

    # --- 0. locale manifest（语言清单的唯一来源） ---
    manifest_entries, manifest_problems, manifest_default = load_locale_manifest(locales_dir)
    selectable_languages = [entry.code for entry in manifest_entries if entry.selectable]

    if languages is None:
        languages = (
            tuple(selectable_languages)
            if selectable_languages
            else FALLBACK_AUDITED_LANGUAGES
        )

    # enabled locale 必须有对应 JSON；planned locale 不需要。
    missing_locale_files = [
        entry.code
        for entry in manifest_entries
        if entry.selectable and not (locales_dir / f"{entry.code}.json").is_file()
    ]

    report = AuditReport(
        root=str(root),
        locales_dir=str(locales_dir),
        scope=scope,
        entry_module=entry_module,
        file_scopes=all_files,
        reachable_modules=sorted(reachable_modules),
        scanned_files=selected,
        excluded_files=excluded,
        languages=list(languages),
        manifest_path=str(locales_dir / MANIFEST_FILENAME),
        manifest_default=manifest_default,
        manifest_entries=manifest_entries,
        manifest_problems=manifest_problems,
        missing_locale_files=missing_locale_files,
    )

    # --- 1. locale key 集合 ---
    locales = load_locales(locales_dir, languages)
    report.key_sets = {lang: set(keys) for lang, keys in locales.items()}

    # 基准语言优先取 manifest 的默认 locale，保证与运行时一致。
    reference_lang = (
        manifest_default
        if manifest_default in languages
        else (languages[0] if languages else DEFAULT_LANGUAGE)
    )
    reference_keys = report.key_sets.get(reference_lang, set())

    for lang in languages:
        keys = report.key_sets.get(lang, set())
        missing = sorted(reference_keys - keys)
        extra = sorted(keys - reference_keys)
        if missing:
            report.missing_in_language[lang] = missing
        if extra:
            report.extra_in_language[lang] = extra

    # --- 2. 占位符一致性（跨语言） ---
    reference_values = locales.get(reference_lang, {})

    for key, value in reference_values.items():
        expected = extract_placeholders(value)
        if expected is None:
            report.malformed_placeholders.append((key, reference_lang, value))
            continue
        for lang in languages:
            if lang == reference_lang:
                continue
            other = locales.get(lang, {}).get(key)
            if other is None:
                continue
            actual = extract_placeholders(other)
            if actual is None:
                report.malformed_placeholders.append((key, lang, other))
                continue
            if expected != actual:
                report.placeholder_mismatch.append((key, lang, expected, actual))

    # --- 3. 扫描源码（按作用域） ---
    for file_scope in selected:
        path = root / file_scope.path
        try:
            source = path.read_text(encoding="utf-8")
        except Exception:
            continue

        file_findings = analyze_source(source, file_scope.path, reference_keys, file_scope.scope)

        report.literal_calls.extend(file_findings.literal_calls)
        report.mapping_keys |= file_findings.mapping_keys
        report.hardcoded.extend(file_findings.hardcoded)
        for line in file_findings.dynamic_lines:
            report.dynamic_calls.append((file_scope.path, line, file_scope.scope))

    # --- 4. 代码引用 vs locale 缺失 ---
    for call in report.literal_calls:
        if call.key not in reference_keys:
            report.missing_referenced_keys.append((call.file, call.line, call.key, call.scope))

    # --- 5. t() 调用参数 vs 占位符 ---
    for call in report.literal_calls:
        if not call.kwargs:
            continue
        value = reference_values.get(call.key)
        if value is None:
            continue
        placeholders = extract_placeholders(value)
        if not placeholders:
            report.format_call_mismatch.append((call.file, call.line, call.key, set(), set(call.kwargs)))
            continue
        if any(is_positional_placeholder(name) for name in placeholders):
            continue
        provided = set(call.kwargs)
        missing = placeholders - provided
        extra = provided - placeholders
        if missing or extra:
            report.format_call_mismatch.append((call.file, call.line, call.key, missing, extra))

    # --- 6. 未被静态引用的 locale key（仅 runtime 作用域报告） ---
    if scope in (SCOPE_RUNTIME, "all"):
        report.unused_keys = sorted(reference_keys - report.referenced_keys)

    return report


# ============================================================
# 报告渲染
# ============================================================


def _format_key_list(keys: Iterable[str], limit: int = 20) -> str:
    keys = list(keys)
    if not keys:
        return "(none)"
    shown = keys[:limit]
    suffix = "" if len(keys) <= limit else f"  ... (+{len(keys) - limit})"
    return ", ".join(shown) + suffix


def _count_by_file(candidates: Sequence[HardcodedCandidate]) -> Dict[str, int]:
    counts: "OrderedDict[str, int]" = OrderedDict()
    for candidate in candidates:
        counts[candidate.file] = counts.get(candidate.file, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _render_hardcoded_section(
    lines: List[str],
    candidates: Sequence[HardcodedCandidate],
    scope: str,
    max_samples: int,
) -> None:
    add = lines.append
    scoped = [c for c in candidates if c.scope == scope]

    add(f"  硬编码候选: {len(scoped)}")

    if not scoped:
        add("    [OK] 无")
        return

    add("  按文件统计:")
    for name, count in _count_by_file(scoped).items():
        add(f"    {count:>4}  {name}")

    add(f"  样例（最多 {max_samples} 条）:")
    for candidate in scoped[:max_samples]:
        kind = "f-string" if candidate.is_fstring else "str"
        add(f"    {candidate.file}:{candidate.line} [{kind}] {candidate.api} -> {candidate.text!r}")


def render_text(report: AuditReport, max_samples: int = 5) -> str:
    """把审计结果渲染成人类可读文本报告（runtime 与 legacy 分开汇总）。"""
    lines: List[str] = []
    add = lines.append

    runtime_files = report.files_in_scope(SCOPE_RUNTIME)
    legacy_files = report.files_in_scope(SCOPE_LEGACY)
    tool_files = report.files_in_scope(SCOPE_TESTS_AND_TOOLS)

    add("=" * 72)
    add("RiskPilot i18n 审计报告（静态 / 离线 / 可达性优先）")
    add("=" * 72)
    add(f"仓库根目录 : {report.root}")
    add(f"locale 目录: {report.locales_dir}")
    add(f"入口模块   : {report.entry_module}.py")
    add(f"作用域     : --scope {report.scope}")
    add(f"审计语言   : {', '.join(report.languages)}")
    add("")

    # --- 可达性分类 ---
    add("-" * 72)
    add("0. 运行时可达性分类（由 AST import 图判定，非文件名猜测）")
    add("-" * 72)
    add(f"  runtime_reachable  : {len(runtime_files)} 个文件")
    for fs in runtime_files:
        add(f"      - {fs.path}")
    add(f"  legacy_unreachable : {len(legacy_files)} 个文件")
    for fs in legacy_files:
        add(f"      - {fs.path}  ({fs.reason})")
    add(f"  tests_and_tools    : {len(tool_files)} 个文件")
    for fs in tool_files:
        add(f"      - {fs.path}")
    add("")
    add(f"  本次扫描文件: {len(report.scanned_files)} 个")
    for fs in report.scanned_files:
        add(f"      - {fs.path}  [{fs.scope}]")
    add("")

    # --- locale manifest 层 ---
    add("-" * 72)
    add("0b. locale manifest（阶段 2：语言清单的唯一来源）")
    add("-" * 72)
    add(f"  manifest    : {report.manifest_path}")
    add(f"  默认 locale : {report.manifest_default}")
    add(f"  注册语言    : {len(report.manifest_entries)} 个")

    if report.manifest_entries:
        add("")
        add("  code      status      ui_enabled  direction  fallback  native_name")
        for entry in report.manifest_entries:
            add(
                f"  {entry.code:<9} {entry.status:<11} "
                f"{str(entry.ui_enabled):<11} {entry.direction:<10} "
                f"{str(entry.fallback):<9} {entry.native_name}"
            )

    add("")
    add(f"  UI 可选       : {', '.join(report.selectable_languages) or '(无)'}")
    add(f"  仅注册(planned): {', '.join(report.planned_languages) or '(无)'}")
    add(f"  本次审计语言  : {', '.join(report.languages)}")

    if report.manifest_problems:
        for problem in report.manifest_problems:
            add(f"  [ERROR] {problem}")
    else:
        add("  [OK] manifest 自洽（code 唯一、fallback 合法无环、默认可用）")

    if report.missing_locale_files:
        for code in report.missing_locale_files:
            add(f"  [ERROR] locales/{code}.json 缺失（UI 可选语言必须有 JSON）")
    else:
        add("  [OK] 所有 UI 可选语言都存在对应 JSON")

    if report.planned_languages:
        add(
            f"  [INFO] planned locale 不要求 JSON（阶段 2 尚未翻译）: "
            f"{', '.join(report.planned_languages)}"
        )
    add("")

    # --- 全局 locale 层 ---
    add("-" * 72)
    add("1. locale key 集合（共享层，作用于 runtime）")
    add("-" * 72)
    for lang in report.languages:
        add(f"  {lang}: {len(report.key_sets.get(lang, set()))} 个 key")
    if report.missing_in_language:
        for lang, keys in report.missing_in_language.items():
            add(f"  [ERROR] {lang} 缺失 {len(keys)} 个: {_format_key_list(keys)}")
    else:
        add("  [OK] 各语言 key 集合一致")
    if report.extra_in_language:
        for lang, keys in report.extra_in_language.items():
            add(f"  [WARN] {lang} 多出 {len(keys)} 个: {_format_key_list(keys)}")
    add("")

    add("-" * 72)
    add("2. 占位符一致性")
    add("-" * 72)
    if report.placeholder_mismatch:
        for key, lang, expected, actual in report.placeholder_mismatch:
            add(f"  [ERROR] {key} / {lang}: 期望 {sorted(expected)} 实际 {sorted(actual)}")
    else:
        add("  [OK] 跨语言占位符一致")
    for key, lang, value in report.malformed_placeholders:
        add(f"  [ERROR] {key} / {lang}: 无法解析 {value!r}")
    for file, line, key, missing, extra in report.format_call_mismatch:
        parts = []
        if missing:
            parts.append(f"缺少 {sorted(missing)}")
        if extra:
            parts.append(f"多余 {sorted(extra)}")
        add(f"  [ERROR] {file}:{line} t({key!r}) -> " + "，".join(parts))
    add("")

    # --- 逐作用域 ---
    for scope, title in (
        (SCOPE_RUNTIME, "RUNTIME（当前产品真实运行的代码）"),
        (SCOPE_LEGACY, "LEGACY（当前不会执行的旧代码 —— legacy debt）"),
    ):
        if report.scope != "all" and report.scope != scope:
            continue

        scoped_files = [fs for fs in report.scanned_files if fs.scope == scope]

        add("=" * 72)
        add(f"{title}")
        add("=" * 72)
        add(f"  扫描文件: {len(scoped_files)}")
        add("")

        scoped_calls = [c for c in report.literal_calls if c.scope == scope]
        scoped_dynamic = [d for d in report.dynamic_calls if d[2] == scope]

        add(f"  t() 字面量调用   : {len(scoped_calls)} 次 / {len({c.key for c in scoped_calls})} 个不同 key")
        add(f"  动态 t() 调用     : {len(scoped_dynamic)} 次")
        missing_ref = [m for m in report.missing_referenced_keys if m[3] == scope]
        if missing_ref:
            for file, line, key, _s in missing_ref:
                add(f"  [ERROR] {file}:{line} 引用但 locale 缺失: {key}")
        else:
            add("  [OK] 引用的 key 均存在于 locale")
        add("")
        _render_hardcoded_section(lines, report.hardcoded, scope, max_samples)
        add("")
        add(f"  {scope} 汇总: ERROR={report.count('error', scope)} "
            f"WARNING={report.count('warning', scope)} INFO={report.count('info', scope)}")
        add("")

    # --- 总汇总 ---
    add("=" * 72)
    add("总汇总（按作用域）")
    add("=" * 72)
    add(f"  {'scope':<16}{'ERROR':>8}{'WARNING':>10}{'INFO':>8}")
    for scope in (SCOPE_RUNTIME, SCOPE_LEGACY, SCOPE_TESTS_AND_TOOLS):
        add(f"  {scope:<16}{report.count('error', scope):>8}"
            f"{report.count('warning', scope):>10}{report.count('info', scope):>8}")
    add(f"  {'TOTAL':<16}{report.count('error'):>8}"
        f"{report.count('warning'):>10}{report.count('info'):>8}")
    add("=" * 72)

    if report.unused_keys:
        add(f"  未被静态引用的 locale key（信息）: {len(report.unused_keys)} 个")
        add(f"    {_format_key_list(report.unused_keys)}")
        add("")

    return "\n".join(lines)


def report_to_dict(report: AuditReport) -> dict:
    """把审计结果转换为可 JSON 序列化的字典（每条 finding 含 scope / reachable）。"""
    findings = report.findings

    return {
        "root": report.root,
        "locales_dir": report.locales_dir,
        "entry_module": report.entry_module,
        "scope": report.scope,
        "languages": report.languages,
        "locale_manifest": {
            "path": report.manifest_path,
            "default": report.manifest_default,
            "problems": report.manifest_problems,
            "selectable_languages": report.selectable_languages,
            "planned_languages": report.planned_languages,
            "missing_locale_files": report.missing_locale_files,
            "entries": [entry.to_dict() for entry in report.manifest_entries],
        },
        "reachability": {
            "entry": f"{report.entry_module}.py",
            "reachable_modules": report.reachable_modules,
            "runtime_files": [fs.path for fs in report.files_in_scope(SCOPE_RUNTIME)],
            "legacy_files": [fs.path for fs in report.files_in_scope(SCOPE_LEGACY)],
            "tests_and_tools_files": [fs.path for fs in report.files_in_scope(SCOPE_TESTS_AND_TOOLS)],
            "file_classification": [
                {
                    "path": fs.path,
                    "module": fs.module,
                    "scope": fs.scope,
                    "reachable": fs.reachable,
                    "reason": fs.reason,
                }
                for fs in report.file_scopes
            ],
        },
        "scanned_files": [{"path": fs.path, "scope": fs.scope, "reachable": fs.reachable}
                          for fs in report.scanned_files],
        "locale_key_counts": {lang: len(keys) for lang, keys in report.key_sets.items()},
        "missing_in_language": report.missing_in_language,
        "extra_in_language": report.extra_in_language,
        "placeholder_mismatch": [
            {"key": key, "language": lang, "expected": sorted(exp), "actual": sorted(act)}
            for key, lang, exp, act in report.placeholder_mismatch
        ],
        "malformed_placeholders": [
            {"key": key, "language": lang, "value": value}
            for key, lang, value in report.malformed_placeholders
        ],
        "format_call_mismatch": [
            {"file": f, "line": l, "key": k, "missing": sorted(m), "extra": sorted(e)}
            for f, l, k, m, e in report.format_call_mismatch
        ],
        "literal_call_count": len(report.literal_calls),
        "literal_key_count": len({c.key for c in report.literal_calls}),
        "dynamic_call_count": len(report.dynamic_calls),
        "dynamic_calls": [{"file": f, "line": l, "scope": s} for f, l, s in report.dynamic_calls],
        "mapping_key_count": len(report.mapping_keys),
        "missing_referenced_keys": [
            {"file": f, "line": l, "key": k, "scope": s}
            for f, l, k, s in report.missing_referenced_keys
        ],
        "unused_key_count": len(report.unused_keys),
        "unused_keys": report.unused_keys,
        "hardcoded_count": len(report.hardcoded),
        "hardcoded_by_scope": {
            scope: len(report.candidates_in_scope(scope))
            for scope in (SCOPE_RUNTIME, SCOPE_LEGACY, SCOPE_TESTS_AND_TOOLS)
        },
        "hardcoded_by_file": {
            scope: _count_by_file(report.candidates_in_scope(scope))
            for scope in (SCOPE_RUNTIME, SCOPE_LEGACY, SCOPE_TESTS_AND_TOOLS)
        },
        "hardcoded": [
            {
                "file": c.file,
                "line": c.line,
                "api": c.api,
                "text": c.text,
                "is_fstring": c.is_fstring,
                "scope": c.scope,
                "reachable": c.reachable,
            }
            for c in report.hardcoded
        ],
        "severity_counts": {
            "error": report.count("error"),
            "warning": report.count("warning"),
            "info": report.count("info"),
        },
        "severity_counts_by_scope": {
            scope: {
                "error": report.count("error", scope),
                "warning": report.count("warning", scope),
                "info": report.count("info", scope),
            }
            for scope in (SCOPE_RUNTIME, SCOPE_LEGACY, SCOPE_TESTS_AND_TOOLS)
        },
        "blocking_scope": SCOPE_RUNTIME,
        "findings": [
            {
                "severity": f.severity,
                "category": f.category,
                "location": f.location,
                "detail": f.detail,
                "scope": f.scope,
                "reachable": f.reachable,
            }
            for f in findings
        ],
    }


# ============================================================
# CLI
# ============================================================


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="i18n_audit",
        description="RiskPilot 全站国际化静态审计（离线 / 只读 / 可达性优先）",
    )
    parser.add_argument("--root", default=None, help="仓库根目录（默认：本脚本上级目录）")
    parser.add_argument("--locales-dir", default=None, help="locale 目录（默认：<root>/locales）")
    parser.add_argument(
        "--scope",
        choices=SCOPE_CHOICES,
        default=SCOPE_RUNTIME,
        help="审计作用域：runtime（默认，可达生产代码）/ legacy（不可达旧代码）/ all（两者，分组报告）",
    )
    parser.add_argument(
        "--entry",
        default=DEFAULT_ENTRY_MODULE,
        help=f"入口模块名（默认：{DEFAULT_ENTRY_MODULE}，对应 `streamlit run app.py`）",
    )
    parser.add_argument(
        "--languages",
        default=None,
        help=(
            "参与比较的语言，逗号分隔。"
            "默认取 locales/manifest.json 中 ui_enabled 且非 planned 的语言；"
            "基准语言为 manifest 的默认 locale"
        ),
    )
    parser.add_argument("--report-only", action="store_true",
                        help="只输出报告，始终返回退出码 0")
    parser.add_argument("--errors-only", action="store_true",
                        help="仅在 runtime 存在 ERROR 时返回非零（忽略 WARNING 与 legacy）")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出报告")
    parser.add_argument("--max-samples", type=int, default=5,
                        help="文本报告中硬编码样例的最大条数（默认 5）")
    parser.add_argument("--include-tests", action="store_true", help="同时审计 test_*.py")
    parser.add_argument("--include-tools", action="store_true", help="同时审计 scripts/ 等开发工具")
    return parser


def default_root() -> Path:
    """脚本位于 <root>/scripts/ 下，根目录即上一级。"""
    return Path(__file__).resolve().parent.parent


def compute_exit_code(report: AuditReport, report_only: bool, errors_only: bool) -> int:
    """
    根据模式计算退出码。

    只有 **runtime** 作用域的发现会阻断发布；legacy 的 WARNING 不影响当前产品。
    """
    if report_only:
        return 0

    runtime = SCOPE_RUNTIME

    if errors_only:
        return 1 if report.count("error", runtime) > 0 else 0

    for severity in BLOCKING_SEVERITIES:
        if report.count(severity, runtime) > 0:
            return 1
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    root = Path(args.root).resolve() if args.root else default_root()
    locales_dir = Path(args.locales_dir).resolve() if args.locales_dir else None
    # None -> run_audit 从 locales/manifest.json 推导审计语言。
    languages = (
        tuple(lang.strip() for lang in args.languages.split(",") if lang.strip())
        if args.languages
        else None
    )

    report = run_audit(
        root,
        locales_dir=locales_dir,
        languages=languages,
        scope=args.scope,
        entry_module=args.entry,
        include_tests=args.include_tests,
        include_tools=args.include_tools,
    )

    if args.json:
        print(json.dumps(report_to_dict(report), ensure_ascii=False, indent=2))
    else:
        print(render_text(report, max_samples=args.max_samples))

    exit_code = compute_exit_code(report, args.report_only, args.errors_only)

    if not args.json:
        runtime_error = report.count("error", SCOPE_RUNTIME)
        runtime_warning = report.count("warning", SCOPE_RUNTIME)
        legacy_warning = report.count("warning", SCOPE_LEGACY)

        if args.report_only:
            print(f"\n[report-only] 始终返回退出码 0"
                  f"（runtime WARNING={runtime_warning}，legacy WARNING={legacy_warning}）")
        elif exit_code != 0:
            print(f"\n[strict] runtime 存在 {runtime_error} 个 ERROR / "
                  f"{runtime_warning} 个 WARNING，返回退出码 {exit_code}")
        else:
            print(f"\n[strict] runtime 未发现 ERROR / WARNING"
                  f"（legacy WARNING={legacy_warning} 不阻断发布）")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
