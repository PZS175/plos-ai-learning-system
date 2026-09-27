"""知识图谱服务测试。

验证：
1. 基于错题本知识点构建节点和边
2. 薄弱知识点识别正确
3. 节点分类（weak/normal/strong）合理
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from plos.db import Database
from plos.services import ErrorBookService, KnowledgeGraphService
from plos.services.user_service import UserService


class _MockUserService(UserService):
    def get_current_user_id(self) -> int:
        return 0


def test_knowledge_graph():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test_graph.db"
        db = Database(db_path=db_path)
        user_service = _MockUserService()
        errorbook_service = ErrorBookService(db=db, user_service=user_service)
        graph_service = KnowledgeGraphService(db=db, user_service=user_service)

        # 添加错题：导数相关知识点
        errorbook_service.add_error(
            question="求 f(x)=x^2 的导数",
            answer="2x",
            analysis="幂函数求导法则",
            knowledge_points=["导数", "幂函数"],
            question_type="解答题",
            subject="数学",
            chapter="函数与导数",
            knowledge_point="导数",
            difficulty=3,
            mastery_level=0,
            user_id=0,
        )
        errorbook_service.add_error(
            question="求 f(x)=x^3 的导数",
            answer="3x^2",
            analysis="幂函数求导法则",
            knowledge_points=["导数", "幂函数", "复合函数"],
            question_type="解答题",
            subject="数学",
            chapter="函数与导数",
            knowledge_point="导数",
            difficulty=3,
            mastery_level=0,
            user_id=0,
        )

        graph = graph_service.build_graph(user_id=0)
        assert "nodes" in graph
        assert "edges" in graph
        assert "weak_points" in graph

        node_ids = {n["id"] for n in graph["nodes"]}
        assert "导数" in node_ids
        assert "幂函数" in node_ids
        assert "复合函数" in node_ids

        # 错题数 >=2 且掌握度为 0 应标记为 weak
        assert "导数" in graph["weak_points"]
        assert "幂函数" in graph["weak_points"]

        # 边：导数-幂函数 共同出现 2 次
        edge = next(
            (e for e in graph["edges"]
             if set([e["source"], e["target"]]) == {"导数", "幂函数"}),
            None,
        )
        assert edge is not None
        assert edge["weight"] == 2

        # 关闭数据库连接
        if db._connection is not None:
            db._connection.close()
            db._connection = None

        print("test_knowledge_graph passed")


if __name__ == "__main__":
    test_knowledge_graph()
