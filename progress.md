# 进度记录

## 2026-02-23 n8n 工作流引擎 + 目录合并

### 执行结果

| 步骤               | 状态 | 详情                                                              |
| ------------------ | ---- | ----------------------------------------------------------------- |
| workflow_types.py  | ✅   | n8n 类型系统（Connection / NodeExecutionData / ExecutionContext） |
| workflow_engine.py | ✅   | BFS 执行栈 + waiting 队列 + 多输出分支 + pinData + 错误路由       |
| api_workflow.py    | ✅   | SSE 流式 + 同步双端点 + pin-data CRUD                             |
| NodeDrawer.jsx     | ✅   | NDV 侧抽屉（Params / Input / Output 三标签页）                    |
| NodePalette.jsx    | ✅   | 节点选择面板（分类 + 搜索 + ⌘K 快捷键）                           |
| WorkflowPanel.jsx  | ✅   | 集成 NDV、右键菜单、执行动画、多端口 Handle                       |
| notebook_store.py  | ✅   | 4 子目录（upload/text/prompt/skills）+ SHA-256 去重               |
| api_analyze.py     | ✅   | 上传去重 → LLM 格式整理（只改错别字+版式）→ clean text            |
| text 文件命名      | ✅   | `{filename}.raw.md` / `{filename}.md` 与源文件同名                |
| config.py          | ✅   | 删除独立 uploads 目录                                             |
| .gitignore         | ✅   | notebooks/_ 替换 uploads/_                                        |
| test (backend)     | ✅   | 60/60 全部通过                                                    |
| frontend build     | ✅   | 198 模块，0 错误                                                  |
| README.md          | ✅   | 全面更新                                                          |
| AGENTS.md          | ✅   | 同步更新                                                          |

### 阻碍

无

---

## 2026-02-23 session→notebook 重命名 + web_ui.py 拆分

### 执行结果

| 步骤              | 状态 | 详情                               |
| ----------------- | ---- | ---------------------------------- |
| notebook_store.py | ✅   | FileNotebook + INDEX.md + 兼容别名 |
| api_analyze.py    | ✅   | 180 行                             |
| api_tune.py       | ✅   | 180 行                             |
| api_execute.py    | ✅   | 200 行                             |
| api_skills.py     | ✅   | 160 行                             |
| api_workflow.py   | ✅   | 70 行                              |
| web_ui.py 精简    | ✅   | 1673 → 40 行                       |
| 前端 useNotebook  | ✅   | Hook 重命名 + localStorage key     |
| npm build         | ✅   | 611ms                              |
| 后端加载          | ✅   | 27 routes                          |
| 数据迁移          | ✅   | sessions/ → notebooks/             |
| AGENTS.md         | ✅   | 同步更新                           |
| git commit        | ✅   | d03c7a6                            |

### 阻碍

无
