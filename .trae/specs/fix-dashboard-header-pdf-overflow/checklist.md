# Checklist

- [x] `.pdf-export-btn` 的 `min-width` 已从 76px 收紧到 48px
- [x] 按钮文案从 `导出PDF` 改为图标 + 短文本 `📄 PDF`
- [x] `.pdf-export-btn` 增加 `flex-shrink: 0` 防止被压缩
- [x] `.panel-head h3` 已应用 `min-width: 0; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap`
- [x] `.section-head h2` 已应用同样的弹性截断规则
- [x] 新增 `.section-title` 容器包裹 h2 + p，整体 `flex: 1; min-width: 0`
- [x] section-head 中的 p 在窄屏下也能省略号截断
- [x] ≤760px 断点下按钮仅显示图标（width: 32px），aria-label="导出 PDF"
- [ ] 1440 / 1100 / 900 / 600px 四个视口下所有模块标题保持单行（待用户本地起服务后目视确认）
- [ ] 1440 / 1100 / 900 / 600px 四个视口下 PDF 按钮完整可见、未与标题重叠（待用户本地起服务后目视确认）
- [x] 任意模块点击 PDF 按钮仍能成功下载对应 PDF（`exportModulePdf` 主体逻辑未改动）
- [x] 按钮 disabled 期间仍显示 `生成中…` 状态（`textNode.textContent='生成中…'` 保留）
