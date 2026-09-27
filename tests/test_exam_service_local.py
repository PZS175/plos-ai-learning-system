"""试卷生成与导出本地逻辑测试（迁移自根目录 exam_test.py）。

不依赖大模型：手动插入题目，测试组卷记录、关联查询、Word/PDF 导出。
原脚本直接连接真实用户库并 DELETE 数据，现改用临时数据库。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from plos.db import Database
from plos.services import ExamService
from plos.services.user_service import UserService


@pytest.fixture()
def exam_env(tmp_path: Path):
    db = Database(db_path=tmp_path / "exam_test.db")
    user_service = UserService(db=db)
    service = ExamService(db=db, user_service=user_service)
    yield db, service
    db.close()


def _insert_paper_with_questions(db: Database) -> int:
    qids = []
    for q_type, question, answer in [
        ("choice", "下列哪个是二次函数的图像？", "A"),
        ("fill", "一元二次方程 ax^2+bx+c=0 的求根公式中，判别式为____。", "b^2-4ac"),
        ("judge", "函数 y=x^2 在 R 上单调递增。", "错误"),
        ("short", "简述导数的几何意义。", "函数在某点切线的斜率"),
    ]:
        options = '["抛物线", "直线", "双曲线", "圆"]' if q_type == "choice" else "[]"
        qid = db.insert(
            "INSERT INTO practice_questions (user_id, knowledge_point, question_type, difficulty, question, options, answer, analysis) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (0, "函数与导数", q_type, 3, question, options, answer, "解析内容"),
        )
        qids.append(qid)

    paper_id = db.insert(
        "INSERT INTO exam_papers (user_id, title, knowledge_points, type_counts, difficulty_min, difficulty_max, total_score) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (0, "函数与导数测试卷", '["函数与导数"]', '{"choice":1,"fill":1,"judge":1,"short":1}', 2, 4, 18),
    )
    for idx, qid in enumerate(qids):
        db.insert(
            "INSERT INTO exam_paper_questions (paper_id, question_id, sort_order, score) VALUES (?, ?, ?, ?)",
            (paper_id, qid, idx, [3, 3, 2, 10][idx]),
        )
    return paper_id


def test_database_tables_created(exam_env):
    db, _ = exam_env
    tables = {r["name"] for r in db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "exam_papers" in tables
    assert "exam_paper_questions" in tables
    assert "practice_questions" in tables


def test_paper_query_and_delete_keeps_questions(exam_env):
    db, service = exam_env
    paper_id = _insert_paper_with_questions(db)

    paper = service.get_paper(paper_id, user_id=0)
    assert paper is not None
    assert len(paper["questions"]) == 4
    assert paper["total_score"] == 18

    # 删除试卷后题目仍保留在题库
    service.delete_paper(paper_id, user_id=0)
    assert service.get_paper(paper_id, user_id=0) is None
    remaining = db.fetchone("SELECT COUNT(*) AS c FROM practice_questions WHERE user_id = 0")["c"]
    assert remaining == 4


@pytest.mark.parametrize("fmt", ["docx", "pdf"])
def test_paper_export(exam_env, tmp_path: Path, fmt: str):
    db, service = exam_env
    paper_id = _insert_paper_with_questions(db)
    output_dir = tmp_path / "export"
    output_dir.mkdir()

    paths = service.export_paper(paper_id, output_dir, fmt=fmt, user_id=0)
    assert paths, f"{fmt} 导出应返回文件路径"
    for p in paths:
        assert p.exists(), f"{fmt} 导出文件不存在：{p}"
