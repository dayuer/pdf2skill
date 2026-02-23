"""文件分类器 — 根据文件名和扩展名自动判定处理级别。

Level 体系：
  L1_SCHEMA   : 保障利益表 (Excel) — 全局数据骨架
  L2_CLAUSE   : 保险条款 (PDF/DOCX) — 语义分块
  L3_RULE     : 健康告知/投保须知/责任免除 — 规则树
  L4_LIST     : 医院/机构清单 — 结构化字典
  L5_AUX      : QA/计划书/保单样本 — 辅助语料
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path


class FileLevel(str, Enum):
    L1_SCHEMA = "schema"
    L2_CLAUSE = "clause"
    L3_RULE = "rule"
    L4_LIST = "list"
    L5_AUX = "auxiliary"
    UNKNOWN = "unknown"


# ── 分类规则（按优先级从高到低匹配）──

_L1_KEYWORDS = ["保障利益", "利益表", "保障计划", "保障方案", "产品费率"]
_L3_KEYWORDS = ["健康告知", "投保须知", "责任免除", "免除事项", "重要提示", "特别约定"]
_L4_KEYWORDS = ["清单", "名录", "名单", "医院列表", "机构列表", "目录"]
_L5_KEYWORDS = ["QA", "问答", "计划书", "保单样本", "电子保单", "服务手册", "增值服务"]
_L2_KEYWORDS = ["条款", "保险合同", "主险", "附加险", "附约"]

_EXCEL_EXTS = {".xlsx", ".xls", ".csv"}


def classify_file(filename: str) -> FileLevel:
    """根据文件名和扩展名判定处理级别。

    分类优先级：L1 > L3 > L4 > L5 > L2 > UNKNOWN
    Excel 文件在无关键词匹配时默认 L1。
    """
    stem = Path(filename).stem
    ext = Path(filename).suffix.lower()
    name_lower = stem.lower()

    # L1：保障利益表 / 费率表（Excel 强信号）
    if _match_any(stem, _L1_KEYWORDS):
        return FileLevel.L1_SCHEMA

    # L3：健康告知 / 投保须知 / 责任免除
    if _match_any(stem, _L3_KEYWORDS):
        return FileLevel.L3_RULE

    # L4：清单 / 名录
    if _match_any(stem, _L4_KEYWORDS):
        return FileLevel.L4_LIST

    # L5：QA / 计划书
    if _match_any(stem, _L5_KEYWORDS):
        return FileLevel.L5_AUX

    # L2：条款 / 保险合同
    if _match_any(stem, _L2_KEYWORDS):
        return FileLevel.L2_CLAUSE

    # Excel 无关键词命中 → 默认 L1（大概率是利益表/费率表）
    if ext in _EXCEL_EXTS:
        return FileLevel.L1_SCHEMA

    # PDF/DOCX 无关键词命中 → 默认 L2（按条款处理）
    if ext in {".pdf", ".docx", ".doc"}:
        return FileLevel.L2_CLAUSE

    return FileLevel.UNKNOWN


def classify_files(filenames: list[str]) -> dict[str, FileLevel]:
    """批量分类。返回 {filename: level} 映射。"""
    return {f: classify_file(f) for f in filenames}


def _match_any(text: str, keywords: list[str]) -> bool:
    """检查 text 中是否包含任一关键词（大小写不敏感）。"""
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in keywords)
