"""
日志分析器 — R1 调用可观测性工具

从 JSONL 日志中提取统计数据，用于优化 Prompt 和监控成本。

功能：
1. Token 消耗统计（按阶段、按 Prompt 版本）
2. 延迟分布（P50/P95/P99）
3. 校验通过率趋势
4. 高延迟 / 低质量调用定位
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .config import config


@dataclass
class PhaseStats:
    """单阶段统计"""

    phase: str
    call_count: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_think_tokens: int = 0
    latencies_ms: list[int] = field(default_factory=list)
    pass_count: int = 0
    fail_count: int = 0
    pending_count: int = 0

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens

    @property
    def avg_latency_ms(self) -> float:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else 0

    @property
    def p50_latency_ms(self) -> int:
        if not self.latencies_ms:
            return 0
        s = sorted(self.latencies_ms)
        return s[len(s) // 2]

    @property
    def p95_latency_ms(self) -> int:
        if not self.latencies_ms:
            return 0
        s = sorted(self.latencies_ms)
        idx = int(len(s) * 0.95)
        return s[min(idx, len(s) - 1)]

    @property
    def pass_rate(self) -> float:
        total = self.pass_count + self.fail_count
        return self.pass_count / total if total > 0 else 0

    # DeepSeek R1 价格（粗估：¥2/M input, ¥8/M output）
    @property
    def estimated_cost_rmb(self) -> float:
        return (
            self.total_input_tokens * 2 / 1_000_000
            + self.total_output_tokens * 8 / 1_000_000
        )


@dataclass
class AnalysisReport:
    """分析报告"""

    total_calls: int = 0
    total_tokens: int = 0
    total_cost_rmb: float = 0
    by_phase: dict[str, PhaseStats] = field(default_factory=dict)
    by_version: dict[str, PhaseStats] = field(default_factory=dict)

    def to_markdown(self) -> str:
        """生成 Markdown 格式的分析报告"""
        lines = [
            "# R1 调用分析报告",
            "",
            f"总调用数：{self.total_calls}",
            f"总 Token 消耗：{self.total_tokens:,}",
            f"预估成本：¥{self.total_cost_rmb:.2f}",
            "",
            "## 按阶段统计",
            "",
            "| 阶段 | 调用数 | 输入Token | 输出Token | 思考Token | 平均延迟 | P95延迟 | 通过率 | 成本 |",
            "|------|--------|-----------|-----------|-----------|---------|---------|--------|------|",
        ]

        for name, stats in sorted(self.by_phase.items()):
            lines.append(
                f"| {name} | {stats.call_count} | "
                f"{stats.total_input_tokens:,} | {stats.total_output_tokens:,} | "
                f"{stats.total_think_tokens:,} | "
                f"{stats.avg_latency_ms:.0f}ms | {stats.p95_latency_ms}ms | "
                f"{stats.pass_rate:.0%} | ¥{stats.estimated_cost_rmb:.3f} |"
            )

        if self.by_version:
            lines.extend([
                "",
                "## 按 Prompt 版本统计",
                "",
                "| 版本 | 调用数 | 平均延迟 | 通过率 | 成本 |",
                "|------|--------|---------|--------|------|",
            ])
            for ver, stats in sorted(self.by_version.items()):
                lines.append(
                    f"| {ver} | {stats.call_count} | "
                    f"{stats.avg_latency_ms:.0f}ms | "
                    f"{stats.pass_rate:.0%} | ¥{stats.estimated_cost_rmb:.3f} |"
                )

        return "\n".join(lines)


def analyze_logs(
    log_dir: Optional[str | Path] = None,
    date: Optional[str] = None,
) -> AnalysisReport:
    """
    分析 JSONL 日志文件。

    Args:
        log_dir: 日志目录（默认使用配置）
        date: 指定日期（如 2026-02-22），None 表示分析所有

    Returns:
        AnalysisReport 分析报告
    """
    log_path = Path(log_dir or config.log_dir)

    if date:
        files = [log_path / f"{date}.jsonl"]
    else:
        files = sorted(log_path.glob("*.jsonl"))

    report = AnalysisReport()
    by_phase: dict[str, PhaseStats] = defaultdict(lambda: PhaseStats(phase=""))
    by_version: dict[str, PhaseStats] = defaultdict(lambda: PhaseStats(phase=""))

    for f in files:
        if not f.exists():
            continue

        for line in f.read_text(encoding="utf-8").strip().split("\n"):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            phase = record.get("phase", "unknown")
            version = record.get("prompt_version", "unknown")
            input_tokens = record.get("input_tokens", 0)
            output_tokens = record.get("output_tokens", 0)
            think_tokens = record.get("think_tokens", 0)
            latency = record.get("latency_ms", 0)
            validation = record.get("validation_result", "pending")

            report.total_calls += 1
            report.total_tokens += input_tokens + output_tokens

            # 按阶段汇总
            ps = by_phase[phase]
            ps.phase = phase
            ps.call_count += 1
            ps.total_input_tokens += input_tokens
            ps.total_output_tokens += output_tokens
            ps.total_think_tokens += think_tokens
            ps.latencies_ms.append(latency)
            if validation == "pass":
                ps.pass_count += 1
            elif validation in ("fail_format", "fail_incomplete", "fail_hallucination"):
                ps.fail_count += 1
            else:
                ps.pending_count += 1

            # 按版本汇总
            vs = by_version[version]
            vs.phase = version
            vs.call_count += 1
            vs.total_input_tokens += input_tokens
            vs.total_output_tokens += output_tokens
            vs.total_think_tokens += think_tokens
            vs.latencies_ms.append(latency)
            if validation == "pass":
                vs.pass_count += 1
            elif validation.startswith("fail"):
                vs.fail_count += 1

    report.by_phase = dict(by_phase)
    report.by_version = dict(by_version)
    report.total_cost_rmb = sum(s.estimated_cost_rmb for s in by_phase.values())

    return report


# ──── CLI ────

if __name__ == "__main__":
    import sys

    date_arg = sys.argv[1] if len(sys.argv) > 1 else None
    report = analyze_logs(date=date_arg)
    print(report.to_markdown())
