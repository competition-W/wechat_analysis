# Tasks

- [x] Task 1: 收紧 PDF 按钮尺寸与文本（CSS）
  - 调整 `.pdf-export-btn`：`min-width: 76px → 48px`，padding 由 `0 10px` 收为 `0 8px`
  - 按钮文字由 `导出PDF` 改为 `📄 PDF`（图标 + 短文本）
  - 增加 `:focus-visible` 与悬停 `title` 提示保证可访问性
  - 在 ≤760px 断点下进一步压缩到仅图标（`width: 32px; padding: 0`），`aria-label="导出 PDF"`
  - 涉及文件：[index.html:52](file:///c:/Users/wjz/Documents/Codex/2026-07-02/llm-github-plugin-github-openai-api/repo_src/api/static/index.html#L52)

- [x] Task 2: 标题区增加弹性截断（CSS）
  - `.panel-head h3` 与 `.section-head h2` 增加：
    - `min-width: 0; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap`
  - 新增 `.section-title` 容器包裹 h2 + p，`flex: 1; min-width: 0`，p 在窄屏同样省略号截断
  - `.pdf-export-btn` 自身增加 `flex-shrink: 0` 保证不被压缩
  - 涉及文件：[index.html:13](file:///c:/Users/wjz/Documents/Codex/2026-07-02/llm-github-plugin-github-openai-api/repo_src/api/static/index.html#L13)、[index.html:52](file:///c:/Users/wjz/Documents/Codex/2026-07-02/llm-github-plugin-github-openai-api/repo_src/api/static/index.html#L52)

- [x] Task 3: 改造 `installPdfExportButtons` 创建按钮的 DOM 结构（JS）
  - 创建按钮时使用图标 + 文本混合 DOM（`<span class="pdf-export-icon">📄</span><span class="pdf-export-text">PDF</span>`）
  - 同步设置 `aria-label` 与 `title` 提供完整语义
  - 不改 `exportModulePdf` 调用逻辑
  - 涉及文件：[index.html:119](file:///c:/Users/wjz/Documents/Codex/2026-07-02/llm-github-plugin-github-openai-api/repo_src/api/static/index.html#L119)

- [x] Task 4: 验证
  - [x] Code review 通过：[index.html:52](file:///c:/Users/wjz/Documents/Codex/2026-07-02/llm-github-plugin-github-openai-api/repo_src/api/static/index.html#L52) `.pdf-export-btn` `min-width:48px / flex-shrink:0 / width:32px` 在 760px 断点生效
  - [x] Code review 通过：[index.html:52](file:///c:/wjz/Documents/Codex/2026-07-02/llm-github-plugin-github-openai-api/repo_src/api/static/index.html#L52) `.section-title / .panel-title / .section-head h2 / .panel-head h3` 已应用 `min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap`
  - [x] Code review 通过：[index.html:75-103](file:///c:/wjz/Documents/Codex/2026-07-02/llm-github-plugin-github-openai-api/repo_src/api/static/index.html#L75-L103) 所有 `.section-head` 已用 `.section-title` 包裹 h2 + p
  - [x] Code review 通过：[index.html:119](file:///c:/wjz/Documents/Codex/2026-07-02/llm-github-plugin-github-openai-api/repo_src/api/static/index.html#L119) `installPdfExportButtons` 创建 icon+text DOM，`aria-label` & `title` 完整
  - [x] Code review 通过：[index.html:118](file:///c:/wjz/Documents/Codex/2026-07-02/llm-github-plugin-github-openai-api/repo_src/api/static/index.html#L118) `exportModulePdf` 中 `textNode.textContent='生成中…'` 保留 PDF 导出逻辑
  - [ ] Live verify：用户需在本地起服务（`uvicorn api.main:app --port 8000`），在 1440 / 1100 / 900 / 600px 视口下打开 `http://localhost:8000/`，目视检查每个模块的标题是否单行、PDF 按钮是否完整可见，点击 PDF 按钮验证导出
  - 涉及文件：[index.html](file:///c:/wjz/Documents/Codex/2026-07-02/llm-github-plugin-github-openai-api/repo_src/api/static/index.html)

# Task Dependencies
- [Task 2] depends on [Task 1]（需要先确定按钮尺寸，再设置 flex 收缩）
- [Task 3] depends on [Task 1]（DOM 结构与 CSS 类名保持一致）
- [Task 4] depends on [Task 1, Task 2, Task 3]
