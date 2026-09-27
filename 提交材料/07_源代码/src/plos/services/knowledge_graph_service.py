"""知识图谱服务。

基于错题本与练习记录构建知识点关联网络，标记知识漏洞与薄弱节点。
数据来源于现有表：error_book.knowledge_points / mastery_level、practice_records。
不新增业务表，仅做可视化计算。
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from ..db import Database, get_db
from ..utils.logger import get_logger
from .user_service import UserService

logger = get_logger("services.knowledge_graph_service")

#: 错题本掌握度上限（mastery_level 取值为 0/1/2 三档）
_MASTERY_MAX = 2
#: 节点配色阈值（掌握率百分比）：≥_STRONG 绿、≥_NORMAL 黄、其余红
_MASTERY_STRONG = 80
_MASTERY_NORMAL = 40


def _parse_knowledge_points(text: Optional[str]) -> List[str]:
    """解析错题本中的知识点 JSON 数组。"""
    if not text:
        return []
    try:
        data = json.loads(text)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except Exception:
        pass
    # 兼容旧数据：逗号分隔
    return [x.strip() for x in str(text).split(",") if x.strip()]


class KnowledgeGraphService:
    """构建并返回知识点网络图数据。"""

    def __init__(
        self,
        db: Optional[Database] = None,
        user_service: Optional[UserService] = None,
    ):
        self.db = db or get_db()
        self.user_service = user_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def build_review_path(self, user_id: Optional[int] = None, limit: int = 5) -> List[Dict[str, Any]]:
        """为薄弱知识点规划补课顺序（可解释）。

        排序规则：互相关联的薄弱点归为一组连着复习（对照学习效率更高），
        组内按错题数降序（错得最多的先补）；孤立薄弱点排在其后。
        """
        graph = self.build_graph(user_id=user_id)
        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        weak = [n for n in nodes if n.get("category") == "weak"]
        if not weak:
            return []
        weak_ids = {n["id"] for n in weak}

        # 邻接表（仅薄弱点之间的边）
        neighbors: Dict[str, set] = {n["id"]: set() for n in weak}
        for e in edges:
            a, b = e["source"], e["target"]
            if a in weak_ids and b in weak_ids:
                neighbors[a].add(b)
                neighbors[b].add(a)

        by_id = {n["id"]: n for n in weak}
        visited: set = set()
        path: List[Dict[str, Any]] = []

        # 组遍历：组内按 error_count 降序
        remaining = sorted(weak, key=lambda n: -(n.get("error_count") or 0))
        for seed in remaining:
            if seed["id"] in visited:
                continue
            # 收集与 seed 连通的薄弱点
            group = []
            stack = [seed["id"]]
            while stack:
                cur = stack.pop()
                if cur in visited:
                    continue
                visited.add(cur)
                group.append(by_id[cur])
                stack.extend(neighbors.get(cur, set()) - visited)
            group.sort(key=lambda n: -(n.get("error_count") or 0))
            for idx, node in enumerate(group):
                related = sorted(neighbors.get(node["id"], set()) - {node["id"]})
                reason = (
                    f"错题 {node.get('error_count', 0)} 道"
                    + (f"，与「{'、'.join(related[:2])}」关联，建议连着复习" if related and idx > 0 else "")
                )
                path.append(
                    {
                        "point": node["id"],
                        "error_count": node.get("error_count", 0),
                        "mastery": round(float(node.get("avg_mastery", 0) or 0), 2),
                        "reason": reason,
                    }
                )
            if len(path) >= limit:
                break
        return path[:limit]

    def build_graph(self, user_id: Optional[int] = None) -> Dict[str, Any]:
        """构建知识点网络图。

        返回：
        {
            "nodes": [{"id": str, "label": str, "error_count": int,
                       "practice_count": int, "accuracy": float,
                       "avg_mastery": float, "mastery_rate": int,
                       "category": str, "radius": float}, ...],
            "edges": [{"source": str, "target": str, "weight": int}, ...],
            "weak_points": [str, ...],
        }
        """
        uid = self._user_id(user_id)

        # 节点统计容器
        node_stats: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {
                "error_count": 0,
                "practice_count": 0,
                "correct_count": 0,
                "mastery_sum": 0,
                "mastery_count": 0,
            }
        )
        # 边统计：共同出现的知识点对
        edge_weights: Dict[Tuple[str, str], int] = defaultdict(int)

        # 1. 从错题本统计知识点
        errors = self.db.fetchall(
            "SELECT knowledge_points, mastery_level FROM error_book WHERE user_id = ?",
            (uid,),
        )
        for row in errors:
            kps = _parse_knowledge_points(row.get("knowledge_points"))
            mastery = row.get("mastery_level", 0) or 0
            for kp in kps:
                node_stats[kp]["error_count"] += 1
                node_stats[kp]["mastery_sum"] += mastery
                node_stats[kp]["mastery_count"] += 1
            # 同一错题知识点之间建立边
            for i, a in enumerate(kps):
                for b in kps[i + 1 :]:
                    key = (a, b) if a < b else (b, a)
                    edge_weights[key] += 1

        # 2. 从练习记录统计知识点正确率
        records = self.db.fetchall(
            "SELECT knowledge_point, is_correct FROM practice_records WHERE user_id = ?",
            (uid,),
        )
        for row in records:
            kp = str(row.get("knowledge_point", "") or "").strip()
            if not kp:
                continue
            node_stats[kp]["practice_count"] += 1
            if row.get("is_correct"):
                node_stats[kp]["correct_count"] += 1

        # 3. 构建节点列表
        nodes: List[Dict[str, Any]] = []
        weak_points: List[str] = []
        for kp, stats in node_stats.items():
            error_count = stats["error_count"]
            practice_count = stats["practice_count"]
            correct_count = stats["correct_count"]
            accuracy = correct_count / practice_count if practice_count > 0 else 1.0
            avg_mastery = (
                stats["mastery_sum"] / stats["mastery_count"]
                if stats["mastery_count"] > 0
                else 0
            )

            # 掌握率：优先取错题本掌握度（0~_MASTERY_MAX → 0~100%），
            # 没有掌握度记录的知识点退回练习正确率
            if stats["mastery_count"] > 0:
                mastery_rate = round(avg_mastery / _MASTERY_MAX * 100)
            else:
                mastery_rate = round(accuracy * 100)

            # 节点分类按掌握率阈值：≥80% 绿(strong)、40~79% 黄(normal)、<40% 红(weak)
            if mastery_rate >= _MASTERY_STRONG:
                category = "strong"
            elif mastery_rate >= _MASTERY_NORMAL:
                category = "normal"
            else:
                category = "weak"
                weak_points.append(kp)

            # 半径：基础 + 错误数/练习数加权
            radius = 12 + min(24, (error_count + practice_count) * 2)

            nodes.append(
                {
                    "id": kp,
                    "label": kp,
                    "error_count": error_count,
                    "practice_count": practice_count,
                    "accuracy": round(accuracy, 2),
                    "avg_mastery": round(avg_mastery, 2),
                    "mastery_rate": mastery_rate,
                    "category": category,
                    "radius": radius,
                }
            )

        # 4. 构建边列表（过滤低权重边避免图太乱）
        edges: List[Dict[str, Any]] = []
        for (a, b), weight in edge_weights.items():
            if weight >= 1 and a in node_stats and b in node_stats:
                edges.append({"source": a, "target": b, "weight": weight})

        return {
            "nodes": nodes,
            "edges": edges,
            "weak_points": weak_points,
        }

    def get_weak_points(self, user_id: Optional[int] = None) -> List[str]:
        """返回当前薄弱知识点列表。"""
        graph = self.build_graph(user_id=user_id)
        return graph.get("weak_points", [])
