"""插件库插件：英语不规则动词。

- 75 个中学常用不规则动词：原形 / 过去式 / 过去分词 / 中文；
- 按变化类型自动归类（AAA 三式相同、ABB 过去式＝过去分词、ABA 原形＝过去分词、ABC 三式不同）；
- 支持搜索，以及「自测模式」：给出原形与中文，默写过去式和过去分词，即时判对错并统计。
"""

from __future__ import annotations

import random
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

# (原形, 过去式, 过去分词, 中文)
_VERBS: List[Tuple[str, str, str, str]] = [
    # AAA：三式相同
    ("cut", "cut", "cut", "切、割"),
    ("hit", "hit", "hit", "打、撞"),
    ("hurt", "hurt", "hurt", "伤害"),
    ("let", "let", "let", "让"),
    ("put", "put", "put", "放"),
    ("read", "read", "read", "读（读音变化）"),
    ("set", "set", "set", "设置"),
    ("shut", "shut", "shut", "关闭"),
    ("cost", "cost", "cost", "花费"),
    ("spread", "spread", "spread", "传播"),
    # ABA：原形＝过去分词
    ("become", "became", "become", "变成"),
    ("come", "came", "come", "来"),
    ("run", "ran", "run", "跑"),
    # ABB：过去式＝过去分词
    ("bring", "brought", "brought", "带来"),
    ("build", "built", "built", "建造"),
    ("buy", "bought", "bought", "买"),
    ("catch", "caught", "caught", "抓住"),
    ("dig", "dug", "dug", "挖"),
    ("feel", "felt", "felt", "感觉"),
    ("fight", "fought", "fought", "打架"),
    ("find", "found", "found", "找到"),
    ("get", "got", "got", "得到"),
    ("hang", "hung", "hung", "悬挂"),
    ("have", "had", "had", "有"),
    ("hear", "heard", "heard", "听见"),
    ("hold", "held", "held", "握住"),
    ("keep", "kept", "kept", "保持"),
    ("lead", "led", "led", "带领"),
    ("leave", "left", "left", "离开"),
    ("lend", "lent", "lent", "借出"),
    ("lose", "lost", "lost", "丢失"),
    ("make", "made", "made", "制造"),
    ("mean", "meant", "meant", "意味着"),
    ("meet", "met", "met", "遇见"),
    ("pay", "paid", "paid", "支付"),
    ("say", "said", "said", "说"),
    ("sell", "sold", "sold", "卖"),
    ("send", "sent", "sent", "发送"),
    ("shine", "shone", "shone", "照耀"),
    ("shoot", "shot", "shot", "射击"),
    ("sit", "sat", "sat", "坐"),
    ("sleep", "slept", "slept", "睡觉"),
    ("spend", "spent", "spent", "花费"),
    ("stand", "stood", "stood", "站立"),
    ("strike", "struck", "struck", "打击"),
    ("sweep", "swept", "swept", "打扫"),
    ("teach", "taught", "taught", "教"),
    ("tell", "told", "told", "告诉"),
    ("think", "thought", "thought", "想"),
    ("understand", "understood", "understood", "理解"),
    ("win", "won", "won", "赢"),
    # ABC：三式不同
    ("be", "was/were", "been", "是"),
    ("begin", "began", "begun", "开始"),
    ("bite", "bit", "bitten", "咬"),
    ("blow", "blew", "blown", "吹"),
    ("break", "broke", "broken", "打破"),
    ("choose", "chose", "chosen", "选择"),
    ("do", "did", "done", "做"),
    ("draw", "drew", "drawn", "画"),
    ("drink", "drank", "drunk", "喝"),
    ("drive", "drove", "driven", "驾驶"),
    ("eat", "ate", "eaten", "吃"),
    ("fall", "fell", "fallen", "落下"),
    ("fly", "flew", "flown", "飞"),
    ("forget", "forgot", "forgotten", "忘记"),
    ("freeze", "froze", "frozen", "结冰"),
    ("give", "gave", "given", "给"),
    ("go", "went", "gone", "去"),
    ("grow", "grew", "grown", "生长"),
    ("hide", "hid", "hidden", "隐藏"),
    ("know", "knew", "known", "知道"),
    ("lie", "lay", "lain", "躺"),
    ("mistake", "mistook", "mistaken", "弄错"),
    ("ride", "rode", "ridden", "骑"),
    ("ring", "rang", "rung", "响铃"),
    ("rise", "rose", "risen", "上升"),
    ("see", "saw", "seen", "看见"),
    ("shake", "shook", "shaken", "摇动"),
    ("show", "showed", "shown", "展示"),
    ("sing", "sang", "sung", "唱"),
    ("speak", "spoke", "spoken", "说、讲"),
    ("steal", "stole", "stolen", "偷"),
    ("swim", "swam", "swum", "游泳"),
    ("take", "took", "taken", "拿"),
    ("throw", "threw", "thrown", "扔"),
    ("wake", "woke", "woken", "醒来"),
    ("wear", "wore", "worn", "穿"),
    ("write", "wrote", "written", "写"),
]

_KIND_LABELS = {
    "AAA": "AAA 型（三式同形）",
    "ABB": "ABB 型（过去式＝过去分词）",
    "ABA": "ABA 型（原形＝过去分词）",
    "ABC": "ABC 型（三式不同）",
}


def verb_kind(verb: Tuple[str, str, str, str]) -> str:
    """按三种形式推导变化类型。"""
    base, past, past_participle = verb[0], verb[1], verb[2]
    if base == past == past_participle:
        return "AAA"
    if past == past_participle:
        return "ABB"
    if base == past_participle:
        return "ABA"
    return "ABC"


def _normalize(text: str) -> str:
    return text.strip().lower().replace(" ", "")


class VerbPanel(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._filtered: List[Tuple[str, str, str, str]] = list(_VERBS)
        self._quiz_verb: Optional[Tuple[str, str, str, str]] = None
        self._quiz_total = 0
        self._quiz_correct = 0
        self._quiz_checked = False
        self._build_ui()
        self._apply_filter("")
        self._next_quiz()

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        c = self._colors
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("英语不规则动词")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        head.addWidget(title)
        head.addStretch()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索：原形 / 过去式 / 中文，如 go、bought、买")
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

        self.mode_btn = QPushButton("📝 自测模式")
        self.mode_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mode_btn.setStyleSheet(
            f"background: {c.get('accent', '#4f5df5')}; color: white;"
            "border: none; border-radius: 8px; padding: 8px 16px; font-weight: bold;"
        )
        self.mode_btn.clicked.connect(self._toggle_mode)
        head.addWidget(self.mode_btn)
        root.addLayout(head)

        self.stack = QStackedWidget()
        self.stack.addWidget(self._build_browse_page())
        self.stack.addWidget(self._build_quiz_page())
        root.addWidget(self.stack, 1)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 12px;"
        )
        root.addWidget(self.count_label)

    def _build_browse_page(self) -> QWidget:
        c = self._colors
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

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
        detail_layout.setSpacing(10)

        self.base_label = QLabel("选择左侧动词查看")
        self.base_label.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 26px; font-weight: bold;"
        )
        detail_layout.addWidget(self.base_label)

        self.kind_label = QLabel("")
        self.kind_label.setStyleSheet(
            f"color: {c.get('accent', '#4f5df5')}; font-size: 13px; font-weight: bold;"
        )
        detail_layout.addWidget(self.kind_label)

        form_card = QFrame()
        form_card.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')}; border-radius: 10px;"
        )
        form_layout = QVBoxLayout(form_card)
        form_layout.setContentsMargins(18, 16, 18, 16)
        form_layout.setSpacing(6)
        self.past_label = QLabel("")
        self.past_label.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 17px;"
        )
        self.pp_label = QLabel("")
        self.pp_label.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 17px;"
        )
        self.cn_label = QLabel("")
        self.cn_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 15px;"
        )
        form_layout.addWidget(self.past_label)
        form_layout.addWidget(self.pp_label)
        form_layout.addWidget(self.cn_label)
        detail_layout.addWidget(form_card)

        copy_row = QHBoxLayout()
        copy_row.addStretch()
        self.copy_btn = QPushButton("📋 复制三态")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setStyleSheet(
            f"background: {c.get('accent_light', 'rgba(79,93,245,0.12)')};"
            f"color: {c.get('accent', '#4f5df5')};"
            "border: none; border-radius: 8px; padding: 7px 16px; font-weight: bold;"
        )
        self.copy_btn.clicked.connect(self._copy_forms)
        copy_row.addWidget(self.copy_btn)
        detail_layout.addLayout(copy_row)
        detail_layout.addStretch()
        splitter.addWidget(detail)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)
        return page

    def _build_quiz_page(self) -> QWidget:
        c = self._colors
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addStretch()

        card = QFrame()
        card.setMaximumWidth(620)
        card.setStyleSheet(
            f"background: {c.get('card_bg', '#fff')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')}; border-radius: 14px;"
        )
        wrap = QHBoxLayout()
        wrap.addStretch()
        wrap.addWidget(card)
        wrap.addStretch()
        layout.addLayout(wrap)

        inner = QVBoxLayout(card)
        inner.setContentsMargins(24, 22, 24, 22)
        inner.setSpacing(12)

        self.quiz_stats = QLabel("已答 0 · 正确 0")
        self.quiz_stats.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.quiz_stats.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 13px;"
        )
        inner.addWidget(self.quiz_stats)

        self.quiz_question = QLabel("")
        self.quiz_question.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.quiz_question.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 30px; font-weight: bold;"
        )
        inner.addWidget(self.quiz_question)

        row1 = QHBoxLayout()
        row1.addWidget(self._caption("过去式"))
        self.past_edit = QLineEdit()
        self.past_edit.setStyleSheet(self._input_style())
        self.past_edit.returnPressed.connect(self._check_quiz)
        row1.addWidget(self.past_edit, 1)
        inner.addLayout(row1)

        row2 = QHBoxLayout()
        row2.addWidget(self._caption("过去分词"))
        self.pp_edit = QLineEdit()
        self.pp_edit.setStyleSheet(self._input_style())
        self.pp_edit.returnPressed.connect(self._check_quiz)
        row2.addWidget(self.pp_edit, 1)
        inner.addLayout(row2)

        self.quiz_feedback = QLabel("")
        self.quiz_feedback.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.quiz_feedback.setWordWrap(True)
        self.quiz_feedback.setStyleSheet("font-size: 15px; font-weight: bold;")
        inner.addWidget(self.quiz_feedback)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.check_btn = QPushButton("检查")
        self.check_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.check_btn.setStyleSheet(
            f"background: {c.get('accent', '#4f5df5')}; color: white;"
            "border: none; border-radius: 8px; padding: 8px 22px; font-weight: bold;"
        )
        self.check_btn.clicked.connect(self._check_quiz)
        button_row.addWidget(self.check_btn)

        self.next_btn = QPushButton("下一个")
        self.next_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.next_btn.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 8px 18px;"
        )
        self.next_btn.clicked.connect(self._next_quiz)
        button_row.addWidget(self.next_btn)
        button_row.addStretch()
        inner.addLayout(button_row)
        layout.addStretch()
        return page

    def _caption(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setMinimumWidth(72)
        label.setStyleSheet(
            f"color: {self._colors.get('fg_secondary', '#6b7280')}; font-size: 13px;"
        )
        return label

    def _input_style(self) -> str:
        c = self._colors
        return (
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 8px 10px; font-size: 15px;"
        )

    # ------------------------------------------------------------------
    # 浏览
    # ------------------------------------------------------------------
    def _match(self, verb: Tuple[str, str, str, str], keyword: str) -> bool:
        return any(keyword in field.lower() for field in verb)

    def _apply_filter(self, text: str) -> None:
        keyword = text.strip().lower()
        self._filtered = (
            [verb for verb in _VERBS if self._match(verb, keyword)]
            if keyword
            else list(_VERBS)
        )
        self.list_widget.clear()
        for base, _past, _pp, cn in self._filtered:
            self.list_widget.addItem(QListWidgetItem(f"{base}　·　{cn}"))
        self.count_label.setText(f"共 {len(self._filtered)} 个动词（库中共 {len(_VERBS)} 个）")
        if self._filtered:
            self.list_widget.setCurrentRow(0)
            self._show_detail()
        else:
            self.base_label.setText("没有匹配的动词")
            self.kind_label.setText("")
            self.past_label.setText("")
            self.pp_label.setText("")
            self.cn_label.setText("")

    def _on_row_changed(self, _row: int) -> None:
        self._show_detail()

    def _current_verb(self) -> Optional[Tuple[str, str, str, str]]:
        row = self.list_widget.currentRow()
        if 0 <= row < len(self._filtered):
            return self._filtered[row]
        return None

    def _show_detail(self, _item: Optional[QListWidgetItem] = None) -> None:
        verb = self._current_verb()
        if verb is None:
            return
        base, past, pp, cn = verb
        self.base_label.setText(base)
        self.kind_label.setText(_KIND_LABELS.get(verb_kind(verb), ""))
        self.past_label.setText(f"过去式　　{past}")
        self.pp_label.setText(f"过去分词　{pp}")
        self.cn_label.setText(f"中文　　　{cn}")

    def _copy_forms(self) -> None:
        verb = self._current_verb()
        if verb is None:
            return
        from PyQt6.QtWidgets import QApplication
        from PyQt6.QtCore import QTimer

        QApplication.clipboard().setText(f"{verb[0]} - {verb[1]} - {verb[2]}　{verb[3]}")
        self.copy_btn.setText("✓ 已复制")
        QTimer.singleShot(1200, lambda: self.copy_btn.setText("📋 复制三态"))

    # ------------------------------------------------------------------
    # 自测
    # ------------------------------------------------------------------
    def _toggle_mode(self) -> None:
        if self.stack.currentIndex() == 0:
            self.stack.setCurrentIndex(1)
            self.mode_btn.setText("📖 返回速查")
            self.search_edit.setEnabled(False)
            self._next_quiz()
        else:
            self.stack.setCurrentIndex(0)
            self.mode_btn.setText("📝 自测模式")
            self.search_edit.setEnabled(True)

    def _next_quiz(self) -> None:
        self._quiz_verb = random.choice(_VERBS)
        self._quiz_checked = False
        base, _past, _pp, cn = self._quiz_verb
        self.quiz_question.setText(f"{base}　（{cn}）")
        self.past_edit.clear()
        self.pp_edit.clear()
        self.past_edit.setFocus()
        self.quiz_feedback.setText("")
        self._refresh_quiz_stats()

    def _check_quiz(self) -> None:
        if self._quiz_verb is None or self._quiz_checked:
            return
        self._quiz_checked = True
        base, past, pp, _cn = self._quiz_verb
        past_input = _normalize(self.past_edit.text())
        pp_input = _normalize(self.pp_edit.text())
        expected_past = {_normalize(piece) for piece in past.split("/")}
        expected_pp = {_normalize(piece) for piece in pp.split("/")}
        ok = past_input in expected_past and pp_input in expected_pp

        self._quiz_total += 1
        if ok:
            self._quiz_correct += 1
            self.quiz_feedback.setText("✓ 正确")
            color = self._colors.get("success", "#10b981")
        else:
            self.quiz_feedback.setText(f"✗ 正确答案：{base} - {past} - {pp}")
            color = self._colors.get("error", "#ef4444")
        self.quiz_feedback.setStyleSheet(
            f"color: {color}; font-size: 15px; font-weight: bold;"
        )
        self._refresh_quiz_stats()

    def _refresh_quiz_stats(self) -> None:
        rate = self._quiz_correct / self._quiz_total * 100 if self._quiz_total else 0
        self.quiz_stats.setText(
            f"已答 {self._quiz_total} · 正确 {self._quiz_correct} · 正确率 {rate:.0f}%"
        )


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "不规则动词",
        lambda c: VerbPanel(c),
        icon="📝",
        subtitle="75 个常用不规则动词速查与默写自测",
    )
