# pdf2skill

> 将非结构化文档（PDF/EPUB/TXT）转化为大模型可调用的结构化 Agent Skill 文件。

---

## 核心思路

```
PDF / EPUB / TXT
      │
      ▼
  MinerU 版面解析 → Markdown
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
pip install openai fastapi uvicorn ebooklib numpy
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

### 3. 启动 Web UI

```bash
uvicorn src.web_ui:app --reload --port 8000
```

打开 `http://localhost:8000`，三步完成：

1. **上传文档** → 自动识别文档类型 + 生成 Schema
2. **预览采样** → 抽取 5 个 chunk 进行试提取，确认质量
3. **全量执行** → SSE 实时进度推送，支持断点续传

### 4. CLI 模式

```bash
python -m src.pipeline 你的文档.pdf "书名" --max-chunks 50
```

---

## 项目结构

```
src/
├── config.py           # 全局配置（.env 自动加载）
├── document_loader.py  # 多格式加载器（PDF/EPUB/TXT → Markdown）
├── markdown_chunker.py # AST 语义切分（三级降级策略）
├── semantic_filter.py  # 语义密度粗筛（双通道评估）
├── schema_generator.py # Phase 0: R1 动态 Schema 推断
├── skill_extractor.py  # Phase 2: 约束提取（5 种文档策略）
├── skill_validator.py  # 三重校验（格式/完整性/幻觉）
├── skill_reducer.py    # Phase 3: 向量去重 + R1 合并
├── skill_packager.py   # Phase 4: .md 文件输出 + ZIP 打包
├── llm_client.py       # DeepSeek R1 客户端（同步/异步 + 重试）
├── pipeline.py         # Pipeline 主流程编排器
├── session_store.py    # 文件化会话管理（断点续传）
├── web_ui.py           # FastAPI Web UI（三阶段交互）
├── prompt_loader.py    # Prompt 模板加载器
├── batch_runner.py     # 批量处理入口
└── log_analyzer.py     # JSONL 调用日志分析
prompts/                # Prompt 模板（按文档类型/版本管理）
docs/                   # 架构文档
tests/                  # 单元测试
```

---

## Pipeline 阶段

| 阶段   | 名称           | 模型        | 说明                                     |
| ------ | -------------- | ----------- | ---------------------------------------- |
| **0**  | Schema Genesis | R1          | TOC + 前言 → 推断 Skill Schema 模板      |
| **1A** | 版面降维       | —           | MinerU PDF → Markdown，噪音清洗          |
| **1B** | AST 切分       | —           | 按标题层级切分，父级上下文注入           |
| **1C** | 语义粗筛       | 低成本模型  | 双通道评估，丢弃低密度块                 |
| **2**  | 约束提取       | R1          | Schema + 文本块 → 结构化 Skill，并行 Map |
| **3**  | 去重合并       | R1 + bge-m3 | 向量聚类 + R1 Reduce/Critic              |
| **4**  | 打包输出       | —           | .md 文件集 + index.md + ZIP              |

---

## 支持的文档类型

提取策略根据文档类型自动匹配：

| 类型     | 提取策略                     | 示例           |
| -------- | ---------------------------- | -------------- |
| 技术手册 | 操作步骤、排错流程、配置方法 | K8s 运维手册   |
| 操作规范 | SOP、安全规范、审批流程      | 质量管理手册   |
| 学术论文 | 概念框架、方法论、模型定义   | 计算机科学论文 |
| 叙事类   | 关键事件、决策点、因果链     | 商业案例、小说 |
| 方法论   | 原理公式、分析框架、评估模型 | 投资学教材     |

---

## 测试

```bash
python -m pytest tests/ -v
```

---

## 成本参考

处理一本 300 页技术书籍约 **¥8-10**（DeepSeek R1 定价）。

---

## 详细架构

参见 [docs/架构.md](docs/架构.md)。
