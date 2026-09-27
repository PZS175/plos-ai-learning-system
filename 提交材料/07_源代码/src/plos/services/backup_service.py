"""数据备份与恢复服务。

支持全量学习数据一键导出/导入（.plosbackup）、自动滚动备份、
错题/笔记分项 JSON 导出，以及数据库完整性校验。
"""

from __future__ import annotations

import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..db import Database, get_db
from ..utils.logger import get_logger
from ..utils.paths import get_data_dir
from .errorbook_service import ErrorBookService
from .flashcard_service import FlashcardService
from .user_service import UserService

logger = get_logger("services.backup_service")

BACKUP_VERSION = "1.0"
BACKUP_META_NAME = "metadata.json"
BACKUP_DATA_NAME = "backup.json"


# 全量备份覆盖的数据表（顺序兼顾外键依赖：父表在前、子表在后）
_BACKUP_TABLES = [
    "users",
    "settings",
    "conversations",
    "messages",
    "documents",
    "chunks",
    "error_book",
    "flashcards",
    "study_logs",
    "statistics_records",
    "user_stat",
    "study_plans",
    "study_plan_tasks",
    "study_plan_adjustments",
    "note_annotations",
    "mindmaps",
    "terms",
    "subjective_grades",
    "practice_questions",
    "practice_records",
    "exam_papers",
    "exam_paper_questions",
    "asr_records",
    "diagram_records",
    "ocr_records",
    "learning_packages",
    "teaching_style_config",
    # 新增模块用户数据表
    "notes",
    "review_schedules",
    "textbook_cache",
]

# 不含 user_id 列、需要特殊过滤条件的表
_NO_USER_ID_TABLES = {"settings", "users", "exam_paper_questions"}


class BackupService:
    """学习数据备份与恢复服务。"""

    def __init__(
        self,
        db: Optional[Database] = None,
        user_service: Optional[UserService] = None,
        errorbook_service: Optional[ErrorBookService] = None,
        flashcard_service: Optional[FlashcardService] = None,
    ):
        self.db = db or get_db()
        self.user_service = user_service
        self.errorbook_service = errorbook_service
        self.flashcard_service = flashcard_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def run_auto_backup(self, keep: Optional[int] = None) -> Optional[Path]:
        """自动滚动备份（仅 SQLite 主库，轻量快速）。

        频率由 settings 的 ``auto_backup_frequency`` 控制：
        ``daily``（默认）每天一次、``weekly`` 每周一次、``off`` 关闭。
        保留份数由 ``auto_backup_keep_count`` 控制，默认 5 份。
        返回备份路径或 None（未触发）。
        """
        from datetime import timedelta

        freq = self._get_setting("auto_backup_frequency") or "daily"
        if freq == "off":
            return None

        keep_count = keep
        if keep_count is None:
            raw = self._get_setting("auto_backup_keep_count")
            try:
                keep_count = int(raw) if raw else 5
            except ValueError:
                keep_count = 5

        now = datetime.now()
        last_str = self._get_setting("auto_backup_last_date")
        try:
            last = datetime.strptime(last_str, "%Y-%m-%d") if last_str else None
        except ValueError:
            last = None

        # 频率判定：daily 按天、weekly 按 7 天
        if last is not None:
            if freq == "weekly":
                if now - last < timedelta(days=7):
                    return None
            else:  # daily
                if now.date() == last.date():
                    return None

        backup_dir = get_data_dir() / "backups" / "auto"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y%m%d_%H%M%S")
        target = backup_dir / f"plos_auto_{stamp}.plosbackup"

        # 复用全量导出逻辑生成 .plosbackup
        try:
            self.export_full_backup(target)
        except Exception as e:
            logger.error("Auto backup failed: %s", e)
            target.unlink(missing_ok=True)
            return None

        self._set_setting("auto_backup_last_date", now.strftime("%Y-%m-%d"))
        self._prune_auto_backups(backup_dir, keep=keep_count)
        logger.info("Auto backup created: %s", target)
        return target

    def _get_setting(self, key: str) -> str:
        try:
            row = self.db.fetchone(
                "SELECT value FROM settings WHERE key = ?", (key,)
            )
            return row["value"] if row else ""
        except Exception:
            return ""

    def _set_setting(self, key: str, value: str) -> None:
        try:
            self.db.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
        except Exception as e:
            logger.warning("Failed to persist setting %s: %s", key, e)

    def _prune_auto_backups(self, backup_dir: Path, keep: int) -> None:
        """只保留最近 keep 份自动备份。"""
        try:
            # 兼容历史版本生成的 .db 自动备份，否则旧格式文件永远不会被清理，
            # 「最多保留 N 份」的保留策略会失效
            files = sorted(
                list(backup_dir.glob("plos_auto_*.plosbackup"))
                + list(backup_dir.glob("plos_auto_*.db"))
            )
            for stale in files[:-keep] if keep > 0 else files:
                stale.unlink(missing_ok=True)
        except Exception as e:
            logger.warning("Failed to prune auto backups: %s", e)

    def export_full_backup(self, output_path: Path, user_id: Optional[int] = None) -> Path:
        """导出全量备份（JSON 压缩包），返回最终文件路径。"""
        uid = self._user_id(user_id)
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        data: Dict[str, List[dict]] = {}
        conn = self.db.get_connection()
        for table in _BACKUP_TABLES:
            try:
                if table == "settings":
                    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
                elif table == "users":
                    # 用户表使用 id 作为当前用户标识
                    rows = conn.execute(
                        f"SELECT * FROM {table} WHERE id = ?", (uid,)
                    ).fetchall()
                elif table == "exam_paper_questions":
                    # 该表无 user_id，通过试卷归属间接过滤当前用户
                    rows = conn.execute(
                        "SELECT q.* FROM exam_paper_questions q "
                        "JOIN exam_papers p ON q.paper_id = p.id "
                        "WHERE p.user_id = ?",
                        (uid,),
                    ).fetchall()
                else:
                    rows = conn.execute(
                        f"SELECT * FROM {table} WHERE user_id = ?", (uid,)
                    ).fetchall()
                data[table] = [dict(row) for row in rows]
            except Exception as e:
                logger.warning("Backup table %s failed: %s", table, e)
                data[table] = []

        # 递归打包数据目录下的附件（图片、文档等，实际附件在 attachments/ 子目录）
        data_dir = get_data_dir().resolve()
        attachments: List[Path] = []
        for pattern in ["*.png", "*.jpg", "*.jpeg", "*.pdf", "*.docx", "*.txt"]:
            attachments.extend(data_dir.rglob(pattern))

        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # 写入元数据（版本、时间、归属用户），便于导入时校验兼容性
            metadata = {
                "version": BACKUP_VERSION,
                "created_at": datetime.now().isoformat(),
                "user_id": uid,
                "tables": list(data.keys()),
                "row_counts": {t: len(rows) for t, rows in data.items()},
            }
            zf.writestr(BACKUP_META_NAME, json.dumps(metadata, ensure_ascii=False, indent=2))
            zf.writestr(BACKUP_DATA_NAME, json.dumps(data, ensure_ascii=False, indent=2))
            for att in attachments:
                # 保留相对目录结构（如 attachments/xxx.png），统一用正斜杠
                rel_str = str(att.relative_to(data_dir)).replace("\\", "/")
                # rel 本身已含 attachments/ 前缀时不能重复拼接，
                # 否则备份每恢复一次，磁盘上就多嵌套一层 attachments/attachments/...
                arcname = rel_str if rel_str.startswith("attachments/") else "attachments/" + rel_str
                zf.write(att, arcname)

        logger.info("Exported full backup to %s", output_path)
        return output_path

    def import_full_backup(self, backup_path: Path) -> int:
        """从备份文件恢复数据，返回恢复的用户数据条数估算。

        导入前会先把当前数据库文件复制到临时位置作为安全网；
        若导入过程中出现任何错误，会从安全网恢复，保证不破坏现有数据。
        """
        import tempfile

        backup_path = Path(backup_path)
        if not backup_path.exists():
            raise FileNotFoundError(f"备份文件不存在：{backup_path}")

        # ---- 安全网：导入前快照当前数据库，失败可回滚 ----
        safety_net: Optional[Path] = None
        try:
            safety_net = Path(tempfile.mkdtemp()) / "plos_safety.db"
            shutil.copy2(self.db.db_path, safety_net)
        except Exception as e:
            logger.warning("Failed to create safety net backup, will proceed without rollback: %s", e)
            safety_net = None

        try:
            with zipfile.ZipFile(backup_path, "r") as zf:
                # 读取元数据（兼容旧版无 metadata.json 的备份）
                meta: Dict = {}
                if BACKUP_META_NAME in zf.namelist():
                    try:
                        meta = json.loads(zf.read(BACKUP_META_NAME).decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        logger.warning("Backup metadata unreadable: %s", e)
                logger.info("Importing backup version=%s user_id=%s", meta.get("version", "0.0"), meta.get("user_id"))

                data_name = BACKUP_DATA_NAME if BACKUP_DATA_NAME in zf.namelist() else "backup.json"
                with zf.open(data_name) as f:
                    try:
                        data = json.loads(f.read().decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError) as e:
                        raise ValueError("备份文件解析失败，文件可能已损坏") from e
                if not isinstance(data, dict):
                    raise ValueError("备份文件格式错误，缺少数据字典")

                # 解压附件（Zip Slip 防护 + 旧版双前缀折叠）
                data_dir = get_data_dir().resolve()
                for member in zf.namelist():
                    if not member.startswith("attachments/"):
                        continue
                    rel_name = member
                    while rel_name.startswith("attachments/attachments/"):
                        rel_name = rel_name[len("attachments/"):]
                    target = (data_dir / rel_name).resolve()
                    if data_dir not in target.parents and target != data_dir:
                        logger.warning("Skip unsafe backup entry (path traversal): %s", member)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)

            # 数据导入
            count = 0
            failed_tables = []
            for table in _BACKUP_TABLES:
                rows = data.get(table, [])
                if not rows:
                    continue
                try:
                    with self.db.transaction() as conn:
                        conn.execute("PRAGMA defer_foreign_keys = ON")
                        real_cols = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                        if not real_cols:
                            raise RuntimeError(f"目标库中不存在表：{table}")
                        columns = [c for c in rows[0].keys() if c in real_cols]
                        placeholders = ", ".join(["?"] * len(columns))
                        col_names = ", ".join(columns)
                        # 时点恢复：对“按 user_id 隔离”的业务表，先清除备份所属用户
                        # 在库中的现存行（含备份之后新增的数据），再写入备份行，
                        # 使该用户数据精确回到备份时刻；不影响其他用户。
                        if table not in _NO_USER_ID_TABLES and "user_id" in real_cols:
                            uids = {
                                row.get("user_id")
                                for row in rows
                                if row.get("user_id") is not None
                            }
                            if uids:
                                uid_ph = ",".join("?" for _ in uids)
                                conn.execute(
                                    f"DELETE FROM {table} WHERE user_id IN ({uid_ph})",
                                    tuple(uids),
                                )
                        pk_cols = [c for c in columns if c in {
                            r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall() if r["pk"]
                        }]
                        if pk_cols:
                            for row in rows:
                                conds = " AND ".join(f"{c} = ?" for c in pk_cols)
                                conn.execute(f"DELETE FROM {table} WHERE {conds}", tuple(row.get(c) for c in pk_cols))
                        insert_sql = f"INSERT INTO {table} ({col_names}) VALUES ({placeholders})"
                        for row in rows:
                            conn.execute(insert_sql, tuple(row.get(c) for c in columns))
                            count += 1
                except Exception as e:
                    failed_tables.append(table)
                    logger.warning("Restore table %s failed: %s", table, e)

            if failed_tables:
                raise RuntimeError(f"以下数据表恢复失败：{', '.join(failed_tables)}")

            logger.info("Imported full backup: %d rows", count)
            return count

        except Exception:
            # 任何阶段失败：回滚数据库安全网后抛出
            if safety_net is not None:
                try:
                    shutil.copy2(safety_net, self.db.db_path)
                    logger.info("Restored database from safety net after import failure")
                except Exception as e:
                    logger.error("Failed to restore safety net: %s", e)
            raise
        finally:
            if safety_net is not None:
                try:
                    safety_net.unlink(missing_ok=True)
                    safety_net.parent.rmdir()
                except Exception:
                    pass

    def export_errors_to_markdown(self, file_path: Path, user_id: Optional[int] = None) -> int:
        """导出错题为 Markdown。"""
        if self.errorbook_service is None:
            raise RuntimeError("ErrorBookService not available")
        return self.errorbook_service.export_to_markdown(file_path, user_id=user_id)

    def export_flashcards_to_markdown(self, file_path: Path, user_id: Optional[int] = None) -> int:
        """导出闪卡为 Markdown。"""
        if self.flashcard_service is None:
            raise RuntimeError("FlashcardService not available")
        return self.flashcard_service.export_to_markdown(file_path, user_id=user_id)

    def check_database_integrity(self) -> tuple[bool, str]:
        """校验数据库完整性，返回 (是否正常, 问题描述)。

        使用 SQLite 的 PRAGMA integrity_check，正常时返回 (True, "ok")。
        """
        try:
            row = self.db.get_connection().execute("PRAGMA integrity_check").fetchone()
            result = row[0] if row else "unknown"
            if result == "ok":
                return True, "ok"
            return False, str(result)
        except Exception as e:
            logger.error("Database integrity check failed: %s", e)
            return False, str(e)

    def export_errorbook_json(self, file_path: Path, user_id: Optional[int] = None) -> int:
        """分项导出：当前账号错题本为 JSON 文件，返回导出错题条数。"""
        uid = self._user_id(user_id)
        rows = self.db.fetchall(
            "SELECT * FROM error_book WHERE user_id = ? ORDER BY updated_at DESC", (uid,)
        )
        Path(file_path).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        logger.info("Exported %d errors to JSON: %s", len(rows), file_path)
        return len(rows)

    def export_notes_json(self, file_path: Path, user_id: Optional[int] = None) -> int:
        """分项导出：当前账号富文本笔记为 JSON 文件，返回导出条数。"""
        uid = self._user_id(user_id)
        rows = self.db.fetchall(
            "SELECT * FROM notes WHERE user_id = ? ORDER BY updated_at DESC", (uid,)
        )
        Path(file_path).write_text(
            json.dumps(rows, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
        )
        logger.info("Exported %d notes to JSON: %s", len(rows), file_path)
        return len(rows)
