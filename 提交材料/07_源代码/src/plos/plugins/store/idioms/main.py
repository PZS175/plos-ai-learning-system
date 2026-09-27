"""插件库插件：成语词典。

60 条中学常用成语，含释义、近义词、反义词与易错字提示：

- 支持按成语、释义、近反义词全文搜索；
- 右侧展示完整释义，可一键复制；
- 「随机抽查」随机抽一条并隐藏释义，先自己想，再点「显示释义」核对。
"""

from __future__ import annotations

import random
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

# (成语, 释义, 近义, 反义, 易错提示)
_IDIOMS: List[Tuple[str, str, str, str, str]] = [
    ("爱不释手", "喜爱得舍不得放下。", "手不释卷", "弃如敝屣", "「释」不要写成「择」。"),
    ("安然无恙", "平安无事，没有受到任何损害。", "平安无事", "危在旦夕", ""),
    ("拔苗助长", "比喻违反事物发展规律，急于求成，反而把事情弄糟。", "欲速不达", "循序渐进", ""),
    ("百折不挠", "无论受到多少挫折都不退缩、不屈服。", "不屈不挠", "半途而废", "「挠」读 náo，不要写成「饶」。"),
    ("班门弄斧", "在行家面前卖弄本领，不自量力。", "贻笑大方", "虚心求教", ""),
    ("半途而废", "事情做到一半就停下来，不能坚持到底。", "浅尝辄止", "坚持不懈", ""),
    ("变本加厉", "变得比原来更加严重。", "雪上加霜", "微不足道", "贬义词，不能形容好的变化。"),
    ("别出心裁", "独创一格，与众不同。", "独具匠心", "千篇一律", ""),
    ("不耻下问", "不以向学问比自己差的人请教为耻。", "虚怀若谷", "自以为是", "「耻」是意动用法，指「以……为耻」。"),
    ("不胫而走", "没有腿却能跑，形容消息传播得很快。", "不翼而飞", "秘而不宣", "「胫」指小腿，不要写成「径」。"),
    ("不谋而合", "事先没有商量，见解却完全一致。", "不约而同", "各行其是", ""),
    ("沧海桑田", "大海变成农田，农田变成大海，比喻世事变化很大。", "世事变迁", "一成不变", ""),
    ("姹紫嫣红", "形容各种颜色的花朵鲜艳美丽。", "万紫千红", "残花败柳", ""),
    ("畅所欲言", "尽情地说出想说的话。", "直言不讳", "吞吞吐吐", ""),
    ("车水马龙", "车像流水，马像游龙，形容车马往来不绝、热闹非凡。", "川流不息", "门可罗雀", ""),
    ("出类拔萃", "才能或品行超出同类之上。", "鹤立鸡群", "碌碌无为", "「萃」指聚集，不要写成「粹」。"),
    ("川流不息", "行人车马像水流一样连续不断。", "络绎不绝", "断断续续", "「川」不要写成「穿」。"),
    ("大器晚成", "能担当大事的人往往成就较晚。", "厚积薄发", "小时了了", ""),
    ("道听途说", "路上听来的传闻，指没有根据的消息。", "捕风捉影", "言之凿凿", ""),
    ("得心应手", "心里怎么想，手就能怎么做，形容技艺纯熟。", "游刃有余", "笨手笨脚", ""),
    ("东山再起", "比喻失势之后重新恢复地位。", "卷土重来", "一蹶不振", ""),
    ("断章取义", "不顾全篇或全文，孤立地截取其中一段。", "望文生义", "融会贯通", ""),
    ("对牛弹琴", "比喻对不懂道理的人讲道理，白费口舌。", "白费口舌", "对症下药", ""),
    ("耳濡目染", "经常听到看到，无形之中受到影响。", "潜移默化", "充耳不闻", "「濡」不要写成「儒」。"),
    ("防微杜渐", "在错误或坏事刚露头时就加以制止，不让它发展。", "防患未然", "养痈遗患", ""),
    ("釜底抽薪", "从锅底下抽走柴火，比喻从根本上解决问题。", "正本清源", "扬汤止沸", "「釜」指锅，不要写成「斧」。"),
    ("高瞻远瞩", "站得高看得远，形容眼光远大。", "深谋远虑", "鼠目寸光", "「瞻」不要写成「赡」。"),
    ("刮目相看", "用新的眼光看待别人，指已有进步。", "另眼相看", "不屑一顾", ""),
    ("汗流浃背", "汗流得满背都是，形容非常劳累或惶恐。", "挥汗如雨", "泰然自若", "「浃」不要写成「夹」。"),
    ("华而不实", "只开花不结果，比喻外表好看而内容空虚。", "虚有其表", "表里如一", ""),
    ("焕然一新", "形容出现了崭新的面貌。", "面目一新", "依然如故", "「焕」不要写成「换」。"),
    ("集思广益", "集中众人的智慧，广泛吸收有益的意见。", "群策群力", "独断专行", ""),
    ("家喻户晓", "每家每户都知道。", "妇孺皆知", "鲜为人知", ""),
    ("见异思迁", "看见别的事物就改变主意，指意志不坚定。", "三心二意", "专心致志", ""),
    ("矫揉造作", "故意做作，极不自然。", "装腔作势", "落落大方", "「矫」不要写成「骄」。"),
    ("兢兢业业", "形容做事小心谨慎、认真踏实。", "勤勤恳恳", "敷衍了事", "「兢」不要写成「竞」。"),
    ("开卷有益", "读书总有好处。", "受益匪浅", "不学无术", ""),
    ("刻舟求剑", "比喻拘泥成法，不知道变通。", "墨守成规", "随机应变", ""),
    ("脍炙人口", "好的诗文或事物受到人们普遍称赞。", "喜闻乐见", "索然无味", "「脍」读 kuài。"),
    ("滥竽充数", "没有真才实学的人混在行家里面充数。", "鱼目混珠", "货真价实", ""),
    ("力挽狂澜", "比喻尽力挽回险恶的局面。", "中流砥柱", "束手无策", ""),
    ("络绎不绝", "形容行人车马来来往往，接连不断。", "川流不息", "杳无人迹", ""),
    ("名列前茅", "名次排在前面。", "首屈一指", "名落孙山", ""),
    ("目不暇接", "东西太多，眼睛看不过来。", "眼花缭乱", "一目了然", "「暇」不要写成「瑕」。"),
    ("锲而不舍", "不停地雕刻，比喻有恒心、有毅力。", "持之以恒", "半途而废", "「锲」读 qiè。"),
    ("忍俊不禁", "忍不住笑出来。", "哑然失笑", "无动于衷", "后面不能再加「地笑了」，语义重复。"),
    ("融会贯通", "把各方面的知识道理融合起来，得到全面透彻的理解。", "举一反三", "囫囵吞枣", ""),
    ("舍本逐末", "舍弃根本的、主要的，而去追求枝节的、次要的。", "本末倒置", "追本溯源", ""),
    ("身临其境", "亲身到了那个地方。", "设身处地", "置身事外", ""),
    ("世外桃源", "比喻不受外界影响的地方或幻想中的美好世界。", "人间仙境", "人间地狱", ""),
    ("势如破竹", "形容作战或工作节节胜利，毫无阻碍。", "所向披靡", "节节败退", ""),
    ("水到渠成", "水流到的地方自然成渠，比喻条件成熟，事情自然成功。", "顺理成章", "拔苗助长", ""),
    ("随波逐流", "比喻没有主见，随着别人走。", "人云亦云", "特立独行", ""),
    ("谈笑风生", "形容谈话谈得高兴而有风趣。", "谈笑自若", "沉默寡言", "「生」不要写成「声」。"),
    ("天衣无缝", "比喻事物周密完善，找不出破绽。", "无懈可击", "漏洞百出", ""),
    ("完璧归赵", "比喻把原物完好无损地归还本人。", "物归原主", "久借不还", ""),
    ("亡羊补牢", "羊丢了再修补羊圈，比喻出了问题后及时补救。", "知错就改", "未雨绸缪", ""),
    ("望梅止渴", "比喻用空想或假象来安慰自己。", "画饼充饥", "脚踏实地", ""),
    ("温故知新", "温习旧的知识，能得到新的理解和体会。", "融会贯通", "一知半解", ""),
    ("卧薪尝胆", "形容人刻苦自励、发愤图强。", "发愤图强", "苟且偷安", ""),
    ("无微不至", "形容关怀照顾得非常细致周到。", "关怀备至", "漠不关心", ""),
    ("栩栩如生", "形容形象生动逼真，像活的一样。", "惟妙惟肖", "死气沉沉", "「栩」读 xǔ。"),
    ("悬梁刺股", "形容学习非常刻苦。", "废寝忘食", "游手好闲", ""),
    ("雪中送炭", "在别人急需时给予帮助。", "解囊相助", "落井下石", ""),
    ("循序渐进", "按照一定的顺序、步骤逐渐深入或提高。", "按部就班", "急于求成", ""),
    ("一鼓作气", "比喻趁劲头大的时候一下子把事情完成。", "一气呵成", "一蹶不振", ""),
    ("一丝不苟", "办事认真，连最细微的地方也不马虎。", "精益求精", "粗枝大叶", "「苟」不要写成「句」。"),
    ("义愤填膺", "胸中充满义愤。", "满腔义愤", "无动于衷", "「膺」指胸，不要写成「鹰」。"),
    ("因地制宜", "根据各地的具体情况，制定适宜的办法。", "因势利导", "生搬硬套", ""),
    ("引经据典", "引用经典著作作为说话、写作的依据。", "旁征博引", "信口开河", ""),
    ("有备无患", "事先有准备，就可以避免祸患。", "未雨绸缪", "临渴掘井", ""),
    ("语重心长", "言辞诚恳，情意深长。", "苦口婆心", "冷言冷语", ""),
    ("载歌载舞", "又唱歌又跳舞，形容欢乐的情景。", "欢天喜地", "愁眉苦脸", "「载」读 zài。"),
]


class IdiomPanel(QWidget):
    def __init__(self, ctx) -> None:
        super().__init__()
        self._ctx = ctx
        self._colors = ctx.theme_colors()
        self._filtered: List[Tuple[str, str, str, str, str]] = list(_IDIOMS)
        self._revealed = True
        self._current: Optional[Tuple[str, str, str, str, str]] = None
        self._build_ui()
        self._apply_filter("")

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        c = self._colors
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(12)

        head = QHBoxLayout()
        title = QLabel("成语词典")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        head.addWidget(title)
        head.addStretch()

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("搜索：成语 / 释义 / 近反义，如 一鼓作气、坚持")
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

        self.quiz_btn = QPushButton("🎲 随机抽查")
        self.quiz_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.quiz_btn.setToolTip("随机抽一条并隐藏释义，先自己想，再点「显示释义」核对")
        self.quiz_btn.setStyleSheet(
            f"background: {c.get('accent', '#4f5df5')}; color: white;"
            "border: none; border-radius: 8px; padding: 8px 16px; font-weight: bold;"
        )
        self.quiz_btn.clicked.connect(self._random_quiz)
        head.addWidget(self.quiz_btn)
        root.addLayout(head)

        splitter = QSplitter(Qt.Orientation.Horizontal)

        self.list_widget = QListWidget()
        self.list_widget.setMinimumWidth(200)
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

        self.idiom_label = QLabel("选择左侧成语查看")
        self.idiom_label.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 26px; font-weight: bold;"
        )
        detail_layout.addWidget(self.idiom_label)

        meaning_card = QFrame()
        meaning_card.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')}; border-radius: 10px;"
        )
        meaning_layout = QVBoxLayout(meaning_card)
        meaning_layout.setContentsMargins(18, 16, 18, 16)
        self.meaning_label = QLabel("")
        self.meaning_label.setWordWrap(True)
        self.meaning_label.setStyleSheet(
            f"color: {c.get('fg_primary', '#111827')}; font-size: 17px;"
        )
        meaning_layout.addWidget(self.meaning_label)
        detail_layout.addWidget(meaning_card)

        self.relation_label = QLabel("")
        self.relation_label.setWordWrap(True)
        self.relation_label.setStyleSheet(
            f"color: {c.get('fg_secondary', '#6b7280')}; font-size: 14px;"
        )
        detail_layout.addWidget(self.relation_label)

        self.tip_label = QLabel("")
        self.tip_label.setWordWrap(True)
        self.tip_label.setStyleSheet(
            f"color: {c.get('warning', '#f59e0b')}; font-size: 14px;"
        )
        detail_layout.addWidget(self.tip_label)

        button_row = QHBoxLayout()
        button_row.addStretch()
        self.reveal_btn = QPushButton("🙈 隐藏释义")
        self.reveal_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.reveal_btn.setStyleSheet(
            f"background: {c.get('bg_tertiary', '#f3f4f6')};"
            f"color: {c.get('fg_primary', '#111827')};"
            f"border: 1px solid {c.get('border', '#e5e7eb')};"
            "border-radius: 8px; padding: 8px 16px;"
        )
        self.reveal_btn.clicked.connect(self._toggle_reveal)
        button_row.addWidget(self.reveal_btn)

        self.copy_btn = QPushButton("📋 复制释义")
        self.copy_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.copy_btn.setStyleSheet(
            f"background: {c.get('accent_light', 'rgba(79,93,245,0.12)')};"
            f"color: {c.get('accent', '#4f5df5')};"
            "border: none; border-radius: 8px; padding: 8px 16px; font-weight: bold;"
        )
        self.copy_btn.clicked.connect(self._copy_meaning)
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
                for item in _IDIOMS
                if any(keyword in field.lower() for field in item)
            ]
        else:
            self._filtered = list(_IDIOMS)

        self.list_widget.clear()
        for idiom, *_rest in self._filtered:
            self.list_widget.addItem(QListWidgetItem(idiom))
        self.count_label.setText(f"共 {len(self._filtered)} 条成语")
        if self._filtered:
            self.list_widget.setCurrentRow(0)
            self._show_detail()
        else:
            self.idiom_label.setText("没有匹配的成语")
            self.meaning_label.setText("")
            self.relation_label.setText("")
            self.tip_label.setText("")

    def _on_row_changed(self, _row: int) -> None:
        self._show_detail()

    def _show_detail(self, _item: Optional[QListWidgetItem] = None) -> None:
        row = self.list_widget.currentRow()
        if not (0 <= row < len(self._filtered)):
            return
        self._current = self._filtered[row]
        idiom, meaning, synonym, antonym, tip = self._current
        self.idiom_label.setText(idiom)
        self.relation_label.setText(f"近义词：{synonym or '—'}　　反义词：{antonym or '—'}")
        self.tip_label.setText(f"⚠ {tip}" if tip else "")
        self._render_meaning()

    def _render_meaning(self) -> None:
        if self._current is None:
            return
        c = self._colors
        if self._revealed:
            self.meaning_label.setText(self._current[1])
            self.meaning_label.setStyleSheet(
                f"color: {c.get('fg_primary', '#111827')}; font-size: 17px;"
            )
            self.reveal_btn.setText("🙈 隐藏释义")
        else:
            self.meaning_label.setText("（先自己说说它的意思，再点右侧「显示释义」核对）")
            self.meaning_label.setStyleSheet(
                f"color: {c.get('fg_muted', '#9ca3af')}; font-size: 15px;"
            )
            self.reveal_btn.setText("👀 显示释义")

    def _toggle_reveal(self) -> None:
        if self._current is None:
            return
        self._revealed = not self._revealed
        self._render_meaning()

    def _random_quiz(self) -> None:
        if not _IDIOMS:
            return
        self.search_edit.clear()
        self._filtered = list(_IDIOMS)
        self.list_widget.clear()
        for idiom, *_rest in self._filtered:
            self.list_widget.addItem(QListWidgetItem(idiom))
        index = _IDIOMS.index(random.choice(_IDIOMS))
        self.list_widget.setCurrentRow(index)
        self._revealed = False
        self._show_detail()
        self.quiz_btn.setText("🎲 换一条")

    def _copy_meaning(self) -> None:
        if self._current is None:
            return
        idiom, meaning, synonym, antonym, _tip = self._current
        QApplication.clipboard().setText(
            f"{idiom}：{meaning}\n近义：{synonym or '—'}　反义：{antonym or '—'}"
        )
        self.copy_btn.setText("✓ 已复制")
        QTimer.singleShot(1200, lambda: self.copy_btn.setText("📋 复制释义"))


def register(ctx) -> None:
    ctx.register_panel(
        "main",
        "成语词典",
        lambda c: IdiomPanel(c),
        icon="📚",
        subtitle="常用成语释义、近反义词与易错提示",
    )
