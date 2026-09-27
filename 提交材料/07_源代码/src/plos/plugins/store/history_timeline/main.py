"""插件库插件：历史朝代年表。

中国历史主要朝代（夏 → 中华人民共和国）的起止年代、都城、代表人物与重大事件：

- 按时间顺序排列，右侧展示详情并自动计算存续年数；
- 支持按朝代名 / 都城 / 人物 / 事件关键词搜索；
- 年代统一用公元纪年，公元前年份显示为「前 2070」并支持存续年数换算。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

# (朝代, 起始年, 结束年, 都城, 代表人物与大事)  年份为负数表示公元前，None 表示至今
_PERIODS: List[Tuple[str, int, Optional[int], str, str]] = [
    ("夏", -2070, -1600, "阳城、斟鄩", "禹、启；中国历史上第一个王朝，世袭制代替禅让制。"),
    ("商", -1600, -1046, "亳、殷", "商汤、盘庚；甲骨文与青铜器高度发达，盘庚迁殷。"),
    ("西周", -1046, -771, "镐京", "周武王、周公；实行分封制与礼乐制度。"),
    ("东周·春秋", -770, -476, "洛邑", "齐桓公、晋文公、孔子；春秋五霸，思想百家争鸣的开端。"),
    ("东周·战国", -475, -221, "洛邑", "商鞅变法、孟子、庄子；战国七雄，铁器牛耕推广。"),
    ("秦", -221, -207, "咸阳", "秦始皇；统一六国，推行郡县制、统一文字度量衡，修长城。"),
    ("西汉", -202, 8, "长安", "汉高祖刘邦、汉武帝；丝绸之路开通，张骞出使西域。"),
    ("东汉", 25, 220, "洛阳", "光武帝刘秀、蔡伦；改进造纸术，班超经营西域。"),
    ("三国", 220, 280, "洛阳、成都、建业", "曹操、刘备、孙权、诸葛亮；赤壁之战，魏蜀吴鼎立。"),
    ("西晋", 266, 316, "洛阳", "司马炎；结束三国分裂，短暂统一后陷入动乱。"),
    ("东晋", 317, 420, "建康", "王羲之、陶渊明、祖逖；淝水之战，书法与田园诗兴起。"),
    ("南北朝", 420, 589, "建康、平城、洛阳", "祖冲之、贾思勰；圆周率精确到七位小数，民族大融合。"),
    ("隋", 581, 618, "长安", "隋文帝、隋炀帝；开凿大运河，创立科举制。"),
    ("唐", 618, 907, "长安", "唐太宗、武则天、李白、杜甫；贞观之治与开元盛世，诗歌鼎盛。"),
    ("五代十国", 907, 960, "开封等", "政权更迭频繁，南方经济持续发展。"),
    ("北宋", 960, 1127, "开封", "宋太祖赵匡胤、王安石、苏轼；活字印刷术、指南针应用于航海。"),
    ("南宋", 1127, 1279, "临安", "岳飞、辛弃疾、文天祥；经济重心南移完成。"),
    ("元", 1271, 1368, "大都", "忽必烈、郭守敬；行省制度确立，马可·波罗来华。"),
    ("明", 1368, 1644, "南京、北京", "明太祖朱元璋、郑和、李时珍；郑和下西洋，修《本草纲目》。"),
    ("清", 1636, 1912, "北京", "康熙、乾隆；康乾盛世，1840 年鸦片战争后逐步沦为半殖民地。"),
    ("中华民国", 1912, 1949, "南京", "辛亥革命推翻帝制，五四运动与新文化运动兴起。"),
    ("中华人民共和国", 1949, None, "北京", "1949 年 10 月 1 日成立，开启社会主义建设新时期。"),
]

_CURRENT_YEAR = 2026


def _year_text(year: Optional[int]) -> str:
    if year is None:
        return "至今"
    return f"前 {abs(year)}" if year < 0 else f"{year}"


def _duration_text(start: int, end: Optional[int]) -> str:
    finish = _CURRENT_YEAR if end is None else end
    return f"约 {abs(finish - start)} 年"


class HistoryPanel(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._filtered: List[Tuple[str, int, Optional[int], str, str]] = list(_PERIODS)
        self._build_ui()
        self._apply_filter("")

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        c = self._colors
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("中国历史朝代年表")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        head.addWidget(title)
        head.addStretch()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索：朝代 / 都城 / 人物，如 唐、长安、郑和")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(280)
        self.search_edit.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 8px 10px;"
        )
        self.search_edit.textChanged.connect(self._apply_filter)
        head.addWidget(self.search_edit)
        root.addLayout(head)

        hint = QLabel("年代采用公元纪年，「前」表示公元前；存续年数按起止年份计算。")
        hint.setStyleSheet(f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;")
        root.addWidget(hint)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.list_widget = QListWidget()
        self.list_widget.setMinimumWidth(230)
        self.list_widget.itemClicked.connect(self._show_detail)
        self.list_widget.currentRowChanged.connect(self._on_row_changed)
        self.list_widget.setStyleSheet(
            f"QListWidget {{ background: {c.get('card_bg', '#fff')};"
            f" border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
            f" color: {c.get('fg_primary', '#111827')}; padding: 4px; }}"
            f"QListWidget::item:selected {{ background: {c.get('accent_light', 'rgba(79,93,245,0.12)')}; }}"
        )
        splitter.addWidget(self.list_widget)

        detail = QFrame()
        detail.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 12px;"
        )
        detail_layout = QVBoxLayout(detail)
        detail_layout.setContentsMargins(24, 22, 24, 22)
        detail_layout.setSpacing(12)

        self.name_label = QLabel("选择左侧朝代查看")
        self.name_label.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 26px; font-weight: bold;"
        )
        detail_layout.addWidget(self.name_label)

        info_card = QFrame()
        info_card.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')}; border-radius: 10px;"
        )
        info_layout = QVBoxLayout(info_card)
        info_layout.setContentsMargins(18, 16, 18, 16)
        info_layout.setSpacing(6)
        self.span_label = QLabel("")
        self.span_label.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 17px;"
        )
        self.capital_label = QLabel("")
        self.capital_label.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 15px;"
        )
        info_layout.addWidget(self.span_label)
        info_layout.addWidget(self.capital_label)
        detail_layout.addWidget(info_card)

        self.event_label = QLabel("")
        self.event_label.setWordWrap(True)
        self.event_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 15px;"
        )
        detail_layout.addWidget(self.event_label)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.copy_btn = QPushButton("📋 复制条目")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setStyleSheet(
            f"background: {c.get('accent_light', 'rgba(79,93,245,0.12)')};"
            f"color: {c.get('accent', '#4f5df5')};"
            "border: none; border-radius: 8px; padding: 8px 16px; font-weight: bold;"
        )
        self.copy_btn.clicked.connect(self._copy_item)
        button_row.addWidget(self.copy_btn)
        detail_layout.addLayout(button_row)
        detail_layout.addStretch()
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 1)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;"
        )
        root.addWidget(self.count_label)

    # ------------------------------------------------------------------
    def _apply_filter(self, text: str) -> None:
        keyword = text.strip().lower()
        if keyword:
            self._filtered = [
                item
                for item in _PERIODS
                if keyword in item[0].lower()
                or keyword in item[3].lower()
                or keyword in item[4].lower()
                or keyword in _year_text(item[1])
                or keyword in _year_text(item[2])
            ]
        else:
            self._filtered = list(_PERIODS)

        self.list_widget.clear()
        for name, start, end, _capital, _event in self._filtered:
            self.list_widget.addItem(
                QListWidgetItem(f"{name}　{_year_text(start)} — {_year_text(end)}")
            )
        self.count_label.setText(f"共 {len(self._filtered)} 个时期")
        if self._filtered:
            self.list_widget.setCurrentRow(0)
            self._show_detail()
        else:
            self.name_label.setText("没有匹配的条目")
            self.span_label.setText("")
            self.capital_label.setText("")
            self.event_label.setText("")

    def _on_row_changed(self, _row: int) -> None:
        self._show_detail()

    def _show_detail(self, _item: Optional[QListWidgetItem] = None) -> None:
        row = self.list_widget.currentRow()
        if not (0 <= row < len(self._filtered)):
            return
        name, start, end, capital, event = self._filtered[row]
        self.name_label.setText(name)
        self.span_label.setText(
            f"起止：{_year_text(start)} — {_year_text(end)}（{_duration_text(start, end)}）"
        )
        self.capital_label.setText(f"都城：{capital}")
        self.event_label.setText(event)

    def _copy_item(self) -> None:
        row = self.list_widget.currentRow()
        if not (0 <= row < len(self._filtered)):
            return
        name, start, end, capital, event = self._filtered[row]
        QApplication.clipboard().setText(
            f"{name}（{_year_text(start)}—{_year_text(end)}）都城：{capital}\n{event}"
        )
        self.copy_btn.setText("✓ 已复制")
        QTimer.singleShot(1200, lambda: self.copy_btn.setText("📋 复制条目"))


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "朝代年表",
        lambda c: HistoryPanel(c),
        icon="🏯",
        subtitle="中国历史朝代起止、都城与大事速查",
    )
