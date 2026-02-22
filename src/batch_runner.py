"""
批量处理器 — 多文档并行 Pipeline 执行

支持：
1. 目录批量扫描（递归 glob PDF/TXT/EPUB）
2. 多文档串行执行（避免 R1 API 过载）
3. 汇总报告生成
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import PipelineConfig, config
from .document_loader import detect_format, DocFormat
from .pipeline import PipelineResult, run_pipeline


_SUPPORTED_EXTENSIONS = {".pdf", ".txt", ".text", ".md", ".markdown", ".epub"}


@dataclass
class BatchResult:
    """批量处理结果"""

    results: list[PipelineResult] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)
    total_elapsed: float = 0

    @property
    def total_skills(self) -> int:
        return sum(r.final_skills_count for r in self.results)

    @property
    def total_documents(self) -> int:
        return len(self.results) + len(self.errors)

    def summary(self) -> str:
        lines = [
            "# 批量处理报告",
            "",
            f"文档总数：{self.total_documents}",
            f"成功：{len(self.results)}，失败：{len(self.errors)}",
            f"生成 Skill 总数：{self.total_skills}",
            f"总耗时：{self.total_elapsed:.1f}s",
            "",
            "## 成功列表",
            "",
            "| 文档 | 格式 | 块数 | Skill数 | 耗时 |",
            "|------|------|------|---------|------|",
        ]
        for r in self.results:
            lines.append(
                f"| {r.doc_name} | {r.doc_format} | "
                f"{r.total_chunks} | {r.final_skills_count} | "
                f"{r.elapsed_seconds:.1f}s |"
            )

        if self.errors:
            lines.extend([
                "",
                "## 失败列表",
                "",
                "| 文件 | 错误 |",
                "|------|------|",
            ])
            for filepath, error in self.errors:
                lines.append(f"| {filepath} | {error[:80]} |")

        return "\n".join(lines)


def scan_documents(
    directory: str | Path,
    *,
    recursive: bool = True,
) -> list[Path]:
    """
    扫描目录下的所有支持格式文件。

    Args:
        directory: 目录路径
        recursive: 是否递归扫描

    Returns:
        文件路径列表（按名称排序）
    """
    path = Path(directory)
    if not path.is_dir():
        raise NotADirectoryError(f"不是目录：{path}")

    files: list[Path] = []
    glob_fn = path.rglob if recursive else path.glob

    for ext in _SUPPORTED_EXTENSIONS:
        files.extend(glob_fn(f"*{ext}"))

    return sorted(set(files))


def run_batch(
    files: list[str | Path],
    *,
    output_dir: Optional[str | Path] = None,
    cfg: Optional[PipelineConfig] = None,
) -> BatchResult:
    """
    串行处理多个文档。

    Args:
        files: 文件路径列表
        output_dir: 输出目录
        cfg: Pipeline 配置

    Returns:
        BatchResult 批量结果
    """
    batch = BatchResult()
    t_start = time.monotonic()

    for i, filepath in enumerate(files):
        path_str = str(filepath)
        print(f"\n{'=' * 60}")
        print(f"[{i + 1}/{len(files)}] 处理文档：{filepath}")
        print(f"{'=' * 60}")

        try:
            result = run_pipeline(
                filepath,
                output_dir=output_dir,
                cfg=cfg,
            )
            batch.results.append(result)
        except Exception as e:
            print(f"❌ 处理失败：{e}")
            batch.errors.append((path_str, str(e)))

    batch.total_elapsed = time.monotonic() - t_start
    print(f"\n{'=' * 60}")
    print(batch.summary())

    return batch


# ──── CLI ────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法：")
        print("  python -m src.batch_runner <目录>     — 扫描目录并批量处理")
        print("  python -m src.batch_runner <文件1> <文件2> ...  — 处理指定文件")
        sys.exit(1)

    target = Path(sys.argv[1])

    if target.is_dir():
        files = scan_documents(target)
        print(f"扫描到 {len(files)} 个文档")
        result = run_batch(files)
    else:
        files = [Path(f) for f in sys.argv[1:]]
        result = run_batch(files)

    print(f"\n完成。共生成 {result.total_skills} 个 Skill")
