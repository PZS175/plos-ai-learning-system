"""自适应练习模块功能测试（迁移自根目录 practice_test.py）。

不依赖大模型：验证数据库迁移、自适应难度逻辑、客观题判分、记录统计。
原脚本直接对真实用户库执行 DELETE，现改用临时数据库。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plos.db import Database
from plos.services.practice_service import PracticeService
from plos.services.user_service import UserService


@pytest.fixture()
def practice_env(tmp_path: Path):
    db = Database(db_path=tmp_path / "practice_test.db")
    user_service = UserService(db=db)
    service = PracticeService(db=db, user_service=user_service)
    yield db, service
    db.close()


def _insert_question(db: Database) -> int:
    return db.insert(
        "INSERT INTO practice_questions (user_id, knowledge_point, question_type, difficulty, question, options, answer, analysis) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (0, "导数", "choice", 3, "测试题干", '["甲","乙","丙","丁"]', "A", "测试解析"),
    )


def _add_record(db: Database, qid: int, answer: str, correct: int) -> None:
    db.insert(
        "INSERT INTO practice_records (user_id, question_id, knowledge_point, user_answer, is_correct, score, feedback) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (0, qid, "导数", answer, correct, 100 if correct else 0, "test"),
    )


def test_database_tables_created(practice_env):
    db, _ = practice_env
    tables = {r["name"] for r in db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "practice_questions" in tables
    assert "practice_records" in tables


def test_adaptive_difficulty(practice_env):
    db, service = practice_env
    qid = _insert_question(db)

    # 无记录 -> 默认 3
    assert service.suggest_difficulty("导数", user_id=0) == 3

    # 全对 -> 上调到 4
    for _ in range(4):
        _add_record(db, qid, "A", 1)
    assert service.suggest_difficulty("导数", user_id=0) == 4

    # 全错 -> 下调到 2
    db.execute("DELETE FROM practice_records WHERE user_id = 0")
    for _ in range(4):
        _add_record(db, qid, "B", 0)
    assert service.suggest_difficulty("导数", user_id=0) == 2

    # 中等 -> 维持 3
    db.execute("DELETE FROM practice_records WHERE user_id = 0")
    for i in range(4):
        _add_record(db, qid, "A", 1 if i < 2 else 0)
    assert service.suggest_difficulty("导数", user_id=0) == 3


def test_objective_grading_normalization(practice_env):
    db, service = practice_env
    qid = _insert_question(db)

    # 小写作答也应判对
    result = service.submit_answer(qid, "a", user_id=0)
    assert result["is_correct"] is True
    assert result["score"] == 100

    # 错误作答返回正确答案
    result2 = service.submit_answer(qid, "B", user_id=0)
    assert result2["is_correct"] is False
    assert result2["correct_answer"] == "A"


def test_stats_and_records(practice_env):
    db, service = practice_env
    qid = _insert_question(db)
    service.submit_answer(qid, "a", user_id=0)
    service.submit_answer(qid, "B", user_id=0)

    stats = service.get_stats(user_id=0)
    assert stats["total"] == 2 and stats["correct"] == 1
    records = service.list_records(limit=10, user_id=0)
    assert len(records) == 2
