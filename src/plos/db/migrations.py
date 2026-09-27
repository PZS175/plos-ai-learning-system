"""版本化数据库迁移框架。

以 ``PRAGMA user_version`` 记录当前 schema 版本，按序执行 MIGRATIONS 中
版本号更大的迁移步骤。所有迁移必须幂等：旧库从 0 逐级升级，新库由
schema.sql 建立基线后直接跳到最新版本。

新增迁移的规则：
1. 只能追加新步骤，不能修改既有步骤（否则新旧库升级路径分叉）；
2. 用 ``IF NOT EXISTS`` / 列存在性检查保证幂等；
3. version 必须严格递增。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable, List

from ..utils.logger import get_logger

logger = get_logger("db.migrations")


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: Callable[[object], None]


def _legacy_schema_patches(db) -> None:
    """历史 schema 补丁合集（幂等）：列补齐、兼容表重建。"""
    migrations = [
        ("error_book", "user_id", "INTEGER DEFAULT 0"),
        ("error_book", "subject", "TEXT DEFAULT ''"),
        ("error_book", "chapter", "TEXT DEFAULT ''"),
        ("error_book", "knowledge_point", "TEXT DEFAULT ''"),
        ("error_book", "difficulty", "INTEGER DEFAULT 1"),
        ("flashcards", "user_id", "INTEGER DEFAULT 0"),
        ("flashcards", "subject", "TEXT DEFAULT ''"),
        ("flashcards", "tags", "TEXT DEFAULT ''"),
        ("conversations", "user_id", "INTEGER DEFAULT 0"),
        ("messages", "user_id", "INTEGER DEFAULT 0"),
        ("documents", "user_id", "INTEGER DEFAULT 0"),
        ("documents", "is_favorite", "INTEGER DEFAULT 0"),
        ("chunks", "user_id", "INTEGER DEFAULT 0"),
        ("study_logs", "user_id", "INTEGER DEFAULT 0"),
        ("error_book", "is_favorite", "INTEGER DEFAULT 0"),
        ("documents", "is_favorite", "INTEGER DEFAULT 0"),
        ("error_book", "knowledge_points", "TEXT DEFAULT '[]'"),
        ("error_book", "question_type", "TEXT DEFAULT ''"),
        ("chunks", "chapter", "TEXT DEFAULT ''"),
        ("chunks", "level", "INTEGER DEFAULT 0"),
        ("users", "password_hash", "TEXT DEFAULT ''"),
        # 自适应练习（高优先级2）：旧 practice_records 表补充 knowledge_point
        ("practice_records", "knowledge_point", "TEXT DEFAULT ''"),
        ("practice_records", "feedback", "TEXT DEFAULT ''"),
        # 教学风格精细配置：新增多维配置 JSON
        ("teaching_style_config", "config_json", "TEXT DEFAULT '{}'"),
    ]
    conn = db.get_connection()
    for table, column, dtype in migrations:
        if db._column_exists(table, column):
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {dtype}")
            logger.info("Migrated: added %s.%s", table, column)
        except sqlite3.Error as e:
            logger.error("Migration failed for %s.%s: %s", table, column, e)
    try:
        conn.commit()
    except sqlite3.Error as e:
        logger.error("Failed to commit migration: %s", e)

    # 确保用户学习统计表存在（旧数据库兼容）
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS user_stat ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 0, "
            "knowledge_point TEXT NOT NULL DEFAULT '', "
            "error_count INTEGER NOT NULL DEFAULT 0, "
            "total_count INTEGER NOT NULL DEFAULT 0, "
            "error_rate REAL NOT NULL DEFAULT 0.0, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_stat_user_point ON user_stat(user_id, knowledge_point)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_user_stat_user_rate ON user_stat(user_id, error_rate)"
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.error("Failed to create user_stat table: %s", e)

    # 自动出题与练习记录表（高优先级2，旧库兼容迁移）
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS practice_questions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 0, "
            "knowledge_point TEXT NOT NULL DEFAULT '', "
            "question_type TEXT NOT NULL DEFAULT 'choice' "
            "CHECK(question_type IN ('choice', 'fill', 'judge', 'short', 'open')), "
            "difficulty INTEGER NOT NULL DEFAULT 3, "
            "question TEXT NOT NULL DEFAULT '', "
            "options TEXT NOT NULL DEFAULT '[]', "
            "answer TEXT NOT NULL DEFAULT '', "
            "analysis TEXT NOT NULL DEFAULT '', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_practice_questions_user ON practice_questions(user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_practice_questions_kp ON practice_questions(user_id, knowledge_point)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS practice_records ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 0, "
            "question_id INTEGER NOT NULL DEFAULT 0, "
            "knowledge_point TEXT NOT NULL DEFAULT '', "
            "user_answer TEXT NOT NULL DEFAULT '', "
            "is_correct INTEGER NOT NULL DEFAULT 0, "
            "score REAL NOT NULL DEFAULT 0, "
            "feedback TEXT NOT NULL DEFAULT '', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_practice_records_user ON practice_records(user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_practice_records_question ON practice_records(question_id)"
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.error("Failed to create practice tables: %s", e)

    # 旧版 practice_records（来自已移除的练习会话模块）含 session_id NOT NULL
    # 且无默认值，会阻断新代码插入。重建为新结构并保留旧数据。
    try:
        cursor = conn.execute("PRAGMA table_info(practice_records)")
        columns = {row[1] for row in cursor.fetchall()}
        if "session_id" in columns:
            conn.commit()  # PRAGMA 需在事务外生效
            conn.execute("PRAGMA foreign_keys = OFF")
            try:
                conn.execute("DROP TABLE IF EXISTS practice_records_migrate_tmp")
                conn.execute(
                    "CREATE TABLE practice_records_migrate_tmp ("
                    "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                    "user_id INTEGER NOT NULL DEFAULT 0, "
                    "question_id INTEGER NOT NULL DEFAULT 0, "
                    "knowledge_point TEXT NOT NULL DEFAULT '', "
                    "user_answer TEXT NOT NULL DEFAULT '', "
                    "is_correct INTEGER NOT NULL DEFAULT 0, "
                    "score REAL NOT NULL DEFAULT 0, "
                    "feedback TEXT NOT NULL DEFAULT '', "
                    "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
                )
                conn.execute(
                    "INSERT INTO practice_records_migrate_tmp "
                    "(user_id, question_id, knowledge_point, user_answer, is_correct, score, feedback, created_at) "
                    "SELECT user_id, question_id, '', user_answer, is_correct, score, '', created_at "
                    "FROM practice_records"
                )
                conn.execute("DROP TABLE practice_records")
                conn.execute(
                    "ALTER TABLE practice_records_migrate_tmp RENAME TO practice_records"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_practice_records_user ON practice_records(user_id)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_practice_records_question ON practice_records(question_id)"
                )
                conn.commit()
                logger.info(
                    "Migrated legacy practice_records to new schema (data preserved)"
                )
            finally:
                conn.execute("PRAGMA foreign_keys = ON")
    except sqlite3.Error as e:
        logger.error("Failed to rebuild legacy practice_records: %s", e)

    # 试卷生成与导出表（高优先级3，旧库兼容迁移）
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS exam_papers ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 0, "
            "title TEXT NOT NULL DEFAULT '', "
            "knowledge_points TEXT NOT NULL DEFAULT '[]', "
            "type_counts TEXT NOT NULL DEFAULT '{}', "
            "difficulty_min INTEGER NOT NULL DEFAULT 1, "
            "difficulty_max INTEGER NOT NULL DEFAULT 5, "
            "total_score INTEGER NOT NULL DEFAULT 0, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exam_papers_user ON exam_papers(user_id)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS exam_paper_questions ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "paper_id INTEGER NOT NULL, "
            "question_id INTEGER NOT NULL, "
            "sort_order INTEGER NOT NULL DEFAULT 0, "
            "score INTEGER NOT NULL DEFAULT 0, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_exam_paper_questions_paper "
            "ON exam_paper_questions(paper_id)"
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.error("Failed to create exam tables: %s", e)

    # 主观题批改记录表（高优先级4，旧库兼容迁移）
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS subjective_grades ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 0, "
            "error_id INTEGER NOT NULL DEFAULT 0, "
            "question_id INTEGER NOT NULL DEFAULT 0, "
            "user_answer TEXT NOT NULL DEFAULT '', "
            "score REAL NOT NULL DEFAULT 0, "
            "total_score REAL NOT NULL DEFAULT 0, "
            "scoring_points TEXT NOT NULL DEFAULT '[]', "
            "lost_points TEXT NOT NULL DEFAULT '[]', "
            "error_reasons TEXT NOT NULL DEFAULT '[]', "
            "improvement TEXT NOT NULL DEFAULT '', "
            "feedback TEXT NOT NULL DEFAULT '', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_subjective_grades_user ON subjective_grades(user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_subjective_grades_error ON subjective_grades(error_id)"
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.error("Failed to create subjective_grades table: %s", e)

    # 学习计划动态调优变更记录表（高优先级5，旧库兼容迁移）
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS study_plan_adjustments ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "plan_id INTEGER NOT NULL, "
            "user_id INTEGER NOT NULL DEFAULT 0, "
            "trigger_type TEXT NOT NULL DEFAULT 'error_added', "
            "trigger_desc TEXT NOT NULL DEFAULT '', "
            "adjustment_desc TEXT NOT NULL DEFAULT '', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_study_plan_adjustments_plan ON study_plan_adjustments(plan_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_study_plan_adjustments_user ON study_plan_adjustments(user_id)"
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.error("Failed to create study_plan_adjustments table: %s", e)

    # 低优先级功能预留数据表（旧库兼容迁移）
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS asr_records ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 0, "
            "audio_path TEXT NOT NULL DEFAULT '', "
            "transcript TEXT NOT NULL DEFAULT '', "
            "duration_seconds REAL NOT NULL DEFAULT 0, "
            "status TEXT NOT NULL DEFAULT 'pending', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_asr_records_user ON asr_records(user_id)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS diagram_records ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 0, "
            "prompt TEXT NOT NULL DEFAULT '', "
            "diagram_type TEXT NOT NULL DEFAULT 'flowchart', "
            "source_type TEXT NOT NULL DEFAULT '', "
            "source_id INTEGER NOT NULL DEFAULT 0, "
            "mermaid_code TEXT NOT NULL DEFAULT '', "
            "svg_data TEXT NOT NULL DEFAULT '', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_diagram_records_user ON diagram_records(user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_diagram_records_source ON diagram_records(source_type, source_id)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS learning_packages ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 0, "
            "title TEXT NOT NULL DEFAULT '', "
            "description TEXT NOT NULL DEFAULT '', "
            "content_json TEXT NOT NULL DEFAULT '{}', "
            "is_shared INTEGER NOT NULL DEFAULT 0, "
            "share_code TEXT NOT NULL DEFAULT '', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_learning_packages_user ON learning_packages(user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_learning_packages_share ON learning_packages(share_code)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS teaching_style_config ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 0 UNIQUE, "
            "style TEXT NOT NULL DEFAULT 'encouraging', "
            "enabled INTEGER NOT NULL DEFAULT 0, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_teaching_style_user ON teaching_style_config(user_id)"
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.error("Failed to create low priority placeholder tables: %s", e)

    # 术语词典表（中优先级4，旧库兼容迁移）
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS terms ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "user_id INTEGER NOT NULL DEFAULT 0, "
            "term TEXT NOT NULL DEFAULT '', "
            "definition TEXT NOT NULL DEFAULT '', "
            "source_type TEXT NOT NULL DEFAULT '', "
            "source_id INTEGER NOT NULL DEFAULT 0, "
            "context TEXT NOT NULL DEFAULT '', "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_terms_user ON terms(user_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_terms_term ON terms(term)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_terms_source ON terms(source_type, source_id)"
        )
        conn.commit()
    except sqlite3.Error as e:
        logger.error("Failed to create terms table: %s", e)



def _hot_query_indices(db) -> None:
    """高频查询索引：按用户维度检索与按时间排序的主要访问路径。"""
    db.get_connection().executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_error_book_user ON error_book(user_id);
        CREATE INDEX IF NOT EXISTS idx_error_book_user_created ON error_book(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_error_book_user_mastery ON error_book(user_id, mastery_level);
        CREATE INDEX IF NOT EXISTS idx_flashcards_user ON flashcards(user_id);
        CREATE INDEX IF NOT EXISTS idx_flashcards_user_review ON flashcards(user_id, next_review);
        CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_messages_user ON messages(user_id);
        CREATE INDEX IF NOT EXISTS idx_documents_user ON documents(user_id);
        CREATE INDEX IF NOT EXISTS idx_chunks_user ON chunks(user_id);
        CREATE INDEX IF NOT EXISTS idx_study_logs_user_created ON study_logs(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_statistics_user_type_date
            ON statistics_records(user_id, record_type, record_date);
        CREATE INDEX IF NOT EXISTS idx_practice_records_user_kp
            ON practice_records(user_id, knowledge_point);
        CREATE INDEX IF NOT EXISTS idx_exam_papers_user ON exam_papers(user_id);
        CREATE INDEX IF NOT EXISTS idx_ocr_records_user ON ocr_records(user_id);
        CREATE INDEX IF NOT EXISTS idx_study_plan_tasks_plan ON study_plan_tasks(plan_id);
        """
    )


def _user_stat_unique(db) -> None:
    """user_stat 去重并建 (user_id, knowledge_point) 唯一索引。

    唯一索引是统计 UPSERT 原子自增的前提；历史数据先按组保留最小 id。
    """
    conn = db.get_connection()
    conn.executescript(
        """
        DELETE FROM user_stat
        WHERE id NOT IN (
            SELECT MIN(id) FROM user_stat GROUP BY user_id, knowledge_point
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_user_stat_user_point_uq
            ON user_stat(user_id, knowledge_point);
        """
    )
    conn.commit()


def _readable_latex_content(db) -> None:
    """存量学习内容里的 LaTeX 记号统一转成可读 Unicode（一次性迁移）。

    覆盖闪卡、错题本、主观批改与练习记录反馈，保证复习/列表/编辑/导出/
    搜索等所有读库入口不再出现裸 LaTeX 命令。

    注意：practice_questions 刻意不迁移——试卷导出依赖库中保留的
    \\frac 结构才能在 Word/PDF 里渲染成上下结构的真分数。
    """
    import json

    from ..utils.latex_clean import latex_to_unicode

    # (表名, ((列名, 是否为 JSON 数组), ...))
    text_tables = [
        ("flashcards", (("front_content", False), ("back_content", False))),
        (
            "error_book",
            (("question", False), ("answer", False), ("analysis", False)),
        ),
        ("practice_records", (("feedback", False),)),
        (
            "subjective_grades",
            (
                ("improvement", False),
                ("feedback", False),
                ("scoring_points", True),
                ("lost_points", True),
                ("error_reasons", True),
            ),
        ),
    ]

    def _clean(value: object) -> object:
        """清洗单个字符串；非字符串或空串原样返回。"""
        if not isinstance(value, str) or not value:
            return value
        try:
            return latex_to_unicode(value)
        except Exception:
            return value

    def _clean_json_list(value: str) -> str:
        """JSON 数组里的每个字符串元素分别清洗后回写。"""
        try:
            items = json.loads(value) if value else []
        except Exception:
            return value
        if not isinstance(items, list):
            return value
        cleaned = [_clean(str(item)) for item in items]
        return json.dumps(cleaned, ensure_ascii=False)

    for table, fields in text_tables:
        conn = db.get_connection()
        try:
            cols = ", ".join(col for col, _ in fields)
            rows = conn.execute(f"SELECT id, {cols} FROM {table}").fetchall()
        except sqlite3.Error as e:
            logger.warning("Skipped latex cleanup for %s: %s", table, e)
            continue
        try:
            for row in rows:
                updates = {}
                for col, is_list in fields:
                    raw = row[col]
                    cleaned = _clean_json_list(raw) if is_list else _clean(raw)
                    if cleaned != raw:
                        updates[col] = cleaned
                if updates:
                    set_sql = ", ".join(f"{c} = ?" for c in updates)
                    conn.execute(
                        f"UPDATE {table} SET {set_sql} WHERE id = ?",
                        (*updates.values(), row["id"]),
                    )
            conn.commit()
        except sqlite3.Error as e:
            conn.rollback()
            logger.error("Failed latex cleanup for %s: %s", table, e)
        logger.info("Cleanup latex content done for table=%s rows=%d", table, len(rows))


def _multiuser_password_hash(db) -> None:
    """多用户账号系统：为已存在的 users 表补充 password_hash 列。

    说明：旧库 user_version 已到 4，向旧迁移函数追加的列不会重跑，
    因此独立成版本 5。列存在性检查保证幂等。
    """
    conn = db.get_connection()
    for table, column, dtype in [
        ("users", "password_hash", "TEXT DEFAULT ''"),
    ]:
        if db._column_exists(table, column):
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {dtype}")
            logger.info("Migrated: added %s.%s", table, column)
        except sqlite3.Error as e:
            logger.error("Migration failed for %s.%s: %s", table, column, e)
    try:
        conn.commit()
    except sqlite3.Error as e:
        logger.error("Failed to commit migration: %s", e)


MIGRATIONS: List[Migration] = [
    Migration(1, "legacy_schema_patches", _legacy_schema_patches),
    Migration(2, "hot_query_indices", _hot_query_indices),
    Migration(3, "user_stat_unique", _user_stat_unique),
    Migration(4, "readable_latex_content", _readable_latex_content),
    Migration(5, "multiuser_password_hash", _multiuser_password_hash),
]

LATEST_VERSION = MIGRATIONS[-1].version


def get_schema_version(db) -> int:
    """读取当前 schema 版本（PRAGMA user_version）。"""
    row = db.get_connection().execute("PRAGMA user_version").fetchone()
    return int(row[0]) if row else 0


def run_migrations(db) -> int:
    """按序执行未应用的迁移，返回实际执行的步数。失败时抛出异常。"""
    current = get_schema_version(db)
    applied = 0
    for migration in MIGRATIONS:
        if migration.version <= current:
            continue
        try:
            migration.apply(db)
            conn = db.get_connection()
            conn.execute(f"PRAGMA user_version = {migration.version}")
            conn.commit()
            applied += 1
            logger.info("Migration %s (%s) applied", migration.version, migration.name)
        except Exception as e:
            logger.error("Migration %s (%s) failed: %s", migration.version, migration.name, e)
            raise
    return applied
