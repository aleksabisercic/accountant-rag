"""Review PDF parsing and chunking output quality.

Usage:
    python review_pdf_output.py [pdf_dir]

Writes per-PDF review files to data/review/<pdf_name>/:
  - full_markdown.md       — raw markdown from pymupdf4llm
  - full_markdown_latin.md — after Cyrillic→Latin transliteration
  - chunks.md              — all chunks with metadata and dividers
"""

import shutil
import sys
from pathlib import Path

from src.pdf_loader import (
    ScannedPDFError,
    chunk_markdown,
    extract_markdown,
    transliterate_to_latin,
)

REVIEW_DIR = Path("data/review")


def review_pdf(pdf_path: Path) -> dict | None:
    """Process one PDF and write review files. Returns stats or None on failure."""
    name = pdf_path.stem
    out_dir = REVIEW_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        md_raw = extract_markdown(pdf_path)
    except ScannedPDFError:
        print(f"  SKIP (scanned PDF): {pdf_path.name}")
        return None

    md_latin = transliterate_to_latin(md_raw)
    chunks = chunk_markdown(md_raw, pdf_path.name)

    # Write raw markdown
    (out_dir / "full_markdown.md").write_text(md_raw, encoding="utf-8")

    # Write transliterated markdown
    (out_dir / "full_markdown_latin.md").write_text(md_latin, encoding="utf-8")

    # Write chunks review file
    total = len(chunks)
    lines = []
    for i, chunk in enumerate(chunks, 1):
        section = chunk["metadata"]["section"] or "(no section)"
        embed_text = chunk.get("embedding_text", chunk["text"])
        lines.append(f'## Chunk {i} / {total}  |  section: "{section}"  |  len: {len(embed_text)}')
        lines.append("")
        lines.append(embed_text)
        lines.append("")
        lines.append("---")
        lines.append("")
    (out_dir / "chunks.md").write_text("\n".join(lines), encoding="utf-8")

    lengths = [len(c.get("embedding_text", c["text"])) for c in chunks]
    return {
        "name": pdf_path.name,
        "chunks": total,
        "avg": sum(lengths) // total if total else 0,
        "min": min(lengths) if lengths else 0,
        "max": max(lengths) if lengths else 0,
    }


def main():
    pdf_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/pdfs")
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        print(f"No PDFs found in {pdf_dir}")
        return

    # Clean previous output
    if REVIEW_DIR.exists():
        shutil.rmtree(REVIEW_DIR)

    print(f"Processing {len(pdfs)} PDFs from {pdf_dir}\n")
    stats = []
    for pdf in pdfs:
        print(f"  {pdf.name}")
        result = review_pdf(pdf)
        if result:
            stats.append(result)

    # Summary table
    if stats:
        name_w = max(len(s["name"]) for s in stats)
        print(f"\n{'PDF':<{name_w}}  chunks  avg   min   max")
        print("-" * (name_w + 30))
        for s in stats:
            print(f"{s['name']:<{name_w}}  {s['chunks']:>6}  {s['avg']:>3}  {s['min']:>4}  {s['max']:>4}")

    print(f"\nOutput written to {REVIEW_DIR.resolve()}/")


if __name__ == "__main__":
    main()
