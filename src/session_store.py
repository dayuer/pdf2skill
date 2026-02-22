"""
文件化会话管理 — 持久化 Pipeline 状态

每个会话对应 sessions/{session_id}/ 目录：
  meta.json    — 文档名、格式、类型、时间
  schema.json  — R1 推断的 Schema
  chunks.json  — 粗筛后的文本块列表
  skills/      — 每个提取到的 Skill 单独一个 .json 文件
  status.json  — 处理进度

服务器重启后可从磁盘恢复所有会话。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from .markdown_chunker import TextChunk
from .schema_generator import SkillSchema
from .skill_validator import ValidatedSkill, ValidationStatus


_SESSIONS_DIR = Path("sessions")
_SESSIONS_DIR.mkdir(exist_ok=True)


class FileSession:
    """文件化的 Pipeline 会话"""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.root = _SESSIONS_DIR / session_id
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "skills").mkdir(exist_ok=True)

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
            "session_id": self.session_id,
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

    def save_schema(self, schema: SkillSchema) -> None:
        """保存 Schema"""
        data = {
            "book_type": schema.book_type,
            "domains": schema.domains,
            "constraint": schema.to_prompt_constraint(),
        }
        self._write_json("schema.json", data)

    def save_chunks(self, chunks: list[TextChunk]) -> None:
        """保存文本块列表"""
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
        self._write_json("chunks.json", data)

    def save_skill(self, skill: ValidatedSkill, idx: int) -> None:
        """保存单个 Skill（立即写盘）"""
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
        path = self.root / "skills" / f"{idx:04d}_{self._safe_name(skill.name)}.json"
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    # ──── Checkpoint Index（断点续传） ────

    def mark_chunks_done(self, chunk_indices: list[int]) -> None:
        """标记一批 chunk 已处理完毕，追加写入 index"""
        idx_file = self.root / "progress_index.json"
        existing = self._load_progress_index()
        existing.update(chunk_indices)
        idx_file.write_text(
            json.dumps(sorted(existing), ensure_ascii=False),
            encoding="utf-8",
        )

    def get_pending_chunk_indices(self, total: int) -> list[int]:
        """返回还未处理的 chunk 索引列表"""
        done = self._load_progress_index()
        return [i for i in range(total) if i not in done]

    def get_done_count(self) -> int:
        """已处理的 chunk 数量"""
        return len(self._load_progress_index())

    def _load_progress_index(self) -> set[int]:
        idx_file = self.root / "progress_index.json"
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
        return self._read_json("schema.json")

    def load_chunks(self) -> list[TextChunk]:
        """从磁盘还原文本块列表（全量）"""
        data = self._read_json("chunks.json")
        if not data:
            return []
        return [self._dict_to_chunk(c) for c in data]

    def load_chunks_by_indices(self, indices: list[int]) -> list[TextChunk]:
        """按索引加载指定 chunk（最小内存：只读文件一次，只返回需要的）"""
        data = self._read_json("chunks.json")
        if not data:
            return []
        idx_set = set(indices)
        return [self._dict_to_chunk(c) for c in data if c["index"] in idx_set]

    def chunk_count(self) -> int:
        """总 chunk 数（不加载全部内容）"""
        data = self._read_json("chunks.json")
        return len(data) if data else 0

    @staticmethod
    def _dict_to_chunk(c: dict) -> TextChunk:
        return TextChunk(
            content=c["content"],
            context=c["context"],
            index=c["index"],
            heading_path=c.get("heading_path", []),
        )

    def load_skills(self) -> list[dict]:
        """加载所有已保存的 Skill"""
        skills_dir = self.root / "skills"
        skills = []
        for f in sorted(skills_dir.glob("*.json")):
            try:
                skills.append(json.loads(f.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, OSError):
                continue
        return skills

    def load_status(self) -> dict | None:
        return self._read_json("status.json")

    def skill_count(self) -> int:
        return len(list((self.root / "skills").glob("*.json")))

    # ──── 调优历史（Prompt 版本链） ────

    def save_tune_record(
        self,
        *,
        chunk_index: int,
        prompt_hint: str,
        extracted_skills: list[dict],
        source_text: str = "",
    ) -> int:
        """追加一条调优记录，返回新版本号"""
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
        self._write_json("tune_history.json", history)
        return version

    def load_tune_history(self) -> list[dict]:
        """读取完整调优历史"""
        return self._read_json("tune_history.json") or []

    def get_active_prompt_hint(self) -> str:
        """返回最后一次调优的 prompt_hint（用于全量执行）"""
        history = self.load_tune_history()
        if not history:
            return ""
        return history[-1].get("prompt_hint", "")

    # ──── 工具 ────

    def _write_json(self, filename: str, data: dict | list) -> None:
        path = self.root / filename
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _read_json(self, filename: str) -> dict | list | None:
        path = self.root / filename
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None

    @staticmethod
    def _safe_name(name: str) -> str:
        """将 Skill 名称转为文件安全字符串"""
        safe = name.replace("/", "-").replace("\\", "-").replace(" ", "-")
        return safe[:60] if safe else "unnamed"


def list_sessions() -> list[dict]:
    """列出所有持久化的会话"""
    sessions = []
    for d in sorted(_SESSIONS_DIR.iterdir()):
        if d.is_dir():
            fs = FileSession(d.name)
            meta = fs.load_meta()
            if meta:
                status = fs.load_status()
                meta["skills_on_disk"] = fs.skill_count()
                meta["status"] = status
                sessions.append(meta)
    return sessions
