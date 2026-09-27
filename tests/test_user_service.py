"""用户服务测试（此前无直接用例）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from plos.db import Database
from plos.services import UserService


@pytest.fixture()
def user_service(tmp_path: Path):
    db = Database(db_path=tmp_path / "users.db")
    service = UserService(db=db)
    yield service
    db.close()


def test_default_user_created(user_service):
    users = user_service.list_users()
    assert len(users) >= 1


def test_create_list_rename_delete_user(user_service):
    uid = user_service.create_user("tester", "测试员")
    assert uid > 0

    user = user_service.get_user(uid)
    assert user is not None
    assert user["username"] == "tester"
    assert user["nickname"] == "测试员"

    user_service.rename_user(uid, "测试员改")
    assert user_service.get_user(uid)["nickname"] == "测试员改"

    user_service.delete_user(uid)
    assert user_service.get_user(uid) is None


def test_duplicate_username_rejected(user_service):
    user_service.create_user("dup_user", "A")
    with pytest.raises(Exception):
        user_service.create_user("dup_user", "B")


def test_current_user_switch_and_delete_guard(user_service):
    users = user_service.list_users()
    current_id = user_service.get_current_user_id()
    assert any(u["id"] == current_id for u in users)

    new_id = user_service.create_user("switcher", "切换目标")
    user_service.set_current_user(new_id)
    assert user_service.get_current_user_id() == new_id

    # 删除当前登录用户：服务会先切换到其他用户再删除，保证始终有有效用户
    user_service.delete_user(new_id)
    assert user_service.get_user(new_id) is None
    assert user_service.get_current_user_id() != new_id
    assert user_service.get_current_user() is not None
