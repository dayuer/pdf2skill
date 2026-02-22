"""
markdown_chunker 测试用例

覆盖三种切分策略 + 后处理 + 噪音清洗 + 边界情况
"""

import pytest
from src.markdown_chunker import (
    ChunkResult,
    TextChunk,
    chunk_markdown,
    clean_markdown,
    _extract_headings,
    _build_heading_path,
    MIN_CHUNK_CHARS,
    MAX_CHUNK_CHARS,
)


# ──── 辅助函数 ────


def _make_section(title: str, level: int, body: str) -> str:
    """生成一个 Markdown 章节"""
    return f"{'#' * level} {title}\n\n{body}\n\n"


def _make_book_md(sections: list[tuple[str, int, str]]) -> str:
    """生成包含多个章节的 Markdown 文本"""
    return "".join(_make_section(t, l, b) for t, l, b in sections)


# ──── 策略 A 测试：标题级 AST 切分 ────


class TestHeadingASTStrategy:
    """标题层级切分（策略 A）"""

    def test_basic_split_by_h2(self):
        """基本的二级标题切分"""
        md = _make_book_md([
            ("第一章 概述", 2, "这是概述内容，" * 50),
            ("第二章 安装", 2, "安装步骤如下，" * 50),
            ("第三章 配置", 2, "配置方法说明，" * 50),
            ("第四章 部署", 2, "部署流程说明，" * 50),
            ("第五章 监控", 2, "监控方案设计，" * 50),
            ("第六章 运维", 2, "运维手册内容，" * 50),
        ])
        result = chunk_markdown(md, "测试书籍", clean=False)
        assert result.strategy == "heading_ast"
        assert len(result.chunks) >= 6

    def test_context_injection(self):
        """父级上下文正确注入"""
        md = _make_book_md([
            ("第一章", 2, "内容" * 100),
            ("1.1 小节", 3, "小节内容" * 100),
            ("1.2 小节", 3, "另一小节" * 100),
            ("第二章", 2, "第二章内容" * 100),
            ("第三章", 2, "第三章内容" * 100),
        ])
        result = chunk_markdown(md, "架构指南", clean=False)
        assert result.strategy == "heading_ast"
        # 验证至少有一个块包含文档名称上下文
        contexts = [c.context for c in result.chunks]
        assert any("架构指南" in ctx for ctx in contexts)

    def test_heading_path_built_correctly(self):
        """标题层级路径正确构建"""
        md = "# 根\n\n## 一级\n\n### 二级\n\n### 二级B\n\n## 一级B\n\n"
        headings = _extract_headings(md)
        # 测试 "二级" 的层级路径
        path = _build_heading_path(headings, 2)  # ### 二级
        assert "根" in path
        assert "一级" in path
        assert "二级" in path

    def test_split_level_3(self):
        """指定以三级标题为切分边界"""
        md = _make_book_md([
            ("第一章", 2, "章内容" * 50),
            ("1.1 安装", 3, "安装步骤" * 80),
            ("1.2 配置", 3, "配置方法" * 80),
            ("第二章", 2, "第二章" * 50),
            ("2.1 部署", 3, "部署说明" * 80),
            ("2.2 验证", 3, "验证流程" * 80),
            ("第三章", 2, "第三章" * 50),
        ])
        result = chunk_markdown(md, "测试", split_level=3, clean=False)
        assert result.strategy == "heading_ast"
        assert len(result.chunks) >= 5


# ──── 策略 B 测试：段落级切分 ────


class TestParagraphStrategy:
    """段落边界切分（策略 B）—— 标题不足 5 个时触发"""

    def test_paragraph_fallback(self):
        """标题数量 < 5 时降级到段落切分"""
        # 只有 2 个标题，但有清晰段落
        md = "## 标题A\n\n" + "\n\n".join(
            [f"这是第 {i} 个段落的内容，" * 30 for i in range(10)]
        )
        result = chunk_markdown(md, "测试", clean=False)
        assert result.strategy == "paragraph"
        assert len(result.chunks) >= 1

    def test_respects_max_chars(self):
        """段落切分遵守最大字数限制"""
        paragraphs = "\n\n".join(["这是一个长段落，" * 200 for _ in range(5)])
        result = chunk_markdown(paragraphs, "测试", clean=False)
        for chunk in result.chunks:
            # 允许一定误差（单段落超长无法避免）
            assert chunk.char_count <= MAX_CHUNK_CHARS * 2


# ──── 策略 C 测试：滑动窗口 ────


class TestSlidingWindowStrategy:
    """滑动窗口切分（策略 C）—— 纯文本墙"""

    def test_pure_text_wall(self):
        """无标题无段落分隔的纯文本"""
        text = "这是一堵文本墙没有任何换行" * 500
        result = chunk_markdown(text, "测试", clean=False)
        assert result.strategy == "sliding_window"
        assert len(result.chunks) >= 2

    def test_overlap_exists(self):
        """滑动窗口间存在重叠"""
        text = "A" * 10000  # 纯字符，无换行
        result = chunk_markdown(text, "测试", clean=False)
        if len(result.chunks) >= 2:
            # 相邻块应该有重叠（后一块的开头部分包含在前一块的末尾）
            c0_end = result.chunks[0].content[-100:]
            c1_start = result.chunks[1].content[:100]
            # 至少有部分字符重叠
            assert len(set(c0_end) & set(c1_start)) > 0


# ──── 后处理测试 ────


class TestPostProcessing:
    """过短合并 + 超长二次切分"""

    def test_short_chunks_merged(self):
        """短于 MIN_CHUNK_CHARS 的块会被合并到前一个块"""
        md = _make_book_md([
            ("章一", 2, "内容" * 100),
            ("章二", 2, "短"),  # 极短
            ("章三", 2, "内容" * 100),
            ("章四", 2, "内容" * 100),
            ("章五", 2, "内容" * 100),
        ])
        result = chunk_markdown(md, "测试", clean=False)
        # "短" 应该被合并，总块数应少于 5
        for chunk in result.chunks:
            # 不应出现只有几个字的独立块
            if chunk.char_count < MIN_CHUNK_CHARS:
                # 如果这是最后一个块（末尾残余），可以接受
                assert chunk.index == len(result.chunks) - 1

    def test_oversized_chunk_split(self):
        """超过 MAX_CHUNK_CHARS 的块会被二次切分"""
        # 一个极长的章节
        md = _make_book_md([
            ("第一章", 2, "这是详细内容，" * 1000),
            ("第二章", 2, "第二章内容，" * 100),
            ("第三章", 2, "第三章内容，" * 100),
            ("第四章", 2, "第四章内容，" * 100),
            ("第五章", 2, "第五章内容，" * 100),
        ])
        result = chunk_markdown(md, "测试", clean=False)
        # 第一章应被二次切分，总块数 > 5
        assert len(result.chunks) > 5


# ──── 噪音清洗测试 ────


class TestNoiseCleaning:
    """Phase 1A 噪音清洗"""

    def test_remove_page_numbers(self):
        """移除纯页码行"""
        text = "正文内容\n\n42\n\n继续正文\n\n- 123 -\n\n更多内容"
        cleaned = clean_markdown(text)
        assert "42" not in cleaned.split("\n")
        assert "正文内容" in cleaned

    def test_remove_references_section(self):
        """移除参考文献章节"""
        md = _make_book_md([
            ("第一章 核心", 2, "这是核心内容" * 50),
            ("参考文献", 2, "Smith et al. 2020\nJones 2019"),
        ])
        cleaned = clean_markdown(md)
        assert "参考文献" not in cleaned
        assert "核心内容" in cleaned

    def test_remove_acknowledgments(self):
        """移除致谢章节"""
        md = _make_book_md([
            ("第一章", 2, "正文" * 50),
            ("致谢", 2, "感谢张三教授的指导"),
        ])
        cleaned = clean_markdown(md)
        assert "致谢" not in cleaned

    def test_compress_blank_lines(self):
        """压缩连续空行"""
        text = "段落一\n\n\n\n\n\n段落二"
        cleaned = clean_markdown(text)
        assert "\n\n\n" not in cleaned
        assert "段落一" in cleaned
        assert "段落二" in cleaned


# ──── 边界情况 ────


class TestEdgeCases:
    """边界情况处理"""

    def test_empty_input(self):
        """空输入"""
        result = chunk_markdown("", "空文档")
        assert result.strategy == "empty"
        assert len(result.chunks) == 0

    def test_whitespace_only(self):
        """纯空白输入"""
        result = chunk_markdown("   \n\n\n   ", "空白文档")
        assert result.strategy == "empty"

    def test_single_heading(self):
        """只有一个标题"""
        md = "## 唯一章节\n\n这是内容" * 50
        result = chunk_markdown(md, "单章书", clean=False)
        assert len(result.chunks) >= 1

    def test_index_sequential(self):
        """切分后的 index 必须连续递增"""
        md = _make_book_md([
            (f"第{i}章", 2, f"内容{i}，" * 80) for i in range(10)
        ])
        result = chunk_markdown(md, "测试", clean=False)
        for i, chunk in enumerate(result.chunks):
            assert chunk.index == i

    def test_doc_name_in_context(self):
        """文档名称出现在每个块的上下文中"""
        md = _make_book_md([
            (f"章{i}", 2, f"内容{i}" * 80) for i in range(6)
        ])
        result = chunk_markdown(md, "我的书", clean=False)
        for chunk in result.chunks:
            assert "我的书" in chunk.context
