"""
Local tests — no Qdrant needed.
Usage: python test_local.py
"""

import asyncio
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

BASE_DIR = Path(__file__).resolve().parent
PDF_DIR = BASE_DIR / "data" / "pdfs"


# --- Test 1: PDF extraction + chunking ---

def test_pdf_loader():
    from src.pdf_loader import process_single_pdf, chunk_markdown, transliterate_to_latin

    print("\n=== Test 1a: Transliteration ===")
    cyrillic = "ПДВ стопа за 2024. годину"
    latin = transliterate_to_latin(cyrillic)
    print(f"  Cyrillic: {cyrillic}")
    print(f"  Latin:    {latin}")
    assert "PDV" in latin, "Transliteration failed"
    print("  ✓ Transliteration works")

    print("\n=== Test 1b: Chunking from Markdown ===")
    sample_md = """# Zakon o PDV-u

## Član 1 — Opšte odredbe

Porez na dodatu vrednost (PDV) je opšti porez na potrošnju koji se obračunava
i plaća na isporuku dobara i pružanje usluga, u svim fazama proizvodnje i
prometa dobara i usluga, kao i na uvoz dobara.

## Član 2 — Stope

Opšta stopa PDV-a iznosi 20%.
Posebna stopa PDV-a iznosi 10% za osnovne životne namirnice, lekove i medicinsku opremu.

## Član 3 — Tabele

| Kategorija | Stopa |
|-----------|-------|
| Opšta | 20% |
| Posebna | 10% |
| Oslobođeno | 0% |
"""
    chunks = chunk_markdown(sample_md, "test_zakon.pdf")
    print(f"  Produced {len(chunks)} chunks")
    for i, c in enumerate(chunks):
        print(f"  Chunk {i}: {len(c['text'])} chars, section='{c['metadata']['section']}'")
        print(f"    Preview: {c['text'][:80]}...")
    assert len(chunks) > 0, "No chunks produced"
    assert all(c["metadata"]["filename"] == "test_zakon.pdf" for c in chunks)
    print("  ✓ Chunking works")

    # Test with real PDFs if available
    pdfs = list(PDF_DIR.glob("*.pdf"))
    if pdfs:
        print(f"\n=== Test 1c: Real PDF ({pdfs[0].name}) ===")
        chunks = process_single_pdf(pdfs[0])
        print(f"  Extracted {len(chunks)} chunks")
        if chunks:
            print(f"  First chunk preview: {chunks[0]['text'][:120]}...")
        print("  ✓ Real PDF extraction works")
    else:
        print("\n  ⏭ No PDFs in data/pdfs/ — skipping real PDF test")


# --- Test 2: Embeddings (Cohere API) ---

async def test_embeddings():
    from src.embeddings import embed_documents, embed_query

    print("\n=== Test 2a: Single query embedding ===")
    vec = await embed_query("Koja je stopa PDV-a u Srbiji?")
    print(f"  Vector length: {len(vec)}")
    print(f"  First 5 dims: {vec[:5]}")
    assert len(vec) == 1024, f"Expected 1024 dims, got {len(vec)}"
    print("  ✓ Query embedding works")

    print("\n=== Test 2b: Batch document embedding ===")
    texts = [
        "Opšta stopa PDV-a iznosi 20%.",
        "Posebna stopa PDV-a iznosi 10%.",
        "Rok za podnošenje prijave je 15. u mesecu.",
    ]
    vecs = await embed_documents(texts)
    print(f"  Embedded {len(vecs)} texts, each {len(vecs[0])} dims")
    assert len(vecs) == 3
    print("  ✓ Batch embedding works")


# --- Test 3: Agent (mock search, real Claude) ---

async def test_agent_no_qdrant():
    """Test agent with a patched search that returns fake results."""
    import src.agent as agent_module

    print("\n=== Test 3: Agent with mocked search ===")

    # Patch the search handler to return fake results
    original_handle = agent_module._handle_search

    async def mock_search(query: str) -> str:
        import json
        return json.dumps({
            "results": [
                {
                    "text": "Opšta stopa PDV-a iznosi 20%. Posebna stopa iznosi 10% za osnovne životne namirnice.",
                    "source": "zakon_o_pdv.pdf",
                    "section": "Član 2 — Stope",
                    "relevance": 0.92,
                },
            ]
        }, ensure_ascii=False)

    agent_module._handle_search = mock_search
    try:
        messages = [{"role": "user", "content": "Koja je stopa PDV-a?"}]
        result = await agent_module.run(messages)
        print(f"  Response: {result.text[:200]}...")
        print(f"  Sources: {result.sources}")
        print(f"  Web search used: {result.web_search_used}")
        assert result.text, "Empty response"
        print("  ✓ Agent works (with mock search)")
    finally:
        agent_module._handle_search = original_handle


# --- Run ---

if __name__ == "__main__":
    print("=" * 60)
    print("Accountant RAG — Local Tests (no Qdrant)")
    print("=" * 60)

    test_pdf_loader()

    print("\n--- Running async tests ---")
    asyncio.run(test_embeddings())
    asyncio.run(test_agent_no_qdrant())

    print("\n" + "=" * 60)
    print("All tests passed!")
    print("=" * 60)
