# Admin vodic

## Dobijanje vaseg Chat ID-a

1. Otvorite bota u Telegramu t.me/MariMarRacunovodstvoBot
2. Posaljite `/start`
3. Bot ce vam prikazati vas **korisnciki ID** (broj)
4. Postojeci admin vas dodaje komandom `/addadmin <vas ID>`

---

## Upravljanje korisnicima

Klijent salje `/start` botu i dobija svoj ID. Taj ID vam prosledi (Viber,
poruka, uzivo — svejedno).

| Komanda                          | Opis                                 |
| -------------------------------- | ------------------------------------ |
| `/adduser 123456 Petar Petrovic` | Dodaj korisnika (ime je opciono)     |
| `/adduser 123456 Novo Ime`       | Azuriraj ime postojeceg korisnika    |
| `/removeuser 123456`             | Ukloni korisnika                     |
| `/users`                         | Prikazi sve korisnike (sa imenima)   |
| `/addadmin 123456 Ime Admina`    | Dodaj admina (ime je opciono)        |
| `/removeadmin 123456`            | Ukloni admina                        |
| `/broadcast <poruka>`            | Pošalji obaveštenje svim korisnicima |

Ako je lista korisnika prazna — svi imaju pristup (bez ogranicenja). Ne mozete
ukloniti sami sebe kao admina.

### Broadcast

`/broadcast <poruka>` salje obavestenje svim korisnicima i adminima. Poruka mora
biti kraca od 4096 karaktera (Telegram limit). Slanje ide u pozadini — odmah
dobijate potvrdu, a na kraju izvestaj koliko je uspesno poslato.

### Korisnicke komande (svi korisnici)

| Komanda      | Opis                                 |
| ------------ | ------------------------------------ |
| `/start`     | Prikazi dobrodoslicu i korisnciki ID |
| `/clear`     | Obrisi istoriju razgovora            |
| `/help`      | Prikazi listu komandi                |
| `/dokumenti` | Prikazi dokumente u bazi znanja      |

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
