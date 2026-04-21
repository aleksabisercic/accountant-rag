# Arhitektura vestina (Skills)

## Sta su vestine

**Alati (Tools)** = atomicne funkcije sa ulazom/izlazom. **RAG** = preuzimanje
cinjenica. **Vestine (Skills)** = proceduralno znanje koje menja ponasanje
agenta — paketi instrukcija, logike odlucivanja i opcionih skripti koji se
ucitavaju po potrebi.

Kljucni mehanizam je **postepeno otkrivanje** (3 nivoa):

1. **Uvek prisutno**: ime + opis (~100 tokena/vestina) — "meni"
2. **Na zahtev**: pune instrukcije se ucitavaju kada agent odredi relevantnost
3. **Detaljno**: referencni fajlovi, sabloni, skripte — ucitavaju se kada
   instrukcije upucuju na njih

Ovaj obrazac je konsenzus industrije: Anthropic, OpenAI Codex, Google ADK,
LangChain. Kodifikovan kao `SKILL.md` otvoreni standard (agentskills.io, dec
2025).

---

## Zasto je ovo bitno (brojke)

- 50 alata staticno ucitanih = ~72K tokena/zahtev. Sa odlozenim ucitavanjem =
  ~8.7K tokena (**88% smanjenje**)
- Anthropic merenje: tacnost Opus-a skocila **49% → 74%** sa pretragom alata
  naspram staticnog ucitavanja
- LangChain merenje: uspesnost zadataka **29% → 95%** sa vestinama
- Tacnost izbora **naglo opada** posle ~20 alata po agentu

---

## Pregled industrije

| Framework         | Obrazac                                                | Napomene                                |
| ----------------- | ------------------------------------------------------ | --------------------------------------- |
| Claude Code       | SKILL.md + 3 nivoa + subagenti                         | Zlatni standard. Hot-reload, filtriranje |
| OpenAI Codex      | Isti SKILL.md standard                                 | Eksplicitna/implicitna kontrola poziva  |
| Google ADK        | `SkillToolset` → auto `list_skills`, `load_skill`      | Najautomatizovaniji                     |
| LangChain         | `SkillStore.scan()` (frontmatter) + lazy `load()`      | Dobar kesing sloj                       |
| CrewAI / Semantic | Staticna dodela alata po agentu                        | Jednostavniji ali ne skalira            |

---

## Kompromisi

| Dimenzija      | Prednost                                      | Mana                                                         |
| -------------- | --------------------------------------------- | ------------------------------------------------------------ |
| **Tokeni**     | 90% smanjenje naspram staticnog ucitavanja     | Dodatni round-trip za ucitavanje (1 poziv alata)             |
| **Pouzdanost** | Fokusiran kontekst → bolji izbor alata        | Kvalitet opisa je sve — los opis = pogresna vestina          |
| **Odrzavanje** | Markdown fajlovi, git verzionisani, bez deploy-a | 26% community vestina sadrzi ranjivosti (arxiv, 42K vestina) |
| **Latencija**  | Lazy load + kes = brzo posle prvog koriscenja | Inicijalno ucitavanje dodaje ~1 API krug                     |
| **Kompleksnost** | Opravdano pri 10+ mogucnosti                | Preterano za ≤5 alata                                       |

**Kljucni uvid**: Za sadasnji setup sa 3 alata, vestine su preuranjena
optimizacija. Postaju bitne pri ~8-10+ mogucnosti. Ali ako je plan prosiriti sa
"specificnim kalkulacijama i procedurama," idemo ka tome.

---

## Trenutna arhitektura

- **Agent petlja**: `agent.py` — raw Anthropic SDK, max 5 krugova, hardkodirana
  lista alata
- **Alati**: `search_documents`, `calculator`, `web_search` (native) — staticni,
  bez registra
- **System prompt**: statican fajl (`prompts/system.txt`), srpski, ucita se
  jednom
- **Nema mehanizma za prosirenje** — alati se rutiraju preko
  `if block.name == "tool_name"`

---

## Predlog implementacije

### Opcija A: Lagana (preporucena za nasu skalu)

**Pretpostavka**: Dodajemo 5-15 domenski specificnih procedura (poreski
obracuni, regulatorni upiti, tokovi podnosenja).

```
accountant-rag/
  skills/
    pdv_obracun/
      SKILL.md          # frontmatter + instrukcije
      formulas.json     # referencni podaci
    obracun_zarada/
      SKILL.md
    poresko_podnosenje/
      SKILL.md
```

Implementacija u `agent.py`:

1. **Skeniranje** `skills/` pri pokretanju → izgradnja kataloga (samo ime +
   opis)
2. **Ubacivanje kataloga** u system prompt kao dodatak (~50-100 tokena/vestina)
3. **Dodavanje alata**: `load_skill(name)` → vraca puno SKILL.md telo + listu
   dostupnih referencnih fajlova
4. **Dodavanje alata**: `read_skill_file(name, filename)` → vraca specifican
   referencni fajl
5. **Agent odlucuje** kada da ucita vestinu na osnovu korisnickog upita naspram
   opisa u katalogu

Potrebne izmene:

- `agent.py`: dodati `SkillStore` klasu (~80 linija), 2 nova alata, 2 nova
  handler-a u petlji
- System prompt: dodati sekciju sa katalogom vestina
- Bez promena na Telegram handler, embeddings, ili vector store

### Opcija B: Potpuno rutiranje (preuranjena za sada)

Vise specijalizovanih agenata, svaki sa svojim podskupom vestina. Orkestrator
rutira po nameri. Razmisliti tek pri 30+ vestina ili visedomenska podrska.

---

## Primer vestine

```yaml
# skills/pdv_obracun/SKILL.md
---
name: pdv-obracun
description: "Obracun PDV-a (VAT calculation) — koristi kada korisnik
  pita o PDV stopi, osnovici, ili obracunu poreza na dodatu vrednost."
---

## Kada koristiti
Korisnik pita o PDV-u, poreskoj osnovici, ili obracunu poreza.

## Procedura
1. Utvrdi tip prometa (dobra vs. usluge)
2. Utvrdi PDV stopu: opsta (20%), posebna (10%), oslobodjeno (0%)
3. Za obracun koristi calculator tool:
   - Osnovica = ukupan_iznos / (1 + stopa)
   - PDV = ukupan_iznos - osnovica
4. Za mesoviti promet, razdvoji stavke po stopama

## Referenca
Pogledaj `formulas.json` za sve vazece stope i pragove.
```

Tok rada kada korisnik pita o PDV-u:

1. Agent vidi `pdv-obracun` u katalogu sa opisom → prepoznaje relevantnost
2. Poziva `load_skill("pdv-obracun")` → dobija punu proceduru
3. Prati proceduru, koristeci `calculator` alat
4. Po potrebi poziva `read_skill_file("pdv-obracun", "formulas.json")` za tabele
   stopa

---

## Pretpostavke

1. **Vestine su procedure na srpskom** — sadrzaj je domensko znanje, ne kod
2. **5-15 vestina kratkorocno** — ako znacajno vise, preispitati rutiranje
3. **Vestine su staticne** — kreirane od strane tima, ne korisnika (bez
   sigurnosnog gate-a)
4. **Postojeci alati ostaju** — vestine uce *kada i kako* da se koriste, ne
   zamenjuju ih
5. **Jedan agent je dovoljan** — nema potrebe za multi-agent orkestracija na ovoj
   skali

---

## Plan implementacije (prioritet)

1. `SkillStore` klasa: skeniranje frontmatter-a, lazy-load sadrzaja, kesiranje
2. Dva alata: `load_skill`, `read_skill_file`
3. Izmena system prompt-a: ubacivanje kataloga
4. 2-3 inicijalne vestine kao proof of concept
5. Config unos u `config.yaml` za putanju do skills direktorijuma

---

## Preporuceni resursi

**Osnovno (citati prvo)**

1. Anthropic — Building Effective Agents — kanonski mentalni model za agent
   obrasce (rutiranje, nizanje, orkestrator-radnici)
2. Anthropic — Advanced Tool Use — obrazac postepenog otkrivanja sa konkretnim
   merenjima
3. AgentSkills.io — What Are Skills — SKILL.md specifikacija otvorenog standarda

**Orijentisano na implementaciju**

4. LangChain Skills Blog — SkillStore implementacija, scan/load obrazac
5. Google ADK Skills Guide — cetiri obrasca implementacije (inline, file-based,
   external, meta-skills)
6. LangGraph Scalable Skills — prakticni tutorijal za 3-nivoa obrazac

**Dublja analiza arhitekture**

7. Claude Code Full Stack Architecture — dekompozicija vestina, hook-ova,
   subagenata i MCP-a
8. OpenAI Codex Skills docs — isti standard, drugaciji izbori implementacije

**Bezbednost**

9. arXiv: Agent Skills for LLMs — anketa koja je otkrila 26% stopu ranjivosti u
   community vestinama
