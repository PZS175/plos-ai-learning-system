"""AI 对话服务。

封装本地大模型对话流程，包括基于知识库的 RAG 增强、会话历史管理、异常降级提示。
业务层不操作 Qt 控件，返回文本或抛出可处理的业务异常。
"""

from __future__ import annotations

from typing import List, Optional

from ..ai import ModelManager
from ..core.enums import Role
from ..core.models import ChatMessage, InferenceResponse, SearchResult
from ..utils.exceptions import ModelConnectionError
from ..utils.logger import get_logger
from .errorbook_service import ErrorBookService
from .rag_service import RAGService
from .session_manager import SessionManager
from .teaching_style_service import TeachingStyleService
from .user_service import UserService

logger = get_logger("services.chat_service")


class ChatService:
    """本地 AI 对话助手服务。"""

    SYSTEM_PROMPT_RAG = (
        "你是 PLOS AI 个人学习助手，基于本地模型运行。"
        "请根据提供的参考资料回答用户问题，如果参考资料不足，请明确说明。"
        "回答要简洁、准确、适合学生学习。"
    )

    SYSTEM_PROMPT_CHAT = (
        "你是 PLOS AI 个人学习助手，基于本地模型运行。"
        "请用中文回答，尽量简洁、清晰、适合学生理解。"
    )

    SYSTEM_PROMPT_CONTEXT = (
        "你是 PLOS AI 个人学习助手，基于本地模型运行。"
        "当前已启用个性化学习上下文，回答时请结合用户的错题本和知识库内容，"
        "针对薄弱点进行讲解，帮助学生查漏补缺。回答要简洁、准确、适合学生理解。"
    )

    SYSTEM_PROMPT_SOCRATIC = (
        "你是 PLOS AI 个人学习助手，当前处于【启发教学模式】。"
        "你的核心目标是引导学生独立思考，而不是直接给出完整答案。请严格遵守：\n"
        "1. 不要直接输出最终答案或完整解题过程；\n"
        "2. 优先反问学生的思路，询问他已经知道哪些相关知识点；\n"
        "3. 根据学生的回复，逐步释放提示和线索；\n"
        "4. 用提问、类比、举例等方式帮助学生自己找到答案；\n"
        "5. 如果学生完全卡壳，只能给出极小的一步提示，且仍要保留思考空间；\n"
        "6. 回答简洁、适合学生理解，每轮尽量只引导学生向前迈出一小步。"
    )

    SYSTEM_PROMPT_EXPLAIN_BASIC = (
        "你是 PLOS AI 个人学习助手，当前处于【基础版讲解模式】。"
        "请用通俗易懂的语言讲解知识点，避免使用过于抽象的术语。"
        "多用生活化类比和具体例子，帮助学生建立直观理解。回答简洁、循序渐进。"
    )

    SYSTEM_PROMPT_EXPLAIN_ADVANCED = (
        "你是 PLOS AI 个人学习助手，当前处于【进阶版讲解模式】。"
        "请深入讲解知识点的原理、推导过程与内在联系。"
        "鼓励学生理解本质而非死记硬背，可涉及定理证明、公式推导和知识拓展。"
    )

    SYSTEM_PROMPT_EXPLAIN_EXAM = (
        "你是 PLOS AI 个人学习助手，当前处于【应试版讲解模式】。"
        "请围绕考试高频考点进行讲解，明确常考题型、易错点和答题技巧。"
        "给出典型例题与解题模板，帮助学生快速提分。"
    )

    SYSTEM_PROMPT_EXPLAIN_RESEARCH = (
        "你是 PLOS AI 个人学习助手，当前处于【科研深度版讲解模式】。"
        "请从学术研究视角讲解知识点，介绍前沿进展、经典论文与研究方法。"
        "适合有科研兴趣的学生，鼓励批判性思维与深入探索。"
    )

    NO_DIRECT_ANSWER_CONSTRAINT = (
        "【输出约束：禁止直接给出答案】你只能提供提示、思路引导、相关知识点提醒或下一步思考方向，"
        "禁止直接给出完整答案、最终结论或完整解题步骤。"
    )

    def __init__(
        self,
        model_manager: ModelManager,
        session_manager: SessionManager,
        rag_service: Optional[RAGService] = None,
        errorbook_service: Optional[ErrorBookService] = None,
        user_service: Optional[UserService] = None,
        teaching_style_service: Optional[TeachingStyleService] = None,
    ):
        self.model_manager = model_manager
        self.session_manager = session_manager
        self.rag_service = rag_service
        self.errorbook_service = errorbook_service
        self.user_service = user_service
        self.teaching_style_service = teaching_style_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def _build_messages(
        self,
        conversation_id: int,
        user_text: str,
        use_rag: bool = False,
        use_context: bool = False,
        top_k: int = 4,
        user_id: Optional[int] = None,
        teaching_mode: str = "normal",
        no_direct_answer: bool = False,
        explanation_mode: str = "basic",
    ) -> List[ChatMessage]:
        """构建包含系统提示、参考资料、错题本上下文和历史记录的消息列表。"""
        system_content = self.SYSTEM_PROMPT_CHAT
        context_blocks: List[str] = []

        if use_context:
            system_content = self.SYSTEM_PROMPT_CONTEXT
            # 1. 知识库 RAG
            if self.rag_service is not None:
                try:
                    results: List[SearchResult] = self.rag_service.search(user_text, top_k=top_k)
                    if results:
                        context_blocks.append("知识库参考资料：")
                        for i, result in enumerate(results, 1):
                            context_blocks.append(f"[{i}] 来源：{result.source}\n{result.content}")
                except Exception as e:
                    logger.warning("RAG search failed in context mode: %s", e)
            # 2. 错题本上下文
            if self.errorbook_service is not None:
                try:
                    errors = self.errorbook_service.list_errors(
                        tag=user_text[:20],
                        limit=5,
                        user_id=user_id,
                    )
                    if errors:
                        context_blocks.append("用户相关错题：")
                        for i, err in enumerate(errors, 1):
                            context_blocks.append(
                                f"[{i}] 题目：{err['question'][:200]}\n"
                                f"解析：{err['analysis'][:300]}"
                            )
                except Exception as e:
                    logger.warning("Error book context loading failed: %s", e)
        elif use_rag and self.rag_service is not None:
            try:
                results: List[SearchResult] = self.rag_service.search(user_text, top_k=top_k)
                if results:
                    system_content = self.SYSTEM_PROMPT_RAG
                    context_blocks.append("参考资料：")
                    for i, result in enumerate(results, 1):
                        context_blocks.append(f"[{i}] 来源：{result.source}\n{result.content}")
            except Exception as e:
                logger.warning("RAG search failed, falling back to plain chat: %s", e)

        # 启发教学模式：替换基础 system prompt
        if teaching_mode == "socratic":
            system_content = self.SYSTEM_PROMPT_SOCRATIC
        else:
            # 知识点讲解模式切换（非启发教学时生效）
            explain_map = {
                "basic": self.SYSTEM_PROMPT_EXPLAIN_BASIC,
                "advanced": self.SYSTEM_PROMPT_EXPLAIN_ADVANCED,
                "exam": self.SYSTEM_PROMPT_EXPLAIN_EXAM,
                "research": self.SYSTEM_PROMPT_EXPLAIN_RESEARCH,
            }
            if explanation_mode in explain_map:
                system_content = explain_map[explanation_mode]

        # 禁止直接给出答案：追加约束
        if no_direct_answer:
            system_content = f"{system_content}\n\n{self.NO_DIRECT_ANSWER_CONSTRAINT}"

        # 教学风格精细配置：追加风格约束
        if self.teaching_style_service is not None:
            try:
                style_prompt = self.teaching_style_service.get_prompt(user_id=user_id)
                if style_prompt:
                    system_content = f"{system_content}\n\n{style_prompt}"
            except Exception as e:
                logger.warning("Failed to apply teaching style prompt: %s", e)

        messages: List[ChatMessage] = []
        if context_blocks:
            messages.append(ChatMessage(role=Role.SYSTEM, content="\n\n".join(context_blocks)))
        messages.append(ChatMessage(role=Role.SYSTEM, content=system_content))

        # 加载历史消息（最近若干轮）
        history = self.session_manager.get_messages(conversation_id, user_id=user_id)
        # 限制上下文长度：保留系统提示后最多 10 条历史
        messages.extend(history[-10:])
        messages.append(ChatMessage(role=Role.USER, content=user_text))
        return messages

    def send_message(
        self,
        conversation_id: int,
        user_text: str,
        use_rag: bool = False,
        use_context: bool = False,
        save_history: bool = True,
        user_id: Optional[int] = None,
        teaching_mode: str = "normal",
        no_direct_answer: bool = False,
        explanation_mode: str = "basic",
    ) -> InferenceResponse:
        """发送用户消息并获取模型回复。"""
        if not self.model_manager.is_text_available():
            raise ModelConnectionError(
                self.model_manager.backend_type.value,
                detail="本地模型服务未启动，请检查 Ollama 是否运行。",
            )

        uid = user_id if user_id is not None else self._user_id()

        if save_history:
            self.session_manager.add_message(conversation_id, Role.USER, user_text, user_id=uid)

        messages = self._build_messages(
            conversation_id,
            user_text,
            use_rag=use_rag,
            use_context=use_context,
            user_id=uid,
            teaching_mode=teaching_mode,
            no_direct_answer=no_direct_answer,
            explanation_mode=explanation_mode,
        )
        try:
            response = self.model_manager.chat(messages=messages)
        except ModelConnectionError:
            raise
        except Exception as e:
            logger.error("Chat inference failed: %s", e)
            raise ModelConnectionError(
                self.model_manager.backend_type.value,
                detail=f"模型调用失败：{e}",
            ) from e

        if response is None:
            raise ModelConnectionError(
                self.model_manager.backend_type.value,
                detail="模型调用返回为空",
            )
        if save_history:
            self.session_manager.add_message(
                conversation_id, Role.ASSISTANT, response.content, user_id=uid
            )
        return response

    def send_message_stream(
        self,
        conversation_id: int,
        user_text: str,
        use_rag: bool = False,
        use_context: bool = False,
        user_id: Optional[int] = None,
        teaching_mode: str = "normal",
        no_direct_answer: bool = False,
        explanation_mode: str = "basic",
    ):
        """流式对话：逐段 yield 事件字典。

        事件：{"type": "chunk", "text"} / {"type": "done", "content", "duration_ms"}
        / {"type": "error", "message"}。结束后自动保存双方消息。
        """
        import time as _time

        start = _time.perf_counter()
        uid = user_id if user_id is not None else self._user_id()
        try:
            if not self.model_manager.is_text_available():
                raise ModelConnectionError(
                    self.model_manager.backend_type.value,
                    detail="本地模型服务未启动，请检查 Ollama 是否运行。",
                )
            self.session_manager.add_message(conversation_id, Role.USER, user_text, user_id=uid)
            messages = self._build_messages(
                conversation_id,
                user_text,
                use_rag=use_rag,
                use_context=use_context,
                user_id=uid,
                teaching_mode=teaching_mode,
                no_direct_answer=no_direct_answer,
                explanation_mode=explanation_mode,
            )
            parts = []
            for chunk in self.model_manager.chat_stream(messages=messages):
                parts.append(chunk)
                yield {"type": "chunk", "text": chunk}
            full = "".join(parts)
            self.session_manager.add_message(
                conversation_id, Role.ASSISTANT, full, user_id=uid
            )
            duration_ms = int((_time.perf_counter() - start) * 1000)
            yield {"type": "done", "content": full, "duration_ms": duration_ms}
        except Exception as e:
            logger.error("Streaming chat failed: %s", e)
            yield {"type": "error", "message": str(e)}

    def create_conversation(self, title: str = "新对话", user_id: Optional[int] = None) -> int:
        return self.session_manager.create_conversation(title, user_id=user_id)
