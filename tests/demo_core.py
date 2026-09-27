"""Demo script to verify the bottom-layer modules of PLOS AI.

Run with:
    cd plos_ai
    python -m tests.demo_core

This demo verifies:
- Config loading/saving
- Hardware detection
- AI model manager availability checks
- Database initialization
- SM-2 scheduler
- Vector store (basic)

It does NOT require any actual model to be running (it checks availability).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add src to path when running as script
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plos.config import detect_hardware, load_config, save_config
from plos.core.enums import FlashcardRating
from plos.db import SM2Scheduler, VectorStore, get_db
from plos.utils.logger import setup_logger


def main() -> int:
    setup_logger()

    print("=" * 60)
    print("PLOS AI Core Module Demo")
    print("=" * 60)

    # 1. Configuration
    print("\n[1] Loading configuration...")
    config = load_config()
    print(f"  Backend type: {config['backend']['type']}")
    print(f"  Ollama host: {config['backend']['ollama_host']}")
    print(f"  Text model: {config['models']['text_model']}")
    print(f"  OCR confidence threshold: {config['ocr']['confidence_threshold']}")

    # Modify and save
    config["models"]["temperature"] = 0.5
    save_config(config)
    print("  Saved updated config with temperature=0.5")

    # 2. Hardware detection
    print("\n[2] Detecting hardware...")
    hw = detect_hardware()
    print(f"  OS: {hw.os_name}")
    print(f"  CPU: {hw.cpu_model} ({hw.cpu_cores} cores)")
    print(f"  RAM: {hw.ram_total_gb} GB")
    print(f"  GPU: {hw.gpu_name or 'None'} (VRAM: {hw.gpu_vram_gb} GB)")
    print(f"  Low-spec: {hw.is_low_spec}")

    # 3. AI model manager availability (won't call models)
    print("\n[3] Model manager availability check...")
    from plos.ai import ModelManager

    manager = ModelManager(config, hardware=hw)
    print(f"  Backend: {manager.backend_type.value}")
    print(f"  Text available: {manager.is_text_available()}")
    print(f"  Vision available: {manager.is_vision_available()}")
    print(f"  Embedding available: {manager.is_embedding_available()}")
    if manager.is_text_available():
        print(f"  Text models: {manager.list_text_models()[:5]}")
    else:
        print("  (Ollama server not running or not reachable)")

    # 4. Database
    print("\n[4] Database initialization...")
    db = get_db()
    print(f"  DB path: {db.db_path}")
    # Insert and read a setting
    db.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        ("demo_key", "demo_value"),
    )
    row = db.fetchone("SELECT value FROM settings WHERE key = ?", ("demo_key",))
    print(f"  Test setting read-back: {row['value'] if row else 'None'}")

    # 5. SM-2 scheduler
    print("\n[5] SM-2 scheduler...")
    result = SM2Scheduler.review(FlashcardRating.GOOD)
    print(f"  First 'good' review: interval={result.interval}d, next={result.next_review}")
    result2 = SM2Scheduler.review(
        FlashcardRating.EASY,
        current_ease=result.ease,
        current_interval=result.interval,
        current_repetitions=result.repetitions,
    )
    print(f"  Next 'easy' review: interval={result2.interval}d, ease={result2.ease}")

    # 6. Vector store (ChromaDB)
    print("\n[6] Vector store (ChromaDB)...")
    vs = VectorStore()
    print(f"  Persist dir: {vs.persist_dir}")
    try:
        vs.add(
            ids=["demo-1", "demo-2"],
            documents=["PLOS AI is a local learning OS", "Qwen2.5-VL understands images"],
            embeddings=[[0.1] * 768, [0.2] * 768],
            metadatas=[{"source": "intro"}, {"source": "model"}],
        )
        count = vs.count()
        print(f"  Added demo vectors. Count: {count}")
        results = vs.query(query_embedding=[0.15] * 768, top_k=2)
        print(f"  Query returned {len(results)} results")
    except Exception as e:
        print(f"  Vector store error (ChromaDB may not be installed): {e}")

    print("\n" + "=" * 60)
    print("Demo complete. Check logs/plos.log for details.")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
