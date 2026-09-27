"""验证错题本与闪卡的数据持久化、导入导出。

规范化为标准 pytest 用例：使用临时数据库（原脚本直接清空真实用户库），
导出文件写入 tmp_path 而非 data/ 目录。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from plos.db import Database
from plos.services import ErrorBookService, FlashcardService


@pytest.fixture()
def persistence_env(tmp_path: Path):
    db = Database(db_path=tmp_path / "persistence.db")
    yield db
    db.close()


def test_errorbook_persistence_and_roundtrip(persistence_env, tmp_path: Path):
    db = persistence_env
    service = ErrorBookService(db)

    eid = service.add_error(
        question="牛顿第二定律公式？",
        answer="F = ma",
        analysis="力等于质量乘以加速度",
        knowledge_tags="物理,力学",
        mastery_level=1,
    )
    assert eid > 0

    # 模拟重新加载（同路径复用实例，数据仍在库中）
    service2 = ErrorBookService(db)
    errors = service2.list_errors()
    assert len(errors) == 1, f"Expected 1 error, got {len(errors)}"
    assert errors[0]["question"] == "牛顿第二定律公式？"

    # 导出 JSON
    json_path = tmp_path / "test_errorbook.json"
    count = service2.export_to_json(json_path)
    assert count == 1
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(data) == 1

    # 导出 CSV
    csv_path = tmp_path / "test_errorbook.csv"
    count = service2.export_to_csv(csv_path)
    assert count == 1
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
        assert len(rows) == 1

    # 清空并导入 JSON，内容保持一致
    db.execute("DELETE FROM error_book")
    imported = service2.import_from_json(json_path)
    assert imported == 1
    errors = service2.list_errors()
    assert errors[0]["answer"] == "F = ma"


def test_flashcard_persistence_and_roundtrip(persistence_env, tmp_path: Path):
    db = persistence_env
    service = FlashcardService(db)

    cid = service.add_card(front_content="牛顿第二定律", back_content="F = ma")
    assert cid > 0

    service2 = FlashcardService(db)
    cards = service2.list_cards()
    assert len(cards) == 1, f"Expected 1 card, got {len(cards)}"
    assert cards[0]["front_content"] == "牛顿第二定律"

    # 导出 JSON
    json_path = tmp_path / "test_flashcards.json"
    count = service2.export_to_json(json_path)
    assert count == 1
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert len(data) == 1

    # 导出 CSV
    csv_path = tmp_path / "test_flashcards.csv"
    count = service2.export_to_csv(csv_path)
    assert count == 1
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
        assert len(rows) == 1

    # 清空并导入 CSV，内容保持一致
    db.execute("DELETE FROM flashcards")
    imported = service2.import_from_csv(csv_path)
    assert imported == 1
    cards = service2.list_cards()
    assert cards[0]["back_content"] == "F = ma"
