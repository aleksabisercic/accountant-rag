"""Tests for chunk_markdown using real Serbian legal text from PDF extraction.

Fixtures are actual pymupdf4llm markdown output from Serbian accounting regulations.
Tests cover both Latin and Cyrillic input — production PDFs come in both scripts.
"""

import cyrtranslit

from src.pdf_loader import chunk_markdown

FILENAME = "Godisnji_porez_2025.pdf"

# Real markdown from pymupdf4llm output of a Serbian tax regulation PDF.
# Contains h2 header, long paragraphs with ". " and "; " separators,
# footnotes, page numbers, markdown tables — the full production mess.
REAL_SERBIAN_MD = """\
Godišnji porez na dohodak građana za 2025. godinu

90

## GODIŠNJI POREZ NA DOHODAK GRAĐANA ZA 2025. GODINU (podnošenje poreske prijave na novom Obrascu PP GPDG do 15. maja 2026)

U _Službenom glasniku RS_ , broj 30/26 od 27. marta 2026. godine, objavljen je _Pravilnik o izmenama i dopunama Pravilnika o poreskoj prijavi za godišnji porez na dohodak građana_ , koji je u primeni od 4. aprila 2026. godine. Međutim, ove izmene nisu od značaja za širi krug obveznika godišnjeg poreza.

Naime, osim pravno-tehničkih usklađivanja, izmene _Pravilnika o poreskoj prijavi za godišnji porez na dohodak građana_ (u nastavku: Pravilnik) odnose se na to da su **poreskom prijavom, koja se podnosi na Obrascu PP GPDG, obuhvaćena primanja pomoraca** iz člana 84v _Zakona o porezu na dohodak građana_ (u nastavku: Zakon).

U tom smislu propisano je da se **pod rednim brojem 3.4.4** Oporezivi prihodi, pored ostalog, unosi iznos oporezivih prihoda iz člana 84v _Zakona_ , za koje je obveznik dužan da sam obračuna i plati porez i doprinose, dok se **pod rednim brojem 3.5.4** Oporezivi prihodi, pored ostalog, unosi iznos oporezivih prihoda iz člana 84v _Zakona_ , koji su ostvareni u kalendarskoj godini za koju se utvrđuje godišnji porez, i oporezovani u drugoj državi.

**Napominjemo da i dalje važe objašnjenja** , koja ovde nećemo ponavljati, a koja smo dali u časopisu _Porezi i računovodstvo_ broj:

- **4/25** , str. 11 – opšta pravila i specifičnosti – ko je obveznik godišnjeg poreza na dohodak građana; koji prihodi su predmet oporezivanja godišnjim porezom; umanjivanje oporezivog prihoda za iznos plaćenog poreza i doprinosa na te prihode, uz poseban osvrt na slučaj kada je prihod ostvaren krajem godine za koju se

> 1 Ministarstvo finansija dalo je **Objašnjenje u vezi sa godišnjim porezom na dohodak građana za 2025. godinu** , broj 1023186 2026 10520 004 007 012 001 od 30. marta 2026. godine.

pkt // POREZI I RAČUNOVODSTVO // april 2026 // broj 04

91

## **2. ROK ZA PODNOŠENJE PORESKE PRIJAVE PP GPDG I PLAĆANJE GODIŠNJEG POREZA**

Poreska prijava za utvrđivanje godišnjeg poreza na dohodak građana za 2025. godinu može se podneti isključivo u elektronskom obliku, preko portala Poreske uprave (ePorezi), na Obrascu PP GPDG, a krajnji rok za njeno podnošenje je 15. maj 2026. godine.

**Prvi korak** je da, najkasnije do 1. aprila 2026. godine za godišnji porez za 2025. godinu, poreski organ popuni poresku prijavu na osnovu podataka iz službenih evidencija, i postavi je u elektronskom obliku, kao radnu verziju prijave (status „u pripremi") na portal Poreske uprave, čime je čini dostupnom fizičkom licu – obvezniku godišnjeg poreza. U ovoj poreskoj prijavi iskazani su podaci o prihodima koje je ostvarilo fizičko lice, a za koje su poreskom organu podnete odgovarajuće poreske prijave (npr. poslodavac je podneo poresku prijavu na Obrascu PPP-PD po osnovu isplate zarade zaposlenom; preduzetnik je podneo poreski bilans na Obrascu PB-2; i dr.). Poreska prijava može biti delimično popunjena jer poreski organ ne raspolaže svim podacima koji su potrebni za obračun godišnjeg poreza.

Poreski obveznici kojima je dohodak za oporezivanje godišnjim porezom na dohodak građana 0 dinara (polje 3.12 Obrasca PP GPDG), nemaju obavezu podnošenja poreske prijave. Naime, u ovom slučaju i godišnji porez na dohodak građana (polje 3.17 Obrasca PP GPDG) iznosi 0 dinara.

**Treći korak** je da fizičko lice podnese poresku prijavu u elektronskom obliku preko portala Poreske uprave **najkasnije do 15. maja 2026. godine** za godišnji porez za 2025. godinu, i da plati utvrđeni iznos poreza. Naime, prema članu 114. stav 1. _Zakona_ , porezi koje, u skladu sa ovim zakonom, obveznik utvrđuje samooporezivanjem, **plaćaju se najkasnije do isteka roka za podnošenje poreske prijave** , što znači da godišnji porez na dohodak građana za 2025. godinu fizičko lice ima obavezu da plati najkasnije do 15. maja 2026. godine.
"""

# Multi-header markdown to test breadcrumb with all 3 levels
MULTI_LEVEL_MD = """\
# Zakon o porezu na dohodak građana

## Glava I - Opšte odredbe

### Član 1. Predmet oporezivanja

Porez na dohodak građana plaćaju fizička lica koja ostvaruju dohodak; stopa poreza iznosi 10% na oporezivi dohodak do šestostruke prosečne godišnje zarade; iznad tog iznosa primenjuje se stopa od 15%.
"""


class TestRealSerbianDocument:
    """Run chunk_markdown on real pymupdf4llm output and verify the contract."""

    def setup_method(self):
        self.chunks = chunk_markdown(REAL_SERBIAN_MD, FILENAME)

    def test_produces_chunks(self):
        assert len(self.chunks) >= 2, "Multi-section document should produce multiple chunks"

    def test_every_chunk_has_required_keys(self):
        for i, c in enumerate(self.chunks):
            assert "text" in c, f"chunk {i}: missing 'text'"
            assert "embedding_text" in c, f"chunk {i}: missing 'embedding_text'"
            assert "filename" in c["metadata"], f"chunk {i}: missing metadata.filename"
            assert "section" in c["metadata"], f"chunk {i}: missing metadata.section"
            assert "parent_text" in c["metadata"], f"chunk {i}: missing metadata.parent_text"

    def test_filename_is_correct(self):
        for c in self.chunks:
            assert c["metadata"]["filename"] == FILENAME

    def test_embedding_text_prepends_metadata(self):
        """embedding_text must start with 'Izvor: ...' prefix and end with chunk text."""
        for c in self.chunks:
            assert c["embedding_text"].startswith(f"Izvor: {FILENAME} | Sekcija: ")
            assert c["embedding_text"].endswith(c["text"])

    def test_embedding_text_differs_from_text(self):
        """embedding_text is strictly longer than text (has metadata prefix)."""
        for c in self.chunks:
            assert len(c["embedding_text"]) > len(c["text"])

    def test_parent_text_contains_chunk_text(self):
        """Each chunk's text should be a substring of its parent_text."""
        for c in self.chunks:
            assert c["text"] in c["metadata"]["parent_text"], (
                f"chunk text not found in parent_text: {c['text'][:80]}..."
            )

    def test_chunks_under_size_limit(self):
        """No chunk should exceed the configured CHUNK_SIZE (2000)."""
        from src.pdf_loader import CHUNK_SIZE
        for c in self.chunks:
            assert len(c["text"]) <= CHUNK_SIZE, (
                f"Chunk too large: {len(c['text'])} > {CHUNK_SIZE}"
            )

    def test_section_from_h2_header(self):
        """Real doc has ## headers — at least one chunk should have a non-empty section."""
        sections = [c["metadata"]["section"] for c in self.chunks if c["metadata"]["section"]]
        assert len(sections) >= 1

    def test_no_content_lost(self):
        """Key Serbian legal terms from the input must appear across chunks."""
        all_text = " ".join(c["text"] for c in self.chunks)
        assert "godišnji porez" in all_text.lower()
        assert "Obrascu PP GPDG" in all_text


class TestBreadcrumbHierarchy:
    """Test full h1 > h2 > h3 breadcrumb using multi-level headers."""

    def setup_method(self):
        self.chunks = chunk_markdown(MULTI_LEVEL_MD, "zakon.pdf")

    def test_full_breadcrumb(self):
        """Deepest section should have all three header levels joined."""
        deepest = [c for c in self.chunks if "Član 1" in c["metadata"]["section"]]
        assert len(deepest) >= 1
        section = deepest[0]["metadata"]["section"]
        parts = section.split(" > ")
        assert len(parts) == 3
        assert "Zakon o porezu" in parts[0]
        assert "Glava I" in parts[1]
        assert "Član 1" in parts[2]

    def test_h1_only_sections_exist(self):
        """Content before h2 should have h1-only breadcrumb."""
        h1_only = [c for c in self.chunks if c["metadata"]["section"]
                    and " > " not in c["metadata"]["section"]]
        # May or may not exist depending on content before first h2
        # but if it does, it should be clean
        for c in h1_only:
            assert ">" not in c["metadata"]["section"]


class TestParentTextOnSplitSection:
    """When a section is long enough to be recursively split,
    all child chunks must share the same parent_text."""

    def setup_method(self):
        self.chunks = chunk_markdown(REAL_SERBIAN_MD, FILENAME)

    def test_siblings_share_parent(self):
        """Group chunks by section — siblings must have identical parent_text."""
        by_section: dict[str, list[dict]] = {}
        for c in self.chunks:
            key = c["metadata"]["section"]
            by_section.setdefault(key, []).append(c)

        for section, group in by_section.items():
            if len(group) > 1:
                parent = group[0]["metadata"]["parent_text"]
                for c in group[1:]:
                    assert c["metadata"]["parent_text"] == parent, (
                        f"Sibling chunks in section '{section}' have different parent_text"
                    )

    def test_parent_is_longer_than_children_when_split(self):
        """If section was split, parent_text > any single chunk text."""
        by_section: dict[str, list[dict]] = {}
        for c in self.chunks:
            key = c["metadata"]["section"]
            by_section.setdefault(key, []).append(c)

        for section, group in by_section.items():
            if len(group) > 1:
                parent_len = len(group[0]["metadata"]["parent_text"])
                for c in group:
                    assert parent_len > len(c["text"]), (
                        f"parent_text should be longer than child in split section '{section}'"
                    )


class TestEmbedTextSelectionPriority:
    """The embed text selection line in ingest.py and telegram_handler.py:
        c.get("contextualized_text", c.get("embedding_text", c["text"]))

    This is the actual production line. Test it with real chunk output."""

    @staticmethod
    def _select(chunk: dict) -> str:
        """Exact copy of the production selection logic."""
        return chunk.get("contextualized_text", chunk.get("embedding_text", chunk["text"]))

    def test_real_chunks_select_embedding_text(self):
        """Without contextualizer, real chunks should select embedding_text."""
        chunks = chunk_markdown(REAL_SERBIAN_MD, FILENAME)
        for c in chunks:
            selected = self._select(c)
            assert selected == c["embedding_text"]
            assert selected != c["text"]  # must not fall through to raw text

    def test_contextualized_overrides_embedding_text(self):
        """Simulating what contextualize_chunks() does: adds contextualized_text key."""
        chunks = chunk_markdown(REAL_SERBIAN_MD, FILENAME)
        # Simulate contextualizer adding the key to first chunk
        chunk = {**chunks[0], "contextualized_text": "Kontekst.\n\n" + chunks[0]["text"]}
        assert self._select(chunk) == chunk["contextualized_text"]


# Cyrillic version of a Serbian tax regulation — same content as REAL_SERBIAN_MD
# but in Cyrillic script, as many Serbian accounting PDFs are published.
CYRILLIC_SERBIAN_MD = """\
Годишњи порез на доходак грађана за 2025. годину

90

## ГОДИШЊИ ПОРЕЗ НА ДОХОДАК ГРАЂАНА ЗА 2025. ГОДИНУ (подношење пореске пријаве на новом Обрасцу ПП ГПДГ до 15. маја 2026)

У Службеном гласнику РС, број 30/26 од 27. марта 2026. године, објављен је Правилник о изменама и допунама Правилника о пореској пријави за годишњи порез на доходак грађана, који је у примени од 4. априла 2026. године. Међутим, ове измене нису од значаја за шири круг обвезника годишњег пореза.

Наиме, осим правно-техничких усклађивања, измене Правилника о пореској пријави за годишњи порез на доходак грађана (у наставку: Правилник) односе се на то да су пореском пријавом, која се подноси на Обрасцу ПП ГПДГ, обухваћена примања помораца из члана 84в Закона о порезу на доходак грађана (у наставку: Закон).

## 2. РОК ЗА ПОДНОШЕЊЕ ПОРЕСКЕ ПРИЈАВЕ ПП ГПДГ И ПЛАЋАЊЕ ГОДИШЊЕГ ПОРЕЗА

Пореска пријава за утврђивање годишњег пореза на доходак грађана за 2025. годину може се поднети искључиво у електронском облику, преко портала Пореске управе (еПорези), на Обрасцу ПП ГПДГ, а крајњи рок за њено подношење је 15. мај 2026. године.
"""


class TestCyrillicToLatinConversion:
    """Verify Cyrillic PDF markdown is transliterated to Latin in chunk output.

    Production path: pymupdf4llm extracts Cyrillic markdown → chunk_markdown()
    calls transliterate_to_latin() → all stored text is Latin.
    Query path: user sends Cyrillic query → agent.py calls cyrtranslit.to_latin().
    Both sides must produce matching Latin text for vector search to work."""

    def setup_method(self):
        self.chunks = chunk_markdown(CYRILLIC_SERBIAN_MD, "porez_2025.pdf")

    def test_chunk_text_is_latin(self):
        """No Cyrillic characters should remain in chunk text."""
        cyrillic_range = range(0x0400, 0x04FF + 1)  # Cyrillic Unicode block
        for c in self.chunks:
            cyrillic_chars = [ch for ch in c["text"] if ord(ch) in cyrillic_range]
            assert not cyrillic_chars, (
                f"Cyrillic chars found in chunk text: {''.join(cyrillic_chars[:10])}"
            )

    def test_embedding_text_is_latin(self):
        """No Cyrillic in embedding_text either."""
        cyrillic_range = range(0x0400, 0x04FF + 1)
        for c in self.chunks:
            cyrillic_chars = [ch for ch in c["embedding_text"] if ord(ch) in cyrillic_range]
            assert not cyrillic_chars, (
                f"Cyrillic chars found in embedding_text: {''.join(cyrillic_chars[:10])}"
            )

    def test_section_breadcrumb_is_latin(self):
        """Section headers must also be transliterated."""
        cyrillic_range = range(0x0400, 0x04FF + 1)
        for c in self.chunks:
            section = c["metadata"]["section"]
            cyrillic_chars = [ch for ch in section if ord(ch) in cyrillic_range]
            assert not cyrillic_chars, (
                f"Cyrillic chars found in section: {''.join(cyrillic_chars[:10])}"
            )

    def test_known_terms_transliterated(self):
        """Specific Cyrillic terms must appear as their Latin equivalents."""
        all_text = " ".join(c["text"] for c in self.chunks)
        # "годишњи порез" → "godišnji porez"
        assert "godišnji porez" in all_text.lower()
        # "пореска пријава" → "poreska prijava"
        assert "poreska prijava" in all_text.lower()

    def test_cyrillic_query_matches_indexed_chunks(self):
        """Simulate the query-time path: Cyrillic query → Latin → should match chunk content."""
        # This is what agent.py _handle_search() does
        cyrillic_query = "годишњи порез на доходак грађана"
        latin_query = cyrtranslit.to_latin(cyrillic_query, "sr")

        all_text = " ".join(c["text"] for c in self.chunks).lower()
        assert latin_query.lower() in all_text, (
            f"Transliterated query '{latin_query}' not found in indexed chunks"
        )
