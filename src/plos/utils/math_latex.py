"""LaTeX 片段切分与轻量 HTML 渲染（无 Qt、无第三方依赖）。

设计要点
--------
- 只有带标记的片段才当作公式：``\\( \\)`` 行内、``\\[ \\]`` 独立居中，
  以及 ``$ $`` / ``$$ $$`` 两种常见写法；其余文本原样保留。
- 任何解析异常都回退为**原始 LaTeX 源码**，绝不返回空白、绝不抛异常。
- 行内公式用可读的线性写法（``a/b``、``x²``、``√x``），因为 Qt 富文本
  的行内元素无法堆叠；独立公式用上下结构真分式（单张表格实现，
  运算符号用 rowspan 跨两行），视觉上接近正式排版。
- 颜色由调用方注入，便于深浅主题分别取色。
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from .latex_clean import unicode_symbol
from .logger import get_logger

logger = get_logger("utils.math_latex")

#: 公式锚点前缀：QTextBrowser 里点击/悬浮时据此识别公式片段
MATH_HREF_SCHEME = "plosmath"

#: 独立公式（先匹配，避免 $$ 被当成两个 $）
_BLOCK_DELIMITERS: Tuple[Tuple[str, str], ...] = (("\\[", "\\]"), ("$$", "$$"))
#: 行内公式
_INLINE_DELIMITERS: Tuple[Tuple[str, str], ...] = (("\\(", "\\)"), ("$", "$"))

#: 行内 $...$ 的保守约束：跨行、超长、空内容一律不当公式（避免把价格 "$5" 当公式）
_INLINE_DOLLAR_MAX_LEN = 300
#: $...$ 内容只允许出现这些「像数学」的字符；含中文说明是普通文本
_DOLLAR_SAFE_RE = re.compile(r"^[0-9a-zA-Z\s+\-*/=<>()\[\]{}^_.,;:!'|\\]+$")

_COMMAND_RE = re.compile(r"\\([a-zA-Z]+)\s*")
_GROUP_LIMIT = 24  # 嵌套层级上限，防御畸形输入
#: 需要在 HTML 中转义的字符
_ESCAPE_TABLE = {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}

#: 常用符号补充（未命中时再查 latex_clean 的大表）
_EXTRA_SYMBOLS = {
    "cdot": "·", "times": "×", "div": "÷", "pm": "±", "mp": "∓",
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "equiv": "≡", "infty": "∞", "propto": "∝",
    "rightarrow": "→", "to": "→", "leftarrow": "←", "Rightarrow": "⇒",
    "Leftrightarrow": "⇔", "in": "∈", "notin": "∉", "subset": "⊂",
    "subseteq": "⊆", "cup": "∪", "cap": "∩", "emptyset": "∅",
    "sum": "∑", "prod": "∏", "int": "∫", "partial": "∂", "nabla": "∇",
    "angle": "∠", "perp": "⊥", "parallel": "∥", "triangle": "△",
    "degree": "°", "circ": "∘", "prime": "′", "therefore": "∴", "because": "∵",
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "theta": "θ", "lambda": "λ", "mu": "μ", "pi": "π", "rho": "ρ",
    "sigma": "σ", "phi": "φ", "omega": "ω", "varphi": "ϕ",
    "Delta": "Δ", "Gamma": "Γ", "Theta": "Θ", "Lambda": "Λ", "Pi": "Π",
    "Sigma": "Σ", "Phi": "Φ", "Omega": "Ω", "Psi": "Ψ",
    "forall": "∀", "exists": "∃", "neg": "¬", "land": "∧", "lor": "∨",
    "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉",
    "langle": "⟨", "rangle": "⟩", "ldots": "…", "cdots": "⋯",
    "quad": " ", "qquad": "  ", "thinspace": " ", "medspace": " ",
    "overline": "",  # 单独处理
}

#: 直接丢弃（只影响排版，不影响语义）
_DROPPED_COMMANDS = {
    "left", "right", "big", "Big", "bigl", "bigr", "Bigl", "Bigr",
    "biggl", "biggr", "Biggl", "Biggr", "displaystyle", "textstyle",
    "scriptstyle", "scriptscriptstyle", "limits", "nolimits", "centering",
    "hline", "phantom", "quad", "qquad", "smallskip", "medskip",
}

#: 取内容当普通文本（内容不进入公式解析）
_TEXT_COMMANDS = {
    "text", "textrm", "textit", "textbf", "texttt", "textnormal", "textup",
    "mathrm", "mathit", "mathbf", "mathsf", "mathtt", "operatorname",
    "mbox", "hbox", "ensuremath", "ce", "textsc", "textsl",
}

#: 上下标类结构命令
_BIG_OPERATORS = {"sum", "prod", "int", "iint", "oint", "bigcup", "bigcap", "lim"}


def escape_html(text: str) -> str:
    """转义 HTML 特殊字符。"""
    if not text:
        return ""
    return "".join(_ESCAPE_TABLE.get(ch, ch) for ch in text)


def text_to_html(text: str) -> str:
    """普通文本 → HTML（转义 + 换行）。"""
    return escape_html(text).replace("\r\n", "\n").replace("\n", "<br/>")


# ----------------------------------------------------------------------
# 片段切分
# ----------------------------------------------------------------------

def split_math_segments(text: str) -> List[Tuple[str, str, bool]]:
    """把文本切成 [(kind, content, is_block)]，kind 为 "text" / "math"。

    普通文本原样返回；只有带标记的片段才标为 math。
    找不到闭合标记时按普通文本处理（绝不吞掉后面的内容）。
    """
    if not text:
        return []
    segments: List[Tuple[str, str, bool]] = []
    buffer: List[str] = []
    pos = 0
    length = len(text)

    def flush() -> None:
        if buffer:
            segments.append(("text", "".join(buffer), False))
            buffer.clear()

    while pos < length:
        found = _find_next_delimiter(text, pos)
        if found is None:
            buffer.append(text[pos:])
            break
        open_delim, close_delim, open_at = found
        close_at = text.find(close_delim, open_at + len(open_delim))
        if close_at < 0:
            # 未闭合：整段当普通文本，避免误吞后续内容
            buffer.append(text[pos:])
            break
        content = text[open_at + len(open_delim): close_at]
        is_block = (open_delim, close_delim) in _BLOCK_DELIMITERS
        if not _acceptable(content, is_block, open_delim):
            buffer.append(text[pos: close_at + len(close_delim)])
            pos = close_at + len(close_delim)
            continue
        buffer.append(text[pos:open_at])
        flush()
        segments.append(("math", content.strip(), is_block))
        pos = close_at + len(close_delim)

    flush()
    return segments


def _find_next_delimiter(text: str, start: int):
    """返回 (open, close, index)：取最靠前的起始标记；同位置时独立公式优先。"""
    best = None
    for open_delim, close_delim in _BLOCK_DELIMITERS + _INLINE_DELIMITERS:
        index = text.find(open_delim, start)
        if index < 0:
            continue
        if best is None or index < best[2]:
            best = (open_delim, close_delim, index)
    return best


def _acceptable(content: str, is_block: bool, open_delim: str) -> bool:
    """判断这段内容是否真的像公式而不是普通文本里碰巧出现的符号。"""
    if not content.strip():
        return False
    if is_block:
        return True
    if open_delim != "$":
        return True
    # $...$ 是最容易被误判的写法：不允许跨行/超长，内容必须「像数学」，
    # 含中文一律视为普通文本（例如「这件 $5 元」）
    if "\n" in content or len(content) > _INLINE_DOLLAR_MAX_LEN:
        return False
    return bool(_DOLLAR_SAFE_RE.match(content))


def iter_math_spans(text: str) -> List[Tuple[int, int, str, bool]]:
    """返回公式片段在原文中的 (start, end, latex, is_block)，供导出等场景定位。"""
    spans: List[Tuple[int, int, str, bool]] = []
    pos = 0
    for kind, content, is_block in split_math_segments(text):
        if kind != "math":
            pos += len(content)
            continue
        start = text.find(content, pos)
        if start < 0:
            continue
        spans.append((start, start + len(content), content, is_block))
        pos = start + len(content)
    return spans


def strip_math_delimiters(text: str) -> str:
    """去掉公式标记只留内容（导出/格式化时使用）。"""
    return "".join(content for _, content, _ in split_math_segments(text))


def contains_math(text: str) -> bool:
    """文本里是否含公式片段。

    用于快速判断「要不要走公式渲染」：不含公式的调用点保持原有零开销路径。
    """
    if not text or "\\" not in text and "$" not in text:
        return False
    return any(kind == "math" for kind, _, _ in split_math_segments(text))


def normalize_text_keep_math(text: str) -> str:
    """清洗文本但**保留公式标记**。

    入库时必须用它而不是 ``latex_to_unicode``：后者会把 ``\\(`` ``\\)``
    删掉并把 ``\\frac{a}{b}`` 压成 ``a/b``，公式就再也到不了全局渲染器与导出层。
    这里只对公式之外的文本做 LaTeX→Unicode 清洗，公式片段原样保留。
    """
    if not text:
        return ""
    from .latex_clean import latex_to_unicode

    out: List[str] = []
    for kind, content, is_block in split_math_segments(text):
        if kind == "text":
            out.append(latex_to_unicode(content))
        else:
            out.append(f"\\[{content}\\]" if is_block else f"\\({content}\\)")
    return "".join(out)


# ----------------------------------------------------------------------
# LaTeX → HTML
# ----------------------------------------------------------------------

@dataclass
class _Node:
    """顶层节点：普通文本片段，或需要上下结构渲染的分式。"""

    kind: str                       # "text" | "frac"
    html: str = ""                  # kind == "text"
    num: List["_Node"] = field(default_factory=list)   # kind == "frac"
    den: List["_Node"] = field(default_factory=list)


def _read_group(text: str, start: int, depth: int = 0) -> Tuple[Optional[str], int]:
    """读取 text[start] 处的 {...}（支持嵌套），返回 (内容, 右括号之后的下标)。"""
    if start >= len(text) or text[start] != "{":
        return None, start
    level = 0
    for index in range(start, len(text)):
        ch = text[index]
        if ch == "\\":
            continue
        if ch == "{":
            level += 1
        elif ch == "}":
            level -= 1
            if level == 0:
                return text[start + 1:index], index + 1
    return None, start


def _read_operand(text: str, start: int) -> Tuple[str, int]:
    """读取上下标的操作数：{...} 或单个字符。"""
    if start >= len(text):
        return "", start
    if text[start] == "{":
        group, end = _read_group(text, start)
        if group is None:
            return "", start
        return group, end
    if text[start] == "\\":
        match = _COMMAND_RE.match(text, start)
        if match:
            return match.group(0), match.end()
    return text[start], start + 1


def _needs_paren(expr: str) -> bool:
    """线性书写分式时，分子/分母含顶层加减号才需要括号。"""
    depth = 0
    for index, ch in enumerate(expr):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and ch in "+-":
            if index > 0 and expr[index - 1] not in "([{":
                return True
    return False


def _symbol(name: str) -> Optional[str]:
    if name in _EXTRA_SYMBOLS:
        return _EXTRA_SYMBOLS[name]
    return unicode_symbol(name)


def _scan(text: str, depth: int = 0) -> List[_Node]:
    """扫描顶层，产出文本节点与分式节点（供独立公式的表格布局使用）。"""
    nodes: List[_Node] = []
    buffer: List[str] = []
    index = 0
    length = len(text)

    def flush() -> None:
        if buffer:
            nodes.append(_Node(kind="text", html="".join(buffer)))
            buffer.clear()

    while index < length:
        ch = text[index]

        if ch == "\\":
            match = _COMMAND_RE.match(text, index)
            if not match:
                # 转义字符或行分隔
                nxt = text[index + 1] if index + 1 < length else ""
                if nxt == "\\":
                    buffer.append("<br/>")
                    index += 2
                    continue
                if nxt in "{}%&#_$":
                    buffer.append(escape_html(nxt))
                    index += 2
                    continue
                if nxt == "," or nxt == ";" or nxt == ":":
                    buffer.append(" ")
                    index += 2
                    continue
                if nxt == "!":
                    index += 2
                    continue
                buffer.append("\\")
                index += 1
                continue

            name = match.group(1)
            cursor = match.end()

            if name in ("frac", "dfrac", "tfrac", "cfrac") and depth < _GROUP_LIMIT:
                num_end = cursor
                while num_end < length and text[num_end] in " \t":
                    num_end += 1
                num, after_num = _read_group(text, num_end)
                if num is not None:
                    den_start = after_num
                    while den_start < length and text[den_start] in " \t":
                        den_start += 1
                    den, after_den = _read_group(text, den_start)
                    if den is not None:
                        flush()
                        nodes.append(
                            _Node(
                                kind="frac",
                                num=_scan(num, depth + 1),
                                den=_scan(den, depth + 1),
                            )
                        )
                        index = after_den
                        continue
                # 残缺的 \frac：原样保留，不破坏后续内容
                buffer.append(escape_html(text[index:cursor]))
                index = cursor
                continue

            if name in ("sqrt", "sqrt2", "sqrt3"):
                kind = {"sqrt": "", "sqrt2": "2", "sqrt3": "3"}[name]
                cursor = _skip_spaces(text, cursor)
                if cursor < length and text[cursor] == "[":
                    close = text.find("]", cursor)
                    if close > 0:
                        kind = text[cursor + 1:close].strip()
                        cursor = _skip_spaces(text, close + 1)
                group, after = _read_group(text, cursor)
                if group is None:
                    buffer.append(escape_html(text[index:match.end()]))
                    index = match.end()
                    continue
                inner = _to_inline_html(group, depth + 1)
                prefix = "√" if not kind or kind == "2" else (
                    "∛" if kind == "3" else f"<sup>{escape_html(kind)}</sup>√"
                )
                buffer.append(
                    f"{prefix}<span style='text-decoration:overline'>{inner}</span>"
                )
                index = after
                continue

            if name in _TEXT_COMMANDS:
                cursor = _skip_spaces(text, cursor)
                group, after = _read_group(text, cursor)
                if group is None:
                    buffer.append(escape_html(text[index:match.end()]))
                    index = match.end()
                    continue
                buffer.append(escape_html(group))
                index = after
                continue

            if name in ("begin", "end"):
                cursor = _skip_spaces(text, cursor)
                group, after = _read_group(text, cursor)
                env = (group or "").strip()
                if name == "end":
                    index = after if group is not None else match.end()
                    continue
                body, after_body = _read_environment(text, after, env)
                if body is None:
                    index = after if group is not None else match.end()
                    continue
                if env in ("cases", "aligned", "array", "matrix", "pmatrix", "bmatrix"):
                    buffer.append(_render_environment(body, env, depth))
                else:
                    buffer.append(_to_inline_html(body, depth + 1))
                index = after_body
                continue

            if name in _DROPPED_COMMANDS:
                index = match.end()
                continue

            if name in ("bar", "hat", "vec", "tilde", "dot", "ddot", "overline"):
                cursor = _skip_spaces(text, cursor)
                group, after = _read_group(text, cursor)
                if group is None:
                    index = match.end()
                    continue
                inner = _to_inline_html(group, depth + 1)
                if name == "overline":
                    buffer.append(
                        f"<span style='text-decoration:overline'>{inner}</span>"
                    )
                else:
                    mark = {
                        "bar": "\u0304", "hat": "\u0302", "tilde": "\u0303",
                        "vec": "\u20d7", "dot": "\u0307", "ddot": "\u0308",
                    }[name]
                    buffer.append(f"{inner}{mark}")
                index = after
                continue

            symbol = _symbol(name)
            if symbol is None:
                # 未收录的命令：保留可读名称，不露出反斜杠
                buffer.append(escape_html(name))
            else:
                buffer.append(escape_html(symbol))
            index = match.end()
            continue

        if ch == "^" or ch == "_":
            operand, after = _read_operand(text, index + 1)
            inner = _to_inline_html(operand, depth + 1)
            tag = "sup" if ch == "^" else "sub"
            buffer.append(f"<{tag}>{inner}</{tag}>")
            index = after
            continue

        if ch == "{":
            group, after = _read_group(text, index)
            if group is None:
                buffer.append(escape_html(ch))
                index += 1
                continue
            buffer.append(_to_inline_html(group, depth + 1))
            index = after
            continue

        if ch == "}":
            index += 1
            continue

        if ch == "&":
            buffer.append("　")
            index += 1
            continue

        if ch == "~":
            buffer.append("&nbsp;")
            index += 1
            continue

        if ch in "$":
            index += 1
            continue

        buffer.append(escape_html(ch))
        index += 1

    flush()
    return nodes


def _skip_spaces(text: str, start: int) -> int:
    while start < len(text) and text[start] in " \t":
        start += 1
    return start


def _read_environment(text: str, start: int, env: str) -> Tuple[Optional[str], int]:
    """读取 \\begin{env} ... \\end{env} 之间的内容。"""
    marker = f"\\end{{{env}}}"
    end = text.find(marker, start)
    if end < 0:
        return None, start
    return text[start:end], end + len(marker)


def _render_environment(body: str, env: str, depth: int) -> str:
    """方程组 / 矩阵类环境 → 多行文本（Qt 行内元素无法排版真正的矩阵）。"""
    lines: List[str] = []
    for row in re.split(r"\\\\", body):
        cells = [cell.strip() for cell in row.split("&")]
        cells = [cell for cell in cells if cell]
        if cells:
            lines.append("　".join(_to_inline_html(cell, depth + 1) for cell in cells))
    if not lines:
        return ""
    joined = "<br/>".join(lines)
    if env in ("cases", "aligned", "array", "matrix"):
        return joined
    if env == "pmatrix":
        return f"({joined})"
    if env == "bmatrix":
        return f"[{joined}]"
    return joined


def _to_inline_html(text: str, depth: int = 0) -> str:
    """把子表达式渲染成行内 HTML；分式在此退化为 a/b 线性写法。"""
    if depth > _GROUP_LIMIT:
        return escape_html(text)
    parts: List[str] = []
    for node in _scan(text, depth):
        if node.kind == "text":
            parts.append(node.html)
            continue
        num = _to_inline_html_from_nodes(node.num, depth + 1)
        den = _to_inline_html_from_nodes(node.den, depth + 1)
        num_text, den_text = num.strip(), den.strip()
        plain_num = _plain_text(num_text)
        plain_den = _plain_text(den_text)
        if _needs_paren(plain_num):
            num_text = f"({num_text})"
        if _needs_paren(plain_den):
            den_text = f"({den_text})"
        parts.append(f"{num_text}<span>&#8260;</span>{den_text}")
    return "".join(parts)


def _to_inline_html_from_nodes(nodes: Sequence[_Node], depth: int) -> str:
    parts: List[str] = []
    for node in nodes:
        if node.kind == "text":
            parts.append(node.html)
            continue
        num = _to_inline_html_from_nodes(node.num, depth + 1)
        den = _to_inline_html_from_nodes(node.den, depth + 1)
        plain_num, plain_den = _plain_text(num), _plain_text(den)
        if _needs_paren(plain_num):
            num = f"({num})"
        if _needs_paren(plain_den):
            den = f"({den})"
        parts.append(f"{num}<span>&#8260;</span>{den}")
    return "".join(parts)


def _plain_text(html: str) -> str:
    """去掉标签，用于判断是否需要括号。"""
    return re.sub(r"<[^>]+>", "", html)


def latex_to_html(latex: str, block: bool = False) -> str:
    """单条公式 → HTML 片段；解析失败回退为原始 LaTeX 源码。"""
    source = (latex or "").strip()
    if not source:
        return ""
    try:
        nodes = _scan(source)
        if block and any(node.kind == "frac" for node in nodes):
            html = _render_stacked(nodes)
        else:
            html = _to_inline_html_from_nodes(nodes, 0)
        # 解析成功但什么都没剩下（例如未闭合环境）：同样回退源码，
        # 满足「解析失败要展示原始 LaTeX，不能空白」的要求
        if not html or not _plain_text(html).strip():
            return _fallback(source)
        return html
    except Exception as e:  # pragma: no cover - 兜底路径
        logger.warning("LaTeX 转 HTML 失败，回退源码: %s | %s", source[:60], e)
        return _fallback(source)


def _fallback(source: str) -> str:
    """兜底：把原始 LaTeX 当普通文本展示，保证不空白、不崩溃。"""
    return f"<span class='math-raw'>{escape_html(source)}</span>"


def _render_stacked(nodes: Sequence[_Node]) -> str:
    """独立公式：用一张两行表格把分式渲染成上下结构，运算符 rowspan 跨行。"""
    top_cells: List[str] = []
    bottom_cells: List[str] = []
    for node in nodes:
        if node.kind == "frac":
            num = _to_inline_html_from_nodes(node.num, 1)
            den = _to_inline_html_from_nodes(node.den, 1)
            top_cells.append(f"<td class='mnum'>{num}</td>")
            bottom_cells.append(f"<td class='mden'>{den}</td>")
        else:
            top_cells.append(f"<td class='mop' rowspan='2'>{node.html}</td>")
    if not top_cells:
        return _to_inline_html_from_nodes(nodes, 0)
    return (
        "<table class='mstack' cellspacing='0' cellpadding='0' align='center'>"
        f"<tr>{''.join(top_cells)}</tr>"
        f"<tr>{''.join(bottom_cells)}</tr>"
        "</table>"
    )


# ----------------------------------------------------------------------
# 完整文档
# ----------------------------------------------------------------------

def render_body(text: str, color: str, math_color: str) -> str:
    """把混排文本渲染成 HTML body 片段（不含 <html> 外壳）。"""
    parts: List[str] = []
    for kind, content, is_block in split_math_segments(text):
        if kind == "text":
            parts.append(text_to_html(content))
            continue
        parts.append(_formula_html(content, is_block))
    return "".join(parts)


def _formula_html(latex: str, is_block: bool) -> str:
    """公式片段 → 带锚点的 HTML，锚点里塞 base64 源码便于悬浮提示与复制。"""
    inner = latex_to_html(latex, block=is_block)
    payload = base64.urlsafe_b64encode(latex.encode("utf-8")).decode("ascii")
    if is_block:
        inner = f"<div class='mathblock'>{inner}</div>"
    return f"<a class='mathf' href='{MATH_HREF_SCHEME}:{payload}'>{inner}</a>"


def math_style(color: str, math_color: str, font_px: int = 13) -> str:
    """公式与混排所需的最小 CSS（颜色由主题注入）。"""
    return f"""
    body {{ color: {color}; font-size: {font_px}px; }}
    a.mathf {{ color: {math_color}; text-decoration: none; }}
    a.mathf span, a.mathf sup, a.mathf sub {{ color: {math_color}; }}
    .math-raw {{ color: {math_color}; }}
    .mathblock {{ text-align: center; margin: 6px 0; }}
    table.mstack {{ margin: 2px auto; }}
    table.mstack td.mnum {{
        border-bottom: 1px solid {math_color};
        padding: 0 4px; text-align: center;
    }}
    table.mstack td.mden {{ padding: 0 4px; text-align: center; }}
    table.mstack td.mop {{ padding: 0 3px; text-align: center; }}
    """
