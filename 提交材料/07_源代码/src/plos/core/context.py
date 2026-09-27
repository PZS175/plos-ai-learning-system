"""应用上下文：集中构建并持有全部业务服务（轻量依赖注入容器）。

主窗口、预览工具与集成测试共用同一份服务装配逻辑，
避免服务构造顺序散落在 UI 层造成重复与漂移。
"""

from __future__ import annotations

from dataclasses import dataclass

from ..ai import ModelManager
from ..services import (
    ASRService,
    BackupService,
    ChatService,
    DiagnosticReportService,
    DiagramService,
    ErrorBookService,
    ExamService,
    FlashcardService,
    InputRouter,
    KnowledgeGraphService,
    LearningPackageService,
    MindMapService,
    NoteAnnotationService,
    OCRService,
    PracticeService,
    RAGService,
    SearchService,
    SessionManager,
    StatisticsService,
    StudyPlanService,
    TeachingStyleService,
    TerminologyService,
    TTSService,
    UserService,
    WebClipService,
)
from ..utils.logger import get_logger

logger = get_logger("core.context")


@dataclass
class AppContext:
    """业务服务容器：一次构建，处处复用。"""

    user_service: UserService
    tts_service: TTSService
    statistics_service: StatisticsService
    search_service: SearchService
    session_manager: SessionManager
    errorbook_service: ErrorBookService
    flashcard_service: FlashcardService
    practice_service: PracticeService
    exam_service: ExamService
    rag_service: RAGService
    teaching_style_service: TeachingStyleService
    chat_service: ChatService
    ocr_service: OCRService
    input_router: InputRouter
    study_plan_service: StudyPlanService
    note_annotation_service: NoteAnnotationService
    knowledge_graph_service: KnowledgeGraphService
    diagnostic_report_service: DiagnosticReportService
    terminology_service: TerminologyService
    mindmap_service: MindMapService
    diagram_service: DiagramService
    asr_service: ASRService
    web_clip_service: WebClipService
    backup_service: BackupService
    learning_package_service: LearningPackageService
    smart_queue_service: object
    cause_profile_service: object
    report_service: object
    voice_learning_service: object

    @classmethod
    def build(cls, model_manager: ModelManager) -> "AppContext":
        """按依赖顺序装配全部服务。

        构造顺序有约束：教学风格服务须先于 ChatService 以注入 system
        prompt；错题本创建后须回挂学习计划服务以支持动态调优。
        """
        user_service = UserService()
        tts_service = TTSService()
        statistics_service = StatisticsService(user_service=user_service)
        search_service = SearchService(user_service=user_service)
        session_manager = SessionManager(user_service=user_service)
        errorbook_service = ErrorBookService(
            user_service=user_service,
            model_manager=model_manager,
        )
        flashcard_service = FlashcardService(
            user_service=user_service,
            statistics_service=statistics_service,
        )
        practice_service = PracticeService(
            user_service=user_service,
            model_manager=model_manager,
            errorbook_service=errorbook_service,
        )
        exam_service = ExamService(
            user_service=user_service,
            model_manager=model_manager,
        )
        rag_service = RAGService(model_manager, user_service=user_service)
        teaching_style_service = TeachingStyleService(
            user_service=user_service,
        )
        chat_service = ChatService(
            model_manager,
            session_manager,
            rag_service=rag_service,
            errorbook_service=errorbook_service,
            user_service=user_service,
            teaching_style_service=teaching_style_service,
        )
        ocr_service = OCRService(
            model_manager,
            user_service=user_service,
        )
        input_router = InputRouter(ocr_service, rag_service)
        study_plan_service = StudyPlanService(
            model_manager=model_manager,
            flashcard_service=flashcard_service,
            errorbook_service=errorbook_service,
            user_service=user_service,
        )
        errorbook_service.set_study_plan_service(study_plan_service)
        note_annotation_service = NoteAnnotationService(user_service=user_service)
        knowledge_graph_service = KnowledgeGraphService(
            user_service=user_service,
        )
        diagnostic_report_service = DiagnosticReportService(
            user_service=user_service,
            knowledge_graph_service=knowledge_graph_service,
        )
        terminology_service = TerminologyService(
            user_service=user_service,
            model_manager=model_manager,
        )
        mindmap_service = MindMapService(
            model_manager=model_manager,
            user_service=user_service,
        )
        diagram_service = DiagramService(
            model_manager=model_manager,
            user_service=user_service,
        )
        asr_service = ASRService()
        web_clip_service = WebClipService(
            rag_service=rag_service,
            user_service=user_service,
        )
        backup_service = BackupService(
            user_service=user_service,
            errorbook_service=errorbook_service,
            flashcard_service=flashcard_service,
        )
        from ..services.smart_queue_service import SmartQueueService

        from ..services.cause_profile_service import CauseProfileService

        cause_profile_service = CauseProfileService(
            user_service=user_service,
            errorbook_service=errorbook_service,
        )
        smart_queue_service = SmartQueueService(
            user_service=user_service,
            flashcard_service=flashcard_service,
            errorbook_service=errorbook_service,
        )

        from ..services.voice_learning_service import VoiceLearningService

        voice_learning_service = VoiceLearningService(user_service=user_service)
        from ..services.report_service import ReportService

        report_service = ReportService(
            user_service=user_service,
            statistics_service=statistics_service,
            errorbook_service=errorbook_service,
            cause_profile_service=cause_profile_service,
            smart_queue_service=smart_queue_service,
        )
        learning_package_service = LearningPackageService(
            user_service=user_service,
            errorbook_service=errorbook_service,
            flashcard_service=flashcard_service,
            study_plan_service=study_plan_service,
            rag_service=rag_service,
            model_manager=model_manager,
        )
        logger.info("AppContext built: %d services wired", 25)
        return cls(
            user_service=user_service,
            tts_service=tts_service,
            statistics_service=statistics_service,
            search_service=search_service,
            session_manager=session_manager,
            errorbook_service=errorbook_service,
            flashcard_service=flashcard_service,
            practice_service=practice_service,
            exam_service=exam_service,
            rag_service=rag_service,
            teaching_style_service=teaching_style_service,
            chat_service=chat_service,
            ocr_service=ocr_service,
            input_router=input_router,
            study_plan_service=study_plan_service,
            note_annotation_service=note_annotation_service,
            knowledge_graph_service=knowledge_graph_service,
            diagnostic_report_service=diagnostic_report_service,
            terminology_service=terminology_service,
            mindmap_service=mindmap_service,
            diagram_service=diagram_service,
            asr_service=asr_service,
            web_clip_service=web_clip_service,
            backup_service=backup_service,
            learning_package_service=learning_package_service,
            smart_queue_service=smart_queue_service,
            cause_profile_service=cause_profile_service,
            report_service=report_service,
            voice_learning_service=voice_learning_service,
        )
