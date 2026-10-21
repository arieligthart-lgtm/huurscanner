# Huurscanner Utrecht

Scant elke 5 minuten Pararius, Funda, Huurwoningen.nl, Kamernet, Vesteda, Wonen bij Bouwinvest, ikwilhuren.nu (MVGM) en 123Wonen. Past een nieuwe woning bij je criteria, dan krijg je direct een Telegram-bericht met foto, prijs, m², slaapkamers en de link.

Criteria staan in `config.yaml`: Utrecht, max € 2.000, min 60 m², min 2 slaapkamers.

---

## Installatie (± 15 minuten, eenmalig)

### Stap 1. Telegram-bot maken

1. Open Telegram en zoek **@BotFather**.
2. Stuur `/newbot`, kies een naam (bijv. *Huurscanner Arie*) en een gebruikersnaam die eindigt op `bot`.
3. BotFather geeft je een **token** (lijkt op `7412345678:AAH...`). Bewaar die even.
4. Klik op de link naar je nieuwe bot en stuur hem `/start`. Dit is nodig, anders mag de bot jou geen berichten sturen.

### Stap 2. GitHub-repository maken

1. Maak een gratis account op [github.com](https://github.com) als je die nog niet hebt.
2. Klik rechtsboven op **+ → New repository**.
3. Naam: `huurscanner`. Kies **Public** (dan zijn de minuten onbeperkt gratis; je token blijft geheim, zie stap 3).
4. Klik **Create repository**.
5. Klik op **uploading an existing file** en sleep alle bestanden uit de map `huurscanner` erin.
   * De map `.github` is op een Mac verborgen. Druk in Finder op **Cmd + Shift + .** om hem zichtbaar te maken en sleep hem mee.
   * Lukt dat niet: maak hem daarna los aan via **Add file → Create new file**, typ als naam `.github/workflows/scan.yml` en plak de inhoud van dat bestand.
6. Klik **Commit changes**.

### Stap 3. Token veilig toevoegen

1. In je repository: **Settings → Secrets and variables → Actions → New repository secret**.
2. Name: `TELEGRAM_BOT_TOKEN`, Secret: het token uit stap 1. Opslaan.

### Stap 4. Testen en starten

1. Ga naar het tabblad **Actions**. Zie je een melding over workflows, klik dan op de groene knop om ze toe te staan.
2. Klik links op **Huurscanner → Run workflow**, vink **Alleen een testbericht** aan en klik **Run workflow**.
3. Binnen een minuut krijg je in Telegram: *Huurscanner is gekoppeld.*
4. Klik nog een keer op **Run workflow**, nu zonder vinkje. Je krijgt een overzicht van wat er nu al online staat. Daarna draait hij vanzelf en krijg je alleen nog nieuwe woningen.

Klaar. Je laptop mag dicht.

---

## Goed om te weten

* **Snelheid.** GitHub plant de scan elke 5 minuten, maar loopt bij drukte soms 5 tot 15 minuten uit. Voor echt minuut-tot-minuut snelheid kun je later een kleine server nemen (± € 5 per maand); de code werkt daar ongewijzigd.
* **Bron geblokkeerd?** Sommige sites (vooral Funda en Huurwoningen.nl) weren soms servers van GitHub. Faalt een bron 6 scans op rij, dan krijg je één waarschuwing in Telegram en zodra hij weer werkt een herstelmelding. De andere bronnen gaan gewoon door.
* **Prijs inclusief of exclusief.** Bij Vesteda is de getoonde prijs kale huur, bij Kamernet soms excl. G/W/E. Dat staat dan als waarschuwing in de melding. Bij Bouwinvest tellen de servicekosten al mee.
* **Slaapkamers.** Pararius, Funda, Vesteda en Bouwinvest geven het aantal slaapkamers. Huurwoningen.nl geeft alleen kamers; dan geldt 3 kamers of meer. Kan de scanner iets niet vinden, dan krijg je de woning tóch, met een ⚠️ erbij. Liever niet? Zet `strikt: true` in `config.yaml`.
* **Dubbele meldingen.** Staat dezelfde woning (met huisnummer) op meerdere sites, dan krijg je hem maar één keer.

## Aanpassen

Alles staat in `config.yaml`. Open het bestand op GitHub, klik op het potloodje, pas aan en klik **Commit changes**. De volgende scan gebruikt de nieuwe instellingen.

* Andere stad of budget: pas `criteria` aan.
* Bron uitzetten: zet hem op `false` onder `bronnen`.
* Makelaar toevoegen: kopieer een blok onder `extra_makelaars`, vul de aanbodpagina en een stukje van de woninglink in.

## Pauzeren of stoppen

**Actions → Huurscanner → ⋯ → Disable workflow.** Weer aanzetten kan op dezelfde plek.

## Lokaal draaien (optioneel)

```bash
pip install -r requirements.txt
python scanner.py --dry-run --show-all     # alles tonen, niets versturen
TELEGRAM_BOT_TOKEN=... python scanner.py   # echte run
```
