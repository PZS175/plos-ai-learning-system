"""学习包导入分享服务测试。

验证：
1. 学习包导出为 zip 并包含 package.json
2. 数据表（错题、闪卡等）正确打包与导入
3. 分享码生成与格式校验
4. 列表与删除接口正常
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path
from typing import Any, Optional
from unittest.mock import MagicMock

from plos.services import LearningPackageService
from plos.services.user_service import UserService


class _MockUserService(UserService):
    def __init__(self):
        pass

    def get_current_user_id(self) -> int:
        return 0


class _MockDatabase:
    """模拟多表内存数据库。"""

    def __init__(self):
        self._rows: dict = {}
        self._next_id = 1

    def _table(self, table: str) -> list:
        if table not in self._rows:
            self._rows[table] = []
        return self._rows[table]

    def fetchone(self, query: str, parameters: tuple = ()) -> Optional[dict]:
        query = query.lower()
        if "learning_packages" in query and "where share_code" in query:
            for r in self._rows.get("learning_packages", []):
                if r["share_code"] == parameters[0] and r["is_shared"] == 1:
                    return dict(r)
            return None
        if "where id = ?" in query and "and user_id = ?" in query:
            table = "learning_packages"
            for r in self._rows.get(table, []):
                if r["id"] == parameters[0] and r["user_id"] == parameters[1]:
                    return dict(r)
            return None
        return None

    def fetchall(self, query: str, parameters: tuple = ()) -> list:
        query = query.lower()
        if "learning_packages" in query and "where user_id = ?" in query:
            uid = parameters[0]
            return [
                {
                    "id": r["id"],
                    "title": r["title"],
                    "description": r["description"],
                    "content_json": r["content_json"],
                    "is_shared": r["is_shared"],
                    "share_code": r["share_code"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                }
                for r in self._rows.get("learning_packages", [])
                if r["user_id"] == uid
            ]
        if "from error_book" in query and "where user_id = ?" in query:
            uid = parameters[0]
            return [dict(r) for r in self._rows.get("error_book", []) if r["user_id"] == uid]
        if "from flashcards" in query and "where user_id = ?" in query:
            uid = parameters[0]
            return [dict(r) for r in self._rows.get("flashcards", []) if r["user_id"] == uid]
        if "from study_plans" in query and "where user_id = ?" in query:
            uid = parameters[0]
            return [dict(r) for r in self._rows.get("study_plans", []) if r["user_id"] == uid]
        if "from documents" in query and "where user_id = ?" in query:
            uid = parameters[0]
            return [dict(r) for r in self._rows.get("documents", []) if r["user_id"] == uid]
        return []

    def execute(self, query: str, parameters: tuple = ()) -> Any:
        query_lower = query.lower()
        if "update learning_packages" in query_lower:
            for r in self._rows.get("learning_packages", []):
                if r["id"] == parameters[-1]:
                    if len(parameters) == 3:
                        r["is_shared"] = parameters[0]
                        r["share_code"] = parameters[1]
                    elif len(parameters) == 2:
                        r["share_code"] = parameters[0]
        elif "delete from learning_packages" in query_lower:
            table = self._table("learning_packages")
            self._rows["learning_packages"] = [
                r for r in table
                if not (r["id"] == parameters[0] and r["user_id"] == parameters[1])
            ]
        else:
            table = self._table_from_query(query_lower)
            if table:
                self._table(table).append(dict(zip([table], [parameters])))
        return MagicMock()

    def insert(self, query: str, parameters: tuple = ()) -> int:
        query_lower = query.lower()
        if "learning_packages" in query_lower:
            table = "learning_packages"
        elif "error_book" in query_lower:
            table = "error_book"
        elif "flashcards" in query_lower:
            table = "flashcards"
        elif "study_plans" in query_lower:
            table = "study_plans"
        else:
            table = "unknown"

        row = {"id": self._next_id}
        self._next_id += 1
        # 简单根据参数顺序填充常见字段
        if table == "learning_packages":
            row.update({
                "user_id": parameters[0],
                "title": parameters[1],
                "description": parameters[2],
                "content_json": parameters[3],
                "is_shared": parameters[4],
                "share_code": parameters[5],
                "created_at": "2024-01-01 00:00:00",
                "updated_at": "2024-01-01 00:00:00",
            })
        elif table == "error_book":
            row.update({
                "user_id": parameters[0],
                "question": parameters[1],
                "answer": parameters[2],
                "analysis": parameters[3],
                "knowledge_tags": parameters[4],
                "knowledge_points": parameters[5],
                "question_type": parameters[6],
                "image_path": parameters[7],
                "mastery_level": parameters[8],
                "subject": parameters[9],
                "chapter": parameters[10],
                "knowledge_point": parameters[11],
                "difficulty": parameters[12],
            })
        elif table == "flashcards":
            row.update({
                "user_id": parameters[0],
                "front_content": parameters[1],
                "back_content": parameters[2],
                "front_image": parameters[3],
                "card_type": parameters[4],
                "subject": parameters[5],
                "tags": parameters[6],
                "ease": parameters[7],
                "interval": parameters[8],
                "repetitions": parameters[9],
                "next_review": parameters[10],
            })
        elif table == "study_plans":
            row.update({
                "user_id": parameters[0],
                "title": parameters[1],
                "goal": parameters[2],
                "weak_subjects": parameters[3],
                "daily_minutes": parameters[4],
                "start_date": parameters[5],
                "end_date": parameters[6],
            })
        self._table(table).append(row)
        return row["id"]

    def get_connection(self):
        raise NotImplementedError("MockDatabase does not provide raw connection")

    def _table_from_query(self, query: str) -> Optional[str]:
        # 仅用于 execute 的简单分发表
        return None


def _create_service() -> LearningPackageService:
    return LearningPackageService(
        db=_MockDatabase(),
        user_service=_MockUserService(),
        errorbook_service=None,
        flashcard_service=None,
        study_plan_service=None,
        rag_service=None,
    )


def test_share_code_format():
    service = _create_service()
    code = service.share_package(
        service.db.insert(
            "INSERT INTO learning_packages (user_id, title, description, content_json, is_shared, share_code) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (0, "测试", "", "{}", 0, ""),
        )
    )
    assert len(code) == 10
    assert code.isalnum()

    info = service.parse_share_code(code)
    assert info["valid"] is True
    assert info["title"] == "测试"

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
        path = Path(f.name)
    path.unlink()


def test_export_import_round_trip():
    service = _create_service()
    # 预置一条错题
    service.db.insert(
        "INSERT INTO error_book (user_id, question, answer, analysis, knowledge_tags, "
        "knowledge_points, question_type, image_path, mastery_level, subject, chapter, knowledge_point, difficulty) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (0, "题", "答", "析", "tag", '["kp"]', "选择", "", 0, "数学", "", "", 1),
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_path = Path(tmp_dir) / "pack.zip"
        includes = {
            "error_book": True,
            "flashcards": False,
            "study_plans": False,
            "study_plan_tasks": False,
            "note_annotations": False,
            "terms": False,
            "documents": False,
        }
        result_path = service.export_package(
            output_path,
            title="测试包",
            description="测试描述",
            includes=includes,
        )
        assert result_path.exists()

        with zipfile.ZipFile(result_path, "r") as zf:
            assert "package.json" in zf.namelist()
            data = zf.read("package.json").decode("utf-8")
            package = __import__("json").loads(data)
            assert package["meta"]["title"] == "测试包"
            assert package["meta"]["description"] == "测试描述"
            assert len(package["error_book"]) == 1
            assert package["error_book"][0]["question"] == "题"

        counts = service.import_package(result_path)
        assert counts["error_book"] == 1

        packages = service.list_packages()
        assert len(packages) == 1
        assert packages[0]["title"] == "测试包"

    print("test_export_import_round_trip passed")


def test_delete_package():
    service = _create_service()
    pid = service.db.insert(
        "INSERT INTO learning_packages (user_id, title, description, content_json, is_shared, share_code) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (0, "待删", "", "{}", 0, ""),
    )
    service.delete_package(pid)
    assert len(service.list_packages()) == 0
    print("test_delete_package passed")


def test_preview_package():
    service = _create_service()
    service.db.insert(
        "INSERT INTO error_book (user_id, question, answer, analysis, knowledge_tags, "
        "knowledge_points, question_type, image_path, mastery_level, subject, chapter, knowledge_point, difficulty) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (0, "题", "答", "析", "tag", '["kp"]', "选择", "", 0, "数学", "", "", 1),
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_path = Path(tmp_dir) / "pack.zip"
        includes = {t: (t == "error_book") for t in ["error_book", "flashcards", "study_plans", "study_plan_tasks", "note_annotations", "terms"]}
        includes["documents"] = False
        service.export_package(output_path, title="预览包", includes=includes)

        preview = service.preview_package(output_path)
        assert preview["meta"]["title"] == "预览包"
        assert preview["counts"]["error_book"] == 1
        assert preview["counts"]["flashcards"] == 0
        assert preview["checksum_ok"] is True

        # 选择性导入：只导入 error_book
        counts = service.import_package(output_path, selected={"error_book": True, "flashcards": False})
        assert counts["error_book"] == 1

    print("test_preview_package passed")


def test_progress_callback():
    service = _create_service()
    progress_log = []

    with tempfile.TemporaryDirectory() as tmp_dir:
        output_path = Path(tmp_dir) / "pack.zip"
        includes = {t: False for t in ["error_book", "flashcards", "study_plans", "study_plan_tasks", "note_annotations", "terms"]}
        includes["documents"] = False
        service.export_package(output_path, title="进度包", includes=includes, progress_callback=lambda p, m: progress_log.append((p, m)))

    assert any(p == 100 for p, _ in progress_log)
    print("test_progress_callback passed")


if __name__ == "__main__":
    test_share_code_format()
    test_export_import_round_trip()
    test_delete_package()
    test_preview_package()
    test_progress_callback()
    print("All learning package tests passed")
