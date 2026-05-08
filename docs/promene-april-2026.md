# Sta je novo (17. april — 8. maj 2026)

Sazet pregled promena uvedenih u poslednje dve nedelje.

---

## 1. Novi nacin onboardinga klijenata

Ranije: admin je rucno dodavao svaki chat ID. Sada: dve automatske opcije.

**Pozivni link (preporuceno).** Admin kuca `/invite Petar Petrovic, Firma d.o.o.`,
bot vraca jednokratni link koji vazi 7 dana. Klijent klikne — pristup automatski
odobren, svi admini dobijaju obavestenje da je link iskoriscen.

**Samo-registracija.** Ako klijent otvori bota bez linka, bot ga vodi kroz
formu (ime, naziv firme), a admini dobijaju zahtev sa dugmicima ✅ Odobri /
❌ Odbij. Jedan klik — pristup je dat.

Detalji: `docs/admin-guide.md`.

---

## 2. Pregled pitanja korisnika — `/pitanja`

Nova admin komanda. Ispisuje sva pitanja korisnika u zadatom periodu.

| Komanda          | Opis                                          |
| ---------------- | --------------------------------------------- |
| `/pitanja`       | Pitanja iz poslednjih 7 dana (podrazumevano)  |
| `/pitanja 30`    | Poslednjih 30 dana (max 90)                   |

Format: `• <pitanje> (<ime ili ID korisnika>)`.

---

## 3. Automatski nedeljni izvestaj (ponedeljak 09:00)

Svakog ponedeljka u 09:00 (vreme Beograd) bot automatski salje svim adminima
listu svih pitanja iz protekle nedelje. Isti format kao `/pitanja 7`.

Cilj: admin ima nedeljni uvid u to sta klijenti pitaju, bez potrebe da rucno
pokrece komandu.

---

## 4. `/dokumenti` — sada samo za admine

Komanda za listu dokumenata u bazi znanja vise nije vidljiva obicnim
korisnicima. Sklonjena je iz korisnickog menija i prebacena u admin meni.

Razlog: spisak indeksiranih PDF-ova je interna informacija — klijenti ne
moraju da vide kako je baza znanja organizovana.

---

## 5. Tacniji odgovori bota

- **Vise alata po pitanju:** bot sada moze koristiti do 5 alata (pretraga
  baze, web pretraga, kalkulacije) po jednom pitanju umesto 3. Rezultat:
  manje "ne znam" odgovora kod kompleksnijih pitanja.
- **Vremenska zona:** svi datumi i racunice rokova rade u beogradskoj zoni
  (umesto UTC). Vazno kod pitanja tipa "kad istice rok za...".

---

## 6. Cisci interfejs odgovora

Web linkovi se vise ne prikazuju u footer-u odgovora — bot citira samo
dokumente iz baze znanja. Kraci, pregledniji odgovori; web pretraga se i
dalje koristi interno kada je potrebna.

---

## 7. Napomene po nivou pouzdanosti odgovora

Bot sada na kraju svakog odgovora automatski dodaje napomenu i kontakt
podatke Mari-Mar doo (telefoni, kalendar) — prilagodjenu nivou pouzdanosti
odgovora:

- **Visok** — kratka napomena, "mozete kontaktirati za potvrdu".
- **Srednji** — napomena da odgovor zavisi od specificnosti situacije.
- **Nizak** — sira napomena da je za odluke neophodna konsultacija.
- **Nema odgovora** — bot otvoreno kaze da nema pouzdane informacije i
  upucuje korisnika na strucni tim.

Pitanja van opsega (ne-racunovodstvo) ne dobijaju napomenu.

Eskalacije adminima sada se okidaju i kod **niske pouzdanosti**, ne samo
kada bot nema odgovor — admin ranije vidi gde je bot nesiguran. Detalji:
`docs/admin-guide.md` (sekcija "Eskalacije").

---

## Sta klijent treba da uradi

Nista. Sve promene su automatske. Postojeci korisnici nastavljaju normalno
da koriste bota; admini imaju dve nove komande (`/invite`, `/pitanja`) i
dobijace nedeljni izvestaj svakog ponedeljka.
