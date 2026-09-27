"""备份与恢复服务测试（破坏性操作的恢复路径此前零覆盖）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from plos.db import Database
from plos.services import ErrorBookService, FlashcardService, UserService
from plos.services.backup_service import BackupService


@pytest.fixture()
def backup_env(tmp_path: Path):
    db = Database(db_path=tmp_path / "backup.db")
    user_service = UserService(db=db)
    errorbook_service = ErrorBookService(db=db, user_service=user_service)
    flashcard_service = FlashcardService(db=db, user_service=user_service)
    backup_service = BackupService(
        db=db,
        user_service=user_service,
        errorbook_service=errorbook_service,
        flashcard_service=flashcard_service,
    )
    yield db, errorbook_service, flashcard_service, backup_service, tmp_path
    db.close()


def _seed(db: Database, errorbook_service, flashcard_service) -> tuple[int, int]:
    error_id = errorbook_service.add_error(
        question="备份测试题干",
        answer="备份测试答案",
        analysis="备份测试解析",
        subject="数学",
    )
    card_id = flashcard_service.add_card(
        front_content="备份测试正面",
        back_content="备份测试背面",
        subject="数学",
    )
    return error_id, card_id


def _count_rows(db: Database, table: str) -> int:
    return db.fetchone(f"SELECT COUNT(*) AS c FROM {table}")["c"]


def test_full_backup_roundtrip(backup_env, tmp_path: Path):
    db, errorbook_service, flashcard_service, backup_service, tmp = backup_env
    _seed(db, errorbook_service, flashcard_service)
    errors_before = _count_rows(db, "error_book")
    cards_before = _count_rows(db, "flashcards")
    assert errors_before >= 1 and cards_before >= 1

    # 导出备份
    backup_path = backup_service.export_full_backup(tmp_path / "backup.zip")
    assert backup_path.exists()
    assert backup_path.stat().st_size > 0

    # 破坏性清空后恢复
    db.execute("DELETE FROM error_book")
    db.execute("DELETE FROM flashcards")
    assert _count_rows(db, "error_book") == 0
    assert _count_rows(db, "flashcards") == 0

    restored = backup_service.import_full_backup(backup_path)
    assert restored > 0
    assert _count_rows(db, "error_book") == errors_before
    assert _count_rows(db, "flashcards") == cards_before


def test_markdown_exports(backup_env, tmp_path: Path):
    db, errorbook_service, flashcard_service, backup_service, tmp = backup_env
    _seed(db, errorbook_service, flashcard_service)

    errors_md = backup_service.export_errors_to_markdown(tmp_path / "errors.md")
    assert errors_md >= 1
    assert (tmp_path / "errors.md").read_text(encoding="utf-8").find("备份测试题干") >= 0

    cards_md = backup_service.export_flashcards_to_markdown(tmp_path / "cards.md")
    assert cards_md >= 1
    assert (tmp_path / "cards.md").read_text(encoding="utf-8").find("备份测试正面") >= 0
