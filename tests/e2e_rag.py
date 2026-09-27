"""端到端 RAG 测试。

测试文档上传、分块入库、语义检索、RAG 增强对话。

运行：
    cd plos_ai
    .venv\\Scripts\\Activate.ps1
    $env:PYTHONPATH="src"; py -m tests.e2e_rag
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plos.ai import ModelManager
from plos.config import detect_hardware, load_config
from plos.services import ChatService, RAGService, SessionManager
from plos.utils.logger import setup_logger


def _ensure_utf8_stdout() -> None:
    """Windows 控制台可能默认 gbk，强制使用 utf-8 避免输出乱码/报错。"""
    import sys
    import io

    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def main() -> int:
    _ensure_utf8_stdout()
    setup_logger()

    print("=" * 60)
    print("PLOS AI E2E RAG Test")
    print("=" * 60)

    config = load_config()
    hardware = detect_hardware()
    manager = ModelManager(config, hardware=hardware)

    print(f"Embedding available: {manager.is_embedding_available()}")
    print(f"Embedding models: {manager.list_embedding_models()}")

    if not manager.is_embedding_available():
        print("ERROR: Embedding model not available. Please run: ollama pull nomic-embed-text")
        return 1

    rag = RAGService(manager)

    # 创建一个测试文档
    doc_path = Path("data/test_rag.txt")
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(
        "牛顿第一定律：任何物体都要保持匀速直线运动或静止状态，直到外力迫使它改变运动状态为止。\n"
        "牛顿第二定律：物体的加速度与作用力成正比，与质量成反比，公式为 F = ma。\n"
        "牛顿第三定律：相互作用的两个物体之间的作用力和反作用力总是大小相等、方向相反。\n",
        encoding="utf-8",
    )

    print(f"\nAdding document: {doc_path}")
    try:
        doc_id = rag.add_document(doc_path)
        print(f"Document added, id={doc_id}")
    except Exception as e:
        print(f"ERROR: Failed to add document: {e}")
        return 1

    query = "牛顿第二定律的公式是什么？"
    print(f"\nSearching: {query}")
    try:
        results = rag.search(query, top_k=2)
        for i, r in enumerate(results, 1):
            print(f"[{i}] {r.source} (score={r.score:.3f}): {r.content[:80]}...")
    except Exception as e:
        print(f"ERROR: Search failed: {e}")
        return 1

    session = SessionManager()
    chat = ChatService(manager, session)
    conv_id = chat.create_conversation("RAG Test")

    print(f"\nAsking AI with RAG: {query}")
    try:
        response = chat.send_message(conv_id, query, use_rag=True)
        print(f"AI: {response.content}")
    except Exception as e:
        print(f"ERROR: RAG chat failed: {e}")
        return 1

    print("\nE2E RAG test passed!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
