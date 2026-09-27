"""教学风格精细配置服务。

支持多维教学风格：基础风格、讲解深度、示例偏好、语气、提问方式、语言优先等。
配置持久化到数据库，并在 AI 对话 system prompt 中注入组合后的风格约束。
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from ..db import Database, get_db
from ..utils.logger import get_logger
from .user_service import UserService

logger = get_logger("services.teaching_style_service")


class TeachingStyleService:
    """教学风格配置业务服务。"""

    STYLE_MAP = {
        "strict": "严格",
        "encouraging": "鼓励",
        "concise": "简洁",
        "socratic": "苏格拉底式",
    }

    STYLE_PROMPTS = {
        "strict": (
            "【教学风格：严格】你是一位要求严格的老师。"
            "对学生回答的准确性、逻辑完整性和步骤规范性有较高要求；"
            "当学生出现概念错误或步骤跳跃时，直接指出问题并给出改进方向；"
            "不过度安抚，保持专业、清晰、目标导向的语气。"
        ),
        "encouraging": (
            "【教学风格：鼓励】你是一位耐心鼓励的老师。"
            "在学生遇到困难时给予积极反馈，肯定已有进步；"
            "用引导式提问帮助学生建立信心，避免打击式批评；"
            "语气温暖、支持性强，同时确保知识讲解准确。"
        ),
        "concise": (
            "【教学风格：简洁】你是一位高效简洁的老师。"
            "优先给出核心结论、关键步骤和记忆要点，避免冗长铺垫；"
            "使用条目化、结构化表达，让学生快速抓住重点；"
            "除非学生明确要求，否则不做过多展开。"
        ),
        "socratic": (
            "【教学风格：苏格拉底式】你是一位善于启发思考的老师。"
            "不直接给出答案，而是通过连续提问引导学生发现结论；"
            "每个问题只聚焦一个小步骤，逐步推进；"
            "在学生回答后先肯定合理部分，再提出下一个思考点。"
        ),
    }

    DEPTH_PROMPTS = {
        "brief": "讲解保持精简，只给出最关键的概念和结论，不做展开。",
        "moderate": "讲解适度详细，覆盖定义、原理和 1-2 个典型场景。",
        "detailed": "讲解深入详细，包括背景、推导、边界条件和常见误区。",
    }

    EXAMPLE_PROMPTS = {
        "none": "不额外举例。",
        "simple": "必要时给出简单直接的例子帮助理解。",
        "life": "优先使用生活化、贴近学生经验的例子。",
        "analogy": "多使用类比，将抽象概念映射到熟悉事物。",
        "code": "涉及可计算内容时，给出清晰的伪代码或代码示例。",
    }

    TONE_PROMPTS = {
        "formal": "语气正式、客观，使用规范学术表达。",
        "casual": "语气轻松自然，像朋友聊天一样解释。",
        "enthusiastic": "语气充满热情和感染力，调动学习积极性。",
        "calm": "语气平和稳重，降低学生焦虑感。",
    }

    QUESTION_PROMPTS = {
        "none": "讲解过程中不主动提问。",
        "hint": "在关键步骤后用提示性问题确认学生是否跟上。",
        "challenge": "适时抛出挑战性问题，引导学生深入思考。",
        "confirm": "每讲完一个知识点后询问学生是否理解。",
    }

    DEFAULT_CONFIG: Dict[str, Any] = {
        "style": "encouraging",
        "explanation_depth": "moderate",
        "example_style": "simple",
        "tone": "calm",
        "question_style": "hint",
        "use_local_language": True,
        "avoid_direct_answer": False,
        "focus_on_common_mistakes": False,
    }

    def __init__(
        self,
        db: Optional[Database] = None,
        user_service: Optional[UserService] = None,
    ):
        self.db = db or get_db()
        self.user_service = user_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    @staticmethod
    def _default_config() -> Dict[str, Any]:
        return dict(TeachingStyleService.DEFAULT_CONFIG)

    def get_config(self, user_id: Optional[int] = None) -> Dict[str, Any]:
        """读取指定用户的完整教学风格配置。"""
        uid = self._user_id(user_id)
        row = self.db.fetchone(
            "SELECT style, enabled, config_json FROM teaching_style_config WHERE user_id = ?",
            (uid,),
        )
        if row is None:
            return {"enabled": False, **self._default_config()}

        config = self._default_config()
        try:
            stored = json.loads(row["config_json"] or "{}")
            if isinstance(stored, dict):
                config.update(stored)
        except Exception as e:
            logger.warning("Failed to parse teaching style config_json: %s", e)

        config["style"] = row["style"] if row["style"] in self.STYLE_PROMPTS else config["style"]
        config["enabled"] = bool(row["enabled"])
        return config

    def save_config(
        self,
        config: Optional[Dict[str, Any]] = None,
        enabled: Optional[bool] = None,
        user_id: Optional[int] = None,
    ) -> None:
        """保存指定用户的教学风格配置。"""
        config = config or {}
        style = config.get("style", "encouraging")
        if style not in self.STYLE_PROMPTS:
            raise ValueError(f"不支持的教学风格：{style}")

        clean_config = self._default_config()
        clean_config.update({k: v for k, v in config.items() if k in clean_config})
        clean_config["style"] = style

        if enabled is None:
            existing = self.get_config(user_id)
            enabled = existing.get("enabled", False)

        uid = self._user_id(user_id)
        exists = self.db.fetchone(
            "SELECT id FROM teaching_style_config WHERE user_id = ?",
            (uid,),
        )
        config_json = json.dumps(clean_config, ensure_ascii=False)
        if exists:
            self.db.execute(
                "UPDATE teaching_style_config SET style = ?, enabled = ?, config_json = ?, "
                "updated_at = CURRENT_TIMESTAMP WHERE user_id = ?",
                (style, int(enabled), config_json, uid),
            )
        else:
            self.db.insert(
                "INSERT INTO teaching_style_config (user_id, style, enabled, config_json) "
                "VALUES (?, ?, ?, ?)",
                (uid, style, int(enabled), config_json),
            )
        logger.info("Teaching style saved user_id=%d style=%s enabled=%s", uid, style, enabled)

    def get_prompt(self, user_id: Optional[int] = None) -> str:
        """组合生成教学风格 system prompt 片段；未启用时返回空字符串。"""
        config = self.get_config(user_id)
        if not config.get("enabled", False):
            return ""

        parts: list[str] = []
        style = config.get("style", "encouraging")
        parts.append(self.STYLE_PROMPTS.get(style, ""))

        depth = config.get("explanation_depth", "moderate")
        if depth in self.DEPTH_PROMPTS:
            parts.append(self.DEPTH_PROMPTS[depth])

        example = config.get("example_style", "simple")
        if example in self.EXAMPLE_PROMPTS:
            parts.append(self.EXAMPLE_PROMPTS[example])

        tone = config.get("tone", "calm")
        if tone in self.TONE_PROMPTS:
            parts.append(self.TONE_PROMPTS[tone])

        question = config.get("question_style", "hint")
        if question in self.QUESTION_PROMPTS:
            parts.append(self.QUESTION_PROMPTS[question])

        if config.get("use_local_language", True):
            parts.append("优先使用中文回答，必要时保留专业术语原文。")

        if config.get("avoid_direct_answer", False):
            parts.append("不要直接给出最终答案，先引导学生独立思考。")

        if config.get("focus_on_common_mistakes", False):
            parts.append("讲解时请指出该知识点常见错误与易混淆点。")

        prompt = "\n\n".join(p for p in parts if p)
        return prompt
