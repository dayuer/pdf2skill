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

---

## 2026-02-23 session→workflow 统一 + LLM 分块 + 队列化

### 执行结果

| 步骤                  | 状态 | 详情                                            | Commit  |
| --------------------- | ---- | ----------------------------------------------- | ------- |
| 上传 bug 修复         | ✅   | batchUpload 无 workflowId 时自动创建工作流      | 9da8720 |
| nb→wf NameError       | ✅   | batch_upload 6 处 nb→wf 修复 500 崩溃           | 5d68a09 |
| 统一上传路径          | ✅   | 消灭遗留 uploadFile/onUpload 单文件通道         | 8b46a1b |
| session→workflow 清理 | ✅   | 9 文件 40+/190- 行，零 session 残留             | 53e4a75 |
| UI 文件名不截断       | ✅   | source-file-name 移除 ellipsis                  | 70a7b64 |
| 分块信息移除          | ✅   | SourcePanel 移除 chunks 显示区域                | 381c393 |
| 左栏可拖宽到 65%      | ✅   | handleLeftResize max=65% viewport               | b7b2c60 |
| LLM 语义分块          | ✅   | chunker_v0.1.md prompt + DeepSeek R1 调用       | 837db02 |
| 后台分块 + SSE        | ✅   | POST 入队立即返回 + GET SSE 推送进度            | 3244b47 |
| asyncio.Queue 队列    | ✅   | 串行消费者，60s 空闲退出，无 Redis 依赖         | 6c51e16 |
| chunk/ 目录           | ✅   | workflow_store 新增 chunk_dir（与 text 平级）   | 3244b47 |
| meta.json files 列表  | ✅   | save_meta 增加 files 字段 + 保留 created_at     | 3416e85 |
| dev.sh 管理脚本       | ✅   | start/stop/restart/status + python3.11 绝对路径 | eaebbf3 |
| AGENTS.md 同步更新    | ✅   | 全面替换（路由表/目录结构/术语统一）            | 本次    |

### 阻碍

无
