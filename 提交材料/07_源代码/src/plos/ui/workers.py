"""后台工作线程，避免模型调用、OCR 等耗时操作阻塞 UI。

所有 worker 都通过信号与主线程通信，不直接操作界面控件。
"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter
from typing import Dict, List, Optional

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

from ..core.models import SearchResult
from ..services import (
    ASRService,
    ChatService,
    DiagramService,
    ErrorBookService,
    ExamService,
    LearningPackageService,
    MindMapService,
    OCRService,
    PracticeService,
    RAGService,
    StudyPlanService,
    TTSService,
    WebClipService,
)


class WorkerSignals(QObject):
    """通用 worker 信号。

    - meta: 附带元信息（如 duration_ms 耗时统计）
    - progress: 长任务的进度反馈 (percent, message)，与 meta 语义分离
    """

    result = pyqtSignal(object)
    meta = pyqtSignal(dict)
    progress = pyqtSignal(int, str)
    error = pyqtSignal(str)
    finished = pyqtSignal()


class CallableWorker(QRunnable):
    """通用后台任务：执行任意可调用对象，结果通过信号回传。

    用于把零散的耗时操作（网络探测、文件扫描、批量数据库查询等）
    移出 UI 主线程，避免打包后界面掉帧或鼠标转圈。
    """

    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self._fn = fn
        self._args = args
        self._kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as e:
            self.signals.error.emit(str(e))
        else:
            self.signals.result.emit(result)
        finally:
            self.signals.finished.emit()


class ChatWorker(QRunnable):
    """在后台线程执行 AI 对话请求。"""

    def __init__(
        self,
        chat_service: ChatService,
        conversation_id: int,
        text: str,
        use_rag: bool = False,
        use_context: bool = False,
        teaching_mode: str = "normal",
        no_direct_answer: bool = False,
        explanation_mode: str = "basic",
    ):
        super().__init__()
        self.chat_service = chat_service
        self.conversation_id = conversation_id
        self.text = text
        self.use_rag = use_rag
        self.use_context = use_context
        self.teaching_mode = teaching_mode
        self.no_direct_answer = no_direct_answer
        self.explanation_mode = explanation_mode
        self.signals = WorkerSignals()

    def run(self) -> None:
        start = perf_counter()
        try:
            response = self.chat_service.send_message(
                self.conversation_id,
                self.text,
                use_rag=self.use_rag,
                use_context=self.use_context,
                teaching_mode=self.teaching_mode,
                no_direct_answer=self.no_direct_answer,
                explanation_mode=self.explanation_mode,
            )
            if response is None:
                raise RuntimeError("模型返回为空")
            self.signals.result.emit(response.content)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.meta.emit({"duration_ms": int((perf_counter() - start) * 1000)})
            self.signals.finished.emit()


class ChatStreamSignals(QObject):
    """流式对话专用信号。"""

    chunk = pyqtSignal(str)
    done = pyqtSignal(str, int)  # 完整文本, 耗时 ms
    error = pyqtSignal(str)
    finished = pyqtSignal()


class ChatStreamWorker(QRunnable):
    """在后台线程驱动流式对话生成器。"""

    def __init__(self, stream_generator):
        super().__init__()
        self._gen = stream_generator
        self.signals = ChatStreamSignals()

    def run(self) -> None:
        try:
            for event in self._gen:
                etype = event.get("type")
                if etype == "chunk":
                    self.signals.chunk.emit(event["text"])
                elif etype == "done":
                    self.signals.done.emit(event.get("content", ""), int(event.get("duration_ms", 0)))
                elif etype == "error":
                    self.signals.error.emit(event.get("message", "未知错误"))
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()

class OCRWorker(QRunnable):
    """在后台线程执行 VL 图片 OCR 识别。"""

    def __init__(self, ocr_service: OCRService, image_path: Path):
        super().__init__()
        self.ocr_service = ocr_service
        self.image_path = image_path
        self.signals = WorkerSignals()

    def run(self) -> None:
        start = perf_counter()
        try:
            text = self.ocr_service.extract_text(self.image_path)
            self.signals.result.emit(text)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.meta.emit({"duration_ms": int((perf_counter() - start) * 1000)})
            self.signals.finished.emit()


class PhotoSolveWorker(QRunnable):
    """在后台线程执行 VL 图片解题。"""

    def __init__(self, ocr_service: OCRService, image_path: Path):
        super().__init__()
        self.ocr_service = ocr_service
        self.image_path = image_path
        self.signals = WorkerSignals()

    def run(self) -> None:
        start = perf_counter()
        try:
            answer = self.ocr_service.solve_image_question(self.image_path)
            self.signals.result.emit(answer)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.meta.emit({"duration_ms": int((perf_counter() - start) * 1000)})
            self.signals.finished.emit()


class BatchOCRWorker(QRunnable):
    """在后台线程执行批量试卷 OCR 识别与题目切分。"""

    def __init__(
        self,
        ocr_service: OCRService,
        image_paths: List[Path],
        split_questions: bool = True,
    ):
        super().__init__()
        self.ocr_service = ocr_service
        self.image_paths = image_paths
        self.split_questions = split_questions
        self.signals = WorkerSignals()

    def run(self) -> None:
        start = perf_counter()
        results = []
        try:
            total = len(self.image_paths)
            for index, path in enumerate(self.image_paths, start=1):
                self.signals.progress.emit(
                    int((index - 1) / total * 100), f"正在识别 {path.name}（{index}/{total}）"
                )
                try:
                    results.append(
                        self.ocr_service.recognize_image(
                            path, split_questions=self.split_questions
                        )
                    )
                except Exception as e:  # 单张失败不中断批次
                    results.append({"image_path": str(path), "error": str(e)})
            self.signals.progress.emit(100, f"识别完成（{total} 张）")
            self.signals.result.emit(results)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.meta.emit({"duration_ms": int((perf_counter() - start) * 1000)})
            self.signals.finished.emit()


class ExamPipelineWorker(QRunnable):
    """在后台线程执行卷子全流程流水线。"""

    def __init__(self, pipeline_service, image_paths: List[Path], subject: str = ""):
        super().__init__()
        self.pipeline_service = pipeline_service
        self.image_paths = image_paths
        self.subject = subject
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            summary = self.pipeline_service.run(
                self.image_paths,
                subject=self.subject,
                progress=lambda p, m: self.signals.progress.emit(p, m),
            )
            self.signals.result.emit(summary)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()

class RAGSearchWorker(QRunnable):
    """在后台线程执行知识库检索。"""

    def __init__(self, rag_service: RAGService, query: str, top_k: int = 4):
        super().__init__()
        self.rag_service = rag_service
        self.query = query
        self.top_k = top_k
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            results: List[SearchResult] = self.rag_service.search(self.query, self.top_k)
            self.signals.result.emit(results)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()


class RAGAddWorker(QRunnable):
    """在后台线程执行文档入库。"""

    def __init__(self, rag_service: RAGService, file_path: Path):
        super().__init__()
        self.rag_service = rag_service
        self.file_path = file_path
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            document_id = self.rag_service.add_document(self.file_path)
            self.signals.result.emit(document_id)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()


class AutoTagWorker(QRunnable):
    """在后台线程执行错题自动考点打标。"""

    def __init__(
        self,
        errorbook_service: ErrorBookService,
        error_id: Optional[int] = None,
        question: Optional[str] = None,
    ):
        super().__init__()
        self.errorbook_service = errorbook_service
        self.error_id = error_id
        self.question = question
        self.signals = WorkerSignals()

    def run(self) -> None:
        start = perf_counter()
        try:
            if self.error_id is not None:
                tags = self.errorbook_service.apply_auto_tag_to_error(self.error_id)
            elif self.question:
                tags = self.errorbook_service.auto_tag(self.question)
            else:
                raise ValueError("必须提供 error_id 或 question")
            self.signals.result.emit(tags)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.meta.emit({"duration_ms": int((perf_counter() - start) * 1000)})
            self.signals.finished.emit()


class VariantQuestionWorker(QRunnable):
    """在后台线程执行 AI 变式题生成。"""

    def __init__(self, errorbook_service: ErrorBookService, error_id: int):
        super().__init__()
        self.errorbook_service = errorbook_service
        self.error_id = error_id
        self.signals = WorkerSignals()

    def run(self) -> None:
        start = perf_counter()
        try:
            variant = self.errorbook_service.generate_variant_question(self.error_id)
            self.signals.result.emit(variant)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.meta.emit({"duration_ms": int((perf_counter() - start) * 1000)})
            self.signals.finished.emit()


class MindMapWorker(QRunnable):
    """在后台线程执行思维导图生成。"""

    def __init__(
        self,
        mindmap_service: MindMapService,
        title: str,
        content: str,
        target_type: str,
        target_id: int,
    ):
        super().__init__()
        self.mindmap_service = mindmap_service
        self.title = title
        self.content = content
        self.target_type = target_type
        self.target_id = target_id
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            graph = self.mindmap_service.generate_mindmap(
                self.title,
                self.content,
                self.target_type,
                self.target_id,
            )
            self.signals.result.emit(graph)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()


class StudyPlanGenerateWorker(QRunnable):
    """在后台线程执行学习计划生成。"""

    def __init__(
        self,
        study_plan_service: StudyPlanService,
        title: str,
        goal: str,
        weak_subjects: str,
        daily_minutes: int,
    ):
        super().__init__()
        self.study_plan_service = study_plan_service
        self.title = title
        self.goal = goal
        self.weak_subjects = weak_subjects
        self.daily_minutes = daily_minutes
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            plan_id = self.study_plan_service.create_plan(
                title=self.title,
                goal=self.goal,
                weak_subjects=self.weak_subjects,
                daily_minutes=self.daily_minutes,
            )
            self.signals.result.emit(plan_id)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()


class TTSWorker(QRunnable):
    """在后台线程执行离线 TTS 朗读，避免阻塞 UI。"""

    def __init__(self, tts_service: TTSService, text: str):
        super().__init__()
        self.tts_service = tts_service
        self.text = text
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            self.tts_service.speak(self.text)
            self.signals.result.emit(True)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()


class ASRWorker(QRunnable):
    """在后台线程执行语音转文字，避免阻塞 UI。"""

    def __init__(self, asr_service: ASRService, audio_bytes: bytes):
        super().__init__()
        self.asr_service = asr_service
        self.audio_bytes = audio_bytes
        self.signals = WorkerSignals()

    def run(self) -> None:
        start = perf_counter()
        try:
            text = self.asr_service.transcribe_audio(self.audio_bytes)
            self.signals.result.emit(text)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.meta.emit({"duration_ms": int((perf_counter() - start) * 1000)})
            self.signals.finished.emit()


class PracticeGenWorker(QRunnable):
    """在后台线程执行自动出题。"""

    def __init__(
        self,
        practice_service: PracticeService,
        knowledge_point: str,
        question_type: str,
        difficulty: Optional[int] = None,
    ):
        super().__init__()
        self.practice_service = practice_service
        self.knowledge_point = knowledge_point
        self.question_type = question_type
        self.difficulty = difficulty
        self.signals = WorkerSignals()

    def run(self) -> None:
        start = perf_counter()
        try:
            question = self.practice_service.generate_question(
                self.knowledge_point,
                question_type=self.question_type,
                difficulty=self.difficulty,
            )
            self.signals.result.emit(question)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.meta.emit({"duration_ms": int((perf_counter() - start) * 1000)})
            self.signals.finished.emit()


class PracticeJudgeWorker(QRunnable):
    """在后台线程执行练习判题。"""

    def __init__(
        self,
        practice_service: PracticeService,
        question_id: int,
        user_answer: str,
    ):
        super().__init__()
        self.practice_service = practice_service
        self.question_id = question_id
        self.user_answer = user_answer
        self.signals = WorkerSignals()

    def run(self) -> None:
        start = perf_counter()
        try:
            result = self.practice_service.submit_answer(
                self.question_id, self.user_answer
            )
            self.signals.result.emit(result)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.meta.emit({"duration_ms": int((perf_counter() - start) * 1000)})
            self.signals.finished.emit()


class ExamGenWorker(QRunnable):
    """在后台线程执行试卷生成。"""

    def __init__(
        self,
        exam_service: ExamService,
        title: str,
        knowledge_points: list,
        type_counts: dict,
        difficulty_min: int,
        difficulty_max: int,
    ):
        super().__init__()
        self.exam_service = exam_service
        self.title = title
        self.knowledge_points = knowledge_points
        self.type_counts = type_counts
        self.difficulty_min = difficulty_min
        self.difficulty_max = difficulty_max
        self.signals = WorkerSignals()

    def run(self) -> None:
        start = perf_counter()
        try:
            paper = self.exam_service.generate_paper(
                self.title,
                self.knowledge_points,
                self.type_counts,
                difficulty_min=self.difficulty_min,
                difficulty_max=self.difficulty_max,
            )
            self.signals.result.emit(paper)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.meta.emit({"duration_ms": int((perf_counter() - start) * 1000)})
            self.signals.finished.emit()


class WebClipWorker(QRunnable):
    """在后台线程执行网页剪藏并入库。"""

    def __init__(self, web_clip_service: WebClipService, url: str):
        super().__init__()
        self.web_clip_service = web_clip_service
        self.url = url
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            document_id = self.web_clip_service.clip_url(self.url)
            self.signals.result.emit(document_id)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()


class DiagramWorker(QRunnable):
    """在后台线程执行 AI 示意图生成与渲染。"""

    def __init__(
        self,
        diagram_service: DiagramService,
        prompt: str,
        diagram_type: str = "flowchart",
        source_type: str = "",
        source_id: int = 0,
        dark: bool = False,
    ):
        super().__init__()
        self.diagram_service = diagram_service
        self.prompt = prompt
        self.diagram_type = diagram_type
        self.source_type = source_type
        self.source_id = source_id
        self.dark = dark
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            result = self.diagram_service.generate_diagram(
                self.prompt,
                diagram_type=self.diagram_type,
                source_type=self.source_type,
                source_id=self.source_id,
            )
            image_path = self.diagram_service.render_diagram(result["id"], dark=self.dark)
            self.signals.result.emit({"diagram": result, "image_path": str(image_path)})
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()


class PackageExportWorker(QRunnable):
    """在后台线程执行学习包导出。"""

    def __init__(
        self,
        service: LearningPackageService,
        output_path: Path,
        title: str,
        description: str,
        includes: Dict[str, bool],
    ):
        super().__init__()
        self.service = service
        self.output_path = Path(output_path)
        self.title = title
        self.description = description
        self.includes = includes
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            def _progress(percent: int, message: str) -> None:
                self.signals.progress.emit(percent, message)

            path = self.service.export_package(
                self.output_path,
                title=self.title,
                description=self.description,
                includes=self.includes,
                progress_callback=_progress,
            )
            self.signals.result.emit(str(path))
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()


class PackageImportWorker(QRunnable):
    """在后台线程执行学习包导入。"""

    def __init__(
        self,
        service: LearningPackageService,
        file_path: Path,
        selected: Optional[Dict[str, bool]] = None,
        conflict_strategy: str = "skip",
    ):
        super().__init__()
        self.service = service
        self.file_path = Path(file_path)
        self.selected = selected
        self.conflict_strategy = conflict_strategy
        self.signals = WorkerSignals()

    def run(self) -> None:
        try:
            def _progress(percent: int, message: str) -> None:
                self.signals.progress.emit(percent, message)

            counts = self.service.import_package(
                self.file_path,
                selected=self.selected,
                conflict_strategy=self.conflict_strategy,
                progress_callback=_progress,
            )
            self.signals.result.emit(counts)
        except Exception as e:
            self.signals.error.emit(str(e))
        finally:
            self.signals.finished.emit()


class ThreadPool:
    """简单的线程池封装。"""

    _instance = None
    # 持有运行中 worker 的 Python 引用：QRunnable 入队后若被 GC 回收，
    # 运行时会触发 "wrapped C/C++ object has been deleted" 随机崩溃
    _active_workers: set = set()

    @classmethod
    def instance(cls) -> QThreadPool:
        if cls._instance is None:
            cls._instance = QThreadPool.globalInstance()
        return cls._instance

    @classmethod
    def start_worker(cls, worker: QRunnable) -> None:
        """启动 worker 并保活，worker 发出 finished 信号后自动释放引用。"""
        cls._active_workers.add(worker)
        signals = getattr(worker, "signals", None)
        if signals is not None and hasattr(signals, "finished"):
            signals.finished.connect(lambda: cls._active_workers.discard(worker))
        else:
            # 所有自定义 worker 都必须在 finally 中发 finished；
            # 没有该信号的任务不允许进入保活池，直接启动并丢弃引用
            cls._active_workers.discard(worker)
        cls.instance().start(worker)


_ai_pool: Optional[QThreadPool] = None


def get_ai_pool(max_threads: int = 1) -> QThreadPool:
    """获取专用于 AI 任务的独立线程池。

    与全局 ThreadPool 隔离，且默认**串行执行（并发上限 1）**：
    本地 Ollama 通常只有一个模型实例，并发请求只会互相排队甚至超时，
    因此批量导入（如 50 题自动打标）也逐个执行。
    """
    global _ai_pool
    if _ai_pool is None:
        _ai_pool = QThreadPool()
        _ai_pool.setMaxThreadCount(max(1, max_threads))
    return _ai_pool
