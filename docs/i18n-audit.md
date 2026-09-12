# RiskPilot 全站国际化审计报告（全球化阶段 1 · 修订版 + 阶段 2 / 阶段 3 / 阶段 4A / 阶段 4B 更新）

- 审计日期：2026-09-10
- 审计对象：`/Users/cenglingxin/Desktop/RiskPilot_FINAL`
- 审计方式：静态、离线、只读（不启动应用、不联网、不写业务数据）
- 审计工具：`scripts/i18n_audit.py`（可达性优先 + 作用域化）
- 工具测试：`test_i18n_audit.py`（96/96 通过）

> 本修订版不进入业务代码实施。上文所有数字均可用文末命令复现。
> 本阶段只修改了三个文件：`scripts/i18n_audit.py`、`test_i18n_audit.py`、`docs/i18n-audit.md`。

---

## 阶段状态总表（截至阶段 4B）

| 阶段 | 范围 | 状态 |
| --- | --- | --- |
| 阶段 1 | 全站国际化审计（可达性 + 作用域） | ✅ verified（静态 / 离线） |
| 阶段 2 | locale registry / BCP 47 / 界面语言持久化 | ✅ **verified**（自动测试 + 真实浏览器验收 41/41） |
| 阶段 3 | 统一 `content_locale` 与 `localized_content` 协议 | ✅ **implemented，automated tests passed**（协议层交付，**无浏览器验收**：未接线任何页面） |
| 阶段 4A | Current Risk 接入统一协议 | ✅ **verified**（自动测试 + 真实浏览器验收 56/56） |
| 阶段 4B | Vision Risk 接入统一协议 | ✅ **verified**（自动测试 274/274 + 真实浏览器验收 62/62） |
| 阶段 4C | Report 生成接入统一协议 | ⏳ **not started** |

> 阶段 3 **不得**记为"浏览器 verified"：它只交付数据结构、语言轴与只读兼容，
> 没有接线任何页面，也没有浏览器验收（见 S3.4 脚注）。
> 阶段 4B **不含** Report 生成与 docx 导出，那部分属阶段 4C。

---

## 阶段 2 实施更新（locale registry / BCP 47 / 界面语言持久化）✅ verified

阶段 2 已实施**并完成真实浏览器验收**（见 S2.5，41/41 通过），本节记录审计侧的变化。**它不推翻第 1~12 节的任何结论**
（`pages/*` 不可达、runtime `st.*` 硬编码为 0、legacy 硬编码不阻断发布等全部保持不变），
只把语言清单从"审计脚本硬编码"升级为"读 `locales/manifest.json`"。

### S2.1 语言清单的唯一来源

| 产物 | 作用 |
| --- | --- |
| `locales/manifest.json` | **数据**：语言清单唯一来源（11 条：`en`、`zh-CN` + 9 个 `planned`） |
| `utils/locale_registry.py` | **行为**：读取 / 校验 / 归一化（BCP 47 别名解析、fallback 链、direction） |
| `utils/ui_language_state.py` | **状态内核**：`ui_lang` 单写入者（初始化 + 用户选择） |
| `app.py` | `?lang=` 持久化、`st.context.locale` 浏览器候选、registry 驱动的选择器 |

`en` 与 `zh-CN` 为 `status: stable` 且 `ui_enabled: true`；
`es`、`pt-BR`、`fr`、`sw`、`de`、`ar`、`hi`、`ja`、`ko` 为 `planned`（`ui_enabled: false`，`ar.direction: rtl`）。
planned 语言**只登记元数据**：不出现在选择器、不被 `i18n.load_locale()` 加载，因此**不要求** `<code>.json`。

### S2.2 审计脚本的 manifest 接入

`scripts/i18n_audit.py` 新增（只读 JSON，不导入应用代码，与 `utils/locale_registry.py` 互为独立交叉验证）：

| 检查 | 级别 | 说明 |
| --- | --- | --- |
| manifest 自洽 | **ERROR** | 顶层必须是数组；`code` 唯一非空；`native_name`/`english_name`/`ai_language_name` 非空；`direction ∈ {ltr,rtl}`；`status ∈ {stable,experimental,planned}`；`ui_enabled` 为布尔；planned 不得 `ui_enabled`；fallback 不得自指 / 悬空 / 成环；恰好一个 `is_default` 且必须可用 |
| UI 可选语言必须有 JSON | **ERROR** | 仅对 `ui_enabled` 且非 `planned` 的语言检查 |
| planned 不要求 JSON | INFO | 明确列出，避免误判为缺失 |
| 报告 direction / status / fallback | INFO | `0b. locale manifest` 段落以表格输出 |

- 审计语言不再硬编码：默认取 manifest 中 `ui_enabled` 且非 `planned` 的语言；
  manifest 不可用时回退 `("en", "zh-CN")` 并把问题记为 ERROR。
- 基准语言优先取 manifest 的默认 locale（`en`），保证与运行时一致。
- `--languages` 默认值改为 `None`（由 manifest 推导）；显式传入仍可覆盖。

### S2.3 阶段 2 复核数字（`--scope all --json`）

| 指标 | 阶段 1 | 阶段 2 | 变化原因 |
| --- | --- | --- | --- |
| manifest 问题 | — | **0** | manifest 自洽 |
| UI 可选语言 | — | `en`, `zh-CN` | 与阶段 1 一致 |
| 仅登记（planned） | — | 9 种 | 新增保留元数据 |
| runtime 文件数 | 8 | **9** | 新增 `utils/locale_registry.py`（runtime 可达） |
| runtime ERROR | 0 | **0** | 不变 |
| runtime WARNING | 17 | **17** | 不变（10 动态 key + 7 docx 通道） |
| runtime INFO | 79 | **79** | 不变 |
| legacy 文件数 / WARNING | 17 / 372 | **17 / 372** | **不变** |
| tests_and_tools 文件数 | 11 | **12** | 新增 `test_locale_registry.py` |
| locale key（en / zh-CN） | 483 / 483 | **483 / 483** | 不变 |

> `app.py` 因新增 URL/浏览器语言逻辑而变长，docx 通道的 7 处英文标签行号随之下移
> （如 `'Language:'` 由 3804 → 3912），**性质与数量不变**。

### S2.4 测试与验证（阶段 2 复核）

| 命令 | 结果 |
| --- | --- |
| `python3 -m py_compile ...` | 通过 |
| `python3 test_locale_registry.py` | **273/273** 通过 |
| `python3 test_ui_language_state.py` | **224/224** 通过 |
| `python3 test_i18n_audit.py` | **120/120** 通过（阶段 1 为 96/96） |
| `python3 test_localized_baseline_display.py` | **89/89** 通过 |
| `python3 test_localized_log_display.py` | **208/208** 通过 |
| `python3 test_helpers.py` | **69/69** 通过 |
| `python3 scripts/i18n_audit.py --scope runtime --report-only` | ERROR 0 / WARNING 17 / INFO 79 |
| `python3 scripts/i18n_audit.py --scope legacy` | 退出码 0（legacy 372 WARNING **不阻断**） |
| `sha256(data/projects.json)` | `07befa9161480f023f14794babc52754600be4c309078a26c9e41dffa4d92735`（44773 字节，**未变化**） |
| 真实浏览器验收（Playwright + Chromium 1234，无头） | **41/41** 通过，服务端日志 0 异常 |

### S2.5 真实浏览器验收（Section IX）

环境：**Streamlit 1.63** 无头运行于 `127.0.0.1:8599`；**Playwright 1.62** + 已缓存 Chromium（revision 1234）。
覆盖 7 个场景，共 **41 项断言，41/41 全部通过**。

**零异常**：`DuplicateWidgetID` = 0、`Traceback` = 0、`console.error` = 0（`pageerror` 亦为 0）。

**数据完整性**：本次验收只做页面交互与刷新，未生成 AI 报告、未写业务数据；
`sha256(data/projects.json)` = `07befa9161480f023f14794babc52754600be4c309078a26c9e41dffa4d92735`（**未变化**）。

| 场景 | 结果 | 关键证据 |
| --- | --- | --- |
| 1 无白屏 / 首屏渲染 | ✅ | 首屏正文 1276 字符，无 `stException` |
| 2 浏览器 locale 生效 | ✅ | `locale=zh-CN` → 界面中文且 URL 自动变为 `?lang=zh-CN`；标题本地化为「全球施工风险管理」 |
| 3 无无限 rerun / 无闪烁 | ✅ | 4s 内正文完全稳定；单 WebSocket 会话；同文档导航计数 2（含初始 `about:blank`） |
| 4 无弃用告警 | ✅ | 无 `deprecat*` / `experimental_get_query_params`；无 `pageerror`、无 `console.error` |
| 5 刷新持久化 | ✅ | 刷新后 URL 仍 `?lang=zh-CN`，界面与选择器仍为「简体中文」 |
| 6 参数优先级与非法值 | ✅ | URL `?lang=zh-CN` 覆盖浏览器 `en-US`；`zh-TW`（繁体）被拒→`?lang=en`；planned `es` 被拒→`?lang=en`；选择器选项仅 `['English','简体中文']` |
| 7 切换语言不改项目 / 报告 | ✅ | overview 切换语言后项目仍为 `Nairobi Affordable Housing`、模块卡片保留；报告中心内 `risk_report_language` 由 `Swahili` → `Swahili` 不变，`dashboard_view` 保留；URL 双向均正确回写 |

设计确认：**只有界面语言跨刷新持久化**（经 `?lang=`）；报告中心等属会话内状态，整页刷新后按设计回到 `overview`。

验收截图：`/tmp/i18n-acceptance/`（`01-first-paint-zh-CN.png`、`02-after-reload-zh-CN.png`、
`03-selector-options-en.png`、`04-overview-zh-CN.png`、`05-report-center-en.png`）。

> 说明：`Streamlit 1.63` 的 selectbox 已由 BaseWeb 迁移为 React Aria ComboBox，
> 选中值位于 `input[role="combobox"]` 的 `value`，选项为 `[role="option"]`；
> 容器上仍有 `st-key-<key>` 类。后续 UI 自动化测试应按此定位。

---

## 阶段 3 实施更新（统一 `content_locale` 与 `localized_content` 协议）✅ verified

阶段 3 建立**统一的多语言动态内容数据协议**。本节只记录审计侧的变化；
**它不推翻第 1~12 节的任何结论**——`pages/*` 不可达、runtime `st.*` 硬编码为 0、
legacy 硬编码不阻断发布，全部保持不变。

协议细节见 **`docs/localized-content-schema.md`**（独立契约文档）。

### S3.1 新增模块与职责

| 产物 | 作用 |
| --- | --- |
| `utils/localized_content.py` | **数据协议**：record schema、payload 校验、输入指纹、状态判定、旧 archive 只读兼容 |
| `utils/content_locale.py` | **语言轴**：内容语言由界面语言**推导**（不做镜像），仅在显式设置时写 `content_locale` |
| `utils/baseline_display.py` | `ai_text_lookup()` 升级为 Baseline / Daily Logs 的统一读取入口（新协议 → 旧 archive），`ai_text_store()` **双写** |
| `utils/log_display.py` | 沿用统一读取入口，无需改动业务逻辑 |
| `test_localized_content.py` / `test_content_locale.py` | 新增测试（409 / 104 项） |

### S3.2 三条语言轴

| 轴 | 存储 | 阶段 3 变化 |
| --- | --- | --- |
| `ui_locale` | `ui_lang` | **写入点仍只有 1 处**（`app.py` 选择器回调），静态守卫持续校验 |
| `content_locale` | `content_locale`（可选） | **新增**；默认由 `ui_locale` 推导，不落盘、不产生镜像状态 |
| `report_locale` | `risk_report_language` | 不变，与上面两轴完全独立 |

> **刻意设计**：切换界面语言**不会**写入 / 覆盖 `content_locale`。
> 因此不存在"两处内容语言状态不同步"这一整类缺陷。

### S3.3 审计数字（阶段 2 → 阶段 3）

| 指标 | 阶段 2 | 阶段 3 | 变化原因 |
| --- | --- | --- | --- |
| manifest 问题 | 0 | **0** | 不变 |
| runtime 文件数 | 9 | **11** | 新增 `utils/locale_registry.py`（已有）+ `utils/content_locale.py`、`utils/localized_content.py`（runtime 可达） |
| runtime ERROR | 0 | **0** | 不变 |
| runtime WARNING | 17 | **17** | 不变（10 动态 key + 7 docx 通道） |
| runtime INFO | 79 | **79** | 不变 |
| legacy 文件数 / WARNING | 17 / 372 | **17 / 372** | **不变** |
| tests_and_tools 文件数 | 12 | **14** | 新增 `test_content_locale.py`、`test_localized_content.py` |
| locale key（en / zh-CN） | 483 / 483 | **483 / 483** | **不变**（本阶段不新增翻译） |
| `sha256(data/projects.json)` | `07befa9…2735` | **`07befa9…2735`（44773 字节，未变化）** | **不迁移数据** |

### S3.4 测试与验证（阶段 3 复核）

| 命令 | 结果 |
| --- | --- |
| `python3 test_content_locale.py` | **104/104** 通过 |
| `python3 test_localized_content.py` | **409/409** 通过 |
| `python3 test_localized_baseline_display.py` | **121/121** 通过（含 32 项新协议用例） |
| `python3 test_localized_log_display.py` | **249/249** 通过（含 41 项新协议用例） |
| `python3 test_ui_language_state.py` | **333/333** 通过（含 109 项三轴矩阵用例） |
| `python3 test_locale_registry.py` | **273/273** 通过 |
| `python3 test_i18n_audit.py` | **125/125** 通过（含 5 项阶段 3 可达性守卫） |
| `python3 scripts/i18n_audit.py --scope runtime --report-only` | ERROR 0 / WARNING 17 / INFO 79 |
| `python3 scripts/i18n_audit.py --scope legacy` | 退出码 0（legacy 372 WARNING **不阻断**） |
| `.venv/bin/python test_helpers.py` | **69/69** 通过 |

> 本阶段**无浏览器验收**：未接线任何页面，未调用真实 AI，未改动 `pages/*`。

### S3.5 阶段 3 的边界（明确未做）

| 未做 | 说明 |
| --- | --- |
| 未迁移 `projects.json` | 73 处 AI 自由文本需重新生成，属阶段 4/5 |
| 未接线 Current Risk / Vision / Report | 只定义 `content_type` 契约；`pages/*`、`vision_risk.py`、`agents/risk_agent.py` 未引用本协议 |
| 未新增 `es`/`fr` 翻译文件 | 沿用阶段 2"只登记元数据"策略 |
| 未改风险公式 / 数值字段 | 可翻译载荷**任意深度**禁止携带 `penalty`/`score`/`level`/`confidence`/`coordinates`/`geometry`/`severity`/`likelihood`/`exposure` 等字段 |
| 未删除任何既有代码 | 旧 `ai_text_by_language` 继续**双写 + 只读兼容** |

---

## 阶段 4A 实施更新（Current Risk 接入统一 `localized_content` 协议）✅ 已实施（浏览器验收见 S4A.5）

阶段 4A **只处理 Current Risk**：页面可见的 AI 自由文本、AI prompt 的目标语言、
session cache 的项目/locale/input-hash 隔离、`localized_content` 存取、缺失/过期/重新生成状态。

**Vision Risk、Report 生成、日期数字格式、RTL、`pages/*`、Baseline/Logs 业务逻辑、风险公式与数值字段均未触碰。**

### S4A.1 新增与改动

| 产物 | 作用 |
| --- | --- |
| `utils/current_risk_display.py`（**新增**） | 纯函数层：输入指纹 `current_risk_input_hash()`、只读状态 `get_current_risk_display()`、payload 校验 `validate_current_risk_text_payload()`、prompt 构造 `build_current_risk_text_prompt()`、命名空间 cache key、显示层安全截断 |
| `app.py` | `ai_explain_current_risk()` 改为「目标语言 prompt → AI → payload 校验」；`render_current_risk_text()` 只渲染已校验 payload；Current Risk 视图按 `available / stale / missing / empty / invalid / legacy_compatible` 分支渲染；按钮触发写盘 + 会话缓存 |
| `agents/risk_agent.py` | `explain_current_risk(prompt)` 接收完整 prompt（**删除硬编码 `"Use English."`**），不再自行拼接语言指令 |
| `locales/en.json`、`locales/zh-CN.json` | 新增 18 个 Current Risk 文案 key（组件名、区块标题、按钮、四种状态提示） |
| `test_current_risk_localization.py`（**新增**） | 248 项断言：A~I 九组场景 + AST 静态守卫 |

### S4A.2 Current Risk 的语言、缓存与数据隔离

| 关注点 | 阶段 4A 行为 |
| --- | --- |
| 内容语言来源 | `content_locale`（默认由 `ui_locale` 推导；`planned` 语言归一为 `en`），**只读目标语言版本，无跨语言 fallback** |
| AI prompt 语言 | `build_current_risk_text_prompt(context, target_locale)` 使用 `ai_language_name(target_locale)`；JSON 字段名固定英文（机器契约，不翻译） |
| 输入指纹 | `sha256(schema_version + project_id + 天气/地形/日志/视觉/当前风险数值)`；**不含** `ui_lang`、`risk_report_language`、已生成文本、`current_risk_score` 等旧快照字段 |
| 会话缓存 | key = `(content_type, record_id, project_id, content_locale, input_hash)`，按 (项目, 语言) 分组 LRU(3)；**不做全局清空**，切语言 / 切项目天然隔离 |
| 持久化 | 统一写入 `project["localized_content"]["current_risk_explanation"]["project"][<locale>]`；**不再写 `ai_text_by_language`** |
| 旧数据 | 仅当 `ai_text_by_language` 的语言与目标语言**完全一致**时按 `legacy_compatible` 原样显示；未知 / 不匹配语言一律不显示，且**不自动迁移** |
| 触发边界 | AI 只在用户点击按钮时调用；页面打开 / rerun / 切语言 / 切项目 **0 次调用**（AST 守卫 + mock 测试） |
| 失败处理 | payload 校验失败或 AI 异常 → **不写盘、不缓存**，只显示失败提示 |
| 数值隔离 | 生成与读取前后 `score/level/components/weights/severity/likelihood/exposure/raw_risk` 及项目原始字段**完全不变** |

### S4A.3 审计数字（阶段 3 → 阶段 4A）

| 指标 | 阶段 3 | 阶段 4A | 变化原因 |
| --- | --- | --- | --- |
| manifest 问题 | 0 | **0** | 不变 |
| runtime 文件数 | 11 | **12** | 新增 `utils/current_risk_display.py`（runtime 可达） |
| runtime ERROR | 0 | **0** | 不变 |
| runtime WARNING | 17 | **17** | 不变（10 动态 key + 7 docx 通道）；新增文案**全部使用字面量 `t()` key**，未引入动态 key |
| runtime INFO | 79 | **87** | 见脚注 |
| legacy 文件数 / WARNING | 17 / 372 | **17 / 372** | **不变** |
| tests_and_tools 文件数 | 14 | **15** | 新增 `test_current_risk_localization.py` |
| locale key（en / zh-CN） | 483 / 483 | **500 / 500** | 阶段 4A 新增 **18** 个 Current Risk key（见 S4A.1） |
| `sha256(data/projects.json)` | `07befa9…2735` | **`07befa9…2735`（44773 字节，未变化）** | **不迁移数据** |

> 脚注：当前是多人协作的**脏工作区**，`INFO`（未被静态引用的 key）与 locale key 的绝对增量
> **不完全归属本阶段**——工作区同时包含 `action.*`、`chain.node.*`、`hazard.*`、`reason.*` 等其他协作改动。
> 可核验的是：阶段 4A 新增的 18 个 key **全部被 `app.py` 静态引用**（`missing_referenced_keys = []`、
> 跨语言 `placeholder_mismatch = 0`、`en` 与 `zh-CN` key 集合完全一致）。

### S4A.4 测试与验证（阶段 4A 复核）

| 命令 | 结果 |
| --- | --- |
| `python3 -m py_compile app.py agents/risk_agent.py utils/current_risk_display.py utils/localized_content.py …` | 通过 |
| `python3 test_current_risk_localization.py` | **248/248** 通过（新增） |
| `python3 test_localized_content.py` | **410/410** 通过（阶段 3 的「Current Risk 未接线」守卫升级为接线守卫） |
| `python3 test_content_locale.py` | **104/104** 通过 |
| `python3 test_localized_baseline_display.py` | **121/121** 通过 |
| `python3 test_localized_log_display.py` | **249/249** 通过 |
| `python3 test_locale_registry.py` | **273/273** 通过 |
| `python3 test_ui_language_state.py` | **333/333** 通过 |
| `python3 test_i18n_audit.py` | **125/125** 通过 |
| `python3 test_helpers.py` | **69/69** 通过 |
| `python3 scripts/i18n_audit.py --scope runtime --report-only` | ERROR 0 / WARNING 17 / INFO 87 |
| `python3 scripts/i18n_audit.py --scope runtime`（严格） | 退出码 **1**，沿用 §2.2 既有策略（17 个 runtime WARNING）；阶段 4A **未新增**任何 WARNING |
| `python3 scripts/i18n_audit.py --scope legacy` | 退出码 0（legacy 372 WARNING **不阻断**） |
| `sha256(data/projects.json)` | `07befa9161480f023f14794babc52754600be4c309078a26c9e41dffa4d92735`（44773 字节，**未变化**） |

> 测试全部**离线**：不联网、不调用真实 AI（`RiskAgent.explain_current_risk` 与 AI client 均被 mock）、
> 不写 `data/projects.json`、不导入 `app.py`（`app.py` 导入即执行 Streamlit 页面，改用 AST 静态检查）。

### S4A.5 真实浏览器验收（阶段 4A）

**方法**：在 `/tmp/rp_accept` 做项目**只读副本**（仅把 `data/projects.json` 换成临时 fixture），
用 `runpy` 执行**未经修改**的 `app.py`，并注入**确定性 mock AI**
（替换 `RiskAgent.explain_current_risk`）与离线的天气 / 地理编码响应。
因此本次验收：**不联网、不调用真实 AI、不写入真实 `data/projects.json`**。

| 项 | 值 |
| --- | --- |
| 地址 | `http://127.0.0.1:8611` |
| 浏览器 | Playwright + 已缓存 Chromium（headless，1440×1150） |
| 脚本 | `/tmp/rp_accept/acceptance.py`（临时环境，未提交到仓库） |
| 结果 | **56 / 56 通过** |
| 截图 | `/tmp/rp_accept/shots/01…09_*.png`（9 张） |
| 真实数据 | `data/projects.json` sha256 **未变化**（`07befa9…2735`） |

**验收路径（单会话，全部为真实 UI 操作）**

| # | 操作 | 期望 | 结果 |
| --- | --- | --- | --- |
| 1 | 打开项目 → 进入 Current Risk | `empty` 状态 + 「用当前语言生成解释」；确定性组件组成显示 `Baseline: 58/100` 等英文文案 | ✅ |
| 2 | 等待 rerun | AI 调用 **0** | ✅ |
| 3 | 点击生成（`ui=en`） | AI 调用 **1** 次；prompt 目标语言 English；显示英文正文；按钮变为「Analyze Why…」 | ✅ |
| 4 | 切换语言到 `简体中文` | AI 调用 **0**；显示 `missing`（并列出已保存 English）；**不回退显示英文正文**；组件名本地化为「风险组成」 | ✅ |
| 5 | 点击生成（`ui=zh-CN`） | AI 调用累计 **2** 次；第二次 prompt 为 Simplified Chinese；显示中文正文 | ✅ |
| 6 | 切回 `English` | AI 调用 **0**；直接显示**已存储**的英文正文（非重新生成） | ✅ |
| 7 | 经项目组合页切换到第二个项目 | AI 调用 **0**；仪表盘切到目标项目；**上一项目正文不显示** | ✅ |
| 8 | 打开新项目 Current Risk | AI 调用 **0**；显示 `stale` 警告 + 「查看过期版本」入口；过期正文**默认折叠不可见** | ✅ |
| 9 | 展开「过期版本」 | 过期正文可见；AI 调用仍 **0** | ✅ |
| 10 | 点击重新生成 | AI 调用累计 **3** 次；显示新正文；stale 警告消失；过期正文不再出现 | ✅ |
| 11 | 全流程 | `stException` 计数 **0**；无跨语言正文串扰 | ✅ |

**AI 调用证据**（mock 逐次记录 `ai_calls.jsonl`）：共 **3** 次，locale 序列 `en → zh-CN → en`；
每次 prompt 约 2.8 KB，均含目标语言名与固定英文 JSON 字段名。
即：**页面打开 / rerun / 切换语言 / 切换项目 / 展开旧版本 = 0 次调用；点击按钮 = 1 次调用。**

**临时数据写入结果**：`Acceptance Alpha → localized_content{en, zh-CN}`；
`Acceptance Stale → localized_content{en}`（重新生成后覆盖为当前指纹）；
两个项目的 `ai_text_by_language` 全程为空 → **未双写 legacy 字段**。

### S4A.6 阶段 4A 的边界（明确未做）

| 未做 | 说明 |
| --- | --- |
| Vision Risk | `vision_risk.py` prompt 仍全中文硬编码（缺陷 **D2 保留**） |
| Report 生成 / docx 导出 | 仍走各自既有路径（缺陷 **D3 保留**） |
| 日期、数字、单位格式化 | 未引入 locale-aware formatter（缺陷 **D7 保留**） |
| RTL / 新增 locale 文件 | 未新增 `es`/`fr` 等翻译文件 |
| `pages/*` | 未修改（仍不可达） |
| Baseline / Daily Logs 业务逻辑 | 未改变（协议读取路径已在阶段 3 接线） |
| 风险公式与数值字段 | `compute_current_risk()` 未改动；可翻译载荷仍禁止携带数值字段 |
| 历史数据迁移 | 未迁移 `projects.json` |

---

## 阶段 4B 实施更新（Vision Risk 接入统一 `localized_content` 协议）✅ verified

阶段 4B **只处理 Vision Risk**：把"识别事实"与"人类可读文本"分层——canonical detection
保持**语言无关**，AI 自由文本按**目标内容语言**单独生成，并按
`project / image / locale / input hash` 隔离存取。

**Report 生成与 docx 导出（阶段 4C）、日期数字格式化、RTL、`pages/*`、
Baseline/Logs 业务逻辑、风险公式与本体 key、历史项目数据均未触碰。**

### S4B.1 新增与改动

| 产物 | 作用 |
| --- | --- |
| `utils/vision_display.py`（**新增**） | 纯函数层：detection 规范化 / 校验、`vision_record_id()`、`vision_localization_input_hash()`、文本 payload 校验、目标语言 prompt、cache key、只读显示状态合并 |
| `vision_risk.py` | `build_vision_prompt()` 只产出 canonical detection；`call_deepseek_vision()` 保留视觉通道；新增 `ai_generate_vision_text()`、`store_vision_text()`；`vision_risk_page()` 按 `available / missing / stale / legacy_compatible / invalid` 分支渲染 |
| `agents/risk_agent.py` | 新增 `explain_vision_text(prompt)`：接收完整 prompt，不再自带语言指令 |
| `locales/en.json`、`locales/zh-CN.json` | 新增 Vision 文案（本工作区快照：`vision.*` key **81 / 81**，两侧 key 集合一致） |
| `test_vision_localization.py`（**新增**） | **274** 项断言：A~J 场景 + AST 静态守卫 |

### S4B.2 Vision 架构结果（阶段 4B 实际实现）

1. **canonical detection 与 localized text 已分层**：`build_vision_prompt()`（`vision_risk.py:334`）
   只请求机器可计算的识别事实（本体键、等级、置信度、坐标），不产出标题 / 原因 / 建议；
   人类可读文本由 `build_vision_text_prompt()`（`utils/vision_display.py:1178`）在目标语言下
   用**文本**模型单独生成。
2. **detection cache 不以 locale 区分**：`vision_detection_cache_key()`
   （`utils/vision_display.py:1272`）= `(project_id, image_hash, model_id, ontology_version)`，
   **无 locale 分量**，切换界面语言复用同一检测，不重跑视觉模型。
3. **localized text 以 project / image / locale / input hash 隔离**：
   `vision_text_cache_key()`（`utils/vision_display.py:1293`）=
   `(project_id, content_type, record_id, locale, input_hash)`；
   `record_id = vis-<sha256(project_id | image_hash | detection_schema_v1)[:32]>`
   （`vision_record_id()`，`utils/vision_display.py:895`，因此**相同文件名但 bytes 不同**的图片
   必然得到不同 record）；
   持久化路径 `project["localized_content"]["vision_analysis"][<record_id>][<locale>]`
   （`store_vision_text()`，`vision_risk.py:1619`）；
   `input_hash` 只由识别事实（每条 finding 的 id / 键 / 等级 / 惩罚 / 坐标 / 置信度 + 总体等级 +
   惩罚总分）构成（`build_vision_localization_input()`，**不含** locale、UI 状态、报告语言与时间戳）。
4. **"生成当前语言版本"不会调用视觉识别模型**：只调用**文本**模型 1 次，
   不重传图片、不重跑识别、不改任何识别事实。
5. **切换语言不改变** `finding_id`、`confidence`、`location`、`penalty`、`level`
   以及 Current Risk 的视觉数值贡献：canonical detection 存于 `project["vision_detection"]`
   （`DETECTION_FIELD`，`utils/vision_display.py:83`），切换语言只切换 `localized_content`
   的读取叶子。
6. **未修改本体 key、风险公式与历史项目数据**：`RISK_ONTOLOGY` 定义（`vision_risk.py:46`）未改动
   （仅被读取用于生成本地化标签）；`compute_current_risk()` 未改动；
   `data/projects.json` sha256 前后一致（未迁移、未新增 legacy 双写）。

> 旧数据兼容：无 `vision_detection` 的历史项目可按 `legacy_compatible` **只读**显示旧
> `visual_risks`（`legacy_vision_text()`），**不做自动迁移**，也不会被当作目标语言的最新内容。

### S4B.3 阶段 4B 验收摘要

| 项 | 值 |
| --- | --- |
| `test_vision_localization.py` | **274 / 274** |
| 真实浏览器验收 | **62 / 62** |
| mock vision 调用 | **2** |
| mock text 调用 | **3** |
| text locale 顺序 | `en → zh-CN → en` |
| real AI 调用 | **0** |
| Streamlit 异常 / `DuplicateWidgetID` | **0 / 0** |
| `sha256(data/projects.json)` | `07befa9161480f023f14794babc52754600be4c309078a26c9e41dffa4d92735`（**前后完全一致**） |
| 正式仓库改动 | 最终重跑过程**零修改**（写入仅发生在 `/tmp` 临时验收目录） |

浏览器验收方法沿用阶段 4A：项目**只读副本** + 临时 fixture + **确定性 mock AI**
（视觉与文本模型全部 mock），因此**不联网、不调用真实 AI、不写真实 `data/projects.json`**；
调用次数由 mock 逐次记录的调用日志统计。

### S4B.4 浏览器实测通过的状态与边界

| 项 | 结果 |
| --- | --- |
| `empty` | 上传后 0 次模型调用，显示上传态与「开始风险检测」入口 |
| `missing` | 中文界面显示"尚无本语言版本"并列出已保存语言；**不回退显示英文正文**；显示本语言的生成提示 |
| `available` | 识别 + 生成后显示当前语言正文与该语言的 available 状态 |
| `stale` | 过期正文默认折叠不可见，可展开只读查看；展开与切项目 **0 次调用**；重新识别后 stale 警告消失 |
| `zh-CN` / `en` 正文隔离 | 任一界面语言下都不显示另一语言的正文 |
| 项目隔离 | 切换项目 0 次调用，上一项目的识别结果与正文不串扰 |
| 切换语言不重新检测 | 切换语言 0 次调用，识别事实与正文语言互不影响 |
| 分析图片与生成语言版本的调用边界 | 打开 / rerun / 上传 / 切换项目 / 切换语言 / 展开旧版本 = **0 次**；「开始风险检测」= 1 视觉 + 1 文本；「用当前语言生成文本」= 1 文本 |
| detection 数值与事实保持不变 | 风险数 2、惩罚分 30、总体等级 HIGH 在两种界面语言下完全一致；幻觉风险未进入界面 |

### S4B.5 仅由自动测试覆盖（**未**浏览器实测）

| 项 | 覆盖方式 |
| --- | --- |
| `legacy_compatible`（旧 `visual_risks` 只读兼容态） | **自动测试**（`test_vision_localization.py`）；浏览器验收剧本**不含**该场景 |
| 相同文件名、不同图片 bytes 的 image hash 隔离 | **自动测试**；浏览器验收剧本**不含**该场景 |

> 浏览器验收范围为现有 **62 项**场景，**不包含**上述两项；
> 文档不得据此声称这两项已经过浏览器验证。

### S4B.6 阶段 4B 的边界（明确未做）

| 未做 | 说明 |
| --- | --- |
| Report 生成接入统一协议 | 属**阶段 4C（not started）**；`render_risk_report_center`、`export_report_to_word` 未接线 |
| Word 导出 7 处英文标签 | 缺陷 **D3 保留** |
| 日期、数字、单位格式化 | 缺陷 **D7 保留** |
| RTL / 新增 locale 文件 | 未新增 `es`/`fr` 等翻译文件 |
| `pages/*` | 未修改（仍不可达） |
| 历史数据迁移 | 未迁移 `projects.json`（`visual_risk*` 字段在 12 个项目中仍不存在） |

---

## 0. 修订说明：本次纠正了什么

初版审计按文件名假设 `pages/*` 属于产品代码，把 **143 处硬编码**归因于 `pages/*`，
并把"迁移 `pages/*`"列为下一阶段——这会把工投入当前不会执行的旧代码。

本次修订通过 **AST import 图 + `st.navigation` 注册表**核实运行时可达性，结论完全逆转：

| 项目 | 初版结论 | 修订版结论（经核实） |
| --- | --- | --- |
| `pages/*` 是否运行 | 假设运行 | **不可达**，`st.navigation` 禁用了传统 `pages/` 自动发现，且全仓无任何代码 `import pages` |
| 当前产品 UI 硬编码 | 143 处 | **7 处**（且全部在 Word 导出通道，`st.*` 页面 UI 通道为 **0**） |
| 硬编码总量 | 143 处 | **372 处**，其中 143 处在 `pages/*`、其余在 `app_before_ui2.py` 与备份目录 |
| 下一阶段建议 | 迁移 `pages/*` | **归档/删除旧代码**，迁移工作转向 runtime 真实缺口（见第 7 节） |

**一句话结论：当前产品可达代码的页面 UI 已 100% 走 `t()`；真正的国际化缺口不在硬编码，
而在 AI 内容语言、数字/日期本地化、内容语言元数据与三条语言轴的缺失。**

---

## 1. 运行时可达性核实

### 1.1 判定方法（不按文件名猜测）

1. 唯一启动命令确认为 `streamlit run app.py`（`README_FINAL.md`、`.code-workspace`）。
2. `app.py:6698` 调用 `st.navigation(...)`，注册 5 个 `st.Page(<函数>, ...)`：
   `new_project_page`、`app_page`、`project_dashboard_page`、`vision_risk_page`、`history_page`。
   `st.navigation` 存在时 Streamlit 不再自动发现 `pages/` 目录。
3. 全仓检索 `import pages` / `from pages`：**无任何匹配**（仅旧快照里有字符串 `"pages/project_dashboard.py"`）。
4. 从 `app.py` 出发做 **AST import 图 BFS**（`ast.walk` 覆盖函数体内的延迟 import），
   递归解析 `import X` / `from X.Y import Z`，映射到文件路径后得到可达闭包。

### 1.2 runtime_reachable（8 个文件）

```
app.py                        i18n.py                       vision_risk.py
agents/risk_agent.py          utils/__init__.py             utils/baseline_display.py
utils/log_display.py          utils/ui_language_state.py
```

要点：

- `agents/risk_agent.py` 通过 **函数内延迟 import** 可达（`app.py:326/881/1509/1763/3538`），
  因此属于 runtime——仅看文件顶部 import 会误判。
- `vision_risk.py` 在 `app.py:6654` 顶层 import，属 runtime。

### 1.3 legacy_unreachable（17 个文件）

```
app_before_ui2.py                        agents/__init__.py
agents/manager_agent.py                  agents/quality_agent.py
agents/safety_agent.py                   agents/schedule_agent.py
backup-before-teammate-update/app.py     backup-before-teammate-update/vision_risk.py
pages/baseline_risk.py                   pages/current_risk.py
pages/new_project.py                     pages/project.py
pages/project_dashboard.py               pages/weather_terrain.py
prompts/risk_prompt.py                   utils/storage.py
utils/weather.py
```

值得注意的非 `pages/` 不可达模块：

| 文件 | 说明 |
| --- | --- |
| `utils/storage.py` | 含 AI 调用与写盘逻辑，**当前无人调用**（死模块） |
| `utils/weather.py` | 天气存储层，**当前无人调用** |
| `agents/manager_agent.py` 等 4 个 | Manager/Quality/Safety/Schedule Agent 仅有测试，应用未使用 |
| `prompts/risk_prompt.py` | 0 字节空文件 |
| `app_before_ui2.py`、`backup-before-teammate-update/` | 旧版快照与备份 |

### 1.4 tests_and_tools（11 个文件）

`test_api.py`、`test_helpers.py`、`test_i18n_audit.py`、`test_localized_baseline_display.py`、
`test_localized_log_display.py`、`test_manager.py`、`test_quality.py`、`test_safety.py`、
`test_schedule.py`、`test_ui_language_state.py`、`scripts/i18n_audit.py`。

---

## 2. 审计脚本作用域（`--scope`）

### 2.1 语义

| `--scope` | 扫描范围 | 用途 |
| --- | --- | --- |
| `runtime`（**默认**） | 仅可达生产代码 | 当前产品发布门禁 |
| `legacy` | 仅不可达旧代码 | 量化 legacy debt，不阻断发布 |
| `all` | 两者，**报告分组** | 全面盘点 |

测试与工具默认排除，需 `--include-tests` / `--include-tools` 显式开启。

### 2.2 退出码矩阵（实测）

| 命令 | 退出码 | 依据 |
| --- | --- | --- |
| `--scope runtime --report-only` | **0** | 只输出报告 |
| `--scope runtime` | **1** | runtime 有 17 个 WARNING |
| `--scope runtime --errors-only` | **0** | runtime ERROR = 0 |
| `--scope legacy --report-only` | **0** | 只输出报告 |
| `--scope legacy` | **0** | legacy 的 372 个 WARNING **不阻断发布** |
| `--scope all --json --report-only` | **0** | 只输出报告 |

**严格 runtime 模式返回 1 的真实原因**：runtime 存在 17 个 WARNING =
10 个 `dynamic_translation_key`（无法静态校验的动态 key）+ 7 个 `hardcoded_string`
（Word 导出通道）。**与 legacy 硬编码完全无关**——`--scope legacy` 单独运行返回 0 即为证明。

### 2.3 JSON 契约

每条 finding 均携带 `scope` 与 `reachable`：

```json
{
  "severity": "warning",
  "category": "hardcoded_string",
  "location": "app.py:3804",
  "detail": "metadata.add_run -> 'Language:'",
  "scope": "runtime",
  "reachable": true
}
```

顶层另含 `reachability`（含逐文件分类与理由）、`scanned_files`、
`hardcoded_by_scope`、`severity_counts_by_scope`、`blocking_scope: "runtime"`。

---

## 3. 审计结果（分作用域）

| 作用域 | 文件数 | ERROR | WARNING | INFO |
| --- | --- | --- | --- | --- |
| **runtime** | 8 | **0** | **17** | 79 |
| legacy | 17 | 0 | 372 | 0 |
| tests_and_tools | 11 | 0 | 0 | 0 |

### 3.1 runtime 的 17 个 WARNING

| 类别 | 数量 | 位置 |
| --- | --- | --- |
| `dynamic_translation_key` | 10 | `app.py`(3)、`i18n.py`(2)、`vision_risk.py`(3)、`utils/baseline_display.py`(1)、`utils/log_display.py`(1) |
| `hardcoded_string` | 7 | `app.py` 的 `export_report_to_word`（docx `add_run` 通道） |

7 处硬编码明细（全部为 Word 导出元数据标签）：

```
app.py:3762  title.add_run      -> 'RiskPilot Risk Report'
app.py:3775  metadata.add_run   -> 'Project:'
app.py:3788  metadata.add_run   -> 'Audience:'
app.py:3796  metadata.add_run   -> 'Report Type:'
app.py:3804  metadata.add_run   -> 'Language:'
app.py:3812  metadata.add_run   -> 'Current Risk:'
app.py:3822  metadata.add_run   -> 'Generated:'
```

> 这 7 处**不随 `report_locale` 变化**，是导出报告里的固定英文标签（见缺陷 D3）。

### 3.2 硬编码按作用域分布

| 作用域 | 数量 | 文件分布 |
| --- | --- | --- |
| runtime | **7** | `app.py`（docx 通道） |
| legacy | **372** | 见下表 |

legacy 硬编码分布：

| 文件 | 数量 |
| --- | --- |
| `backup-before-teammate-update/app.py` | 160 |
| `pages/new_project.py` | 42 |
| `backup-before-teammate-update/vision_risk.py` | 41 |
| `pages/project_dashboard.py` | 40 |
| `app_before_ui2.py` | 28 |
| `pages/weather_terrain.py` | 21 |
| `pages/current_risk.py` | 18 |
| `pages/baseline_risk.py` | 12 |
| `pages/project.py` | 10 |
| **`pages/*` 小计** | **143** |

### 3.3 locale 层（共享，作用于 runtime）✔

| 语言 | key 数 |
| --- | --- |
| `en` | 483 |
| `zh-CN` | 483 |

- 缺失 key：0；多余 key：0；跨语言占位符不一致：0；无法解析占位符：0。
- `t()` 引用：301 次字面量调用 / 290 个不同 key；映射表 key 114 个；动态调用 10 次。
- 代码引用但 locale 缺失：**0**。

### 3.4 未被静态引用的 key（79 个，INFO）

78 个实为动态拼接引用（`ontology.*` 70、`report.*` 5、`risk.level.*` 3），
仅 **1 个为真实死 key**：`dashboard.info_log_language`。

---

## 4. 运行时内容分类（七类）

七类定义：`static_ui`（代码内静态 UI 文案）、`stable_enum`（闭集枚举）、
`deterministic_text`（规则生成的确定性语句）、`ai_generated_text`（LLM 生成）、
`user_generated_text`（用户输入）、`numeric_or_date`（数字/日期/单位）、
`external_api_text`（外部接口文本）。

### 4.1 真实页面定位（**均在 `app.py` 函数内，不存在对应的 `pages/*` 文件**）

| 页面 | 入口函数:行号 | 渲染文件:行号 | 主要数据来源 |
| --- | --- | --- | --- |
| Portfolio / App | `app_page()` `app.py:4331` | `app.py:4331-4757` | `projects.json` 项目列表 |
| New Project | `new_project_page()` `app.py:4810` | `app.py:4810-5089` | 用户表单 + `geocode_location` |
| Dashboard Overview | `project_dashboard_page()` `app.py:5096` | 视图 `overview` `app.py:6387-6648` | 项目汇总字段 |
| Baseline | 同上（`dashboard_view="baseline"`） | `app.py:5180-5332` | `project["baseline_risk"]` |
| Current Risk | 同上（`dashboard_view="current"`） | `app.py:5359-5732` | baseline + 天气 + 日志 |
| Weather & Terrain | 同上（`dashboard_view="weather"`） | `app.py:5737-5828` | `get_weather` / `get_historical_weather` / `get_elevation_grid` |
| Daily Logs | 同上（`dashboard_view="logs"`） | `app.py:5834-6279` | `project["daily_logs"]` |
| Daily Risk | 同上（`dashboard_view="daily"`） | `app.py:6286-6386` | 日志聚合 |
| Risk Report Center | 同上（`dashboard_view="risk_reports"`） | `render_risk_report_center()` `app.py:3955-4325` | baseline + 日志 + 天气 |
| Vision Risk | `vision_risk_page()` `vision_risk.py:1700` | `vision_risk.py:1700-2110` | 上传图片 + 视觉 AI |
| History | `history_page()` `app.py:4762` | `app.py:4762-4803` | `projects.json` 归档 |

> `project_dashboard_page()` 通过 `st.session_state.dashboard_view` 切换子视图（**不是 `st.tabs`**）。
> Baseline / Current Risk / Weather / Logs / Daily Risk / Report Center 都是它的子视图，
> 共用同一个渲染文件 `app.py`。

### 4.2 逐页本地化画像

| 页面 | 内容类型 | 存储字段 | 当前翻译函数 | locale 选择规则 | fallback 规则 | 跨语言泄漏风险 | 优先级 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Portfolio / App | `static_ui` + `stable_enum` + `user_generated_text` | `projects.json` 键名、`project_type`、`name` | `t()` / `risk_label()` / `mapping_label()` | `get_current_language()` | 静态 UI 回退 `en`；枚举回退原文 | 低 | P3 |
| New Project | `static_ui` + `user_generated_text` + `external_api_text` | `name`、`location`、`country`、`lat/lon`、`timezone` | `t()` | `get_current_language()` | 静态 UI 回退 `en` | 低（`display_name` 属外部原文） | P1 |
| Dashboard Overview | `static_ui` + `stable_enum` + `numeric_or_date` | 汇总字段（不落库） | `t()` / `mapping_label()` | `get_current_language()` | 静态 UI 回退 `en` | 低 | P2 |
| Baseline | `static_ui` + `stable_enum` + `deterministic_text` + `ai_generated_text` | `baseline_risk.*`（**无 language**） | `t()`；链/影响/措施走闭集翻译表 | `get_current_language()` | 闭集未命中 → 回退**原文**（可能是英文 AI 文本） | **中**：AI 生成的 chain/impact/action 若不在闭集，将以写入时的语言直出 | **P0** |
| Current Risk | `static_ui` + `stable_enum` + `ai_generated_text` + `numeric_or_date` | 仅 `st.session_state`（未落库） | `t()` / `risk_label()` | session key `f"current_risk_ai_analysis::{lang}"` | 无（结果不持久化） | **高**：AI 解释**恒为英文**（缺陷 D1） | **P0** |
| Weather & Terrain | `static_ui` + `numeric_or_date` + `external_api_text` | 不落库（`st.cache_data`） | `t()` | `get_current_language()` | 静态 UI 回退 `en` | 低 | P5 |
| Daily Logs | `static_ui` + `user_generated_text` + `ai_generated_text` + `stable_enum` | `daily_logs[]`：`notes`/`title`/`incidents`（用户），`ai_analysis.*`（**无 language**） | `t()`；确定性模板走 `log_display` | `get_current_language()` | 确定性模板可重渲染；AI 文本回退原文 | **高**：AI 分析文本随写入语言直出 | **P0** |
| Daily Risk | `static_ui` + `stable_enum` + `numeric_or_date` | 由日志聚合 | `t()` / `mapping_label()` | `get_current_language()` | 静态 UI 回退 `en` | 低 | P2 |
| Vision Risk | `static_ui` + `ai_generated_text` + `stable_enum` | `project["vision_detection"]`（语言无关 canonical detection）+ `localized_content.vision_analysis[<record_id>][<locale>]`（AI 文本） | `t()`（UI 外壳 + 确定性标签） | `content_locale`（显式传入文本 prompt） | 无（目标语言无版本时显示 `missing`，不回退其他语言） | **低（阶段 4B 修复 D2）**：识别事实与语言彻底解耦，正文按 locale 隔离 | **P0** |
| Risk Report Center | `static_ui` + `ai_generated_text` + `deterministic_text` | 仅 `st.session_state`；导出 docx | `t()`；prompt 走 `build_risk_report_prompt` | `report_locale`（报告语言） | 跟随报告语言 | **中**：docx 固定英文标签（缺陷 D3） | P1 |
| History | `static_ui` + `user_generated_text` + `numeric_or_date` | `projects.json` 归档 | `t()` | `get_current_language()` | 静态 UI 回退 `en` | 低 | P3 |

### 4.3 分类统计汇总（runtime）

| 内容类型 | runtime 覆盖 | 是否已本地化 |
| --- | --- | --- |
| `static_ui` | 483 个 locale key / 301 次 `t()` 调用 | ✅ 已本地化（`en` + `zh-CN`） |
| `stable_enum` | 114 个映射表 key + `risk_label` | ✅ 闭集已本地化，未命中回退原文 |
| `deterministic_text` | `utils/baseline_display.py`、`utils/log_display.py` 模板 | ✅ 可确定性重渲染 |
| `ai_generated_text` | 6 个运行时 AI 调用点 | ❌ **未受控**（见第 5 节） |
| `user_generated_text` | 41 个字段实例 | ✅ 应保持原文（正确行为） |
| `numeric_or_date` | 见 6.3 | ❌ **未本地化** |
| `external_api_text` | `display_name` 等 | ⚠️ 现状为原文直出（合理，需明确策略） |

---

## 5. 全球化能力审计

### 5.1 AI 内容清单（7 个调用点，其中 1 个为死代码）

| # | 函数 | 位置 | prompt 位置 | JSON schema | 目标语言来源 | 结果存储 | 缓存/归档 key | 语言复用风险 | 改变风险数值 | 用户触发 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `ai_baseline_assessment` | `app.py:766` | `app.py:784-874` + `ai_output_language_rules()` `app.py:447` | 有（`app.py:823-859`） | `get_current_language()` | `baseline_risk` + `ai_text_by_language[lang]["baseline"]` | `baseline` | 中：无 language 元数据，回读时无法判定 | **是**（AI 产出 risks/level） | 是（点击生成） |
| 2 | `ai_analyze_daily_log` | `app.py:1339` | `app.py:1356-1505` | 有（`app.py:1482-1494`） | `ai_output_language_rules()` | `daily_logs[].ai_analysis` + `.language` + `ai_text_by_language[lang]["logs"][log_id]` | `logs` + `log_id` | 中：`ai_analysis` 无 language 字段 | **是**（S/L/E） | 是 |
| 3 | `ai_generate_log_text_version` | `app.py:300` | `build_log_text_version_prompt` `utils/log_display.py:410` | 有 | **显式 `language` 参数** | 仅 `ai_text_by_language[lang]["logs"][log_id]` | `logs` + `log_id` | **低**（设计正确） | 否 | 是 |
| 4 | `ai_explain_current_risk` | `app.py:1797` | `build_current_risk_text_prompt()` `utils/current_risk_display.py:533` | 有 | ✅ **`content_locale`**（`planned` 归一 `en`）；`agents/risk_agent.py` 不再自带语言指令 | `localized_content[current_risk_explanation][project][locale]` + 会话缓存 | `(content_type, record_id, project_id, locale, input_hash)` | **低（阶段 4A 修复 D1）** | 否（纯解释） | 是 |
| 5 | Risk Report 生成 | `render_risk_report_center` `app.py:3955-4325` | `build_risk_report_prompt` `app.py:3182-3514` | 有 | **`report_locale` 参数** | 仅 `st.session_state`；可导出 docx | `risk_report_result` / `risk_report_language_used` | 低（session key 含语言） | 否 | 是 |
| 6 | `call_deepseek_vision` | `vision_risk.py:499` | `build_vision_prompt()` `vision_risk.py:334` | 有（非 `json_object` 模式） | ✅ **不需要语言**：本调用只产出语言无关 canonical detection（本体键 / 等级 / 置信度 / 坐标） | `project["vision_detection"]` | `(project_id, image_hash, model_id, ontology_version)`（**无 locale**，切语言复用） | **低（阶段 4B 修复 D2）** | **是**（penalty 影响分数） | 是 |
| 6b | `ai_generate_vision_text` | `vision_risk.py:1554` | `build_vision_text_prompt()` `utils/vision_display.py:1178` | 有 | ✅ **`content_locale`**（显式 `target_locale`，不做隐式推断） | `localized_content.vision_analysis[<vis-…>][<locale>]` + 会话缓存 | `(project_id, content_type, record_id, locale, input_hash)` | **低（阶段 4B）** | 否（只写人类可读文本） | 是 |
| 7 | `analyze_daily_log` | `utils/storage.py:1891` | 同模块内 | 有 | — | 写 `projects.json` | — | — | 是 | **死代码**（模块不可达） |

**关键结论**：6 个运行时 AI 调用点中，**#3 / #4 / #5 / #6b 的语言受控正确**；
#6 不需要语言（只产出语言无关识别事实）；**#1 / #2 虽有语言规则但产物缺少语言元数据**（待阶段 5 处理）。

### 5.2 缓存清单

| 类型 | 位置 | cache key 组成 | 含 locale | 含 project/record id | 串扰风险 |
| --- | --- | --- | --- | --- | --- |
| `st.cache_data` | `app.py:2165` `geocode_location` (ttl=86400) | 地址字符串 | 否 | 否 | 无（结果与语言无关） |
| `st.cache_data` | `app.py:2212` `get_weather` (ttl=1800) | lat/lon | 否 | 否 | 无 |
| `st.cache_data` | `app.py:2240` `get_historical_weather` (ttl=1800) | lat/lon/日期 | 否 | 否 | 无 |
| `st.cache_data` | `app.py:2303` `get_elevation_grid` (ttl=86400) | 边界框 | 否 | 否 | 无 |
| `st.cache_resource` | **无** | — | — | — | — |
| `session_state` | `current_risk_ai_cache` `app.py:532` | `(content_type, record_id, project_id, locale, input_hash)`，按 (项目, 语言) 分组 LRU(3) | ✅ 是 | ✅ 是 | **无（阶段 4A 修复 D4 的 Current Risk 部分）** |
| `session_state` | `risk_report_result` / `risk_report_language_used` | 报告结果 + 语言 | ✅ 是 | ❌ 否 | **是**：跨项目复用风险 |
| `session_state` | Vision 检测 / 文本命名空间 | 检测键 `(project_id, image_hash, model_id, ontology_version)`；文本键 `(project_id, content_type, record_id, locale, input_hash)` | 检测键刻意**不含** locale；文本键**含** locale | ✅ 是 | **无（阶段 4B 修复 D4 的 Vision 部分）** |
| `projects.json` | `ai_text_by_language[<lang>][<section>][<record_id>]` | 语言 + 分区 + 记录 id | ✅ 是 | ✅ 是 | **低（设计正确）**；但**当前数据中该字段完全不存在**（0 个项目） |

### 5.3 日期、数字与单位

**当前状态：全仓无任何 locale-aware formatter（`requirements.txt` 无 Babel）。**
所有格式化均为 Python f-string 硬编码英文/ISO 写法。

| 类型 | 运行时位置 | 当前写法 | locale-aware |
| --- | --- | --- | --- |
| 日期（展示） | `app.py` 多处 `datetime.now()` 直接插值 | ISO `YYYY-MM-DD` | ❌ |
| 时间 | 日志时间戳 | ISO | ❌ |
| 整数 / 千位分隔 | 无千位分隔实现 | 裸整数 | ❌ |
| 小数 | 里程/坐标等 | 裸 float / `round()` | ❌（小数点符号未本地化） |
| 百分比 | 概率、完成度 | `f"{x}%"` | ❌（`fr`/`de` 需空格与逗号） |
| 温度 | `app.py:5765` 等 | `"°C"` 硬编码字符串 | ❌（`en-US` 需 °F，`ar` 需方向处理） |
| 降水量 | 天气区块 | `mm` 硬编码 | ❌ |
| 风速 | 天气区块 | 原始数值，无单位本地化 | ❌ |
| 高程 | `get_elevation_grid` 展示 | `m` 硬编码 | ❌ |
| 风险分数 | `risk_label()` / 分数徽章 | 0-100 整数 | ⚠️ 数字本身可接受，但档位文案走枚举 |
| 货币 | **不存在** | — | — |
| docx 导出 | `export_report_to_word` `app.py` | `strftime` 英文月份缩写 + 固定英文标签 | ❌（缺陷 D3） |

> **文档字符串已翻译 ≠ 数字与单位已本地化。** 这一项是独立的、当前完全未覆盖的能力域。

### 5.4 外部 API 文本

| 来源 | 函数 | 会直接展示的文本字段 | 展示位置 | 建议策略 |
| --- | --- | --- | --- | --- |
| 地理编码 | `geocode_location` `app.py:2165` | `display_name`（如 `"Bucharest, Romania"`） | `app.py:3001` 经 `t("dashboard.elevation_caption", location=…)` 插入 | **保持原文**（地名不翻译），但需按地区格式化；`country` 可走枚举映射 |
| 天气 | `get_weather` `app.py:2212` | 仅数值（温度/降水/风速），无描述文本 | 天气区块 | 数值需按 locale 格式化（见 5.3）；单位需可切换 |
| 历史天气 | `get_historical_weather` `app.py:2240` | 仅数值 | 天气区块 | 同上 |
| 高程 | `get_elevation_grid` `app.py:2303` | 仅数值 | 地形区块 | 单位本地化 |
| 时区名 | 地理编码结果 | `timezone`（如 `"Europe/Bucharest"`） | 当前未展示 | 如展示需映射为本地化名称 |

> 对外部文本引入"翻译"会把地名变成不可检索的错误值，建议**保持原文**；
> 真正需要的是**按地区格式化**与**国家/时区枚举的本地化显示名**。

### 5.5 现有项目数据兼容性（`data/projects.json` 只读统计）

| 指标 | 数量 |
| --- | --- |
| 项目总数 | **12** |
| Baseline 有 `language` 元数据 | **0** |
| Baseline 无 `language` 元数据 | **12** |
| Baseline 各语言 archive（`ai_text_by_language`） | **0** |
| 日志总数 | **22** |
| 日志有 `ai_analysis.language` 元数据 | **0** |
| 日志无 `ai_analysis.language` 元数据 | **22** |
| 可确定性翻译文本（闭集命中） | **27**（风险链 2 + 影响 9 + 措施 16） |
| 必须重新生成的 AI 自由文本 | **73**（日志摘要 22 + 风险标题/原因/建议各 17） |
| 用户输入文本字段（应保持原文） | **41** |
| Current Risk 已保存文本 | **0**（仅存于 session，未持久化） |
| Vision 已保存文本 | **0**（`visual_risk*` 字段在 12 个项目中均不存在） |
| Reports 已保存文本 | **0**（`risk_reports` 字段为空） |

**兼容性结论**：

1. 存量数据的语言**完全未知**——无任何 `language` 元数据。任何语言感知读取都必须
   把"无元数据"显式当作 `"und"`（undetermined），并在 UI 上提示"该内容语言未知"，
   **不得隐式当作当前 `ui_locale`**。
2. 27 项确定性文本可通过闭集翻译表**零成本重渲染**（这也是 `baseline_display` /
   `log_display` 已有的正确设计）。
3. 73 项 AI 自由文本**必须重新调用 AI 生成**才能切换语言——存在付费 AI 成本，
   需在路线图阶段 3/4 中按需触发并做 per-record 缓存。
4. Current Risk / Vision / Reports 未持久化，当前无迁移负担，但也意味着
   用户在会话内切换语言后**看不到语言一致的内容**。

---

## 6. 统一目标架构（本阶段只定义，不实施）

### 6.1 统一内部 locale code（BCP 47）

```
en  zh-CN  es  pt-BR  fr  sw  de  ar  hi  ja  ko
```

现状 `utils/ui_language_state.py` 的 `SUPPORTED_LANGUAGES = {"en": "English", "zh-CN": "简体中文"}`
已做到"code 作键、显示名作值"，但**缺少 `english_name`、`fallback`、`direction`、
`ai_language_name`、`status`**，无法支撑 RTL 与 AI prompt 构造。

### 6.2 三条独立语言轴（关键设计）

| 轴 | 语义 | 默认 | 现状 |
| --- | --- | --- | --- |
| `ui_locale` | 产品界面语言 | 用户偏好 | ✅ 已有（`ui_language_state`） |
| `content_locale` | AI 页面内容语言 | 跟随 `ui_locale` | ❌ 缺失（导致 D1/D2） |
| `report_locale` | 导出报告语言，可独立选择 | 跟随 `ui_locale` | ⚠️ 部分存在（`risk_report_language_used`） |

**三者必须解耦**：当前把 `ui_locale` 当作内容语言的唯一来源，正是 Vision 恒中文、
Current Risk 恒英文的根因。

### 6.3 locale manifest 建议字段

```json
{
  "code": "pt-BR",
  "native_name": "Português (Brasil)",
  "english_name": "Portuguese (Brazil)",
  "fallback": "en",
  "direction": "ltr",
  "ai_language_name": "Brazilian Portuguese",
  "status": "experimental"
}
```

### 6.4 统一 `localized_content` 数据模型

```json
{
  "content_type": "baseline_summary",
  "record_id": "log_2026_09_10_a1b2",
  "locale": "zh-CN",
  "payload": { },
  "input_hash": "sha256:…",
  "schema_version": 1,
  "generated_at": "2026-09-10T12:00:00Z",
  "source": "ai"
}
```

`locale: "und"` 的存量数据以 `source: "legacy"` 标记。

### 6.5 fallback 原则（必须写进代码约定）

| 内容类型 | fallback 规则 |
| --- | --- |
| `static_ui` | 缺失 key 可回退 `en`，**但测试必须报告**（当前 `i18n.load_locale` 静默回退，需加告警） |
| `stable_enum` | 可回退 `en` 显示；当前行为是回退**原文**，应改为回退 `en` 显示名 |
| `ai_generated_text` | **禁止**跨语言隐式回退；语言不符时提示"需重新生成" |
| `user_generated_text` | 保持原文，永不翻译 |
| 报告 | 仅使用 `report_locale`，与 `ui_locale` 无关 |
| `numeric_or_date` | 使用统一的 locale-aware formatter，无回退概念 |

> **不通过切换 `ui_locale` 来迁就内容语言**——这是当前架构最容易踩的坑。

---

## 7. 语言上线批次评估

### 7.1 第一批：`en`、`zh-CN`、`es`、`pt-BR`、`fr`、`sw`

| locale | 目标地区 | 日期/数字格式 | 文本长度风险 | 字体要求 | AI 输出能力风险 | 术语审校要求 |
| --- | --- | --- | --- | --- | --- | --- |
| `en` | 全球基准 | `MM/DD/YYYY`、`1,234.56` | 低（基准） | 系统默认 | 低 | 低 |
| `zh-CN` | 中国大陆 | `YYYY-MM-DD`、`1,234.56` | **低**（CJK 最紧凑，短 ~30%） | 需 CJK 字体（`Noto Sans SC`），避免 tofu | 低 | 高（施工安全术语） |
| `es` | 西班牙 / 拉美 | `DD/MM/YYYY`、`1.234,56` | **高**（+15~25%） | 拉丁扩展足够 | 低 | 高 |
| `pt-BR` | 巴西 | `DD/MM/YYYY`、`1.234,56` | **高**（+15~25%） | 拉丁扩展足够 | 中 | 高 |
| `fr` | 法国 / 西非 | `DD/MM/YYYY`、`1 234,56`（窄空格） | **最高**（+20~30%） | 拉丁扩展足够 | 低 | 高 |
| `sw` | 东非（肯尼亚/坦桑尼亚） | `DD/MM/YYYY`、`1,234.56` | 中（+10~20%） | 拉丁扩展足够 | **中高**（低资源语言，术语一致性差） | **最高**（需母语审校 + 术语表） |

要点：

- `es`/`pt-BR`/`fr` 的按钮与标签需预留 **1.4× 宽度**；
- `fr` 需注意不换行空格（`&nbsp;`）在数字与标点前的处理；
- `sw` 建议先做**术语表冻结**再放开 UI；
- 第一批**不涉及 RTL**，风险最低。

### 7.2 第二批：`de`、`ar`、`hi`、`ja`、`ko`

| locale | 重点风险 |
| --- | --- |
| `de` | **长复合词**（如 `Arbeitssicherheitsunterweisung`）导致按钮/表头溢出、连字符断词需显式启用 |
| `ar` | **RTL 全链路**：`direction: rtl`、镜像 padding/icon、`bidi` 混排数字与英文术语 |
| `hi` | Devanagari 字体（`Noto Sans Devanagari`）、字形簇（字素簇）长度 ≠ 字符数、换行规则不同 |
| `ja` | CJK 换行（禁则处理）、`ja` 与 `zh-CN` **字形差异**（同一 Unicode 码位写法不同，需 `lang` 属性区分字体） |
| `ko` | Hangul 换行、`ko` 字体与 CJK 回退顺序 |

**RTL 的硬性约束（`ar`）**：

1. **不能用一条全局 CSS `direction: rtl` 翻转整个应用**；
2. **数字、公式、地图、图表坐标轴必须保持 LTR**（风险分数、温度、经纬度、ECharts 坐标轴）；
3. 图标语义需镜像（"返回箭头"），但品牌 logo、时钟、地图图钉**不镜像**；
4. `st.dataframe` / `st.metric` / 图表库需逐项验证；
5. 中文与阿拉伯语混排时必须显式 `dir` 与 `lang` 属性，否则出现标点错位。

---

## 8. 修订后的实施路线图

> 原"阶段 2：迁移 `pages/*`"**已删除**——`pages/*` 不可达，迁移无意义。
> 旧代码的处理移至阶段 9（归档与删除），本阶段不删除任何文件。

### 阶段 2：locale registry + BCP 47 + 用户语言偏好持久化 —— ✅ 已实施并通过浏览器验收（verified）

> 实施与验收结果见文首「阶段 2 实施更新」（含 S2.5 真实浏览器验收，41/41 通过）。以下为原计划，保留备查。

- **修改文件**：`utils/ui_language_state.py`、`i18n.py`、`locales/`（新增 manifest）。
- **数据迁移**：`ui_language` 偏好从 session/`localStorage` 迁移到 manifest code，保留 `"zh-CN"` 等已有值（兼容）。
- **回滚**：保留 `SUPPORTED_LANGUAGES` 兼容层，可一键回退到两语言。
- **自动化测试**：`test_ui_language_state.py` 扩展；新增 manifest schema 校验（必填字段、`fallback` 必须存在、`direction ∈ {ltr,rtl}`）。
- **浏览器验收**：语言切换后刷新不丢失；未支持语言代码回退 `en`。
- **付费 AI**：**否**。

### 阶段 3：统一 `content_locale` 与 `localized_content` 协议 —— ✅ 已实施（verified）

> 实施与验收结果见文首「阶段 3 实施更新」。以下为原计划，保留备查。

- **修改文件**：新增 `utils/localized_content.py`；`i18n.py`；`utils/baseline_display.py`、`utils/log_display.py` 接入。
- **数据迁移**：存量 27 项确定性文本**零成本重渲染**；73 项 AI 文本标记 `locale: "und"`。
- **回滚**：新旧读取路径并存（`read_localized()` 兼容旧字段）。
- **自动化测试**：`input_hash` 一致性；`locale: "und"` 不得被当作当前 `ui_locale` 直出。
- **浏览器验收**：切换语言后 Baseline 链/影响/措施立即变化；AI 文本显示"需重新生成"提示。
- **付费 AI**：**否**（本阶段只做协议与确定性文本）。

### 阶段 4：Current Risk / Vision / Report 接入统一协议

#### 阶段 4A：Current Risk —— ✅ 已实施（verified）

> 实施与验收结果见文首「阶段 4A 实施更新」。以下为原计划，保留备查。
> 实际完成范围：`ai_explain_current_risk`、`agents/risk_agent.py:131` 移除 `"Use English."`、
> Current Risk 会话缓存 key 含 project id / locale / input hash、`localized_content` 存取与四种状态。
> **实测付费 AI 成本为 0**：验收与回归全部使用 mock AI。

#### 阶段 4B：Vision Risk —— ✅ 已实施并通过浏览器验收（verified）

> 实施与验收结果见文首「阶段 4B 实施更新」（自动测试 **274/274** + 浏览器验收 **62/62**）。
> 实际完成范围：`build_vision_prompt()` 只产出语言无关 canonical detection、
> `utils/vision_display.py` 契约层、`store_vision_text()` 写 `localized_content`、
> Vision 会话缓存按 project / image / locale / input hash 隔离、五种显示状态。
> **实测付费 AI 成本为 0**：验收与回归全部使用 mock 模型（视觉 2 次 / 文本 3 次）。
> 本阶段**不含** Report 生成；Report 属阶段 4C。

#### 阶段 4C：Report 生成 —— ⏳ 未开始（not started）

> 以下为原「阶段 4B」计划中属于 Report 的部分，保留备查。

- **修改文件**：`app.py`（`render_risk_report_center`、`export_report_to_word`）。
- **数据迁移**：无需迁移（该页面当前未持久化）。
- **回滚**：保留原 prompt 常量，通过 flag 切换。
- **自动化测试**：新增"prompt 必须包含目标语言指令"的断言测试；`session_state` key 必须含 project id。
- **浏览器验收**：`report_locale=zh-CN` 时报告为中文；切项目不串扰。
- **付费 AI**：**是**（需真实调用验证多语言输出，建议限制为 3 种语言 × 各 1 次）。

### 阶段 5：第一批六种 locale 文件与术语表

- **修改文件**：`locales/{es,pt-BR,fr,sw}.json`、新增 `docs/glossary.md`。
- **数据迁移**：无。
- **回滚**：新增语言 `status: "experimental"`，可整体下线。
- **自动化测试**：`python3 scripts/i18n_audit.py --scope runtime --errors-only` 作为硬门禁（483 key 全量对齐）。
- **浏览器验收**：六语言逐页走查，重点 `fr` 溢出。
- **付费 AI**：**否**（术语表可人工 + 免费模型辅助）。

### 阶段 6：日期、数字、单位和时区本地化

- **修改文件**：新增 `utils/formatting.py`；`app.py` 天气/高程/日志/导出 全部格式化点；`requirements.txt` 加 `Babel`。
- **数据迁移**：无（纯展示层）。
- **回滚**：`formatting.py` 保留 `legacy` 函数。
- **自动化测试**：`test_formatting.py`，覆盖 `en/zh-CN/de/fr/ar` 的日期、千位分隔、温度、降水、风速、高程、百分比。
- **浏览器验收**：切换语言后单位与小数点符号同步变化。
- **付费 AI**：**否**。

### 阶段 7：RTL 与第二批语言

- **修改文件**：`locales/{de,ar,hi,ja,ko}.json`、`app.py` 布局与图表配置、CSS/主题。
- **数据迁移**：无。
- **回滚**：`direction` 由 manifest 驱动，可强制 `ltr` 回退。
- **自动化测试**：RTL 快照测试；数字/坐标轴 `dir=ltr` 断言。
- **浏览器验收**：`ar` 下地图、图表、表格、docx 导出逐项走查。
- **付费 AI**：**是**（`ar`/`hi`/`ja`/`ko` 的 AI 输出质量抽样，每种语言 3-5 次）。

### 阶段 8：CI 质量门禁和浏览器测试矩阵

- **修改文件**：CI 配置、`scripts/i18n_audit.py`（阈值参数）、测试矩阵。
- **数据迁移**：无。
- **回滚**：门禁可临时降级为 `--report-only`。
- **自动化测试**：`--errors-only` 硬门禁 + `--scope runtime` WARNING 阈值 + Playwright 多语言矩阵。
- **浏览器验收**：多语言矩阵自动截图对比。
- **付费 AI**：**否**（Mock AI 响应）。

### 阶段 9：旧代码归档与数据迁移工具

- **修改文件**：`pages/*`、`app_before_ui2.py`、`backup-before-teammate-update/`、`utils/storage.py`、`utils/weather.py`、4 个未使用 Agent → 移入 `archive/` 或删除（**需用户明确批准**）。
- **数据迁移**：`data/projects.json` 的 `locale: "und"` → 按需重新生成 AI 内容（用户触发）。
- **回滚**：归档而非删除，可 `git revert` 恢复。
- **自动化测试**：归档后 `--scope legacy` 应报 0 文件；runtime 门禁保持绿。
- **浏览器验收**：应用功能无回归。
- **付费 AI**：**是**（仅当用户选择"重新生成历史内容"时按需触发）。

---

## 9. 已知缺陷清单

| # | 缺陷 | 位置 | 影响 |
| --- | --- | --- | --- |
| D1 | ✅ **已修复（阶段 4A）** Current Risk AI 解释曾恒为英文：语言规则构造后未随请求发送，`agents/risk_agent.py` 硬编码 `"Use English."`。现由 `build_current_risk_text_prompt(context, target_locale)` 生成目标语言 prompt，并把完整 prompt 交给 `RiskAgent` | `app.py:1797`、`utils/current_risk_display.py:533`、`agents/risk_agent.py` | 中文界面下展示英文 AI 解释 |
| D2 | ✅ **已修复（阶段 4B）** Vision 曾：视觉 prompt 全中文硬编码、识别事实与自然语言混在一起。现 `build_vision_prompt()` 只产出语言无关 canonical detection，人类可读文本由 `build_vision_text_prompt(..., target_locale)` 按目标内容语言单独生成 | `vision_risk.py:334`、`utils/vision_display.py:1178`、`vision_risk.py:1554` | 英文界面下展示中文风险描述 |
| D3 | Word 导出 7 处英文标签不随 `report_locale` 变化（**阶段 4A 未处理**） | `app.py:3828-3870` | 导出报告语言不一致 |
| D4 | `session_state` 缓存键不含 project id（**Current Risk 与 Vision 均已修复**；仅 `risk_report_result` 待阶段 4C） | `CURRENT_RISK_SESSION_CACHE_KEY` `app.py:532` 含 project id + locale + input hash；Vision 检测键含 project id + image hash（`utils/vision_display.py:1272`）、Vision 文本键含 project id + record id + locale + input hash（`utils/vision_display.py:1293`）；`risk_report_result` 未改 | 切换项目后串扰上一项目结果 |
| D5 | 死 key `dashboard.info_log_language` | `locales/en.json`、`locales/zh-CN.json` | 无功能影响（清理项） |
| D6 | 存量 AI 内容无 `language` 元数据（12 项目 / 22 日志 / 73 处自由文本） | `data/projects.json` | 无法判定已存文本语言 |
| D7 | 无 locale-aware 日期/数字/单位格式化 | 全 runtime | `fr`/`de`/`ar` 上线前必须解决 |
| D8 | 不可达死模块 `utils/storage.py`（含 AI 调用与写盘） | `utils/storage.py` | 维护成本、误用风险 |
| D9 | `i18n.load_locale` 静默回退 `en` | `i18n.py` | 缺 key 不告警，难以发现漏翻 |

---

## 10. 验证结果

| 命令 | 退出码 | 结果 |
| --- | --- | --- |
| `python3 -m py_compile scripts/i18n_audit.py test_i18n_audit.py` | 0 | 编译通过 |
| `python3 test_i18n_audit.py` | 0 | **96/96 通过** |
| `python3 scripts/i18n_audit.py --scope runtime --report-only` | 0 | ERROR 0 / WARNING 17 / INFO 79 |
| `python3 scripts/i18n_audit.py --scope runtime` | **1** | runtime 17 WARNING 阻断（与 legacy 无关） |
| `python3 scripts/i18n_audit.py --scope legacy --report-only` | 0 | legacy 372 WARNING，**不阻断** |
| `python3 scripts/i18n_audit.py --scope all --json --report-only` | 0 | JSON 可解析，每条 finding 含 `scope`/`reachable` |

作用域语义测试（fixture 验证，非断言式声明）：

- legacy 页面有硬编码 → runtime 严格模式**不被阻断**（返回 0）✅
- `--scope legacy` 能报告这些候选（`reachable=False`）✅
- `--scope all` 分组显示 RUNTIME / LEGACY 两段 ✅
- runtime 文件加硬编码 → 严格模式**被阻断**（返回 1）✅

---

## 11. 工具能力与已知限制

**能力**：可达性分析（AST import 图）、三作用域报告、locale key 集合、跨语言与调用级占位符一致性、
`t()` 三类引用识别、多通道硬编码检测（`st.*` + docx `add_run`）、
严格 / errors-only / report-only 退出码策略、文本与 JSON 双输出。

**限制**：

- 文案出口仅覆盖 `st.*` 与 `add_run`；`components.html()` 的 SVG/HTML 内文字、
  docx 正文（`add_paragraph`）未覆盖；
- 动态拼接 key（10 处）只能告警，无法校验；
- 可达性基于静态 import 图，**不做运行时探针**；动态 `importlib` 加载会漏判（本仓库无此用法）；
- `--scope runtime` 的 WARNING 门槛含"无法静态校验的动态 key"，属审计盲区而非缺陷，
  阶段 3 静态化后应归零。

---

## 12. 阶段 1（修订）产出物

| 文件 | 说明 |
| --- | --- |
| `scripts/i18n_audit.py` | 审计脚本（重写：可达性分析 + `--scope` + 多通道硬编码） |
| `test_i18n_audit.py` | 审计脚本测试（重写，96/96 通过） |
| `docs/i18n-audit.md` | 本报告 |

**未修改**：`app.py`、`i18n.py`、`vision_risk.py`、`locales/*`、`data/projects.json`、
`utils/*`、`pages/*`、`agents/*`、`prompts/*` 及全部业务与风险公式。
