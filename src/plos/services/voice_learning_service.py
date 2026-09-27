"""语音学习场景：听写/跟读的文本比对评分引擎。

TTS 播报 → 学生作答（键盘输入或 ASR 转写）→ 与原文比对，
输出得分、漏词、多词与逐句反馈。纯规则实现，离线可用。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from ..utils.logger import get_logger

logger = get_logger("services.voice_learning")

# 归一化：去标点空白、转小写（中文不受影响）
_PUNCT = re.compile("[" + re.escape("，。！？、；：" + chr(0x201C) + chr(0x201D) + chr(0x2018) + chr(0x2019) + "（）") + r"\[\]{}\s,.!?;:()\"']+")


class VoiceLearningService:
    """听写/跟读评分。"""

    def __init__(self, user_service=None):
        self.user_service = user_service

    @staticmethod
    def normalize(text: str) -> List[str]:
        """归一化为词元列表（中文按字，英文按词）。"""
        cleaned = _PUNCT.sub("", str(text or "")).lower()
        tokens: List[str] = []
        buf = ""
        for ch in cleaned:
            if "\u4e00" <= ch <= "\u9fff":
                if buf:
                    tokens.append(buf)
                    buf = ""
                tokens.append(ch)
            elif ch.isalnum():
                buf += ch
            else:
                if buf:
                    tokens.append(buf)
                    buf = ""
        if buf:
            tokens.append(buf)
        return tokens

    def grade(self, original: str, spoken: str) -> Dict[str, Any]:
        """比对原文与作答，返回得分与差异明细。

        得分 = 命中字/词数 / 原文词元数（百分比，四舍五入）。
        """
        origin_tokens = self.normalize(original)
        spoken_tokens = self.normalize(spoken)

        if not origin_tokens:
            return {"score": 0, "missing": [], "extra": [], "feedback": "原文为空，无法评分"}

        # 统计原文词元命中情况（spoken 侧多重集消耗）
        spoken_pool: Dict[str, int] = {}
        for tok in spoken_tokens:
            spoken_pool[tok] = spoken_pool.get(tok, 0) + 1

        missing: List[str] = []
        hit = 0
        for tok in origin_tokens:
            if spoken_pool.get(tok, 0) > 0:
                spoken_pool[tok] -= 1
                hit += 1
            else:
                missing.append(tok)

        origin_pool: Dict[str, int] = {}
        for tok in origin_tokens:
            origin_pool[tok] = origin_pool.get(tok, 0) + 1
        # extra：spoken 中超出原文频次的词元
        remaining = dict(spoken_pool)
        extras: List[str] = []
        for tok in spoken_tokens:
            if origin_pool.get(tok, 0) > 0 and remaining.get(tok, 0) > 0:
                remaining[tok] -= 1
            elif extras and extras[-1] == tok:
                extras[-1] += tok  # 合并连续同词
            else:
                extras.append(tok)

        score = round(hit / len(origin_tokens) * 100)
        if score >= 95:
            feedback = "满分！完全正确。"
        elif score >= 80:
            feedback = "很好，只有少量遗漏。"
        elif score >= 60:
            feedback = "及格，注意漏掉的部分，建议再听一遍。"
        else:
            feedback = "需要加油：漏掉较多，建议分句跟读。"

        result = {
            "score": score,
            "hit": hit,
            "total": len(origin_tokens),
            "missing": missing,
            "extra": extras,
            "feedback": feedback,
        }
        logger.info("Voice grade: score=%s missing=%s extra=%s", score, len(missing), len(extras))
        return result
