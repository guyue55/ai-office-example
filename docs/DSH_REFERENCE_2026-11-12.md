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
| 设计规范执行力 | 规范在文档与 `AGENTS.md` | 20+ 个 spec 测试扫描样式表，违规即失败 | **值得移植**（已落地 1 项） |
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

### 3.2 「新行为归属位置」映射表（值得移植，尚未做）

DSH `docs/architecture.zh.md` 末节是一张 22 行的表，每行形如「添加模型提供方 → 在 `ctx.llm` 上注册其适配器」。本仓库 `docs/ARCHITECTURE.md` **只有 39 行 2 节**，没有这一节。

本仓库对应的真实缺口（【实跑】）：新增一个操作（以 `project.list` 为线索）要改动 **7 个文件**——`entrypoints/mcp/server.py`、`entrypoints/http/routes.py`、`application/service.py`、`application/contracts.py`、`application/commands.py`、`application/capability_map.json`、`scripts/recovery_drill.py`。这套「改哪里」的知识目前只存在于维护者脑中，没写成表。

**建议**：在 `docs/ARCHITECTURE.md` 增加一节「新行为的归属位置」，至少覆盖——新增操作、新增权限码、新增业务码、新增 Web 页、新增迁移、新增 Agent 工具、新增 MCP 工具。这是纯文档工作，零风险，收益是让接手者不必反向工程。

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

- **远程 CI 未运行**：本轮只改了本地 `.github/workflows/ci.yml` 并做 YAML 解析，未推送、未触发。远端 `dev` 已 ahead 44 个提交。
- **设计规范门禁只覆盖静态文本**：`check_ui_styles.py` 不渲染页面，因此无法发现「规则合规但视觉错误」的情况。DSH 同样如此（它也无像素基线），但两者都不构成视觉验收。
- **DSH 的 `test:expected`、`test:snapshot`、`test:bench` 机制未细读**：仅在 `package.json` 见到脚本名，未研究其实现，因此不构成移植建议。
- **`docs/subsystems/*`（138 篇）未逐篇阅读**，只读了 `architecture.zh.md`、`testing.zh.md`、`web-styling.zh.md`、`ui-radius.zh.md`、`ui-design-review-cases.zh.md`、`cookbook/reviewing-ui-design.zh.md`。

## 六、值得做但本轮未做的（按性价比排序）

### 6.1 把「无 JS 可读」变成可机检约束（P1，高性价比）

`AGENTS.md` 有多条关于无 JS 回退、禁止 `innerHTML` 的要求，但全仓 `tests/*.py` 中 `innerHTML` 引用 **0** 处、`axe` **0** 处 → **没有任何回归能因违规而失败**。这正是 DSH `testing.zh.md` 说的「一个守卫只有在回归能让它失败时才有效」。

**建议**：仿照本次样式门禁的做法，加 `scripts/check_frontend_rules.py`，扫描模板与 JS：`innerHTML` 直接赋值、未加 `type="button"` 的裸 `<button>`、`onclick=` 内联处理器、无 `aria-label` 的图标按钮。同样用基线锁定存量。

### 6.2 能力清单的三份真相收敛（P1，需设计）

【实跑】当前同一个操作名存在于三处且互相独立：`application/capability_map.json`（60 条）、`application/commands.py` 的 `OPERATION_INPUTS`、`application/contracts.py` 的 `OPERATION_OUTPUTS`。`check_capability_coverage.py` 会断言 `set(rows) == set(OPERATION_INPUTS)`，所以集合一致有保障，但**内容没有单一来源**。

更重要的一处**纠正**：我最初记录「`capability_map.json` 只被检查脚本读取」是**错的**。实测它有运行时消费者——`application/service.py:214` 把 `transport_coverage()` 放进 `capabilities` 输出，`application/agent_tools.py:21` 用它构造内部 Agent 的工具目录（`row["sdk"]` → 操作名，按 `row["mode"] == "direct"` 过滤）。所以它是**真实的运行时契约**，不是装饰性文件。（先前错误源于只 grep 了 `capability_map` 字面量，漏掉了经 `capability_catalog.transport_coverage()` 的间接引用。）

### 6.3 应用层缺少统一操作常量（P2）

`application/service.py:229-260` 的 `operations` 字典只有 **28** 个键，而 `OPERATION_INPUTS` 有 **60** 个；差集 32 个走前缀分支（`agent.` → `agent_service().request`、`session.` → `Sessions(...).request`、`account.` → `account_service().request`，见 `service.py:172-202`）。**没有** `OPERATIONS` 统一常量。

DSH 的对应解法是「能力 seam 三角色 + 注册表」。本仓库要收敛，建议先加一个只读的 `OPERATIONS` 常量（名字 + 输入模型 + 输出模型 + 分发方式），再让 `service.py`/`commands.py`/`contracts.py`/`capability_map.json` 从它派生。**这是较大重构，需单独立项**，不宜与本次混做。

### 6.4 `capabilities` 输出硬编码 `bulk: False`（P2）

`application/service.py:226` 写死 `"bulk": False`，`modules/access/outputs.py:130` 类型是 `bulk: Literal[False]`。这不是 bug（当前确实不支持批量），但把「当前不支持」写成了类型层面的不可变事实，未来开启批量要改类型定义。建议改为普通的 `bool` 并保留说明。

### 6.5 断点不统一（P2）

【实跑】`@media` 共 25 条、9 种宽度：540/600/650/700/701/760/1000/1100/1200px。且 700px 有 `@media(max-width:700px)`（13 处）与 `@media (max-width:700px)`（4 处）两种写法。

DSH 用令牌化的六档圆角 + spec 强制。本仓库可先把断点收敛到 3 档（如 700／1000／1200），并把写法统一（加不加空格）。可并入 §6.1 的检查脚本。

### 6.6 统一 `page-header` 与按钮宏（P3）

20 个页面手写 `<header class="page-header">` 四件套（eyebrow/h1/muted/actions），3 种变体；`<button` 66 个、`<a class="button` 16 个，而提供的 `field()` 宏只被调用 13 次。**无 button/card/input 宏**。

Jinja 宏是低成本抽象，不像 DSH 的 `ui-primitives` 需要打包系统。建议先抽 `{% macro page_header(eyebrow, title, muted=None, actions=None) %}`，风险极低。

## 七、建议执行顺序

1. **已完成**：样式门禁 + 重复 token 清理 + CI 接入。
2. 补 `docs/ARCHITECTURE.md` 的「新行为归属位置」表（§3.2，纯文档，零风险）。
3. 给计划类文档加状态标注（§3.3，纯文档）。
4. 加 `scripts/check_frontend_rules.py`（§6.1，与本次样式门禁同一套基线机制，可复制）。
5. 断点收敛（§6.5，可并入第 4 步）。
6. `page-header` 宏（§6.6，独立小 PR）。
7. `capabilities.bulk` 类型放开（§6.4）。
8. 能力清单单一来源重构（§6.2/§6.3，需立项设计）。

## 八、与既有审查记录的关系

本文件与 [架构与 Web 双维度专业审查](AUDIT_2026-11-12.md) 是**不同视角**：
- 前者（11-12 上午）复核的是「专家报告说的对不对」，结论多为「该问题不存在或需降级」。
- 本文件（11-12 晚间）是在**已知 DSH 做法**的前提下，判断「哪些机制能解决本仓库的真实弱点」。

两份文件都遵循同一纪律：区分实跑与源码阅读，未验证项显式声明，不把历史验收数字当作当前证据。
