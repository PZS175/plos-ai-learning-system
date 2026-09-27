"""AI 生成示意图服务测试。

验证：
1. 模型生成结构化数据并保存到 diagram_records
2. Mermaid 代码解析正确
3. 离线生成 SVG 矢量图（浅色/深色主题）
4. 分层布局与力导向布局
5. 列表与删除接口正常
6. Mermaid 源码更新与重新渲染
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock, patch

from plos.core.models import InferenceResponse
from plos.services import DiagramService
from plos.services.user_service import UserService


class _MockUserService(UserService):
    def __init__(self):
        pass

    def get_current_user_id(self) -> int:
        return 0


class _MockDatabase:
    """模拟 diagram_records 内存数据库。"""

    def __init__(self):
        self._rows = {}
        self._next_id = 1

    def fetchone(self, query: str, parameters: tuple = ()) -> Optional[dict]:
        if "diagram_records" not in query:
            return None
        if "WHERE id = ?" in query and "AND user_id = ?" in query:
            did = parameters[0]
            return self._rows.get(did)
        if "WHERE id = ?" in query:
            did = parameters[0]
            return self._rows.get(did)
        return None

    def fetchall(self, query: str, parameters: tuple = ()) -> list:
        if "diagram_records" not in query:
            return []
        uid = parameters[0] if parameters else None
        return [
            {
                "id": r["id"],
                "prompt": r["prompt"],
                "diagram_type": r["diagram_type"],
                "source_type": r["source_type"],
                "source_id": r["source_id"],
                "created_at": "2024-01-01 00:00:00",
            }
            for r in self._rows.values()
            if uid is None or r.get("user_id") == uid
        ]

    def execute(self, query: str, parameters: tuple = ()) -> Any:
        if "UPDATE" in query and "diagram_records" in query:
            did = parameters[-1]
            if did in self._rows:
                if "mermaid_code" in query:
                    self._rows[did]["mermaid_code"] = parameters[0]
                else:
                    self._rows[did]["svg_data"] = parameters[0]
        elif "DELETE" in query and "diagram_records" in query:
            did = parameters[0]
            self._rows.pop(did, None)
        return MagicMock()

    def insert(self, query: str, parameters: tuple = ()) -> int:
        if "diagram_records" not in query:
            return 0
        did = self._next_id
        self._next_id += 1
        self._rows[did] = {
            "id": did,
            "user_id": parameters[0],
            "prompt": parameters[1],
            "diagram_type": parameters[2],
            "source_type": parameters[3],
            "source_id": parameters[4],
            "mermaid_code": parameters[5],
            "svg_data": "",
        }
        return did


class _MockModelManager:
    def is_text_available(self) -> bool:
        return True

    def chat(self, messages: list, **kwargs):
        content = (
            '{"nodes": [{"id": "a", "label": "开始"}, {"id": "b", "label": "结束"}], '
            '"edges": [{"from": "a", "to": "b", "label": "完成"}]}'
        )
        return InferenceResponse(content=content, model="mock")


def _create_service() -> DiagramService:
    """创建服务实例；调用方需通过 patch 将附件目录指向临时目录。"""
    return DiagramService(
        model_manager=_MockModelManager(),
        db=_MockDatabase(),
        user_service=_MockUserService(),
    )


def test_generate_diagram():
    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch("plos.services.diagram_service.get_attachments_dir", return_value=Path(tmp_dir)):
            service = _create_service()
            result = service.generate_diagram(
                "测试流程",
                diagram_type="flowchart",
                source_type="test",
                source_id=1,
            )
            assert result["id"] == 1
            assert result["diagram_type"] == "flowchart"
            assert result["prompt"] == "测试流程"
            assert "graph TD" in result["mermaid_code"]
            assert "开始" in result["mermaid_code"]
            assert "结束" in result["mermaid_code"]
            assert 'a -->|"完成"| b' in result["mermaid_code"]

            row = service.db.fetchone(
                "SELECT * FROM diagram_records WHERE id = ? AND user_id = ?",
                (result["id"], 0),
            )
            assert row is not None
            assert row["mermaid_code"] == result["mermaid_code"]

    print("test_generate_diagram passed")


def test_render_diagram():
    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch("plos.services.diagram_service.get_attachments_dir", return_value=Path(tmp_dir)):
            service = _create_service()
            result = service.generate_diagram("测试流程", diagram_type="flowchart")
            image_path = service.render_diagram(result["id"])
            assert image_path.exists()
            assert image_path.suffix == ".svg"
            content = image_path.read_text(encoding="utf-8")
            assert "<svg" in content
            assert "</svg>" in content
            assert "开始" in content or "结束" in content

            row = service.db.fetchone(
                "SELECT * FROM diagram_records WHERE id = ? AND user_id = ?",
                (result["id"], 0),
            )
            assert row is not None
            assert row["svg_data"] == content

    print("test_render_diagram passed")


def test_render_dark_theme():
    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch("plos.services.diagram_service.get_attachments_dir", return_value=Path(tmp_dir)):
            service = _create_service()
            result = service.generate_diagram("测试", diagram_type="concept")
            image_path = service.render_diagram(result["id"], dark=True)
            content = image_path.read_text(encoding="utf-8")
            assert "#121721" in content or "#1E2530" in content

    print("test_render_dark_theme passed")


def test_parse_mermaid():
    code = """graph TD
    A["开始"]
    B["结束"]
    C["其他"]
    A -->|"执行"| B
    """
    nodes, edges = DiagramService._parse_mermaid(code)
    assert len(nodes) == 3
    assert len(edges) == 1
    node_ids = {n["id"] for n in nodes}
    assert node_ids == {"A", "B", "C"}
    assert edges[0]["from"] == "A"
    assert edges[0]["to"] == "B"
    assert edges[0]["label"] == "执行"

    simple_code = "graph TD\n    X --> Y\n"
    nodes, edges = DiagramService._parse_mermaid(simple_code)
    assert len(nodes) == 2
    assert len(edges) == 1
    assert edges[0]["label"] == ""

    print("test_parse_mermaid passed")


def test_layouts():
    nodes = [{"id": f"n{i}", "label": f"节点{i}"} for i in range(5)]
    edges = [
        {"from": "n0", "to": "n1", "label": ""},
        {"from": "n0", "to": "n2", "label": ""},
        {"from": "n1", "to": "n3", "label": ""},
        {"from": "n2", "to": "n3", "label": ""},
        {"from": "n3", "to": "n4", "label": ""},
    ]
    flow_positions = DiagramService._compute_layout(nodes, edges, "flowchart")
    assert len(flow_positions) == 5
    # 力导向布局
    force_positions = DiagramService._compute_layout(nodes, edges, "concept")
    assert len(force_positions) == 5
    # 所有坐标非负
    for pos in flow_positions.values():
        assert pos[0] >= 0 and pos[1] >= 0

    print("test_layouts passed")


def test_update_mermaid_code():
    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch("plos.services.diagram_service.get_attachments_dir", return_value=Path(tmp_dir)):
            service = _create_service()
            result = service.generate_diagram("原始", diagram_type="flowchart")
            new_code = "graph TD\n    X[\"新开始\"] --> Y[\"新结束\"]\n"
            service.update_mermaid_code(result["id"], new_code)
            row = service.db.fetchone(
                "SELECT mermaid_code FROM diagram_records WHERE id = ?",
                (result["id"],),
            )
            assert row["mermaid_code"] == new_code

            image_path = service.render_diagram(result["id"])
            content = image_path.read_text(encoding="utf-8")
            assert "新开始" in content

    print("test_update_mermaid_code passed")


def test_list_and_delete():
    with tempfile.TemporaryDirectory() as tmp_dir:
        with patch("plos.services.diagram_service.get_attachments_dir", return_value=Path(tmp_dir)):
            service = _create_service()
            result = service.generate_diagram("测试", diagram_type="concept")

            diagrams = service.list_diagrams()
            assert len(diagrams) == 1
            assert diagrams[0]["prompt"] == "测试"
            assert diagrams[0]["diagram_type"] == "concept"

            image_path = service.render_diagram(result["id"])
            assert image_path.exists()

            service.delete_diagram(result["id"])
            diagrams = service.list_diagrams()
            assert len(diagrams) == 0
            assert not image_path.exists()

    print("test_list_and_delete passed")


if __name__ == "__main__":
    test_generate_diagram()
    test_render_diagram()
    test_render_dark_theme()
    test_parse_mermaid()
    test_layouts()
    test_update_mermaid_code()
    test_list_and_delete()
    print("All diagram tests passed")
