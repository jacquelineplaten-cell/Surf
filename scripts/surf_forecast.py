#!/usr/bin/env python3
"""
Persoonlijke surf forecast voor Scheveningen Zuid en Vlugtenburg strand.

Haalt golf-, wind- en getijdata op bij Open-Meteo (gratis, geen API key nodig),
beoordeelt de omstandigheden voor een gevorderde beginner / intermediate surfer,
en stuurt een verhalende e-mail met onderbouwing.

Alle drempelwaarden staan in SPOTS hieronder en kun je naar smaak bijstellen.
"""

import os
import random
import smtplib
import ssl
import sys
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

import requests

AMSTERDAM = ZoneInfo("Europe/Amsterdam")
DAYS_AHEAD = 4  # aantal dagen dat we in elke mail meenemen

MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"
WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

DUTCH_DAYS = ["maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag"]

# ---------------------------------------------------------------------------
# Spot-profielen. ideal_wind_dir = windrichting (graden, "waar komt de wind
# vandaan") die aflandig is op deze plek. ideal_swell_dir = (min, max) graden
# waaruit swell hier het beste binnenkomt. wave_min/max = golfhoogte range
# die voor een gevorderde beginner/intermediate surfer leuk en behapbaar is.
# ---------------------------------------------------------------------------
SPOTS = {
    "Scheveningen Zuid": {
        "lat": 52.095,
        "lon": 4.268,
        "ideal_wind_dir": 45,       # noordoost = aflandig
        "ideal_swell_dir": (270, 360),  # west tot noord, optimaal noordwest
        "wave_min": 0.35,
        "wave_max": 1.2,
        "note_too_big": "dat wordt hol en snel, meer iets voor gevorderde surfers",
    },
    "Vlugtenburg": {
        "lat": 52.001,
        "lon": 4.128,
        "ideal_wind_dir": 112,      # oost tot zuidoost = aflandig
        "ideal_swell_dir": (200, 315),  # zuidwest tot noordwest
        "wave_min": 0.3,
        "wave_max": 1.4,
        "note_too_big": "kan behoorlijk grof en stevig worden voor dit plekje",
    },
}


def circular_diff(a, b):
    d = abs(a - b) % 360
    return min(d, 360 - d)


def in_range(value, lo, hi):
    if lo <= hi:
        return lo <= value <= hi
    return value >= lo or value <= hi  # range die over 0/360 heen wikkelt


def fetch_json(url, params):
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_spot_data(spot):
    marine = fetch_json(MARINE_URL, {
        "latitude": spot["lat"],
        "longitude": spot["lon"],
        "hourly": "wave_height,wave_period,swell_wave_height,swell_wave_period,"
                  "swell_wave_direction,sea_level_height_msl",
        "timezone": "Europe/Amsterdam",
        "forecast_days": DAYS_AHEAD + 2,
    })
    weather = fetch_json(WEATHER_URL, {
        "latitude": spot["lat"],
        "longitude": spot["lon"],
        "hourly": "wind_speed_10m,wind_direction_10m,wind_gusts_10m",
        "daily": "sunrise,sunset",
        "timezone": "Europe/Amsterdam",
        "forecast_days": DAYS_AHEAD + 2,
    })

    hours = {}
    m_times = marine["hourly"]["time"]
    for i, t in enumerate(m_times):
        hours[t] = {
            "wave_height": marine["hourly"]["wave_height"][i],
            "swell_period": marine["hourly"]["swell_wave_period"][i],
            "swell_dir": marine["hourly"]["swell_wave_direction"][i],
            "sea_level": marine["hourly"]["sea_level_height_msl"][i],
        }
    w_times = weather["hourly"]["time"]
    for i, t in enumerate(w_times):
        if t not in hours:
            continue
        hours[t]["wind_speed"] = weather["hourly"]["wind_speed_10m"][i]
        hours[t]["wind_dir"] = weather["hourly"]["wind_direction_10m"][i]
        hours[t]["wind_gust"] = weather["hourly"]["wind_gusts_10m"][i]

    daily = {}
    for i, d in enumerate(weather["daily"]["time"]):
        daily[d] = {
            "sunrise": weather["daily"]["sunrise"][i][-5:],
            "sunset": weather["daily"]["sunset"][i][-5:],
        }

    return hours, daily


# ---------------------------------------------------------------------------
# Beoordeling per uur
# ---------------------------------------------------------------------------

def evaluate_hour(spot, h):
    wave = h.get("wave_height")
    wind_speed = h.get("wind_speed")
    wind_dir = h.get("wind_dir")
    gust = h.get("wind_gust")
    period = h.get("swell_period")
    swell_dir = h.get("swell_dir")

    if wave is None or wind_speed is None:
        return "nee", ["geen data"]

    if wave < spot["wave_min"]:
        return "nee", [f"te weinig power ({wave:.1f} m), waarschijnlijk vlak/soep"]
    if wave > spot["wave_max"]:
        return "nee", [f"{wave:.1f} m, {spot['note_too_big']}"]

    wind_diff = circular_diff(wind_dir, spot["ideal_wind_dir"])
    reasons = []

    if wind_diff <= 55:
        if wind_speed <= 30:
            verdict = "top"
            reasons.append("aflandige wind, dus schone golven")
        else:
            verdict = "twijfel"
            reasons.append(f"aflandig maar hard ({wind_speed:.0f} km/u), lastig peddelen")
    elif wind_diff >= 130:
        if wind_speed <= 12:
            verdict = "goed"
            reasons.append("aanlandig maar zwak, water blijft redelijk rustig")
        elif wind_speed <= 20:
            verdict = "twijfel"
            reasons.append("aanlandige wind, verwacht wat chop")
        else:
            verdict = "nee"
            reasons.append(f"stevige aanlandige wind ({wind_speed:.0f} km/u), wordt een klotsbak")
    else:
        if wind_speed <= 22:
            verdict = "goed"
            reasons.append("zijwind, over het algemeen prima surfbaar")
        else:
            verdict = "twijfel"
            reasons.append("stevige zijwind, kan rommelig water geven")

    if verdict == "nee":
        return verdict, reasons

    if period is not None:
        if period < 5 and verdict in ("top", "goed"):
            verdict = "twijfel"
            reasons.append(f"korte periode ({period:.0f} s), dus minder power")
        elif period >= 8 and verdict == "goed":
            verdict = "top"
            reasons.append(f"lange periode ({period:.0f} s), veel energie in de golven")

    if swell_dir is not None and not in_range(swell_dir, *spot["ideal_swell_dir"]):
        if verdict == "top":
            verdict = "goed"
        elif verdict == "goed":
            verdict = "twijfel"
        reasons.append("swellrichting niet helemaal ideaal voor deze plek")

    if gust is not None and gust >= 45 and verdict != "nee":
        verdict = "twijfel"
        reasons.append(f"pittige windstoten tot {gust:.0f} km/u")

    return verdict, reasons


RANK = {"nee": 0, "twijfel": 1, "goed": 2, "top": 3}


def evaluate_spot_day(spot, hours, day_str, sunrise, sunset):
    day_hours = sorted(t for t in hours if t.startswith(day_str))
    daylight = [t for t in day_hours if sunrise <= t[-5:] <= sunset]
    if not daylight:
        daylight = day_hours
    if not daylight:
        return None

    evals = [(t, *evaluate_hour(spot, hours[t])) for t in daylight]
    best_rank = max((RANK[v] for _, v, _ in evals), default=0)
    if best_rank <= RANK["nee"]:
        return None

    # Langste aaneengesloten reeks uren op het beste niveau, zodat we geen
    # los uur aan het begin en een los uur aan het eind samenvoegen tot een
    # nepvenster dat de uren ertussen ook meepakt.
    runs, current = [], []
    for e in evals:
        if RANK[e[1]] == best_rank:
            current.append(e)
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)
    best_run = max(runs, key=len)

    start, end = best_run[0][0][-5:], best_run[-1][0][-5:]
    verdict_word = {3: "top", 2: "goed", 1: "twijfel"}[best_rank]

    # Neem het middelste uur van dit venster als representatief moment, en
    # gebruik ALLEEN de redenen van dat ene uur (niet gemixt met andere uren
    # in het venster, want die kunnen qua wind/richting net anders zijn).
    mid_t = best_run[len(best_run) // 2][0]
    mid = hours[mid_t]
    _, reasons = evaluate_hour(spot, mid)

    return {
        "verdict": verdict_word,
        "rank": best_rank,
        "window": (start, end),
        "window_mid_time": mid_t,
        "wave_height": mid.get("wave_height"),
        "swell_period": mid.get("swell_period"),
        "wind_speed": mid.get("wind_speed"),
        "wind_dir": mid.get("wind_dir"),
        "reasons": reasons,
    }


def find_tide_events(hours, day_str, next_day_str):
    times = sorted(t for t in hours if day_str <= t[:10] <= next_day_str)
    levels = [(t, hours[t]["sea_level"]) for t in times if hours[t].get("sea_level") is not None]
    if len(levels) < 5:
        return []

    # Ruwe hoogtedata bevat naast het getij ook wind/luchtdruk-ruis, die als
    # kleine valse pieken/dalen zou worden gezien. Glad daarom met een
    # voortschrijdend gemiddelde over 3 uur voordat we pieken zoeken.
    smoothed = []
    for i in range(len(levels)):
        lo, hi = max(0, i - 1), min(len(levels), i + 2)
        avg = sum(v for _, v in levels[lo:hi]) / (hi - lo)
        smoothed.append((levels[i][0], avg))

    raw_events = []
    for i in range(1, len(smoothed) - 1):
        t, cur = smoothed[i]
        _, prev = smoothed[i - 1]
        _, nxt = smoothed[i + 1]
        if cur >= prev and cur >= nxt:
            raw_events.append((t, "hoog", cur))
        elif cur <= prev and cur <= nxt:
            raw_events.append((t, "laag", cur))

    # Twee opeenvolgende pieken van hetzelfde type (bv. twee "hoog" na
    # elkaar) zijn altijd ruis rond hetzelfde getij-moment: houd de meest
    # extreme van de twee aan zodat hoog/laag netjes blijven afwisselen.
    merged = []
    for t, kind, level in raw_events:
        if merged and merged[-1][1] == kind:
            more_extreme = (kind == "hoog" and level > merged[-1][2]) or (kind == "laag" and level < merged[-1][2])
            if more_extreme:
                merged[-1] = (t, kind, level)
            continue
        merged.append((t, kind, level))

    # Getij wisselt in NL ongeveer elke 6 uur; events die nog te dicht op
    # elkaar zitten na het mergen zijn nog steeds ruis.
    events = []
    for t, kind, _ in merged:
        if events:
            gap_hours = (datetime.fromisoformat(t) - datetime.fromisoformat(events[-1][0])).total_seconds() / 3600
            if gap_hours < 4:
                continue
        events.append((t, kind))

    return [(t[-5:], kind) for t, kind in events if t.startswith(day_str)]


def describe_tide_for_window(window_mid_hm, day_events):
    """Korte zin over getijfase rond het gekozen surfmoment."""
    if not day_events:
        return ""
    before = [e for e in day_events if e[0] <= window_mid_hm]
    after = [e for e in day_events if e[0] > window_mid_hm]
    if before and after:
        b_t, b_k = before[-1]
        a_t, a_k = after[0]
        richting = "opkomend water" if b_k == "laag" else "afgaand water"
        return f"dat is {richting}, tussen {b_k}water ({b_t}) en {a_k}water ({a_t})"
    if before:
        b_t, b_k = before[-1]
        return f"dat is ruim na {b_k}water van {b_t}"
    a_t, a_k = after[0]
    return f"dat is nog voor {a_k}water om {a_t}"


WIND_COMPASS = [
    (0, "noord"), (45, "noordoost"), (90, "oost"), (135, "zuidoost"),
    (180, "zuid"), (225, "zuidwest"), (270, "west"), (315, "noordwest"), (360, "noord"),
]


def compass(deg):
    if deg is None:
        return "onbekend"
    best = min(WIND_COMPASS, key=lambda x: circular_diff(deg, x[0]))
    return best[1]


FACTOIDS = [
    "Forecast feitje: de periode (in seconden) is de tijd tussen twee golven. "
    "Hoe hoger de periode, hoe meer energie er in de swell zit — 0,8 m met 8 s "
    "kan lekkerder binnenkomen dan 0,8 m met 4 s.",
    "Forecast feitje: aflandige wind (wind die van het land af de zee op waait) "
    "maakt golven meestal schoner en steiler. Aanlandige wind maakt ze eerder "
    "rommelig en afgetopt.",
    "Forecast feitje: bij twijfel is een uur voor tot een uur na laagwater vaak "
    "een veilige gok op een Nederlands strand — dan liggen de zandbanken meestal "
    "het bloot.",
]


def narrate_day(d, spot_results, tide_events):
    day_name = DUTCH_DAYS[d.weekday()]
    heading = f"*{day_name.capitalize()} {d.day}/{d.month}*"

    usable = {name: r for name, r in spot_results.items() if r is not None}
    if not usable:
        return f"{heading}\nWeinig tot geen bruikbare golven bij Scheveningen Zuid of Vlugtenburg. Ik zou hem overslaan."

    lines = [heading]
    if tide_events:
        tide_overview = ", ".join(f"{kind}water rond {t}" for t, kind in tide_events)
        lines.append(f"Getij: {tide_overview}.")

    ranked = sorted(usable.items(), key=lambda kv: kv[1]["rank"], reverse=True)

    mood = {
        "top": ["Dit wordt genieten", "Dit ziet er echt leuk uit", "Hier zou ik voor gaan"],
        "goed": ["Dit kan een leuke sessie worden", "Prima optie", "Dit is het proberen waard"],
        "twijfel": ["Het kan, maar het is geen zekerheidje", "Twijfelgeval, maar zeker de moeite waard om te checken"],
    }

    for spot_name, r in ranked:
        opener = random.choice(mood[r["verdict"]])
        start, end = r["window"]
        wave = r["wave_height"]
        period = r["swell_period"]
        wind_dir_word = compass(r["wind_dir"])
        wind_speed = r["wind_speed"]

        tide_phrase = describe_tide_for_window(r["window_mid_time"][-5:], tide_events)
        tide_text = f" {tide_phrase.capitalize()}." if tide_phrase else ""

        reason_text = "; ".join(r["reasons"])
        wave_txt = f"{wave:.1f} m" if wave is not None else "onbekende hoogte"
        period_txt = f", {period:.0f} s periode" if period else ""

        lines.append(
            f"{opener} bij {spot_name}: rond {start}–{end} zie ik ongeveer {wave_txt}{period_txt}, "
            f"met wind uit het {wind_dir_word} ({wind_speed:.0f} km/u). {reason_text.capitalize()}."
            f"{tide_text}"
        )

    skipped = [name for name, r in spot_results.items() if r is None]
    if skipped:
        lines.append(f"({', '.join(skipped)} zie ik voor {day_name} niet zitten voor jouw niveau.)")

    return "\n".join(lines)


def build_email_body(today):
    lines_html = []
    lines_plain = []

    intro = (
        "Halloooo! Hier je persoonlijke surf forecast voor Scheveningen Zuid en "
        "Vlugtenburg, afgestemd op ervaren beginner/intermediate niveau. Automatisch "
        "gegenereerd op basis van Open-Meteo data, dus check voor vertrek altijd nog "
        "even een app of webcam. Ik neem je mee door de komende dagen 👇"
    )
    lines_plain.append(intro)
    lines_html.append(f"<p>{intro}</p>")

    spot_data = {name: fetch_spot_data(spot) for name, spot in SPOTS.items()}

    any_good_day = False
    for i in range(DAYS_AHEAD):
        d = today + timedelta(days=i)
        day_str = d.isoformat()
        next_day_str = (d + timedelta(days=1)).isoformat()

        spot_results = {}
        tide_events_by_spot = {}
        for name, spot in SPOTS.items():
            hours, daily = spot_data[name]
            sunrise = daily.get(day_str, {}).get("sunrise", "06:00")
            sunset = daily.get(day_str, {}).get("sunset", "21:30")
            spot_results[name] = evaluate_spot_day(spot, hours, day_str, sunrise, sunset)
            tide_events_by_spot[name] = find_tide_events(hours, day_str, next_day_str)

        if any(r is not None for r in spot_results.values()):
            any_good_day = True

        main_tide = tide_events_by_spot.get("Scheveningen Zuid") or next(iter(tide_events_by_spot.values()), [])
        text = narrate_day(d, spot_results, main_tide)
        lines_plain.append("\n" + text)
        lines_html.append("<p>" + text.replace("\n", "<br>").replace("*", "") + "</p>")

    if not any_good_day:
        closing = "Kortom: komende dagen weinig kans op leuke golven voor jouw niveau. Volgende update maandag of vrijdag!"
    else:
        closing = "Have fun en check voor je vertrekt altijd nog even de actuele stand van zaken. Volgende update maandag of vrijdag!"
    lines_plain.append("\n" + closing)
    lines_html.append(f"<p>{closing}</p>")

    factoid = random.choice(FACTOIDS)
    lines_plain.append("\n" + factoid)
    lines_html.append(f"<p><em>{factoid}</em></p>")

    return "\n".join(lines_plain), "".join(lines_html)


def send_email(subject, plain_text, html_text):
    smtp_host = os.environ["SMTP_HOST"]
    smtp_port = int(os.environ.get("SMTP_PORT", "465"))
    smtp_user = os.environ["SMTP_USERNAME"]
    smtp_pass = os.environ["SMTP_PASSWORD"]
    from_email = os.environ.get("FROM_EMAIL", smtp_user)
    to_email = os.environ.get("TO_EMAIL", "Jacquelinevanduijvenbode@hotmail.com")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(plain_text, "plain", "utf-8"))
    msg.attach(MIMEText(html_text, "html", "utf-8"))

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context) as server:
        server.login(smtp_user, smtp_pass)
        server.sendmail(from_email, [to_email], msg.as_string())


def main():
    # Gebruik de datum in Amsterdamse tijd, ook als de runner in UTC draait.
    today = datetime.now(AMSTERDAM).date()

    plain_text, html_text = build_email_body(today)
    subject = f"\U0001F30A Surf forecast Scheveningen Zuid & Vlugtenburg — {today.strftime('%d-%m')}"

    if "--dry-run" in sys.argv:
        print(subject)
        print()
        print(plain_text)
        return

    send_email(subject, plain_text, html_text)
    print("Forecast verstuurd.")


if __name__ == "__main__":
    main()
