# 以 deepseek-harness 为范本的对比审查与优化建议（2026-11-12）

本文件的任务不是「遇到问题去查 DSH」，而是**先了解 deepseek-harness 的整体做法，再反过来审视本仓库**，判断哪些机制值得移植、哪些前提并不成立。

基线：分支 `dev`，提交 `1b6b498`，VERSION `1.1.2`。DSH 侧基线：`/Users/apple/Project/Git/deepseek-harness`，提交 `e8f7b93a34`，版本 `0.2.2-rc.2`。审查只读进行，未连真实实例、未读取凭证、未修改在用业务数据。

## 证据分级

| 标记 | 含义 |
|---|---|
| **【实跑】** | 本轮实际执行命令、脚本或浏览器测量得到 |
| **【源码】** | 阅读源码确认，未构造运行时验证 |
| **【未跑】** | 本轮未验证，不得当作通过 |

DSH 是 TypeScript/pnpm 的 agent harness，本仓库是 Python/FastAPI + Jinja SSR 的办公基座。**两者形态不同**，所以本文只移植「可机检的规则」与「组织方式」，不移植技术栈、组件库或构建链。

## 一、结论摘要

先说最重要的判断：**DSH 最值得抄的不是它的 UI，而是它「把规范变成能让回归失败的检查」的做法**。本仓库 `AGENTS.md` 写了 96 处「禁止/不得/不能/必须」，但能自动拦截的只有 `scripts/check_*.py` 覆盖的少数几类。

| 类别 | 本仓库现状 | DSH 做法 | 判断 |
|---|---|---|---|
| 设计规范执行力 | 规范在文档与 `AGENTS.md` | 20+ 个 spec 测试扫描样式表，违规即失败 | **值得移植**（已落地 2 项） |
| 门禁组织 | CI 里逐个 `run:` 调用 10 个检查 | 统一 `scripts/run-gates.ts`（1762 行）+ 85 个 `check:`/`verify:` 脚本 | 可部分移植 |
| 新增能力的登记方式 | 同一操作名散在 7 个文件 | 能力 seam 三角色 + 一张「新行为归属位置」映射表 | **值得移植（先做映射表）** |
| 覆盖率 | 无覆盖率工具 | 每文件 100% 门槛 | 不建议照抄，见 §4.2 |
| 视觉回归 | 无像素基线 | 也**没有**像素基线，靠规则扫描 | 印证路线正确 |
| 决策记录 | 无 `.agents/`，`docs/plans/` 10 篇一次性计划 | `.agents/notes/` 四态 2772 篇 | 值得轻量移植 |
| 文档规模 | 49 篇 4699 行 | 717 篇（含 356 篇中文） | 不追求数量，追求「一处一个事实」 |
| 前端架构 | 零构建，15 个非模块 JS 1781 行 | 60+ 个 `ui-*` 包 + 薄 Web 壳（67 行） | 现状可接受，但边界应显式化 |

## 二、本轮已实施（有代码与门禁）

### 2.1 新增样式规范门禁 `scripts/check_ui_styles.py`（【实跑】）

把 DSH `packages/client/ui-theme/tests/` 里四条不依赖渲染的规则移植为 Python，扫描 5 个功能样式表：

| 规则 | 判定 | 当前值 |
|---|---|---|
| `color_literal` | 十六进制、颜色函数、`white`/`black` 字面量（用词边界排除 `white-space`） | 101 |
| `unguarded_hover` | `:hover` 规则未落在 `@media (hover: hover)` 区间内 | 26 |
| `off_scale_radius` | `border-radius` 出现 `4 < n < 999` 的 px（放行 0／≤4px／50%／999px） | 38 |
| `border_1px` | 中性 `1px solid` 边框 | 58 |

**基线机制**（这是关键设计）：`tests/expected/ui-style-baseline.json` 记录每个文件的每类命中数。**数量增加立即失败；数量减少也要求同步收紧**，防止修好后又悄悄退回去。

已做红绿验证：
- 往 `settings.css` 追加 `.synthetic-gate-probe { color: #ff00ff; }` → 失败 `settings.css.color_literal 由 3 增加到 4：新增了违规`；还原后通过。
- 清理一处 token 后未更新基线 → 失败 `styles.css.color_literal 由 69 减少到 68：请更新基线以收紧`；`--update` 后通过。
- 现状态：`UI_STYLES_OK {"border_1px": 58, "color_literal": 101, "off_scale_radius": 38, "unguarded_hover": 26}`。

### 2.2 清理重复 token 与死 token（【实跑】）

`styles.css:619-623` 曾有一个与 `design-tokens.css:3-9` 完全重复的 `:root` 块：

```css
:root {
  --space-1:4px; --space-2:8px; --space-3:12px; --space-4:16px;
  --space-6:24px; --space-8:32px;
  --danger:#a83b35; --radius:8px;
}
```

处理：把 `--danger:#a83b35` 迁入 `design-tokens.css:24` 的令牌层（它是唯一应有颜色字面量的文件），删除整块冗余定义。依据：`--radius` 的 `var(--radius)` 全仓引用数 **0**（死 token）；`--space-*` 六个值与令牌层完全相同（纯副本）。

验证：`var(--danger)` 的两处使用（`assistant.css:124`、`settings.css:71`）仍可解析；`grep --radius:` 在全部 css 中已 0 命中；`pytest tests/test_web_console.py` → **60 passed**；`ruff check`/`ruff format --check` 通过；门禁重新收紧到 101。

### 2.3 门禁接入 CI（【实跑】）

`.github/workflows/ci.yml` 的 `quality` job 在 `check_capability_coverage.py` 之后新增一行 `check_ui_styles.py`。YAML 解析验证通过（7 个 job 齐全）。**注意：这只证明本地文件合法，未证明远程 CI 已运行**（见 §5）。

## 三、DSH 侧值得移植的具体机制

### 3.1 规范即测试（已移植并验证）

DSH 的四个 spec 测试都有明确的判据实现，可直接对照：

- `hover-styles.client.spec.ts`（47 行）：`@media\s+\(hover:\s*hover\)(?:\s+and\s+[^{}]+)?\s*\{` 求区间后判每条 `:hover` 偏移；单测明确拒绝「被 `@media(min-width:800px)` 包裹」「被 `(any-hover:hover)` 包裹」「被 `(hover:hover), (pointer:coarse)` 包裹」三种假守卫。
- `radius-styles.client.spec.ts`（52 行）：只要值出现 `>4 且 <99` 的 px 或引用不存在的 `--dsw-radius-*` 即违规；例外用 `tests/expected/radius-exceptions.expected.json` **逐文件逐选择器逐声明**登记，不豁免整包。
- `color-tokens.client.spec.ts`（113 行）：扫全部 css/ts，`EXEMPT_PROPERTIES` 只放行 mask 与 box-shadow，`transparentLiteral()` 放行 alpha=0。
- DSH **没有** `toHaveScreenshot` 视觉基线——这印证本仓库「不引入像素快照」的选择是对的。

### 3.2 「新行为归属位置」映射表（已落地，2026-11-12 晚）

DSH `docs/architecture.zh.md` 末节是一张 22 行的表，每行形如「添加模型提供方 → 在 `ctx.llm` 上注册其适配器」。本仓库 `docs/ARCHITECTURE.md` **只有 39 行 2 节**，没有这一节。

本仓库对应的真实缺口（【实跑】）：新增一个操作（以 `project.list` 为线索）要改动 **7 个文件**——`entrypoints/mcp/server.py`、`entrypoints/http/routes.py`、`application/service.py`、`application/contracts.py`、`application/commands.py`、`application/capability_map.json`、`scripts/recovery_drill.py`。这套「改哪里」的知识目前只存在于维护者脑中，没写成表。

**已落地**：`docs/ARCHITECTURE.md` 新增「新行为的归属位置」一节（9 行表），覆盖新增应用操作／权限码／业务码／Web 页面／模板片段或脚本／数据库迁移／Agent 工具／模型供应商／导出制品与契约快照，并在末尾写明验证命令。

撰写时发现并修正了本文档一处数字错误：初稿写「32 个差集走前缀分支，见 `service.py:172-202`」，实测 `grep -c 'operation.startswith' service.py` = **5**，其中真正的分发分支只有 3 个（`service.py:171` 的 `agent.`、`:180` 的 `session.`、`:190` 的 `account.`）；`:158` 的 `data.change.` 与 `:159` 的 `access.` 是 `exclusive` 判定，不是分发。§6.3 中的相同表述一并按下述更正。

### 3.3 决策记录四态（值得轻量移植）

DSH `.agents/notes/` 分 `archived`（1855）／`implemented`（758）／`proposed`（96）／`rejected`（16），共 2772 篇。本仓库 **无 `.agents/`**，`docs/plans/` 只有 10 篇 406 行的一次性计划，且不标状态。

本仓库已有一个真实反例：`docs/UI_THEME_CHATGPT_REFERENCE.md`（161 行）给出了深色模式三步走计划与验收标准，但**全文 0 处状态标注**，实现侧 `prefers-color-scheme|data-theme|dark` 命中 **0** —— 读者无法从文档判断它是否已实施。

**建议**（低成本）：把 `docs/plans/` 改造为带状态的目录，或在每篇计划顶部加一行 `状态：草案 / 已实施 / 已否决`。不必建 2772 篇。

### 3.4 统一门禁入口（可部分移植）

DSH `scripts/run-gates.ts` 1762 行，`package.json` 有 85 个 `check:`/`verify:` 脚本，`Gate` 接口带 `needs`/`after`/`env`/`quick`/`allowFailure`/`retries`。

本仓库现状：`scripts/` 29 个 py（其中 6 个 `check_*.py`），CI 里逐个 `run:` 调用 10 处，**无聚合入口**；`manage.sh` 只做实例运维（start/stop/status/check/…），其 `check` 是「实例只读验收」，与代码质量无关。

**建议**：加一个 `scripts/run_gates.py --mode local|ci`，把现有检查串起来，让开发者一条命令跑完，而非复制 CI 里的 10 行。不必做成 1762 行的调度器。

## 四、明确**不建议**照抄的部分

### 4.1 前端包化与构建链（不抄）

DSH 有 60+ 个 `client/ui-*` 包、CSS Modules、Vite。本仓库是 Jinja SSR + 零构建，`apps/web/src` 对应物（本仓库的 15 个 JS）共 1781 行、**0 处 `import`/`export`**，靠 `window.OfficeUI` 等 5 个全局命名空间协作。

引入模块化构建会破坏「无 JS 可读、离线可部署、单进程」的既有约束。**现状可接受**。真正的问题是边界不可见（§6.1），而不是没有构建工具。

### 4.2 每文件 100% 覆盖率（不抄）

DSH `vitest.config.ts:423-430` 设 `perFile: true` + statements/branches/functions/lines 全 100%，注释写「Per-file so a well-covered big file can't subsidize a bare one」。

本仓库 `pyproject.toml`/`uv.lock` **无 pytest-cov**，`tests/` 121 个 `test_*.py` 共 21078 行、865 个用例。直接引入 100% 门槛会立刻不可用（大量 web 模板分支、异常路径）。**建议**：先只对「纯函数模块」（`app/application/codebook.py`、`catalog.py` 这类）设门槛，不搞全仓一刀切。

### 4.3 文档双语与体量（不抄）

DSH 717 篇文档（356 篇中文），本仓库 49 篇 4699 行。体量差距来自产品复杂度差异，不是质量差距。本仓库的文档内部一致性其实更好：**180 条相对链接断链 0 条**（【实跑】）。

## 五、未被验证的部分（不得当作已通过）

- **远程 CI 未运行**：本轮只改了本地 `.github/workflows/ci.yml` 并做 YAML 解析，未推送、未触发。远端 `dev` ahead 数见 §九。
- **两个门禁都只覆盖静态文本**：`check_ui_styles.py` 与 `check_frontend_rules.py` 都不渲染页面，因此无法发现「规则合规但视觉错误」的情况。DSH 同样如此（它也无像素基线），但两者都不构成视觉验收。
- **`page_header` 宏的等价性靠渲染摘要，不是像素比对**：验证的是 HTML 结构归一化后的 sha256 一致，未在浏览器中逐页复核视觉。逻辑上结构相同即视觉相同，但本轮未做浏览器取证。
- **断点收敛未做**（§6.5）：700–760px 区间无浏览器覆盖，因此不敢改，也就无法声称该区间布局正确。
- **DSH 的 `test:expected`、`test:snapshot`、`test:bench` 机制未细读**：仅在 `package.json` 见到脚本名，未研究其实现，因此不构成移植建议。
- **`docs/subsystems/*`（138 篇）未逐篇阅读**，只读了 `architecture.zh.md`、`testing.zh.md`、`web-styling.zh.md`、`ui-radius.zh.md`、`ui-design-review-cases.zh.md`、`cookbook/reviewing-ui-design.zh.md`。

## 六、值得做但本轮未做的（按性价比排序）

### 6.1 把「无 JS 可读」变成可机检约束（P1，**已完成**）

`AGENTS.md` 有多条关于无 JS 回退、禁止 `innerHTML` 的要求，但全仓 `tests/*.py` 中 `innerHTML` 引用 **0** 处、`axe` **0** 处 → **没有任何回归能因违规而失败**。这正是 DSH `testing.zh.md` 说的「一个守卫只有在回归能让它失败时才有效」。

**已落地**：新增 `scripts/check_frontend_rules.py` + `tests/expected/frontend-rules-baseline.json`，扫描模板目录下全部 html/js/css，六条规则：`inline_style`、`inline_handler`、`inner_html`、`native_dialog`、`media_spacing`、`breakpoint`。基线同样**双向锁定**，当前六项全 **0**（即硬门禁，无存量豁免）——因为本轮同时清掉了存量：`links.html:29`/`:85` 两处内联样式改为语义 class（`.unbulleted`／`.selection-note`），20 处 `@media(` 补上空格。

两处实现要点（否则会误判）：
- `native_dialog` **不能匹配裸 `confirm(`**：`templates/interactions.js:197` 自己定义了共享的 `function confirm(message, options = {})`，裸调用是那个组件而非浏览器弹窗。第一版因此误报 2 处。
- 基线里 `breakpoint` 规则只阻止**新增**档位，见 §6.5 的冻结决定。

红绿验证：注入 `style="color:red"`／`el.innerHTML = bad;`／`alert("x")`／`@media (max-width:880px)` 四次分别失败并给出新增计数与行号，还原后 `FRONTEND_RULES_OK` 全 0。已接入 CI `quality` job。

### 6.2 能力清单的三份真相收敛（P1，**部分收窄：两份已被门禁约束**）

【实跑】当前同一个操作名存在于三处：`application/capability_map.json`（60 条）、`application/commands.py` 的 `OPERATION_INPUTS`、`application/contracts.py` 的 `OPERATION_OUTPUTS`。`check_capability_coverage.py` 会断言 `set(rows) == set(OPERATION_INPUTS)`，所以集合一致有保障，但**内容没有单一来源**。

**本轮修正**：原表述「三处互相独立」不准确。实测 `check_capability_coverage.py` 已交叉校验 MCP 工具名（`TOOL_INPUTS`）、`OfficeClient` 方法、CLI action、`Change`/`DataChange` 预览集合与 `capability_map` 的一致性 → 输入侧早已被门禁约束。真正**从未被校验**的是 `OPERATION_OUTPUTS`，见 §7 第 8 步。

更重要的一处**纠正**：我最初记录「`capability_map.json` 只被检查脚本读取」是**错的**。实测它有运行时消费者——`application/service.py:214` 把 `transport_coverage()` 放进 `capabilities` 输出，`application/agent_tools.py:21` 用它构造内部 Agent 的工具目录（`row["sdk"]` → 操作名，按 `row["mode"] == "direct"` 过滤）。所以它是**真实的运行时契约**，不是装饰性文件。（先前错误源于只 grep 了 `capability_map` 字面量，漏掉了经 `capability_catalog.transport_coverage()` 的间接引用。）

### 6.3 应用层缺少统一操作常量（P2）

`application/service.py:229-260` 的 `operations` 字典只有 **28** 个键，而 `OPERATION_INPUTS` 有 **60** 个；差集 32 个走前缀分支。**没有** `OPERATIONS` 统一常量。

**更正**：本文早前把前缀分支写作「见 `service.py:172-202`」，并暗示有 22 个分支。实测 `grep -c 'operation.startswith' service.py` = **5**，其中真正的分发分支只有 3 个——`service.py:171` 的 `agent.`（→ `agent_service().request`）、`:180` 的 `session.`（→ `Sessions(...).request`）、`:190` 的 `account.`（→ `account_service().request`）；`:158` 的 `data.change.` 与 `:159` 的 `access.` 是 `exclusive` 判定，不是分发。

DSH 的对应解法是「能力 seam 三角色 + 注册表」。本仓库要收敛，建议先加一个只读的 `OPERATIONS` 常量（名字 + 输入模型 + 输出模型 + 分发方式），再让 `service.py`/`commands.py`/`contracts.py`/`capability_map.json` 从它派生。**这是较大重构，需单独立项**，不宜与本次混做。

### 6.4 `capabilities` 输出硬编码 `bulk: False`（P2，**二次分析后否决**）

原建议：`application/service.py:226` 写死 `"bulk": False`、`modules/access/outputs.py:130` 类型是 `bulk: Literal[False]`，建议改成普通 `bool`。

**否决理由（本轮实测）**：`contracts/openapi.json` 现在生成的是 `"bulk": {"type": "boolean", "const": false}`。改成 `bool` 会让 OpenAPI 从 `const: false` 退化为无约束的 `boolean` —— **契约精度下降、零行为收益**。原判断「未来开启批量要改类型定义」确实成立，但改类型定义正是一次有意识的契约变更，不应为「以后可能改」提前弱化当前契约。

若真要支持批量，正确顺序是：先实现批量语义并让 `capability_map.json` 出现批量操作（当前 60 个操作中无 batch/bulk 类），再一并放类型与 OpenAPI。**结论：不改，保留 `Literal[False]`。**

### 6.5 断点不统一（P2，**收敛被否决，改为冻结现状**）

【实跑】`@media` 共 25 条、9 种宽度：540/600/650/700/701/760/1000/1100/1200px。700px 曾有 `@media(max-width:700px)`（13 处）与 `@media (max-width:700px)`（4 处）两种写法——**空格写法已统一**（20 处 `@media(` 全部补齐）。`git diff -w` 为空，属纯空白改动。

**档位收敛原计划 9 → 5（600/650/760→700、1100→1000），试做后全部回退**，理由逐条核实：
- `styles.css` 的 `@media (max-width:760px) { .workspace { width:100vw } }` 作用在 `.workspace{width:min(840px,95vw)}`（`styles.css:620-626`）之上。并入 700 会让 700–760px 区间的助手抽屉从 95vw 变 100vw；700px 视口实测差 **35px**、760px 差 **38px**。
- 650/600 归并同样改变筛选区与弹窗的折行时机。
- 现有浏览器回归只测 `1440/1024/768/390/320`（`tests/test_ui_catalog_browser.py:80,139`），**700–760px 区间没有覆盖**，改完无法证伪。

**最终决定**：`BREAKPOINTS = (540, 600, 650, 700, 701, 760, 1000, 1100, 1200)` 作为冻结清单写进 `check_frontend_rules.py`，只阻止新增档位；归并留给有视觉取证的后续工作。这是本轮**唯一**「发现问题但因无法验证而主动不做」的项。

### 6.6 统一 `page-header` 与按钮宏（P3，**page-header 已完成**）

20 个页面手写 `<header class="page-header">` 四件套（eyebrow/h1/muted/actions），3 种变体；`<button` 66 个、`<a class="button` 16 个，而提供的 `field()` 宏只被调用 13 次。**无 button/card/input 宏**。

**已落地**：新增 `templates/_page_header.html`，签名 `{% macro page_header(eyebrow, title, summary=None, variant="", panel_title=None) %}`，20 个页面全部改用，另 4 个（`assistant_workspace`/`error`/`login_recovery`/`signed_out`）本就没有该结构。右侧动作区由调用方 `{% call %}` 传入而**不代为包裹 `.actions`**，保留各页原有布局语义；`panel_title` 专供 `links.html` 的 `data-panel-title`。

等价性用渲染摘要验证：渲染全部 24 个模板、抽取 `<header class="page-header[^>]*>.*?</header>`、归一化空白后比对 sha256，**前后不等价模板数 0**。

一个踩过的坑：最初把 `data-panel-title` 用字符串拼进属性区，Jinja 自动转义把引号变成 `&#34;`（实测输出 `<header class="page-header" data-panel-title=&#34;…&#34;>`）→ 改为专用参数后消失。

新模板片段必须登记 `tests/ui-pages.json` 的 `components`（否则 `test_browser_page_inventory_is_complete` 失败，本轮正是它先抓到的），39 → 40 项。

**按钮宏仍不做**：`<button>` 66 处、`<a class="button">` 16 处，但按钮只有 3 种 class 组合，抽象收益低于 Jinja 宏能表达的差异（图标、确认属性、`data-*` 载荷），本轮不强行统一。

## 七、执行顺序与最终状态

| 步 | 事项 | 状态 |
|---|---|---|
| 1 | 样式门禁 + 重复 token 清理 + CI 接入（§2.1–2.3） | **已完成** |
| 2 | `docs/ARCHITECTURE.md`「新行为归属位置」表（§3.2） | **已完成** |
| 3 | 计划类文档加状态标注（§3.3） | **已完成**（6 篇，+28 行） |
| 4 | `scripts/check_frontend_rules.py`（§6.1） | **已完成**（六项全 0） |
| 5 | 断点收敛（§6.5） | **收敛否决，改为冻结现状**；写法已统一 |
| 6 | `page-header` 宏（§6.6） | **已完成**（20 页，输出等价） |
| 7 | `capabilities.bulk` 放开类型（§6.4） | **二次分析后否决**（会降低 OpenAPI 精度） |
| 8 | 能力清单单一来源重构（§6.2/§6.3） | **未做**，需单独立项；本轮只补了缺的那条交叉校验 |

第 8 步的修正：原判断「三份真相互相独立」不准确。`check_capability_coverage.py` 早已断言 `set(capability_map) == set(OPERATION_INPUTS)`，并校验 MCP 工具名／SDK 方法／CLI action。真正的缺口是 **`OPERATION_OUTPUTS` 从未与输入清单交叉校验**。已补：

```python
outputs = set(OPERATION_OUTPUTS) | ({"access.list"} if "access.list" in OPERATION_INPUTS else set())
assert outputs == set(OPERATION_INPUTS), ("输出契约与输入契约不一致", ...)
```

`access.list` 的差集是**合理的**：`contracts.validate_output()` 按 `payload["kind"]` 从 `ACCESS_PAGES`（employees/roles/permission-sets/permissions/audit）动态取模型，不进静态表。红验证：从 `contracts.py` 删掉 `"record.history"` 一项 → `AssertionError: ('输出契约与输入契约不一致', ['record.history'], [])`。

## 八、与既有审查记录的关系

本文件与 [架构与 Web 双维度专业审查](AUDIT_2026-11-12.md) 是**不同视角**：
- 前者（11-12 上午）复核的是「专家报告说的对不对」，结论多为「该问题不存在或需降级」。
- 本文件（11-12 晚间）是在**已知 DSH 做法**的前提下，判断「哪些机制能解决本仓库的真实弱点」。

两份文件都遵循同一纪律：区分实跑与源码阅读，未验证项显式声明，不把历史验收数字当作当前证据。

## 九、本轮全部改动的门禁证据（【实跑】）

| 门禁 | 结果 |
|---|---|
| `pytest -m 'not live' -q` | **761 passed, 147 deselected** |
| `ruff check .` / `ruff format --check .` | All checks passed / 479 files already formatted |
| `mypy office-data-service/app clients/python/src clients/agent/src` | Success: no issues in **137** source files |
| `scripts/check_architecture.py` | `ARCHITECTURE_BOUNDARIES_OK` |
| `scripts/check_capability_coverage.py` | `CAPABILITY_COVERAGE_OK operations=60`（新增输出契约交叉校验） |
| `scripts/export_contracts.py --check` | `CONTRACT_SNAPSHOTS_OK` |
| `scripts/configure_template.py --check` | `{"status": "verified", "changed": []}` |
| `scripts/check_ui_styles.py` | `{"border_1px": 58, "color_literal": 101, "off_scale_radius": 38, "unguarded_hover": 26}` |
| `scripts/check_frontend_rules.py` | 六项全 `0` |
| `OFFICE_AGENT_BROWSER_TESTS=1 pytest tests/test_ui_catalog_browser.py` | 1 passed |

未运行：远程 CI、Docker 构建、真实 OIDC／模型、移动真机与读屏器。

## 十、反向筛查：本项目手写板块 vs DSH（2026-11-12 夜间）

前九节是「把 DSH 的机制移植成门禁」（横切面）。本节换方向：按**能力板块**盘点本项目自己手写了什么，再逐项看 DSH 是否有更完整的实现。证据分级同前：**【实跑】**指本轮真跑过命令或脚本，**【源码】**指只读代码得出的判断。

### 10.1 本项目手写板块的体量【实跑】

| 板块 | 文件数 / 行数 |
|---|---|
| `office-data-service/app/application/` | 20 文件 2755 行 |
| `office-data-service/app/modules/` | 38 文件 4612 行（access 18/2645、agent 8/942、data 7/714、query 4/311） |
| `office-data-service/app/entrypoints/` | 26 文件 3730 行 |
| `office-data-service/app/infrastructure/` | 13 文件 1416 行 |
| `office-data-service/app/common/` | 11 文件 362 行 |
| `clients/agent` | 1624 行（`runtime` 446、`gateway` 225、`toolsets` 211 最大） |
| `clients/python` | 2138 行（`client` 473、`cli` 326 最大） |

DSH 侧对应 `docs/subsystems/` 共 **57 篇**：agent-team、approval、attachment、boot、browser-use、client-modules、client-resources、commands、compaction、computer-use、conversation、core、credentials、deliverables、extensions、feedback、filesystem、forge、goal、jobs、liveshare、llm-streaming、lsp、mcp、office-kit、otel、permission-presets、persistence、plan、product-telemetry、ptc-runtime、sandbox、schedule、scope、session-projection、session-query、session-reference、session-telemetry、session-title、session、settings、shell、sidebar-right、skills、slots、spill、ssh、storage、subagent、subprocess、system-prompt、terminal、todo、token-meter、tools、translation、typert、user-questions、voice-input、web-client、web-server、web、webhook、workflow、working-directory、workspace、worktrees。

### 10.2 已定位并修复的三处（本轮已落地，各自都有旧实现红验证）

#### A. 会话标题：会被截断代理对 + 可被方向符伪造

**缺陷【实跑】**：`assistant-shell.js` 用 `prompt.slice(0,40)` 按 UTF-16 码元截断。`('A'+'🎉'.repeat(25)).slice(0,40)` 末尾得到孤立高位代理项 `d83c`，该 JSON 到达服务端后 Pydantic 直接拒绝（`unable to parse raw data as a unicode string`），**首条消息含 emoji 且落在切点时新建会话变成 422**。同时标题原样入库：`ConversationCreate(title="测试\x1b[31m红色\x1b[0m\u202e反转")` 通过校验，ANSI 转义与 U+202E 方向符可让标题视觉反转。

**DSH 做法【源码】**：`packages/session/session-title/src/normalize.ts` 依次剥离 OSC／CSI／ESC／控制符／方向控制符，再 `\s+` 压成单空格；截断按**码点**累加字节预算，绝不切断字符；`SessionTitleSource` 区分 `fallback`／`provider`／`user`，用户显式改名即 pin 标题、停止自动生成。

**本轮落地**：新增 `app/common/text.py`（清洗 + 按码点截断 + 空标题回退「新会话」），schema 在 `mode="before"` 阶段清洗并拒绝纯不可见标题，会话首条消息由服务端派生标题（用户改名后不覆盖），前端改用 `Array.from(prompt).slice(0,40).join('')`。落在 `common` 层而非 `modules/agent` 下，是因为结构门禁不允许 `schemas` 依赖同模块的非白名单兄弟文件——这条规则本身是对的。

#### B. 模型用量：原样倾倒 JSON

**缺陷【实跑】**：任务详情把 `turn.result.usage` 整个 JSON 塞进 `<details>`；会话流里只有一句「模型服务未返回完整用量」，用户无法判断**哪些请求**没被计量。

**DSH 做法【源码】**：`token-meter` 的 `TokenMeasurement{logRevision, baseline{kind:'usage'|'estimated'}, surfaceDeltaTokens, totalTokens, surfaceTokens, nodes[]}`，区分「有提供方锚点」与「无锚点自估」。

**本轮落地**：按字段展示请求次数、输入／输出 Token、工具调用次数，明确标注未返回用量的请求数，无用量时也不留空。**只呈现已有五字段，不声称能做位置级计量**——本项目的 usage 由运行时一次性汇总写入，没有 per-node 数据。

#### C. 敏感信息清洗：键名变体与文本凭证大面积漏出

**缺陷【实跑】**：原 `SECRET_KEYS` 只有 9 个精确键名，`sanitize()` 只替换 `Bearer`。实测 10 个真实形状中 **8 个原样外发**：`apiKey`、`X-Api-Key`、`token`、`db_password`、`private_key`、`session_token`、URL 里的 `?access_token=`、`sk-` 前缀密钥。

**本轮落地**：键名先归一 camelCase／短横线再判定，按 `_password`／`_token`／`_secret`／`_key`／`_credential` 后缀兜底；`max_tokens`、`input_tokens`、`token_limit` 等预算计数**显式放行**（误伤会让成本信息失真）；文本清洗扩展到 `Basic` 头、URL 查询参数、`sk-`／`AKIA`／`ghp_` 前缀与 PEM 私钥块。服务端三处（`agent_service.py`、`agent_runner.py` 两处）都调同一个函数，因此自动生效。

仍需说明边界：这是**本地兜底**，不承诺识别全部个人信息或提示注入；不符合已知形状的裸凭证仍可能漏出，外发仍须企业批准目的地与范围。

### 10.3 确认存在但本轮未做的（附判断依据）

| 缺口 | 现状【源码】 | DSH 对应 | 为什么本轮不做 |
|---|---|---|---|
| 会话历史过短 | `agent_history.py` 只取最近 **3 轮**、每轮各 1000 字符，总预算 `min(6000, limit//2)`，超限从头丢；无摘要、无 token 计量 | `compaction` 三事件（start/summary/end）+ 摘要挂 `surfaceOp:{op:'replace'}`，锁括住整个操作 | 摘要压缩需要新的模型调用与持久化契约（迁移 + 契约导出 + 计费边界），是独立设计题，不能在对比轮顺手改 |
| 缺少 todo / plan / subagent / schedule | `grep 'todo\|subagent\|schedule'` 在两个源码目录只命中 UI 文案 | 各自有独立 subsystem 与工具 | 属新增产品能力，不是「更优实现」；需先定产品范围 |
| 无跨会话引用 | `Conversations` 只有 owned/create/listing/detail/ensure_idle/attach/archive/rename | `session-reference` 有候选、规范 mention、`capturedThroughSeq` | 同上；且要连带设计历史可见性与权限指纹 |
| 无 `/` 斜杠命令 | 无命令注册表 | `commands` 的 `CommandDefinition` 直接对 agent 执行、不创建模型消息 | 属交互形态扩展，需与既有原生表单提交通路统一 |
| 审批只有一条审计 | `service.py` 仅 `audit("agent.decided", ...)` | `approval/asked` + `approval/decided` 事件对，非 `allowed-once` 一律拒绝 | 改审计事件结构会影响既有审计读取方与契约快照，需单独立项 |

### 10.4 本项目做得好、不建议照抄 DSH 的部分

- `progress.py` 的单槽最新投影 + `asyncio.timeout(1)` 隔离，比「排无限队列」更稳；观察失败只停观察，不取消业务。
- `gateway.py` 的一次性 ticket：`proposal()` 拒重放与过期，`execute()` **先消耗再执行**，失败也不自动重放。
- `agent_history.py` 的权限指纹校验：权限变化即断绝旧历史并明确告知，而不是静默继续喂旧上下文。
- `common/errors.py` 的 `validation_error()` 只回传 `path` 与 `code`（`include_input=False, include_context=False`），不把用户输入回显到错误里。

### 10.5 本轮门禁证据【实跑】

| 门禁 | 结果 |
|---|---|
| `pytest -m 'not live' -q` | **777 passed, 147 deselected**（本轮 +16） |
| `pytest --project clients/agent clients/agent/tests` | **104 passed** |
| `ruff check` / `ruff format --check` | All checks passed / 全部已格式化 |
| `mypy office-data-service/app clients/python/src clients/agent/src` | Success: no issues in **138** source files |
| `check_architecture` / `check_capability_coverage` | `ARCHITECTURE_BOUNDARIES_OK` / `CAPABILITY_COVERAGE_OK operations=60` |
| `export_contracts.py --check` | `CONTRACT_SNAPSHOTS_OK` |
| `check_ui_styles` / `check_frontend_rules` | 与第九节一致，无新增违规 |

红验证：标题测试在旧 schema 上 **4 failed**、前端 `slice` 断言失败；用量测试在旧 shell 上失败；脱敏测试在旧键名逻辑上 **2 failed**。

未运行：真实 OIDC／模型供应商、Docker 构建、移动真机与读屏器、远程 CI。

