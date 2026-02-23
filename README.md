# pdf2skill

> 将非结构化文档（PDF/EPUB/TXT/DOCX/Excel）转化为大模型可调用的结构化 Agent Skill 文件。

---

## 核心思路

```
PDF / EPUB / TXT / DOCX / Excel
      │
      ▼
  MinerU 版面解析 → Markdown
      │
      ▼
  LLM 格式整理（只改错别字 + 版式）
      │
      ▼
  AST 语义切分 → 语义密度粗筛
      │
      ▼
  DeepSeek R1 约束提取（Schema-First）
      │
      ▼
  向量去重 + R1 合并审查
      │
      ▼
  .md Skill 文件集 + index.md 路由表
```

每个 Skill 对应一个**完整的决策流程**（给定场景 → 2-5 步判断 → 可执行结论），而非函数调用（太细）或整书摘要（太粗）。

---

## 快速开始

### 1. 环境准备

```bash
# Python 3.11+
pip install openai fastapi uvicorn ebooklib numpy python-docx openpyxl

# 前端
cd frontend && npm install
```

### 2. 配置 API 凭证

```bash
cp .env.example .env
# 编辑 .env，填入你的 DeepSeek R1 API 地址和密钥
```

`.env` 示例：

```ini
LLM_BASE_URL=http://your-api-host:5000/v1
LLM_API_KEY=sk-your-api-key
LLM_MODEL=DeepSeek-R1
```

### 3. 启动

```bash
# 后端
uvicorn src.web_ui:app --reload --port 8000

# 前端（另一个终端）
cd frontend && npm run dev -- --host
```

### 4. 使用流程

1. **上传文档** → SHA-256 去重 → LLM 格式整理 → 自动 Schema 生成
2. **预览采样** → 抽取 chunk 试提取，调优 Prompt
3. **全量执行** → SSE 实时推送，支持断点续传

### 5. CLI 模式

```bash
python -m src.pipeline 你的文档.pdf "书名" --max-chunks 50
```

---

## 项目结构

```
src/
├── web_ui.py           # FastAPI 入口（include 5 个 router）
├── deps.py             # DI 依赖注入
├── schemas.py          # Pydantic V2 请求模型
├── config.py           # 全局配置（.env 自动加载）
├── api_analyze.py      # 上传/分析（SHA-256 去重 + LLM 格式整理）
├── api_tune.py         # chunk 调优/抽验
├── api_execute.py      # SSE 全量执行/断点续传
├── api_skills.py       # Skills CRUD/注册表/图谱/检索
├── api_workflow.py     # 工作流执行（SSE/同步）/save/load/pin-data
├── notebook_store.py   # 笔记本自包含存储（upload/text/prompt/skills）
├── workflow_engine.py  # n8n 式 BFS 工作流引擎
├── workflow_types.py   # 工作流类型系统
├── document_loader.py  # 多格式加载器（PDF/TXT/EPUB → Markdown）
├── markdown_chunker.py # AST 语义切分（三级降级）
├── semantic_filter.py  # 语义密度粗筛
├── schema_generator.py # R1 动态 Schema 推断
├── skill_extractor.py  # Schema + 文本块 → R1 → Raw Skill
├── skill_validator.py  # 三重校验（格式/完整性/幻觉）
├── skill_reducer.py    # 向量去重 + R1 合并
├── skill_packager.py   # .md 输出 + ZIP 打包
├── llm_client.py       # DeepSeek R1 客户端（同步/异步 + 重试）
└── pipeline.py         # Pipeline 主流程编排器

frontend/src/           # React + Vite + ReactFlow
├── components/
│   ├── WorkflowPanel   # n8n 式工作流画布
│   ├── NodeDrawer      # 节点参数侧抽屉（NDV）
│   ├── NodePalette     # 节点选择面板（⌘K）
│   └── ...             # SourcePanel / WorkPanel / StudioPanel
```

---

## 笔记本目录结构

每个笔记本自包含所有文件：

```
notebooks/{id}/
  meta.json              # 文档元信息
  workflow.json           # 工作流定义
  status.json             # 执行进度
  file_hashes.json        # SHA-256 去重索引
  upload/                 # 源文件（PDF/TXT/EPUB，原样保留）
  text/                   # 处理后文本
    {filename}.raw.md     # document_loader 直出
    {filename}.md         # LLM 格式整理后（只改错别字 + 版式）
    chunks.json           # 切分后文本块
    schema.json           # R1 推断的 Schema
  prompt/                 # 各流程节点的提示词
    system_prompt.md      # Schema 生成提示词
    extraction_hint.md    # 提取策略
    tune_history.json     # 调优历史
  skills/                 # 提取的 Skill 文件
```

---

## Pipeline 阶段

| 阶段   | 名称           | 模型        | 说明                                     |
| ------ | -------------- | ----------- | ---------------------------------------- |
| **0**  | Schema Genesis | R1          | TOC + 前言 → 推断 Skill Schema 模板      |
| **1A** | 版面降维       | —           | MinerU PDF → Markdown，噪音清洗          |
| **1B** | LLM 格式整理   | R1          | 只改错别字 + 版式整理，保留全部原始内容  |
| **1C** | AST 切分       | —           | 按标题层级切分，父级上下文注入           |
| **1D** | 语义粗筛       | 低成本模型  | 双通道评估，丢弃低密度块                 |
| **2**  | 约束提取       | R1          | Schema + 文本块 → 结构化 Skill，并行 Map |
| **3**  | 去重合并       | R1 + bge-m3 | 向量聚类 + R1 Reduce/Critic              |
| **4**  | 打包输出       | —           | .md 文件集 + index.md + ZIP              |

---

## 测试

```bash
python -m pytest tests/ -v   # 60 tests
```

---

## 成本参考

处理一本 300 页技术书籍约 **¥8-10**（DeepSeek R1 定价）。

---

## 详细架构

参见 [docs/架构.md](docs/架构.md)。
