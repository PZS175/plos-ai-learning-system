"""AI 错因画像：把错题按错因归入四象限，并给出训练处方。

启发式规则（不依赖模型，离线可用）：
- 有 AI 批改错因记录（subjective_grades.error_reasons）→ 关键词归类
- 无记录 → 按数据信号推断：掌握度低 + 多次练习错误 = 概念不清；
  掌握度尚可但错误 = 计算失误/审题偏差的保守降级为"待诊断"

四象限：概念不清 / 计算失误 / 审题偏差 / 知识空白，各配训练处方。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger

logger = get_logger("services.cause_profile")

# 错因关键词 → 象限
_KEYWORD_MAP = {
    "概念": "concept",
    "定义": "concept",
    "公式用错": "concept",
    "计算": "calculation",
    "算错": "calculation",
    "符号": "calculation",
    "审题": "comprehension",
    "看错": "comprehension",
    "理解错": "comprehension",
    "没有思路": "blank",
    "不会": "blank",
    "空白": "blank",
}

_PRESCRIPTIONS = {
    "concept": {
        "label": "概念不清",
        "color": "error",
        "prescription": "回到定义：重读对应章节的概念讲解，做 3 道基础概念判断题，再返回错题重练。",
    },
    "calculation": {
        "label": "计算失误",
        "color": "warning",
        "prescription": "限时训练：每天 10 分钟口算/步骤演算专项，写全中间步骤，减少跳步。",
    },
    "comprehension": {
        "label": "审题偏差",
        "color": "info",
        "prescription": "圈画训练：读题时圈出条件与问句，复述题目要求后再动笔，练习 5 道同型题。",
    },
    "blank": {
        "label": "知识空白",
        "color": "purple",
        "prescription": "先补前置：该知识点可能没学透，建议从教材对应章节重学，再由易到难刷题。",
    },
}


class CauseProfileService:
    """错因四象限画像与训练处方。"""

    def __init__(self, db=None, user_service=None, errorbook_service=None):
        if db is None:
            from ..db import get_db

            db = get_db()
        self.db = db
        self.user_service = user_service
        self.errorbook_service = errorbook_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def get_cause_profile(self, user_id: Optional[int] = None) -> Dict[str, Any]:
        """返回四象限画像：每象限错题数、占比与训练处方。"""
        uid = self._user_id(user_id)
        rows = self.db.fetchall(
            "SELECT e.id, e.mastery_level, e.question, "
            "COALESCE(g.error_reasons, '') AS reasons "
            "FROM error_book e "
            "LEFT JOIN subjective_grades g ON g.error_id = e.id "
            "WHERE e.user_id = ?",
            (uid,),
        )
        counts = {k: 0 for k in _PRESCRIPTIONS}
        examples: Dict[str, List[str]] = {k: [] for k in _PRESCRIPTIONS}
        for row in rows:
            cause = self._classify(row)
            counts[cause] += 1
            if len(examples[cause]) < 3:
                q = str(row["question"] or "")[:24]
                if q:
                    examples[cause].append(q)

        total = sum(counts.values())
        quadrants = []
        for key, meta in _PRESCRIPTIONS.items():
            n = counts[key]
            quadrants.append(
                {
                    "key": key,
                    "label": meta["label"],
                    "color": meta["color"],
                    "count": n,
                    "ratio": round(n / total * 100, 1) if total else 0.0,
                    "prescription": meta["prescription"],
                    "examples": examples[key],
                }
            )
        quadrants.sort(key=lambda x: x["count"], reverse=True)
        logger.info("Cause profile built: total=%d", total)
        return {"total": total, "quadrants": quadrants}

    def _classify(self, row: Dict[str, Any]) -> str:
        """单题错因归类：优先用 AI 批改错因关键词，否则按掌握度降级推断。"""
        reasons = str(row.get("reasons") or "")
        for keyword, key in _KEYWORD_MAP.items():
            if keyword in reasons:
                return key
        mastery = row.get("mastery_level", 0) or 0
        if mastery == 0:
            return "blank"
        if mastery == 1:
            return "concept"
        return "calculation"
