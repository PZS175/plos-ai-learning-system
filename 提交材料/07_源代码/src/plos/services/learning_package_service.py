"""学习包导入分享服务。

支持将错题、闪卡、学习计划、批注、术语、文档等学习数据打包为 zip，
通过文件导入/导出，或生成分享码进行交换。服务层不依赖 PyQt。
新增：导入预览、内容校验、冲突策略、进度回调。
"""

from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import string
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

from ..db import Database, get_db
from ..utils.logger import get_logger
from ..utils.paths import get_attachments_dir
from .errorbook_service import ErrorBookService
from .flashcard_service import FlashcardService
from .rag_service import RAGService
from .study_plan_service import StudyPlanService
from .user_service import UserService

logger = get_logger("services.learning_package_service")


# 包格式版本，用于导入时校验兼容性
_PACKAGE_VERSION = "1.1"

# 支持打包的数据表（按依赖顺序，文档最后处理以避免附件缺失）
_PACKAGE_TABLES = [
    "error_book",
    "flashcards",
    "study_plans",
    "study_plan_tasks",
    "note_annotations",
    "terms",
]

# 每张表导出时保留的字段（避免导出 id/user_id 等局部标识）
_EXPORT_FIELDS: Dict[str, List[str]] = {
    "error_book": [
        "question",
        "answer",
        "analysis",
        "knowledge_tags",
        "knowledge_points",
        "question_type",
        "image_path",
        "mastery_level",
        "subject",
        "chapter",
        "knowledge_point",
        "difficulty",
    ],
    "flashcards": [
        "front_content",
        "back_content",
        "front_image",
        "card_type",
        "subject",
        "tags",
        "ease",
        "interval",
        "repetitions",
        "next_review",
    ],
    "study_plans": [
        "title",
        "goal",
        "weak_subjects",
        "daily_minutes",
        "start_date",
        "end_date",
    ],
    "study_plan_tasks": [
        "title",
        "description",
        "subject",
        "estimated_minutes",
        "due_date",
        "is_completed",
    ],
    "note_annotations": [
        "target_type",
        "target_id",
        "selected_text",
        "note_content",
        "highlight_color",
    ],
    "terms": [
        "term",
        "definition",
        "source_type",
        "source_id",
    ],
}


def _generate_share_code() -> str:
    """生成 10 位大小写字母+数字分享码，包含校验位便于前端快速校验格式。"""
    alphabet = string.ascii_uppercase + string.ascii_lowercase + string.digits
    code = "".join(secrets.choice(alphabet) for _ in range(9))
    check = sum(ord(c) for c in code) % 36
    check_char = alphabet[check]
    return code + check_char


def _is_valid_share_code(share_code: str) -> bool:
    """校验分享码格式与校验位。"""
    if not share_code or len(share_code) != 10:
        return False
    alphabet = string.ascii_uppercase + string.ascii_lowercase + string.digits
    if not all(c in alphabet for c in share_code):
        return False
    code = share_code[:-1]
    check_char = share_code[-1]
    expected = alphabet[sum(ord(c) for c in code) % 36]
    return check_char == expected


def _content_checksum(data: Dict[str, Any]) -> str:
    """为包内容生成校验和，用于完整性校验。"""
    return hashlib.sha256(json.dumps(data, ensure_ascii=True, sort_keys=True).encode("utf-8")).hexdigest()[:16]


class LearningPackageService:
    """学习包业务服务：打包、导入预览、导入、分享码管理。"""

    def __init__(
        self,
        db: Optional[Database] = None,
        user_service: Optional[UserService] = None,
        errorbook_service: Optional[ErrorBookService] = None,
        flashcard_service: Optional[FlashcardService] = None,
        study_plan_service: Optional[StudyPlanService] = None,
        rag_service: Optional[RAGService] = None,
        model_manager: Optional[object] = None,
    ):
        self.db = db or get_db()
        self.user_service = user_service
        self.errorbook_service = errorbook_service
        self.flashcard_service = flashcard_service
        self.study_plan_service = study_plan_service
        self.rag_service = rag_service
        self.model_manager = model_manager

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def _table_rows(
        self,
        table: str,
        user_id: int,
        id_filter: Optional[Set[int]] = None,
    ) -> List[Dict[str, Any]]:
        """从指定表导出当前用户数据，返回已清理的字典列表。"""
        fields = _EXPORT_FIELDS.get(table, ["*"])
        if fields == ["*"]:
            sql = f"SELECT * FROM {table} WHERE user_id = ?"
            rows = self.db.fetchall(sql, (user_id,))
        else:
            cols = ", ".join(fields)
            sql = f"SELECT {cols} FROM {table} WHERE user_id = ?"
            rows = self.db.fetchall(sql, (user_id,))

        result = []
        for row in rows:
            row_dict = dict(row)
            if id_filter is not None and "id" in row_dict and row_dict["id"] not in id_filter:
                continue
            if table == "error_book" and "knowledge_points" in row_dict and isinstance(
                row_dict["knowledge_points"], str
            ):
                try:
                    row_dict["knowledge_points"] = json.loads(row_dict["knowledge_points"])
                except Exception:
                    row_dict["knowledge_points"] = []
            result.append(row_dict)
        return result

    def _document_rows(self, user_id: int) -> List[Dict[str, Any]]:
        """导出文档元数据，并准备附件打包。"""
        rows = self.db.fetchall(
            "SELECT id, filename, file_path, file_type, page_count FROM documents WHERE user_id = ?",
            (user_id,),
        )
        docs = []
        for row in rows:
            doc = dict(row)
            doc["file_path"] = str(doc.get("file_path", ""))
            docs.append(doc)
        return docs

    def get_inventory_summary(self, user_id: Optional[int] = None) -> Dict[str, Any]:
        """当前用户学习资源的分布摘要，供 AI 推荐组包使用。"""
        uid = self._user_id(user_id)
        errors = self.db.fetchall(
            "SELECT COALESCE(NULLIF(knowledge_point, ''), '未分类') AS kp, COUNT(*) AS c "
            "FROM error_book WHERE user_id = ? GROUP BY kp ORDER BY c DESC LIMIT 20",
            (uid,),
        )
        cards = self.db.fetchall(
            "SELECT COALESCE(NULLIF(subject, ''), '未分类') AS subject, COUNT(*) AS c "
            "FROM flashcards WHERE user_id = ? GROUP BY subject ORDER BY c DESC LIMIT 20",
            (uid,),
        )
        docs = self.db.fetchall(
            "SELECT COALESCE(NULLIF(filename, ''), '未命名文档') AS name FROM documents "
            "WHERE user_id = ? LIMIT 20",
            (uid,),
        )
        return {
            "errors": [{"point": r["kp"], "count": r["c"]} for r in errors],
            "flashcards": [{"subject": r["subject"], "count": r["c"]} for r in cards],
            "documents": [r["name"] for r in docs],
        }

    def _chat(self, prompt: str, max_tokens: int = 1024) -> str:
        if self.model_manager is None or not self.model_manager.is_text_available():
            raise RuntimeError("模型服务不可用，AI 智能组包需要本地模型或云端 API")
        from ..core.models import ChatMessage, Role

        response = self.model_manager.chat(
            messages=[ChatMessage(role=Role.USER, content=prompt)], max_tokens=max_tokens
        )
        if response is None or not response.content:
            raise RuntimeError("模型返回为空")
        return response.content

    def ai_recommend_package(self, requirement: str, user_id: Optional[int] = None) -> Dict[str, Any]:
        """根据用户需求描述，由 AI 推荐学习包的标题/描述/包含内容。

        返回 {"title", "description", "includes": {表名: bool}, "reason"}。
        """
        from ..utils.json_utils import extract_json_object, raw_snippet

        requirement = (requirement or "").strip()
        if not requirement:
            raise ValueError("请先描述学习包需求，例如：期末复习，函数与导数相关")
        inventory = self.get_inventory_summary(user_id=user_id)
        prompt = (
            "你是学习资料整理助手。学生想打包一份学习包，需求描述：\n"
            f"{requirement}\n\n"
            f"学生现有资源分布：\n"
            f"错题知识点：{inventory['errors']}\n"
            f"闪卡科目：{inventory['flashcards']}\n"
            f"文档：{inventory['documents']}\n\n"
            "请推荐学习包应包含的内容。严格输出 JSON 对象（不要解释、不要代码块）：\n"
            '{"title": "简洁标题", "description": "一句话描述", '
            '"include": {"error_book": true/false, "flashcards": true/false, '
            '"study_plans": true/false, "note_annotations": true/false, "terms": true/false}, '
            '"reason": "推荐理由一句话"}\n'
            "只选择与需求相关的资源类别。"
        )
        last_raw = ""
        data = None
        for _ in range(2):  # 解析失败重试一次
            last_raw = self._chat(prompt)
            data = extract_json_object(last_raw)
            if data:
                break
        if not data:
            raise RuntimeError(f"AI 推荐解析失败，原始输出片段：{raw_snippet(last_raw)}")

        includes = {
            "error_book": bool(data.get("include", {}).get("error_book", False)),
            "flashcards": bool(data.get("include", {}).get("flashcards", False)),
            "study_plans": bool(data.get("include", {}).get("study_plans", False)),
            "note_annotations": bool(data.get("include", {}).get("note_annotations", False)),
            "terms": bool(data.get("include", {}).get("terms", False)),
        }
        if not any(includes.values()):
            includes["error_book"] = True
            includes["flashcards"] = True
        return {
            "title": str(data.get("title", "")).strip()[:60] or requirement[:30],
            "description": str(data.get("description", "")).strip()[:200],
            "includes": includes,
            "reason": str(data.get("reason", "")).strip()[:200],
        }

    def export_package(
        self,
        output_path: Path,
        title: str = "学习包",
        description: str = "",
        includes: Optional[Dict[str, bool]] = None,
        user_id: Optional[int] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> Path:
        """导出学习包到 zip 文件，返回最终路径。"""
        uid = self._user_id(user_id)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if includes is None:
            includes = {t: True for t in _PACKAGE_TABLES}
            includes["documents"] = True

        if progress_callback:
            progress_callback(5, "收集学习数据...")

        data: Dict[str, Any] = {}
        for table in _PACKAGE_TABLES:
            data[table] = self._table_rows(table, uid) if includes.get(table, False) else []

        docs = []
        doc_files: List[Path] = []
        if includes.get("documents", False):
            docs = self._document_rows(uid)
            attachments_dir = get_attachments_dir()
            for doc in docs:
                src = Path(doc["file_path"])
                if not src.exists():
                    src = attachments_dir / src.name
                if src.exists():
                    doc_files.append(src)
                else:
                    logger.warning("Document file missing during export: %s", doc["file_path"])
        data["documents"] = docs

        if progress_callback:
            progress_callback(40, "生成元数据与校验...")

        package_meta = {
            "version": _PACKAGE_VERSION,
            "title": title,
            "description": description,
            "exported_at": datetime.now().isoformat(),
            "includes": includes,
            "checksum": _content_checksum(data),
        }
        payload = {"meta": package_meta, **data}

        if progress_callback:
            progress_callback(60, "写入 zip 文件...")

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("package.json", json.dumps(payload, ensure_ascii=False, indent=2))
            for i, src in enumerate(doc_files):
                arcname = f"attachments/{src.name}"
                zf.write(src, arcname)
                if progress_callback:
                    progress_callback(60 + int((i + 1) / max(1, len(doc_files)) * 35), f"打包附件 {src.name}...")

        if progress_callback:
            progress_callback(100, "导出完成")

        logger.info(
            "Exported learning package %s for user_id=%d tables=%s docs=%d",
            output_path,
            uid,
            [t for t in _PACKAGE_TABLES if includes.get(t)],
            len(docs),
        )
        return output_path

    def preview_package(self, package_path: Path) -> Dict[str, Any]:
        """预览学习包内容，返回元数据和各类型条目数量。"""
        package_path = Path(package_path)
        if not package_path.exists():
            raise FileNotFoundError(f"学习包文件不存在：{package_path}")

        with zipfile.ZipFile(package_path, "r") as zf:
            with zf.open("package.json") as f:
                try:
                    data = json.loads(f.read().decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    raise ValueError("学习包解析失败，文件可能已损坏") from e

            if not isinstance(data, dict) or "meta" not in data:
                raise ValueError("学习包格式错误，缺少 meta 信息")

            meta = data["meta"]
            version = meta.get("version", "")
            if version not in (_PACKAGE_VERSION, "1.0"):
                logger.warning(
                    "Learning package version mismatch: expected %s, got %s",
                    _PACKAGE_VERSION,
                    version,
                )

            # 校验和检查
            stored_checksum = meta.get("checksum", "")
            content = {k: v for k, v in data.items() if k != "meta"}
            actual_checksum = _content_checksum(content)
            checksum_ok = not stored_checksum or stored_checksum == actual_checksum

            attachment_names = [Path(n).name for n in zf.namelist() if n.startswith("attachments/")]

        counts = {"documents": len(data.get("documents", []))}
        for table in _PACKAGE_TABLES:
            counts[table] = len(data.get(table, []))

        return {
            "meta": meta,
            "counts": counts,
            "checksum_ok": checksum_ok,
            "attachment_count": len(attachment_names),
            "attachment_names": attachment_names,
        }

    def import_package(
        self,
        package_path: Path,
        user_id: Optional[int] = None,
        selected: Optional[Dict[str, bool]] = None,
        conflict_strategy: str = "skip",
        progress_callback: Optional[Callable[[int, str], None]] = None,
    ) -> Dict[str, int]:
        """从 zip 文件导入学习包，返回各类型导入数量统计。

        Args:
            selected: 指定要导入的数据类型，None 表示全部导入。
            conflict_strategy: 文档冲突策略，skip 或 overwrite。
        """
        uid = self._user_id(user_id)
        package_path = Path(package_path)
        if not package_path.exists():
            raise FileNotFoundError(f"学习包文件不存在：{package_path}")

        if progress_callback:
            progress_callback(5, "解析学习包...")

        with zipfile.ZipFile(package_path, "r") as zf:
            with zf.open("package.json") as f:
                try:
                    data = json.loads(f.read().decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError) as e:
                    raise ValueError("学习包解析失败，文件可能已损坏") from e

            if not isinstance(data, dict) or "meta" not in data:
                raise ValueError("学习包格式错误，缺少 meta 信息")

            # 校验和检查
            meta = data["meta"]
            stored_checksum = meta.get("checksum", "")
            content = {k: v for k, v in data.items() if k != "meta"}
            actual_checksum = _content_checksum(content)
            if stored_checksum and stored_checksum != actual_checksum:
                raise ValueError("学习包校验和不匹配，文件可能已损坏")

            attachments: Dict[str, bytes] = {}
            for name in zf.namelist():
                if name.startswith("attachments/"):
                    arcname = Path(name).name
                    attachments[arcname] = zf.read(name)

        counts: Dict[str, int] = {t: 0 for t in _PACKAGE_TABLES}
        counts["documents"] = 0

        if selected is None:
            selected = {t: True for t in _PACKAGE_TABLES}
            selected["documents"] = True

        total_steps = len(_PACKAGE_TABLES) + 1
        step = 0

        # 导入数据表
        pending_tasks: List[Dict[str, Any]] = data.get("study_plan_tasks", [])
        for table in _PACKAGE_TABLES:
            step += 1
            if progress_callback:
                progress_callback(int(step / total_steps * 80), f"导入 {table}...")
            if not selected.get(table, False):
                continue
            rows = data.get(table, [])
            if not rows:
                continue
            if table == "study_plans":
                count = self._import_study_plans(rows, pending_tasks, uid)
            else:
                count = self._import_table_rows(table, rows, uid)
            counts[table] = count

        # 导入文档
        step += 1
        if progress_callback:
            progress_callback(int(step / total_steps * 80), "导入文档...")
        doc_rows = data.get("documents", [])
        if selected.get("documents", False) and doc_rows:
            if self.rag_service is not None:
                counts["documents"] = self._import_documents(doc_rows, attachments, uid, conflict_strategy)
            else:
                logger.warning("RAGService not available, skipping document import")

        # 记录学习包元数据
        title = meta.get("title", "导入的学习包")
        description = meta.get("description", "")
        package_id = self.db.insert(
            "INSERT INTO learning_packages (user_id, title, description, content_json, is_shared, share_code) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                uid,
                title,
                description,
                json.dumps(counts, ensure_ascii=False),
                0,
                "",
            ),
        )
        if progress_callback:
            progress_callback(100, "导入完成")

        logger.info(
            "Imported learning package id=%d user_id=%d counts=%s",
            package_id,
            uid,
            counts,
        )
        return counts

    def _import_table_rows(
        self,
        table: str,
        rows: List[Dict[str, Any]],
        user_id: int,
    ) -> int:
        """将单表数据写入数据库，返回插入条数。"""
        if not rows:
            return 0

        if table == "study_plans":
            return self._import_study_plans(rows, [], user_id)
        if table == "study_plan_tasks":
            return 0

        columns = list(rows[0].keys())
        try:
            sample = self.db.fetchone(f"SELECT * FROM {table} LIMIT 0")
            valid_columns = list(sample.keys()) if sample else columns
        except Exception:
            valid_columns = columns

        insert_columns = [c for c in columns if c in valid_columns]
        if not insert_columns:
            return 0

        placeholders = ", ".join(["?"] * (len(insert_columns) + 1))
        col_names = ", ".join(["user_id"] + insert_columns)
        sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"

        count = 0
        for row in rows:
            # 导出时 error_book.knowledge_points 被解析为结构化列表，
            # 入库前需重新序列化为 JSON 字符串（SQLite 不能直接绑定 list）
            if table == "error_book" and isinstance(row.get("knowledge_points"), (list, dict)):
                row = {**row, "knowledge_points": json.dumps(
                    row["knowledge_points"], ensure_ascii=False
                )}
            values = [user_id] + [row.get(c) for c in insert_columns]
            try:
                self.db.execute(sql, tuple(values))
                count += 1
            except Exception as e:
                logger.warning("Import %s row failed: %s", table, e)
        return count

    def _import_study_plans(
        self,
        rows: List[Dict[str, Any]],
        tasks: List[Dict[str, Any]],
        user_id: int,
    ) -> int:
        """导入学习计划及其任务，保持层级关系。"""
        if not rows or self.study_plan_service is None:
            return 0
        count = 0
        for plan in rows:
            try:
                plan_id = self.study_plan_service.create_plan(
                    title=plan.get("title", ""),
                    goal=plan.get("goal", ""),
                    weak_subjects=plan.get("weak_subjects", ""),
                    daily_minutes=int(plan.get("daily_minutes", 30) or 30),
                    start_date=plan.get("start_date"),
                    user_id=user_id,
                )
                count += 1
                valid_tasks = [t for t in tasks if t.get("title")]
                for task in valid_tasks:
                    self.db.execute(
                        "INSERT INTO study_plan_tasks (plan_id, user_id, title, description, "
                        "subject, estimated_minutes, due_date, is_completed) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            plan_id,
                            user_id,
                            task.get("title", ""),
                            task.get("description", ""),
                            task.get("subject", ""),
                            int(task.get("estimated_minutes", 0) or 0),
                            task.get("due_date", ""),
                            int(task.get("is_completed", 0) or 0),
                        ),
                    )
            except Exception as e:
                logger.warning("Import study plan failed: %s", e)
        return count

    def _import_documents(
        self,
        docs: List[Dict[str, Any]],
        attachments: Dict[str, bytes],
        user_id: int,
        conflict_strategy: str = "skip",
    ) -> int:
        """导入文档：解压附件后调用 RAGService.add_document。"""
        if not docs or self.rag_service is None:
            return 0

        existing = {
            row["filename"]
            for row in self.db.fetchall("SELECT filename FROM documents WHERE user_id = ?", (user_id,))
        }

        count = 0
        temp_dir = Path(tempfile.mkdtemp(prefix="pkg_import_"))
        try:
            for doc in docs:
                filename = doc.get("filename", "")
                if not filename:
                    continue
                if filename in existing and conflict_strategy == "skip":
                    logger.info("Skipping existing document: %s", filename)
                    continue
                file_bytes = attachments.get(filename) or attachments.get(Path(filename).name)
                if not file_bytes:
                    logger.warning("Attachment missing for document: %s", filename)
                    continue
                temp_path = temp_dir / filename
                temp_path.write_bytes(file_bytes)
                try:
                    self.rag_service.add_document(temp_path, user_id=user_id)
                    count += 1
                except Exception as e:
                    logger.warning("Import document %s failed: %s", filename, e)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)
        return count

    def list_packages(self, user_id: Optional[int] = None) -> List[dict]:
        """列出当前用户的学习包记录。"""
        uid = self._user_id(user_id)
        return self.db.fetchall(
            "SELECT id, title, description, content_json, is_shared, share_code, created_at, updated_at "
            "FROM learning_packages WHERE user_id = ? ORDER BY updated_at DESC",
            (uid,),
        )

    def delete_package(self, package_id: int, user_id: Optional[int] = None) -> None:
        """删除学习包记录（不影响已导入的实际学习数据）。"""
        uid = self._user_id(user_id)
        row = self.db.fetchone(
            "SELECT id FROM learning_packages WHERE id = ? AND user_id = ?",
            (package_id, uid),
        )
        if row is None:
            raise ValueError(f"学习包不存在：ID={package_id}")
        self.db.execute(
            "DELETE FROM learning_packages WHERE id = ? AND user_id = ?",
            (package_id, uid),
        )
        logger.info("Deleted learning package id=%d user_id=%d", package_id, uid)

    def share_package(
        self,
        package_id: int,
        user_id: Optional[int] = None,
    ) -> str:
        """为学习包生成分享码并标记为可分享。"""
        uid = self._user_id(user_id)
        row = self.db.fetchone(
            "SELECT id FROM learning_packages WHERE id = ? AND user_id = ?",
            (package_id, uid),
        )
        if row is None:
            raise ValueError(f"学习包不存在：ID={package_id}")

        share_code = _generate_share_code()
        self.db.execute(
            "UPDATE learning_packages SET is_shared = ?, share_code = ? WHERE id = ?",
            (1, share_code, package_id),
        )
        logger.info("Generated share code for package id=%d", package_id)
        return share_code

    def get_by_share_code(self, share_code: str) -> Optional[dict]:
        """根据分享码查询学习包元数据（不返回文件内容）。"""
        if not _is_valid_share_code(share_code):
            return None
        return self.db.fetchone(
            "SELECT id, title, description, content_json, share_code, created_at "
            "FROM learning_packages WHERE share_code = ? AND is_shared = 1",
            (share_code,),
        )

    def parse_share_code(self, share_code: str) -> Dict[str, Any]:
        """解析分享码并返回可读信息；若无效则抛出异常。"""
        if not _is_valid_share_code(share_code):
            raise ValueError("分享码格式错误或校验位不匹配")
        info = self.get_by_share_code(share_code)
        if info is None:
            raise ValueError("分享码不存在或对应学习包未启用分享")
        return {
            "valid": True,
            "package_id": info["id"],
            "title": info["title"],
            "description": info["description"],
            "created_at": info["created_at"],
        }
