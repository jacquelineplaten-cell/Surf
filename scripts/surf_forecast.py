#!/usr/bin/env python3
"""
Persoonlijke surf forecast voor Scheveningen Noord (Hart Beach) en Vlugtenburg strand.

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
    "Scheveningen Noord (Hart Beach)": {
        "lat": 52.1036,
        "lon": 4.2656,
        "ideal_wind_dir": 135,      # zuidoost = aflandig
        "ideal_swell_dir": (225, 360),  # zuidwest tot noord, optimaal noordwest
        "wave_min": 0.3,
        "wave_max": 1.1,
        "note_too_big": "wordt al snel een closeout op deze mellow bank bij de havenhoofden",
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
    """Beoordeelt een uur met een 1-5 sterren score voor een intermediate surfer.

    1 ster: golfhoogte zit sowieso buiten de behapbare range voor dit niveau
    (te vlak, of te grof/hol) — de rest doet er dan niet meer toe.
    2-5 sterren: golfhoogte is behapbaar; de rest (wind, periode, swell-
    richting) bepaalt een kwaliteitsscore 0-1 die naar sterren wordt vertaald.
    Wind weegt het zwaarst (bepaalt of het water schoon of rommelig is),
    dan golfhoogte t.o.v. de 'sweet spot', dan periode, dan swellrichting.
    """
    wave = h.get("wave_height")
    wind_speed = h.get("wind_speed")
    wind_dir = h.get("wind_dir")
    gust = h.get("wind_gust")
    period = h.get("swell_period")
    swell_dir = h.get("swell_dir")

    if wave is None or wind_speed is None:
        return 1, ["geen data"]

    if wave < spot["wave_min"]:
        return 1, [f"te weinig power ({wave:.1f} m), waarschijnlijk vlak/soep"]
    if wave > spot["wave_max"]:
        return 1, [f"{wave:.1f} m, {spot['note_too_big']}"]

    reasons = []

    # Golfhoogte: piekt in de 'sweet spot' op 60% van de behapbare range
    # (net iets boven het midden — intermediate surfers mogen best wat power).
    span = spot["wave_max"] - spot["wave_min"]
    sweet = spot["wave_min"] + 0.6 * span
    wave_q = max(0.0, 1 - abs(wave - sweet) / span)

    wind_diff = circular_diff(wind_dir, spot["ideal_wind_dir"])
    if wind_diff <= 55:
        if wind_speed <= 25:
            wind_q = 1.0
            reasons.append("aflandige wind, dus schone golven")
        elif wind_speed <= 35:
            wind_q = 0.6
            reasons.append(f"aflandig maar stevig ({wind_speed:.0f} km/u), lastiger peddelen")
        else:
            wind_q = 0.3
            reasons.append(f"aflandig maar erg hard ({wind_speed:.0f} km/u)")
    elif wind_diff >= 130:
        if wind_speed <= 10:
            wind_q = 0.5
            reasons.append("aanlandig maar zwak, water blijft redelijk rustig")
        elif wind_speed <= 18:
            wind_q = 0.25
            reasons.append("aanlandige wind, verwacht wat chop")
        else:
            wind_q = 0.0
            reasons.append(f"stevige aanlandige wind ({wind_speed:.0f} km/u), wordt een klotsbak")
    else:
        if wind_speed <= 15:
            wind_q = 0.8
            reasons.append("zijwind, over het algemeen prima surfbaar")
        elif wind_speed <= 25:
            wind_q = 0.5
            reasons.append("stevige zijwind, kan wat rommelig water geven")
        else:
            wind_q = 0.2
            reasons.append(f"harde zijwind ({wind_speed:.0f} km/u), verwacht rommel")

    if period is None:
        period_q = 0.5
    elif period < 5:
        period_q = 0.2
        reasons.append(f"korte periode ({period:.0f} s), dus minder power")
    elif period < 7:
        period_q = 0.6
    elif period < 9:
        period_q = 0.9
        reasons.append(f"nette periode ({period:.0f} s), lekker wat power")
    else:
        period_q = 1.0
        reasons.append(f"lange periode ({period:.0f} s), veel energie in de golven")

    if swell_dir is not None and not in_range(swell_dir, *spot["ideal_swell_dir"]):
        dir_q = 0.4
        reasons.append("swellrichting niet helemaal ideaal voor deze plek")
    else:
        dir_q = 1.0

    if gust is not None and gust >= 45:
        wind_q = min(wind_q, 0.3)
        reasons.append(f"pittige windstoten tot {gust:.0f} km/u")

    quality = 0.4 * wind_q + 0.3 * wave_q + 0.2 * period_q + 0.1 * dir_q

    if quality >= 0.75:
        stars = 5
    elif quality >= 0.5:
        stars = 4
    elif quality >= 0.25:
        stars = 3
    else:
        stars = 2

    # Bij echt slechte (harde aanlandige) wind kan het nooit meer dan 2
    # sterren worden, ongeacht hoe goed golfhoogte/periode verder zijn.
    if wind_q == 0.0:
        stars = min(stars, 2)

    return stars, reasons


def evaluate_spot_day(spot, hours, day_str, sunrise, sunset):
    day_hours = sorted(t for t in hours if t.startswith(day_str))
    daylight = [t for t in day_hours if sunrise <= t[-5:] <= sunset]
    if not daylight:
        daylight = day_hours
    if not daylight:
        return {"stars": 1, "window": None, "window_mid_time": None, "wave_height": None,
                "swell_period": None, "wind_speed": None, "wind_dir": None, "reasons": ["geen data"]}

    evals = [(t, *evaluate_hour(spot, hours[t])) for t in daylight]
    best_stars = max(stars for _, stars, _ in evals)

    # Langste aaneengesloten reeks uren op het beste niveau, zodat we geen
    # los uur aan het begin en een los uur aan het eind samenvoegen tot een
    # nepvenster dat de uren ertussen ook meepakt.
    runs, current = [], []
    for e in evals:
        if e[1] == best_stars:
            current.append(e)
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)
    best_run = max(runs, key=len)

    start, end = best_run[0][0][-5:], best_run[-1][0][-5:]

    # Neem het middelste uur van dit venster als representatief moment, en
    # gebruik ALLEEN de redenen van dat ene uur (niet gemixt met andere uren
    # in het venster, want die kunnen qua wind/richting net anders zijn).
    mid_t = best_run[len(best_run) // 2][0]
    mid = hours[mid_t]
    _, reasons = evaluate_hour(spot, mid)

    return {
        "stars": best_stars,
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


def star_string(n):
    n = max(1, min(5, n))
    return "★" * n + "☆" * (5 - n)


def cap(s):
    return s[0].upper() + s[1:] if s else s


MOOD = {
    5: ["Dit wordt genieten", "Dit ziet er echt leuk uit", "Hier zou ik voor gaan"],
    4: ["Dit kan een hele leuke sessie worden", "Mooie kans", "Dit is het proberen meer dan waard"],
    3: ["Dit kan een oké sessie worden", "Prima optie, geen topdag maar wel de moeite waard"],
    2: ["Twijfelachtig", "Kan, maar reken er niet op"],
    1: ["Niet doen", "Ik zou het water laten"],
}


def narrate_day(d, spot_results, tide_events):
    day_name = DUTCH_DAYS[d.weekday()]
    day_best_stars = max(r["stars"] for r in spot_results.values())
    heading = f"*{day_name.capitalize()} {d.day}/{d.month} — {star_string(day_best_stars)}*"

    lines = [heading]

    # Getij is alleen de moeite waard om te melden als de dag het ook echt
    # waard is (meer dan 3 sterren) — anders is het ruis.
    if day_best_stars > 3 and tide_events:
        tide_overview = ", ".join(f"{kind}water rond {t}" for t, kind in tide_events)
        lines.append(f"Getij: {tide_overview}.")

    ranked = sorted(spot_results.items(), key=lambda kv: kv[1]["stars"], reverse=True)

    for spot_name, r in ranked:
        stars = r["stars"]
        star_txt = star_string(stars)

        if stars <= 2 or r["window"] is None:
            reason = r["reasons"][0] if r["reasons"] else "geen bruikbare data"
            lines.append(f"{star_txt} {spot_name}: {random.choice(MOOD[stars])} — {reason}.")
            continue

        opener = random.choice(MOOD[stars])
        start, end = r["window"]
        window_txt = start if start == end else f"{start}–{end}"
        wave = r["wave_height"]
        period = r["swell_period"]
        wind_dir_word = compass(r["wind_dir"])
        wind_speed = r["wind_speed"]

        tide_text = ""
        if stars > 3:
            tide_phrase = describe_tide_for_window(r["window_mid_time"][-5:], tide_events)
            if tide_phrase:
                tide_text = f" {cap(tide_phrase)}."

        reason_text = "; ".join(r["reasons"])
        wave_txt = f"{wave:.1f} m" if wave is not None else "onbekende hoogte"
        period_txt = f", {period:.0f} s periode" if period else ""

        lines.append(
            f"{star_txt} {spot_name}: {opener} — rond {window_txt} zie ik ongeveer {wave_txt}{period_txt}, "
            f"met wind uit het {wind_dir_word} ({wind_speed:.0f} km/u). {cap(reason_text)}."
            f"{tide_text}"
        )

    return "\n".join(lines), day_best_stars


def build_email_body(today):
    lines_html = []
    lines_plain = []

    spot_data = {name: fetch_spot_data(spot) for name, spot in SPOTS.items()}

    days_info = []
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

        main_tide = tide_events_by_spot.get("Scheveningen Noord (Hart Beach)") or next(iter(tide_events_by_spot.values()), [])
        day_best_stars = max(r["stars"] for r in spot_results.values())
        days_info.append({"date": d, "spot_results": spot_results, "tide_events": main_tide, "stars": day_best_stars})

    # --- Intro: meteen zeggen of en welke dagen 3-5 sterren hebben ---
    good_days = [info for info in days_info if info["stars"] >= 3]
    if good_days:
        summary_bits = ", ".join(
            f"{DUTCH_DAYS[info['date'].weekday()].capitalize()} {star_string(info['stars'])}"
            for info in good_days
        )
        intro = (
            f"Halloooo! Deze ronde is het de moeite waard om te gaan op: {summary_bits}. "
            "Hieronder per dag het hele verhaal met onderbouwing. Automatisch gegenereerd op "
            "basis van Open-Meteo data, dus check voor vertrek altijd nog even een app of webcam."
        )
    else:
        intro = (
            "Halloooo! Deze ronde zitten er geen dagen bij met 3 sterren of meer voor "
            "Scheveningen Noord of Vlugtenburg — ik zou het water laten voor jouw niveau. "
            "Hieronder toch even per dag waarom."
        )
    lines_plain.append(intro)
    lines_html.append(f"<p>{intro}</p>")

    for info in days_info:
        text, _ = narrate_day(info["date"], info["spot_results"], info["tide_events"])
        lines_plain.append("\n" + text)
        html_block = text.replace("\n", "<br>").replace("*", "")
        html_block = html_block.replace("★", '<span style="color:#d4a017">★</span>')
        html_block = html_block.replace("☆", '<span style="color:#ccc">☆</span>')
        lines_html.append("<p>" + html_block + "</p>")

    if good_days:
        closing = "Have fun en check voor je vertrekt altijd nog even de actuele stand van zaken. Volgende update maandag of vrijdag!"
    else:
        closing = "Volgende update maandag of vrijdag — hopelijk dan meer goed nieuws!"
    lines_plain.append("\n" + closing)
    lines_html.append(f"<p>{closing}</p>")

    factoid = random.choice(FACTOIDS)
    lines_plain.append("\n" + factoid)
    lines_html.append(f"<p><em>{factoid}</em></p>")

    if good_days:
        best = max(good_days, key=lambda i: i["stars"])
        subject_bit = f"beste dag {DUTCH_DAYS[best['date'].weekday()].capitalize()} {star_string(best['stars'])}"
    else:
        subject_bit = "geen 3+ sterren dagen"

    return "\n".join(lines_plain), "".join(lines_html), subject_bit


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

    plain_text, html_text, subject_bit = build_email_body(today)
    subject = f"\U0001F30A Surf forecast {today.strftime('%d-%m')} — {subject_bit}"

    if "--dry-run" in sys.argv:
        print(subject)
        print()
        print(plain_text)
        return

    send_email(subject, plain_text, html_text)
    print("Forecast verstuurd.")


if __name__ == "__main__":
    main()
