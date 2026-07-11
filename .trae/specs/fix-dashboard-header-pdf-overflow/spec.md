# 看板模块头部"导出 PDF"按钮挤压标题 Spec

## Why
在每个看板模块（`.panel` / `.section-head`）的右上角新增"导出 PDF"按钮后，按钮的最小宽度（76px + 内边距 = 96px）与原面板头部标题、tabs 等控件竞争 flex 空间，导致：
- 中等宽度视口下统计标题被挤到第二行（折行）
- 视觉重心从右上角偏移到中间
- 头部区域整洁度下降

需在不改变"每个模块独立导出"核心能力的前提下，恢复头部排版的整洁与单行结构。

## What Changes
- **紧凑 PDF 按钮**：`min-width` 从 76px 降为 48px，文案从"导出PDF"改为图标 + 短文本（`📄 PDF`），悬停提示保留完整语义。
- **标题优雅截断**：`.panel-head h3` / `.section-head h2` 增加 `min-width:0; flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap`，保证永远不折行；长标题以省略号结尾，悬停通过 `title` 属性显示完整文本。
- **section-head 布局优化**：将 h2+p 用一个弹性容器（`.section-title`）包起来，容器获得 `min-width:0; flex:1`，按钮 `flex-shrink:0` 固定在右侧，确保按钮和标题不互相挤压。
- **响应式微调**：≤1180px 与 ≤760px 两个断点下，按钮 padding 略缩，文字隐藏仅留图标，腾出更多空间给标题。
- 不改动 PDF 导出功能逻辑（`exportModulePdf` / `installPdfExportButtons` 行为不变），仅调整 DOM 创建与 CSS。

## Impact
- Affected specs: 看板前端展示（`api/static/index.html`）
- Affected code:
  - [index.html:52](file:///c:/Users/wjz/Documents/Codex/2026-07-02/llm-github-plugin-github-openai-api/repo_src/api/static/index.html#L52) — `.pdf-export-btn` 样式
  - [index.html:13](file:///c:/Users/wjz/Documents/Codex/2026-07-02/llm-github-plugin-github-openai-api/repo_src/api/static/index.html#L13) — `.panel-head` / `.section-head` 容器样式
  - [index.html:119](file:///c:/Users/wjz/Documents/Codex/2026-07-02/llm-github-plugin-github-openai-api/repo_src/api/static/index.html#L119) — `installPdfExportButtons` 创建按钮逻辑

## ADDED Requirements

### Requirement: 标题永远不折行
模块头部的 `<h3>` 标题在任何视口下必须保持单行显示；当内容超过容器宽度时，以省略号（`...`）截断，悬停通过原生 `title` 属性展示完整文本。

#### Scenario: 标题长度超过可用空间
- **WHEN** 视口宽度为 1100px，模块标题为"区域业务结构（含销售员/售后员/产品类别三个维度）"
- **THEN** 标题以单行 + 省略号显示，title 提示显示完整文本
- **AND** 视觉重心保持在头部左侧，不出现两行

#### Scenario: 短标题在窄屏
- **WHEN** 视口宽度为 600px，模块标题为"群聊消息趋势"
- **THEN** 标题仍为单行，不与 PDF 按钮重叠
- **AND** PDF 按钮位于最右侧完整可见

### Requirement: 紧凑 PDF 按钮
PDF 导出按钮在不损失可识别性的前提下缩小占位：宽屏（≥1180px）显示"📄 PDF"，中等屏（760~1180px）显示"📄 PDF"但缩小 padding，窄屏（≤760px）只显示图标"📄"。

#### Scenario: 宽屏显示
- **WHEN** 视口 ≥ 1180px
- **THEN** 按钮显示"📄 PDF"完整文字 + 图标
- **AND** `min-width: 48px`，padding `0 10px`

#### Scenario: 窄屏仅图标
- **WHEN** 视口 ≤ 760px
- **THEN** 按钮仅显示"📄"图标
- **AND** `width: 32px; padding: 0`
- **AND** 通过 `aria-label` 与 `title` 提供"导出 PDF"语义

### Requirement: section-head 标题区不挤压
`.section-head` 中的 `<h2>` 与说明 `<p>` 被视为一个弹性单元（新增 `.section-title` 容器），获得 `flex:1; min-width:0`；按钮 `flex-shrink:0` 锁定在右侧。

#### Scenario: 中等视口 section-head
- **WHEN** 视口 900px，section-head 含 `<h2>经营摘要</h2><p>当前周期的服务规模与业务覆盖</p>` 与 PDF 按钮
- **THEN** h2 + p 作为整体在左侧，按钮在右侧
- **AND** h2 永远单行，p 在宽度允许时单行、否则省略号

### Requirement: PDF 导出功能不受影响
原 `exportModulePdf` 的导出行为、文件名、按钮 disabled/loading 文案、错误处理不变。

#### Scenario: 点击 PDF 按钮
- **WHEN** 用户点击任意模块的 PDF 按钮
- **THEN** 触发原 `exportModulePdf` 流程，下载对应模块的 PDF
- **AND** 按钮 disabled 期间显示"生成中…"

## MODIFIED Requirements
（无 — 既有功能保持原样）

## REMOVED Requirements
（无）
