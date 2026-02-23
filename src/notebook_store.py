"""
笔记本持久化管理 — Notebook Store

每个笔记本对应 notebooks/{notebook_id}/ 目录，包含四个子目录：

  upload/              — 源文件（PDF/TXT/EPUB，原样保留）
  text/                — 处理后的文本
    raw.md             — 原始 Markdown（document_loader 直出）
    clean.md           — LLM 格式整理后的 Markdown
    chunks.json        — 切分后文本块
    schema.json        — R1 推断的 Schema
  prompt/              — 各流程节点的提示词
    system_prompt.md   — Schema 生成提示词
    extraction_hint.md — 提取策略
    tune_history.json  — 调优历史
  skills/              — 提取的 Skill 文件

根目录文件：
  meta.json            — 文档元信息
  status.json          — 处理进度
  workflow.json        — 工作流定义
  progress_index.json  — 断点续传索引
  pin_data.json        — 固定数据（调试用）
  file_hashes.json     — 已上传文件的 SHA-256 去重索引

根目录包含 INDEX.md 索引文件。
服务器重启后可从磁盘恢复所有笔记本。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path


from .markdown_chunker import TextChunk
from .schema_generator import SkillSchema
from .skill_validator import ValidatedSkill, ValidationStatus


_NOTEBOOKS_DIR = Path("notebooks")
_NOTEBOOKS_DIR.mkdir(exist_ok=True)

# 子目录名称常量
_SUB_UPLOAD = "upload"
_SUB_TEXT = "text"
_SUB_PROMPT = "prompt"
_SUB_SKILLS = "skills"


def _rebuild_index() -> None:
    """重建根目录 INDEX.md 索引"""
    lines = ["# 笔记本索引\n\n"]
    lines.append("| ID | 文档名 | 类型 | 领域 | 块数 | Skills | 创建时间 |\n")
    lines.append("|---|---|---|---|---|---|---|\n")
    for d in sorted(_NOTEBOOKS_DIR.iterdir()):
        if d.is_dir() and (d / "meta.json").exists():
            try:
                meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
                skills_dir = d / _SUB_SKILLS
                skills_count = len(list(skills_dir.glob("*.json"))) if skills_dir.exists() else 0
                created = time.strftime("%Y-%m-%d %H:%M", time.localtime(meta.get("created_at", 0)))
                lines.append(
                    f"| `{d.name}` | {meta.get('doc_name', '')} | "
                    f"{meta.get('format', '')} | {', '.join(meta.get('domains', []))} | "
                    f"{meta.get('filtered_chunks', 0)}/{meta.get('total_chunks', 0)} | "
                    f"{skills_count} | {created} |\n"
                )
            except (json.JSONDecodeError, OSError):
                continue
    (_NOTEBOOKS_DIR / "INDEX.md").write_text("".join(lines), encoding="utf-8")


class FileNotebook:
    """文件化的笔记本 — 自包含所有文件和子目录"""

    def __init__(self, notebook_id: str) -> None:
        self.notebook_id = notebook_id
        self._dir = _NOTEBOOKS_DIR / notebook_id
        self._dir.mkdir(parents=True, exist_ok=True)
        # 自动创建子目录
        for sub in (_SUB_UPLOAD, _SUB_TEXT, _SUB_PROMPT, _SUB_SKILLS):
            (self._dir / sub).mkdir(exist_ok=True)

    @property
    def root(self) -> Path:
        return self._dir

    @property
    def upload_dir(self) -> Path:
        return self._dir / _SUB_UPLOAD

    @property
    def text_dir(self) -> Path:
        return self._dir / _SUB_TEXT

    @property
    def prompt_dir(self) -> Path:
        return self._dir / _SUB_PROMPT

    @property
    def skills_dir(self) -> Path:
        return self._dir / _SUB_SKILLS

    # ──── 文件去重 ────

    def file_hash(self, data: bytes) -> str:
        """计算文件 SHA-256"""
        return hashlib.sha256(data).hexdigest()

    def is_duplicate(self, file_hash: str) -> bool:
        """检查文件是否已上传过"""
        hashes = self._load_hashes()
        return file_hash in hashes

    def register_file(self, filename: str, file_hash: str) -> None:
        """注册已上传文件的哈希"""
        hashes = self._load_hashes()
        hashes[file_hash] = {
            "filename": filename,
            "uploaded_at": time.time(),
        }
        self._write_json("file_hashes.json", hashes)

    def _load_hashes(self) -> dict:
        return self._read_json("file_hashes.json") or {}

    def list_uploads(self) -> list[dict]:
        """列出所有已上传文件"""
        files = []
        for f in sorted(self.upload_dir.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                files.append({
                    "name": f.name,
                    "size": f.stat().st_size,
                    "path": str(f),
                })
        return files

    # ──── 写入 ────

    def save_meta(
        self,
        *,
        doc_name: str,
        format: str,
        filepath: str,
        book_type: str = "",
        domains: list[str] | None = None,
        total_chunks: int = 0,
        filtered_chunks: int = 0,
        prompt_type: str = "",
        core_components: list[str] | None = None,
        skill_types: list[str] | None = None,
    ) -> None:
        """保存文档元信息"""
        data = {
            "notebook_id": self.notebook_id,
            "doc_name": doc_name,
            "format": format,
            "filepath": filepath,
            "book_type": book_type,
            "domains": domains or [],
            "total_chunks": total_chunks,
            "filtered_chunks": filtered_chunks,
            "prompt_type": prompt_type,
            "core_components": core_components or [],
            "skill_types": skill_types or [],
            "created_at": time.time(),
        }
        self._write_json("meta.json", data)
        _rebuild_index()

    def save_schema(self, schema: SkillSchema) -> None:
        """保存 Schema → text/schema.json"""
        data = {
            "book_type": schema.book_type,
            "domains": schema.domains,
            "constraint": schema.to_prompt_constraint(),
        }
        self._write_json(f"{_SUB_TEXT}/schema.json", data)

    def save_chunks(self, chunks: list[TextChunk]) -> None:
        """保存文本块列表 → text/chunks.json"""
        data = [
            {
                "index": c.index,
                "content": c.content,
                "context": c.context,
                "heading_path": c.heading_path,
                "char_count": c.char_count,
            }
            for c in chunks
        ]
        self._write_json(f"{_SUB_TEXT}/chunks.json", data)

    def save_raw_text(self, markdown: str, source_name: str = "") -> None:
        """保存原始 Markdown → text/{stem}.raw.md"""
        name = Path(source_name).stem if source_name else "raw"
        (self.text_dir / f"{name}.raw.md").write_text(markdown, encoding="utf-8")
        # 记录文件名映射
        self._write_json("text/_filemap.json", {"stem": name})

    def save_clean_text(self, markdown: str, source_name: str = "") -> None:
        """保存 LLM 整理后的文本 → text/{stem}.md"""
        name = self._get_text_stem() if not source_name else Path(source_name).stem
        (self.text_dir / f"{name}.md").write_text(markdown, encoding="utf-8")

    def save_skill(self, skill: ValidatedSkill, idx: int) -> None:
        """保存单个 Skill → skills/xxxx_name.json"""
        data = {
            "name": skill.name,
            "trigger": skill.trigger,
            "domain": skill.domain,
            "prerequisites": skill.prerequisites,
            "source_ref": skill.source_ref,
            "confidence": skill.confidence,
            "body": skill.body,
            "raw_text": skill.raw_text,
            "status": skill.status.value if hasattr(skill.status, 'value') else str(skill.status),
            "warnings": skill.warnings,
            "source_chunk_index": skill.source_chunk_index,
            "source_context": skill.source_context,
        }
        path = self.skills_dir / f"{idx:04d}_{self._safe_name(skill.name)}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ──── Prompt 管理 ────

    def save_prompt(self, node_type: str, content: str) -> None:
        """保存节点提示词 → prompt/{node_type}.md"""
        path = self.prompt_dir / f"{node_type}.md"
        path.write_text(content, encoding="utf-8")

    def load_prompt(self, node_type: str) -> str | None:
        """读取节点提示词"""
        path = self.prompt_dir / f"{node_type}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None

    # ──── Checkpoint Index（断点续传） ────

    def mark_chunks_done(self, chunk_indices: list[int]) -> None:
        """标记一批 chunk 已处理完毕"""
        idx_file = self._dir / "progress_index.json"
        existing = self._load_progress_index()
        existing.update(chunk_indices)
        idx_file.write_text(json.dumps(sorted(existing), ensure_ascii=False), encoding="utf-8")

    def get_pending_chunk_indices(self, total: int) -> list[int]:
        done = self._load_progress_index()
        return [i for i in range(total) if i not in done]

    def get_done_count(self) -> int:
        return len(self._load_progress_index())

    def _load_progress_index(self) -> set[int]:
        idx_file = self._dir / "progress_index.json"
        if not idx_file.exists():
            return set()
        try:
            data = json.loads(idx_file.read_text(encoding="utf-8"))
            return set(data) if isinstance(data, list) else set()
        except (json.JSONDecodeError, OSError):
            return set()

    def save_status(
        self,
        *,
        phase: str,
        completed: int = 0,
        total: int = 0,
        raw_skills: int = 0,
        passed: int = 0,
        failed: int = 0,
        final_skills: int = 0,
        elapsed_s: float = 0,
    ) -> None:
        """保存/更新处理进度"""
        data = {
            "phase": phase,
            "completed": completed,
            "total": total,
            "raw_skills": raw_skills,
            "passed": passed,
            "failed": failed,
            "final_skills": final_skills,
            "elapsed_s": round(elapsed_s, 1),
            "updated_at": time.time(),
        }
        self._write_json("status.json", data)

    # ──── 读取 ────

    def load_meta(self) -> dict | None:
        return self._read_json("meta.json")

    def load_schema(self) -> dict | None:
        # 新路径优先，回退旧路径
        return self._read_json(f"{_SUB_TEXT}/schema.json") or self._read_json("schema.json")

    def load_chunks(self) -> list[TextChunk]:
        # 新路径优先，回退旧路径
        data = self._read_json(f"{_SUB_TEXT}/chunks.json") or self._read_json("chunks.json")
        if not data:
            return []
        return [self._dict_to_chunk(c) for c in data]

    def load_chunks_by_indices(self, indices: list[int]) -> list[TextChunk]:
        data = self._read_json(f"{_SUB_TEXT}/chunks.json") or self._read_json("chunks.json")
        if not data:
            return []
        idx_set = set(indices)
        return [self._dict_to_chunk(c) for c in data if c["index"] in idx_set]

    def chunk_count(self) -> int:
        data = self._read_json(f"{_SUB_TEXT}/chunks.json") or self._read_json("chunks.json")
        return len(data) if data else 0

    def _get_text_stem(self) -> str:
        """获取文本文件的 stem（从 _filemap.json 或 meta）"""
        fmap = self._read_json("text/_filemap.json")
        if fmap and fmap.get("stem"):
            return fmap["stem"]
        meta = self.load_meta()
        if meta and meta.get("doc_name"):
            return meta["doc_name"]
        return "document"

    def load_raw_text(self) -> str | None:
        """读取原始 Markdown（{stem}.raw.md 优先，回退 raw.md）"""
        stem = self._get_text_stem()
        for path in (
            self.text_dir / f"{stem}.raw.md",
            self.text_dir / "raw.md",
            self._dir / "raw.md",
        ):
            if path.exists():
                return path.read_text(encoding="utf-8")
        return None

    def load_clean_text(self) -> str | None:
        """读取 LLM 整理后的文本（{stem}.md 优先，回退 clean.md）"""
        stem = self._get_text_stem()
        for path in (
            self.text_dir / f"{stem}.md",
            self.text_dir / "clean.md",
        ):
            if path.exists():
                return path.read_text(encoding="utf-8")
        return None

    @staticmethod
    def _dict_to_chunk(c: dict) -> TextChunk:
        return TextChunk(
            content=c["content"],
            context=c["context"],
            index=c["index"],
            heading_path=c.get("heading_path", []),
        )

    def load_skills(self) -> list[dict]:
        skills = []
        for f in sorted(self.skills_dir.glob("*.json")):
            try:
                skills.append(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return skills

    def load_status(self) -> dict | None:
        return self._read_json("status.json")

    def skill_count(self) -> int:
        return len(list(self.skills_dir.glob("*.json")))

    # ──── 调优历史 ────

    def save_tune_record(self, *, chunk_index: int, prompt_hint: str, extracted_skills: list[dict], source_text: str = "") -> int:
        history = self.load_tune_history()
        version = len(history) + 1
        record = {
            "version": version,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "chunk_index": chunk_index,
            "prompt_hint": prompt_hint,
            "extracted_skills": extracted_skills,
            "source_text_preview": source_text[:300],
        }
        history.append(record)
        self._write_json(f"{_SUB_PROMPT}/tune_history.json", history)
        # 同时保存当前提取策略到 prompt/extraction_hint.md
        if prompt_hint:
            self.save_prompt("extraction_hint", prompt_hint)
        return version

    def load_tune_history(self) -> list[dict]:
        # 新路径优先，回退旧路径
        return self._read_json(f"{_SUB_PROMPT}/tune_history.json") or self._read_json("tune_history.json") or []

    def get_active_prompt_hint(self) -> str:
        history = self.load_tune_history()
        if not history:
            return ""
        return history[-1].get("prompt_hint", "")

    # ──── 公共写入 ────

    def update_meta(self, meta: dict) -> None:
        """更新 meta.json（部分更新）。"""
        self._write_json("meta.json", meta)

    # ──── 工具 ────

    def _write_json(self, filename: str, data: dict | list) -> None:
        path = self._dir / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_json(self, filename: str) -> dict | list | None:
        path = self._dir / filename
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def _safe_name(name: str) -> str:
        safe = name.replace("/", "-").replace("\\", "-").replace(" ", "-")
        return safe[:60] if safe else "unnamed"


def list_notebooks() -> list[dict]:
    """列出所有持久化的笔记本"""
    notebooks = []
    for d in sorted(_NOTEBOOKS_DIR.iterdir()):
        if d.is_dir():
            nb = FileNotebook(d.name)
            meta = nb.load_meta()
            if meta:
                status = nb.load_status()
                meta["skills_on_disk"] = nb.skill_count()
                meta["status"] = status
                meta["uploads"] = nb.list_uploads()
                notebooks.append(meta)
    return notebooks
