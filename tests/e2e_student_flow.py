"""普通学生「一天使用」全流程端到端测试。

设计思路：模拟**刚装好 PLOS AI 的新学生**，从建用户开始，把一天里真正会做的
事按顺序跑一遍：

    建账号 → 导入讲义 → 知识库问答 → 拍照识题 → 错题入库 → AI 打标
    → 一键格式化 → 导出练习卷 → 组卷 → 闪卡复习 → 自动出题判分
    → 学习计划 → 术语词典 → 学习统计 → 备份/还原 → 切换用户 → 演示数据
    → 界面冒烟（双主题渲染）

为了不污染开发机上的 data/config/logs，脚本会把整个数据根目录指向一个沙箱
（%TEMP%/plos_e2e_student），相当于全新安装后的首次运行——首启类 Bug 才能暴露。

运行：
    cd plos_ai
    .venv\\Scripts\\python.exe -m tests.e2e_student_flow            # 全部（会调本地大模型）
    .venv\\Scripts\\python.exe -m tests.e2e_student_flow --local    # 只跑不依赖大模型的步骤
    .venv\\Scripts\\python.exe -m tests.e2e_student_flow --no-ui    # 跳过界面冒烟阶段
    .venv\\Scripts\\python.exe -m tests.e2e_student_flow --keep     # 保留上次沙箱数据

每次运行都会清空沙箱重建（等价于全新安装），退出码非 0 表示有检查项失败，
每一处失败都会打印完整堆栈，方便直接定位。
"""

from __future__ import annotations

import argparse
import io
import shutil
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# ----------------------------------------------------------------------
# 沙箱：必须在导入任何 plos 业务模块之前改掉"项目根目录"
# ----------------------------------------------------------------------

import plos.utils.paths as _paths  # noqa: E402

SANDBOX = Path(
    str(Path(tempfile.gettempdir()) / "plos_e2e_student")
).resolve()
_paths._project_root = lambda: SANDBOX  # type: ignore[assignment]


def _ensure_utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        if stream.encoding and stream.encoding.lower() != "utf-8":
            buffer = stream.buffer
            wrapper = io.TextIOWrapper(buffer, encoding="utf-8")
            if stream is sys.stdout:
                sys.stdout = wrapper
            else:
                sys.stderr = wrapper


class Report:
    """收集每一步的结果，最后统一汇报：一处失败不影响后续步骤继续跑。"""

    def __init__(self) -> None:
        self.passed: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.skipped: list[str] = []

    def run(self, name: str, fn, *, skip: bool = False) -> object:
        if skip:
            print(f"  [跳过] {name}")
            self.skipped.append(name)
            return None
        started = time.time()
        try:
            result = fn()
        except Exception:
            detail = traceback.format_exc()
            print(f"  [失败] {name}  ({time.time() - started:.1f}s)")
            print("         " + detail.strip().replace("\n", "\n         "))
            self.failed.append((name, detail))
            return None
        print(f"  [通过] {name}  ({time.time() - started:.1f}s)")
        self.passed.append(name)
        return result

    def summary(self) -> int:
        print("\n" + "=" * 68)
        print(f"通过 {len(self.passed)} 项 / 失败 {len(self.failed)} 项 / 跳过 {len(self.skipped)} 项")
        if self.failed:
            print("\n失败清单：")
            for name, _ in self.failed:
                print(f"  - {name}")
        print("=" * 68)
        return 1 if self.failed else 0


def _make_exam_image(path: Path) -> Path:
    """生成一张"学生拍下来的题目照片"：印刷体数学题 + 蓝色笔迹圈画。"""
    from PIL import Image, ImageDraw, ImageFont

    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1000, 420), (252, 250, 244))
    draw = ImageDraw.Draw(image)

    def _font(size: int):
        for name in ("simsun.ttc", "msyh.ttc", "simhei.ttf"):
            try:
                return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)
            except Exception:
                continue
        return ImageFont.load_default()

    title_font = _font(26)
    body_font = _font(24)
    draw.text((30, 24), "3. 已知函数 f(x)=x^2-4x+3，求它在区间 [0,3] 上的最小值。", fill=(20, 20, 20), font=title_font)
    draw.text((30, 74), "解：f(x)=(x-2)^2-1，顶点横坐标为 x=2。", fill=(20, 20, 20), font=body_font)
    draw.text((30, 116), "因为 2 在区间 [0,3] 内，所以最小值为 f(2)=-1。", fill=(20, 20, 20), font=body_font)
    draw.text((30, 168), "答：最小值是 -1，此时 x=2。", fill=(20, 20, 20), font=body_font)
    # 蓝色笔迹：学生圈出的顶点
    draw.ellipse((196, 66, 268, 112), outline=(40, 90, 210), width=3)
    image.save(path)
    return path


def _make_handout(path: Path) -> Path:
    """生成一份"老师发的讲义"，含公式标记，用来验证导入与检索。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "高一数学讲义 · 二次函数\n\n"
        "一、顶点式\n"
        "二次函数的一般式是 y=ax^2+bx+c（a≠0），配方后写成顶点式：\n"
        "\\(y=a(x-h)^2+k\\)，其中 h=-b/(2a)，k=(4ac-b^2)/(4a)。\n"
        "顶点坐标就是 (h, k)，对称轴是直线 x=h。\n\n"
        "二、最值\n"
        "当 a>0 时开口向上，函数有最小值 k；\n"
        "当 a<0 时开口向下，函数有最大值 k。\n"
        "在闭区间 [m,n] 上求最值，要先看对称轴是否落在区间内。\n\n"
        "三、例题\n"
        "例：求 \\(f(x)=x^2-4x+3\\) 在 [0,3] 上的最小值。\n"
        "解：配方得 f(x)=(x-2)^2-1，对称轴 x=2 落在区间内，故最小值为 -1。\n",
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--local", action="store_true", help="跳过所有需要大模型的步骤")
    parser.add_argument("--keep", action="store_true", help="保留上次沙箱数据")
    parser.add_argument("--no-ui", action="store_true", help="跳过界面冒烟阶段")
    args = parser.parse_args()

    _ensure_utf8_stdout()

    if SANDBOX.exists() and not args.keep:
        shutil.rmtree(SANDBOX, ignore_errors=True)
    SANDBOX.mkdir(parents=True, exist_ok=True)

    print("=" * 68)
    print("PLOS AI · 学生全流程端到端测试")
    print(f"沙箱数据目录：{SANDBOX}")
    print(f"模式：{'仅本地（不调用大模型）' if args.local else '完整（调用本地大模型）'}")
    print("=" * 68)

    from plos.ai import ModelManager
    from plos.config import detect_hardware, load_config, save_config
    from plos.core.enums import FlashcardRating
    from plos.db import get_db
    from plos.services import (
        BackupService,
        ChatService,
        DemoService,
        ErrorBookService,
        ErrorPaperService,
        ExamService,
        FlashcardService,
        ImageEnhanceService,
        KnowledgeGraphService,
        OCRService,
        PracticeService,
        RAGService,
        ReviewService,
        SessionManager,
        StatisticsService,
        StudyPlanService,
        TerminologyService,
        TextFormatService,
        UserService,
        get_text_format_service,
    )
    from plos.services.image_enhance_service import EnhanceOptions
    from plos.utils.logger import setup_logger

    setup_logger()
    report = Report()
    local_only = args.local

    # ---------------- 阶段 1：环境与服务装配 ----------------

    print("\n[阶段 1] 环境与服务装配")

    config = report.run("加载配置（全新安装走默认值）", load_config)
    hardware = report.run("硬件探测", detect_hardware)
    db = report.run("初始化数据库（自动建表）", get_db)

    def _resolve_first_run_models():
        """模拟「启动引导」：按硬件档次 + 已安装模型，把真实模型名写进配置。

        真实首启会走 StartupDialog → resolve_ollama_models，把机器上**确实存在**的
        模型名落到 config；测试里照做，否则测的不是学生真正会跑的那套模型。
        """
        from plos.ai.ollama_client import OllamaClient
        from plos.utils.startup_probe import resolve_ollama_models

        client = OllamaClient(config.get("backend", {}).get("ollama_host"), timeout=5)
        if not client.is_available():
            print("         Ollama 未连接：保持配置里的模型名（学生此时看不到 AI 能力）")
            return None
        installed = client.list_models()
        resolved = resolve_ollama_models(installed, hardware)
        models = config.setdefault("models", {})
        models["text_model"] = resolved["text_model"]
        if resolved.get("vision_model"):
            models["vision_model"] = resolved["vision_model"]
        config["model_profile"] = resolved.get("profile", "auto")
        save_config(config)
        print(
            f"         硬件推荐套装={config['model_profile']}｜"
            f"文本={models['text_model']}｜视觉={models.get('vision_model')}｜"
            f"已装模型 {len(installed)} 个"
        )
        return resolved

    report.run("首启模型套装解析（硬件 + 已安装模型）", _resolve_first_run_models)
    manager = report.run("初始化模型管理器", lambda: ModelManager(config, hardware=hardware))

    def _model_status():
        assert manager is not None
        available = {
            "文本": manager.is_text_available(),
            "视觉": manager.is_vision_available(),
            "向量": manager.is_embedding_available(),
        }
        print(f"         模型可用性：{available}")
        return available

    report.run("模型可用性探测", _model_status)

    user_service = UserService(db)
    statistics_service = StatisticsService(db, user_service=user_service)
    errorbook_service = ErrorBookService(
        db, user_service=user_service, model_manager=manager
    )
    flashcard_service = FlashcardService(db, user_service=user_service)
    flashcard_service.statistics_service = statistics_service
    study_plan_service = StudyPlanService(
        # --local 模式强制走本地任务模板，避免误触发大模型
        model_manager=(None if local_only else manager),
        flashcard_service=flashcard_service,
        errorbook_service=errorbook_service,
        db=db,
        user_service=user_service,
    )
    errorbook_service.set_study_plan_service(study_plan_service)
    rag_service = RAGService(manager, db=db, user_service=user_service)
    session_manager = SessionManager(db=db, user_service=user_service)
    chat_service = ChatService(
        manager,
        session_manager,
        rag_service=rag_service,
        errorbook_service=errorbook_service,
        user_service=user_service,
    )
    ocr_service = OCRService(manager, db=db, user_service=user_service)
    practice_service = PracticeService(
        db, user_service=user_service, model_manager=manager,
        errorbook_service=errorbook_service,
    )
    exam_service = ExamService(db, user_service=user_service, model_manager=manager)
    paper_service = ErrorPaperService(
        db, errorbook_service=errorbook_service, user_service=user_service
    )
    terminology_service = TerminologyService(
        db, user_service=user_service, model_manager=manager
    )
    review_service = ReviewService(db, user_service=user_service)
    image_service = ImageEnhanceService()
    backup_service = BackupService(
        db,
        user_service=user_service,
        errorbook_service=errorbook_service,
        flashcard_service=flashcard_service,
    )

    # ---------------- 阶段 2：新学生建档 ----------------

    print("\n[阶段 2] 新学生建档")

    def _create_user():
        user_id = user_service.create_user("xiaoming", nickname="小明")
        user_service.set_current_user(user_id)
        current = user_service.get_current_user()
        assert current and current["id"] == user_id, f"当前用户异常：{current}"
        print(f"         当前用户：{current['nickname']}（id={user_id}）")
        return user_id

    user_id = report.run("新建学生账号并切换为当前用户", _create_user)

    def _first_run_state():
        """全新安装首次运行：错题/闪卡/计划/文档都该是空的。"""
        assert user_id is not None
        assert errorbook_service.list_errors(user_id=user_id) == []
        assert flashcard_service.list_cards(user_id=user_id) == []
        assert rag_service.list_documents(user_id=user_id) == []
        assert study_plan_service.list_plans(user_id=user_id) == []
        return True

    report.run("首启状态干净（无残留数据）", _first_run_state)

    # ---------------- 阶段 3：拍错题 → 图片增强 → 识别 ----------------

    print("\n[阶段 3] 拍照识题链路")

    photo = _make_exam_image(SANDBOX / "素材" / "数学错题.png")
    handout = _make_handout(SANDBOX / "素材" / "二次函数讲义.md")
    print(f"         素材就绪：{photo.name} / {handout.name}")

    def _enhance():
        result = image_service.enhance(
            photo,
            EnhanceOptions(auto_clear=True, remove_pen=True, pen_color="blue", strength="mid"),
        )
        assert result.ok, f"图片增强失败：{result.error}"
        assert result.output_path and result.output_path.exists()
        print(f"         增强完成：{result.output_path.name}，应用了 {result.applied}")
        return result

    enhance_result = report.run("拍照后图片增强（去笔迹 + 清晰化）", _enhance)
    enhanced_path = (
        enhance_result.output_path if enhance_result is not None and enhance_result.ok else photo
    )

    ocr_text_holder: dict[str, str] = {}

    def _ocr_single():
        assert ocr_service.is_image_service_available(), ocr_service.get_status_message()
        text = ocr_service.extract_text(enhanced_path)
        assert text and text.strip(), "识别结果为空"
        print(f"         识别文本：{text.strip()[:60]}...")
        ocr_text_holder["text"] = text
        return text

    report.run("单张识别（VL 模型）", _ocr_single, skip=local_only)

    batched_holder: dict[str, list] = {}

    def _ocr_batch():
        results = ocr_service.batch_recognize([enhanced_path], split_questions=True, user_id=user_id)
        assert results, "批量识别没有返回结果"
        first = results[0]
        assert not first.get("error"), f"批量识别报错：{first.get('error')}"
        questions = first.get("questions") or []
        assert questions, "没有切分出题目"
        q = questions[0]
        assert q.get("question"), "题目内容为空"
        print(f"         切出 {len(questions)} 道题，第一题：{q['question'][:40]}...")
        batched_holder["questions"] = questions
        return questions

    report.run("批量识别 + 自动切题", _ocr_batch, skip=local_only)

    # ---------------- 阶段 4：一键格式化与错题入库 ----------------

    print("\n[阶段 4] 错题入库与打标")

    def _format_text():
        service = get_text_format_service()
        source = "已知 f(x)=x^2-4x+3，求 f(x)<=0 的解集，答案是 1<=x<=3。"
        formatted = service.format_text(source)
        assert "\\(" in formatted, f"算式没有被补上公式标记：{formatted}"
        again = service.format_text(formatted)
        assert again == formatted, "格式化不幂等，重复点击会产生差异"
        print(f"         格式化结果：{formatted}")
        return formatted

    report.run("一键格式化文本（补公式标记 + 幂等）", _format_text)

    raw_question = (
        batched_holder.get("questions", [{}])[0].get("question")
        if batched_holder.get("questions")
        else "已知函数 f(x)=x^2-4x+3，求它在区间 [0,3] 上的最小值。"
    )
    error_id_holder: dict[str, int] = {}

    def _add_error():
        error_id = errorbook_service.add_error(
            question=raw_question,
            answer="最小值为 -1，此时 x=2。",
            analysis="配方得 f(x)=(x-2)^2-1，对称轴 x=2 落在 [0,3] 内。",
            knowledge_points=["二次函数最值"],
            question_type="解答题",
            subject="数学",
            chapter="二次函数",
            knowledge_point="二次函数最值",
            difficulty=3,
            image_path=str(enhanced_path),
            user_id=user_id,
        )
        row = errorbook_service.get_error(error_id, user_id=user_id)
        assert row and row["question"], "错题写入后读不回来"
        error_id_holder["id"] = error_id
        return error_id

    error_id = report.run("新增错题（带图片与知识点）", _add_error)

    def _format_service_on_error():
        """错题入库清洗不能把公式标记删掉，否则后续渲染与导出全失效。"""
        assert error_id is not None
        row = errorbook_service.get_error(error_id, user_id=user_id)
        assert row is not None
        formatted = TextFormatService().format_text(row["question"])
        assert "\n\n\n" not in formatted
        return formatted

    report.run("入库文本清洗不影响公式", _format_service_on_error)

    def _auto_tag():
        assert error_id is not None
        tags = errorbook_service.apply_auto_tag_to_error(error_id, user_id=user_id)
        row = errorbook_service.get_error(error_id, user_id=user_id)
        assert row is not None
        print(f"         AI 打标：知识点={row.get('knowledge_point')!r} 题型={row.get('question_type')!r} 难度={row.get('difficulty')}")
        assert row.get("knowledge_point") or row.get("knowledge_points"), f"打标结果为空：{tags}"
        return tags

    report.run("AI 自动打标并写回错题", _auto_tag, skip=local_only)

    def _mastery_and_weak():
        assert error_id is not None
        errorbook_service.set_mastery(error_id, 0, user_id=user_id)
        errorbook_service.set_mastery(error_id, 1, user_id=user_id)
        weak = errorbook_service.get_weak_knowledge_points(top_n=5, user_id=user_id)
        assert isinstance(weak, list)
        print(f"         薄弱知识点：{weak}")
        return weak

    report.run("标记掌握度 + 薄弱知识点统计", _mastery_and_weak)

    # ---------------- 阶段 5：讲义导入与知识库问答 ----------------

    print("\n[阶段 5] 讲义导入与知识库问答")

    doc_holder: dict[str, int] = {}

    def _add_document():
        assert manager is not None and manager.is_embedding_available(), "缺少向量模型 nomic-embed-text"
        doc_id = rag_service.add_document(handout, user_id=user_id)
        assert doc_id > 0
        chunk_count = db.fetchone(
            "SELECT COUNT(*) AS c FROM chunks WHERE document_id = ?", (doc_id,)
        )
        assert chunk_count and chunk_count["c"] > 0, "文档没有切片入库"
        print(f"         文档 id={doc_id}，切片 {chunk_count['c']} 段")
        doc_holder["id"] = doc_id
        return doc_id

    report.run("导入讲义（解析 + 切片 + 向量化）", _add_document, skip=local_only)

    def _search():
        hits = rag_service.search("二次函数在闭区间上的最值怎么求", top_k=3)
        assert hits, "检索没有命中任何片段"
        for hit in hits:
            print(f"         命中 {hit.source} (score={hit.score:.3f})：{hit.content[:40]}...")
        assert any("最值" in hit.content or "顶点" in hit.content for hit in hits), "命中的内容与问题无关"
        return hits

    report.run("知识库语义检索", _search, skip=local_only)

    def _chat_rag():
        conv_id = chat_service.create_conversation("二次函数答疑", user_id=user_id)
        response = chat_service.send_message(
            conv_id, "结合我的讲义，说说二次函数在闭区间上求最小值的步骤。",
            use_rag=True, user_id=user_id,
        )
        assert response.content and response.content.strip(), "AI 回答为空"
        print(f"         AI：{response.content.strip()[:80]}...")
        messages = session_manager.get_messages(conv_id, user_id=user_id)
        assert len(messages) >= 2, "会话历史没有落库"
        return response

    report.run("知识库增强问答（RAG 对话）", _chat_rag, skip=local_only)

    def _chat_stream():
        conv_id = chat_service.create_conversation("流式对话", user_id=user_id)
        chunks: list[str] = []
        final = ""
        for event in chat_service.send_message_stream(
            conv_id, "用一句话说明顶点式的好处。", user_id=user_id
        ):
            if event["type"] == "chunk":
                chunks.append(event["text"])
            elif event["type"] == "done":
                final = event["content"]
            elif event["type"] == "error":
                raise AssertionError(f"流式对话报错：{event['message']}")
        assert final.strip(), "流式对话没有最终内容"
        print(f"         流式收字 {len(''.join(chunks))} 个，最终回答：{final.strip()[:50]}...")
        return final

    report.run("流式对话（打字机输出）", _chat_stream, skip=local_only)

    # ---------------- 阶段 6：导出练习卷与组卷 ----------------

    print("\n[阶段 6] 导出与组卷")

    export_dir = SANDBOX / "导出"
    export_dir.mkdir(parents=True, exist_ok=True)

    def _export_error_paper():
        errors = paper_service.select_errors(scope="all", user_id=user_id)
        assert errors, "没有选中任何错题"
        paths = []
        for fmt in ("docx", "pdf"):
            out = paper_service.export(
                errors,
                export_dir / f"错题练习卷.{fmt}",
                fmt=fmt,
                options={"with_answers": True, "with_wrong_answer": True, "blank_lines": 6},
                user_id=user_id,
            )
            assert out.exists() and out.stat().st_size > 1024, f"{fmt} 导出文件异常：{out}"
            paths.append(out)
        sizes = {p.suffix: p.stat().st_size // 1024 for p in paths}
        print(f"         导出：{[p.name for p in paths]}（{sizes} KB）")
        # 回归：嵌入字体必须子集化，否则单页卷就会膨胀到 18MB
        assert sizes[".pdf"] < 2048, f"PDF 体积异常（字体可能没做子集化）：{sizes['.pdf']}KB"
        return paths

    report.run("错题练习卷导出（Word + PDF）", _export_error_paper)

    exam_holder: dict[str, int] = {}

    def _generate_exam():
        paper = exam_service.generate_paper(
            "二次函数单元小测",
            ["二次函数最值"],
            {"choice": 2, "fill": 1, "judge": 1},
            user_id=user_id,
        )
        questions = paper.get("questions") or []
        assert len(questions) >= 3, f"生成题目过少：{len(questions)}"
        print(f"         生成 {len(questions)} 道题，总分 {paper.get('total_score')}")
        exam_holder["id"] = paper["id"]
        return paper

    report.run("AI 组卷（按知识点 + 题型配比）", _generate_exam, skip=local_only)

    def _export_exam():
        assert exam_holder.get("id"), "没有可导出的试卷"
        for fmt in ("docx", "pdf"):
            paths = exam_service.export_paper(exam_holder["id"], export_dir, fmt=fmt, user_id=user_id)
            assert paths, f"{fmt} 组卷导出没有返回文件"
            for path in paths:
                assert path.exists() and path.stat().st_size > 1024, f"导出文件异常：{path}"
            if fmt == "pdf":
                size_kb = paths[0].stat().st_size // 1024
                assert size_kb < 2048, f"试卷 PDF 体积异常：{size_kb}KB"
            print(f"         {fmt} 导出：{[p.name for p in paths]}")
        return True

    report.run("试卷导出（试卷 + 参考答案，Word/PDF）", _export_exam, skip=local_only)

    # ---------------- 阶段 7：闪卡复习 ----------------

    print("\n[阶段 7] 错题转闪卡与复习")

    card_holder: dict[str, int] = {}

    def _add_card():
        assert error_id is not None
        row = errorbook_service.get_error(error_id, user_id=user_id)
        assert row is not None
        card_id = flashcard_service.add_card(
            front_content=row["question"],
            back_content=f"答案：{row.get('answer', '')}\n解析：{row.get('analysis', '')}",
            subject=row.get("subject", "") or "数学",
            tags=row.get("knowledge_point", "") or "",
            user_id=user_id,
        )
        card_holder["id"] = card_id
        assert flashcard_service.get_stats(user_id=user_id)["total"] >= 1
        return card_id

    report.run("错题加入闪卡", _add_card)

    def _review_card():
        assert card_holder.get("id"), "没有闪卡可复习"
        due = flashcard_service.get_due_cards(limit=50, user_id=user_id)
        assert due, "新卡应该立刻进入待复习队列"
        result = flashcard_service.review_card(
            card_holder["id"], FlashcardRating.GOOD, user_id=user_id
        )
        assert result["interval"] >= 1
        print(f"         复习结果：间隔 {result['interval']} 天，易度 {result['ease']:.2f}")
        assert flashcard_service.get_stats(user_id=user_id)["due_today"] == 0, "评分后不该仍是待复习"
        return result

    report.run("闪卡复习（SM-2 评分）", _review_card)

    # ---------------- 阶段 8：自动出题与判分 ----------------

    print("\n[阶段 8] 自动出题与判分")

    practice_holder: dict[str, dict] = {}

    def _generate_question():
        question = practice_service.generate_question(
            "二次函数最值", question_type="choice", user_id=user_id
        )
        assert question.get("question"), "题目内容为空"
        assert question.get("answer"), "缺少标准答案"
        print(f"         题目：{question['question'][:50]}...")
        practice_holder["question"] = question
        return question

    report.run("按知识点自动出题", _generate_question, skip=local_only)

    def _submit_answer():
        question = practice_holder.get("question")
        if not question:
            raise AssertionError("没有可作答的题目")
        result = practice_service.submit_answer(
            question["id"], str(question.get("answer") or "A"), user_id=user_id
        )
        assert result["is_correct"] is True, f"提交标准答案却判错：{result}"
        print(f"         判分：{result['score']} 分，下一题建议难度 {result['next_difficulty']}")
        return result

    report.run("提交作答并判分（自适应难度）", _submit_answer, skip=local_only)

    def _practice_stats():
        stats = practice_service.get_stats(user_id=user_id)
        records = practice_service.list_records(limit=20, user_id=user_id)
        print(f"         练习统计：{stats}")
        assert stats["total"] >= 1 and records, "练习记录没有落库"
        question = practice_holder.get("question")
        if question:
            review_service.schedule_review(
                "practice", question["id"], True, user_id=user_id
            )
            print(f"         复习队列：{review_service.get_review_stats(user_id=user_id)}")
        return stats

    report.run("练习统计与复习计划落库", _practice_stats, skip=local_only)

    # ---------------- 阶段 9：学习计划 ----------------

    print("\n[阶段 9] 学习计划")

    plan_holder: dict[str, int] = {}

    def _create_plan():
        plan_id = study_plan_service.create_plan(
            "本周数学冲刺", "拿下二次函数最值", "数学", 60, user_id=user_id
        )
        plan = study_plan_service.get_plan(plan_id, user_id=user_id)
        assert plan and plan.get("tasks"), "计划没有生成任务"
        print(f"         计划 id={plan_id}，任务 {len(plan['tasks'])} 条：{[t['title'] for t in plan['tasks']][:3]}...")
        plan_holder["id"] = plan_id
        return plan_id

    report.run("生成一周学习计划（有模型走 AI，无模型本地降级）", _create_plan)

    def _toggle_task():
        assert plan_holder.get("id"), "没有计划"
        tasks = study_plan_service.list_tasks(plan_holder["id"], user_id=user_id)
        assert tasks
        study_plan_service.toggle_task_complete(tasks[0]["id"], True, user_id=user_id)
        refreshed = study_plan_service.get_plan(plan_holder["id"], user_id=user_id)
        assert refreshed is not None
        assert any(t["is_completed"] for t in refreshed["tasks"]), "任务完成状态没有保存"
        print(f"         完成 1 条任务后进度：{refreshed.get('progress')}%")
        return True

    report.run("勾选任务完成并刷新进度", _toggle_task)

    def _check_adjustment():
        """计划已经在跑的情况下又遇到新错题，系统应自动插一条针对性复习任务。"""
        assert plan_holder.get("id")
        before = len(study_plan_service.list_tasks(plan_holder["id"], user_id=user_id))
        extra_error = errorbook_service.add_error(
            question="已知 \\(f(x)=x^2-2x\\)，求它在 [0,3] 上的值域。",
            answer="值域为 [-1,3]。",
            analysis="配方得 f(x)=(x-1)^2-1，端点代入得 3。",
            subject="数学",
            chapter="二次函数",
            knowledge_point="二次函数值域",
            question_type="解答题",
            user_id=user_id,
        )
        after = len(study_plan_service.list_tasks(plan_holder["id"], user_id=user_id))
        adjustments = study_plan_service.list_adjustments(plan_holder["id"], user_id=user_id)
        print(f"         新增错题 #{extra_error} 后任务数 {before} → {after}，调优记录 {len(adjustments)} 条")
        assert after > before, "错题新增后计划没有插入复习任务"
        assert adjustments, "没有留下调优记录"
        return adjustments

    report.run("错题驱动计划调优（自动插入复习任务）", _check_adjustment)

    # ---------------- 阶段 10：术语词典 ----------------

    print("\n[阶段 10] 术语词典")

    def _extract_terms():
        source = "二次函数的一般式为 y=ax^2+bx+c，顶点式 y=a(x-h)^2+k 中 (h,k) 就是抛物线的顶点坐标。"
        terms = terminology_service.extract_terms(source, source_type="errorbook", user_id=user_id)
        assert terms, "没有提取到任何术语"
        print(f"         提取术语：{[t['term'] for t in terms][:5]}")
        first = terminology_service.get_term(terms[0]["id"], user_id=user_id)
        assert first is not None
        return terms

    report.run("从文本提取专业术语", _extract_terms, skip=local_only)

    # ---------------- 阶段 11：学习统计 ----------------

    print("\n[阶段 11] 学习统计")

    def _stats():
        statistics_service.record_study_duration(45, subject="数学", user_id=user_id)
        daily = statistics_service.get_daily_study_duration(days=7, user_id=user_id)
        weekly = statistics_service.get_weekly_report(user_id=user_id)
        streak = statistics_service.get_study_streak(user_id=user_id)
        summary = statistics_service.get_dashboard_summary(user_id=user_id)
        assert any(row["minutes"] > 0 for row in daily), f"学习时长没有记录：{daily}"
        assert summary["total_errors"] >= 1, f"错题汇总异常：{summary}"
        assert weekly["minutes"] >= 45
        print(f"         今日学习 {summary['today_minutes']} 分钟，连续 {streak} 天，错题 {summary['total_errors']} 道")
        return summary

    report.run("学习时长/周报/连续天数/看板汇总", _stats)

    def _subject_distribution():
        distribution = statistics_service.get_error_subject_distribution(user_id=user_id)
        accuracy = statistics_service.get_flashcard_accuracy(days=7, user_id=user_id)
        assert distribution, "错题学科分布为空"
        print(f"         错题分布：{distribution}；闪卡正确率：{accuracy.get('overall')}")
        return distribution

    report.run("错题学科分布 + 闪卡正确率", _subject_distribution)

    # ---------------- 阶段 12：备份与还原 ----------------

    print("\n[阶段 12] 备份与还原")

    backup_holder: dict[str, Path] = {}

    def _export_backup():
        target = export_dir / "学生数据备份.plosbackup"
        path = backup_service.export_full_backup(target, user_id=user_id)
        assert path.exists() and path.stat().st_size > 1024
        import zipfile

        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
        assert any("metadata.json" in n for n in names) and any("backup.json" in n for n in names)
        print(f"         备份文件 {path.name}（{path.stat().st_size // 1024}KB，{len(names)} 个条目）")
        backup_holder["path"] = path
        return path

    report.run("导出完整备份", _export_backup)

    def _auto_backup():
        path = backup_service.run_auto_backup()
        integrity_ok, message = backup_service.check_database_integrity()
        assert integrity_ok, f"数据库完整性检查失败：{message}"
        print(f"         自动备份：{path.name if path else '本次未触发（按频率策略）'}；完整性 {message}")
        return path

    report.run("自动滚动备份 + 数据库完整性检查", _auto_backup)

    def _restore_backup():
        assert backup_holder.get("path"), "没有备份文件"
        before = len(errorbook_service.list_errors(user_id=user_id))
        restored = backup_service.import_full_backup(backup_holder["path"])
        after = len(errorbook_service.list_errors(user_id=user_id))
        assert after == before, f"还原后错题数量不一致：{before} -> {after}"
        print(f"         还原 {restored} 行，错题数保持 {after} 道")
        return restored

    report.run("备份还原往返一致", _restore_backup)

    # ---------------- 阶段 13：多用户与演示数据 ----------------

    print("\n[阶段 13] 多用户与演示数据")

    def _switch_user():
        second = user_service.create_user("student2", nickname="小红")
        user_service.set_current_user(second)
        assert user_service.get_current_user()["id"] == second
        # 新用户视角应当是干净的，看不到小明的错题
        assert errorbook_service.list_errors(user_id=second) == [], "新用户看到了别人的错题"
        user_service.set_current_user(user_id)
        assert user_service.get_current_user()["id"] == user_id
        print(f"         已切换到小红（id={second}）并切回小明，数据互相隔离")
        return second

    report.run("新增第二用户并验证数据隔离", _switch_user)

    def _demo_data():
        second = user_service.create_user("demo_user", nickname="演示同学")
        demo = DemoService(db=db, user_service=user_service, model_manager=None)
        summary = demo.load_demo_data(user_id=second, reset=True)
        assert summary["errors"] > 0 and summary["flashcards"] > 0
        print(f"         演示数据：错题 {summary['errors']}、闪卡 {summary['flashcards']}、计划 {summary['plans']}")
        return summary

    report.run("一键加载演示数据（离线可用）", _demo_data)

    # ---------------- 阶段 14：界面冒烟（真实数据 + 双主题） ----------------

    print("\n[阶段 14] 界面冒烟（真实数据渲染 + 主题切换）")

    def _ui_smoke():
        import os

        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PyQt6.QtWidgets import QApplication, QWidget

        from plos.ui import ui_utils
        from plos.ui.asr_panel import ASRPanel
        from plos.ui.dashboard_panel import DashboardPanel
        from plos.ui.errorbook_panel import ErrorBookPanel
        from plos.ui.exam_panel import ExamPanel
        from plos.ui.favorites_panel import FavoritesPanel
        from plos.ui.flashcard_panel import FlashcardPanel
        from plos.ui.knowledge_graph_panel import KnowledgeGraphPanel
        from plos.ui.knowledge_panel import KnowledgePanel
        from plos.ui.ocr_panel import OCRPanel
        from plos.ui.practice_panel import PracticePanel
        from plos.ui.study_plan_panel import StudyPlanPanel
        from plos.ui.terminology_panel import TerminologyPanel
        from plos.ui.theme_manager import ThemeManager
        from plos.ui.toast import ToastManager
        from plos.ui.workers import ThreadPool

        app = QApplication.instance() or QApplication(["plos_e2e"])

        # 注册 Toast 宿主：否则 show_success 会退化成模态框，在离屏环境里直接卡死
        toast_host = QWidget()
        ui_utils.set_toast_manager(ToastManager(toast_host))

        theme_manager = ThemeManager()
        theme_manager.apply(app)
        ui_utils.set_theme_manager(theme_manager)

        graph_service = KnowledgeGraphService(db=db, user_service=user_service)
        panels = {
            "仪表盘": DashboardPanel(
                chat_service, errorbook_service, flashcard_service, rag_service,
                study_plan_service, statistics_service, theme_manager=theme_manager,
            ),
            "错题本": ErrorBookPanel(errorbook_service, flashcard_service),
            "试卷生成": ExamPanel(exam_service, errorbook_service),
            "闪卡": FlashcardPanel(flashcard_service),
            "知识库": KnowledgePanel(rag_service),
            "练习": PracticePanel(practice_service, errorbook_service),
            "学习计划": StudyPlanPanel(study_plan_service, flashcard_service),
            "术语词典": TerminologyPanel(terminology_service),
            "收藏夹": FavoritesPanel(errorbook_service, rag_service),
            "知识图谱": KnowledgeGraphPanel(graph_service),
            "OCR": OCRPanel(ocr_service, errorbook_service, terminology_service),
            "语音转文字": ASRPanel(),
        }
        rendered = []
        for name, panel in panels.items():
            panel.resize(1200, 800)
            for theme in ("light", "dark"):
                config.setdefault("ui", {})["theme"] = theme
                save_config(config)
                theme_manager.set_theme(theme, app)
                hook = getattr(panel, "on_theme_changed", None)
                if callable(hook):
                    hook()
                pixmap = panel.grab()
                assert not pixmap.isNull(), f"{name} 在 {theme} 主题下渲染失败"
            panel.deleteLater()
            rendered.append(name)

        deadline = time.time() + 20
        while ThreadPool._active_workers and time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
        app.processEvents()
        config.setdefault("ui", {})["theme"] = "light"
        save_config(config)
        theme_manager.set_theme("light", app)
        print(f"         渲染通过的面板：{rendered}")
        return rendered

    report.run("界面冒烟：12 个面板在浅色/深色下渲染", _ui_smoke, skip=args.no_ui)

    # ---------------- 收尾 ----------------

    def _log_scan():
        log_file = SANDBOX / "logs" / "plos.log"
        assert log_file.exists(), f"日志文件不存在：{log_file}"
        content = log_file.read_text(encoding="utf-8", errors="ignore")
        errors = [
            line for line in content.splitlines()
            if "| ERROR" in line or "Traceback" in line
        ]
        if errors:
            print("         日志中的错误行：")
            for line in errors[:10]:
                print(f"           {line[:150]}")
        assert not errors, f"运行期日志出现 {len(errors)} 条 ERROR"
        print(f"         日志 {log_file.stat().st_size // 1024}KB，无 ERROR/Traceback")
        return True

    report.run("运行期日志无 ERROR/Traceback", _log_scan)

    return report.summary()


if __name__ == "__main__":
    sys.exit(main())
