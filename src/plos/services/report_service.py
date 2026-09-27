"""家长/教师学习报告：本地生成 PDF，数据不出门。

汇总指定周期的核心指标：专注时长与趋势、连续天数、错题与错因
四象限、TOP 薄弱知识点、训练处方。使用 PyMuPDF 绘制，无需新依赖。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional

import fitz

from ..utils.logger import get_logger

logger = get_logger("services.report")

_PAGE_W, _PAGE_H = 595, 842  # A4 竖版（pt）
_MARGIN = 50


class ReportService:
    """生成家长/教师版学习报告 PDF。"""

    def __init__(self, db=None, user_service=None, statistics_service=None,
                 errorbook_service=None, cause_profile_service=None,
                 smart_queue_service=None):
        if db is None:
            from ..db import get_db

            db = get_db()
        self.db = db
        self.user_service = user_service
        self.statistics_service = statistics_service
        self.errorbook_service = errorbook_service
        self.cause_profile_service = cause_profile_service
        self.smart_queue_service = smart_queue_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def _collect(self, user_id: int) -> Dict[str, Any]:
        data: Dict[str, Any] = {"nickname": ""}
        if self.user_service is not None:
            user = self.user_service.get_user(user_id) or {}
            data["nickname"] = user.get("nickname") or user.get("username") or ""
        if self.statistics_service is not None:
            try:
                data["weekly"] = self.statistics_service.get_weekly_report(user_id=user_id)
                data["streak"] = self.statistics_service.get_study_streak(user_id=user_id)
            except Exception as e:
                logger.warning("Report: statistics unavailable: %s", e)
        if self.cause_profile_service is not None:
            try:
                data["cause"] = self.cause_profile_service.get_cause_profile(user_id=user_id)
            except Exception as e:
                logger.warning("Report: cause profile unavailable: %s", e)
        if self.errorbook_service is not None:
            try:
                data["weak"] = self.errorbook_service.get_weak_knowledge_points(top_n=5, user_id=user_id) or []
            except Exception as e:
                logger.warning("Report: weak points unavailable: %s", e)
        return data

    @staticmethod
    def _trend(now: float, prev: float) -> str:
        if prev <= 0:
            return f"上周 {prev:.0f}"
        pct = (now - prev) / prev * 100.0
        arrow = "↑" if pct >= 0 else "↓"
        return f"上周 {prev:.0f}，{arrow} {abs(pct):.0f}%"

    def generate(
        self,
        output_path: Path,
        user_id: Optional[int] = None,
        student_name: str = "",
    ) -> Path:
        """生成报告 PDF，返回文件路径。"""
        uid = self._user_id(user_id)
        data = self._collect(uid)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        doc = fitz.open()
        page = doc.new_page(width=_PAGE_W, height=_PAGE_H)
        y = _MARGIN

        def text(line: str, size: float = 11, color=(0.1, 0.12, 0.19), bold: bool = False,
                gap: float = 6) -> None:
            nonlocal y
            font = "china-s" if not bold else "china-ss"
            page.insert_text((_MARGIN, y), line, fontsize=size, fontname=font, color=color)
            y += size + gap

        def section(title: str) -> None:
            nonlocal y
            y += 8
            text(title, size=13, bold=True, gap=8)
            page.draw_line(
                fitz.Point(_MARGIN, y - 4), fitz.Point(_PAGE_W - _MARGIN, y - 4),
                color=(0.42, 0.49, 0.96), width=1,
            )

        # 封面头部
        today = date.today().isoformat()
        text("PLOS AI 学习周报", size=20, bold=True, gap=10)
        student = student_name or data.get("nickname") or "同学"
        text(f"学生：{student}    报告日期：{today}", size=10, color=(0.35, 0.39, 0.47), gap=12)

        # 一、本周概览
        section("一、本周概览")
        w = data.get("weekly") or {}
        minutes = float(w.get("minutes", 0) or 0)
        minutes_prev = float(w.get("minutes_prev", 0) or 0)
        streak = int(data.get("streak", 0) or 0)
        text(f"· 本周专注学习 {minutes:.0f} 分钟（{self._trend(minutes, minutes_prev)}）")
        text(f"· 连续学习 {streak} 天")
        text(f"· 新增错题 {int(w.get('errors', 0) or 0)} 道（{self._trend(float(w.get('errors', 0) or 0), float(w.get('errors_prev', 0) or 0))}）")
        text(f"· 闪卡复习 {int(w.get('reviews', 0) or 0)} 张（{self._trend(float(w.get('reviews', 0) or 0), float(w.get('reviews_prev', 0) or 0))}）")
        acc = float(w.get("accuracy", 0) or 0)
        acc_prev = float(w.get("accuracy_prev", 0) or 0)
        text(f"· 复习正确率 {acc:.0f}%（上周 {acc_prev:.0f}%）")

        # 二、错因画像
        cause = data.get("cause") or {}
        quadrants = [q for q in cause.get("quadrants", []) if q.get("count")]
        if quadrants:
            section("二、错因画像与训练处方")
            for q in quadrants:
                text(
                    f"· {q['label']}：{q['count']} 题（{q['ratio']:.0f}%）",
                    size=11, bold=True, gap=4,
                )
                text(f"  处方：{q['prescription']}", size=10, color=(0.35, 0.39, 0.47), gap=8)
        else:
            section("二、错因画像与训练处方")
            text("· 本周暂无错题数据，继续保持！", color=(0.35, 0.39, 0.47))

        # 三、薄弱知识点 TOP5
        weak = data.get("weak") or []
        section("三、薄弱知识点 TOP5")
        if weak:
            for wp in weak:
                point = wp.get("knowledge_point") or wp.get("point") or ""
                cnt = wp.get("error_count") or wp.get("count") or 0
                text(f"· {point}（相关错题 {cnt} 道）")
        else:
            text("· 暂无薄弱知识点数据", color=(0.35, 0.39, 0.47))

        # 四、给家长的话
        section("四、给家长的话")
        tips = [
            "建议每周与孩子一起查看本报告，重点关注错因象限占比的变化趋势；",
            "「概念不清」增多时优先陪孩子回归教材，而不是刷更多题；",
            "「计算失误」为主时鼓励限时训练，减少对答案密度的焦虑；",
            "学习数据全部保存在本机，本报告由软件在本地生成。",
        ]
        for tip in tips:
            text(f"· {tip}", size=10, color=(0.35, 0.39, 0.47), gap=5)

        doc.set_metadata({"title": "PLOS AI 学习周报", "author": "PLOS AI"})
        doc.save(str(output_path))
        doc.close()
        logger.info("Parent report generated: %s", output_path)
        return output_path
