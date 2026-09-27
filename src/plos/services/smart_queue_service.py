"""「今天学什么」智能队列。

综合三类信号给出当日学习任务的可解释排序：
1. SM-2 到期闪卡（遗忘曲线临界，最高优先）
2. 薄弱知识点错题重练（掌握度低）
3. 本周学习计划中未完成任务

每项都带"为什么推荐"的理由，排序可解释。
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List, Optional

from ..utils.logger import get_logger

logger = get_logger("services.smart_queue")


class SmartQueueService:
    """聚合多信号源生成每日学习队列。"""

    def __init__(
        self,
        db=None,
        user_service=None,
        flashcard_service=None,
        errorbook_service=None,
    ):
        if db is None:
            from ..db import get_db

            db = get_db()
        self.db = db
        self.user_service = user_service
        self.flashcard_service = flashcard_service
        self.errorbook_service = errorbook_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def _due_flashcard_items(self, uid: int) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        if self.flashcard_service is None:
            return items
        try:
            due = self.flashcard_service.get_due_cards(limit=5)
        except Exception as e:
            logger.warning("Smart queue: due cards unavailable: %s", e)
            return items
        for card in due:
            front = str(card.get("front_content", ""))[:32]
            items.append(
                {
                    "type": "flashcard",
                    "type_label": "闪卡复习",
                    "ref_id": card.get("id"),
                    "title": front,
                    "reason": "遗忘曲线到期，现在复习留存率最高",
                    "priority": 95,
                    "navigate": "flashcard",
                }
            )
        return items

    def _weak_point_items(self, uid: int) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        if self.errorbook_service is None:
            return items
        try:
            weak = self.errorbook_service.get_weak_knowledge_points(top_n=3) or []
        except Exception as e:
            logger.warning("Smart queue: weak points unavailable: %s", e)
            return items
        for wp in weak:
            point = wp.get("knowledge_point") or wp.get("point") or ""
            count = wp.get("error_count") or wp.get("count") or 0
            if not point:
                continue
            items.append(
                {
                    "type": "weak_review",
                    "type_label": "薄弱重练",
                    "ref_id": point,
                    "title": f"重练知识点「{point}」",
                    "reason": f"薄弱考点：{count} 道相关错题未掌握",
                    "priority": 85,
                    "navigate": "practice",
                }
            )
        return items

    def _plan_task_items(self, uid: int) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        if self.db is None:
            return items
        try:
            rows = self.db.fetchall(
                "SELECT t.id, t.title, p.title AS plan_title FROM study_plan_tasks t "
                "JOIN study_plans p ON p.id = t.plan_id "
                "WHERE t.user_id = ? AND t.is_completed = 0 ORDER BY t.id LIMIT 4",
                (uid,),
            )
        except Exception as e:
            logger.warning("Smart queue: plan tasks unavailable: %s", e)
            return items
        for row in rows:
            items.append(
                {
                    "type": "plan_task",
                    "type_label": "计划任务",
                    "ref_id": row["id"],
                    "title": str(row["title"])[:32],
                    "reason": f"来自学习计划「{row['plan_title']}」",
                    "priority": 75,
                    "navigate": "study_plan",
                }
            )
        return items

    def build_daily_queue(self, user_id: Optional[int] = None, limit: int = 10) -> Dict[str, Any]:
        """生成当日学习队列（按优先级降序，每项含推荐理由）。"""
        uid = self._user_id(user_id)
        items = (
            self._due_flashcard_items(uid)
            + self._weak_point_items(uid)
            + self._plan_task_items(uid)
        )
        items.sort(key=lambda x: x["priority"], reverse=True)
        queue = items[:limit]
        logger.info("Smart queue built: %d items for user %d", len(queue), uid)
        return {
            "date": date.today().isoformat(),
            "items": queue,
            "total": len(queue),
        }
