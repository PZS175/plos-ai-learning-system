"""学习统计服务。

记录并汇总学习时长、错题分布、闪卡复习正确率等数据，供仪表盘图表使用。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from ..db import Database, get_db
from ..utils.logger import get_logger
from .user_service import UserService

logger = get_logger("services.statistics_service")


class StatisticsService:
    """学习统计业务服务。"""

    def __init__(self, db: Optional[Database] = None, user_service: Optional[UserService] = None):
        self.db = db or get_db()
        self.user_service = user_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def record(
        self,
        record_type: str,
        value: float,
        subject: str = "",
        detail: str = "",
        record_date: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """记录一条统计数据。"""
        uid = self._user_id(user_id)
        if record_date is None:
            record_date = datetime.now().strftime("%Y-%m-%d")
        record_id = self.db.insert(
            "INSERT INTO statistics_records (user_id, record_type, record_date, subject, value, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uid, record_type, record_date, subject, value, detail),
        )
        return record_id

    def record_study_duration(
        self,
        minutes: int,
        subject: str = "",
        user_id: Optional[int] = None,
        record_date: Optional[str] = None,
    ) -> int:
        """记录学习时长（分钟）。"""
        return self.record(
            "study_duration",
            float(minutes),
            subject=subject,
            detail="学习时长",
            user_id=user_id,
            record_date=record_date,
        )

    def record_flashcard_review(
        self,
        rating_value: int,
        subject: str = "",
        user_id: Optional[int] = None,
        record_date: Optional[str] = None,
    ) -> int:
        """记录闪卡复习结果（rating 值）。"""
        return self.record(
            "flashcard_review",
            float(rating_value),
            subject=subject,
            detail="闪卡复习评分",
            user_id=user_id,
            record_date=record_date,
        )

    def get_daily_study_duration(
        self, days: int = 7, user_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """获取最近 N 天每日学习时长。"""
        uid = self._user_id(user_id)
        end = datetime.now()
        start = end - timedelta(days=days - 1)
        rows = self.db.fetchall(
            "SELECT record_date, SUM(value) AS total FROM statistics_records "
            "WHERE user_id = ? AND record_type = 'study_duration' AND record_date >= ? "
            "GROUP BY record_date ORDER BY record_date ASC",
            (uid, start.strftime("%Y-%m-%d")),
        )
        date_map = {r["record_date"]: r["total"] for r in rows}
        result = []
        for i in range(days):
            date_str = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            result.append({"date": date_str, "minutes": date_map.get(date_str, 0) or 0})
        return result

    def get_weekly_report(self, user_id: Optional[int] = None) -> Dict[str, Any]:
        """本周学习速览：时长/新增错题/复习量/正确率，并与上周同期对比。

        周起点为周一；"上周"取完整上一周（便于对比整周趋势）。
        """
        uid = self._user_id(user_id)
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        this_start = monday.strftime("%Y-%m-%d")
        last_start = (monday - timedelta(days=7)).strftime("%Y-%m-%d")

        def minutes_since(start: str, end: Optional[str] = None) -> float:
            sql = (
                "SELECT COALESCE(SUM(value), 0) AS m FROM statistics_records "
                "WHERE user_id = ? AND record_type = 'study_duration' AND record_date >= ?"
            )
            params: List[Any] = [uid, start]
            if end:
                sql += " AND record_date < ?"
                params.append(end)
            row = self.db.fetchone(sql, tuple(params))
            return float(row["m"] or 0)

        def errors_since(start: str, end: Optional[str] = None) -> int:
            sql = "SELECT COUNT(*) AS c FROM error_book WHERE user_id = ? AND date(created_at) >= ?"
            params: List[Any] = [uid, start]
            if end:
                sql += " AND date(created_at) < ?"
                params.append(end)
            row = self.db.fetchone(sql, tuple(params))
            return int(row["c"] or 0)

        def review_stats(start: str, end: Optional[str] = None) -> Dict[str, float]:
            sql = (
                "SELECT COUNT(*) AS c, "
                "COALESCE(SUM(CASE WHEN value >= 2 THEN 1 ELSE 0 END), 0) AS ok "
                "FROM statistics_records "
                "WHERE user_id = ? AND record_type = 'flashcard_review' AND record_date >= ?"
            )
            params: List[Any] = [uid, start]
            if end:
                sql += " AND record_date < ?"
                params.append(end)
            row = self.db.fetchone(sql, tuple(params))
            total = int(row["c"] or 0)
            ok = int(row["ok"] or 0)
            return {"reviews": total, "accuracy": (ok / total * 100.0) if total else 0.0}

        this_reviews = review_stats(this_start)
        last_reviews = review_stats(last_start, this_start)
        return {
            "minutes": minutes_since(this_start),
            "minutes_prev": minutes_since(last_start, this_start),
            "errors": errors_since(this_start),
            "errors_prev": errors_since(last_start, this_start),
            "reviews": this_reviews["reviews"],
            "reviews_prev": last_reviews["reviews"],
            "accuracy": round(this_reviews["accuracy"], 1),
            "accuracy_prev": round(last_reviews["accuracy"], 1),
        }

    def get_study_streak(self, user_id: Optional[int] = None) -> int:
        """连续学习天数：以今天（或今天未学则从昨天）向前连续有学习记录的天数。"""
        days = self.get_daily_study_duration(days=365, user_id=user_id)
        streak = 0
        for index in range(len(days) - 1, -1, -1):
            minutes = days[index].get("minutes", 0) or 0
            if minutes > 0:
                streak += 1
                continue
            # 今天还没学不打断连续性，从昨天起算
            if streak == 0 and index == len(days) - 1:
                continue
            break
        return streak

    def get_error_subject_distribution(
        self, user_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """获取错题科目分布。"""
        uid = self._user_id(user_id)
        rows = self.db.fetchall(
            "SELECT subject, COUNT(*) AS count FROM error_book "
            "WHERE user_id = ? AND subject != '' GROUP BY subject ORDER BY count DESC",
            (uid,),
        )
        return [{"subject": r["subject"], "count": r["count"]} for r in rows]

    def get_flashcard_accuracy(
        self, days: int = 7, user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """获取闪卡复习正确率统计。"""
        uid = self._user_id(user_id)
        end = datetime.now()
        start = end - timedelta(days=days - 1)
        rows = self.db.fetchall(
            "SELECT record_date, value FROM statistics_records "
            "WHERE user_id = ? AND record_type = 'flashcard_review' AND record_date >= ? "
            "ORDER BY record_date ASC",
            (uid, start.strftime("%Y-%m-%d")),
        )
        date_map: Dict[str, List[float]] = {}
        for r in rows:
            date_map.setdefault(r["record_date"], []).append(r["value"])

        daily = []
        total_reviews = 0
        total_score = 0
        for i in range(days):
            date_str = (start + timedelta(days=i)).strftime("%Y-%m-%d")
            scores = date_map.get(date_str, [])
            if scores:
                avg = sum(scores) / len(scores)
                # 将 0-3 评分映射为正确率
                accuracy = min(100.0, max(0.0, avg / 3.0 * 100))
                daily.append({"date": date_str, "accuracy": round(accuracy, 1), "count": len(scores)})
                total_reviews += len(scores)
                total_score += sum(scores)
            else:
                daily.append({"date": date_str, "accuracy": 0.0, "count": 0})

        overall = 0.0
        if total_reviews > 0:
            overall = min(100.0, max(0.0, (total_score / total_reviews) / 3.0 * 100))
        return {"daily": daily, "overall": round(overall, 1), "total_reviews": total_reviews}

    def get_dashboard_summary(self, user_id: Optional[int] = None) -> Dict[str, Any]:
        """获取仪表盘汇总数据。"""
        uid = self._user_id(user_id)
        row = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM error_book WHERE user_id = ?", (uid,)
        )
        total_errors = row["c"] if row else 0
        row = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM error_book WHERE user_id = ? AND mastery_level = 2", (uid,)
        )
        mastered = row["c"] if row else 0
        row = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM error_book WHERE user_id = ? AND mastery_level < 2", (uid,)
        )
        pending = row["c"] if row else 0
        today = datetime.now().strftime("%Y-%m-%d")
        row = self.db.fetchone(
            "SELECT SUM(value) AS total FROM statistics_records "
            "WHERE user_id = ? AND record_type = 'study_duration' AND record_date = ?",
            (uid, today),
        )
        today_duration = row["total"] if row else 0
        return {
            "total_errors": total_errors,
            "mastered": mastered,
            "pending": pending,
            "today_minutes": int(today_duration or 0),
        }
