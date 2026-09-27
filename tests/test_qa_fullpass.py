"""全流程黑盒+白盒回归测试（真实学生使用路径）。

覆盖既有用例没有触及的场景：
- 多账号数据隔离与账号删除后的数据清理
- 备份快照回滚、损坏备份包容错、分项导出
- 艾宾浩斯复习排期/推迟、知识图谱三档配色
- 大图自动降采样、Ollama 不可用时的友好降级
- 笔记 CRUD + 批注、教材缓存生命周期与磁盘上限
- 题库 Excel 导入与错误文件容错
- 插件发现、AI 线程池并发上限、数据库损坏容错
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt  # noqa: E402

from plos.db import Database  # noqa: E402
from plos.services import (  # noqa: E402
    BackupService,
    ErrorBookService,
    KnowledgeGraphService,
    NoteService,
    ReviewService,
    TextbookCacheService,
    UserService,
)


@pytest.fixture(scope="module")
def qapp():
    pytest.importorskip("PyQt6")
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(["qa_fullpass"])
    yield app


@pytest.fixture()
def qa_env(tmp_path: Path):
    db = Database(db_path=tmp_path / "qa.db")
    users = UserService(db=db)
    errorbook = ErrorBookService(db=db, user_service=users)
    notes = NoteService(db=db, user_service=users)
    yield {
        "db": db,
        "users": users,
        "errorbook": errorbook,
        "notes": notes,
        "tmp": tmp_path,
    }
    db.close()


# ----------------------------------------------------------------------
# 一、账号与多账号数据隔离
# ----------------------------------------------------------------------

def test_multi_user_data_isolation_and_delete(qa_env):
    """两个账号的错题/笔记必须完全隔离；删除账号后其数据一并消失。"""
    db, users = qa_env["db"], qa_env["users"]
    errorbook, notes = qa_env["errorbook"], qa_env["notes"]

    alice = users.create_user("alice", "爱丽丝", "pwd-a")
    bob = users.create_user("bob", "鲍勃", "pwd-b")
    assert alice != bob

    errorbook.add_error(question="ALICE-错题", answer="A", user_id=alice)
    notes.save_note("ALICE-笔记", "<p>a</p>", "a", user_id=alice)
    errorbook.add_error(question="BOB-错题", answer="B", user_id=bob)
    notes.save_note("BOB-笔记", "<p>b</p>", "b", user_id=bob)

    alice_errors = errorbook.list_errors(user_id=alice)
    bob_errors = errorbook.list_errors(user_id=bob)
    assert len(alice_errors) == 1 and alice_errors[0]["question"] == "ALICE-错题"
    assert len(bob_errors) == 1 and bob_errors[0]["question"] == "BOB-错题"
    assert [n["title"] for n in notes.list_notes(user_id=alice)] == ["ALICE-笔记"]
    assert [n["title"] for n in notes.list_notes(user_id=bob)] == ["BOB-笔记"]

    # 删除 alice 后：她的数据消失，bob 的数据不受影响
    users.delete_user(alice)
    assert errorbook.list_errors(user_id=alice) == []
    assert notes.list_notes(user_id=alice) == []
    assert len(errorbook.list_errors(user_id=bob)) == 1
    assert [n["title"] for n in notes.list_notes(user_id=bob)] == ["BOB-笔记"]

    orphan = db.fetchone(
        "SELECT COUNT(*) AS c FROM error_book WHERE user_id = ?", (alice,)
    )["c"]
    assert orphan == 0, "删除账号后错题表仍残留该用户数据"


# ----------------------------------------------------------------------
# 二、备份与恢复
# ----------------------------------------------------------------------

def test_backup_restore_rolls_back_to_snapshot(qa_env):
    """备份 → 改数据 → 导入备份，应回到备份时刻的数据状态。"""
    db, users = qa_env["db"], qa_env["users"]
    errorbook = qa_env["errorbook"]
    backup = BackupService(db=db, user_service=users, errorbook_service=errorbook)

    errorbook.add_error(question="快照题1", answer="a")
    errorbook.add_error(question="快照题2", answer="b")
    snapshot = errorbook.list_errors()
    assert len(snapshot) == 2

    pkg = backup.export_full_backup(qa_env["tmp"] / "snap.plosbackup")
    assert pkg.exists() and pkg.stat().st_size > 0

    # 备份后继续修改：新增一题并删掉一题
    errorbook.add_error(question="备份后新增", answer="c")
    errorbook.delete_error(snapshot[0]["id"])
    assert len(errorbook.list_errors()) == 2  # 2 - 1 + 1

    backup.import_full_backup(pkg)
    restored = errorbook.list_errors()
    assert len(restored) == 2, f"应回滚到快照的 2 题，实际 {len(restored)}"
    titles = sorted(item["question"] for item in restored)
    assert titles == ["快照题1", "快照题2"]
    assert "备份后新增" not in titles


def test_corrupt_backup_does_not_damage_database(qa_env):
    """损坏的备份包应被拒绝，且现有数据完好（不破坏数据库）。"""
    db, users = qa_env["db"], qa_env["users"]
    errorbook = qa_env["errorbook"]
    backup = BackupService(db=db, user_service=users, errorbook_service=errorbook)

    errorbook.add_error(question="现有数据不能被破坏", answer="a")
    before = len(errorbook.list_errors())

    broken = qa_env["tmp"] / "broken.plosbackup"
    broken.write_bytes(b"this is definitely not a zip archive")

    with pytest.raises(Exception):
        backup.import_full_backup(broken)

    assert len(errorbook.list_errors()) == before, "损坏备份导入破坏了现有数据"
    assert errorbook.list_errors()[0]["question"] == "现有数据不能被破坏"


def test_section_json_exports(qa_env):
    """错题本 / 笔记分项导出为 JSON，内容可校验。"""
    db, users = qa_env["db"], qa_env["users"]
    errorbook, notes = qa_env["errorbook"], qa_env["notes"]
    backup = BackupService(db=db, user_service=users, errorbook_service=errorbook)

    errorbook.add_error(question="分项导出题", answer="答案", subject="数学")
    notes.save_note("分项导出笔记", "<p>x</p>", "x")

    errors_json = qa_env["tmp"] / "errors.json"
    count = backup.export_errorbook_json(errors_json)
    assert count >= 1 and errors_json.exists()
    payload = json.loads(errors_json.read_text(encoding="utf-8"))
    assert "分项导出题" in json.dumps(payload, ensure_ascii=False)

    notes_json = qa_env["tmp"] / "notes.json"
    backup.export_notes_json(notes_json)
    assert notes_json.exists()
    assert "分项导出笔记" in notes_json.read_text(encoding="utf-8")


# ----------------------------------------------------------------------
# 三、学习闭环
# ----------------------------------------------------------------------

def test_review_schedule_due_and_postpone(qa_env):
    """艾宾浩斯：首次记为待复习，答对推进间隔，推迟后再次到期。"""
    db, users = qa_env["db"], qa_env["users"]
    review = ReviewService(db=db, user_service=users)
    uid = users.get_current_user_id()

    # 首次答对 → 立即进入待复习（下次复习时间在今天之后）
    review.schedule_review("error", 101, is_correct=True, user_id=uid)
    stats = review.get_review_stats(user_id=uid)
    assert stats.get("total", 0) >= 1, f"应生成记忆节点，实际 {stats}"

    # 推迟复习：推到未来，不再属于待复习
    review.postpone_review("error", 101, days=1, user_id=uid)
    due = review.get_due_reviews(user_id=uid)
    assert all(item.get("source_id") != 101 for item in due), "推迟后仍被判定为到期"

    # 连续答对应推进间隔（记忆周期自动更新）
    review.schedule_review("error", 202, is_correct=True, user_id=uid)
    first_due = db.fetchone(
        "SELECT next_review FROM review_schedules WHERE user_id = ? AND source_id = 202",
        (uid,),
    )["next_review"]
    review.schedule_review("error", 202, is_correct=True, user_id=uid)
    second_due = db.fetchone(
        "SELECT next_review FROM review_schedules WHERE user_id = ? AND source_id = 202",
        (uid,),
    )["next_review"]
    assert second_due >= first_due, "答对后记忆周期应不早于上一次"


def test_knowledge_graph_three_categories(qa_env):
    """知识图谱节点按掌握情况分为 weak / normal / strong 三档（对应红/黄/绿）。"""
    db, users = qa_env["db"], qa_env["users"]
    errorbook = qa_env["errorbook"]
    graph_service = KnowledgeGraphService(db=db, user_service=users)
    uid = users.get_current_user_id()

    # 掌握良好：无错题，仅练习正确率高
    qid = db.insert(
        "INSERT INTO practice_questions (user_id, knowledge_point, question_type, question) "
        "VALUES (?, ?, 'choice', ?)",
        (uid, "熟练点", "练习题"),
    )
    for _ in range(2):
        db.insert(
            "INSERT INTO practice_records (user_id, question_id, knowledge_point, is_correct) "
            "VALUES (?, ?, ?, 1)",
            (uid, qid, "熟练点"),
        )
    # 薄弱：错题多
    for i in range(3):
        errorbook.add_error(question=f"错题{i}", knowledge_points=["薄弱点"], answer="x")

    graph = graph_service.build_graph(user_id=uid)
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert "薄弱点" in nodes and nodes["薄弱点"]["category"] == "weak"
    assert "薄弱点" in graph["weak_points"]
    if "熟练点" in nodes:
        assert nodes["熟练点"]["category"] == "strong", "无错题且正确率高应为 strong"

    # 三档 → 颜色令牌映射必须齐备（面板按此上色）
    from plos.ui.knowledge_graph_panel import _NODE_CATEGORY_TOKENS

    assert set(_NODE_CATEGORY_TOKENS) >= {"weak", "normal", "strong"}
    assert _NODE_CATEGORY_TOKENS["weak"] == "error"
    assert _NODE_CATEGORY_TOKENS["normal"] == "warning"
    assert _NODE_CATEGORY_TOKENS["strong"] == "success"


# ----------------------------------------------------------------------
# 五、笔记 / 教材缓存 / 题库导入
# ----------------------------------------------------------------------

def test_note_crud_and_annotations(qa_env):
    """笔记新建/编辑/删除，以及批注增删查。"""
    db, users = qa_env["db"], qa_env["users"]
    notes = qa_env["notes"]
    uid = users.get_current_user_id()

    note_id = notes.save_note("我的笔记", "<p>初始内容</p>", "初始内容", notebook="数学本")
    assert note_id > 0
    assert "数学本" in notes.list_notebooks(user_id=uid)

    notes.save_note("我的笔记", "<p>改后内容</p>", "改后内容", note_id=note_id)
    fetched = notes.get_note(note_id, user_id=uid)
    assert "改后内容" in (fetched.get("content_text") or "")

    from plos.services.note_annotation_service import NoteAnnotationService

    annotations = NoteAnnotationService(db=db, user_service=users)
    doc_id = db.insert(
        "INSERT INTO error_book (user_id, question, answer) VALUES (?, ?, ?)",
        (uid, "批注目标题", "x"),
    )
    ann_id = annotations.add_annotation(
        target_type="error_book",
        target_id=doc_id,
        selected_text="改后内容",
        note_content="这里要背下来",
        user_id=uid,
    )
    assert ann_id, "批注应创建成功"
    assert len(annotations.list_annotations("error_book", doc_id, user_id=uid)) == 1

    annotations.delete_annotation(ann_id, user_id=uid)
    assert annotations.list_annotations("error_book", doc_id, user_id=uid) == []

    assert notes.delete_note(note_id, user_id=uid) is True
    assert notes.get_note(note_id, user_id=uid) is None


def test_textbook_cache_lifecycle_and_limit(qa_env, tmp_path: Path):
    """教材缓存：登记 / 统计占用 / 单本删除 / 清空，且磁盘上限生效。"""
    db, users = qa_env["db"], qa_env["users"]
    cache_dir = tmp_path / "textbook_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    svc = TextbookCacheService(db=db, user_service=users, cache_dir=cache_dir)
    uid = users.get_current_user_id()

    book = tmp_path / "book.pdf"
    book.write_bytes(b"%PDF-1.4 fake textbook" * 100)

    item = svc.cache_file(book, "数学必修一", subject="数学", user_id=uid)
    assert item["id"] > 0
    assert len(svc.list_cache(user_id=uid)) == 1
    assert svc.get_total_size(user_id=uid) == book.stat().st_size

    item2 = svc.cache_file(book, "数学必修二", subject="数学", user_id=uid)
    assert len(svc.list_cache(user_id=uid)) == 2

    assert svc.delete_cache(item["id"], user_id=uid) is True
    assert len(svc.list_cache(user_id=uid)) == 1

    removed = svc.clear_all(user_id=uid)
    assert removed >= 1
    assert svc.list_cache(user_id=uid) == []
    assert svc.get_total_size(user_id=uid) == 0
    assert item2["id"] > 0

    # 磁盘上限：把上限压到 1MB 后，塞入 3MB 教材必须被拒绝并给出可读提示
    monkeypatch_limit = pytest.MonkeyPatch()
    monkeypatch_limit.setattr(svc, "_get_limit_mb", lambda: 1)
    try:
        big = tmp_path / "big.pdf"
        big.write_bytes(b"0" * (3 * 1024 * 1024))
        with pytest.raises(RuntimeError) as exc:
            svc.cache_file(big, "超限教材", user_id=uid)
        assert "上限" in str(exc.value), "超限提示不友好"
        assert svc.list_cache(user_id=uid) == [], "超限教材仍被写入缓存"
    finally:
        monkeypatch_limit.undo()


def test_question_import_xlsx_and_bad_file(qa_env, tmp_path: Path):
    """批量题库导入：Excel 正常解析入库；格式错误文件友好报错且不污染题库。"""
    from plos.services.question_import_service import QuestionImportService

    db, users = qa_env["db"], qa_env["users"]
    errorbook = qa_env["errorbook"]
    svc = QuestionImportService(db=db, user_service=users)
    uid = users.get_current_user_id()

    openpyxl = pytest.importorskip("openpyxl")
    xlsx = tmp_path / "bank.xlsx"
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["题干", "A", "B", "C", "D", "答案", "解析", "知识点", "难度", "题型"])
    ws.append(["1+1=?", "1", "2", "3", "4", "B", "基础加法", "整数运算", 1, "选择题"])
    ws.append(["水的化学式是？", "", "", "", "", "H2O", "常识", "化学式", 2, "填空题"])
    wb.save(xlsx)

    parsed = svc.parse_file(xlsx)
    assert len(parsed) == 2
    assert parsed[0]["question"] == "1+1=?"
    assert parsed[0]["answer"] == "B"
    assert parsed[0]["knowledge_point"] == "整数运算"
    assert parsed[0]["question_type"] == "choice"
    assert len(parsed[0]["options"]) == 4
    assert not parsed[0].get("_error"), parsed[0].get("_error")

    # 批量导入写入的是练习题库（practice_questions），错题本由「导入选中题目」单独触发
    inserted = svc.commit_questions(parsed, user_id=uid)
    assert inserted >= 2, f"应至少入库 2 题，实际 {inserted}"
    hit = db.fetchone(
        "SELECT COUNT(*) AS c FROM practice_questions WHERE user_id = ? AND question = ?",
        (uid, "1+1=?"),
    )["c"]
    assert hit == 1, "导入的题目未进入练习题库"

    before = db.fetchone(
        "SELECT COUNT(*) AS c FROM practice_questions WHERE user_id = ?", (uid,)
    )["c"]

    # 错误格式：非 xlsx/docx → 明确报错，且不新增数据
    bad = tmp_path / "bank.txt"
    bad.write_text("这不是题库文件", encoding="utf-8")
    fake = tmp_path / "fake.xlsx"
    fake.write_bytes(b"not really an excel file")
    with pytest.raises(Exception):
        svc.parse_file(bad)
    with pytest.raises(Exception):
        svc.parse_file(fake)  # 损坏的 xlsx（伪装扩展名）同样必须报错
    after = db.fetchone(
        "SELECT COUNT(*) AS c FROM practice_questions WHERE user_id = ?", (uid,)
    )["c"]
    assert after == before, "错误文件污染了题库"


# ----------------------------------------------------------------------
# 四 / 六 / 七 / 八：图片降采样、插件、并发、异常边界
# ----------------------------------------------------------------------

def test_large_image_is_downscaled(tmp_path: Path):
    """超过阈值的图片应被自动压缩到最大边长以内（OCR 前的预处理）。"""
    pytest.importorskip("PIL")
    from PIL import Image

    from plos.ai.multimodal.image_preprocess import ImagePreprocessor
    from plos.core.constants import MAX_IMAGE_DIMENSION

    big = tmp_path / "big.png"
    Image.new("RGB", (3200, 1800), "white").save(big)

    out = ImagePreprocessor().process(big)
    assert Path(out).exists()
    with Image.open(out) as processed:
        assert max(processed.size) <= MAX_IMAGE_DIMENSION, (
            f"大图未压缩：{processed.size} > {MAX_IMAGE_DIMENSION}"
        )


def test_ollama_unavailable_friendly_message(monkeypatch):
    """Ollama 不可用时给出自然语言提示，而不是原始报错。"""
    from plos.ai.ollama_client import OllamaClient
    from plos.ui.interactions import friendly_error_message

    client = OllamaClient(host="http://127.0.0.1:1")  # 必然连不上的端口
    assert client.is_available() is False

    friendly = friendly_error_message("Connection refused while connecting to 127.0.0.1:1")
    assert "连接不上服务" in friendly or "网络" in friendly
    assert "Traceback" not in friendly and "ConnectionError" not in friendly


def test_plugin_manager_discovers_builtin(qa_env):
    """插件管理页依赖的发现/注册流程可用，内置插件数量正常。"""
    from plos.plugins import PluginManager

    manager = PluginManager(data_dir=qa_env["tmp"] / "plugin_home")
    discovered = manager.discover()
    builtin = [info for info in discovered if info.builtin]
    assert len(builtin) >= 14, f"内置插件数量异常：{len(builtin)}"
    assert all(info.name for info in builtin), "存在无名插件"


def test_ai_pool_is_serial_singleton():
    """AI 任务线程池必须是单例且默认串行（同一时间只执行 1 个 AI 任务）。"""
    from plos.ui.workers import get_ai_pool

    pool = get_ai_pool()
    assert pool.maxThreadCount() == 1, "AI 任务必须串行执行，避免并发打满本地模型"
    assert get_ai_pool(max_threads=8) is pool, "AI 线程池应为单例（参数只影响首次创建）"


def test_hardware_tier_nav_gating():
    """三档硬件 + 强制低配下的可选入口显隐规则。"""
    from plos.config.hardware import HardwareTier
    from plos.ui.main_window import _optional_nav_keys

    low = _optional_nav_keys(HardwareTier.LOW)
    assert low == set(), f"低配应隐藏全部可选入口，实际 {low}"

    mid = _optional_nav_keys(HardwareTier.MID)
    assert "knowledge_graph" in mid, "中配应可见知识图谱入口"
    assert "textbook_cache" in mid, "中配应可见教材缓存"
    assert "asr" not in mid, "中配语音默认关闭，需在设置中开启"

    mid_voice = _optional_nav_keys(HardwareTier.MID, voice_enabled=True)
    assert "asr" in mid_voice, "中配在设置中开启语音后应可见入口"

    high = _optional_nav_keys(HardwareTier.HIGH)
    assert {"knowledge_graph", "textbook_cache", "asr"} <= high, "高配应全部可见"

    # 强制低配总开关：无论内存多大都按低配处理
    from plos.config.hardware import HardwareInfo, get_hardware_tier

    hw = HardwareInfo(
        os_name="Windows",
        cpu_model="Test CPU",
        cpu_cores=16,
        ram_total_gb=64.0,
        gpu_name="RTX 4090",
        gpu_vram_gb=24.0,
        is_low_spec=False,
    )
    forced = get_hardware_tier(hw, force_low_spec=True)
    assert forced == HardwareTier.LOW
    assert _optional_nav_keys(forced) == set(), "强制低配应隐藏全部可选入口"


def test_knowledge_graph_mastery_rate_thresholds(qa_env):
    """配色阈值按掌握率：100% → strong(绿)、50% → normal(黄)、0% → weak(红)。"""
    db, users = qa_env["db"], qa_env["users"]
    errorbook = qa_env["errorbook"]
    graph_service = KnowledgeGraphService(db=db, user_service=users)
    uid = users.get_current_user_id()

    errorbook.add_error(question="熟练题", answer="x", knowledge_points=["熟练KP"], mastery_level=2)
    errorbook.add_error(question="一般题", answer="x", knowledge_points=["一般KP"], mastery_level=1)
    errorbook.add_error(question="薄弱题", answer="x", knowledge_points=["薄弱KP"], mastery_level=0)

    nodes = {n["id"]: n for n in graph_service.build_graph(user_id=uid)["nodes"]}
    assert nodes["熟练KP"]["mastery_rate"] == 100
    assert nodes["熟练KP"]["category"] == "strong"
    assert nodes["一般KP"]["mastery_rate"] == 50
    assert nodes["一般KP"]["category"] == "normal"
    assert nodes["薄弱KP"]["mastery_rate"] == 0
    assert nodes["薄弱KP"]["category"] == "weak"


def test_disk_full_maps_to_friendly_message():
    """磁盘写满（ENOSPC）必须转成自然语言提示，不暴露原始 errno。"""
    from plos.ui.interactions import friendly_error_message

    err = OSError(28, "No space left on device")
    msg = friendly_error_message(err)
    assert "磁盘空间不足" in msg, f"未映射为友好文案：{msg}"
    assert "errno" not in msg.lower() and "28" not in msg

    # 缓存上限等已有具体文案的提示不应被磁盘文案覆盖
    cache_msg = friendly_error_message("缓存空间不足：上限 50MB")
    assert "磁盘空间不足" not in cache_msg, "缓存上限文案被通用磁盘文案覆盖"


def test_splash_covers_cursor_screen(qapp):
    """启动遮罩铺满当前所在屏幕（多屏时取光标屏），任何边缘都不外露。"""
    from plos.ui.splash_screen import SplashScreen

    screen = SplashScreen._target_screen()
    assert screen is not None, "取不到目标屏幕"

    splash = SplashScreen()
    splash.start()
    try:
        assert splash.geometry() == screen.geometry(), "遮罩未铺满目标屏幕"
        assert splash.windowModality() == Qt.WindowModality.ApplicationModal
    finally:
        splash.close_now()


def test_errorbook_ai_buttons_are_single_flight(qapp, qa_env):
    """连点错题本的 AI 按钮时，同一时间只允许启动一个 AI 任务。"""
    from plos.services import FlashcardService
    from plos.ui import workers as workers_mod
    from plos.ui.errorbook_panel import ErrorBookPanel

    db, users = qa_env["db"], qa_env["users"]
    errorbook = qa_env["errorbook"]
    panel = ErrorBookPanel(errorbook, FlashcardService(db=db, user_service=users))
    err_id = errorbook.add_error(question="单飞测试题", answer="x")

    started: list = []
    original = workers_mod.ThreadPool.start_worker

    def _fake_start(worker):
        started.append(worker)

    workers_mod.ThreadPool.start_worker = _fake_start
    try:
        # 模拟真实用户连点 5 次「AI 自动打标」
        for _ in range(5):
            panel._auto_tag_error(err_id)
        assert len(started) == 1, f"连点触发了 {len(started)} 个并发 AI 任务"
        assert panel._ai_busy is True

        # 任务结束（成功或失败都会发 finished）后必须归还占用
        started[0].signals.finished.emit()
        assert panel._ai_busy is False, "任务结束后未恢复，按钮会一直不可点"

        for _ in range(3):
            panel._generate_variant_for(err_id)
        assert len(started) == 2, "释放后应能再次发起，且仍保持单飞"

        started[1].signals.finished.emit()
        for _ in range(3):
            panel._generate_mindmap_for(err_id)
        assert len(started) == 3
    finally:
        workers_mod.ThreadPool.start_worker = original
        panel.deleteLater()


def test_theme_switch_and_resize_keep_panels_renderable(qapp, qa_env):
    """深浅双主题 × 多种窗口尺寸下，主要面板均可渲染，且文字无 BIDI/零宽乱码。"""
    from PyQt6.QtWidgets import QLabel

    from plos.ai import ModelManager
    from plos.config import load_config
    from plos.services import ChatService, FlashcardService, SessionManager
    from plos.ui import ui_utils
    from plos.ui.chat_panel import ChatPanel
    from plos.ui.errorbook_panel import ErrorBookPanel
    from plos.ui.flashcard_panel import FlashcardPanel
    from plos.ui.theme_manager import ThemeManager

    db, users = qa_env["db"], qa_env["users"]
    errorbook = qa_env["errorbook"]
    model_manager = ModelManager(config=load_config())
    session_manager = SessionManager(db=db, user_service=users)
    flashcard = FlashcardService(db=db, user_service=users)
    chat_service = ChatService(
        model_manager=model_manager,
        session_manager=session_manager,
        rag_service=None,
        errorbook_service=errorbook,
        user_service=users,
    )

    panels = {
        "ErrorBookPanel": ErrorBookPanel(errorbook, flashcard),
        "FlashcardPanel": FlashcardPanel(flashcard),
        "ChatPanel": ChatPanel(chat_service, errorbook_service=errorbook, config={"chat": {}}),
    }

    theme_manager = ui_utils._theme_manager_ref or ThemeManager()
    sizes = [(700, 500), (1200, 800), (1600, 900)]
    problems: list[str] = []

    for theme in ("dark", "light"):
        theme_manager.set_theme(theme, qapp)
        theme_manager.apply(qapp)
        for name, panel in panels.items():
            for w, h in sizes:
                panel.resize(w, h)
                pixmap = panel.grab()
                if pixmap.isNull():
                    problems.append(f"{theme}/{name}@{w}x{h} 渲染为空")
                    continue
                image = pixmap.toImage()
                colors = {
                    image.pixelColor(x, y).rgb()
                    for x in range(0, image.width(), 37)
                    for y in range(0, image.height(), 37)
                }
                if len(colors) < 3:
                    problems.append(f"{theme}/{name}@{w}x{h} 疑似空白（仅 {len(colors)} 色）")
            # 文字乱码：不得含零宽/BIDI 控制字符
            for label in panel.findChildren(QLabel):
                for ch in label.text():
                    if ch in ui_utils._INVISIBLE_CONTROLS:
                        problems.append(f"{theme}/{name} 文本含不可见控制字符 {hex(ord(ch))}")
                        break
            panel.deleteLater()

    assert not problems, "；".join(problems)


def test_corrupt_database_raises_clear_error(tmp_path: Path):
    """数据库文件损坏时应抛出可识别的异常，供上层转成友好提示。"""
    from plos.ui.interactions import friendly_error_message

    broken = tmp_path / "broken.db"
    broken.write_bytes(b"SQLite format 3\x00" + b"\x00" * 64 + b"garbage" * 100)

    with pytest.raises(Exception):
        db = Database(db_path=broken)
        db.fetchone("SELECT COUNT(*) AS c FROM error_book")

    friendly = friendly_error_message("file is not a database")
    assert "损坏" in friendly, f"数据库损坏未映射为友好文案：{friendly}"
