"""知识图谱可视化面板。

使用 QGraphicsView 自绘知识点关联网络图，标记薄弱节点。
兼容深浅色主题，玻璃拟态 UI。
"""

from __future__ import annotations

import math
import random
from typing import Any, Dict, List, Optional

from PyQt6.QtCore import Qt, QPointF
from PyQt6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QWheelEvent
from PyQt6.QtWidgets import (
    QGraphicsEllipseItem,
    QGraphicsItem,
    QGraphicsLineItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..services.knowledge_graph_service import KnowledgeGraphService
from ..utils.logger import get_logger
from .math_text import attach_math_delegate
from .ui_utils import create_section_title, apply_glass_style, theme_colors

logger = get_logger("ui.knowledge_graph_panel")


_NODE_CATEGORY_TOKENS = {
    "weak": "error",
    "normal": "warning",
    "strong": "success",
}


def node_color(category: str) -> QColor:
    """按知识点掌握类别返回主题语义色。"""
    c = theme_colors()
    return QColor(c.get(_NODE_CATEGORY_TOKENS.get(category, "warning"), c["warning"]))


class _NodeItem(QGraphicsEllipseItem):
    """可拖拽的节点图元。"""

    def __init__(
        self,
        x: float,
        y: float,
        radius: float,
        node_data: dict,
        color: QColor,
        text_color: QColor,
        parent: Optional[QGraphicsItem] = None,
    ):
        super().__init__(-radius, -radius, radius * 2, radius * 2, parent)
        self.node_data = node_data
        self.radius = radius
        self.setBrush(QBrush(color))
        self.setPen(QPen(Qt.PenStyle.NoPen))
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setZValue(10)
        self.setPos(x, y)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(self._build_tooltip())

        text = QGraphicsTextItem(node_data["label"], self)
        text.setDefaultTextColor(text_color)
        font = QFont("Microsoft YaHei", 9)
        font.setBold(True)
        text.setFont(font)
        text.setPos(-text.boundingRect().width() / 2, radius + 4)

    def _build_tooltip(self) -> str:
        d = self.node_data
        return (
            f"{d['label']}\n"
            f"错题数：{d['error_count']}\n"
            f"练习次数：{d['practice_count']}\n"
            f"正确率：{int(d['accuracy'] * 100)}%\n"
            f"掌握率：{d.get('mastery_rate', 0)}%\n"
            f"状态：{'薄弱' if d['category'] == 'weak' else ('良好' if d['category'] == 'strong' else '一般')}"
        )


class _GraphView(QGraphicsView):
    """支持缩放的知识网络图视图。"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMinimumSize(400, 300)

    def wheelEvent(self, event: QWheelEvent) -> None:
        factor = 1.15 if event.angleDelta().y() > 0 else 0.87
        self.scale(factor, factor)


class KnowledgeGraphPanel(QWidget):
    """知识图谱可视化面板。"""

    def __init__(
        self,
        graph_service: KnowledgeGraphService,
        parent: Optional[QWidget] = None,
    ):
        super().__init__(parent)
        self.graph_service = graph_service
        self._scene = QGraphicsScene(self)
        self._node_items: Dict[str, _NodeItem] = {}
        self._edge_items: List[QGraphicsLineItem] = []
        self._graph_data: Dict[str, Any] = {"nodes": [], "edges": []}
        self._build_ui()
        self.refresh_data()

    def _build_ui(self) -> None:
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(12)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        main_layout.addWidget(splitter)

        # 左侧信息面板
        left_panel = QWidget()
        left_panel.setProperty("glass", True)
        apply_glass_style(left_panel)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(14, 14, 14, 14)
        left_layout.setSpacing(12)

        title = create_section_title("知识图谱")
        left_layout.addWidget(title)

        hint = QLabel(
            "基于错题本与练习记录自动构建知识点关联网络。\n"
            "红色节点为知识漏洞/薄弱点，绿色为掌握良好，黄色为一般。"
        )
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #8A93A8; font-size: 12px;")
        left_layout.addWidget(hint)

        refresh_btn = QPushButton("刷新图谱")
        refresh_btn.clicked.connect(self.refresh_data)
        left_layout.addWidget(refresh_btn)

        weak_title = QLabel("薄弱知识点")
        weak_title.setStyleSheet("font-size: 13px; font-weight: bold;")
        left_layout.addWidget(weak_title)

        self.weak_list = QListWidget()
        # 薄弱知识点名称可能含公式符号，单元格走混排渲染
        self._weak_math_delegate = attach_math_delegate(self.weak_list, 0)
        left_layout.addWidget(self.weak_list)

        legend_title = QLabel("图例")
        legend_title.setStyleSheet("font-size: 13px; font-weight: bold;")
        left_layout.addWidget(legend_title)

        for label, color in [
            ("薄弱 / 知识漏洞", node_color("weak")),
            ("一般掌握", node_color("normal")),
            ("掌握良好", node_color("strong")),
        ]:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color.name()}; font-size: 14px;")
            row_layout.addWidget(dot)
            row_layout.addWidget(QLabel(label))
            row_layout.addStretch()
            left_layout.addWidget(row)

        left_layout.addStretch()
        splitter.addWidget(left_panel)

        # 右侧网络图
        right_panel = QWidget()
        right_panel.setProperty("glass", True)
        apply_glass_style(right_panel)
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(8, 8, 8, 8)

        self.graph_view = _GraphView()
        self.graph_view.setScene(self._scene)
        right_layout.addWidget(self.graph_view)
        splitter.addWidget(right_panel)
        splitter.setSizes([260, 900])

    def _is_dark_theme(self) -> bool:
        """通过调色板背景亮度判断当前是否为深色主题。"""
        bg = self.palette().window().color()
        return bg.lightness() < 128

    def refresh_data(self) -> None:
        """重新构建并渲染知识图谱。"""
        try:
            self._graph_data = self.graph_service.build_graph()
        except Exception as e:
            logger.error("Failed to build knowledge graph: %s", e)
            QMessageBox.warning(self, "加载失败", f"构建知识图谱失败：{e}")
            self._graph_data = {"nodes": [], "edges": []}

        self._render_graph()
        self._render_weak_list()

    def _render_weak_list(self) -> None:
        self.weak_list.clear()
        for point in self._graph_data.get("weak_points", []):
            self.weak_list.addItem(QListWidgetItem(f"⚠ {point}"))
        if not self.weak_list.count():
            self.weak_list.addItem(QListWidgetItem("暂无薄弱知识点"))

    def _layout_nodes(
        self, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]
    ) -> Dict[str, QPointF]:
        """简易力导向布局，返回节点坐标。

        使用以节点 id 派生的固定随机种子，保证同一张图每次刷新布局一致。
        """
        if not nodes:
            return {}

        width, height = 800, 600
        # 布局全过程使用同一固定种子的随机源：初始位置与抖动均可复现
        rng = random.Random(42)
        positions: Dict[str, QPointF] = {}
        for n in nodes:
            positions[n["id"]] = QPointF(
                rng.uniform(width * 0.2, width * 0.8),
                rng.uniform(height * 0.2, height * 0.8),
            )

        iterations = 120
        k_repulse = 8000.0
        k_spring = 0.015
        center = QPointF(width / 2, height / 2)
        damping = 0.8

        velocities: Dict[str, QPointF] = {n["id"]: QPointF(0, 0) for n in nodes}

        for _ in range(iterations):
            # 斥力
            for n in nodes:
                pid = n["id"]
                for m in nodes:
                    qid = m["id"]
                    if pid == qid:
                        continue
                    diff = positions[pid] - positions[qid]
                    dist = math.hypot(diff.x(), diff.y())
                    if dist < 1:
                        diff = QPointF(rng.uniform(-1, 1), rng.uniform(-1, 1))
                        dist = 1
                    force = k_repulse / (dist * dist)
                    velocities[pid] += (diff / dist) * force * 0.5

            # 引力（边）
            for e in edges:
                a, b = e["source"], e["target"]
                if a not in positions or b not in positions:
                    continue
                diff = positions[b] - positions[a]
                dist = math.hypot(diff.x(), diff.y())
                if dist < 1:
                    dist = 1
                force = k_spring * dist
                vel = (diff / dist) * force
                velocities[a] += vel
                velocities[b] -= vel

            # 中心引力
            for n in nodes:
                pid = n["id"]
                diff = center - positions[pid]
                velocities[pid] += diff * 0.0005

            # 更新位置
            for n in nodes:
                pid = n["id"]
                positions[pid] += velocities[pid] * damping
                # 限制边界
                pos = positions[pid]
                pos.setX(max(50, min(width - 50, pos.x())))
                pos.setY(max(50, min(height - 50, pos.y())))
                positions[pid] = pos
                velocities[pid] *= 0.1

        return positions

    def _render_graph(self) -> None:
        self._scene.clear()
        self._node_items.clear()
        self._edge_items.clear()

        nodes = self._graph_data.get("nodes", [])
        edges = self._graph_data.get("edges", [])

        if not nodes:
            text = self._scene.addText("暂无足够数据生成知识图谱\n请录入错题或完成练习")
            text.setDefaultTextColor(QColor(theme_colors()["fg_secondary"]))
            return

        text_color = QColor(theme_colors()["fg_primary"])
        normal_edge = QColor(theme_colors()["fg_secondary"])
        normal_edge.setAlpha(110)
        alert_edge = QColor(theme_colors()["error"])
        alert_edge.setAlpha(170)

        positions = self._layout_nodes(nodes, edges)
        node_category = {n["id"]: n.get("category", "normal") for n in nodes}

        # 先画边：粗细 = 共现次数（关联强度），双薄弱 = 警示色
        for e in edges:
            a, b = e["source"], e["target"]
            if a not in positions or b not in positions:
                continue
            weight = int(e.get("weight", 1))
            pen = QPen(alert_edge if node_category.get(a) == "weak" and node_category.get(b) == "weak" else normal_edge)
            pen.setWidth(min(5, 1 + weight))
            line = QGraphicsLineItem(
                positions[a].x(), positions[a].y(),
                positions[b].x(), positions[b].y(),
            )
            line.setPen(pen)
            line.setZValue(1)
            line.setToolTip(
                f"「{a}」与「{b}」常一起出现（{weight} 次）\n"
                "关联越强的知识点越适合放在一起对比复习"
            )
            self._scene.addItem(line)
            self._edge_items.append(line)

        # 再画节点
        for n in nodes:
            pos = positions[n["id"]]
            color = node_color(n["category"])
            item = _NodeItem(
                pos.x(), pos.y(), n["radius"], n, color, text_color
            )
            self._scene.addItem(item)
            self._node_items[n["id"]] = item

        # 图例：颜色含义一目了然
        legend_items = [
            ("weak", "薄弱（需优先复习）"),
            ("normal", "待巩固"),
            ("strong", "较扎实"),
        ]
        legend_y = 12
        for category, label in legend_items:
            dot = self._scene.addEllipse(
                18, legend_y, 10, 10, QPen(Qt.PenStyle.NoPen),
                QBrush(node_color(category)),
            )
            dot.setZValue(5)
            caption = self._scene.addText(label)
            caption.setDefaultTextColor(QColor(theme_colors()["fg_secondary"]))
            font = caption.font()
            font.setPointSize(9)
            caption.setFont(font)
            caption.setPos(34, legend_y - 6)
            caption.setZValue(5)
            legend_y += 20

        # 补课路径：薄弱点推荐顺序（可解释）
        try:
            path = self.graph_service.build_review_path()
            if path:
                order = " → ".join(f"{i+1}.{step['point']}" for i, step in enumerate(path))
                tip = self._scene.addText(f"建议补课顺序：{order}")
                tip.setDefaultTextColor(QColor(theme_colors()["accent"]))
                font = tip.font()
                font.setPointSize(10)
                tip.setFont(font)
                tip.setPos(18, legend_y + 8)
                tip.setZValue(5)

        except Exception as e:
            logger.warning("Failed to build review path: %s", e)

        # 调整场景矩形
        self._scene.setSceneRect(0, 0, 800, 600)
        self.graph_view.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
