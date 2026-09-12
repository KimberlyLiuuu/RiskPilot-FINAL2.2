# RiskPilot `localized_content` 数据协议契约（全球化阶段 3）

- 状态：**implemented**（阶段 3 交付物）
- 关联报告：`docs/i18n-audit.md`（第 6.4 / 6.5 节为协议雏形）
- 实现：`utils/localized_content.py`（纯数据层）、`utils/content_locale.py`（语言轴）
- 测试：`test_localized_content.py`（409 项）、`test_content_locale.py`（104 项）
- 本阶段**不迁移数据、不调用 AI、不改动 Current Risk / Vision / Report 的业务路径**

---

## 1. 目的

在此之前，多语言动态内容散落在两处、且语言信息不一致：

| 位置 | 问题 |
| --- | --- |
| `project["baseline_risk"]` / `daily_logs[].ai_analysis` | 文本**没有语言元数据**，无法判断已有文本是什么语言 |
| `project["ai_text_by_language"][lang][section][record_id]` | 有语言隔离，但**没有 schema、没有版本、没有输入指纹** |

阶段 3 建立**唯一的动态内容协议**，解决三件事：

1. **语言可判定**：每条内容显式携带 `locale`，无元数据的旧数据一律记为 `und`。
2. **不会串语言**：读取严格按 `content_type + record_id + locale` 精确匹配，**禁止跨语言回退**。
3. **可判定"是否过期"**：用 `input_hash` 判断生成该内容时的输入是否已变化。

---

## 2. 三条独立语言轴

| 轴 | 存储键 | 语义 | 本阶段状态 |
| --- | --- | --- | --- |
| 界面语言 `ui_locale` | `st.session_state["ui_lang"]` | 产品界面文案 | 已有（阶段 2），**写入点仍只有语言选择器回调一处** |
| 内容语言 `content_locale` | `st.session_state["content_locale"]`（可选） | AI / 动态内容语言 | **本阶段建立**，默认由 `ui_locale` **推导** |
| 报告语言 `report_locale` | `st.session_state["risk_report_language"]` | 导出报告语言 | 已有，与上面两轴完全独立 |

关键设计（`utils/content_locale.py`）：

- **不做镜像**：`ui_lang` 变化时**不会**写入 / 覆盖 `content_locale`。内容语言是**推导**出来的
  （`effective_content_locale()`），因此不存在"两处状态不同步"的问题。
- 只有显式调用 `set_content_locale()` 才会创建 override，且**只写 `content_locale` 这一个键**。
- 非法 / planned（`es`、`fr`、`de`…）/ 非字符串取值一律**拒绝**，不写任何键。

```python
from utils.content_locale import get_content_locale, set_content_locale

state = {"ui_lang": "zh-CN"}

get_content_locale(state)          # -> "zh-CN"（推导，state 不变）
set_content_locale(state, "en")    # -> "en"，仅新增 state["content_locale"]
state["ui_lang"]                   # 仍是 "zh-CN"
```

---

## 3. 存储布局

```
project
└── localized_content                     # 新协议（阶段 3）
    └── <content_type>                    # 规范化后的白名单值
        └── <record_id>                   # 单例固定为 "project"
            └── <locale>                  # BCP 47 code 或 "und"
                └── <record>              # 见第 4 节

project
└── ai_text_by_language                   # 旧 archive（保留，只读兼容 + 继续双写）
    └── <locale>
        └── <section>                     # "baseline" / "logs" / "log"
            ├── <payload>                 # 单例
            └── <record_id> -> <payload>  # 多记录（日志）
```

- `LOCALIZED_CONTENT_KEY = "localized_content"`
- `LEGACY_ARCHIVE_KEY = "ai_text_by_language"`
- **双写**：`utils/baseline_display.ai_text_store()` 同时写新协议与旧 archive，
  因此任何仍读旧结构的代码行为不变（阶段 3 的兼容基础）。

---

## 4. Record schema

```json
{
  "schema_version": 1,
  "content_type": "baseline",
  "record_id": "project",
  "locale": "zh-CN",
  "payload": { "risk_chains": [], "impacts": [], "actions": [] },
  "input_hash": "9f2c…（sha256 hex，可为 null）",
  "generated_at": "2026-09-11T01:20:33.512340+00:00",
  "source": "ai_generated"
}
```

| 字段 | 类型 | 必填 | 规则 |
| --- | --- | --- | --- |
| `schema_version` | int | ✅ | 必须是 `1`（`int`，非 `bool`）；`> 1` 视为"不支持的版本"并被拒绝 |
| `content_type` | str | ✅ | 必须是**规范化后的**白名单值（别名先归一化） |
| `record_id` | str | ✅ | 单例固定 `"project"`；多记录为安全 id；字符集 `[A-Za-z0-9._-]` 且至少含一个字母或数字 |
| `locale` | str | ✅ | 已注册 locale（含 planned）或 `"und"`；必须是 `str`（**列表 / 数字一律拒绝**） |
| `payload` | dict | ✅ | 见第 8 节限制 |
| `input_hash` | str \| null | ✅（可为 `null`） | 若存在必须是 64 位小写 sha256 hex |
| `generated_at` | str | ✅ | ISO 8601 且**必须带时区**（无时区一律拒绝） |
| `source` | str | ✅ | 见下表 |

`source` 取值：

| 值 | 含义 |
| --- | --- |
| `ai_generated` | 当前 AI 生成（**默认**） |
| `deterministic` | 由闭集模板确定性产生 |
| `legacy` | 从旧 archive 转换而来 |
| `migrated` | 迁移工具写入 |
| `translated` | 由另一语言翻译而来 |
| `user_authored` | 用户输入 |

> 生产代码请使用 `make_localized_record()` 构造 record，不要手写 dict——
> 它会做规范化 + 校验，非法输入返回 `None` 而**不抛异常**。

---

## 5. `content_type` 白名单

| 规范值 | 单例 | 别名（读取时归一化） | payload 文本字段（阶段 4 契约） |
| --- | --- | --- | --- |
| `baseline` | ✅ | `baseline_risk`、`baseline` | `risk_chains`、`impacts`、`actions` |
| `logs` | ❌ | `log`、`daily_log`、`daily_logs` | `summary`；嵌套 `title`/`reason`/`suggestion` |
| `current_risk_explanation` | ✅ | `current_risk` | `summary`、`risk_level_text`、`reasons`、`recommendations` |
| `vision_analysis` | ✅ | `vision`、`vision_risks` | `summary`；嵌套 `title`/`reason`/`recommendation` |
| `risk_report` | ✅ | `report` | `title`、`summary`、`body` |

```python
normalize_content_type("log")        # -> "logs"
normalize_content_type("  LOG  ")    # -> "logs"（大小写 / 空白容错）
normalize_content_type("system")     # -> None（不在白名单）
```

> **阶段 3 只定义 `current_risk_explanation` / `vision_analysis` / `risk_report` 的契约，
> 不改造这三个页面的业务调用**（阶段 4 完成接线）。
> 截至阶段 4B：`current_risk_explanation` 已接线（阶段 4A，verified）、
> `vision_analysis` 已接线（阶段 4B，verified，见 5.2 的 record id 说明）、
> `risk_report` **尚未接线**（阶段 4C，not started）。

### 5.1 `record_id` 规则与安全

| 规则 | 说明 |
| --- | --- |
| 允许字符 | `A-Za-z0-9._:-`，长度 1–128 |
| 必须含字母或数字 | 防止 `..`、`...`、`___`、` ` 之类纯标点 |
| 拒绝路径穿越 | `../etc`、`a/../b`、`a\b`、`/root`、`C:\x` |
| 拒绝控制字符 | `\x00`、`\x1f`、ANSI 转义 |
| 单例固定 | `baseline` / `current_risk_explanation` / `risk_report` 恒为 `"project"`；`vision_analysis` 协议默认值同样是 `"project"`，但**阶段 4B 接线时显式传入 per-(project, image) 的 record id**（见 5.2） |

> **`:` 是合法字符**（对应代码 `_SAFE_RECORD_ID_RE = re.compile(r"^[A-Za-z0-9_.:\-]+$")`），
> 用于命名空间式 id，例如 `report::manager::risk_report`。
> `:` 之外仍**一律禁止**：`/`、`\`、`..` 路径片段、控制字符、空值与超长值。
>
> ⚠ `record_id` 只是**结构化存储键**（`localized_content[content_type][record_id][locale]`），
> **不得**直接当作未经清理的文件路径使用；将来若要落盘成文件名，必须另行映射 / 清洗。

```python
resolve_record_id("baseline", None)     # -> "project"
resolve_record_id("logs", "log-0001")   # -> "log-0001"
resolve_record_id("logs", None)         # -> None（多记录必须显式给 id）
normalize_record_id("../etc/passwd")    # -> None
```

### 5.2 阶段 4B 实际接线：Vision 的 record id（契约同步）

阶段 4B 已把 `vision_analysis` 接入本协议，实际落库形状与第 5 节的**单例默认值不同**：

| 项 | 实际实现 |
| --- | --- |
| `record_id` | `vis-<sha256(project_id \| image_hash \| detection_schema_v1)[:32]>`（`utils/vision_display.vision_record_id()`）——**每（项目, 图片）一条记录**，不是 `"project"` 单例 |
| 存储路径 | `project["localized_content"]["vision_analysis"][<record_id>][<locale>]` |
| `payload` | `{"summary": str, "findings": [{"finding_id", "title", "reason", "recommendation"}]}`（与第 5 节一致） |
| `input_hash` | 只由识别事实构成（finding id / 本体键 / 等级 / 惩罚 / 坐标 / 置信度 + 总体等级 + 惩罚总分），**不含** locale、UI 状态、报告语言与时间戳 |
| 隔离效果 | 相同文件名、不同图片 bytes 必然得到不同 `record_id`；同一图片在不同项目下互不可见 |
| 识别事实 | 语言无关 canonical detection 存 `project["vision_detection"]`，**不进** `localized_content` |

> 因此"单例固定为 `project`"只适用于未显式传 `record_id` 的调用；显式传入安全 id 的
> 多记录用法（日志、Vision 图片）仍受 5.1 的字符集与安全规则约束。
> 该路径**未迁移历史数据**：无 `vision_detection` 的旧项目按 `legacy_compatible` 只读显示旧
> `visual_risks`，不自动迁移。

---

## 6. `locale` 规则

| 情况 | 结果 |
| --- | --- |
| 已注册且 `ui_enabled` 且非 planned（`en`、`zh-CN`） | ✅ 允许，可作为 `content_locale` |
| 已注册但 `planned`（`es`、`pt-BR`、`fr`、`sw`、`de`、`ar`、`hi`、`ja`、`ko`） | ⚠️ **record 可以保存**（可能有预生成内容），但**不能成为 `content_locale`** |
| `und` | ✅ **专用于"语言未知"的存量数据** |
| 未注册（`xx-YY`）、非法、非字符串 | ❌ 拒绝 |

**`und` 的语义（重要）**

- 存量数据（12 个项目 / 22 条日志 / 73 处 AI 自由文本）**完全没有语言元数据**，
  一律按 `und` 处理，**绝不隐式当作当前 `ui_locale`**。
- UI 应显示"该内容语言未知"，而**不是**把可能的外语文本直出。
- `"und"` 也不等于 `en`：`is_content_fresh(und_record, hash, "en")` 为 `False`。

---

## 7. 读取顺序与纯度契约

`utils/baseline_display.ai_text_lookup()` 是 Baseline / Daily Logs 的**统一读取入口**，顺序为
**首次命中即返回**：

1. `project["localized_content"]` —— 精确 `content_type` + `record_id` + `locale` 匹配；
2. `project["ai_text_by_language"]` —— 旧 archive。

纯度保证（由 AST 静态守卫与运行时测试共同保证）：

| 契约 | 说明 |
| --- | --- |
| 读取不写 `project` | `localized_content_get` / `..._status` / `..._languages` / `legacy_*` 全部只读 |
| 读取不创建容器 | 读不到时不会凭空建出 `localized_content` |
| 读取不迁移 | 旧 archive 只读，**不做任何自动迁移** |
| 写时复制 | `localized_content_put(..., copy_on_write=True)` 返回新 dict，输入不变 |
| 就地写入 | `copy_on_write=False` 供 `app.py` 的 `ai_text_store` 保持"就地改 + `persist()`"行为 |
| 不落盘 | 模块内**没有** `open()` / `json.dump()` / `persist()`，写盘仍由 `app.py` 负责 |
| 不切语言 | 不读 `ui_lang`、不写 `ui_lang`、不调用 AI |

**写入隔离**：`_path_copy()` 逐层复制
`project → localized_content → content_type → record_id` 四个容器，
因此"写 `zh-CN` 不改变 `en`、写记录 A 不改变记录 B、写项目 A 不改变项目 B"是
**结构上**成立的，而不是靠调用方自觉。

---

## 8. payload 限制

| 限制 | 值 | 说明 |
| --- | --- | --- |
| 必须为 `dict` | — | 其它类型一律拒绝 |
| 最大嵌套深度 | 8 | 超深直接拒绝 |
| 单 dict 最大键数 | 200 | |
| 单 list 最大元素数 | 500 | |
| 单字符串最大长度 | 20000 | |
| 键名最大长度 | 128 | |
| 最大节点总数 | 5000 | 防"宽而浅"的膨胀 |
| 最大序列化字节 | 200000 | `json.dumps(..., sort_keys=True)` 后 |

**类型白名单（JSON 子集）**：`str` / `int` / `float`（有限）/ `bool` / `None` / `dict`（键必须是 `str`）/ `list`。

**明确拒绝**并把原因写入问题列表：

- `bytes`、`bytearray`、`set`、`frozenset`、`tuple`、`complex`、对象、类、函数
- `NaN` / `Infinity` / `-Infinity`
- 非字符串键（`1`、`b"k"`）
- 自引用结构（不会递归爆栈，而是被安全拒绝）

### 8.1 危险键（原型污染防护）

`UNSAFE_KEYS` 在**任意深度**都会被拒绝：
`__proto__`、`constructor`、`prototype`、`__class__`、`__globals__`、`__subclasses__`、
`__reduce__`、`__mro__`、`__bases__`、`__dict__`、`__getattribute__`、`__init__` 等。

---

## 9. `input_hash` 与 stale 语义

`compute_input_hash(payload)` 使用 **`json.dumps(..., sort_keys=True, separators=(",", ":"))` + sha256**：

- 键顺序不影响结果（dict 键顺序不同的等价输入得到同一 hash）；
- 非法 / 超限 payload 返回 `None`。

`is_content_fresh(record, input_hash, locale=None)`：

| 条件 | 结果 |
| --- | --- |
| `record["input_hash"]` 为 `null`（旧数据） | **永远 `False`**（`input_hash_missing`） |
| `input_hash` 与 `record["input_hash"]` 不同 | `False`（`input_hash_mismatch`） |
| 传入 `locale` 且与 `record["locale"]` 不同 | `False`（`locale_mismatch`） |
| 两者相同 | `True` |

> **hash 不包含 locale**，因此判断"这份内容能否直接显示"必须**同时**校验 locale，
> 否则会出现"英文内容被当成中文内容的最新版本"。

---

## 10. 状态矩阵（`localized_content_status`）

返回 dict：`status` / `reason` / `content_type` / `record_id` / `locale` /
`saved_locales` / `source` / `needs_generation` / `is_stale`。

| `status` | 触发条件 | `needs_generation` | `record` |
| --- | --- | --- | --- |
| `available` | 新协议 record 存在、校验通过、payload 非空、hash 与 locale 均匹配 | `False` | 该 record |
| `stale` | record 存在且非空，但 `input_hash` 缺失或不匹配 | `False`（内容仍在，只是需要刷新） | 该 record |
| `legacy_compatible` | 无新协议 record，但旧 archive 有目标语言内容 | `False` | — |
| `deterministic` | record 的 `source == "deterministic"` | `False` | 该 record |
| `missing` | 目标语言无内容（`reason` 区分"存在别的语言 `no_record_for_locale`"与"完全没有内容 `no_content`"） | `True` | `None` |
| `invalid` | `content_type` 未知 / `record_id` 不安全 / record 校验失败 | `True` | `None` |

`reason` 常量：`no_content`、`no_record_for_locale`、`empty_payload`、
`input_hash_missing`、`input_hash_mismatch`、`locale_mismatch`、
`invalid_record`、`unsupported_schema_version`、`unknown_content_type`、`unsafe_record_id`。

**空 payload ≠ 存在内容**：`{"risk_chains": ["   "], "actions": []}` 会被判为 `missing`
（`empty_payload`），不会伪装成已有内容，也不会把纯空白写进 `saved_locales`。

---

## 11. 旧数据兼容（只读，不迁移）

| 函数 | 作用 |
| --- | --- |
| `legacy_ai_text_get(project, section, locale, record_id=None)` | 只读读取旧 archive；`log` / `logs` 互为安全网（`logs` 优先） |
| `legacy_content_types(project)` | 旧 archive 里出现过的规范化 `content_type` |
| `legacy_content_locales(project, content_type)` | 旧 archive 里出现过的语言；**未注册写法归为 `und`** |
| `legacy_ai_text_to_record(...)` | 把一条旧内容**纯函数式**转换成 record（不写回） |
| `localize_legacy_project(project, ...)` | 显式迁移工具，返回**新** project；**本阶段无任何运行时调用** |

兼容规则：

1. **不迁移、不写回**：读旧数据绝不修改 `project`，也绝不创建 `localized_content`。
2. **不跨语言回退**：只读目标语言的桶，不做"取不到就换一种语言"。
3. **`und` 而非猜测**：旧 key 若是未注册写法（如 `xx-YY`），归为 `und`，
   **绝不**继承当前界面语言、也不按"英文居多"启发式归属。
4. **转换出的 record `input_hash = null`**，因此其状态必然是 `stale`——
   旧内容**永远**不会被伪装成"最新"。

---

## 12. 阶段 4 契约（本阶段只定义，不接线）

`validate_translatable_payload(content_type, payload)` 在 `validate_payload` 之上，
额外拒绝把**数值 / 非文本字段**放进可翻译载荷，且**任意深度**都拒绝：

```
penalty  score  level  confidence  coordinates  geometry  bbox  box
severity  likelihood  exposure  risk_score  risk_level  raw_risk
risk_count  visual_risk_penalty
```

原因：这些字段参与风险计算与视觉检测。一旦允许 AI 翻译结果回写，翻译措辞的
变化就可能改变分数或几何数据。**可翻译载荷只能承载人类可读文本。**

---

## 13. 本阶段明确"不做"的事

| 不做 | 说明 |
| --- | --- |
| 不迁移 `data/projects.json` | 73 处 AI 自由文本仍需重新生成，属阶段 4/5；`sha256` 保持不变 |
| 不接线 Current Risk / Vision / Report | 只定义契约；`pages/*`、`vision_risk.py`、`agents/risk_agent.py` 未引用本协议 |
| 不调用真实 AI | 模块内无任何 AI / 网络依赖 |
| 不新增 `es`/`fr` 等翻译文件 | 只登记元数据（阶段 2 既定策略） |
| 不改 `ui_lang` 写入点 | `app.py` 仍是**唯一 1 处** `set_current_language()`（选择器回调） |
| 不改风险公式与数值字段 | 静态守卫 + 数值字段隔离测试 |

---

## 14. 验证

```bash
python3 -m py_compile utils/localized_content.py utils/content_locale.py \
    utils/baseline_display.py utils/log_display.py

python3 test_content_locale.py                # 104/104 语言轴
python3 test_localized_content.py             # 409/409 数据协议
python3 test_localized_baseline_display.py    # 121/121 Baseline 显示层
python3 test_localized_log_display.py         # 249/249 Daily Log 显示层
python3 test_ui_language_state.py             # 333/333 三条语言轴集成
python3 test_locale_registry.py               # 273/273 registry
python3 test_i18n_audit.py                    # 125/125 审计脚本（含阶段 3 可达性守卫）
python3 scripts/i18n_audit.py --scope runtime --report-only   # ERROR 0 / WARNING 17 / INFO 79
python3 scripts/i18n_audit.py --scope legacy                  # 退出码 0（legacy 不阻断）
.venv/bin/python test_helpers.py              # 69/69（需 requests/streamlit 环境）

shasum -a 256 data/projects.json
# 07befa9161480f023f14794babc52754600be4c309078a26c9e41dffa4d92735  （44773 字节，未变化）
```

静态守卫覆盖：不导入 `streamlit` / `i18n` / `app` / `pickle` / AI 依赖、
无 `st.*` 与 `session_state` 访问、不落盘、不写 `ui_lang`、
`app.py` 不直接调用本协议、`pages/*` 未接线。

---

## 15. Report（导出报告）契约 —— 阶段 4C-2

阶段 4C-2 新增纯数据模块 `utils/report_contract.py`，
只定义 Report 的**语言标识**、**稳定内部 ID**、**可翻译文本 payload** 与
**record_id**，**不接 UI、不改报告生成链路**（`app.py` 仍无 `risk_report` 契约引用）。

### 15.1 两个必须区分的数据对象

| 对象 | 内容 | 本阶段状态 |
| --- | --- | --- |
| **A. canonical report facts** | 语言无关：项目标识与元数据、canonical risk level/score、Current Risk 的 `components`/`weights`、Baseline/Weather/Logs/Vision 数值事实、audience ID、report type ID、report locale、template version | 只在 `REPORT_CANONICAL_FACT_FIELDS` **声明**字段名；**不实现** snapshot / hash（延后到 4C-3） |
| **B. localized report text** | 只有 `title` / `opening` / `summary` / `key_points` / `actions` / `closing` | 本阶段唯一校验的 payload（`validate_report_text_payload`） |
| C. 用户编辑后的正文 | 用户在编辑器里改完的整篇文本 | 既不是 A 也不是 B：属单独的 editable/export 状态，**不得**反向覆盖 canonical facts，也**不得**再当作原始 AI payload 使用 |

约束：`REPORT_CANONICAL_FACT_FIELDS` 与 `REPORT_TEXT_FIELDS` **不相交**，
且 `risk_level` / `risk_score` / `components` / `weights` 等数值字段
全部落在**禁止进入 payload** 的清单内。

### 15.2 报告语言（report locale）

* 内部一律使用规范 BCP 47 code；报告语言**允许 planned locale**
  （`ui_enabled=false` ≠ 报告语言不可用）。
* 允许的 11 种（= registry 中已注册且具有明确 `ai_language_name`，`supported_report_locales()`）：

  `en, zh-CN, es, pt-BR, fr, sw, de, ar, hi, ja, ko`

* 旧报告语言**显示值** → BCP 47（`content_locale.report_language_to_locale` 兼容层）：

| 显示值 | code | 显示值 | code |
| --- | --- | --- | --- |
| `中文` / `Chinese` / `简体中文` | `zh-CN` | `Deutsch` | `de` |
| `English` | `en` | `日本語` | `ja` |
| `Español` | `es` | `한국어` | `ko` |
| `Português` / `Português (Brasil)` / `Portuguese (Brazil)` | `pt-BR` | `العربية` / `Arabic` | `ar` |
| `Français` | `fr` | `हिन्दी` | `hi` |
| `Swahili` / `Kiswahili` | `sw` | （其余未列出者一律拒绝） | `und` |

* **一律拒绝**（`valid=False` + `locale="und"`，绝不猜成别的语言）：
  `Local Language`、`Italiano`/`Italian`（`it`）、`Bahasa Melayu`（`ms`）、
  `Bahasa Indonesia`（`id`）、`pt-PT`、`zh-TW`、`zh-Hant`、`zz-ZZ`、
  `None`、空字符串、非字符串。
* **不得**因报告 locale 改写 `ui_lang` 或 `content_locale`。

### 15.3 report audience / report type（稳定内部 ID）

| 维度 | 稳定内部 ID | 兼容的旧显示值 |
| --- | --- | --- |
| audience | `worker` / `manager` / `client` | `Worker` / `Manager` / `Client` |
| report type | `daily_risk_alert` / `risk_report` | `Today's Risk Alert` / `Risk Report` |

内部 ID 一律小写；**翻译后的显示文本不是内部 ID**，会直接被拒绝。
容错范围仅限大小写、首尾/连续空白与撇号写法（`'` / `’`）。

### 15.4 可翻译 payload（目标结构）

```json
{
  "title":      "string",
  "opening":    "string",
  "summary":    "string",
  "key_points": ["string"],
  "actions":    ["string"],
  "closing":    "string"
}
```

* 顶层必须是 `dict`；**key 集合严格**（缺字段 / 额外字段一律 invalid）。
* 所有字符串去首尾空白后不得为空；通过时返回**副本**（入参不被修改）。
* `key_points` / `actions`：`list` of `str`，1–8 项，每项 ≤ 1000 字符；
  `title` ≤ 300 字符；`opening` / `summary` / `closing` 各 ≤ 4000 字符。
* 只接受 `str` —— 因此 `bytes` / `set` / `tuple` / 自定义对象 / `NaN` / `Infinity`
  **结构上**无法通过。
* **禁止字段**（`forbidden_field:<key>`；含大小写变体）：
  `risk_level` `score` `level` `severity` `likelihood` `exposure` `raw_risk`
  `penalty` `confidence` `components` `weights` `risk_score` `risk_count`
  `visual_risk_penalty` `coordinates` `geometry` `bbox` … （基础集合复用
  `localized_content.NON_TRANSLATABLE_FIELDS`）。
* 本阶段**不检查**"文本是否真的是目标语言"——这是后续限制。
* 版本常量：`REPORT_SCHEMA_VERSION = 1`、`REPORT_TEMPLATE_VERSION = 1`
  （本阶段只定义，不接入缓存或 DOCX）。

### 15.5 record_id

* 规则：`report::<audience>::<report_type>`，例如 `report::manager::risk_report`。
* **locale 不进入 id**（`localized_content` 已按 locale 分桶）；
  **project id 不进入 id**（记录本来就属于具体 project）。
* `::` 落在共享 record_id 字符集内：`^[A-Za-z0-9_.:\-]+$`（**含 `:`**，见 §5.1）；
  生成后仍强制再过一遍 `normalize_record_id`，非法即拒绝。
* 拒绝：非法 audience/type、路径穿越、控制字符、首尾空白（会被 `strip` 改写即不安全）。

### 15.6 与现有 `localized_content` risk_report schema 的差异（**已记录，延后处理**）

| 项 | 共享协议现状 | 4C 目标 | 处理 |
| --- | --- | --- | --- |
| 文本字段 | `text_fields = ("title", "summary", "body")` | `title/opening/summary/key_points/actions/closing` | **延后到 4C-3** 调整共享 schema；本阶段不改 |
| 单例 | `singleton = True`（`DEFAULT_RECORD_IDS` 含 report） | 每 `(audience, report_type)` 一份 | **延后到 4C-3** 决策（Vision 在 4B 已有"声明单例 → 实际显式 id"的先例） |
| `planning_status` | `planned`（尚未接线） | 4C-5 接线 | 本阶段保持 `planned` |

明确约定：

1. `report_contract.py` 定义的是**阶段 4C 的目标结构化 payload**；
2. 现有 `localized_content` 的 risk_report schema **尚未接线**（0 条记录）；
3. 共享 schema 的最终调整**延后到 4C-3**；
4. 当前**不得**把目标 payload 写入 `data/projects.json`；
5. **不得**通过兼容代码静默丢失 `opening` / `key_points` / `actions` / `closing`
   （现有 validator 只拒绝数值字段、不限制文本字段集合，因此不会"报错"，
   一旦接线必须显式迁移而不是丢弃字段）。

### 15.7 `content_locale.py` 的最小补充（阶段 4C-2）

为让报告语言轴能直接接受 registry 的显示名，仅补充**含义唯一**的别名
（`locales/manifest.json` 的 `native_name` / `english_name`）：

* 正推：`简体中文` → `zh-CN`、`हिन्दी` → `hi`、`العربية` → `ar`、
  `Português (Brasil)` / `Portuguese (Brazil)` → `pt-BR`、`Arabic` → `ar`
* 反推：补 `ar` → `العربية`（否则 `locale_to_report_language("ar")` 会降级成
  `English`，再正推成 `en` —— 一次静默的语言漂移）

**没有**改动任何模糊映射逻辑（`it` / `ms` / `id` 仍返回各自 code 而非 `en`），
也没有把未知名称猜成某种语言。

### 15.8 本阶段不做的事

| 不做 | 说明 |
| --- | --- |
| 不接线 Report | `app.py` / `agents/risk_agent.py` / `pages/*` / `vision_risk.py` 未引用 `report_contract` |
| 不改共享 schema | `utils/localized_content.py` 未修改（本轮以 `git diff --stat` + 执行前后哈希对照确认，不写进永久测试） |
| 不调用 AI / 不生成 DOCX / 不启动 Streamlit | 纯函数与 AST 守卫 |
| 不修 Current Risk `components` 缺陷、不动 prompt、不动 session cache | 属 4C-3/4C-5 |
| 不迁移历史数据 | `data/projects.json` 未变化 |

验证（阶段 4C-2）：

```bash
.venv/bin/python -m py_compile utils/report_contract.py test_report_contract.py utils/content_locale.py

.venv/bin/python test_report_contract.py     # 407/407 报告契约
.venv/bin/python test_content_locale.py      # 104/104 语言轴（回归）
.venv/bin/python test_localized_content.py   # 412/412 数据协议（回归）

shasum -a 256 data/projects.json
# 07befa9161480f023f14794babc52754600be4c309078a26c9e41dffa4d92735  （未变化）
```

**不冻结业务文件字节哈希（4C-2.1 测试稳定化）**：`test_report_contract.py`
已删除 `STAGE_REPORT_CONTRACT_FROZEN` 以及对 `app.py` / `agents/risk_agent.py` /
`vision_risk.py` / `i18n.py` / `utils/localized_content.py` / `locales/*.json` /
`pages/*.py` / `data/projects.json` 的固定 SHA-256 与"未接线"断言，
且**不替换为新的硬编码哈希**。原因：4C-3 要改共享 schema、4C-5 要接线 `app.py`、
旧代码归档阶段可能删除 `pages/*` —— 固定字节哈希会把**合法修改**误报为回归，
多人协作时也会因他人的合法修改导致无关测试失败；文件变化由 Git 追踪。

导出报告仍适用的约定（阶段 4C，不因本次调整改变）：`report_contract.py` 定义
4C 目标 payload；共享 `risk_report` schema 尚未接线；最终调整延后到 4C-3；
当前不得把目标 payload 写入 `data/projects.json`；且不得静默丢失
`opening` / `key_points` / `actions` / `closing`。

"本阶段未修改某个文件"改为**一次性命令**证明（不进入永久测试）：

```bash
git status --short && git diff --stat
shasum -a 256 data/projects.json    # 执行前后各一次，仅在汇报中比较
```
