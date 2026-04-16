import asyncio
import logging
import sys
from pathlib import Path

from src.config import BASE_DIR, CONFIG
from src.contextualizer import contextualize_chunks
from src.embeddings import embed_documents
from src.pdf_loader import (
    ScannedPDFError,
    process_single_pdf_with_doc,
)
from src.vector_store import collection_exists, recreate_collection, upsert_chunks

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

USE_CONTEXTUAL = CONFIG.get("contextual_embeddings", {}).get("enabled", False)


async def main(pdf_dir: Path, *, force: bool = False) -> None:
    if not force and await collection_exists():
        logger.error(
            "Collection already exists! Use --force to recreate it (this deletes all existing vectors)."
        )
        sys.exit(1)

    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        logger.warning("No PDFs found in %s", pdf_dir)
        return

    all_chunks: list[dict] = []
    for pdf_path in pdf_files:
        logger.info("Processing %s", pdf_path.name)
        try:
            full_doc, chunks = process_single_pdf_with_doc(pdf_path)
        except ScannedPDFError:
            logger.warning("  Skipping %s — scanned PDF", pdf_path.name)
            continue
        if not chunks:
            continue

        if USE_CONTEXTUAL:
            logger.info("  Contextualizing %d chunks...", len(chunks))
            chunks = await contextualize_chunks(full_doc, chunks)

        logger.info("  %d chunks", len(chunks))
        all_chunks.extend(chunks)

    if not all_chunks:
        logger.warning("No chunks produced. Check your PDF directory.")
        return

    # Use contextualized text for embedding if available, otherwise raw text
    texts = [c.get("contextualized_text", c.get("embedding_text", c["text"])) for c in all_chunks]
    metadatas = [c["metadata"] for c in all_chunks]

    logger.info("Embedding %d chunks...", len(texts))
    vectors = await embed_documents(texts)

    logger.info("Recreating Qdrant collection and upserting...")
    await recreate_collection()
    # Store original text in Qdrant (what Claude sees), not the contextualized version
    original_texts = [c["text"] for c in all_chunks]
    count = await upsert_chunks(original_texts, vectors, metadatas)

    logger.info("Done. %d chunks indexed.", count)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Ingest PDFs into Qdrant")
    parser.add_argument("pdf_dir", nargs="?", default=None, help="Directory with PDFs")
    parser.add_argument(
        "--force", action="store_true", help="Recreate collection even if it already exists"
    )
    args = parser.parse_args()

    pdf_dir = Path(args.pdf_dir) if args.pdf_dir else BASE_DIR / "data" / "pdfs"
    asyncio.run(main(pdf_dir, force=args.force))
