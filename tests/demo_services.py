"""业务编排层 demo。

验证 services 模块是否可导入、可实例化，并完成基础业务操作：
- 创建会话、添加消息、读取历史
- 错题本 CRUD
- 闪卡创建与 SM-2 复习调度
- AI/RAG/OCR 服务可用性检查

运行：
    cd plos_ai
    $env:PYTHONPATH="src"; py -m tests.demo_services
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plos.ai import ModelManager
from plos.config import detect_hardware, load_config
from plos.core.enums import FlashcardRating, Role
from plos.db import get_db
from plos.services import (
    ChatService,
    ErrorBookService,
    FlashcardService,
    OCRService,
    RAGService,
    SessionManager,
)
from plos.utils.logger import setup_logger


def main() -> int:
    setup_logger()

    print("=" * 60)
    print("PLOS AI Services Layer Demo")
    print("=" * 60)

    config = load_config()
    hw = detect_hardware()
    manager = ModelManager(config, hardware=hw)
    db = get_db()

    # 1. 会话管理
    print("\n[1] SessionManager")
    session = SessionManager(db)
    conv_id = session.create_conversation("测试对话")
    print(f"  Created conversation id={conv_id}")
    session.add_message(conv_id, Role.USER, "你好，请介绍一下牛顿第二定律")
    session.add_message(conv_id, Role.ASSISTANT, "F = ma，力等于质量乘以加速度。")
    messages = session.get_messages(conv_id)
    print(f"  Messages count: {len(messages)}")
    for msg in messages:
        print(f"    {msg.role.value}: {msg.content[:30]}...")

    # 2. 错题本
    print("\n[2] ErrorBookService")
    eb = ErrorBookService(db)
    eid = eb.add_error(
        question="求函数 f(x)=x^2 在 x=1 处的导数",
        answer="2",
        analysis="使用幂函数求导法则：f'(x)=2x，代入 x=1 得 2。",
        knowledge_tags="微积分,导数,幂函数",
        mastery_level=0,
    )
    print(f"  Added error id={eid}")
    eb.set_mastery(eid, 1)
    row = eb.get_error(eid)
    print(f"  Mastery level: {row['mastery_level']}")

    # 3. 闪卡
    print("\n[3] FlashcardService + SM-2")
    fc = FlashcardService(db)
    cid = fc.add_card(front_content="F = ?", back_content="ma")
    print(f"  Added flashcard id={cid}")
    review1 = fc.review_card(cid, FlashcardRating.GOOD)
    print(f"  Review 'good': interval={review1['interval']}d, next={review1['next_review']}")
    review2 = fc.review_card(cid, FlashcardRating.EASY)
    print(f"  Review 'easy': interval={review2['interval']}d, ease={review2['ease']}")
    stats = fc.get_stats()
    print(f"  Stats: {stats}")

    # 4. AI 服务可用性
    print("\n[4] AI Service availability")
    ChatService(manager, session)
    print(f"  ChatService ready, text available: {manager.is_text_available()}")

    ocr = OCRService(manager)
    print(f"  OCRService ready, backend available: {ocr.is_backend_available()}")
    print(f"  OCRService ready, image service available: {ocr.is_image_service_available()}")
    print(f"  Status: {ocr.get_status_message()}")

    RAGService(manager, db=db)
    print(f"  RAGService ready, embedding available: {manager.is_embedding_available()}")

    print("\n" + "=" * 60)
    print("Services demo complete.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
