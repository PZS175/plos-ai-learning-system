"""AI 生成示意图服务。

基于本地文本模型将知识点或笔记转换为结构化图表（流程图、概念图、思维导图），
使用纯 Python 离线生成 SVG 矢量图，内置分层布局与力导向布局，
支持主题配色、边箭头、缩放与 Mermaid 源码编辑。
"""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..ai import ModelManager
from ..core.enums import Role
from ..core.models import ChatMessage
from ..db import Database, get_db
from ..utils.logger import get_logger
from ..utils.paths import get_attachments_dir
from .user_service import UserService

logger = get_logger("services.diagram_service")


_GENERATE_PROMPT = (
    "你是专业的教学图表设计助手。请根据以下主题生成一个结构化图表，"
    "类型为「{diagram_type}」。\n\n"
    "主题/提示：{prompt}\n\n"
    "要求：\n"
    "1. 提取核心概念作为节点，节点标签简洁（不超过 12 个字）；\n"
    "2. 用边表示概念之间的关系，必要时添加简短关系标签；\n"
    "3. 节点数量控制在 3 到 10 个之间，避免过度复杂；\n"
    "4. 严格输出 JSON，不要输出任何其他内容。\n\n"
    "输出格式：\n"
    "{{\n"
    "  \"nodes\": [{{\"id\": \"n1\", \"label\": \"节点1\"}}, ...],\n"
    "  \"edges\": [{{\"from\": \"n1\", \"to\": \"n2\", \"label\": \"关系\"}}, ...]\n"
    "}}\n"
    "如果主题无法生成有效图表，请输出包含一个根节点和 0 条边的合法 JSON。"
)


_DIAGRAM_TYPE_LABELS = {
    "flowchart": "流程图",
    "concept": "概念图",
    "mindmap": "思维导图",
    "mermaid": "Mermaid 图",
}


def _extract_json(text: str) -> Optional[dict]:
    """从模型输出中提取 JSON 对象。"""
    if not text:
        return None
    cleaned = text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json")[1].split("```")[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```")[1].split("```")[0].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


class DiagramTheme:
    """示意图主题配色，支持浅色/深色。"""

    def __init__(self, dark: bool = False):
        self.dark = dark
        self.bg = "#121721" if dark else "#FFFFFF"
        self.card_bg = "#1E2530" if dark else "#EAF4FF"
        self.card_stroke = "#6C7CFF" if dark else "#4F5DF5"
        self.text = "#E6EAF2" if dark else "#1E293B"
        self.title = "#E6EAF2" if dark else "#1E293B"
        self.edge = "#64748B"
        self.edge_label = "#94A3B8" if dark else "#475569"
        self.subtle_text = "#8A93A8"


class DiagramService:
    """AI 生成示意图业务服务。"""

    def __init__(
        self,
        model_manager: ModelManager,
        db: Optional[Database] = None,
        user_service: Optional[UserService] = None,
    ):
        self.model_manager = model_manager
        self.db = db or get_db()
        self.user_service = user_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def _chat(self, prompt: str, max_tokens: int = 2048) -> str:
        """统一调用本地文本模型。"""
        if not self.model_manager.is_text_available():
            raise RuntimeError("模型服务不可用，请检查 Ollama 是否运行。")
        messages = [ChatMessage(role=Role.USER, content=prompt)]
        response = self.model_manager.chat(messages=messages, max_tokens=max_tokens)
        if response is None or not response.content:
            raise RuntimeError("模型返回为空")
        return response.content

    def generate_diagram(
        self,
        prompt: str,
        diagram_type: str = "flowchart",
        source_type: str = "",
        source_id: int = 0,
        user_id: Optional[int] = None,
    ) -> dict:
        """生成示意图并入库，返回包含 id 与 mermaid_code 的字典。"""
        diagram_type = diagram_type if diagram_type in _DIAGRAM_TYPE_LABELS else "flowchart"
        prompt_text = _GENERATE_PROMPT.format(
            diagram_type=_DIAGRAM_TYPE_LABELS.get(diagram_type, "流程图"),
            prompt=prompt,
        )
        raw = self._chat(prompt_text)
        data = _extract_json(raw) or {"nodes": [], "edges": []}
        nodes = data.get("nodes") or []
        edges = data.get("edges") or []
        if not nodes:
            nodes = [{"id": "root", "label": prompt[:12] or "主题"}]
        mermaid_code = self._to_mermaid(nodes, edges, diagram_type)

        uid = self._user_id(user_id)
        record_id = self.db.insert(
            "INSERT INTO diagram_records (user_id, prompt, diagram_type, source_type, source_id, mermaid_code) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uid, prompt, diagram_type, source_type, source_id, mermaid_code),
        )
        logger.info(
            "Generated diagram id=%d type=%s user_id=%d nodes=%d edges=%d",
            record_id, diagram_type, uid, len(nodes), len(edges),
        )
        return {
            "id": record_id,
            "prompt": prompt,
            "diagram_type": diagram_type,
            "nodes": nodes,
            "edges": edges,
            "mermaid_code": mermaid_code,
        }

    @staticmethod
    def _to_mermaid(nodes: List[dict], edges: List[dict], diagram_type: str) -> str:
        """将结构化数据转换为 Mermaid 代码文本。"""
        lines = ["graph TD"]
        for node in nodes:
            nid = str(node.get("id", "")).strip() or f"n{nodes.index(node)}"
            label = str(node.get("label", nid)).replace('"', "#quot;")
            lines.append(f'    {nid}["{label}"]')
        for edge in edges:
            src = str(edge.get("from", "")).strip()
            dst = str(edge.get("to", "")).strip()
            label = str(edge.get("label", "")).replace('"', "#quot;")
            if not src or not dst:
                continue
            if label:
                lines.append(f'    {src} -->|"{label}"| {dst}')
            else:
                lines.append(f"    {src} --> {dst}")
        return "\n".join(lines)

    def render_diagram(
        self,
        diagram_id: int,
        user_id: Optional[int] = None,
        dark: bool = False,
    ) -> Path:
        """将示意图渲染为 SVG 文件，返回 SVG 路径。"""
        uid = self._user_id(user_id)
        row = self.db.fetchone(
            "SELECT id, prompt, diagram_type, mermaid_code FROM diagram_records WHERE id = ? AND user_id = ?",
            (diagram_id, uid),
        )
        if row is None:
            raise ValueError(f"示意图不存在：ID={diagram_id}")

        mermaid_code = row["mermaid_code"]
        diagram_type = row["diagram_type"]
        nodes, edges = self._parse_mermaid(mermaid_code)

        svg_content = self._draw_svg(nodes, edges, diagram_type, row["prompt"], dark=dark)
        output_path = self._svg_path(diagram_id, uid)
        output_path.write_text(svg_content, encoding="utf-8")

        self.db.execute(
            "UPDATE diagram_records SET svg_data = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (svg_content, diagram_id),
        )
        logger.info("Rendered diagram id=%d to %s", diagram_id, output_path)
        return output_path

    def update_mermaid_code(
        self,
        diagram_id: int,
        mermaid_code: str,
        user_id: Optional[int] = None,
    ) -> None:
        """更新示意图的 Mermaid 源码。"""
        uid = self._user_id(user_id)
        row = self.db.fetchone(
            "SELECT id FROM diagram_records WHERE id = ? AND user_id = ?",
            (diagram_id, uid),
        )
        if row is None:
            raise ValueError(f"示意图不存在：ID={diagram_id}")
        self.db.execute(
            "UPDATE diagram_records SET mermaid_code = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (mermaid_code, diagram_id),
        )

    @staticmethod
    def _svg_path(diagram_id: int, user_id: int) -> Path:
        attachments_dir = get_attachments_dir()
        attachments_dir.mkdir(parents=True, exist_ok=True)
        return attachments_dir / f"{user_id}_diagram_{diagram_id}.svg"

    @staticmethod
    def _parse_mermaid(mermaid_code: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """从 Mermaid 代码中解析节点与边。"""
        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []
        node_ids = set()
        for line in mermaid_code.splitlines():
            line = line.strip()
            if not line or line.startswith("graph"):
                continue
            node_match = re.match(r'^(\w+)\[(".+"|[^\]]+)\]\s*$', line)
            if node_match:
                nid = node_match.group(1)
                label = node_match.group(2).strip().strip('"').replace("#quot;", '"')
                nodes.append({"id": nid, "label": label})
                node_ids.add(nid)
                continue
            edge_match = re.match(
                r'^(\w+)\s*-->\s*(?:\|"([^"]+)"\|\s*)?(\w+)\s*$', line
            )
            if edge_match:
                src, label, dst = edge_match.groups()
                edges.append({"from": src, "to": dst, "label": label or ""})
                if src not in node_ids:
                    nodes.append({"id": src, "label": src})
                    node_ids.add(src)
                if dst not in node_ids:
                    nodes.append({"id": dst, "label": dst})
                    node_ids.add(dst)
        return nodes, edges

    @staticmethod
    def _draw_svg(
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        diagram_type: str,
        title: str,
        dark: bool = False,
    ) -> str:
        """使用 SVG 绘制示意图，返回 SVG XML 字符串。"""
        theme = DiagramTheme(dark=dark)
        node_count = len(nodes)
        if node_count == 0:
            return DiagramService._empty_svg(theme, "未能生成有效图表")

        positions = DiagramService._compute_layout(nodes, edges, diagram_type)
        node_w, node_h = 150, 54
        rx = 8

        xs = [p[0] for p in positions.values()]
        ys = [p[1] for p in positions.values()]
        margin = 80
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        width = max(420, int(max_x - min_x + node_w + margin * 2))
        height = max(320, int(max_y - min_y + node_h + margin * 2))
        offset_x = -min_x + margin
        offset_y = -min_y + margin

        def tx(x: float) -> float:
            return x + offset_x

        def ty(y: float) -> float:
            return y + offset_y

        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" style="background:{theme.bg};font-family:Microsoft YaHei UI,Microsoft YaHei,Noto Sans CJK SC,sans-serif;">',
            '<defs>',
            '  <marker id="arrowhead" markerWidth="10" markerHeight="7" refX="9" refY="3.5" orient="auto-start-reverse">',
            f'    <polygon points="0 0, 10 3.5, 0 7" fill="{theme.edge}"/>',
            '  </marker>',
            '</defs>',
        ]

        if title:
            parts.append(
                f'<text x="{width / 2}" y="34" text-anchor="middle" font-size="16" '
                f'font-weight="bold" fill="{theme.title}">{DiagramService._escape_xml(title[:40])}</text>'
            )

        # Edges
        node_rects = {
            nid: (tx(pos[0]), ty(pos[1]), node_w, node_h) for nid, pos in positions.items()
        }
        for edge in edges:
            src_rect = node_rects.get(edge["from"])
            dst_rect = node_rects.get(edge["to"])
            if src_rect is None or dst_rect is None:
                continue
            x1, y1 = src_rect[0] + src_rect[2] / 2, src_rect[1] + src_rect[3] / 2
            x2, y2 = dst_rect[0] + dst_rect[2] / 2, dst_rect[1] + dst_rect[3] / 2
            x1, y1, x2, y2 = DiagramService._edge_intersection(src_rect, dst_rect, x1, y1, x2, y2)

            parts.append(
                f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                f'stroke="{theme.edge}" stroke-width="2" marker-end="url(#arrowhead)"/>'
            )
            if edge.get("label"):
                mid_x = (x1 + x2) / 2
                mid_y = (y1 + y2) / 2
                parts.append(
                    f'<text x="{mid_x:.1f}" y="{mid_y - 7:.1f}" text-anchor="middle" font-size="12" '
                    f'fill="{theme.edge_label}">{DiagramService._escape_xml(edge["label"])}</text>'
                )

        # Nodes
        for node in nodes:
            x, y = positions[node["id"]]
            nx = tx(x)
            ny = ty(y)
            cx = nx + node_w / 2
            cy = ny + node_h / 2
            parts.append(
                f'<rect x="{nx:.1f}" y="{ny:.1f}" width="{node_w}" height="{node_h}" '
                f'rx="{rx}" fill="{theme.card_bg}" stroke="{theme.card_stroke}" stroke-width="2"/>'
            )
            label = DiagramService._escape_xml(node.get("label", node["id"]))
            parts.append(
                f'<text x="{cx:.1f}" y="{cy + 5:.1f}" text-anchor="middle" font-size="13" '
                f'fill="{theme.text}">{label}</text>'
            )

        parts.append("</svg>")
        return "\n".join(parts)

    @staticmethod
    def _empty_svg(theme: DiagramTheme, message: str) -> str:
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="400" height="200" '
            f'viewBox="0 0 400 200" style="background:{theme.bg};font-family:Microsoft YaHei UI,Microsoft YaHei,Noto Sans CJK SC,sans-serif;">'
            f'<text x="200" y="100" text-anchor="middle" font-size="14" fill="{theme.subtle_text}">'
            f'{DiagramService._escape_xml(message)}</text></svg>'
        )

    @staticmethod
    def _compute_layout(
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        diagram_type: str,
    ) -> Dict[str, Tuple[float, float]]:
        """根据图表类型计算节点坐标。"""
        node_w, node_h = 150, 54
        positions: Dict[str, Tuple[float, float]] = {}
        ids = [n["id"] for n in nodes]

        if diagram_type == "flowchart":
            positions = DiagramService._layered_layout(ids, edges, node_w, node_h)
        else:
            positions = DiagramService._force_layout(ids, edges, node_w, node_h)

        # 将整体平移到第一象限
        xs = [p[0] for p in positions.values()]
        ys = [p[1] for p in positions.values()]
        if xs and ys:
            min_x, min_y = min(xs), min(ys)
            positions = {nid: (x - min_x, y - min_y) for nid, (x, y) in positions.items()}
        return positions

    @staticmethod
    def _layered_layout(
        node_ids: List[str],
        edges: List[Dict[str, Any]],
        node_w: float,
        node_h: float,
    ) -> Dict[str, Tuple[float, float]]:
        """分层 DAG 布局：适合流程图。"""
        children: Dict[str, List[str]] = defaultdict(list)
        parents: Dict[str, List[str]] = defaultdict(list)
        for edge in edges:
            src, dst = edge["from"], edge["to"]
            if src in node_ids and dst in node_ids:
                children[src].append(dst)
                parents[dst].append(src)

        in_degree = {nid: len(parents[nid]) for nid in node_ids}
        queue = deque([nid for nid in node_ids if in_degree[nid] == 0])
        levels: Dict[str, int] = {}
        level_nodes: Dict[int, List[str]] = defaultdict(list)
        while queue:
            nid = queue.popleft()
            level = 0
            for p in parents[nid]:
                level = max(level, levels.get(p, 0) + 1)
            levels[nid] = level
            level_nodes[level].append(nid)
            for child in children[nid]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        # 处理环中未被访问的节点
        for nid in node_ids:
            if nid not in levels:
                levels[nid] = 0
                level_nodes[0].append(nid)

        gap_x, gap_y = 40, 70
        positions: Dict[str, Tuple[float, float]] = {}
        for level, nids in sorted(level_nodes.items()):
            y = level * (node_h + gap_y)
            start_x = 0
            for i, nid in enumerate(nids):
                x = start_x + i * (node_w + gap_x)
                positions[nid] = (x, y)
        return positions

    @staticmethod
    def _force_layout(
        node_ids: List[str],
        edges: List[Dict[str, Any]],
        node_w: float,
        node_h: float,
    ) -> Dict[str, Tuple[float, float]]:
        """力导向布局：适合概念图和思维导图。"""
        if not node_ids:
            return {}
        if len(node_ids) == 1:
            return {node_ids[0]: (0, 0)}

        adjacency: Dict[str, List[str]] = defaultdict(list)
        for edge in edges:
            src, dst = edge["from"], edge["to"]
            if src in node_ids and dst in node_ids:
                adjacency[src].append(dst)
                adjacency[dst].append(src)

        # 初始位置：圆周
        positions: Dict[str, Tuple[float, float]] = {}
        radius = max(150, len(node_ids) * 50)
        for i, nid in enumerate(node_ids):
            angle = 2 * math.pi * i / len(node_ids) - math.pi / 2
            positions[nid] = (radius * math.cos(angle), radius * math.sin(angle))

        # Fruchterman-Reingold 简化版
        area = (len(node_ids) * 120) ** 2
        k = math.sqrt(area / len(node_ids))
        temperature = radius / 2

        for _ in range(100):
            disp: Dict[str, List[float]] = {nid: [0.0, 0.0] for nid in node_ids}

            # 斥力
            for i, u in enumerate(node_ids):
                for v in node_ids[i + 1 :]:
                    dx = positions[u][0] - positions[v][0]
                    dy = positions[u][1] - positions[v][1]
                    dist = math.sqrt(dx * dx + dy * dy) + 0.01
                    force = k * k / dist
                    disp[u][0] += dx / dist * force
                    disp[u][1] += dy / dist * force
                    disp[v][0] -= dx / dist * force
                    disp[v][1] -= dy / dist * force

            # 引力
            for u, neighbors in adjacency.items():
                for v in neighbors:
                    dx = positions[v][0] - positions[u][0]
                    dy = positions[v][1] - positions[u][1]
                    dist = math.sqrt(dx * dx + dy * dy) + 0.01
                    force = dist * dist / k
                    disp[u][0] += dx / dist * force
                    disp[u][1] += dy / dist * force

            # 更新位置
            for nid in node_ids:
                dx, dy = disp[nid]
                dist = math.sqrt(dx * dx + dy * dy) + 0.01
                positions[nid] = (
                    positions[nid][0] + dx / dist * min(dist, temperature),
                    positions[nid][1] + dy / dist * min(dist, temperature),
                )
            temperature *= 0.95

        return positions

    @staticmethod
    def _edge_intersection(
        src_rect: Tuple[float, float, float, float],
        dst_rect: Tuple[float, float, float, float],
        x1: float,
        y1: float,
        x2: float,
        y2: float,
    ) -> Tuple[float, float, float, float]:
        """计算边与节点矩形边界的交点，使箭头从节点边缘发出。"""
        def clamp_line(rect: Tuple[float, float, float, float], ox: float, oy: float, dx: float, dy: float):
            cx, cy, w, h = rect
            half_w, half_h = w / 2, h / 2
            if abs(dx) < 1e-6:
                return ox, cy + (half_h if dy > 0 else -half_h)
            if abs(dy) < 1e-6:
                return cx + (half_w if dx > 0 else -half_w), oy
            tx_x = half_w / abs(dx)
            tx_y = half_h / abs(dy)
            t = min(tx_x, tx_y)
            return ox + dx * t, oy + dy * t

        dx = x2 - x1
        dy = y2 - y1
        x1, y1 = clamp_line(src_rect, x1, y1, dx, dy)
        x2, y2 = clamp_line(dst_rect, x2, y2, -dx, -dy)
        return x1, y1, x2, y2

    @staticmethod
    def _escape_xml(text: str) -> str:
        return (
            text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def list_diagrams(self, user_id: Optional[int] = None) -> List[dict]:
        """列出当前用户的示意图记录。"""
        uid = self._user_id(user_id)
        rows = self.db.fetchall(
            "SELECT id, prompt, diagram_type, source_type, source_id, created_at "
            "FROM diagram_records WHERE user_id = ? ORDER BY created_at DESC",
            (uid,),
        )
        return [dict(r) for r in rows]

    def delete_diagram(self, diagram_id: int, user_id: Optional[int] = None) -> None:
        """删除示意图记录及对应 SVG 文件。"""
        uid = self._user_id(user_id)
        row = self.db.fetchone(
            "SELECT id FROM diagram_records WHERE id = ? AND user_id = ?",
            (diagram_id, uid),
        )
        if row is None:
            raise ValueError(f"示意图不存在：ID={diagram_id}")
        image_path = self._svg_path(diagram_id, uid)
        try:
            if image_path.exists():
                image_path.unlink()
        except Exception as e:
            logger.warning("Failed to delete diagram svg %s: %s", image_path, e)
        self.db.execute(
            "DELETE FROM diagram_records WHERE id = ? AND user_id = ?",
            (diagram_id, uid),
        )
