# Surf forecast — Scheveningen Zuid & Vlugtenburg

Een gratis, geautomatiseerde surf forecast per e-mail, 2x per week (maandag- en
vrijdagochtend), speciaal voor jouw niveau (ervaren beginner / intermediate)
en jouw twee vaste spots: **Scheveningen Zuid** en **Vlugtenburg**.

Geen betaald abonnement nodig: de golf-, wind- en getijdata komen gratis van
[Open-Meteo](https://open-meteo.com/) en het versturen draait gratis op
GitHub Actions.

## Hoe het werkt

1. `.github/workflows/surf-forecast.yml` start elke maandag- en vrijdagochtend
   (06:00 UTC) automatisch `scripts/surf_forecast.py`.
2. Het script haalt golfhoogte, golfperiode, swellrichting, getij, wind en
   zonsopkomst/-ondergang op voor beide spots (komende ~4 dagen).
3. Per dag en per spot wordt beoordeeld of de omstandigheden geschikt zijn
   voor een ervaren beginner/intermediate surfer (niet te vlak, niet te grof/
   hol, wind en swellrichting meewegend). Te kleine of te gevorderde dagen
   worden overgeslagen.
4. Van de goede momenten wordt een verhalende tekst gemaakt (net als de
   Surfgirls-mails) mét onderbouwing: waarom dat tijdstip, wat de wind doet,
   hoe het getij ervoor staat.
5. Het resultaat wordt als e-mail verstuurd.

## Eenmalige setup

Je hebt een verzendend e-mailaccount nodig (los van het adres waar je de
forecast op ontvangt). Het makkelijkst is een Gmail-account met een
zogeheten "app-wachtwoord":

1. Heb je nog geen Gmail-account dat je hiervoor wilt gebruiken? Maak er
   gratis eentje aan op [accounts.google.com](https://accounts.google.com).
2. Zet 2-stapsverificatie aan op dat account: Google-account → Beveiliging →
   2-stapsverificatie.
3. Ga naar [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords),
   maak een app-wachtwoord aan (bijv. voor "Mail") en bewaar de 16 tekens die
   je krijgt — dat heb je zo nodig.
4. Ga in deze GitHub-repo naar **Settings → Secrets and variables → Actions →
   New repository secret** en voeg deze secrets toe:

   | Naam | Waarde |
   |---|---|
   | `SMTP_HOST` | `smtp.gmail.com` |
   | `SMTP_PORT` | `465` |
   | `SMTP_USERNAME` | je Gmail-adres |
   | `SMTP_PASSWORD` | het 16-tekens app-wachtwoord van stap 3 |
   | `FROM_EMAIL` | hetzelfde Gmail-adres als `SMTP_USERNAME` |
   | `TO_EMAIL` | `Jacquelinevanduijvenbode@hotmail.com` (of een ander adres) |

Gebruik je liever een ander mailaccount (Outlook, een eigen domein, etc.)?
Vul dan gewoon de bijbehorende SMTP-gegevens in — het script werkt met elke
standaard SMTP-over-SSL provider.

## Testen

- **Handmatig triggeren**: ga naar de tab **Actions** in GitHub → workflow
  "Surf forecast" → **Run workflow**. Zo hoef je niet tot maandag/vrijdag te
  wachten om te zien of alles werkt.
- **Lokaal, zonder mail te versturen**:

  ```bash
  pip install -r requirements.txt
  python scripts/surf_forecast.py --dry-run
  ```

  Dit print de forecast-tekst in je terminal in plaats van 'm te mailen —
  handig om te checken of de tekst/beoordeling klopt zonder secrets nodig te
  hebben.

## Instellingen aanpassen

Alles staat bovenin `scripts/surf_forecast.py`:

- `SPOTS`: per spot de coördinaten, ideale windrichting (aflandig),
  ideale swellrichting en de golfhoogte-range die bij jouw niveau past.
  Vind je de forecast te vaak "nee" of juist te vaak "ja" zeggen, stel dan
  `wave_min`/`wave_max` bij.
- `DAYS_AHEAD`: hoeveel dagen vooruit er in elke mail staan (nu 4).
- Cron-tijdstip: pas de `cron`-regel in
  `.github/workflows/surf-forecast.yml` aan als je liever een ander moment
  dan 06:00 UTC (7-8 uur 's ochtends NL-tijd) wilt.

## Kanttekening

Dit is een automatisch gegenereerde inschatting op basis van gratis
weermodel-data (geen menselijke check zoals bij een betaalde dienst). De
getijtijden zijn een benadering op basis van het golfmodel, niet een
officiële getijtabel. Check voor je vertrekt dus altijd nog even een
surfcam of app als de mail een grensgeval aangeeft ("twijfel").
