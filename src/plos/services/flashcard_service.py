"""闪卡记忆服务。

基于 SM-2 间隔重复算法，支持文字与图片闪卡的创建、复习、调度、导入导出。
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from ..core.enums import FlashcardRating
from ..db import Database, SM2Scheduler, get_db
from ..utils.latex_clean import latex_to_unicode
from ..utils.logger import get_logger
from .user_service import UserService

logger = get_logger("services.flashcard_service")


_FLASHCARD_EXPORT_FIELDS = [
    "id",
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
    "created_at",
    "updated_at",
]


class FlashcardService:
    """闪卡（Anki-style）业务服务。"""

    def __init__(
        self,
        db: Optional[Database] = None,
        user_service: Optional[UserService] = None,
        statistics_service=None,
    ):
        self.db = db or get_db()
        self.user_service = user_service
        self.statistics_service = statistics_service

    def _user_id(self, user_id: Optional[int] = None) -> int:
        if user_id is not None:
            return user_id
        if self.user_service is not None:
            return self.user_service.get_current_user_id()
        return 0

    def add_card(
        self,
        front_content: str,
        back_content: str,
        front_image: Optional[str] = None,
        card_type: str = "text",
        subject: str = "",
        tags: str = "",
        user_id: Optional[int] = None,
    ) -> int:
        """创建一张新闪卡。"""
        uid = self._user_id(user_id)
        # 入库前统一把 LaTeX 记号转成可读 Unicode，复习/列表/导出不再出现裸命令
        front_content = latex_to_unicode(front_content or "")
        back_content = latex_to_unicode(back_content or "")
        card_id = self.db.insert(
            "INSERT INTO flashcards (user_id, front_content, back_content, front_image, card_type, subject, tags) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (uid, front_content, back_content, front_image, card_type, subject, tags),
        )
        logger.info("Added flashcard id=%d user_id=%d", card_id, uid)
        return card_id

    def get_due_cards(
        self, limit: int = 50, user_id: Optional[int] = None
    ) -> List[dict]:
        """获取今天到期的闪卡。"""
        uid = self._user_id(user_id)
        return self.db.fetchall(
            "SELECT * FROM flashcards WHERE user_id = ? AND next_review <= CURRENT_TIMESTAMP "
            "ORDER BY next_review ASC LIMIT ?",
            (uid, limit),
        )

    def review_card(
        self, card_id: int, rating: FlashcardRating, user_id: Optional[int] = None
    ) -> dict:
        """复习一张闪卡并更新 SM-2 参数。"""
        uid = self._user_id(user_id)
        row = self.db.fetchone(
            "SELECT * FROM flashcards WHERE id = ? AND user_id = ?", (card_id, uid)
        )
        if row is None:
            raise ValueError(f"闪卡不存在：{card_id}")

        result = SM2Scheduler.review(
            rating=rating,
            current_ease=row["ease"],
            current_interval=row["interval"],
            current_repetitions=row["repetitions"],
            review_time=datetime.now(),
        )

        self.db.execute(
            "UPDATE flashcards SET ease = ?, interval = ?, repetitions = ?, next_review = ? WHERE id = ? AND user_id = ?",
            (
                result.ease,
                result.interval,
                result.repetitions,
                result.next_review.strftime("%Y-%m-%d %H:%M:%S"),
                card_id,
                uid,
            ),
        )
        # 真实复习行为写入统计（此前仅演示数据会写入，仪表盘复习量/正确率恒为 0）
        if self.statistics_service is not None:
            try:
                self.statistics_service.record_flashcard_review(
                    int(rating.value), subject=row["subject"] or "", user_id=uid
                )
            except Exception as e:
                logger.warning("Failed to record flashcard review stats: %s", e)
        logger.info("Reviewed flashcard id=%d rating=%s next=%s", card_id, rating.name, result.next_review)
        return {
            "card_id": card_id,
            "ease": result.ease,
            "interval": result.interval,
            "repetitions": result.repetitions,
            "next_review": result.next_review,
        }

    def get_card(self, card_id: int, user_id: Optional[int] = None) -> Optional[dict]:
        uid = self._user_id(user_id)
        return self.db.fetchone(
            "SELECT * FROM flashcards WHERE id = ? AND user_id = ?", (card_id, uid)
        )

    def list_cards(
        self,
        limit: int = 200,
        subject: Optional[str] = None,
        tag: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> List[dict]:
        uid = self._user_id(user_id)
        conditions = ["user_id = ?"]
        params = [uid]
        if subject:
            conditions.append("subject = ?")
            params.append(subject)
        if tag:
            conditions.append("(tags LIKE ? OR front_content LIKE ? OR back_content LIKE ?)")
            params.extend([f"%{tag}%"] * 3)
        where = f"WHERE {' AND '.join(conditions)}"
        sql = f"SELECT * FROM flashcards {where} ORDER BY next_review ASC LIMIT ?"
        params.append(limit)
        return self.db.fetchall(sql, tuple(params))

    def delete_card(self, card_id: int, user_id: Optional[int] = None) -> None:
        uid = self._user_id(user_id)
        self.db.execute(
            "DELETE FROM flashcards WHERE id = ? AND user_id = ?", (card_id, uid)
        )

    def update_card(
        self,
        card_id: int,
        front_content: Optional[str] = None,
        back_content: Optional[str] = None,
        subject: Optional[str] = None,
        tags: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> None:
        """更新闪卡内容与标签，带 user_id 隔离校验。"""
        uid = self._user_id(user_id)
        fields = []
        params: List[Any] = []
        if front_content is not None:
            fields.append("front_content = ?")
            params.append(latex_to_unicode(front_content))
        if back_content is not None:
            fields.append("back_content = ?")
            params.append(latex_to_unicode(back_content))
        if subject is not None:
            fields.append("subject = ?")
            params.append(subject)
        if tags is not None:
            fields.append("tags = ?")
            params.append(tags)
        if not fields:
            return
        params.extend([card_id, uid])
        sql = f"UPDATE flashcards SET {', '.join(fields)} WHERE id = ? AND user_id = ?"
        self.db.execute(sql, tuple(params))

    def get_stats(self, user_id: Optional[int] = None) -> dict:
        """返回闪卡统计信息。"""
        uid = self._user_id(user_id)
        row = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM flashcards WHERE user_id = ?", (uid,)
        )
        total = row["c"] if row else 0
        row = self.db.fetchone(
            "SELECT COUNT(*) AS c FROM flashcards WHERE user_id = ? AND next_review <= CURRENT_TIMESTAMP",
            (uid,),
        )
        due = row["c"] if row else 0
        return {"total": total, "due_today": due}

    def list_subjects(self, user_id: Optional[int] = None) -> List[str]:
        """获取当前用户所有不重复科目。"""
        uid = self._user_id(user_id)
        rows = self.db.fetchall(
            "SELECT DISTINCT subject FROM flashcards WHERE user_id = ? AND subject != '' ORDER BY subject",
            (uid,),
        )
        return [r["subject"] for r in rows]

    def export_to_json(self, file_path: Path, user_id: Optional[int] = None) -> int:
        """将闪卡导出为 JSON 文件，返回导出条数。"""
        cards = self.list_cards(limit=10000, user_id=user_id)
        file_path.write_text(
            json.dumps(cards, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Exported %d flashcards to JSON: %s", len(cards), file_path)
        return len(cards)

    def import_from_json(self, file_path: Path) -> int:
        """从 JSON 文件导入闪卡，返回导入条数。"""
        try:
            data = json.loads(file_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to parse flashcard JSON: %s", e)
            return 0
        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            logger.error("Invalid flashcard JSON format: expected list or dict")
            return 0
        count = 0
        for item in data:
            self.add_card(
                front_content=item.get("front_content", ""),
                back_content=item.get("back_content", ""),
                front_image=item.get("front_image"),
                card_type=item.get("card_type", "text"),
                subject=item.get("subject", ""),
                tags=item.get("tags", ""),
            )
            count += 1
        logger.info("Imported %d flashcards from JSON: %s", count, file_path)
        return count

    def export_to_csv(self, file_path: Path, user_id: Optional[int] = None) -> int:
        """将闪卡导出为 CSV 文件，返回导出条数。"""
        cards = self.list_cards(limit=10000, user_id=user_id)
        with file_path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=_FLASHCARD_EXPORT_FIELDS)
            writer.writeheader()
            for row in cards:
                writer.writerow({k: row.get(k, "") for k in _FLASHCARD_EXPORT_FIELDS})
        logger.info("Exported %d flashcards to CSV: %s", len(cards), file_path)
        return len(cards)

    def import_from_csv(self, file_path: Path) -> int:
        """从 CSV 文件导入闪卡，返回导入条数。"""
        count = 0
        with file_path.open("r", newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                self.add_card(
                    front_content=row.get("front_content", ""),
                    back_content=row.get("back_content", ""),
                    front_image=row.get("front_image") or None,
                    card_type=row.get("card_type", "text"),
                    subject=row.get("subject", ""),
                    tags=row.get("tags", ""),
                )
                count += 1
        logger.info("Imported %d flashcards from CSV: %s", count, file_path)
        return count

    def export_to_markdown(self, file_path: Path, user_id: Optional[int] = None) -> int:
        """将闪卡导出为 Markdown 文件，返回导出条数。"""
        cards = self.list_cards(limit=10000, user_id=user_id)
        lines = ["# 闪卡导出\n"]
        for card in cards:
            lines.append(f"## 闪卡 #{card['id']}")
            lines.append(f"- **科目**：{card.get('subject', '')}")
            lines.append(f"- **标签**：{card.get('tags', '')}\n")
            lines.append("### 正面")
            lines.append(card.get("front_content", ""))
            lines.append("\n### 背面")
            lines.append(card.get("back_content", ""))
            lines.append("\n---\n")
        file_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Exported %d flashcards to Markdown: %s", len(cards), file_path)
        return len(cards)
