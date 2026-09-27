"""自动出题与自适应练习服务。

功能：
- 按知识点自动生成多种题型（选择/填空/判断/简答/主观开放）
- 自适应难度：根据该知识点近期做题正确率动态上调/下调难度
- 判题：客观题本地比对，主观题调用大模型判定并给出点评
- 练习记录持久化，支持统计正确率

兼容本地 Ollama 与云端 API 两种后端（统一走 ModelManager.chat）。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..ai import ModelManager
from ..core.enums import Role
from ..core.models import ChatMessage
from ..db import Database, get_db
from ..utils.json_utils import extract_json_object, raw_snippet
from ..utils.latex_clean import latex_to_unicode
from ..utils.logger import get_logger
from .errorbook_service import ErrorBookService
from .user_service import UserService

logger = get_logger("services.practice_service")


# 支持的题型：key -> 中文名
QUESTION_TYPES: Dict[str, str] = {
    "choice": "选择题",
    "fill": "填空题",
    "judge": "判断题",
    "short": "简答题",
    "open": "主观开放题",
}

# 客观题型：本地判分；主观题型：AI 判定
OBJECTIVE_TYPES = {"choice", "fill", "judge"}
SUBJECTIVE_TYPES = {"short", "open"}

# 自适应难度参数
_RECENT_WINDOW = 10          # 参与统计的最近做题记录数
_ACCURACY_HIGH = 0.8          # 正确率 >= 80% 时上调难度
_ACCURACY_LOW = 0.4           # 正确率 <= 40% 时下调难度
_DEFAULT_DIFFICULTY = 3
_MIN_DIFFICULTY = 1
_MAX_DIFFICULTY = 5


_GENERATE_PROMPT = (
    "你是专业的出题老师。请围绕知识点「{knowledge_point}」出一道{type_label}，"
    "难度为 {difficulty}/5（1 最简单，5 最难）。\n\n"
    "要求：\n"
    "1. 题目表述清晰严谨，考查该知识点的核心内容，适合学生练习；\n"
    "2. 选择题必须提供 4 个选项，answer 中只写正确选项字母（如 \"A\"）；\n"
    "3. 填空题的 answer 为标准答案文本；\n"
    "4. 判断题的 answer 只能是 \"正确\" 或 \"错误\"；\n"
    "5. 简答题/主观开放题的 answer 为参考答案要点；\n"
    "6. analysis 给出解题思路或知识点解析；\n"
    "7. 不要使用 LaTeX 命令，数学内容用普通人可读的 Unicode 文本表示："
    "分数写 3/4，平方写 x²，下标写 a₁，根号写 √2，"
    "希腊字母直接写 α β π，比较符写 ≤ ≥ ≠。\n\n"
    "严格输出 JSON，不要输出任何其他内容：\n"
    "{{\n"
    "  \"question\": \"题干\",\n"
    "  \"options\": [\"选项A内容\", \"选项B内容\", \"选项C内容\", \"选项D内容\"],\n"
    "  \"answer\": \"答案\",\n"
    "  \"analysis\": \"解析\"\n"
    "}}\n"
    "非选择题的 options 输出空数组 []。"
)

_JUDGE_PROMPT = (
    "你是阅卷老师。请对下面这道主观题进行结构化批改。\n\n"
    "题目：{question}\n"
    "参考答案：{answer}\n"
    "解析：{analysis}\n"
    "学生作答：{user_answer}\n\n"
    "请从以下维度评估并严格输出 JSON，不要输出任何其他内容：\n"
    "{{\n"
    "  \"is_correct\": true 或 false,\n"
    "  \"score\": 0 到 100 的整数（满分100）,\n"
    "  \"scoring_points\": [\"学生答案中符合得分点的描述1\", \"描述2\"],\n"
    "  \"lost_points\": [\"缺少或错误的得分点1\", \"得分点2\"],\n"
    "  \"error_reasons\": [\"错误原因1\", \"错误原因2\"],\n"
    "  \"improvement\": \"具体改进建议\",\n"
    "  \"feedback\": \"面向学生的总体点评\"\n"
    "}}"
)


def _extract_json(text: str) -> Optional[dict]:
    """委托健壮解析工具（容忍围栏/前后缀/尾逗号）。"""
    return extract_json_object(text)


def _extract_json_legacy(text: str) -> Optional[dict]:
    """从模型输出中提取 JSON 对象，失败返回 None。"""
    if not text:
        return None
    cleaned = text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json")[1].split("```")[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```")[1].split("```")[0].strip()
    # 去掉可能的前后缀文本，取第一个 { 到最后一个 } 之间内容
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        cleaned = cleaned[start : end + 1]
    try:
        data = json.loads(cleaned)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _normalize(text: str) -> str:
    """归一化答案文本用于比对：去空白、全角转半角、忽略大小写。"""
    if not text:
        return ""
    result = str(text).strip()
    replacements = {
        "（": "(", "）": ")", "，": ",", "。": ".", "：": ":",
        "；": ";", "“": '"', "”": '"', "‘": "'", "’": "'",
    }
    for full, half in replacements.items():
        result = result.replace(full, half)
    return result.lower().replace(" ", "")


class PracticeService:
    """自动出题与自适应练习业务服务。"""

    def __init__(
        self,
        db: Optional[Database] = None,
        user_service: Optional[UserService] = None,
        model_manager: Optional[ModelManager] = None,
        errorbook_service: Optional[ErrorBookService] = None,
    ):
        self.db = db or get_db()
        self.user_service = user_service
        self.model_manager = model_manager
        self.errorbook_service = errorbook_service

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def _chat(self, prompt: str, max_tokens: int = 2048) -> str:
        """统一调用模型（Ollama / 云端 API 由 ModelManager 决定）。"""
        if self.model_manager is None or not self.model_manager.is_text_available():
            raise RuntimeError("模型服务不可用，请检查 Ollama 或云端 API 配置")
        messages = [ChatMessage(role=Role.USER, content=prompt)]
        response = self.model_manager.chat(messages=messages, max_tokens=max_tokens)
        if response is None or not response.content:
            raise RuntimeError("模型返回为空")
        return response.content

    # ------------------------------------------------------------------
    # 自适应难度
    # ------------------------------------------------------------------

    def get_recent_stats(
        self, knowledge_point: str, user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """获取某知识点最近 N 次做题统计：次数、正确率、平均难度。"""
        uid = self._user_id(user_id)
        rows = self.db.fetchall(
            "SELECT r.is_correct, q.difficulty FROM practice_records r "
            "JOIN practice_questions q ON q.id = r.question_id "
            "WHERE r.user_id = ? AND r.knowledge_point = ? "
            "ORDER BY r.created_at DESC, r.id DESC LIMIT ?",
            (uid, knowledge_point, _RECENT_WINDOW),
        )
        if not rows:
            return {"count": 0, "accuracy": None, "avg_difficulty": None}
        correct = sum(1 for r in rows if r.get("is_correct"))
        difficulties = [int(r.get("difficulty") or _DEFAULT_DIFFICULTY) for r in rows]
        return {
            "count": len(rows),
            "accuracy": correct / len(rows),
            "avg_difficulty": sum(difficulties) / len(difficulties),
        }

    def suggest_difficulty(
        self, knowledge_point: str, user_id: Optional[int] = None
    ) -> int:
        """根据最近做题正确率自适应推荐难度（1-5）。

        规则：
        - 无做题记录：返回默认难度 3；
        - 正确率 >= 80%：在近期平均难度基础上 +1（上调）；
        - 正确率 <= 40%：在近期平均难度基础上 -1（下调）；
        - 其他：维持近期平均难度；
        - 结果裁剪到 [1, 5]。
        """
        stats = self.get_recent_stats(knowledge_point, user_id=user_id)
        if stats["count"] == 0 or stats["avg_difficulty"] is None:
            return _DEFAULT_DIFFICULTY

        base = round(float(stats["avg_difficulty"]))
        accuracy = float(stats["accuracy"] or 0.0)
        if accuracy >= _ACCURACY_HIGH:
            suggestion = base + 1
        elif accuracy <= _ACCURACY_LOW:
            suggestion = base - 1
        else:
            suggestion = base
        return max(_MIN_DIFFICULTY, min(_MAX_DIFFICULTY, suggestion))

    # ------------------------------------------------------------------
    # 出题
    # ------------------------------------------------------------------

    def generate_question(
        self,
        knowledge_point: str,
        question_type: str = "choice",
        difficulty: Optional[int] = None,
        save: bool = True,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """按知识点生成一道题并入库，返回题目 dict。

        difficulty 传 None 时使用自适应推荐难度。
        """
        knowledge_point = (knowledge_point or "").strip()
        if not knowledge_point:
            raise ValueError("请输入知识点")
        if question_type not in QUESTION_TYPES:
            raise ValueError(f"不支持的题型：{question_type}")

        uid = self._user_id(user_id)
        if difficulty is None:
            difficulty = self.suggest_difficulty(knowledge_point, user_id=uid)
        difficulty = max(_MIN_DIFFICULTY, min(_MAX_DIFFICULTY, int(difficulty)))

        prompt = _GENERATE_PROMPT.format(
            knowledge_point=knowledge_point,
            type_label=QUESTION_TYPES[question_type],
            difficulty=difficulty,
        )
        data = None
        last_raw = ""
        for attempt in range(2):  # 解析失败自动重试一次
            last_raw = self._chat(prompt, max_tokens=2048)
            data = _extract_json(last_raw)
            if data is not None and str(data.get("question", "")).strip():
                break
            data = None
        if data is None or not str(data.get("question", "")).strip():
            raise RuntimeError(
                "题目生成失败：模型未返回有效 JSON（已重试）。"
                f"原始输出片段：{raw_snippet(last_raw)}"
            )

        question_text = str(data.get("question", "")).strip()
        answer = str(data.get("answer", "")).strip()
        analysis = str(data.get("analysis", "")).strip()

        options_raw = data.get("options", [])
        if isinstance(options_raw, str):
            try:
                options_raw = json.loads(options_raw)
            except Exception:
                options_raw = []
        if not isinstance(options_raw, list):
            options_raw = []
        options = [str(o).strip() for o in options_raw if str(o).strip()]
        if question_type == "choice" and len(options) < 2:
            # 兜底：选择题选项不足时按简答题处理
            logger.warning("Choice question options invalid, fallback to short")
            options = []

        result: Dict[str, Any] = {
            "knowledge_point": knowledge_point,
            "question_type": question_type,
            "difficulty": difficulty,
            "question": question_text,
            "options": options,
            "answer": answer,
            "analysis": analysis,
        }

        if save:
            question_id = self.db.insert(
                "INSERT INTO practice_questions "
                "(user_id, knowledge_point, question_type, difficulty, question, options, answer, analysis) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    uid,
                    knowledge_point,
                    question_type,
                    difficulty,
                    question_text,
                    json.dumps(options, ensure_ascii=False),
                    answer,
                    analysis,
                ),
            )
            result["id"] = question_id
        else:
            result["id"] = 0
        logger.info(
            "Generated practice question kp=%s type=%s difficulty=%d",
            knowledge_point, question_type, difficulty,
        )
        return result

    def get_question(
        self, question_id: int, user_id: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """获取题目（不含答案泄露判断，直接返回完整信息）。"""
        uid = self._user_id(user_id)
        row = self.db.fetchone(
            "SELECT * FROM practice_questions WHERE id = ? AND user_id = ?",
            (question_id, uid),
        )
        if row is None:
            return None
        try:
            options = json.loads(row.get("options", "[]") or "[]")
        except Exception:
            options = []
        return {
            "id": row["id"],
            "knowledge_point": row.get("knowledge_point", ""),
            "question_type": row.get("question_type", "choice"),
            "difficulty": row.get("difficulty", 3),
            "question": row.get("question", ""),
            "options": options if isinstance(options, list) else [],
            "answer": row.get("answer", ""),
            "analysis": row.get("analysis", ""),
        }

    # ------------------------------------------------------------------
    # 判题
    # ------------------------------------------------------------------

    def submit_answer(
        self,
        question_id: int,
        user_answer: str,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """提交作答并判分，结果写入 practice_records。

        返回：is_correct、score(0-100)、feedback、correct_answer、analysis。
        """
        uid = self._user_id(user_id)
        question = self.get_question(question_id, user_id=uid)
        if question is None:
            raise ValueError(f"题目不存在：{question_id}")

        user_answer = (user_answer or "").strip()
        if not user_answer:
            raise ValueError("请先输入答案")

        q_type = question["question_type"]
        judge_result: Dict[str, Any]
        if q_type in OBJECTIVE_TYPES:
            is_correct = self._judge_objective(question["answer"], user_answer)
            score = 100 if is_correct else 0
            feedback = "回答正确。" if is_correct else "回答错误，请查看正确答案与解析。"
            judge_result = {
                "is_correct": is_correct,
                "score": score,
                "scoring_points": [],
                "lost_points": [],
                "error_reasons": [],
                "improvement": "",
                "feedback": feedback,
            }
        else:
            judge_result = self._judge_subjective(question, user_answer)
            is_correct = bool(judge_result.get("is_correct"))
            try:
                score = int(float(judge_result.get("score", 0) or 0))
            except (TypeError, ValueError):
                score = 0
            judge_result["score"] = score
            if "feedback" not in judge_result or not str(judge_result.get("feedback")).strip():
                judge_result["feedback"] = "模型未返回完整点评"

        # 反馈文本入库前统一转成可读 Unicode（模型输出可能夹带 LaTeX 记号）
        feedback_text = latex_to_unicode(str(judge_result.get("feedback", "") or ""))
        judge_result["feedback"] = feedback_text

        record_id = self.db.insert(
            "INSERT INTO practice_records "
            "(user_id, question_id, knowledge_point, user_answer, is_correct, score, feedback) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                uid,
                question_id,
                question.get("knowledge_point", ""),
                user_answer,
                1 if is_correct else 0,
                score,
                feedback_text,
            ),
        )
        logger.info(
            "Practice answer submitted question_id=%d correct=%s score=%d",
            question_id, is_correct, score,
        )

        # 主观题批改记录同步到错题本，方便后续查看得分点、失分点等
        if q_type in SUBJECTIVE_TYPES and self.errorbook_service is not None:
            try:
                self._sync_subjective_grade_to_errorbook(
                    uid=uid,
                    question=question,
                    user_answer=user_answer,
                    judge_result=judge_result,
                )
            except Exception as e:
                logger.warning("Failed to sync subjective grade to errorbook: %s", e)

        return_result = dict(judge_result)
        return_result.update({
            "record_id": record_id,
            "is_correct": is_correct,
            "score": score,
            "correct_answer": question.get("answer", ""),
            "analysis": question.get("analysis", ""),
            "knowledge_point": question.get("knowledge_point", ""),
            "next_difficulty": self.suggest_difficulty(
                question.get("knowledge_point", ""), user_id=uid
            ),
        })
        return return_result

    def _sync_subjective_grade_to_errorbook(
        self,
        uid: int,
        question: Dict[str, Any],
        user_answer: str,
        judge_result: Dict[str, Any],
    ) -> None:
        """将主观题批改结果挂接到错题本。

        如果错题本中已存在相同题干，则复用；否则新建一条错题记录。
        """
        q_text = question.get("question", "")
        knowledge_point = question.get("knowledge_point", "")
        # 题干先转可读 Unicode，与 errorbook 入库口径保持一致，
        # 避免清洗前后文本不一致导致同题去重失效、重复生成错题
        q_clean = latex_to_unicode(q_text or "")
        existing = self.db.fetchone(
            "SELECT id FROM error_book WHERE user_id = ? AND question = ? LIMIT 1",
            (uid, q_clean),
        )
        if existing:
            error_id = int(existing["id"])
        else:
            error_id = self.errorbook_service.add_error(
                question=q_clean,
                answer=question.get("answer", ""),
                analysis=question.get("analysis", ""),
                knowledge_tags=knowledge_point,
                knowledge_points=[knowledge_point] if knowledge_point else [],
                question_type="简答题" if question.get("question_type") == "short" else "主观开放题",
                subject="",
                chapter="",
                knowledge_point=knowledge_point,
                difficulty=int(question.get("difficulty", 1) or 1),
                mastery_level=0,
                user_id=uid,
            )
        self.errorbook_service.add_subjective_grade(
            error_id=error_id,
            question_id=int(question.get("id", 0) or 0),
            user_answer=user_answer,
            score=float(judge_result.get("score", 0) or 0),
            total_score=100.0,
            scoring_points=judge_result.get("scoring_points", []),
            lost_points=judge_result.get("lost_points", []),
            error_reasons=judge_result.get("error_reasons", []),
            improvement=str(judge_result.get("improvement", "")),
            feedback=str(judge_result.get("feedback", "")),
            user_id=uid,
        )

    @staticmethod
    def _judge_objective(correct_answer: str, user_answer: str) -> bool:
        """客观题本地判分：双侧先转可读 Unicode 再归一化比对。

        展示层会把 LaTeX 转成 Unicode 文本（如 \\frac{1}{2} → 1/2），
        学生作答也是对照清洗后的文本输入的；这里对两侧都做同样清洗，
        避免库中原样 LaTeX 与界面清洗文本比对不一致导致的误判。
        """
        correct = latex_to_unicode(correct_answer or "")
        user = latex_to_unicode(user_answer or "")
        return _normalize(correct) == _normalize(user)

    def _judge_subjective(self, question: Dict[str, Any], user_answer: str) -> Dict[str, Any]:
        """主观题调用大模型判定，返回结构化评分结果。"""
        prompt = _JUDGE_PROMPT.format(
            question=question.get("question", ""),
            answer=question.get("answer", ""),
            analysis=question.get("analysis", ""),
            user_answer=user_answer,
        )
        raw = self._chat(prompt, max_tokens=1024)
        data = _extract_json(raw)
        if data is None:
            return {
                "is_correct": False,
                "score": 0,
                "scoring_points": [],
                "lost_points": [],
                "error_reasons": [],
                "improvement": "模型判定返回异常，请重试或对照参考答案自行检查。",
                "feedback": "模型判定返回异常，请重试或对照参考答案自行检查。",
            }
        # 统一字段类型：列表字段必须是 list，字符串字段必须是 str
        for list_key in ("scoring_points", "lost_points", "error_reasons"):
            val = data.get(list_key)
            if isinstance(val, str):
                data[list_key] = [val]
            elif not isinstance(val, list):
                data[list_key] = []
            else:
                data[list_key] = [str(v).strip() for v in val if str(v).strip()]
        for str_key in ("improvement", "feedback"):
            data[str_key] = str(data.get(str_key, "") or "")
        if "score" not in data:
            data["score"] = 0
        if "is_correct" not in data:
            data["is_correct"] = False
        return data

    # ------------------------------------------------------------------
    # 统计与记录
    # ------------------------------------------------------------------

    def list_records(
        self, limit: int = 50, user_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """列出最近的做题记录（含题干摘要）。"""
        uid = self._user_id(user_id)
        rows = self.db.fetchall(
            "SELECT r.id, r.question_id, r.knowledge_point, r.user_answer, "
            "r.is_correct, r.score, r.feedback, r.created_at, "
            "q.question_type, q.question, q.difficulty "
            "FROM practice_records r "
            "LEFT JOIN practice_questions q ON q.id = r.question_id "
            "WHERE r.user_id = ? "
            "ORDER BY r.created_at DESC, r.id DESC LIMIT ?",
            (uid, limit),
        )
        return rows

    def get_stats(self, user_id: Optional[int] = None) -> Dict[str, Any]:
        """获取练习总统计：总题数、正确数、正确率。"""
        uid = self._user_id(user_id)
        row = self.db.fetchone(
            "SELECT COUNT(*) AS total, SUM(is_correct) AS correct "
            "FROM practice_records WHERE user_id = ?",
            (uid,),
        )
        if row is None:
            return {"total": 0, "correct": 0, "accuracy": 0.0}
        total = int(row.get("total") or 0)
        correct = int(row.get("correct") or 0)
        return {
            "total": total,
            "correct": correct,
            "accuracy": round(correct / total, 2) if total > 0 else 0.0,
        }

    def refresh_data(self) -> None:
        """用户切换后的数据刷新占位（由 UI 调用）。"""
