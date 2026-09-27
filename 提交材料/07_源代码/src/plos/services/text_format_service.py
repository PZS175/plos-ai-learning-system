"""一键格式化文本服务。

面向数学场景的文本规整，供各页面统一调用：
- 清理 OCR 噪声：零宽字符、全角标点混排、中文之间多余空格、重复标点、多余空行
- 修复数学符号：``<=``/``>=``/``!=`` 等写法归一，LaTeX 命令后的全角括号纠正
- 规整 LaTeX 标记：补齐未闭合的 ``$``、把中文全角括号里的公式参数改半角
- 自动套标记：把明显是数学表达式但没带 ``\\( \\)`` 的片段补上标记，送入全局渲染

设计原则：宁可少改，不可改坏。所有规则都是保守的文本级替换，
不解析语义、不重排语序；无法确定的内容原样保留。
"""

from __future__ import annotations

import re
from typing import List

from ..utils.logger import get_logger
from ..utils.math_latex import split_math_segments

logger = get_logger("services.text_format_service")

#: 零宽字符与 BOM
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\ufeff\u2060"), None)
#: 中文/日文标点与汉字范围（判断「中文之间」用）
_CJK = r"\u4e00-\u9fff\u3400-\u4dbf"
_CJK_BETWEEN_SPACE = re.compile(rf"([{_CJK}])[ \t]+([{_CJK}])")
#: 重复标点（问号/感叹号/句号/逗号/顿号）
_DUP_PUNCT = re.compile(r"([？！。，、；：])\1+")
#: 全角空格
_IDEOGRAPHIC_SPACE = "\u3000"
#: 需要归一的比较/运算符写法
_SYMBOL_FIXES = (
    (re.compile(r"<="), r"\\leq "),
    (re.compile(r">="), r"\\geq "),
    (re.compile(r"!="), r"\\neq "),
    (re.compile(r"(?<![<>=!])<>(?![<>=])"), r"\\neq "),
    (re.compile(r"\*\*"), "^"),
)
#: 带参数的命令（OCR 常把参数花括号识别成全角圆括号）
_ARG_COMMAND = r"\\(?:frac|dfrac|tfrac|sqrt|text|textrm|mathrm|mathbf|overline|operatorname)"
#: \frac（1）（2） → \frac{1}{2}
_TWO_GROUP_FULLWIDTH = re.compile(rf"({_ARG_COMMAND})\s*（([^（）]*)）\s*（([^（）]*)）")
#: \sqrt（2） / \text（速度） → 花括号
_ONE_GROUP_FULLWIDTH = re.compile(rf"({_ARG_COMMAND})\s*（([^（）]*)）")
#: x^（2） / a_（1） → 上下标花括号
_SUPER_SUB_FULLWIDTH = re.compile(r"([\^_])\s*（([^（）]*)）")

#: 明显是数学表达式的片段：不含中文、不跨行，且含运算符与数字/字母
_MATH_SPAN = re.compile(
    r"[0-9A-Za-z\u0370-\u03ff√∛π·×÷≤≥≠≈∞"
    r"\\{}^_+\-*/=<>.()\[\],;:| ]{2,80}"
)
_MATH_SIGNAL = re.compile(r"[=+\-*/^_<>≤≥≠√∛×÷]")
_MATH_ATOM = re.compile(r"[0-9A-Za-z\u0370-\u03ffπ]")
#: 已经有 LaTeX 命令但没带标记的片段
_BARE_LATEX_CMD = re.compile(r"\\(?:frac|sqrt|dfrac|tfrac|times|div|cdot|leq|geq|neq|pm)\b")
#: 带标记的公式命令
_ALREADY_MARKED = re.compile(r"\\[\(\[]")


class TextFormatService:
    """文本规整服务（无 Qt 依赖，可用于任意页面）。"""

    def format_text(self, text: str) -> str:
        """一键格式化：清理噪声 → 规整标记 → 补公式标记 → 归一符号。

        「补标记」必须早于「归一符号」：先把裸算式包进公式片段，
        后续的符号归一才会在公式上下文里生效（两者互不打架且幂等）。
        """
        if not text:
            return text or ""
        result = text
        for step in (
            self.clean_ocr_noise,
            self.normalize_latex_markers,
            self.wrap_math_expressions,
            self.fix_math_symbols,
        ):
            try:
                result = step(result)
            except Exception as e:  # 单步失败不影响整体
                logger.warning("格式化步骤 %s 失败: %s", getattr(step, "__name__", step), e)
        return result

    # ------------------------------------------------------------------
    # 1. OCR 噪声清理
    # ------------------------------------------------------------------

    def clean_ocr_noise(self, text: str) -> str:
        """清掉不可见字符、全角空格、中文间空格、重复标点与多余空行。"""
        if not text:
            return text or ""
        result = text.translate(_INVISIBLE)
        result = result.replace("\r\n", "\n").replace("\r", "\n")
        result = result.replace(_IDEOGRAPHIC_SPACE, " ")
        # 中文之间的空格通常是 OCR 误拆，去掉；中英之间保留一个空格
        for _ in range(3):
            new = _CJK_BETWEEN_SPACE.sub(r"\1\2", result)
            if new == result:
                break
            result = new
        result = _DUP_PUNCT.sub(r"\1", result)
        result = re.sub(r"[ \t]{2,}", " ", result)
        result = re.sub(r"[ \t]+\n", "\n", result)
        result = re.sub(r"\n{3,}", "\n\n", result)
        return result.strip()

    # ------------------------------------------------------------------
    # 2. 数学符号修复
    # ------------------------------------------------------------------

    def fix_math_symbols(self, text: str) -> str:
        """把常见非标准写法归一为规范 LaTeX 命令，并纠正全角括号。"""
        if not text:
            return text or ""
        segments = split_math_segments(text)
        out: List[str] = []
        for kind, content, is_block in segments:
            if kind == "text":
                out.append(content)
                continue
            out.append(self._normalize_math_span(content, is_block))
        return "".join(out)

    @staticmethod
    def _normalize_math_span(content: str, is_block: bool) -> str:
        """公式片段内部的写法归一（全角括号、上下标括号、比较符、幂运算符）。"""
        fixed = content
        # 全角圆括号在公式里通常是花括号被 OCR 认错：先按命令参数还原，
        # 再处理上下标，最后剩下的才当普通括号改半角
        fixed = _TWO_GROUP_FULLWIDTH.sub(r"\1{\2}{\3}", fixed)
        fixed = _ONE_GROUP_FULLWIDTH.sub(r"\1{\2}", fixed)
        fixed = _SUPER_SUB_FULLWIDTH.sub(r"\1{\2}", fixed)
        fixed = fixed.replace("（", "(").replace("）", ")")
        for pattern, replacement in _SYMBOL_FIXES:
            fixed = pattern.sub(replacement, fixed)
        fixed = re.sub(r"[ \t]{2,}", " ", fixed).strip()
        return f"\\[{fixed}\\]" if is_block else f"\\({fixed}\\)"

    # ------------------------------------------------------------------
    # 3. LaTeX 标记规整
    # ------------------------------------------------------------------

    def normalize_latex_markers(self, text: str) -> str:
        """补齐孤立的 ``$``（成对缺失时按普通文本处理，不做破坏性改动）。"""
        if not text:
            return text or ""
        if text.count("$") % 2 == 0:
            return text
        # 奇数个 $：去掉最后一个孤立的 $，避免它把后面的正文吞成公式
        head, _sep, tail = text.rpartition("$")
        logger.debug("检测到未配对的 $，已移除最后一个")
        return f"{head}{tail}"

    # ------------------------------------------------------------------
    # 4. 自动套用公式标记
    # ------------------------------------------------------------------

    def wrap_math_expressions(self, text: str) -> str:
        """给没有标记但明显是数学表达式的片段补上 ``\\( \\)``。

        保守策略：
        - 只处理普通文本片段（已带标记的公式不动）
        - 片段必须不含中文，且同时含「运算符」与「数字/字母」
        - 片段长度 2~80 字符，去掉首尾空白后仍满足条件才加标记
        """
        if not text:
            return text or ""
        out: List[str] = []
        for kind, content, is_block in split_math_segments(text):
            if kind == "math":
                out.append(f"\\[{content}\\]" if is_block else f"\\({content}\\)")
                continue
            out.append(self._wrap_in_text(content))
        return "".join(out)

    def _wrap_in_text(self, text: str) -> str:
        if not text:
            return text

        def _replace(match: "re.Match[str]") -> str:
            span = match.group(0)
            stripped = span.strip()
            if len(stripped) < 2:
                return span
            # 裸 LaTeX 命令没有运算符（如 \frac{1}{2}），要单独放行
            bare_command = bool(_BARE_LATEX_CMD.search(stripped))
            if not bare_command:
                if not _MATH_SIGNAL.search(stripped) or not _MATH_ATOM.search(stripped):
                    return span
            if not self._looks_like_math(stripped):
                return span
            # 保留首尾空白，只给中间内容加标记
            head = span[: len(span) - len(span.lstrip())]
            tail = span[len(span.rstrip()):]
            return f"{head}\\({stripped}\\){tail}"

        return _MATH_SPAN.sub(_replace, text)

    @staticmethod
    def _looks_like_math(span: str) -> bool:
        """排除掉「ASCII 单词串」「日期/编号」等误伤场景。"""
        if "\\(" in span or "\\[" in span or "\\)" in span or "\\]" in span:
            return False
        if _BARE_LATEX_CMD.search(span):
            return True
        # 必须有「强数学信号」：否则 2024-09-11、3-5 这类日期/编号也会被当公式
        strong = bool(re.search(r"[=×÷^_√∛≤≥≠]", span)) or bool(
            re.search(r"[A-Za-z\u0370-\u03ff]", span)
        )
        if not strong:
            return False
        if re.search(r"[0-9]\s*[A-Za-z\u0370-\u03ff]", span) or re.search(
            r"[A-Za-z\u0370-\u03ff]\s*[0-9]", span
        ):
            return True
        if re.search(r"\^|_", span):
            return True
        if re.search(r"[√∛]", span):
            return True
        if re.search(r"[A-Za-z\u0370-\u03ff]\s*[=+\-*/×÷≤≥≠]", span):
            return True
        if re.search(r"[=+\-*/×÷]\s*[0-9]", span):
            return True
        return False


_service: TextFormatService | None = None


def get_text_format_service() -> TextFormatService:
    """获取全局文本格式化服务（无状态，可复用）。"""
    global _service
    if _service is None:
        _service = TextFormatService()
    return _service
