"""把 docs/ 下的 Markdown 文档转成排版好的 Word（.docx）。

用途：竞赛/评审要交 Word 材料时，把已经写好的 Markdown 直接转过去，
不用手动复制粘贴（手粘很容易丢排版、丢表格、丢公式标记）。

用法：
    python tools/md_to_docx.py docs/作品介绍.md            # 转单个文件
    python tools/md_to_docx.py docs/*.md                   # 批量
    python tools/md_to_docx.py docs/作品介绍.md -o 输出.docx

支持：标题（一~三级，用 Word 内置标题样式，导航窗格可见）、段落（**加粗**、
`行内代码`）、无序/有序列表、引用块、表格、水平分割线。
中英文字体分别设置，避免中文回退成 Word 默认字体。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor

BODY_EA = "宋体"
BODY_ASCII = "Times New Roman"
HEAD_EA = "微软雅黑"
HEAD_ASCII = "Microsoft YaHei"
CODE_ASCII = "Consolas"
BODY_SIZE = 11.0
TABLE_SIZE = 10.0

_INLINE = re.compile(r"(\*\*[^*]+\*\*|`[^`]+`)")


def _apply_font(run, *, ea: str, ascii_font: str, size: float,
                bold: bool = False, color: RGBColor | None = None) -> None:
    """统一设置中英文字体（w:eastAsia 必须单独指定，否则 Word 会用默认中文字体）。"""
    run.font.name = ascii_font
    run.font.size = Pt(size)
    run.font.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), ea)
    if color is not None:
        run.font.color.rgb = color


def _spacing(paragraph, *, before: float = 0, after: float = 6,
             line: float = 1.55, indent: float | None = None) -> None:
    fmt = paragraph.paragraph_format
    fmt.space_before = Pt(before)
    fmt.space_after = Pt(after)
    fmt.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    fmt.line_spacing = line
    if indent is not None:
        fmt.left_indent = Pt(indent)


def _add_runs(paragraph, text: str, *, ea: str = BODY_EA, ascii_font: str = BODY_ASCII,
              size: float = BODY_SIZE, bold: bool = False,
              color: RGBColor | None = None) -> None:
    """写入一段文本，支持 **加粗** 与 `行内代码`。"""
    for piece in _INLINE.split(text):
        if not piece:
            continue
        if piece.startswith("**") and piece.endswith("**"):
            _apply_font(paragraph.add_run(piece[2:-2]), ea=ea, ascii_font=ascii_font,
                        size=size, bold=True, color=color)
        elif piece.startswith("`") and piece.endswith("`"):
            _apply_font(paragraph.add_run(piece[1:-1]), ea=ea, ascii_font=CODE_ASCII,
                        size=max(8.0, size - 0.5), bold=bold, color=color)
        else:
            _apply_font(paragraph.add_run(piece), ea=ea, ascii_font=ascii_font,
                        size=size, bold=bold, color=color)


def _heading(doc: Document, text: str, level: int) -> None:
    """标题用 Word 内置标题样式（导航窗格能看目录），字体再单独覆盖一次。"""
    style_name = "Title" if level == 1 else f"Heading {level - 1}"
    try:
        paragraph = doc.add_paragraph(style=style_name)
    except KeyError:  # 模板里没有该样式时退回普通段落
        paragraph = doc.add_paragraph()
    if level == 1:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _spacing(paragraph, before=0, after=14, line=1.3)
        size = 20.0
    else:
        paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
        _spacing(paragraph, before=16 if level == 2 else 10, after=8 if level == 2 else 6, line=1.3)
        size = 15.0 if level == 2 else 12.5
    _add_runs(paragraph, text, ea=HEAD_EA, ascii_font=HEAD_ASCII, size=size, bold=True)


def _add_code_block(doc: Document, lines: list[str]) -> None:
    """代码块 / 结构图：整块用等宽字体单段落呈现，保留换行与缩进。

    字体分工：ASCII 与制表符走 Consolas（半角），中文走宋体（全角），
    这样终端里画的方框线在 Word 里也能对齐。
    """
    paragraph = doc.add_paragraph()
    _spacing(paragraph, before=4, after=10, line=1.05, indent=6)
    run = paragraph.add_run("\n".join(lines))
    run.font.name = CODE_ASCII
    run.font.size = Pt(9)
    run._element.rPr.rFonts.set(qn("w:eastAsia"), BODY_EA)
    # 浅底纹，让结构图在文档里一眼能认出来。
    # 注意：w:shd 在 OOXML 的 pPr 里有固定次序（必须排在 spacing/ind 之前），
    # 直接 append 会让 Word 认为文档结构不合法，所以用 insert_element_before 定位。
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), "F5F6F8")
    paragraph._p.get_or_add_pPr().insert_element_before(
        shd,
        "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap",
        "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN",
        "w:bidi", "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind",
        "w:contextualSpacing", "w:mirrorIndents", "w:suppressOverlap", "w:jc",
        "w:textDirection", "w:textAlignment", "w:textboxTightWrap",
        "w:outlineLvl", "w:divId", "w:cnfStyle", "w:rPr", "w:sectPr", "w:pPrChange",
    )


def _add_table(doc: Document, rows: list[list[str]]) -> None:
    width = max(len(row) for row in rows)
    table = doc.add_table(rows=len(rows), cols=width)
    table.style = "Table Grid"
    table.autofit = True
    for row_index, row in enumerate(rows):
        for col_index in range(width):
            cell_text = row[col_index] if col_index < len(row) else ""
            # 表格里的换行在 Markdown 里写作 <br>，Word 里换成真换行
            cell_text = cell_text.replace("<br>", " / ")
            cell = table.cell(row_index, col_index)
            cell.text = ""
            paragraph = cell.paragraphs[0]
            _spacing(paragraph, before=2, after=2, line=1.15)
            _add_runs(paragraph, cell_text, size=TABLE_SIZE, bold=row_index == 0)
    _spacing(doc.add_paragraph(), before=0, after=0, line=1.0)


def convert(md_path: Path, out_path: Path | None = None) -> Path:
    """把单个 Markdown 文件转成 Word，返回产出路径。"""
    lines = md_path.read_text(encoding="utf-8").splitlines()
    doc = Document()

    for section in doc.sections:  # A4 + 常规页边距
        section.top_margin = Pt(72)
        section.bottom_margin = Pt(72)
        section.left_margin = Pt(80)
        section.right_margin = Pt(80)

    pending_table: list[list[str]] = []
    code_block: list[str] = []
    in_code = False

    def flush_table() -> None:
        if pending_table:
            _add_table(doc, pending_table)
            pending_table.clear()

    for raw in lines:
        stripped = raw.strip()

        # 围栏代码块（含结构图）整块搬运，内部不解析 Markdown
        if stripped.startswith("```"):
            if in_code:
                _add_code_block(doc, code_block)
                code_block.clear()
                in_code = False
            else:
                flush_table()
                in_code = True
            continue
        if in_code:
            code_block.append(raw.rstrip())
            continue

        if stripped.startswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            if all(re.fullmatch(r":?-{2,}:?", c) for c in cells):
                continue  # 表格分隔行
            pending_table.append(cells)
            continue
        flush_table()

        if not stripped or stripped == "---":
            continue

        if stripped.startswith("### "):
            _heading(doc, stripped[4:].strip(), 3)
        elif stripped.startswith("## "):
            _heading(doc, stripped[3:].strip(), 2)
        elif stripped.startswith("# "):
            _heading(doc, stripped[2:].strip(), 1)
        elif stripped.startswith(">"):
            paragraph = doc.add_paragraph()
            _spacing(paragraph, before=2, after=4, line=1.35, indent=14)
            _add_runs(paragraph, stripped.lstrip(">").strip(), size=10.5,
                      color=RGBColor(0x55, 0x55, 0x55))
        elif re.match(r"^[-*] ", stripped):
            paragraph = doc.add_paragraph(style="List Bullet")
            _spacing(paragraph, before=0, after=3, line=1.4, indent=18)
            _add_runs(paragraph, stripped[2:].strip())
        elif re.match(r"^\d+\. ", stripped):
            paragraph = doc.add_paragraph(style="List Number")
            _spacing(paragraph, before=0, after=3, line=1.4, indent=18)
            _add_runs(paragraph, re.sub(r"^\d+\.\s*", "", stripped))
        else:
            paragraph = doc.add_paragraph()
            _spacing(paragraph, before=0, after=8)
            _add_runs(paragraph, stripped)

    flush_table()
    if code_block:  # 围栏没闭合时也要把内容写出来，不能默默丢掉
        _add_code_block(doc, code_block)

    target = out_path or md_path.with_suffix(".docx")
    doc.save(str(target))

    check = Document(str(target))
    chars = sum(len(p.text) for p in check.paragraphs)
    print(f"[OK] {md_path.name} → {target.name}"
          f"（段落 {len(check.paragraphs)}，表格 {len(check.tables)}，约 {chars} 字，"
          f"{target.stat().st_size // 1024} KB）")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Markdown → Word 转换")
    parser.add_argument("inputs", nargs="+", help="Markdown 文件路径")
    parser.add_argument("-o", "--output", help="输出文件（仅单个输入时可用）")
    args = parser.parse_args()

    if args.output and len(args.inputs) > 1:
        print("批量转换时不能用 -o，请逐个转换")
        return 2

    failed = 0
    for item in args.inputs:
        md_path = Path(item)
        if not md_path.exists():
            print(f"[FAIL] 文件不存在：{md_path}")
            failed += 1
            continue
        try:
            convert(md_path, Path(args.output) if args.output else None)
        except Exception as exc:  # pragma: no cover - 命令行工具
            print(f"[FAIL] {md_path.name} 转换失败：{type(exc).__name__}: {exc}")
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
