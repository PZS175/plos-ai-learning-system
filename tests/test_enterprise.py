"""企业级底座回归：迁移框架 / 自动备份 / CLI / streak / 服务容器。"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from plos.db import Database
from plos.db.migrations import LATEST_VERSION, get_schema_version, run_migrations


@pytest.fixture()
def temp_db(tmp_path: Path):
    db = Database(db_path=tmp_path / "ent.db")
    yield db
    db.close()


class TestMigrations:
    def test_fresh_db_at_latest_version(self, temp_db):
        assert get_schema_version(temp_db) == LATEST_VERSION

    def test_rerun_is_idempotent(self, temp_db):
        assert run_migrations(temp_db) == 0

    def test_hot_indices_created(self, temp_db):
        names = {
            r["name"]
            for r in temp_db.fetchall(
                "SELECT name FROM sqlite_master WHERE type='index'"
            )
        }
        for expected in (
            "idx_error_book_user",
            "idx_flashcards_user_review",
            "idx_statistics_user_type_date",
            "idx_study_logs_user_created",
        ):
            assert expected in names, f"缺少索引 {expected}"

    def test_downgrade_then_upgrade(self, temp_db):
        temp_db.execute("PRAGMA user_version = 0")
        assert run_migrations(temp_db) == LATEST_VERSION
        assert get_schema_version(temp_db) == LATEST_VERSION


class TestAutoBackup:
    def test_create_and_same_day_skip(self, temp_db, monkeypatch, tmp_path: Path):
        from plos.services.backup_service import BackupService
        import plos.services.backup_service as bs_mod

        monkeypatch.setattr(bs_mod, "get_data_dir", lambda: tmp_path / "data")
        service = BackupService(db=temp_db)

        first = service.run_auto_backup(keep=3)
        assert first is not None and first.exists() and first.stat().st_size > 0
        assert service.run_auto_backup(keep=3) is None  # 当日不重复

    def test_prune_keeps_latest(self, temp_db, monkeypatch, tmp_path: Path):
        from plos.services.backup_service import BackupService
        import plos.services.backup_service as bs_mod

        monkeypatch.setattr(bs_mod, "get_data_dir", lambda: tmp_path / "data")
        service = BackupService(db=temp_db)

        backup_dir = tmp_path / "data" / "backups" / "auto"
        backup_dir.mkdir(parents=True, exist_ok=True)
        for stamp in ("20260101_000001", "20260102_000001", "20260103_000001"):
            (backup_dir / f"plos_auto_{stamp}.db").write_bytes(b"x")

        service._set_setting("auto_backup_last_date", "")
        service.run_auto_backup(keep=2)
        # 保留策略按「自动备份总数」计算：新旧格式（.db / .plosbackup）都计入，
        # 否则历史 .db 备份永远不会被清理
        remaining = sorted(p.name for p in backup_dir.glob("plos_auto_*"))
        assert len(remaining) == 2, f"应保留 2 份，实际 {remaining}"
        assert "plos_auto_20260101_000001.db" not in remaining


class TestStudyStreak:
    def _seed(self, db: Database, uid: int, dates: list[str]) -> None:
        for d in dates:
            db.execute(
                "INSERT INTO statistics_records (user_id, record_type, record_date, value) "
                "VALUES (?, 'study_duration', ?, ?)",
                (uid, d, 30),
            )

    def test_streak_counts_consecutive_days(self, temp_db):
        from plos.services.statistics_service import StatisticsService
        from plos.services.user_service import UserService

        users = UserService(db=temp_db)
        service = StatisticsService(db=temp_db, user_service=users)
        uid = users.get_current_user_id()
        today = date.today()
        self._seed(
            temp_db,
            uid,
            [(today - timedelta(days=i)).isoformat() for i in range(3)],
        )
        assert service.get_study_streak() == 3

    def test_today_missing_starts_from_yesterday(self, temp_db):
        from plos.services.statistics_service import StatisticsService
        from plos.services.user_service import UserService

        users = UserService(db=temp_db)
        service = StatisticsService(db=temp_db, user_service=users)
        uid = users.get_current_user_id()
        today = date.today()
        self._seed(
            temp_db,
            uid,
            [(today - timedelta(days=i)).isoformat() for i in range(1, 5)],
        )
        assert service.get_study_streak() == 4

    def test_gap_breaks_streak(self, temp_db):
        from plos.services.statistics_service import StatisticsService
        from plos.services.user_service import UserService

        users = UserService(db=temp_db)
        service = StatisticsService(db=temp_db, user_service=users)
        self._seed(
            temp_db,
            users.get_current_user_id(),
            [(date.today() - timedelta(days=5)).isoformat()],
        )
        assert service.get_study_streak() == 0


class TestSmartQueue:
    def test_three_source_aggregation(self, tmp_path: Path):
        """到期卡/薄弱点/计划任务三源聚合 + 优先级排序。"""
        from plos.services import ErrorBookService, FlashcardService, UserService
        from plos.services.smart_queue_service import SmartQueueService

        db = Database(db_path=tmp_path / "sq.db")
        users = UserService(db=db)
        uid = users.get_current_user_id()
        cards = FlashcardService(db=db, user_service=users)
        cards.add_card(front_content="什么是导数？", back_content="变化率")
        db.execute("UPDATE flashcards SET next_review = '2020-01-01 00:00:00'")
        eb = ErrorBookService(db=db, user_service=users, model_manager=None)
        eb.add_error(question="Q", answer="A", knowledge_points=["导数"])
        plan_id = db.insert(
            "INSERT INTO study_plans (user_id, title) VALUES (?, '本周计划')", (uid,)
        )
        db.execute(
            "INSERT INTO study_plan_tasks (plan_id, user_id, title) VALUES (?, ?, '复习讲义')",
            (plan_id, uid),
        )
        sq = SmartQueueService(db=db, user_service=users, flashcard_service=cards, errorbook_service=eb)
        queue = sq.build_daily_queue()
        types = [i["type"] for i in queue["items"]]
        assert types == ["flashcard", "weak_review", "plan_task"], types
        assert queue["items"][0]["priority"] >= queue["items"][-1]["priority"]
        assert all(i["reason"] for i in queue["items"])
        db.close()

class TestCauseProfile:
    def test_keyword_and_fallback_classification(self, tmp_path: Path):
        """AI 批改关键词优先归类；无记录按掌握度降级推断。"""
        from plos.services import ErrorBookService, UserService
        from plos.services.cause_profile_service import CauseProfileService

        db = Database(db_path=tmp_path / "cp.db")
        users = UserService(db=db)
        eb = ErrorBookService(db=db, user_service=users, model_manager=None)
        e1 = eb.add_error(question="计算 12*13", answer="156", mastery_level=0)
        eb.add_error(question="概念辨析题", answer="B", mastery_level=1)
        db.execute(
            "INSERT INTO subjective_grades (user_id, error_id, error_reasons) VALUES (?, ?, '审题看错条件')",
            (users.get_current_user_id(), e1),
        )
        cp = CauseProfileService(db=db, user_service=users)
        profile = cp.get_cause_profile()
        assert profile["total"] == 2
        by_label = {q["label"]: q["count"] for q in profile["quadrants"]}
        assert by_label.get("审题偏差") == 1  # 关键词归类
        assert by_label.get("概念不清") == 1  # 掌握度降级
        assert all(q["prescription"] for q in profile["quadrants"])
        db.close()

class TestReviewPath:
    def test_weak_points_grouped_by_connectivity(self, tmp_path: Path):
        """关联薄弱点成组连排，孤立点殿后，理由可解释。"""
        import json as _json

        from plos.services import KnowledgeGraphService, UserService

        db = Database(db_path=tmp_path / "rp.db")
        users = UserService(db=db)
        uid = users.get_current_user_id()
        for kps, m in [(["导数", "极限"], 0), ("导数", 0), (["概率"], 0)]:
            db.execute(
                "INSERT INTO error_book (user_id, question, answer, knowledge_points, mastery_level) "
                "VALUES (?, 'Q', 'A', ?, ?)",
                (uid, _json.dumps(kps, ensure_ascii=False), m),
            )
        svc = KnowledgeGraphService(db=db, user_service=users)
        path = svc.build_review_path(user_id=uid)
        points = [s["point"] for s in path]
        assert points[0] == "导数"  # 错题最多且关联最多
        assert "概率" in points  # 孤立薄弱点也在路径中
        assert all(s["reason"] for s in path)
        db.close()

class TestWebDAVSync:
    def test_upload_download_roundtrip(self, tmp_path: Path):
        """本地 HTTP 存根验证上传/下载/列表（WebDAV 语义子集）。"""
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        store = {}

        class Stub(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _respond(self, code=200, body=b"", text=""):
                self.send_response(code)
                self.send_header("Content-Type", "text/xml; charset=utf-8")
                self.end_headers()
                self.wfile.write(body or text.encode("utf-8"))

            def do_MKCOL(self):
                self._respond(201)

            def do_PUT(self):
                length = int(self.headers.get("Content-Length", 0))
                store[self.path] = self.rfile.read(length)
                self._respond(201)

            def do_GET(self):
                if self.path in store:
                    self._respond(200, body=store[self.path])
                else:
                    self._respond(404)

            def do_PROPFIND(self):
                names = "".join(
                    f"<D:displayname>{n.rsplit('/', 1)[-1]}</D:displayname>"
                    for n in store
                )
                self._respond(207, text=f"<D:multistatus>{names}</D:multistatus>")

        server = HTTPServer(("127.0.0.1", 0), Stub)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        port = server.server_address[1]

        from plos.services.webdav_sync_service import WebDAVSyncService

        src = tmp_path / "pkg.zip"
        src.write_bytes(b"FAKE-ZIP-CONTENT")
        svc = WebDAVSyncService(
            base_url=f"http://127.0.0.1:{port}",
            username="u",
            password="p",
            remote_dir="plos-sync",
        )
        name = svc.upload_package(src, remote_name="pkg.zip")
        assert name == "pkg.zip"

        out = svc.download_package("pkg.zip", tmp_path / "down")
        assert out.read_bytes() == b"FAKE-ZIP-CONTENT"

        listing = svc.list_remote()
        assert any(item["name"] == "pkg.zip" for item in listing)
        server.shutdown()

class TestPluginLoader:
    def test_discover_and_register(self, tmp_path: Path):
        """清单校验 + register 回调 + 坏插件跳过不阻塞。"""
        import json as _json

        from plos.services.plugin_loader import discover_plugins

        good = tmp_path / "good_plugin"
        good.mkdir()
        (good / "plugin.json").write_text(
            _json.dumps({"name": "示例", "version": "1.0", "entry": "main"}),
            encoding="utf-8",
        )
        (good / "main.py").write_text(
            "def register(registry):\n    registry['panel'] = '示例面板'\n",
            encoding="utf-8",
        )
        bad = tmp_path / "bad_plugin"
        bad.mkdir()
        (bad / "plugin.json").write_text("{ broken", encoding="utf-8")

        registry = {}
        loaded = discover_plugins(tmp_path, registry)
        assert len(loaded) == 1 and loaded[0].name == "示例"
        assert registry.get("panel") == "示例面板"

class TestLatexCleanExport:
    def test_symbols_and_subscripts(self):
        """LaTeX 清洗：符号映射、分式根号、裸上下标。"""
        from plos.utils.latex_clean import latex_to_readable

        BS = chr(92)
        assert "≤" in latex_to_readable(BS + "leq")
        assert "∞" in latex_to_readable(BS + "infty")
        assert "∪" in latex_to_readable("A " + BS + "cup B")
        assert "(1)/(2)" in latex_to_readable(BS + "frac{1}{2}")
        assert "√(" in latex_to_readable(BS + "sqrt{2}")
        assert "x^(2)" in latex_to_readable("x^2")
        assert BS not in latex_to_readable(BS + "(x " + BS + "leq 3" + BS + ")")

    def test_export_contains_no_raw_latex(self, tmp_path: Path):
        """试卷导出边界：docx/pdf 文本不含反斜杠 LaTeX 记号。"""
        import json as _json

        from plos.services import ExamService, UserService

        BS = chr(92)

        class FakeManager:
            backend_type = type("BT", (), {"value": "ollama"})()

            def is_text_available(self):
                return True

            def chat(self, messages, max_tokens=4096):
                content = str(messages[-1].content)
                if "选择题" in content:
                    q = "若不等式 " + BS + "(x^2 - 3x + 2 > 0" + BS + ")，则解集为（ ）。"
                    opts = '["A. ' + BS + '(x < 1' + BS + ')", "B. ' + BS + '(x > 2' + BS + ')"]'
                    body = (
                        '[{"knowledge_point":"K","question":"' + q + '","options":'
                        + opts
                        + ',"answer":"A","analysis":"x","difficulty":3}]'
                    )
                    return type("R", (), {"content": body})()
                q = "判断：不等式 " + BS + "(x^2 - 5x + 6 < 0" + BS + ")"
                body = (
                    '[{"knowledge_point":"K","question":"' + q + '","options":[],"answer":"正确","analysis":"'
                    + BS + "leq " + BS + 'infty","difficulty":3}]'
                )
                return type("R", (), {"content": body})()

        db = Database(db_path=tmp_path / "ex.db")
        users = UserService(db=db)
        svc = ExamService(db=db, user_service=users, model_manager=FakeManager())
        uid = users.get_current_user_id()
        paper = svc.generate_paper(
            title="不等式卷",
            knowledge_points=["不等式"],
            type_counts={"choice": 1, "judge": 1},
            difficulty_min=2,
            difficulty_max=3,
            user_id=uid,
        )
        paths = svc.export_paper(paper["id"], tmp_path, fmt="docx", user_id=uid)
        from docx import Document

        text = "\n".join(p.text for p in Document(str(paths[0])).paragraphs)
        assert BS + "(" not in text and BS + "leq" not in text and BS + "infty" not in text
        db.close()

class TestCli:
    def test_doctor_on_temp_db(self, tmp_path: Path, monkeypatch):
        import plos.cli as cli

        db_path = tmp_path / "cli.db"
        Database(db_path=db_path).close()
        monkeypatch.setattr(cli, "_db_path", lambda: db_path)
        data_dir = tmp_path / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr("plos.utils.paths.get_data_dir", lambda: data_dir, raising=False)
        assert cli.cmd_doctor(with_models=False) == 0

    def test_migrate_reports_zero_steps(self, tmp_path: Path, monkeypatch, capsys):
        import plos.cli as cli

        db_path = tmp_path / "mig.db"
        Database(db_path=db_path).close()
        monkeypatch.setattr(cli, "_db_path", lambda: db_path)
        assert cli.cmd_migrate() == 0
        assert "执行 0 步" in capsys.readouterr().out

    def test_version_output(self, capsys):
        import plos.cli as cli
        from plos import __version__

        assert cli.cmd_version() == 0
        assert f"v{__version__}" in capsys.readouterr().out


class TestAppContext:
    def test_build_wires_services(self, tmp_path: Path, monkeypatch):
        import plos.db.database as dbmod
        from plos.core.context import AppContext

        monkeypatch.setattr(dbmod, "get_db_path", lambda: tmp_path / "ctx.db")
        ctx = AppContext.build(model_manager=None)

        assert len(ctx.__dataclass_fields__) == 29
        # 关键依赖关系：对话服务持有会话管理器；练习服务持有错题本
        assert ctx.chat_service.session_manager is ctx.session_manager
        assert ctx.practice_service.errorbook_service is ctx.errorbook_service
        # 输入路由持有 OCR 与 RAG
        assert ctx.input_router.ocr_service is ctx.ocr_service
