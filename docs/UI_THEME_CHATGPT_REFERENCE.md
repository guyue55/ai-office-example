# UI 主题参考：ChatGPT 风格迁移与深色模式设计（2026-10-27）

> 目标：借鉴 ChatGPT 网页版的视觉质感与交互规范，为模板项目建立「语义设计令牌 + 双主题（浅色/深色）」体系，不引入新前端栈，全部改动落在现有服务端渲染 + 手写 CSS 架构内。

## 一、调研来源与证据边界

| 来源 | 用途 | 证据形态 |
|---|---|---|
| LibreChat（46k+ 星）packages/client/src/theme/defaults.css | 公开开源项目中最接近 ChatGPT 实际用色/间距/圆角刻度的资产；注释明确标注参考 Click UI muted 色 #696e79 | 直接拉取 main 分支源码核对数值 |
| web.dev《prefers-color-scheme》官方文章 | 深色模式标准实现：color-scheme: light dark + :root 变量 + prefers-color-scheme 媒体查询 | 官方文档原文核对 |
| OpenAI 官方帮助中心 | ChatGPT 主题切换选项（亮/暗/跟随系统） | 页面加载受限，选项模式为业界公开常识，标注为推断 |
| xtekky/chatgpt-clone（3.5k 星）、shadcn-ui/chatbot-template（1.1k 星）、assistant-ui/assistant-ui（12.7k 星） | 交互节奏与组件布局参考 | 栈不匹配，仅借鉴设计不引入栈 |

关键结论：ChatGPT 的质感来自四件事——中性灰阶、低密度布局、细边框浅阴影、克制的品牌色点缀。全部与框架无关，可逐条迁移到现有 CSS。

## 二、ChatGPT 风格核心特质拆解

1. 中性灰阶 + 无大色块：浅色白底/极浅灰画布、深灰正文、品牌色仅点缀；强调靠描边、留白、字重，不靠大面积彩色。
2. 暗色是令牌整体翻转：暗色画布约 #212121、侧栏约 #171717、亮灰文字；全部走同一套语义令牌，切主题即翻转令牌，不是逐页调色。
3. 低密度 + 统一圆角 + 极浅阴影：列表/菜单 hover 用浅灰高亮、无分隔竖线；弹层小半径 + 浅阴影 + 细边框；过渡动画克制。
4. Composer（输入框）是视觉主角：圆角胶囊大输入框 + 圆形发送按钮右下内嵌 + 上方小图标工具按钮。

## 三、本项目现状核对（真实源码）

- 模板位置：office-data-service/app/entrypoints/web/templates/
- 样式文件：styles.css（948 行）+ shell.css（79 行）+ assistant.css（115 行）
- 现状令牌：扁平单值（--ink、--bg、--accent:#0f766e），无深色模式，无语义分层
- 技术形态：服务端渲染 Jinja + 手写 CSS，无 Tailwind、无 Node 构建链

结论：不引入 Tailwind/React/shadcn 栈（会破坏模板轻量部署特性），只做令牌架构升级 + 组件视觉对齐。

## 四、设计令牌体系（两层架构）

### 4.1 基础刻度层（Primitive，照抄 ChatGPT/LibreChat 灰阶）

    :root {
      --gray-50:  #f7f7f8;
      --gray-100: #ececec;
      --gray-200: #e3e3e3;
      --gray-300: #cdcdcd;
      --gray-400: #999999;
      --gray-500: #595959;
      --gray-600: #424242;
      --gray-650: #393939;
      --gray-700: #2f2f2f;
      --gray-800: #212121;
      --gray-850: #171717;
      --gray-900: #0d0d0d;
    }

### 4.2 语义角色层（Semantic，浅色为默认）

    :root {
      /* 表面 */
      --surface-canvas:  var(--gray-50);
      --surface-card:    #ffffff;
      --surface-chat:    #ffffff;
      --surface-sidebar: var(--gray-50);
      --surface-hover:   var(--gray-100);
      --surface-active:  var(--gray-200);
      /* 文字 */
      --text-primary:    var(--gray-800);
      --text-secondary:  var(--gray-600);
      --text-muted:      #696e79;
      /* 边框 */
      --border-light:    var(--gray-200);
      --border-medium:   var(--gray-300);
      /* 品牌 */
      --accent:          #0f766e;
      --accent-hover:    #095e58;
      /* 形状 */
      --radius:          10px;
      --radius-sm:       8px;
      /* 阴影 */
      --shadow-pop:      0 2px 12px rgba(0,0,0,.08);
    }

### 4.3 深色覆盖（整体翻转，同一组语义名）

    html.dark {
      --surface-canvas:  var(--gray-800);
      --surface-card:    var(--gray-700);
      --surface-chat:    var(--gray-700);
      --surface-sidebar: var(--gray-850);
      --surface-hover:   var(--gray-650);
      --surface-active:  var(--gray-600);
      --text-primary:    var(--gray-100);
      --text-secondary:  var(--gray-300);
      --text-muted:      #b3b6bd;
      --border-light:    var(--gray-800);
      --border-medium:   var(--gray-650);
      --accent:          #41a79d;
      --accent-hover:    #6dc8b9;
      --shadow-pop:      0 2px 12px rgba(0,0,0,.4);
    }

## 五、深色模式接入（web.dev 官方规范）

1. 声明色域：:root { color-scheme: light dark; }，让浏览器启用对应 UA 样式与表单控件配色。
2. 首屏防闪烁：head 内联同步脚本，读取 localStorage.theme（light/dark/未设置），未设置时用 window.matchMedia('(prefers-color-scheme: dark)') 判断，再给 html 加 .dark。必须在首帧渲染前执行。
3. 切换控件：三态切换（亮/暗/跟随系统），点击写 localStorage 并同步 html class；"跟随系统"时监听 matchMedia change 事件联动。
4. 服务端渲染无阻碍：不需要 React 状态，纯 CSS class 翻转即可，反而比 CSR 更简单。

## 六、组件对齐清单（按 ChatGPT 规范逐项）

| 现有组件 | ChatGPT 规范 | 改法 |
|---|---|---|
| 控制台侧栏 | 无边框、hover 浅灰块、选中项同 hover 色加字重 | 去竖向分隔线，hover 只换背景 |
| 表格/列表 | 行 hover 浅灰、无竖线、圆角 10px 卡片容器 | 移除斑马纹和竖边框 |
| 主按钮 | 实色圆角 8px，hover 加深 | 现有按钮描边偏粗，整体减淡 |
| 次要按钮 | 白底细灰边、hover 浅灰 | 保持结构，弱化边框 |
| 输入框 | 圆角 8~10px，focus 1px 边框 + 4px 半透明环 | 现 :focus-visible 是 2px 外描边，改双描边 |
| Agent 聊天 composer | 圆角胶囊大输入框、圆形发送按钮右下内嵌、上方小图标 | assistant.html 输入区强化 |
| 弹窗/抽屉 | 暗色下 #2f2f2f 卡片 + 浅阴影 + 细边框 | 套 surface-card 令牌 |
| 状态徽标 | 语义色 success/info/warning/error 各配 subtle 填充 + 边框 + 文字三档 | 引入 LibreChat 的 status-* 三档结构 |

## 七、数值对照表（LibreChat defaults.css 摘录）

### 浅色

- --surface-primary = 白；--surface-secondary = gray-50；--surface-tertiary = gray-100
- --text-primary = gray-800；--text-secondary = gray-600；--text-muted = #696e79
- --accent-primary = rgb(18,110,107) 约 #126e6b（与项目现用 #0f766e 接近，可沿用）

### 深色

- --page-canvas = gray-850 #171717；--surface-primary = gray-900 #0d0d0d；--surface-chat = gray-700 #2f2f2f
- --accent-primary = rgb(65,167,157) 约 #41a79d；hover rgb(109,200,185) 约 #6dc8b9
- --border-light = gray-800（LibreChat 注释原文：暗色下边框只需隔开相邻两层表面）
- --text-primary = gray-100；--text-secondary = gray-300；--text-muted = #b3b6bd

### 语义状态色（双主题三档：fill / border / text）

- success / info / warning / error 各有 -subtle（填充）、-border（描边）、-strong（强文字）三值，浅暗各一套。

## 八、实施计划（三步走）

| 阶段 | 内容 | 涉及文件 | 预估改动 |
|---|---|---|---|
| 1. 令牌层 | 基础刻度 + 语义角色 + 深色覆盖 + color-scheme 声明 | styles.css 顶部 | 约 80 行新增 |
| 2. 切换机制 | head 内联防闪脚本 + 三态切换控件 + localStorage | base/layout 模板 | 约 40 行 |
| 3. 组件对齐 | 按第六节清单逐组件替换硬编码颜色为语义令牌 | styles.css / shell.css / assistant.css + 各模板 | 分散微调 |

验收标准：

- 切换亮/暗后所有页面无残留硬编码色（用 DevTools 过滤 hex 检查）
- 系统偏好变化时"跟随系统"档位自动联动
- 刷新页面无闪烁（深色用户首帧即深色）
- 597 项离线测试 + 浏览器冒烟通过

## 九、参考项目清单（仅参考不引入）

- LibreChat：主题令牌数值来源（MIT 许可）
- shadcn-ui/chatbot-template：消息气泡/composer/流式输出交互节奏参考
- assistant-ui/assistant-ui：聊天组件状态管理思路参考（React 栈，不引入）
- xtekky/chatgpt-clone：像素比例参考

---

文档生成于 2026-10-27，基于当日联网调研与源码核对。数值以 LibreChat main 分支 defaults.css 为准，后续如上游更新可重新拉取核对。

