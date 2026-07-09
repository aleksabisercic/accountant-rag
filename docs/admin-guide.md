# Admin vodic

Bot: t.me/MariMarRacunovodstvoBot

---

## Onboarding klijenata — korak po korak

Postoje tri nacina da klijent dobije pristup. U 90% slucajeva koristite
**pozivni link** — najbrzi je i klijent ne mora nista da kuca.

### Pre pocetka (checklist)

- [ ] Imate ime i prezime klijenta i naziv firme
- [ ] Imate kontakt kanal ka klijentu (WhatsApp, Viber, email — svejedno)
- [ ] Klijent ima Telegram nalog (ako nema, prvo neka instalira Telegram)

### Primer 1: Pozivni link (preporucen nacin)

Zelite da dodate Petra Petrovica iz firme "Petrovic doo".

1. Otvorite chat sa botom i posaljite:

   ```
   /invite Petar Petrovic, Petrovic doo
   ```

2. Bot odmah vraca jednokratni link, npr:

   ```
   Pozivnica za Petar Petrovic, Petrovic doo:

   https://t.me/MariMarRacunovodstvoBot?start=Ab3xYz9...

   Link vazi 7 dana. Klijent samo klikne link i automatski dobija pristup.
   ```

3. Kopirajte link i posaljite ga klijentu, npr. uz poruku:

   > Poštovani Petre, pripremili smo Vam pristup našem asistentu za
   > računovodstvena pitanja. Kliknite na link ispod i odmah možete da
   > postavljate pitanja (potreban je Telegram nalog):
   > https://t.me/MariMarRacunovodstvoBot?start=Ab3xYz9...

4. Klijent klikne link → Telegram otvori bota → klijent dobija poruku
   dobrodoslice i odmah moze da pita.
5. Svi admini dobijaju obavestenje da je pozivnica iskoriscena.
6. Proverite sa `/users` da se klijent pojavio na listi.

Napomene:
- Link je **jednokratan** — za svakog klijenta napravite novi `/invite`.
- Link vazi **7 dana**; ako istekne, samo napravite novi.
- Ime i firma koje unesete u `/invite` postaju labela korisnika u `/users`
  i statistici — unesite ih uredno.

### Primer 2: Samoregistracija (klijent je sam nasao bota)

Klijent otvori bota bez pozivnog linka i posalje `/start`:

1. Bot pita klijenta za ime i prezime, zatim za naziv firme.
2. Svi admini dobijaju obavestenje ovog oblika:

   ```
   🔔 Nov zahtev za pristup
   Ime: Petar Petrovic
   Firma: Petrovic doo
   Telegram: @petar_p
   ID: 123456789
   [✅ Odobri]  [❌ Odbij]
   ```

3. Admin klikne **✅ Odobri** → klijent automatski dobija pristup i
   obavestenje da moze da koristi bota.
4. Admin klikne **❌ Odbij** → klijent dobija obavestenje da je zahtev
   odbijen.

Zahtev istice posle **24h** ako niko ne reaguje — klijent tada moze ponovo
da posalje `/start` i podnese nov zahtev.

### Primer 3: Rucno dodavanje (kada znate Telegram ID)

Koristite kada klijent vec ima Telegram ID koji vam je poslao (ID moze da
sazna npr. preko bota @userinfobot), ili kada zelite da vratite ranije
uklonjenog korisnika:

```
/adduser 123456789 Petar Petrovic, Petrovic doo
```

Ista komanda sa novim imenom azurira labelu postojeceg korisnika.

### Najcesci problemi

| Problem                                        | Resenje                                                        |
| ---------------------------------------------- | -------------------------------------------------------------- |
| Klijent javlja "link nije validan ili je istekao" | Link je stariji od 7 dana ili vec iskoriscen — napravite nov `/invite` |
| Klijent kliknuo link, a vec ima pristup        | Sve je u redu — bot mu javi da vec ima pristup, link ostaje neiskoriscen |
| Poslali ste link pogresnoj osobi               | Ako link jos nije kliknut, napravite nov za pravu osobu; ako jeste, uklonite uljeza sa `/removeuser <ID>` (ID vidite u obavestenju o iskoriscenoj pozivnici ili u `/users`) |
| Zahtev za registraciju je istekao (24h)        | Klijent salje `/start` ponovo, ili mu posaljite pozivni link   |
| Klijent vise ne treba da ima pristup           | `/removeuser <ID>` — ID nadjite u `/users`                     |

---

## Dodavanje novog admina

1. Novi admin otvori bota (t.me/MariMarRacunovodstvoBot) i posalje `/start`
2. Bot ce ga voditi kroz registraciju (ime, firma) — postojeci admini
   dobijaju obavestenje u kome pise njegov **ID**
3. Postojeci admin ga doda komandom: `/addadmin <ID> Ime`

Alternativno, ako osoba vec zna svoj Telegram ID (npr. preko @userinfobot),
preskace registraciju — admin je odmah doda sa `/addadmin <ID> Ime`.

Ne mozete ukloniti sami sebe kao admina.

---

## Sve admin komande

| Komanda                          | Opis                                       |
| -------------------------------- | ------------------------------------------ |
| `/invite Ime, Firma`             | Kreiraj pozivni link za klijenta (vazi 7 dana) |
| `/adduser <ID> [ime]`            | Dodaj korisnika rucno                      |
| `/removeuser <ID>`               | Ukloni korisnika                           |
| `/users`                         | Prikazi sve korisnike (sa imenima)         |
| `/addadmin <ID> [ime]`           | Dodaj admina                               |
| `/removeadmin <ID>`              | Ukloni admina                              |
| `/stats [dana]`                  | Statistika (podrazumevano 7 dana)          |
| `/pitanja [dana]`                | Lista pitanja (podrazumevano 7, max 90 dana) |
| `/dokumenti`                     | Lista dokumenata u bazi znanja             |
| `/broadcast <poruka>`            | Posalji obavestenje svim korisnicima       |

Ako je lista korisnika prazna — svi imaju pristup (bez ogranicenja).

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

Napomena: `/dokumenti` je od aprila 2026. dostupna samo adminima — spisak
indeksiranih PDF-ova je interna informacija.

---

## Otpremanje dokumenata (samo admini)

Posaljite PDF fajl direktno u chat sa botom. Bot ce ga obraditi i dodati u bazu
znanja — bez redeploy-a, promena je odmah aktivna.

- Maksimalna velicina: 20 MB
- Samo tekstualni PDF-ovi (skenirani nisu podrzani — bot ce javiti gresku)
- Novi PDF sa istim imenom fajla zamenjuje stari
- Preporuka: do 35 stranica po fajlu (bolji kvalitet pretrage); dokumenti
  koji se seku na vise od 600 delova se odbijaju — podelite ih na manje fajlove

---

## Statistika

`/stats` — poslednjih 7 dana

`/stats 30` — poslednjih 30 dana

Prikazuje: broj pitanja, jedinstvene korisnike, prosecno vreme odgovora i broj
eskalacija.

---

## Pregled pitanja i nedeljni izvestaj

`/pitanja [dana]` ispisuje sva pitanja korisnika u zadatom periodu
(podrazumevano 7 dana, maksimalno 90), u formatu
`• <pitanje> (<ime ili ID korisnika>)`.

Pored toga, **svakog ponedeljka u 09:00** (vreme Beograd) bot automatski salje
svim adminima listu svih pitanja iz protekle nedelje — isti sadrzaj kao
`/pitanja 7`, bez potrebe da iko pokrece komandu.

---

## Eskalacije (automatska obavestenja)

Kada bot proceni da nije siguran u odgovor ili nema dovoljno informacija,
automatski salje obavestenje svim adminima sa originalnim pitanjem korisnika i
odgovorom bota.

Kada se eskalacija okida:
- **Niska pouzdanost** — bot je dao opsti odgovor bez konkretnog izvora.
- **Nema odgovora** — bot nije uspeo da nadje pouzdane informacije.

Sta admin treba da uradi: po potrebi kontaktirajte klijenta direktno i dajte
tacan odgovor. Eskalacije se beleze u statistici (`/stats` → broj eskalacija).

Klijent automatski u svom odgovoru dobija napomenu i kontakt podatke Mari-Mar
doo (telefoni, kalendar) — disclaimere ne pise sam bot, vec se dodaju
automatski na osnovu nivoa pouzdanosti odgovora (visok / srednji / nizak /
nema odgovora).
