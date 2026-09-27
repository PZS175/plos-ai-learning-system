"""LaTeX → Unicode 清洗器。

提供两个入口：
- ``latex_to_readable(text)``：保守清洗，上下标用 ``x^(2)`` 括号形式，
  用于 Word/PDF 导出等场景（向后兼容旧接口）。
- ``latex_to_unicode(text)``：完整 Unicode 转换，上下标使用真实
  Unicode 字符（``x²``、``a₁``、``Σᵢ₌₁ⁿ``），适合 TTS 朗读、
  纯文本展示、搜索索引。

命令行快速验证：
    py -m plos.utils.latex_clean "x^2 + \\frac{1}{2}"
    py -m plos.utils.latex_clean --legacy "x^2"
"""

from __future__ import annotations

import re
import sys
from typing import Dict, List, Optional

from .logger import get_logger

logger = get_logger("utils.latex_clean")

# =========================================================================
# 以下为保守版（latex_to_readable）所用的旧表与旧正则，保持原行为
# =========================================================================

# 命令 → Unicode 符号（长命令在前，避免被短命令抢先匹配）
_SYMBOLS: List[tuple] = sorted(
    {
        "leq": "≤",
        "le": "≤",
        "geq": "≥",
        "ge": "≥",
        "neq": "≠",
        "ne": "≠",
        "infty": "∞",
        "cup": "∪",
        "cap": "∩",
        "times": "×",
        "div": "÷",
        "pm": "±",
        "mp": "∓",
        "cdot": "·",
        "rightarrow": "→",
        "to": "→",
        "pi": "π",
        "alpha": "α",
        "beta": "β",
        "gamma": "γ",
        "delta": "δ",
        "Delta": "Δ",
        "theta": "θ",
        "in": "∈",
        "subset": "⊂",
        "subseteq": "⊆",
        "approx": "≈",
        "degree": "°",
    }.items(),
    key=lambda kv: -len(kv[0]),
)

_CMD = re.compile(r"\\([a-zA-Z]+)\s*")
_FRAC = re.compile(r"\\frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_SQRT = re.compile(r"\\sqrt\s*\{([^{}]*)\}")
_GROUP = re.compile(r"([_^])\s*\{([^{}]*)\}")
_DELIMS = re.compile(r"\\[()\[\]]")
_REST = re.compile(r"\\([a-zA-Z]+)\s*")


def latex_to_readable(text: str) -> str:
    """把 LaTeX 记号转成学生可读的 Unicode 文本。"""
    if not text or "\\" not in text and "$" not in text and "^" not in text and "_" not in text:
        return text or ""
    result = str(text)

    # 1) 剥定界符 \( \) \[ \] 与 $$
    result = _DELIMS.sub("", result)
    result = result.replace("$$", "").replace("$", "")

    # 2) 分式与根号（先处理，内部不再含嵌套的常见情形）
    for _ in range(3):  # 处理有限层嵌套
        before = result
        result = _FRAC.sub(lambda m: f"({m.group(1)})/({m.group(2)})", result)
        result = _SQRT.sub(lambda m: f"√({m.group(1)})", result)
        if result == before:
            break

    # 3) 命令 → 符号
    def _sym(m: "re.Match[str]") -> str:
        return dict(_SYMBOLS).get(m.group(1), f" {m.group(1)} ")

    result = _CMD.sub(_sym, result)

    # 4) 上下标分组转普通括号形式：x^{2} → x^(2)，a_{1} → a_(1)
    result = _GROUP.sub(lambda m: f"{m.group(1)}({m.group(2)})", result)
    # 4b) 裸上下标：x^2 → x^(2)、a_1 → a_(1)、x^10y → x^(10)y
    #     只吞纯数字或单个字母，避免把指数后的变量误吞进括号
    result = re.sub(r"\^([0-9]+|[a-zA-Z])", lambda m: f"^({m.group(1)})", result)
    result = re.sub(r"_([0-9]+|[a-zA-Z])", lambda m: f"_({m.group(1)})", result)

    # 5) 残余孤立反斜杠与花括号清理
    result = result.replace("\\", "").replace("{", "").replace("}", "")
    # 5b) 残缺花括号兜底：x^{2（无右括号）剥壳后重试一次上下标转换
    result = re.sub(r"\^([0-9]+|[a-zA-Z])", lambda m: f"^({m.group(1)})", result)
    result = re.sub(r"_([0-9]+|[a-zA-Z])", lambda m: f"_({m.group(1)})", result)
    result = re.sub(r"\s+", " ", result)
    logger.debug("LaTeX cleaned: %s", result[:50])
    return result.strip()


# =========================================================================
# 完整 Unicode 转换（latex_to_unicode）
# =========================================================================

# Unicode 上标字符表
_SUPERSCRIPTS: Dict[str, str] = {
    "0": "⁰", "1": "¹", "2": "²", "3": "³", "4": "⁴",
    "5": "⁵", "6": "⁶", "7": "⁷", "8": "⁸", "9": "⁹",
    "+": "⁺", "-": "⁻", "=": "⁼", "(": "⁽", ")": "⁾",
    "n": "ⁿ", "i": "ⁱ",
    "a": "ᵃ", "b": "ᵇ", "c": "ᶜ", "d": "ᵈ", "e": "ᵉ", "f": "ᶠ",
    "g": "ᵍ", "h": "ʰ", "j": "ʲ", "k": "ᵏ", "l": "ˡ", "m": "ᵐ",
    "o": "ᵒ", "p": "ᵖ", "r": "ʳ", "s": "ˢ", "t": "ᵗ", "u": "ᵘ",
    "v": "ᵛ", "w": "ʷ", "x": "ˣ", "y": "ʸ", "z": "ᶻ",
}

# Unicode 下标字符表
_SUBSCRIPTS: Dict[str, str] = {
    "0": "₀", "1": "₁", "2": "₂", "3": "₃", "4": "₄",
    "5": "₅", "6": "₆", "7": "₇", "8": "₈", "9": "₉",
    "+": "₊", "-": "₋", "=": "₌", "(": "₍", ")": "₎",
    "a": "ₐ", "e": "ₑ", "h": "ₕ", "i": "ᵢ", "j": "ⱼ", "k": "ₖ",
    "l": "ₗ", "m": "ₘ", "n": "ₙ", "o": "ₒ", "p": "ₚ", "r": "ᵣ",
    "s": "ₛ", "t": "ₜ", "u": "ᵤ", "v": "ᵥ", "x": "ₓ",
}

# 双线体（\mathbb）
_DOUBLE_STRUCK: Dict[str, str] = {
    "C": "ℂ", "H": "ℍ", "N": "ℕ", "P": "ℙ", "Q": "ℚ", "R": "ℝ", "Z": "ℤ",
    "A": "𝔸", "B": "𝔹", "D": "𝔻", "E": "𝔼", "F": "𝔽", "G": "𝔾",
    "I": "𝕀", "J": "𝕁", "K": "𝕂", "L": "𝕃", "M": "𝕄", "O": "𝕆",
    "S": "𝕊", "T": "𝕋", "U": "𝕌", "V": "𝕍", "W": "𝕎", "X": "𝕏", "Y": "𝕐",
}

# 重音命令 → 组合字符（附着在内容最后一个字符上）
_ACCENTS: Dict[str, str] = {
    "bar": "\u0304", "overline": "\u0304",
    "vec": "\u20d7",
    "hat": "\u0302", "widehat": "\u0302",
    "tilde": "\u0303", "widetilde": "\u0303",
    "dot": "\u0307", "ddot": "\u0308",
    "check": "\u030c", "breve": "\u0306",
    "acute": "\u0301", "grave": "\u0300", "mathring": "\u030a",
}

# 完整命令 → Unicode 符号 / 纯文本 / 空串
_UNICODE_COMMANDS: Dict[str, str] = {
    # 希腊字母（小写）
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ",
    "epsilon": "ε", "varepsilon": "ε", "zeta": "ζ", "eta": "η",
    "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ",
    "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ",
    "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ",
    "phi": "φ", "varphi": "ϕ", "chi": "χ", "psi": "ψ", "omega": "ω",
    # 希腊字母（大写）
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ",
    "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
    # 二元运算
    "times": "×", "div": "÷", "pm": "±", "mp": "∓",
    "cdot": "·", "ast": "∗", "star": "⋆", "bullet": "•",
    "oplus": "⊕", "ominus": "⊖", "otimes": "⊗", "oslash": "⊘",
    "odot": "⊙", "dagger": "†", "ddagger": "‡",
    # 关系符
    "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥",
    "neq": "≠", "ne": "≠", "equiv": "≡", "sim": "∼",
    "simeq": "≃", "approx": "≈", "cong": "≅", "propto": "∝",
    "ll": "≪", "gg": "≫", "prec": "≺", "succ": "≻",
    "preceq": "⪯", "succeq": "⪰",
    "subset": "⊂", "supset": "⊃", "subseteq": "⊆", "supseteq": "⊇",
    "nsubseteq": "⊈", "nsupseteq": "⊉",
    "in": "∈", "notin": "∉", "ni": "∋",
    # 集合
    "cup": "∪", "cap": "∩", "emptyset": "∅", "varnothing": "∅",
    "setminus": "∖", "bigcup": "⋃", "bigcap": "⋂",
    "bigoplus": "⨁", "bigotimes": "⨂",
    # 大算符
    "sum": "∑", "prod": "∏", "coprod": "∐",
    "int": "∫", "iint": "∬", "iiint": "∭", "oint": "∮",
    "bigvee": "⋁", "bigwedge": "⋀",
    # 逻辑
    "land": "∧", "wedge": "∧", "lor": "∨", "vee": "∨",
    "neg": "¬", "lnot": "¬", "forall": "∀", "exists": "∃",
    "nexists": "∄", "therefore": "∴", "because": "∵",
    "vdash": "⊢", "models": "⊨", "top": "⊤", "bot": "⊥",
    # 几何与杂项
    "angle": "∠", "triangle": "△", "perp": "⊥", "parallel": "∥",
    "nparallel": "∦", "square": "□", "diamond": "⋄",
    "degree": "°", "prime": "′", "circ": "∘",
    "partial": "∂", "nabla": "∇", "infty": "∞",
    "hbar": "ℏ", "ell": "ℓ", "Re": "ℜ", "Im": "ℑ", "aleph": "ℵ",
    "ldots": "…", "cdots": "⋯", "dots": "…", "vdots": "⋮", "ddots": "⋱",
    # 箭头
    "rightarrow": "→", "to": "→", "longrightarrow": "⟶",
    "xrightarrow": "→",
    "leftarrow": "←", "gets": "←", "longleftarrow": "⟵",
    "xleftarrow": "←",
    "leftrightarrow": "↔", "longleftrightarrow": "⟷",
    "Rightarrow": "⇒", "Longrightarrow": "⟹",
    "Leftarrow": "⇐", "Longleftarrow": "⟸",
    "Leftrightarrow": "⇔", "iff": "⇔", "Longleftrightarrow": "⟺",
    "uparrow": "↑", "downarrow": "↓", "updownarrow": "↕",
    "mapsto": "↦", "longmapsto": "⟼",
    "hookrightarrow": "↪", "hookleftarrow": "↩",
    "nearrow": "↗", "searrow": "↘", "swarrow": "↙", "nwarrow": "↖",
    "rightharpoonup": "⇀", "rightharpoondown": "⇁",
    "rightleftharpoons": "⇌",
    # 定界符
    "lfloor": "⌊", "rfloor": "⌋", "lceil": "⌈", "rceil": "⌉",
    "langle": "⟨", "rangle": "⟩", "Vert": "‖",
    "vert": "|", "lvert": "|", "rvert": "|", "backslash": "\\",
    # 函数名（转纯文本）
    "arccos": "arccos", "arcsin": "arcsin", "arctan": "arctan",
    "arg": "arg", "cos": "cos", "cosh": "cosh", "cot": "cot",
    "coth": "coth", "csc": "csc", "deg": "deg", "det": "det",
    "dim": "dim", "exp": "exp", "gcd": "gcd", "hom": "hom",
    "inf": "inf", "ker": "ker", "lg": "lg", "lim": "lim",
    "liminf": "liminf", "limsup": "limsup", "ln": "ln", "log": "log",
    "max": "max", "min": "min", "sec": "sec", "sin": "sin",
    "sinh": "sinh", "sup": "sup", "tan": "tan", "tanh": "tanh",
    # 转义字符
    "%": "%", "&": "&", "#": "#",
    # 间距与模式开关
    "quad": " ", "qquad": "  ",
    "thinspace": " ", "medspace": " ", "thickspace": " ",
    "displaystyle": "", "textstyle": "", "scriptstyle": "",
    "scriptscriptstyle": "", "limits": "", "nolimits": "",
}

# 按命令长度降序构造正则，保证 \Longrightarrow 先于 \Longmatch 抢占
_CMD_ALTERNATION = "|".join(
    re.escape(k) for k in sorted(_UNICODE_COMMANDS, key=len, reverse=True)
)
_UNICODE_CMD = re.compile(r"\\(" + _CMD_ALTERNATION + r")(?![a-zA-Z])")

_SIZING = re.compile(
    r"\\(?:left|right|bigl|bigr|Bigl|Bigr|biggl|biggr|Biggl|Biggr|big|Big)(?![a-zA-Z])"
)
_ESCAPED_CHAR = re.compile(r"\\([%&_#])")
_MATHBB = re.compile(r"\\mathbb\s*\{([^{}]*)\}")
_UNWRAP = re.compile(
    r"\\(?:text|textrm|textit|textbf|texttt|textsf|textnormal|textup|textsl|"
    r"textsc|mathrm|mathit|mathbf|mathsf|mathtt|mathnormal|mathcal|mathscr|"
    r"mathfrak|boldsymbol|bm|operatorname|mbox|hbox|ensuremath)\*?\s*\{([^{}]*)\}"
)
_ACCENT_RE = re.compile(
    r"\\(" + "|".join(sorted(_ACCENTS, key=len, reverse=True)) + r")\s*\{([^{}]*)\}"
)
_OVERARROW = re.compile(r"\\(overrightarrow|overleftarrow)\s*\{([^{}]*)\}")
_SQRT_N = re.compile(r"\\sqrt\s*\[([^\[\]]*)\]\s*\{([^{}]*)\}")
_FRAC2 = re.compile(r"\\(?:d|t)?frac\s*\{([^{}]*)\}\s*\{([^{}]*)\}")
_SPACING_CMD = re.compile(r"\\[,;:!]")
_SUPER_GROUP = re.compile(r"\^\s*\{([^{}]*)\}")
_SUB_GROUP = re.compile(r"_\s*\{([^{}]*)\}")
_SUPER_TOKEN = re.compile(r"\^\s*([0-9]+(?:\.[0-9]+)?|[a-zA-Z])")
_SUB_TOKEN = re.compile(r"_\s*([0-9]+(?:\.[0-9]+)?|[a-zA-Z])")

# 角度上标 \circ：45^\circ → 45°、45^{\circ} → 45°
_SUPER_CIRC = re.compile(r"\^\\circ(?![a-zA-Z])")
_SUPER_CIRC_GROUP = re.compile(r"\^\s*\{\s*\\circ\s*\}")

# 字面花括号的临时占位符（私用区字符，避免被清理步骤误删）
_LBRACE, _RBRACE = "\uE000", "\uE001"


def _to_positions(content: str, table: Dict[str, str]) -> Optional[str]:
    """尝试把整段文本逐字符转换为上标/下标；任一字符不可表示时返回 None。"""
    out: List[str] = []
    for ch in content:
        mapped = table.get(ch)
        if mapped is None:
            return None
        out.append(mapped)
    return "".join(out) if out else None


def _needs_paren(s: str) -> bool:
    """判断内联分式分子/分母是否需要加括号。

    只有当顶层（不在任何括号/花括号内）出现 + 或 - 二目运算符时才需要，
    例如 (x+1)/2、x/(y−1)；而 n(a₁+aₙ)/2、√2/3、x²−1 这类本身由
    乘法结合或自带括号的表达式无需再套一层括号。
    """
    depth = 0
    for i, ch in enumerate(s):
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0 and ch in "+-":
            # 排除表达式开头的负号（-x、-2）
            if i > 0 and s[i - 1] not in "([{":
                return True
    return False


def _handle_environments(text: str) -> str:
    """剥离 \\begin{...}/\\end{...} 环境，处理列分隔符与换行符。"""
    if not re.search(r"\\begin\s*\{", text):
        return text
    result = text.replace("&", " ")
    result = re.sub(r"\\\\\s*", "; ", result)
    result = re.sub(r"\\begin\s*\{[a-zA-Z*]+\}", "", result)
    result = re.sub(r"\\end\s*\{[a-zA-Z*]+\}", "", result)
    return result


def _frac_repl(m: "re.Match[str]") -> str:
    """分式转分数线形式，按需加括号：\\frac{1}{2}→1/2，\\frac{x+1}{2}→(x+1)/2。"""
    num, den = m.group(1).strip(), m.group(2).strip()
    num_paren, den_paren = _needs_paren(num), _needs_paren(den)
    if not num_paren and not den_paren:
        return f"{num}/{den}"
    if not num_paren:
        return f"{num}/({den})"
    if not den_paren:
        return f"({num})/{den}"
    return f"({num})/({den})"


def _sqrt_repl(m: "re.Match[str]") -> str:
    content = m.group(1).strip()
    return f"√{content}" if len(content) == 1 else f"√({content})"


def _sqrt_n_repl(m: "re.Match[str]") -> str:
    n, content = m.group(1).strip(), m.group(2).strip()
    inner = content if len(content) == 1 else f"({content})"
    if n == "3":
        return f"∛{inner}"
    if n == "4":
        return f"∜{inner}"
    return f"{inner}^(1/{n})"


def _accent_repl(m: "re.Match[str]") -> str:
    mark = _ACCENTS[m.group(1)]
    content = m.group(2).strip()
    if not content:
        return mark
    return content + mark  # 组合字符附着在最后一个字符上


def _super_group_repl(m: "re.Match[str]") -> str:
    content = m.group(1).strip()
    converted = _to_positions(content, _SUPERSCRIPTS)
    return converted if converted else f"^({content})"


def _sub_group_repl(m: "re.Match[str]") -> str:
    content = m.group(1).strip()
    converted = _to_positions(content, _SUBSCRIPTS)
    return converted if converted else f"_({content})"


def _super_token_repl(m: "re.Match[str]") -> str:
    content = m.group(1)
    converted = _to_positions(content, _SUPERSCRIPTS)
    return converted if converted else f"^({content})"


def _sub_token_repl(m: "re.Match[str]") -> str:
    content = m.group(1)
    converted = _to_positions(content, _SUBSCRIPTS)
    return converted if converted else f"_({content})"


def latex_to_unicode(text: str) -> str:
    """把 LaTeX 转成带真实 Unicode 上下标的可读文本。

    与 ``latex_to_readable`` 的区别：上下标优先使用 Unicode 字符
    （x²、a₁、Σᵢ₌₁ⁿ），分式按需加括号（1/2 而非 (1)/(2)），
    并支持 \\mathbb、重音符号、根号次数、矩阵环境等更多语法。
    无法用 Unicode 表示的内容回退为括号形式。
    """
    if not text:
        return ""
    if not any(tok in text for tok in ("\\", "$", "^", "_", "~", "{", "&")):
        return str(text).strip()

    result = str(text)

    # 1) 矩阵/aligned 等环境：剥壳，& 与 \\ 转普通分隔
    result = _handle_environments(result)
    # 2) 数学定界符 \( \) \[ \] 与 $ $、$$ $$
    result = _DELIMS.sub("", result)
    result = result.replace("$$", " ").replace("$", " ")

    # 2b) 角度上标：45^\circ → 45°（在 \circ 被替换成 ∘ 之前处理）
    result = _SUPER_CIRC_GROUP.sub("°", result)
    result = _SUPER_CIRC.sub("°", result)

    # 3) 括号尺寸命令与模式开关：\left( → (
    result = _SIZING.sub("", result)

    # 4) 转义字符与字面花括号（占位保护）
    result = _ESCAPED_CHAR.sub(lambda m: m.group(1), result)
    result = result.replace("\\{", _LBRACE).replace("\\}", _RBRACE)

    # 5) \mathbb{R} → ℝ
    for _ in range(3):
        new = _MATHBB.sub(lambda m: _DOUBLE_STRUCK.get(m.group(1), m.group(1)), result)
        if new == result:
            break
        result = new

    # 6) 解包 \text{...} / \mathrm{...} 等字体命令
    for _ in range(5):
        new = _UNWRAP.sub(lambda m: m.group(1), result)
        if new == result:
            break
        result = new

    # 7) 向量箭头 \overrightarrow{AB} → AB→
    result = _OVERARROW.sub(
        lambda m: f"{m.group(2)}{'→' if m.group(1) == 'overrightarrow' else '←'}",
        result,
    )

    # 8) 重音符号 → 组合字符（\bar{x} → x̄）
    result = _ACCENT_RE.sub(_accent_repl, result)

    # 9) 命令 → Unicode 符号 / 函数名 / 间距
    #    （先于根号与分式，使 \frac{\pi}{2} 分子成为原子 π，输出 π/2）
    result = _UNICODE_CMD.sub(lambda m: _UNICODE_COMMANDS[m.group(1)], result)

    # 10) 根号（含次数）：\sqrt{2} → √2，\sqrt[3]{x+1} → ∛(x+1)
    for _ in range(5):
        before = result
        result = _SQRT_N.sub(_sqrt_n_repl, result)
        result = re.sub(r"\\sqrt\s*\{([^{}]*)\}", _sqrt_repl, result)
        if result == before:
            break

    # 11) 分式（最内层优先，处理有限层嵌套）
    for _ in range(5):
        new = _FRAC2.sub(_frac_repl, result)
        if new == result:
            break
        result = new

    # 12) 空格命令与 ~
    result = _SPACING_CMD.sub(" ", result)
    result = result.replace("~", " ")

    # 13) 上下标：优先真实 Unicode 字符，否则括号形式
    result = _SUPER_GROUP.sub(_super_group_repl, result)
    result = _SUB_GROUP.sub(_sub_group_repl, result)
    result = _SUPER_TOKEN.sub(_super_token_repl, result)
    result = _SUB_TOKEN.sub(_sub_token_repl, result)

    # 14) 残余花括号、未知命令与孤立反斜杠
    result = result.replace("{", "").replace("}", "")
    result = result.replace(_LBRACE, "{").replace(_RBRACE, "}")
    result = re.sub(r"\\([a-zA-Z]+)", r"\1", result)  # 未知命令保留名称
    result = result.replace("\\", "")

    # 14b) 残缺花括号兜底：x^{2（无右括号）剥壳后重试一次上下标转换
    result = _SUPER_TOKEN.sub(_super_token_repl, result)
    result = _SUB_TOKEN.sub(_sub_token_repl, result)

    # 15) 收敛空白、标点与左括号后的多余空格
    result = re.sub(r"[ \t]+", " ", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = re.sub(r"\s+([,.;:!?、。，；：？！）)】\]》」』])", r"\1", result)
    result = re.sub(r"([(\[])\s+", r"\1", result)

    logger.debug("LaTeX → Unicode: %s", result[:50])
    return result.strip()


def unicode_symbol(name: str) -> Optional[str]:
    """查询 LaTeX 命令对应的 Unicode 符号（富文本渲染器复用同一张表）。

    返回 None 表示表中未收录该命令，调用方自行决定回退策略。
    """
    return _UNICODE_COMMANDS.get(name)


def _read_group(s: str, start: int) -> tuple:
    """读取 s[start] 处的 {...}（支持嵌套花括号），返回 (内容, 右括号后索引)。

    无花括号或括号不闭合时返回 (None, start)。
    """
    if start >= len(s) or s[start] != "{":
        return None, start
    depth = 0
    for k in range(start, len(s)):
        ch = s[k]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start + 1 : k], k + 1
    return None, start


_FRAC_TOKEN = re.compile(r"\\[dt]?frac\b")


def split_fractions(text: str) -> list:
    """把文本按分数命令拆分为交替片段，供渲染端绘制上下分数。

    返回 [(普通文本, None) | (None, (分子, 分母)), ...]。
    分子/分母内部可能仍含 LaTeX（含嵌套分数），由调用方递归处理；
    残缺的 \\frac（缺分子或分母花括号）原样保留在普通文本中。
    """
    out: list = []
    pos = 0
    if not text or not _FRAC_TOKEN.search(text):
        return [(text, None)] if text else []
    for m in _FRAC_TOKEN.finditer(text):
        if m.start() < pos:
            continue  # 嵌套在内层分数的分子/分母里，跳过
        j = m.end()
        while j < len(text) and text[j] in " \t":
            j += 1
        num, j2 = _read_group(text, j)
        if num is None:
            continue
        while j2 < len(text) and text[j2] in " \t":
            j2 += 1
        den, j3 = _read_group(text, j2)
        if den is None:
            continue
        if m.start() > pos:
            out.append((text[pos : m.start()], None))
        out.append((None, (num, den)))
        pos = j3
    if pos < len(text):
        out.append((text[pos:], None))
    return out


def _cli(argv: List[str]) -> int:
    """命令行入口：py -m plos.utils.latex_clean "<LaTeX>" [--legacy]。"""
    args = argv[1:]
    legacy = "--legacy" in args
    args = [a for a in args if a != "--legacy"]
    if not args or args[0] in ("-h", "--help"):
        print("用法: py -m plos.utils.latex_clean \"<LaTeX 文本>\" [--legacy]")
        print("  默认输出完整 Unicode（x²、∑ᵢ₌₁ⁿ）；--legacy 输出保守括号形式 x^(2)。")
        return 0
    text = " ".join(args)
    cleaned = latex_to_readable(text) if legacy else latex_to_unicode(text)
    print(cleaned)
    return 0


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
