# 进度日志

## 2026-02-22

### 会话 a9180ce5：Web UI 四阶段重设计

**目标**：将三阶段预览流程重设计为四阶段（上传→深度调优→随机抽样→全量执行）

**完成项**：

1. **布局调整**：分析结果面板拆分为固定信息上排 + 可调项下排 + 摘要区
2. **后端 API**：
   - `skill_extractor.py` 增加 `prompt_hint` 参数（同步/异步）
   - `session_store.py` 增加调优版本链（`tune_history.json`）
   - `web_ui.py` 新增 4 个 API：`/api/chunks`、`/api/tune`、`/api/tune-history`、`/api/sample-check`
   - `execute` 端点自动使用最后确认的 `prompt_hint`
3. **前端重写**：四阶段卡片布局、chunk 选择器、左右双栏（原文|提取结果）对比、prompt 调优输入框、版本时间线回溯、随机抽样验证面板
4. **API 返回增强**：新增 `core_components`、`skill_types` 字段，`save_meta` 持久化

**测试**：pytest 19/19 通过 | Python 语法检查通过 | 浏览器 UI 验证通过

---

### 会话 a9180ce5（续）：Chunker 优化 + 左右分栏 + SKU 知识库工厂

**目标**：(1) 优化 chunk 切分策略 (2) 左右分栏布局 (3) 演进为 SKU 知识库工厂

**完成项**：

1. **Chunker 优化** (`markdown_chunker.py`)：
   - 自适应 `split_level`：遍历 level 2→6，选平均块大小最接近 1500 字的层级
   - 对话体检测：Turn/User/Assistant 模式自动按轮次切分
   - `split_level=0` 为新默认值（自动检测）
2. **前端左右分栏** (`web_ui.py`)：
   - 左栏 (42%)：上传 + 文档摘要 + 可搜索 chunk 列表
   - 右栏 (58%)：prompt 编辑 + 系统 prompt + 提取结果 + 版本历史 + 全量执行
   - 移除 phase 卡片，改为流式操作
3. **SKU 知识库工厂** (Phase 1-6)：
   - P1 数据模型：`SKUType` 枚举 + `sku_type`/`sku_id` 字段
   - P2 三通道 Prompt：`factual`/`procedural`/`relational`/`eureka` 四模板
   - P3 SKU 分类器：`sku_classifier.py`（规则优先 + 启发式兜底）
   - P4 包装器重写：`workspace/skus/{type}/{id}/{header,content}.md` + `mapping.md` + `eureka.md`
   - P5 Pipeline 集成：Phase 3.5 分类阶段 + `sku_stats` 统计
   - P6 Web UI 展示：SSE 事件传 SKU 分布 + 卡片带类型标签颜色

**Commits**：`7dab251`, `07e6b00`, `55b23c0`

**测试**：Python 语法检查全通过 | 浏览器 UI 验证通过
