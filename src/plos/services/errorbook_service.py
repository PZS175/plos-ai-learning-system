"""错题本服务。

提供错题的增删改查、知识点标签管理、掌握度标记、导入导出。
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..ai import ModelManager
from ..core.enums import Role
from ..core.models import ChatMessage
from ..db import Database, get_db
from ..utils.latex_clean import latex_to_unicode
from ..utils.logger import get_logger
from ..utils.math_latex import normalize_text_keep_math
from .user_service import UserService

if TYPE_CHECKING:
    from .study_plan_service import StudyPlanService

logger = get_logger("services.errorbook_service")


_EXPORT_FIELDS = [
    "id",
    "question",
    "answer",
    "analysis",
    "knowledge_tags",
    "knowledge_points",
    "question_type",
    "subject",
    "chapter",
    "knowledge_point",
    "difficulty",
    "image_path",
    "mastery_level",
    "created_at",
    "updated_at",
]


_AUTO_TAG_PROMPT = (
    "你是教育题目解析器，请严格输出JSON，不要多余内容。\n"
    "所有文本值必须用简体中文书写，不要输出英文单词或拼音"
    "（例如应输出「二次函数」而不是 function）。\n"
    "分析题目，输出：\n"
    "knowledge_points：知识点数组（中文）\n"
    "question_type：选择题/填空题/判断题/解答题（中文）\n"
    "difficulty：1-5整数（1最简单）\n"
    "tags：综合标签数组（中文）\n\n"
    "只输出JSON，不要其他解释。\n\n"
    "题目：\n{text}"
)

_VARIANT_PROMPT = (
    "请根据下面这道错题的知识点，生成一道同知识点的变式练习题。"
    "变式题应考查相同核心知识点但场景或数字不同，并给出答案和解析。\n"
    "输出格式：\n【题目】...\n【答案】...\n【解析】...\n\n"
    "原题：\n{text}\n\n"
    "知识点：{knowledge_point}"
)


class ErrorBookService:
    """错题本业务服务。"""

    def __init__(
        self,
        db: Optional[Database] = None,
        user_service: Optional[UserService] = None,
        model_manager: Optional[ModelManager] = None,
        study_plan_service: Optional["StudyPlanService"] = None,
    ):
        self.db = db or get_db()
        self.user_service = user_service
        self.model_manager = model_manager
        self.study_plan_service = study_plan_service

    def set_study_plan_service(
        self, study_plan_service: Optional["StudyPlanService"]
    ) -> None:
        """设置学习计划服务，用于错题新增/掌握度变化时触发动态调优。"""
        self.study_plan_service = study_plan_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def add_error(
        self,
        question: str,
        answer: str = "",
        analysis: str = "",
        knowledge_tags: str = "",
        knowledge_points: Optional[List[str]] = None,
        question_type: str = "",
        image_path: Optional[str] = None,
        mastery_level: int = 0,
        subject: str = "",
        chapter: str = "",
        knowledge_point: str = "",
        difficulty: int = 1,
        user_id: Optional[int] = None,
    ) -> int:
        """添加一条错题记录，返回 error_id。"""
        uid = self._user_id(user_id)
        # 入库前清洗 LaTeX 记号，但**保留公式标记**：公式要交给全局渲染器与导出层，
        # 若在这里把 \( \) 删掉，后续就再也认不出公式了
        question = normalize_text_keep_math(question or "")
        answer = normalize_text_keep_math(answer or "")
        analysis = normalize_text_keep_math(analysis or "")
        kp_list = knowledge_points if knowledge_points is not None else []
        kp_json = json.dumps(kp_list, ensure_ascii=False)
        error_id = self.db.insert(
            "INSERT INTO error_book (user_id, question, answer, analysis, knowledge_tags, "
            "knowledge_points, question_type, image_path, mastery_level, subject, chapter, knowledge_point, difficulty) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uid,
                question,
                answer,
                analysis,
                knowledge_tags,
                kp_json,
                question_type,
                image_path,
                mastery_level,
                subject,
                chapter,
                knowledge_point,
                difficulty,
            ),
        )
        if kp_list:
            self._update_user_stat_on_add(uid, kp_list)
        # 触发学习路径动态调优：错题新增
        if self.study_plan_service is not None:
            try:
                self.study_plan_service.adjust_plan_on_error_added(error_id, user_id=uid)
            except Exception as e:
                logger.warning("Failed to adjust study plan on error added: %s", e)
        logger.info("Added error id=%d user_id=%d", error_id, uid)
        return error_id

    def update_error(
        self,
        error_id: int,
        question: Optional[str] = None,
        answer: Optional[str] = None,
        analysis: Optional[str] = None,
        knowledge_tags: Optional[str] = None,
        knowledge_points: Optional[List[str]] = None,
        question_type: Optional[str] = None,
        image_path: Optional[str] = None,
        mastery_level: Optional[int] = None,
        subject: Optional[str] = None,
        chapter: Optional[str] = None,
        knowledge_point: Optional[str] = None,
        difficulty: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> None:
        uid = self._user_id(user_id)
        # 如知识点变化，需要同步更新统计缓存
        old_kp: List[str] = []
        new_kp: List[str] = []
        if knowledge_points is not None:
            row = self.get_error(error_id, user_id=uid)
            if row is not None:
                try:
                    old_kp = json.loads(row.get("knowledge_points", "[]") or "[]")
                except Exception:
                    old_kp = []
            new_kp = knowledge_points

        fields = []
        values = []
        if question is not None:
            fields.append("question = ?")
            values.append(normalize_text_keep_math(question))
        if answer is not None:
            fields.append("answer = ?")
            values.append(normalize_text_keep_math(answer))
        if analysis is not None:
            fields.append("analysis = ?")
            values.append(normalize_text_keep_math(analysis))
        if knowledge_tags is not None:
            fields.append("knowledge_tags = ?")
            values.append(knowledge_tags)
        if knowledge_points is not None:
            fields.append("knowledge_points = ?")
            values.append(json.dumps(knowledge_points, ensure_ascii=False))
        if question_type is not None:
            fields.append("question_type = ?")
            values.append(question_type)
        if image_path is not None:
            fields.append("image_path = ?")
            values.append(image_path)
        if mastery_level is not None:
            fields.append("mastery_level = ?")
            values.append(mastery_level)
        if subject is not None:
            fields.append("subject = ?")
            values.append(subject)
        if chapter is not None:
            fields.append("chapter = ?")
            values.append(chapter)
        if knowledge_point is not None:
            fields.append("knowledge_point = ?")
            values.append(knowledge_point)
        if difficulty is not None:
            fields.append("difficulty = ?")
            values.append(difficulty)
        if not fields:
            return
        fields.append("updated_at = CURRENT_TIMESTAMP")
        values.extend([error_id, uid])
        sql = f"UPDATE error_book SET {', '.join(fields)} WHERE id = ? AND user_id = ?"
        self.db.execute(sql, tuple(values))

        if knowledge_points is not None:
            self._update_user_stat_on_edit(uid, old_kp, new_kp)

    def delete_error(self, error_id: int, user_id: Optional[int] = None) -> None:
        uid = self._user_id(user_id)
        row = self.get_error(error_id, user_id=uid)
        self.db.execute(
            "DELETE FROM error_book WHERE id = ? AND user_id = ?",
            (error_id, uid),
        )
        if row:
            try:
                kp_list = json.loads(row.get("knowledge_points", "[]") or "[]")
            except Exception:
                kp_list = []
            if kp_list:
                self._update_user_stat_on_delete(row.get("user_id", uid), kp_list)

    # ============================================================
    # 薄弱知识点统计（本地 Python 实现，不依赖大模型）
    # ============================================================

    def _update_user_stat_on_add(
        self, user_id: int, knowledge_points: List[str]
    ) -> None:
        """新增错题时累加对应知识点统计（UPSERT 原子自增，并发安全）。"""
        for point in knowledge_points:
            point = point.strip()
            if not point:
                continue
            self.db.execute(
                "INSERT INTO user_stat (user_id, knowledge_point, error_count, total_count, error_rate) "
                "VALUES (?, ?, 1, 1, 1.0) "
                "ON CONFLICT(user_id, knowledge_point) DO UPDATE SET "
                "error_count = error_count + 1, total_count = total_count + 1, "
                "error_rate = CAST(error_count + 1 AS REAL) / (total_count + 1), "
                "updated_at = CURRENT_TIMESTAMP",
                (user_id, point),
            )

    def _update_user_stat_on_delete(
        self, user_id: int, knowledge_points: List[str]
    ) -> None:
        """删除错题时扣减对应知识点统计。"""
        for point in knowledge_points:
            point = point.strip()
            if not point:
                continue
            row = self.db.fetchone(
                "SELECT id, error_count, total_count FROM user_stat WHERE user_id = ? AND knowledge_point = ?",
                (user_id, point),
            )
            if row is None:
                continue
            new_count = max(0, row["error_count"] - 1)
            new_total = max(1, row["total_count"] - 1)
            self.db.execute(
                "UPDATE user_stat SET error_count = ?, total_count = ?, error_rate = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (new_count, new_total, new_count / new_total, row["id"]),
            )
            # 若已无错题，可清理记录
            if new_count == 0:
                self.db.execute(
                    "DELETE FROM user_stat WHERE id = ?",
                    (row["id"],),
                )

    def _update_user_stat_on_edit(
        self,
        user_id: int,
        old_points: List[str],
        new_points: List[str],
    ) -> None:
        """编辑错题知识点时，先删除旧知识点计数，再增加新知识点计数。"""
        self._update_user_stat_on_delete(user_id, old_points)
        self._update_user_stat_on_add(user_id, new_points)

    def rebuild_user_stats(self, user_id: Optional[int] = None) -> None:
        """根据当前错题表全量重建用户统计缓存。"""
        uid = self._user_id(user_id)
        self.db.execute("DELETE FROM user_stat WHERE user_id = ?", (uid,))
        rows = self.db.fetchall(
            "SELECT knowledge_points FROM error_book WHERE user_id = ?",
            (uid,),
        )
        for row in rows:
            try:
                kp_list = json.loads(row.get("knowledge_points", "[]") or "[]")
            except Exception:
                kp_list = []
            if isinstance(kp_list, list):
                self._update_user_stat_on_add(uid, kp_list)
        logger.info("Rebuilt user stats for user_id=%d", uid)

    def get_weak_knowledge_points(
        self, top_n: int = 5, user_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """获取用户 TOP N 薄弱知识点。

        排序策略：优先按错误次数降序，次数相同则按错误率降序。
        """
        uid = self._user_id(user_id)
        rows = self.db.fetchall(
            "SELECT knowledge_point, error_count, total_count, error_rate FROM user_stat "
            "WHERE user_id = ? AND error_count > 0 "
            "ORDER BY error_count DESC, error_rate DESC LIMIT ?",
            (uid, top_n),
        )
        return [
            {
                "knowledge_point": r["knowledge_point"],
                "error_count": r["error_count"],
                "total_count": r["total_count"],
                "error_rate": round(r["error_rate"] or 0.0, 2),
            }
            for r in rows
        ]

    def get_error(self, error_id: int, user_id: Optional[int] = None) -> Optional[dict]:
        uid = self._user_id(user_id)
        return self.db.fetchone(
            "SELECT * FROM error_book WHERE id = ? AND user_id = ?",
            (error_id, uid),
        )

    def list_errors(
        self,
        mastery_level: Optional[int] = None,
        tag: Optional[str] = None,
        subject: Optional[str] = None,
        chapter: Optional[str] = None,
        knowledge_point: Optional[str] = None,
        difficulty: Optional[int] = None,
        limit: int = 100,
        user_id: Optional[int] = None,
    ) -> List[dict]:
        conditions = ["user_id = ?"]
        params = [self._user_id(user_id)]
        if mastery_level is not None:
            conditions.append("mastery_level = ?")
            params.append(mastery_level)
        if tag:
            # 转义 LIKE 通配符，避免用户输入 % / _ 时误匹配全表
            escaped = tag.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            like = f"%{escaped}%"
            conditions.append(
                "(knowledge_tags LIKE ? ESCAPE '\\' OR subject LIKE ? ESCAPE '\\' "
                "OR chapter LIKE ? ESCAPE '\\' OR knowledge_point LIKE ? ESCAPE '\\')"
            )
            params.extend([like] * 4)
        if subject:
            conditions.append("subject = ?")
            params.append(subject)
        if chapter:
            conditions.append("chapter = ?")
            params.append(chapter)
        if knowledge_point:
            conditions.append("knowledge_point = ?")
            params.append(knowledge_point)
        if difficulty is not None:
            conditions.append("difficulty = ?")
            params.append(difficulty)
        where_clause = f"WHERE {' AND '.join(conditions)}"
        sql = f"SELECT * FROM error_book {where_clause} ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        return self.db.fetchall(sql, tuple(params))

    def list_subjects(self, user_id: Optional[int] = None) -> List[str]:
        """获取当前用户所有不重复科目。"""
        uid = self._user_id(user_id)
        rows = self.db.fetchall(
            "SELECT DISTINCT subject FROM error_book WHERE user_id = ? AND subject != '' ORDER BY subject",
            (uid,),
        )
        return [r["subject"] for r in rows]

    def list_chapters(self, subject: str, user_id: Optional[int] = None) -> List[str]:
        """获取某科目下所有不重复章节。"""
        uid = self._user_id(user_id)
        rows = self.db.fetchall(
            "SELECT DISTINCT chapter FROM error_book WHERE user_id = ? AND subject = ? AND chapter != '' ORDER BY chapter",
            (uid, subject),
        )
        return [r["chapter"] for r in rows]

    def list_knowledge_points(self, user_id: Optional[int] = None) -> List[str]:
        """获取当前用户所有不重复知识点。"""
        uid = self._user_id(user_id)
        rows = self.db.fetchall(
            "SELECT DISTINCT knowledge_point FROM error_book WHERE user_id = ? AND knowledge_point != '' ORDER BY knowledge_point",
            (uid,),
        )
        return [r["knowledge_point"] for r in rows]

    def set_mastery(
        self, error_id: int, level: int, user_id: Optional[int] = None
    ) -> None:
        """掌握度：0 未掌握，1 基本掌握，2 已掌握。"""
        if level not in (0, 1, 2):
            raise ValueError("掌握度必须是 0、1 或 2")
        self.update_error(error_id, mastery_level=level, user_id=user_id)
        # 触发学习路径动态调优：掌握度变化
        if self.study_plan_service is not None:
            try:
                self.study_plan_service.adjust_plan_on_mastery_changed(
                    error_id, level, user_id=user_id
                )
            except Exception as e:
                logger.warning("Failed to adjust study plan on mastery changed: %s", e)

    def toggle_favorite(self, error_id: int, user_id: Optional[int] = None) -> bool:
        """切换错题收藏状态，返回新的收藏状态。"""
        uid = self._user_id(user_id)
        row = self.db.fetchone(
            "SELECT is_favorite FROM error_book WHERE id = ? AND user_id = ?",
            (error_id, uid),
        )
        if row is None:
            raise ValueError(f"错题不存在：{error_id}")
        new_state = 0 if row.get("is_favorite", 0) else 1
        self.db.execute(
            "UPDATE error_book SET is_favorite = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ? AND user_id = ?",
            (new_state, error_id, uid),
        )
        return bool(new_state)

    def list_favorites(self, user_id: Optional[int] = None) -> List[dict]:
        """列出当前用户收藏的错题。"""
        uid = self._user_id(user_id)
        return self.db.fetchall(
            "SELECT * FROM error_book WHERE user_id = ? AND is_favorite = 1 ORDER BY updated_at DESC",
            (uid,),
        )

    def export_to_json(self, file_path: Path, user_id: Optional[int] = None) -> int:
        """将错题导出为 JSON 文件，返回导出条数。"""
        errors = self.list_errors(limit=10000, user_id=user_id)
        file_path.write_text(
            json.dumps(errors, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Exported %d errors to JSON: %s", len(errors), file_path)
        return len(errors)

    def import_from_json(self, file_path: Path) -> int:
        """从 JSON 文件导入错题，返回导入条数。"""
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to parse error book JSON: %s", e)
            return 0
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            logger.error("Invalid error book JSON format: expected list or dict")
            return 0
        count = 0
        failed = 0
        for item in data:
            if not isinstance(item, dict):
                failed += 1
                continue
            try:
                kp_raw = item.get("knowledge_points", "[]")
                try:
                    kp_list = json.loads(kp_raw) if isinstance(kp_raw, str) else kp_raw
                    if not isinstance(kp_list, list):
                        kp_list = []
                except Exception:
                    kp_list = []
                self.add_error(
                    question=item.get("question", "") or "",
                    answer=item.get("answer", "") or "",
                    analysis=item.get("analysis", "") or "",
                    knowledge_tags=item.get("knowledge_tags", "") or "",
                    knowledge_points=kp_list,
                    question_type=item.get("question_type", "") or "",
                    image_path=item.get("image_path"),
                    mastery_level=int(item.get("mastery_level", 0) or 0),
                    subject=item.get("subject", "") or "",
                    chapter=item.get("chapter", "") or "",
                    knowledge_point=item.get("knowledge_point", "") or "",
                    difficulty=int(item.get("difficulty", 1) or 1),
                )
                count += 1
            except Exception as e:
                # 单条坏数据（如 mastery_level 非数字）不中断整批导入
                failed += 1
                logger.warning("Skip invalid error record during JSON import: %s", e)
        logger.info("Imported %d errors from JSON: %s (failed=%d)", count, file_path, failed)
        return count

    def export_to_csv(self, file_path: Path, user_id: Optional[int] = None) -> int:
        """将错题导出为 CSV 文件，返回导出条数。"""
        errors = self.list_errors(limit=10000, user_id=user_id)
        with file_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=_EXPORT_FIELDS)
            writer.writeheader()
            for row in errors:
                writer.writerow({k: row.get(k, "") for k in _EXPORT_FIELDS})
        logger.info("Exported %d errors to CSV: %s", len(errors), file_path)
        return len(errors)

    def import_from_csv(self, file_path: Path) -> int:
        """从 CSV 文件导入错题，返回导入条数。"""
        count = 0
        failed = 0
        with file_path.open("r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    kp_raw = row.get("knowledge_points", "[]")
                    try:
                        kp_list = json.loads(kp_raw) if kp_raw else []
                        if not isinstance(kp_list, list):
                            kp_list = []
                    except Exception:
                        kp_list = []
                    self.add_error(
                        question=row.get("question", "") or "",
                        answer=row.get("answer", "") or "",
                        analysis=row.get("analysis", "") or "",
                        knowledge_tags=row.get("knowledge_tags", "") or "",
                        knowledge_points=kp_list,
                        question_type=row.get("question_type", "") or "",
                        image_path=row.get("image_path") or None,
                        mastery_level=int(row.get("mastery_level", 0) or 0),
                        subject=row.get("subject", "") or "",
                        chapter=row.get("chapter", "") or "",
                        knowledge_point=row.get("knowledge_point", "") or "",
                        difficulty=int(row.get("difficulty", 1) or 1),
                    )
                    count += 1
                except Exception as e:
                    # 单条坏数据不中断整批导入
                    failed += 1
                    logger.warning("Skip invalid error record during CSV import: %s", e)
        logger.info("Imported %d errors from CSV: %s (failed=%d)", count, file_path, failed)
        return count

    def export_to_markdown(self, file_path: Path, user_id: Optional[int] = None) -> int:
        """将错题导出为 Markdown 文件，返回导出条数。"""
        errors = self.list_errors(limit=10000, user_id=user_id)
        lines = ["# 错题本导出\n"]
        for err in errors:
            lines.append(f"## 错题 #{err['id']}")
            lines.append(f"- **科目**：{err.get('subject', '')}")
            lines.append(f"- **章节**：{err.get('chapter', '')}")
            lines.append(f"- **知识点**：{err.get('knowledge_point', '')}")
            lines.append(f"- **难度**：{err.get('difficulty', '')}")
            lines.append(f"- **掌握度**：{err.get('mastery_level', '')}")
            lines.append(f"- **标签**：{err.get('knowledge_tags', '')}\n")
            lines.append("### 题目")
            lines.append(err.get("question", ""))
            lines.append("\n### 正确答案")
            lines.append(err.get("answer", ""))
            lines.append("\n### 解析")
            lines.append(err.get("analysis", ""))
            lines.append("\n---\n")
        file_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Exported %d errors to Markdown: %s", len(errors), file_path)
        return len(errors)

    def auto_tag(
        self, question: str, user_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """调用本地大模型自动识别错题的知识点、题型和难度。"""
        fallback: Dict[str, Any] = {
            "knowledge_points": [],
            "question_type": "",
            "difficulty": 1,
            "tags": "",
            "subject": "",
            "chapter": "",
            "knowledge_point": "",
        }
        if self.model_manager is None or not self.model_manager.is_text_available():
            return fallback

        prompt = _AUTO_TAG_PROMPT.format(text=question[:2000])
        messages = [ChatMessage(role=Role.USER, content=prompt)]
        try:
            response = self.model_manager.chat(messages=messages, max_tokens=1024)
            if response is None:
                logger.warning("Auto tag returned empty response")
                return fallback
            text = response.content.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()
            data = json.loads(text)

            kp = data.get("knowledge_points", [])
            if isinstance(kp, str):
                kp = [k.strip() for k in kp.split(",") if k.strip()]
            if not isinstance(kp, list):
                kp = []

            tags = data.get("tags", [])
            if isinstance(tags, list):
                tags = ",".join(str(t) for t in tags)
            tags = str(tags or "")

            fallback.update({
                "knowledge_points": [str(k).strip() for k in kp if str(k).strip()],
                "question_type": str(data.get("question_type", "")),
                "difficulty": int(data.get("difficulty", 1) or 1),
                "tags": tags,
                "subject": "",
                "chapter": "",
                "knowledge_point": str(kp[0]) if kp else "",
            })
        except Exception as e:
            logger.warning("Auto tag failed: %s", e)
        return fallback

    def add_subjective_grade(
        self,
        error_id: int,
        question_id: int,
        user_answer: str,
        score: float,
        total_score: float = 100.0,
        scoring_points: Optional[List[str]] = None,
        lost_points: Optional[List[str]] = None,
        error_reasons: Optional[List[str]] = None,
        improvement: str = "",
        feedback: str = "",
        user_id: Optional[int] = None,
    ) -> int:
        """把一次主观题批改记录关联到错题本。"""
        uid = self._user_id(user_id)
        error = self.get_error(error_id, user_id=uid)
        if error is None:
            raise ValueError(f"错题不存在：{error_id}")
        # AI 生成内容入库前统一转成可读 Unicode（学生作答保持原样）
        scoring_points = [latex_to_unicode(str(x)) for x in (scoring_points or [])]
        lost_points = [latex_to_unicode(str(x)) for x in (lost_points or [])]
        error_reasons = [latex_to_unicode(str(x)) for x in (error_reasons or [])]
        improvement = latex_to_unicode(improvement or "")
        feedback = latex_to_unicode(feedback or "")
        grade_id = self.db.insert(
            "INSERT INTO subjective_grades "
            "(user_id, error_id, question_id, user_answer, score, total_score, "
            "scoring_points, lost_points, error_reasons, improvement, feedback) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                uid,
                error_id,
                question_id,
                user_answer,
                score,
                total_score,
                json.dumps(scoring_points, ensure_ascii=False),
                json.dumps(lost_points, ensure_ascii=False),
                json.dumps(error_reasons, ensure_ascii=False),
                improvement,
                feedback,
            ),
        )
        logger.info(
            "Added subjective grade id=%d error_id=%d user_id=%d score=%.1f",
            grade_id, error_id, uid, score,
        )
        return grade_id

    def list_subjective_grades(
        self, error_id: int, user_id: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """获取某条错题的全部主观题批改记录。"""
        uid = self._user_id(user_id)
        rows = self.db.fetchall(
            "SELECT * FROM subjective_grades WHERE error_id = ? AND user_id = ? "
            "ORDER BY created_at DESC, id DESC",
            (error_id, uid),
        )
        result = []
        for row in rows:
            item = dict(row)
            for key in ("scoring_points", "lost_points", "error_reasons"):
                try:
                    item[key] = json.loads(item.get(key, "[]") or "[]")
                except Exception:
                    item[key] = []
            result.append(item)
        return result

    def apply_auto_tag_to_error(self, error_id: int, user_id: Optional[int] = None) -> Dict[str, Any]:
        """对已有错题应用自动打标并保存。

        模型不可用或返回空结果时抛出 RuntimeError，绝不写库，
        避免空标签静默覆盖用户已手动填写的科目/知识点/难度。
        """
        error = self.get_error(error_id, user_id=user_id)
        if error is None:
            raise ValueError(f"错题不存在：{error_id}")
        if self.model_manager is None or not self.model_manager.is_text_available():
            raise RuntimeError("模型服务不可用，请先在「模型管理」中启动或配置模型")
        tags = self.auto_tag(error["question"], user_id=user_id)
        if not tags.get("knowledge_points") and not tags.get("question_type") and not tags.get("tags"):
            raise RuntimeError("模型未能识别出有效知识点，请稍后重试或手动填写标签")
        self.update_error(
            error_id,
            knowledge_points=tags["knowledge_points"],
            question_type=tags["question_type"],
            difficulty=tags["difficulty"],
            knowledge_tags=tags["tags"],
            knowledge_point=tags.get("knowledge_point", ""),
            user_id=user_id,
        )
        return tags

    def generate_variant_question(
        self, error_id: int, user_id: Optional[int] = None
    ) -> Dict[str, str]:
        """调用本地大模型生成变式练习题。

        失败时抛出 RuntimeError（含友好提示），由 UI 的 worker error
        信号呈现给用户，而不是静默返回空内容。
        """
        error = self.get_error(error_id, user_id=user_id)
        if error is None:
            raise ValueError(f"错题不存在：{error_id}")
        if self.model_manager is None or not self.model_manager.is_text_available():
            raise RuntimeError("模型服务不可用，请先在「模型管理」中启动或配置模型")

        prompt = _VARIANT_PROMPT.format(
            text=error["question"][:1500],
            knowledge_point=error.get("knowledge_point", ""),
        )
        messages = [ChatMessage(role=Role.USER, content=prompt)]
        try:
            response = self.model_manager.chat(messages=messages, max_tokens=2048)
            if response is None:
                logger.warning("Variant question generation returned empty response")
                raise RuntimeError("模型返回为空，请重试或更换模型")
            variant = self._parse_variant_blocks(response.content)
            if not variant.get("question"):
                raise RuntimeError("模型未返回有效的题目内容，请重试")
            return variant
        except RuntimeError:
            raise
        except Exception as e:
            logger.warning("Variant question generation failed: %s", e)
            raise RuntimeError(f"生成变式题失败：{e}") from e

    @staticmethod
    def _parse_variant_blocks(text: str) -> Dict[str, str]:
        """解析变式题输出块。"""
        import re

        result = {"question": "", "answer": "", "analysis": ""}
        patterns = {
            "question": re.compile(r"【题目】\s*(.*?)\s*(?=【答案】|【解析】|$)", re.DOTALL),
            "answer": re.compile(r"【答案】\s*(.*?)\s*(?=【解析】|$)", re.DOTALL),
            "analysis": re.compile(r"【解析】\s*(.*)$", re.DOTALL),
        }
        for key, pattern in patterns.items():
            match = pattern.search(text)
            if match:
                result[key] = match.group(1).strip()
        if not result["question"] and text.strip():
            result["question"] = text.strip()
        return result
