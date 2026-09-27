"""PLOS AI 命令行工具（无 Qt 依赖，可用于服务器/脚本化运维）。

用法：
    python -m plos.cli doctor [--with-models]
    python -m plos.cli backup
    python -m plos.cli migrate
    python -m plos.cli version

子命令：
    doctor    体检：数据库完整性、schema 版本、孤儿行、目录可写性、模型可达性
    backup    立即执行一次自动滚动备份
    migrate   手动执行待应用的数据库迁移
    version   打印版本信息
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional, Tuple

CHECK_OK = "OK"
CHECK_WARN = "WARN"
CHECK_FAIL = "FAIL"


def _db_path() -> Path:
    from .utils.paths import get_db_path

    return get_db_path()


def _check_database(db) -> List[Tuple[str, str, str]]:
    """数据库完整性、版本与孤儿行检查。返回 (状态, 名称, 详情)。"""
    results: List[Tuple[str, str, str]] = []

    row = db.get_connection().execute("PRAGMA integrity_check").fetchone()
    integrity = str(row[0])
    results.append(
        (CHECK_OK if integrity == "ok" else CHECK_FAIL, "数据库完整性", integrity)
    )

    from .db.migrations import LATEST_VERSION, get_schema_version

    version = get_schema_version(db)
    if version == LATEST_VERSION:
        results.append((CHECK_OK, "Schema 版本", f"v{version}（最新）"))
    elif version < LATEST_VERSION:
        results.append((CHECK_WARN, "Schema 版本", f"v{version} < 最新 v{LATEST_VERSION}，运行 `migrate` 升级"))
    else:
        results.append((CHECK_FAIL, "Schema 版本", f"v{version} 高于已知最新 v{LATEST_VERSION}"))

    orphans = {
        "messages→conversations": (
            "SELECT COUNT(*) AS c FROM messages WHERE conversation_id NOT IN (SELECT id FROM conversations)"
        ),
        "chunks→documents": (
            "SELECT COUNT(*) AS c FROM chunks WHERE document_id NOT IN (SELECT id FROM documents)"
        ),
        "practice_records→practice_questions": (
            "SELECT COUNT(*) AS c FROM practice_records WHERE question_id NOT IN (SELECT id FROM practice_questions)"
        ),
        "exam_paper_questions→exam_papers": (
            "SELECT COUNT(*) AS c FROM exam_paper_questions WHERE paper_id NOT IN (SELECT id FROM exam_papers)"
        ),
    }
    for name, sql in orphans.items():
        try:
            count = db.fetchone(sql)["c"]
            status = CHECK_OK if count == 0 else CHECK_WARN
            results.append((status, f"孤儿行 {name}", f"{count} 条"))
        except Exception as e:
            results.append((CHECK_WARN, f"孤儿行 {name}", f"无法检查：{e}"))
    return results


def _check_paths() -> List[Tuple[str, str, str]]:
    """关键目录可写性与配置可解析性。"""
    results: List[Tuple[str, str, str]] = []
    from .utils.paths import get_data_dir

    data_dir = get_data_dir()
    try:
        probe = data_dir / ".doctor_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        results.append((CHECK_OK, "数据目录可写", str(data_dir)))
    except Exception as e:
        results.append((CHECK_FAIL, "数据目录可写", f"{data_dir}（{e}）"))

    db_file = _db_path()
    if db_file.exists():
        results.append((CHECK_OK, "数据库文件", str(db_file)))
    else:
        results.append((CHECK_WARN, "数据库文件", f"{db_file}（不存在，首次运行时创建）"))
    return results


def _check_models() -> List[Tuple[str, str, str]]:
    """Ollama 后端可达性（网络检查失败降级为 WARN）。"""
    results: List[Tuple[str, str, str]] = []
    host = "http://127.0.0.1:11434"
    try:
        from .config import load_config
        from .core.constants import DEFAULT_OLLAMA_HOST

        host = load_config().get("backend", {}).get("ollama_host", DEFAULT_OLLAMA_HOST)
    except Exception:
        pass
    try:
        import httpx

        resp = httpx.get(f"{host.rstrip('/')}/api/tags", timeout=2.0)
        if resp.status_code == 200:
            models = [m.get("name", "?") for m in resp.json().get("models", [])]
            results.append((CHECK_OK, "Ollama 服务", f"{host}（{len(models)} 个模型）"))
        else:
            results.append((CHECK_WARN, "Ollama 服务", f"{host} 返回 HTTP {resp.status_code}"))
    except Exception as e:
        results.append((CHECK_WARN, "Ollama 服务", f"{host} 不可达（{type(e).__name__}），离线功能不受影响"))
    return results


def cmd_doctor(with_models: bool = False) -> int:
    """执行全量体检并打印报告。返回退出码：0 健康，1 存在 FAIL。"""
    from .db import Database

    checks: List[Tuple[str, str, str]] = []
    db: Optional[object] = None
    try:
        db = Database(db_path=_db_path())
        checks.extend(_check_database(db))
    except Exception as e:
        checks.append((CHECK_FAIL, "数据库连接", str(e)))
    finally:
        if db is not None and hasattr(db, "close"):
            db.close()
    checks.extend(_check_paths())
    if with_models:
        checks.extend(_check_models())

    print("=" * 62)
    print("PLOS AI 体检报告")
    print("=" * 62)
    exit_code = 0
    for status, name, detail in checks:
        mark = {CHECK_OK: "[✓]", CHECK_WARN: "[!]", CHECK_FAIL: "[✗]"}[status]
        print(f"{mark} {name}: {detail}")
        if status == CHECK_FAIL:
            exit_code = 1
    print("=" * 62)
    print("结论：", "存在问题，请处理 [✗] 项" if exit_code else "全部通过")
    return exit_code


def cmd_backup() -> int:
    """立即执行一次滚动备份。"""
    from .db import Database
    from .services.backup_service import BackupService

    db = Database(db_path=_db_path())
    try:
        service = BackupService(db=db)
        # CLI 场景忽略当日已备份的限制，显式要求时总是执行
        service._set_setting("auto_backup_last_date", "")
        path = service.run_auto_backup()
        if path is None:
            print("备份失败，详见日志")
            return 1
        print(f"备份完成：{path}")
        return 0
    finally:
        db.close()


def cmd_migrate() -> int:
    """手动执行待应用的数据库迁移。"""
    from .db import Database
    from .db.migrations import get_schema_version, run_migrations

    db = Database(db_path=_db_path())
    try:
        before = get_schema_version(db)
        applied = run_migrations(db)
        after = get_schema_version(db)
        print(f"schema 版本：v{before} → v{after}（执行 {applied} 步）")
        return 0
    finally:
        db.close()


def cmd_version() -> int:
    """打印版本信息。"""
    from . import __version__

    print(f"PLOS AI v{__version__}")
    try:
        cfg_path = None
        from .utils.paths import get_data_dir

        candidate = get_data_dir() / "config.json"
        if candidate.exists():
            cfg_path = candidate
        print(f"数据目录：{get_data_dir()}")
        if cfg_path:
            json.loads(cfg_path.read_text(encoding="utf-8"))
            print(f"配置文件：{cfg_path}（可解析）")
    except Exception as e:
        print(f"配置检查：异常（{e}）")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="plos",
        description="PLOS AI 个人学习操作系统命令行工具",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    p_doctor = sub.add_parser("doctor", help="数据库/目录/模型全量体检")
    p_doctor.add_argument("--with-models", action="store_true", help="额外探测本地模型服务")
    sub.add_parser("backup", help="立即执行一次滚动备份")
    sub.add_parser("migrate", help="执行待应用的数据库迁移")
    sub.add_parser("version", help="打印版本信息")

    args = parser.parse_args(argv)
    if args.command == "doctor":
        return cmd_doctor(with_models=args.with_models)
    if args.command == "backup":
        return cmd_backup()
    if args.command == "migrate":
        return cmd_migrate()
    if args.command == "version":
        return cmd_version()
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
