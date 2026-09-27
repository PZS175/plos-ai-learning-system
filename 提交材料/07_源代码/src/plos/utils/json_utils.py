"""大模型 JSON 输出的健壮解析工具。

本地 7B 级模型输出 JSON 时常伴：代码块围栏、前后解释文字、
尾逗号、中文引号/冒号、对象包裹数组等。本模块提供容错提取，
失败时返回诊断信息供上层重试与报错。
"""

from __future__ import annotations

import json
import re
from typing import Any, List, Optional

from .logger import get_logger

logger = get_logger("utils.json_utils")

# 常见全角标点 → 半角（仅用于修复尝试，不改变语义文本内容）
_FULLWIDTH_FIXES = [
    ("“", '"'), ("”", '"'),
    ("‘", "'"), ("’", "'"),
    ("，", ","), ("：", ":"), ("；", ";"),
]


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json")[1].split("```")[0].strip()
    elif "```" in cleaned:
        # 取第一个围栏段；若围栏未闭合则取围栏之后全部
        parts = cleaned.split("```")
        cleaned = parts[1].strip() if len(parts) > 1 else cleaned
    return cleaned


def _slice_outer(text: str, open_ch: str, close_ch: str) -> Optional[str]:
    start = text.find(open_ch)
    end = text.rfind(close_ch)
    if start < 0 or end <= start:
        return None
    return text[start : end + 1]


def _repair(candidate: str) -> str:
    """常见 JSON 语法修复：非法反斜杠、尾逗号、全角标点。

    重点：模型在 JSON 字符串里写 LaTeX 定界符（反斜杠括号等）会产生
    JSON 非法转义，需把不构成合法转义的孤立反斜杠双写为字面反斜杠。
    """
    # 合法转义：\" \\ \/ \b \f \n \r \t \uXXXX；其余反斜杠双写
    fixed = re.sub(r"\\(?![\"\\/bfnrtu])", r"\\\\", candidate)
    fixed = re.sub(r",\s*([\]}])", r"\1", fixed)
    for full, half in _FULLWIDTH_FIXES:
        fixed = fixed.replace(full, half)
    return fixed


def _loads_lenient(candidate: str) -> Optional[Any]:
    for text in (candidate, _repair(candidate)):
        if not text:
            continue
        try:
            return json.loads(text, strict=False)
        except Exception:
            continue
    return None


def _unwrap_list(data: Any) -> Optional[List[dict]]:
    """模型常把数组包在对象里（{"questions": [...]}），尝试解包。"""
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("questions", "items", "data", "results", "list", "题目"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return None


def extract_json_array(text: str) -> Optional[List[dict]]:
    """从模型输出提取 JSON 数组（容忍包裹对象/截断），失败返回 None。"""
    if not text:
        return None
    cleaned = _strip_code_fences(text)
    start = cleaned.find("[")
    body = cleaned[start:] if start >= 0 else cleaned
    # 无闭合 ]（截断）时直接以整段为候选，交给截断修复
    sliced = body[: body.rfind("]") + 1] if "]" in body else body
    if sliced is not None:
        data = _loads_lenient(sliced)
        result = _unwrap_list(data)
        if result is not None:
            return result
        # 截断修复：输出在 max_tokens 处被切掉时，取最后一个完整对象并闭合数组
        last_obj = sliced.rfind("}")
        if last_obj > 0:
            data = _loads_lenient(sliced[: last_obj + 1] + "]")
            result = _unwrap_list(data)
            if result:
                logger.warning("Recovered truncated JSON array (%d items)", len(result))
                return result
    # 数组切片失败时，尝试整段解析（可能是包裹对象）
    data = _loads_lenient(_slice_outer(cleaned, "{", "}") or cleaned)
    return _unwrap_list(data)


def extract_json_object(text: str) -> Optional[dict]:
    """从模型输出提取 JSON 对象，失败返回 None。"""
    if not text:
        return None
    cleaned = _strip_code_fences(text)
    sliced = _slice_outer(cleaned, "{", "}")
    if sliced is None:
        return None
    data = _loads_lenient(sliced)
    return data if isinstance(data, dict) else None


def raw_snippet(text: str, limit: int = 200) -> str:
    """截取模型原始输出片段用于错误提示与日志定位。"""
    snippet = " ".join((text or "").split())
    return snippet[:limit] + ("…" if len(snippet) > limit else "")
