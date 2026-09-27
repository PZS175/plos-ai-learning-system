"""艾宾浩斯遗忘复习调度服务。

为每道做过的题目记录作答时间、对错、AI 评估的记忆难度，
按 1/3/7/15/30 天遗忘曲线节点计算下次复习时间。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from ..db import Database, get_db
from ..utils.logger import get_logger
from .user_service import UserService

logger = get_logger("services.review_service")

# 艾宾浩斯复习间隔（天）
_REVIEW_INTERVALS = [1, 3, 7, 15, 30]

# 源类型
SOURCE_PRACTICE = "practice"
SOURCE_ERROR = "error_book"
SOURCE_FLASHCARD = "flashcard"


class ReviewService:
    """艾宾浩斯复习调度服务。"""

    def __init__(self, db: Optional[Database] = None, user_service: Optional[UserService] = None):
        self.db = db or get_db()
        self.user_service = user_service
        self._ensure_table()

    def _ensure_table(self) -> None:
        try:
            self.db.get_connection().executescript(
                """
                CREATE TABLE IF NOT EXISTS review_schedules (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL DEFAULT 0,
                    source_type TEXT NOT NULL DEFAULT 'practice',
                    source_id INTEGER NOT NULL,
                    last_review TIMESTAMP DEFAULT NULL,
                    next_review TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    interval_days INTEGER NOT NULL DEFAULT 1,
                    interval_index INTEGER NOT NULL DEFAULT 0,
                    difficulty REAL NOT NULL DEFAULT 0.5,
                    correct_count INTEGER NOT NULL DEFAULT 0,
                    wrong_count INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, source_type, source_id)
                );
                CREATE INDEX IF NOT EXISTS idx_review_schedules_user_next
                    ON review_schedules(user_id, next_review);
                """
            )
            self.db.get_connection().commit()
        except Exception as e:
            logger.warning("Failed to ensure review_schedules table: %s", e)

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def schedule_review(
        self,
        source_type: str,
        source_id: int,
        is_correct: bool,
        difficulty: float = 0.5,
        user_id: Optional[int] = None,
    ) -> None:
        """记录一次作答并更新复习计划。

        - 答对：推进到下一个间隔节点
        - 答错：回退到第一个间隔（1 天）
        - ``difficulty`` 由 AI 评估，范围 0~1，影响间隔微调
        """
        uid = self._user_id(user_id)
        conn = self.db.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM review_schedules WHERE user_id = ? AND source_type = ? AND source_id = ?",
                (uid, source_type, source_id),
            ).fetchone()

            now = datetime.utcnow()
            if row is None:
                # 首次记录：从第一个间隔开始
                interval_idx = 0
                interval_days = _REVIEW_INTERVALS[0]
                correct_count = 1 if is_correct else 0
                wrong_count = 0 if is_correct else 1
            else:
                correct_count = row["correct_count"] + (1 if is_correct else 0)
                wrong_count = row["wrong_count"] + (0 if is_correct else 1)
                if is_correct:
                    interval_idx = min(row["interval_index"] + 1, len(_REVIEW_INTERVALS) - 1)
                else:
                    interval_idx = 0  # 答错回退
                interval_days = _REVIEW_INTERVALS[interval_idx]

            # 难度微调：难度高则间隔略缩短，难度低则略延长
            factor = 1.0 + (0.5 - difficulty) * 0.4
            actual_days = max(1, int(round(interval_days * factor)))
            next_review = now + timedelta(days=actual_days)

            conn.execute(
                """INSERT INTO review_schedules
                   (user_id, source_type, source_id, last_review, next_review,
                    interval_days, interval_index, difficulty, correct_count, wrong_count)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, source_type, source_id) DO UPDATE SET
                   last_review = excluded.last_review,
                   next_review = excluded.next_review,
                   interval_days = excluded.interval_days,
                   interval_index = excluded.interval_index,
                   difficulty = excluded.difficulty,
                   correct_count = excluded.correct_count,
                   wrong_count = excluded.wrong_count""",
                (uid, source_type, source_id, now, next_review,
                 actual_days, interval_idx, difficulty, correct_count, wrong_count),
            )
            conn.commit()
        except Exception as e:
            logger.error("Failed to schedule review: %s", e)
            raise

    def get_due_reviews(self, user_id: Optional[int] = None) -> List[dict]:
        """获取当前用户所有到期需复习的条目。"""
        uid = self._user_id(user_id)
        return self.db.fetchall(
            """SELECT * FROM review_schedules
               WHERE user_id = ? AND next_review <= CURRENT_TIMESTAMP
               ORDER BY next_review ASC""",
            (uid,),
        )

    def get_due_count(self, user_id: Optional[int] = None) -> int:
        """获取今日待复习数量。"""
        uid = self._user_id(user_id)
        row = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM review_schedules WHERE user_id = ? AND next_review <= CURRENT_TIMESTAMP",
            (uid,),
        )
        return int(row["c"]) if row else 0

    def postpone_review(
        self, source_type: str, source_id: int, days: int = 1, user_id: Optional[int] = None
    ) -> None:
        """推迟复习指定天数（1 天或 3 天）。"""
        uid = self._user_id(user_id)
        self.db.execute(
            """UPDATE review_schedules SET next_review = datetime('now', ? || ' days')
               WHERE user_id = ? AND source_type = ? AND source_id = ?""",
            (f"+{days}", uid, source_type, source_id),
        )

    def get_review_stats(self, user_id: Optional[int] = None) -> dict:
        """获取复习统计：总数、待复习、已掌握（最后一个间隔）。"""
        uid = self._user_id(user_id)
        total = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM review_schedules WHERE user_id = ?", (uid,)
        )
        due = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM review_schedules WHERE user_id = ? AND next_review <= CURRENT_TIMESTAMP",
            (uid,),
        )
        mastered = self.db.fetchone(
            f"SELECT COUNT(*) AS c FROM review_schedules WHERE user_id = ? AND interval_index >= {len(_REVIEW_INTERVALS) - 1}",
            (uid,),
        )
        return {
            "total": int(total["c"]) if total else 0,
            "due": int(due["c"]) if due else 0,
            "mastered": int(mastered["c"]) if mastered else 0,
        }


__all__ = ["ReviewService", "SOURCE_PRACTICE", "SOURCE_ERROR", "SOURCE_FLASHCARD"]
