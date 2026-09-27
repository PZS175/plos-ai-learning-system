"""学习热力图组件：GitHub 风格的半年学习活动网格。

用颜色深浅表达每日学习时长，帮助用户直观感知学习习惯的连续性；
配套的连续天数（streak）由 StatisticsService 计算并在仪表盘展示。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QColor, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from .ui_utils import theme_colors

# 色阶阈值（分钟）：0 / 1-15 / 15-30 / 30-60 / 60+
_LEVEL_THRESHOLDS = (1, 15, 30, 45)

DAYS = 182  # 半年


class StudyHeatMapWidget(QWidget):
    """半年学习热力网格：7 行（周一至周日）× 26 列（周）。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._minutes_by_date: Dict[str, float] = {}
        self._cell_rects: List[tuple[QRect, str, float]] = []
        self._cell, self._gap = self._cell_metrics()
        self.setMinimumHeight(7 * (self._cell + self._gap) + 34)
        self.setMouseTracking(True)
        self._start_date = self._grid_start()

    def _cell_metrics(self) -> tuple[int, int]:
        """按当前宽度自适应格子尺寸（撑满卡片宽度）。"""
        gap = 4
        weeks = 26
        available = max(26 * 8, self.width() - 44)
        cell = max(9, min(18, available // weeks - gap))
        return cell, gap

    @staticmethod
    def _grid_start() -> date:
        """网格起点：包含今天在内的第 26 周的周一。"""
        today = date.today()
        # 回退到本周一
        monday = today - timedelta(days=today.weekday())
        return monday - timedelta(weeks=25)

    def set_data(self, records: List[Dict[str, float]]) -> None:
        """传入按日聚合的学习记录：[{"date": "YYYY-MM-DD", "minutes": n}]。"""
        self._minutes_by_date = {
            r["date"]: float(r.get("minutes", 0) or 0) for r in records
        }
        self._start_date = self._grid_start()
        self.updateGeometry()
        self.update()

    def _level_of(self, minutes: float) -> int:
        for level, threshold in enumerate(_LEVEL_THRESHOLDS):
            if minutes < threshold:
                return level
        return len(_LEVEL_THRESHOLDS)

    def _level_color(self, level: int) -> QColor:
        c = theme_colors()
        if level == 0:
            return QColor(c["bg_tertiary"])
        accent = QColor(c["accent"])
        # level 1-4：透明度递增的 accent 色阶
        alphas = (0, 70, 130, 200, 255)
        color = QColor(accent)
        color.setAlpha(alphas[min(level, 4)])
        return color

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = theme_colors()
        painter.fillRect(self.rect(), QColor(c["card_bg"]))

        self._cell_rects.clear()
        today = date.today()
        left_margin, top_margin = 16, 6
        cell, gap = self._cell_metrics()

        # 左侧星期标签（一 / 三 / 五）
        painter.setPen(QColor(c["fg_muted"]))
        font = painter.font()
        font.setPixelSize(10)
        painter.setFont(font)
        for weekday, label in ((0, "一"), (2, "三"), (4, "五")):
            y = top_margin + weekday * (cell + gap)
            painter.drawText(QRect(0, y, left_margin + 8, cell), Qt.AlignmentFlag.AlignVCenter, label)

        weeks = 26
        for week in range(weeks):
            for weekday in range(7):
                day = self._start_date + timedelta(weeks=week, days=weekday)
                if day > today:
                    continue
                x = left_margin + 10 + week * (cell + gap)
                y = top_margin + weekday * (cell + gap)
                minutes = self._minutes_by_date.get(day.isoformat(), 0.0)
                level = self._level_of(minutes)
                rect = QRect(x, y, cell, cell)
                # 注意：PyQt6 6.11 的 addRoundedRect(QRect, int, int) 重载存在
                # 原生崩溃（进程静默退出），必须用 qreal 参数重载 + fillPath
                path = QPainterPath()
                path.addRoundedRect(
                    float(x), float(y), float(cell), float(cell), 3.0, 3.0
                )
                painter.fillPath(path, self._level_color(level))
                if day == today:
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.setPen(QPen(QColor(c["accent_border"]), 1))
                    painter.drawRoundedRect(rect, 3, 3)
                self._cell_rects.append((rect, day.isoformat(), minutes))

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        from PyQt6.QtWidgets import QToolTip

        pos = event.pos()
        for rect, day_iso, minutes in self._cell_rects:
            if rect.contains(pos):
                tip = f"{day_iso}：{minutes:.0f} 分钟" if minutes else f"{day_iso}：未学习"
                QToolTip.showText(event.globalPosition().toPoint(), tip, self)
                return
        QToolTip.hideText()

    def leaveEvent(self, event) -> None:  # noqa: N802
        from PyQt6.QtWidgets import QToolTip

        QToolTip.hideText()
        super().leaveEvent(event)
