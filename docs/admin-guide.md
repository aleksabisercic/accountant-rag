# Admin vodic

## Dodavanje novog admina

1. Novi admin otvori bota: t.me/MariMarRacunovodstvoBot
2. Posaljite `/start` — bot prikazuje korisnciki ID
3. Postojeci admin ga dodaje: `/addadmin <ID> Ime`

---

## Dodavanje korisnika (klijenata)

### Pozivni link (preporucen nacin)

1. Posaljite botu: `/invite Petar Petrovic, Firma d.o.o.`
2. Bot vraca jednokratni link (vazi 7 dana)
3. Posaljite link klijentu (WhatsApp, email, Viber — svejedno)
4. Klijent klikne link → automatski dobija pristup
5. Svi admini dobijaju obavestenje da je pozivnica iskoriscena

### Registracija (klijent sam trazi pristup)

Ako klijent otvori bota bez pozivnog linka:

1. Klijent salje `/start`
2. Bot pita za ime i prezime, zatim za naziv firme
3. Svi admini dobijaju obavestenje sa dugmicima **✅ Odobri** / **❌ Odbij**
4. Admin klikne ✅ → klijent automatski dobija pristup i obavestenje
5. Admin klikne ❌ → klijent dobija obavestenje da je zahtev odbijen

Zahtev istice posle 24h ako niko ne reaguje. Klijent moze probati ponovo.

### Rucno dodavanje

| Komanda                          | Opis                                 |
| -------------------------------- | ------------------------------------ |
| `/adduser 123456 Petar Petrovic` | Dodaj korisnika rucno (ime opciono)  |
| `/adduser 123456 Novo Ime`       | Azuriraj ime postojeceg korisnika    |
| `/removeuser 123456`             | Ukloni korisnika                     |

---

## Sve admin komande

| Komanda                          | Opis                                 |
| -------------------------------- | ------------------------------------ |
| `/invite Ime, Firma`             | Kreiraj pozivni link za klijenta     |
| `/adduser <ID> [ime]`            | Dodaj korisnika rucno                |
| `/removeuser <ID>`               | Ukloni korisnika                     |
| `/users`                         | Prikazi sve korisnike (sa imenima)   |
| `/addadmin <ID> [ime]`           | Dodaj admina                         |
| `/removeadmin <ID>`              | Ukloni admina                        |
| `/stats [dana]`                  | Statistika (podrazumevano 7 dana)    |
| `/pitanja [dana]`                | Lista pitanja (max 90 dana)          |
| `/broadcast <poruka>`            | Pošalji obaveštenje svim korisnicima |

Ako je lista korisnika prazna — svi imaju pristup (bez ogranicenja). Ne mozete
ukloniti sami sebe kao admina.

### Broadcast

`/broadcast <poruka>` salje obavestenje svim korisnicima i adminima. Poruka mora
biti kraca od 4096 karaktera (Telegram limit). Slanje ide u pozadini — odmah
dobijate potvrdu, a na kraju izvestaj koliko je uspesno poslato.

---

## Korisnicke komande (svi korisnici)

| Komanda      | Opis                                    |
| ------------ | --------------------------------------- |
| `/start`     | Registracija ili pozdravna poruka       |
| `/clear`     | Obrisi istoriju razgovora               |
| `/help`      | Prikazi listu komandi                   |
| `/dokumenti` | Prikazi dokumente u bazi znanja         |

---

## Otpremanje dokumenata

Posaljite PDF fajl direktno u chat sa botom. Bot ce ga obraditi i dodati u bazu
znanja.

- Maksimalna velicina: 20 MB
- Samo tekstualni PDF-ovi (skenirani nisu podrzani)
- Novi PDF sa istim imenom zamenjuje stari

---

## Statistika

`/stats` — poslednjih 7 dana

`/stats 30` — poslednjih 30 dana

Prikazuje: broj pitanja, jedinstvene korisnike, prosecno vreme odgovora.

---

## Eskalacije (automatska obavestenja)

Kada bot proceni da nije siguran u odgovor ili nema dovoljno informacija, automatski salje obavestenje svim adminima sa originalnim pitanjem korisnika i odgovorom bota.

Kada se eskalacija okida:
- **Niska pouzdanost** — bot je dao opsti odgovor bez konkretnog izvora.
- **Nema odgovora** — bot nije uspeo da nadje pouzdane informacije.

Sta admin treba da uradi: po potrebi kontaktirajte klijenta direktno i dajte tacan odgovor. Eskalacije se belezi u statistici (`/stats` → broj eskalacija).

Klijent automatski u svom odgovoru dobija napomenu i kontakt podatke Mari-Mar doo (telefoni, kalendar) — disclaimere ne pise sam bot, vec se dodaju automatski na osnovu nivoa pouzdanosti odgovora (visok / srednji / nizak / nema odgovora).
