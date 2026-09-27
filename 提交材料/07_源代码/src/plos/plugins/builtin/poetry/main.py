"""内置插件：古诗文积累。

中学必背古诗文的篇目、作者、朝代与名句速查：

- 支持按题目 / 作者 / 朝代 / 名句内容全文搜索；
- 右侧展示名句与篇目要点，可一键复制名句；
- 「随机抽查」随机抽一篇并遮住名句，先自己背，再点「显示名句」核对。
"""

from __future__ import annotations

import random
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt
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

# (题目, 作者, 朝代, 名句, 要点)
_WORKS: List[Tuple[str, str, str, str, str]] = [
    ("《论语》十二章", "孔子及其弟子", "先秦", "学而不思则罔，思而不学则殆。", "儒家经典，谈学习态度、方法与修身。"),
    ("曹刿论战", "左丘明", "先秦", "一鼓作气，再而衰，三而竭。", "记长勺之战，说明取信于民与把握战机。"),
    ("生于忧患，死于安乐", "孟子", "先秦", "天将降大任于是人也，必先苦其心志，劳其筋骨。", "论述逆境磨炼人才、安乐导致衰亡。"),
    ("鱼我所欲也", "孟子", "先秦", "生，亦我所欲也；义，亦我所欲也。", "论述舍生取义的道德选择。"),
    ("逍遥游（节选）", "庄子", "先秦", "水击三千里，抟扶摇而上者九万里。", "想象奇伟，追求绝对自由的精神境界。"),
    ("劝学（节选）", "荀子", "先秦", "不积跬步，无以至千里；不积小流，无以成江海。", "强调学习贵在积累与坚持。"),
    ("过秦论（节选）", "贾谊", "西汉", "仁义不施而攻守之势异也。", "总结秦朝速亡的原因。"),
    ("出师表", "诸葛亮", "三国", "今天下三分，益州疲弊，此诚危急存亡之秋也。", "表文典范，抒发忠贞之志与报国之心。"),
    ("桃花源记", "陶渊明", "东晋", "阡陌交通，鸡犬相闻。", "寄托对没有战乱、安宁自足社会的向往。"),
    ("归去来兮辞", "陶渊明", "东晋", "悟已往之不谏，知来者之可追。", "辞赋名篇，写弃官归隐的欣然。"),
    ("兰亭集序", "王羲之", "东晋", "仰观宇宙之大，俯察品类之盛。", "序文名作，由游乐引发对人生的慨叹。"),
    ("三峡", "郦道元", "北魏", "两岸连山，略无阙处。", "《水经注》名段，写三峡山川的雄奇。"),
    ("滕王阁序（节选）", "王勃", "唐", "落霞与孤鹜齐飞，秋水共长天一色。", "骈文名篇，写景壮丽、意境开阔。"),
    ("师说", "韩愈", "唐", "师者，所以传道受业解惑也。", "论述从师学习的必要性与择师标准。"),
    ("马说", "韩愈", "唐", "千里马常有，而伯乐不常有。", "托物寓意，慨叹人才不被赏识。"),
    ("陋室铭", "刘禹锡", "唐", "斯是陋室，惟吾德馨。", "铭文短章，表现安贫乐道的高洁情操。"),
    ("小石潭记", "柳宗元", "唐", "潭中鱼可百许头，皆若空游无所依。", "山水游记，写景清幽、寄寓贬谪心境。"),
    ("钱塘湖春行", "白居易", "唐", "乱花渐欲迷人眼，浅草才能没马蹄。", "写西湖早春，细腻明快。"),
    ("酬乐天扬州初逢席上见赠", "刘禹锡", "唐", "沉舟侧畔千帆过，病树前头万木春。", "于贬谪中见豁达，蕴含哲理。"),
    ("泊秦淮", "杜牧", "唐", "商女不知亡国恨，隔江犹唱后庭花。", "怀古讽今，忧时伤世。"),
    ("无题", "李商隐", "唐", "春蚕到死丝方尽，蜡炬成灰泪始干。", "以物寄情，写执着深挚的情感。"),
    ("望岳", "杜甫", "唐", "会当凌绝顶，一览众山小。", "写泰山雄伟，抒发远大抱负。"),
    ("春望", "杜甫", "唐", "烽火连三月，家书抵万金。", "忧国伤时、思念家人的名篇。"),
    ("茅屋为秋风所破歌", "杜甫", "唐", "安得广厦千万间，大庇天下寒士俱欢颜。", "由自身困顿推及天下寒士，胸襟博大。"),
    ("使至塞上", "王维", "唐", "大漠孤烟直，长河落日圆。", "边塞诗中写景绝唱，画面雄浑。"),
    ("行路难（其一）", "李白", "唐", "长风破浪会有时，直挂云帆济沧海。", "于困顿中仍见豪迈自信。"),
    ("观沧海", "曹操", "东汉", "日月之行，若出其中；星汉灿烂，若出其里。", "登临之作，气象壮阔、胸怀天下。"),
    ("次北固山下", "王湾", "唐", "海日生残夜，江春入旧年。", "写旅途所见，暗含思乡与新旧的哲思。"),
    ("爱莲说", "周敦颐", "北宋", "出淤泥而不染，濯清涟而不妖。", "托物言志，赞高洁品格。"),
    ("岳阳楼记", "范仲淹", "北宋", "先天下之忧而忧，后天下之乐而乐。", "记文名篇，抒发以天下为己任的忧乐观。"),
    ("醉翁亭记", "欧阳修", "北宋", "醉翁之意不在酒，在乎山水之间也。", "写滁州山水之乐与与民同乐的情怀。"),
    ("记承天寺夜游", "苏轼", "北宋", "庭下如积水空明，水中藻、荇交横，盖竹柏影也。", "短文写月夜清景，见闲适旷达。"),
    ("赤壁赋（节选）", "苏轼", "北宋", "寄蜉蝣于天地，渺沧海之一粟。", "由江月引发对人生短暂与永恒的哲思。"),
    ("渔家傲·秋思", "范仲淹", "北宋", "浊酒一杯家万里，燕然未勒归无计。", "边塞词，写思乡与报国两难。"),
    ("水调歌头·明月几时有", "苏轼", "北宋", "但愿人长久，千里共婵娟。", "中秋怀人之作，旷达而深情。"),
    ("江城子·密州出猎", "苏轼", "北宋", "会挽雕弓如满月，西北望，射天狼。", "豪放词代表作，抒报国壮志。"),
    ("破阵子·为陈同甫赋壮词以寄之", "辛弃疾", "南宋", "了却君王天下事，赢得生前身后名。", "壮词寄友，写沙场豪情与壮志难酬。"),
    ("南乡子·登京口北固亭有怀", "辛弃疾", "南宋", "生子当如孙仲谋。", "怀古伤今，赞英雄、叹时局。"),
    ("过零丁洋", "文天祥", "南宋", "人生自古谁无死？留取丹心照汗青。", "绝笔明志，气节凛然。"),
    ("天净沙·秋思", "马致远", "元", "夕阳西下，断肠人在天涯。", "小令名篇，以景写羁旅愁思。"),
    ("山坡羊·潼关怀古", "张养浩", "元", "兴，百姓苦；亡，百姓苦。", "怀古曲，直指民生疾苦。"),
    ("己亥杂诗（其五）", "龚自珍", "清", "落红不是无情物，化作春泥更护花。", "以落花自喻，写不甘沉沦的奉献情怀。"),
]


class PoetryPanel(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._filtered: List[Tuple[str, str, str, str, str]] = list(_WORKS)
        self._revealed = True
        self._quote = ""
        self._build_ui()
        self._apply_filter("")

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        c = self._colors
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("古诗文积累")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        head.addWidget(title)
        head.addStretch()

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索：题目 / 作者 / 朝代 / 名句，如 苏轼、先天下之忧")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(300)
        self.search_edit.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 8px 10px;"
        )
        self.search_edit.textChanged.connect(self._apply_filter)
        head.addWidget(self.search_edit)

        self.quiz_btn = QPushButton("🎲 随机抽查")
        self.quiz_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.quiz_btn.setStyleSheet(
            f"background: {c.get('accent_light', 'rgba(79,93,245,0.1)')};"
            f"color: {c.get('accent', '#4f5df5')};"
            "border: none; border-radius: 8px; padding: 8px 14px; font-weight: bold;"
        )
        self.quiz_btn.setToolTip("随机抽一篇并遮住名句，先自己背，再点「显示名句」核对")
        self.quiz_btn.clicked.connect(self._random_quiz)
        head.addWidget(self.quiz_btn)
        root.addLayout(head)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.list_widget = QListWidget()
        self.list_widget.setMinimumWidth(250)
        self.list_widget.itemClicked.connect(self._show_detail)
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

        self.title_label = QLabel("选择左侧篇目查看")
        self.title_label.setWordWrap(True)
        self.title_label.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 22px; font-weight: bold;"
        )
        detail_layout.addWidget(self.title_label)

        self.author_label = QLabel("")
        self.author_label.setStyleSheet(
            f"color: {c.get('accent', '#4f5df5')}; font-size: 13px;"
        )
        detail_layout.addWidget(self.author_label)

        quote_card = QFrame()
        quote_card.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')}; border-radius: 10px;"
        )
        quote_layout = QVBoxLayout(quote_card)
        quote_layout.setContentsMargins(18, 16, 18, 16)
        self.quote_label = QLabel("")
        self.quote_label.setWordWrap(True)
        self.quote_label.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 19px; font-weight: bold;"
        )
        quote_layout.addWidget(self.quote_label)
        detail_layout.addWidget(quote_card)

        self.note_label = QLabel("")
        self.note_label.setWordWrap(True)
        self.note_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 14px;"
        )
        detail_layout.addWidget(self.note_label)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.reveal_btn = QPushButton("🙈 隐藏名句")
        self.reveal_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reveal_btn.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 8px 16px;"
        )
        self.reveal_btn.clicked.connect(self._toggle_reveal)
        button_row.addWidget(self.reveal_btn)

        self.copy_btn = QPushButton("📋 复制名句")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setStyleSheet(
            f"background: {c.get('accent', '#4f5df5')}; color: white;"
            "border: none; border-radius: 8px; padding: 8px 18px; font-weight: bold;"
        )
        self.copy_btn.clicked.connect(self._copy_quote)
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
                for item in _WORKS
                if keyword in item[0].lower()
                or keyword in item[1].lower()
                or keyword in item[2].lower()
                or keyword in item[3].lower()
                or keyword in item[4].lower()
            ]
        else:
            self._filtered = list(_WORKS)

        self.list_widget.clear()
        for title, author, dynasty, _quote, _note in self._filtered:
            item = QListWidgetItem(f"{title}　·　{dynasty} {author}")
            self.list_widget.addItem(item)
        self.count_label.setText(f"共 {len(self._filtered)} 篇必背古诗文")
        if self._filtered:
            self.list_widget.setCurrentRow(0)
            self._show_detail(self.list_widget.currentItem())
        else:
            self.title_label.setText("没有匹配的篇目")
            self.author_label.setText("")
            self.quote_label.setText("")
            self.note_label.setText("换个关键词试试。")

    def _current_work(self) -> Optional[Tuple[str, str, str, str, str]]:
        row = self.list_widget.currentRow()
        if 0 <= row < len(self._filtered):
            return self._filtered[row]
        return None

    def _show_detail(self, _item: QListWidgetItem) -> None:
        work = self._current_work()
        if work is None:
            return
        title, author, dynasty, quote, note = work
        self.title_label.setText(title)
        self.author_label.setText(f"{dynasty}　{author}")
        self._quote = quote
        self._render_quote()
        self.note_label.setText(note)

    def _render_quote(self) -> None:
        if self._revealed:
            self.quote_label.setText(self._quote)
            self.quote_label.setStyleSheet(
                f"color: {self._colors.get('fg_primary', '#111827')};"
                "font-size: 19px; font-weight: bold;"
            )
            self.reveal_btn.setText("🙈 隐藏名句")
        else:
            self.quote_label.setText("（先默背一遍，再点右侧「显示名句」核对）")
            self.quote_label.setStyleSheet(
                f"color: {self._colors.get('fg_muted', '#9ca3af')}; font-size: 15px;"
            )
            self.reveal_btn.setText("👀 显示名句")

    def _toggle_reveal(self) -> None:
        self._revealed = not self._revealed
        self._render_quote()

    def _random_quiz(self) -> None:
        if not _WORKS:
            return
        work = random.choice(_WORKS)
        self.search_edit.clear()
        self._filtered = list(_WORKS)
        self.list_widget.clear()
        for title, author, dynasty, _quote, _note in self._filtered:
            self.list_widget.addItem(QListWidgetItem(f"{title}　·　{dynasty} {author}"))
        index = _WORKS.index(work)
        self.list_widget.setCurrentRow(index)
        self._revealed = False
        self._show_detail(self.list_widget.currentItem())
        self.quiz_btn.setText("🎲 换一篇")

    def _copy_quote(self) -> None:
        work = self._current_work()
        if work is None:
            return
        QApplication.clipboard().setText(work[3])
        self.copy_btn.setText("✓ 已复制")
        from PyQt6.QtCore import QTimer

        QTimer.singleShot(1200, lambda: self.copy_btn.setText("📋 复制名句"))


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "古诗文积累",
        lambda c: PoetryPanel(c),
        icon="📜",
        subtitle="必背篇目与名句速查，支持背诵抽查",
    )
