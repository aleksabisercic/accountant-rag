import logging
import re
from pathlib import Path

import cyrtranslit
import pymupdf
import pymupdf4llm
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)

from src.config import CONFIG

logger = logging.getLogger(__name__)

CHUNK_SIZE = CONFIG["retrieval"]["chunk_size"]

HEADER_SPLITS = [
    ("#", "h1"),
    ("##", "h2"),
    ("###", "h3"),
]


class ScannedPDFError(Exception):
    """Raised when a PDF appears to be scanned/image-based and cannot be extracted."""


def extract_markdown(pdf_path: Path) -> str:
    try:
        md = pymupdf4llm.to_markdown(str(pdf_path))
    except Exception:
        logger.warning("PyMuPDF4LLM failed on %s — likely a scanned PDF", pdf_path.name)
        raise ScannedPDFError(pdf_path.name)
    if not md.strip():
        logger.warning("Empty text from %s — likely a scanned PDF", pdf_path.name)
        raise ScannedPDFError(pdf_path.name)
    md = _fix_misclassified_tables(str(pdf_path), md)
    return md


_PICTURE_WITH_TEXT_RE = re.compile(
    r"\*\*==> picture \[\d+ x \d+\] intentionally omitted <==\*\*\s*"
    r"\*\*----- Start of picture text -----\*\*"
    r".*?"
    r"\*\*----- End of picture text -----\*\*(?:<br>)?\s*",
    re.DOTALL,
)


def _fix_misclassified_tables(pdf_path: str, md: str) -> str:
    """Replace picture-text blocks with proper markdown tables.

    pymupdf4llm sometimes misclassifies PDF tables as pictures, producing
    'picture intentionally omitted' blocks with raw text. This detects those
    blocks and replaces them with structured tables from pymupdf.find_tables().
    """
    matches = list(_PICTURE_WITH_TEXT_RE.finditer(md))
    if not matches:
        return md

    doc = pymupdf.open(pdf_path)
    table_mds: list[str] = []
    for page in doc:
        for tab in page.find_tables().tables:
            table_mds.append(tab.to_markdown())
    doc.close()

    if not table_mds:
        return md

    parts: list[str] = []
    last_end = 0
    for i, match in enumerate(matches):
        parts.append(md[last_end : match.start()])
        if i < len(table_mds):
            parts.append("\n\n" + table_mds[i] + "\n\n")
        last_end = match.end()
    parts.append(md[last_end:])

    return "".join(parts)


def transliterate_to_latin(text: str) -> str:
    return cyrtranslit.to_latin(text, "sr")


def chunk_markdown(md_text: str, filename: str) -> list[dict]:
    md_text = transliterate_to_latin(md_text)

    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=HEADER_SPLITS, strip_headers=False
    )
    header_chunks = header_splitter.split_text(md_text)

    recursive_splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=0,
        separators=["\n\n", "\n", ". ", "; ", ", ", " ", ""],
        length_function=len,
    )

    chunks = []
    for doc in header_chunks:
        breadcrumb = " > ".join(
            doc.metadata[h]
            for h in ("h1", "h2", "h3")
            if doc.metadata.get(h)
        )
        parent_text = doc.page_content
        if len(parent_text) > CHUNK_SIZE:
            sub_chunks = recursive_splitter.split_text(parent_text)
        else:
            sub_chunks = [parent_text]
        for sc in sub_chunks:
            if sc.strip():
                chunks.append({
                    "text": sc,
                    "embedding_text": f"Izvor: {filename} | Sekcija: {breadcrumb}\n\n{sc}",
                    "metadata": {
                        "filename": filename,
                        "section": breadcrumb,
                        "parent_text": parent_text,
                    },
                })
    return chunks


def process_single_pdf(pdf_path: Path) -> list[dict]:
    md = extract_markdown(pdf_path)
    if not md.strip():
        return []
    return chunk_markdown(md, pdf_path.name)


def process_single_pdf_with_doc(pdf_path: Path) -> tuple[str, list[dict]]:
    """Return (full_document_text, chunks) for contextual embedding."""
    md = extract_markdown(pdf_path)
    if not md.strip():
        return "", []
    latin_md = transliterate_to_latin(md)
    chunks = chunk_markdown(md, pdf_path.name)
    return latin_md, chunks


def load_all_pdfs(pdf_dir: Path) -> list[dict]:
    all_chunks = []
    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        logger.warning("No PDFs found in %s", pdf_dir)
        return []
    for pdf_path in pdf_files:
        logger.info("Processing %s", pdf_path.name)
        try:
            chunks = process_single_pdf(pdf_path)
        except ScannedPDFError:
            logger.warning("  ⏭ Skipping %s — scanned PDF, OCR not available", pdf_path.name)
            continue
        logger.info("  → %d chunks", len(chunks))
        all_chunks.extend(chunks)
    return all_chunks
