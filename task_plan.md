# pdf2skill — 开发任务

## ✅ 已完成

### NotebookLM 风格 UI 改造

- [x] 三栏 flexbox 布局（来源/知识提取/Studio）
- [x] React 组件化（SourcePanel / StudioPanel / WorkflowPanel）
- [x] 文件上传 + chunk 浏览 + 调优 + SSE 全量执行
- [x] 工作流画布（ReactFlow）
- [x] session 恢复 + 视觉打磨

### API 架构重构

- [x] web_ui.py 拆分为 5 个 APIRouter 模块（1673→40 行）
- [x] session→notebook 全栈统一重命名
- [x] notebook_store.py 替代 session_store.py
- [x] 前端 useNotebook hook 替代 useSession
- [x] 删除 HTML 服务代码（前端由 Vite 独立提供）

### 代码质量优化

- [x] deps.py: DI 依赖注入（NotebookDep）
- [x] schemas.py: Pydantic V2 请求模型（6 个）
- [x] 公共函数抽取（\_validate_batch / \_skill_summary）
- [x] 兼容别名清理（FileSession / list_sessions）
- [x] config.py 修复（sessions/ → notebooks/）

### n8n 工作流引擎复刻

- [x] workflow_types.py — n8n 类型系统
- [x] workflow_engine.py — BFS 执行栈 + 多输出分支 + 错误路由 + pinData
- [x] api_workflow.py — SSE 流式执行 + 同步端点 + pin-data CRUD
- [x] WorkflowPanel.jsx — NDV 侧抽屉 + NodePalette（⌘K）+ 右键菜单 + 执行动画
- [x] NodeDrawer.jsx — Params/Input/Output 三标签页
- [x] NodePalette.jsx — 分类式节点选择面板

### 笔记本目录合并 + 上传增强

- [x] upload + notebook 合并为统一自包含目录（upload/text/prompt/skills）
- [x] SHA-256 文件去重（跳过重复上传）
- [x] LLM 格式整理（"除了错别字什么都别改" + 版式）
- [x] text 文件以源文件命名（{filename}.raw.md / {filename}.md）
- [x] 各节点 prompt 自动保存到 prompt/ 子目录

## 📋 待定

（无当前待办）
