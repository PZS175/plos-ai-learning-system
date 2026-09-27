"""自学闭环新功能回归：复习统计接线 / 周报聚合 / 番茄钟 / 每日目标。"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from plos.db import Database
from plos.services import FlashcardService, StatisticsService, UserService


@pytest.fixture()
def env(tmp_path: Path):
    db = Database(db_path=tmp_path / "learn.db")
    users = UserService(db=db)
    stats = StatisticsService(db=db, user_service=users)
    cards = FlashcardService(db=db, user_service=users, statistics_service=stats)
    yield db, users, stats, cards
    db.close()


class TestReviewStatsWiring:
    def test_review_records_statistics(self, env):
        """真实复习必须写入统计（修复前仅演示数据写入，仪表盘恒为 0）。"""
        db, users, stats, cards = env
        uid = users.get_current_user_id()
        card_id = cards.add_card(front_content="1+1=?", back_content="2", subject="数学")

        cards.review_card(card_id, __import__("plos.core.enums", fromlist=["FlashcardRating"]).FlashcardRating.GOOD)

        row = db.fetchone(
            "SELECT COUNT(*) AS c FROM statistics_records "
            "WHERE user_id = ? AND record_type = 'flashcard_review'",
            (uid,),
        )
        assert row["c"] == 1, "复习评分未写入统计"

    def test_review_without_stats_service_still_works(self, tmp_path: Path):
        """未注入统计服务时（旧调用方）复习不应报错。"""
        db = Database(db_path=tmp_path / "nostats.db")
        users = UserService(db=db)
        cards = FlashcardService(db=db, user_service=users)
        from plos.core.enums import FlashcardRating

        card_id = cards.add_card(front_content="Q", back_content="A")
        result = cards.review_card(card_id, FlashcardRating.GOOD)
        assert result["repetitions"] == 1
        db.close()


class TestWeeklyReport:
    def test_windows_do_not_overlap(self, env):
        """本周与上周窗口不得重叠（回归：上周误含本周记录）。"""
        db, users, stats, _cards = env
        uid = users.get_current_user_id()
        today = date.today()
        monday = today - timedelta(days=today.weekday())
        db.execute(
            "INSERT INTO statistics_records (user_id, record_type, record_date, value) VALUES (?, 'study_duration', ?, 45)",
            (uid, today.isoformat()),
        )
        db.execute(
            "INSERT INTO statistics_records (user_id, record_type, record_date, value) VALUES (?, 'study_duration', ?, 70)",
            (uid, (monday - timedelta(days=1)).isoformat()),
        )
        r = stats.get_weekly_report()
        assert r["minutes"] == 45.0
        assert r["minutes_prev"] == 70.0

    def test_accuracy_from_ratings(self, env):
        db, users, stats, _cards = env
        uid = users.get_current_user_id()
        today = date.today().isoformat()
        for value in (2, 3, 0):  # 好/简单/忘记 → 正确率 2/3
            db.execute(
                "INSERT INTO statistics_records (user_id, record_type, record_date, value) VALUES (?, 'flashcard_review', ?, ?)",
                (uid, today, value),
            )
        r = stats.get_weekly_report()
        assert r["reviews"] == 3
        assert abs(r["accuracy"] - 66.7) < 0.1


class TestChatStreaming:
    def test_send_message_stream_events(self, env, monkeypatch):
        """流式对话：chunk 事件序列 + 完整文本落库。"""
        from plos.services import ChatService, ErrorBookService, SessionManager

        db, users, _stats, _cards = env

        class FakeManager:
            backend_type = type("BT", (), {"value": "ollama"})()

            def is_text_available(self):
                return True

            def chat_stream(self, messages, **kw):
                yield "勾股"
                yield "定理 OK"

        service = ChatService(
            FakeManager(),
            SessionManager(db=db, user_service=users),
            rag_service=None,
            errorbook_service=ErrorBookService(db=db, user_service=users, model_manager=None),
            user_service=users,
        )
        cid = service.create_conversation("t")
        events = list(service.send_message_stream(cid, "讲讲勾股定理"))
        kinds = [e["type"] for e in events]
        assert kinds[0] == "chunk" and kinds[-1] == "done"
        done = [e for e in events if e["type"] == "done"][0]
        assert done["content"] == "勾股定理 OK"
        assert "duration_ms" in done

class TestPomodoro:
    def test_finish_records_duration_and_emits(self, env, qapp_ui, monkeypatch):
        """手动结束：专注分钟数写入统计并发出信号。"""

        from plos.ui.pomodoro_widget import PomodoroWidget

        _db, _users, stats, _cards = env
        widget = PomodoroWidget(statistics_service=stats, focus_minutes=25)
        widget.subject_edit.setText("数学")
        # 走真实启动流程（捕获科目），再模拟已专注 5 分钟
        widget._start()
        widget._remaining = 20 * 60
        recorded = []
        widget.session_recorded.connect(lambda m, s: recorded.append((m, s)))
        monkeypatch.setattr(
            "plos.ui.pomodoro_widget.show_warning", lambda *a, **k: None, raising=False
        )
        widget._finish_session()

        assert recorded == [(5, "数学")], f"信号未按预期发出：{recorded}"
        row = stats.db.fetchone(
            "SELECT COALESCE(SUM(value), 0) AS m FROM statistics_records WHERE record_type = 'study_duration'"
        )
        assert row["m"] == 5
        assert widget._state == "idle"

    def test_under_one_minute_not_recorded(self, env, qapp_ui, monkeypatch):
        from plos.ui.pomodoro_widget import PomodoroWidget

        _db, _users, stats, _cards = env
        widget = PomodoroWidget(statistics_service=stats)
        widget._state = "focusing"
        widget._remaining = 24 * 60 + 30  # 不足 1 分钟
        warnings = []
        import plos.ui.pomodoro_widget as mod

        monkeypatch.setattr(mod, "show_warning", lambda *a, **k: warnings.append(a), raising=False)
        widget._finish_session()
        assert warnings, "不足 1 分钟应有提示"
        row = stats.db.fetchone(
            "SELECT COUNT(*) AS c FROM statistics_records WHERE record_type = 'study_duration'"
        )
        assert row["c"] == 0


@pytest.fixture()
def qapp_ui():
    import os

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    return QApplication.instance() or QApplication(["learning_features"])
