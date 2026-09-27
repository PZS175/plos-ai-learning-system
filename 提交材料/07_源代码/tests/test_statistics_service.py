"""学习统计服务测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from plos.db import Database
from plos.services import StatisticsService, UserService


@pytest.fixture()
def stats_env(tmp_path: Path):
    db = Database(db_path=tmp_path / "stats.db")
    user_service = UserService(db=db)
    service = StatisticsService(db=db, user_service=user_service)
    yield service
    db.close()


def test_record_and_read_study_duration(stats_env):
    service = stats_env
    service.record_study_duration(30, subject="数学", user_id=0)
    service.record_study_duration(45, subject="数学", user_id=0)

    daily = service.get_daily_study_duration(days=7, user_id=0)
    assert len(daily) == 7
    # 最近一天应包含今天记录的 75 分钟
    assert daily[-1]["minutes"] == 75


def test_record_flashcard_review(stats_env):
    service = stats_env
    service.record_flashcard_review(2, subject="数学", user_id=0)
    service.record_flashcard_review(3, subject="数学", user_id=0)

    summary = service.get_dashboard_summary()
    assert isinstance(summary, dict)


def test_user_data_isolation(tmp_path: Path):
    db = Database(db_path=tmp_path / "stats_isolation.db")
    user_service = UserService(db=db)
    service = StatisticsService(db=db, user_service=user_service)

    users = user_service.list_users()
    assert len(users) >= 1
    uid_a = users[0]["id"]
    uid_b = user_service.create_user("stats_user_b", "统计隔离B")

    service.record_study_duration(60, user_id=uid_a)
    service.record_study_duration(10, user_id=uid_b)

    total_a = sum(d["minutes"] for d in service.get_daily_study_duration(days=1, user_id=uid_a))
    total_b = sum(d["minutes"] for d in service.get_daily_study_duration(days=1, user_id=uid_b))
    assert total_a == 60
    assert total_b == 10
    db.close()
