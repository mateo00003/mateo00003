#!/usr/bin/env python3
"""Ekadashi calendar generator — computes observed Ekadashi dates for ANY location
and year from first principles (sun/moon positions + local sunrise), then emits a
subscribable iCalendar (.ics) feed.

Why computed, not hardcoded (RSI principle 1 — generators over artifacts):
Ekadashi is the 11th lunar day (tithi) of each fortnight. The *observed* fast day
is the civil date on which the Ekadashi tithi prevails at LOCAL SUNRISE — so it is
location-dependent and cannot be a fixed recurring rule. We compute it.

Self-measurement (RSI principle 2): `validate` runs the same engine at a reference
location (New Delhi) and checks it reproduces a verified ISKCON/Vaishnava date set.

Dependencies: pyephem (self-contained, no network / no ephemeris download).
    pip install ephem
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

import ephem

HERE = Path(__file__).resolve().parent

# Amanta lunar months in order; index = rashi (0=Mesha..11=Meena) the Sun ENTERS
# during that lunar month (the sankranti the month contains).
MONTHS = ["Chaitra", "Vaishakha", "Jyeshtha", "Ashadha", "Shravana", "Bhadrapada",
          "Ashwina", "Kartika", "Margashirsha", "Pausha", "Magha", "Phalguna"]

# Ekadashi names by (month label, paksha). Shukla uses the Amanta month; Krishna
# uses the following month (the Purnimanta convention the traditional names follow).
NAMES = {
    ("Chaitra", "Shukla"): "Kamada", ("Chaitra", "Krishna"): "Papamochani",
    ("Vaishakha", "Shukla"): "Mohini", ("Vaishakha", "Krishna"): "Varuthini",
    ("Jyeshtha", "Shukla"): "Nirjala", ("Jyeshtha", "Krishna"): "Apara",
    ("Ashadha", "Shukla"): "Devshayani", ("Ashadha", "Krishna"): "Yogini",
    ("Shravana", "Shukla"): "Putrada", ("Shravana", "Krishna"): "Kamika",
    ("Bhadrapada", "Shukla"): "Parivartini", ("Bhadrapada", "Krishna"): "Aja",
    ("Ashwina", "Shukla"): "Papankusha", ("Ashwina", "Krishna"): "Indira",
    ("Kartika", "Shukla"): "Devutthana", ("Kartika", "Krishna"): "Rama",
    ("Margashirsha", "Shukla"): "Mokshada", ("Margashirsha", "Krishna"): "Utpanna",
    ("Pausha", "Shukla"): "Pausha Putrada", ("Pausha", "Krishna"): "Saphala",
    ("Magha", "Shukla"): "Jaya", ("Magha", "Krishna"): "Shattila",
    ("Phalguna", "Shukla"): "Amalaki", ("Phalguna", "Krishna"): "Vijaya",
    ("Adhika", "Shukla"): "Padmini", ("Adhika", "Krishna"): "Parama",
}


def ayanamsa(when: dt.date) -> float:
    """Lahiri ayanamsa in degrees: ~23.85 deg at 2000.0, +50.29 arcsec/yr."""
    years = (when - dt.date(2000, 1, 1)).days / 365.25
    return 23.85 + (50.29 / 3600.0) * years


def _ecliptic_lon(body_cls, when_utc) -> float:
    b = body_cls()
    b.compute(when_utc)
    return math.degrees(ephem.Ecliptic(b).lon) % 360.0


def tithi_index(when_utc) -> int:
    """1..30. 11 = Shukla Ekadashi, 26 = Krishna Ekadashi."""
    elong = (_ecliptic_lon(ephem.Moon, when_utc) - _ecliptic_lon(ephem.Sun, when_utc)) % 360.0
    return (math.floor(elong / 12.0) % 30) + 1


def _sidereal_rashi(when_ephem) -> int:
    on = when_ephem.datetime().date()
    trop = _ecliptic_lon(ephem.Sun, when_ephem)
    return int(((trop - ayanamsa(on)) % 360.0) // 30)


def sunrise_utc(lat: float, lon: float, local_date: dt.date, tz: ZoneInfo) -> dt.datetime:
    """UTC datetime of sunrise for the given local civil date at (lat, lon)."""
    obs = ephem.Observer()
    obs.lat, obs.lon = str(lat), str(lon)
    obs.elevation = 0
    obs.pressure = 0
    obs.horizon = "-0:34"  # standard refraction for sunrise
    local_midnight = dt.datetime.combine(local_date, dt.time(0, 0), tzinfo=tz)
    obs.date = ephem.Date(local_midnight.astimezone(dt.timezone.utc).replace(tzinfo=None))
    return obs.next_rising(ephem.Sun()).datetime().replace(tzinfo=dt.timezone.utc)


def _month_label(d: dt.date, paksha: str) -> str:
    """Amanta lunar month (or 'Adhika') for the fortnight containing date d.

    Anchored to the new-moon boundaries of the lunar month and the sankranti
    (rashi entered) inside them. A month with no sankranti is Adhika (leap).
    For Krishna paksha the traditional Ekadashi name uses the *next* month.
    """
    noon = ephem.Date(dt.datetime.combine(d, dt.time(12, 0)))
    m_start = ephem.previous_new_moon(noon)
    m_end = ephem.next_new_moon(noon)
    r_start = _sidereal_rashi(m_start)
    r_end = _sidereal_rashi(ephem.Date(m_end - ephem.minute))
    if r_start == r_end:              # no sankranti in the month -> leap month
        return "Adhika"
    if paksha == "Shukla":
        return MONTHS[r_end]
    return MONTHS[(r_end + 1) % 12]   # Purnimanta shift for Krishna-paksha names


def compute_year(year: int, lat: float, lon: float, tzname: str,
                 rule: str = "vaishnava") -> list[dict]:
    """Return observed Ekadashis for the calendar year at the given location.

    rule: "vaishnava" (ISKCON — on vriddhi, the later day) or "smarta" (the earlier
    day). They differ only when the Ekadashi tithi spans two sunrises.
    """
    tz = ZoneInfo(tzname)
    start = dt.date(year, 1, 1) - dt.timedelta(days=2)
    end = dt.date(year, 12, 31) + dt.timedelta(days=2)
    day = start
    rows: list[tuple[dt.date, int]] = []
    while day <= end:
        rows.append((day, tithi_index(sunrise_utc(lat, lon, day, tz))))
        day += dt.timedelta(days=1)
    tmap = {d: t for d, t in rows}
    dates = [d for d, _ in rows]

    observed: list[dt.date] = []
    for target in (11, 26):  # Shukla, Krishna Ekadashi
        i, n = 0, len(dates)
        while i < n:
            t = tmap[dates[i]]
            if t == target:
                j = i
                while j + 1 < n and tmap[dates[j + 1]] == target:
                    j += 1
                # vriddhi: Vaishnava takes the later sunrise, smarta the earlier.
                observed.append(dates[j] if rule == "vaishnava" else dates[i])
                i = j + 1
            else:
                # kshaya (skipped): Dashami sunrise directly followed by Dwadashi.
                if t == target - 1 and i + 1 < n and tmap[dates[i + 1]] == (target + 1):
                    observed.append(dates[i + 1])
                i += 1

    out = []
    for d in sorted(set(observed)):
        if d.year != year:
            continue
        paksha = "Shukla" if tmap[d] in (11, 12) else "Krishna"
        label = _month_label(d, paksha)
        name = NAMES.get((label, paksha), f"{label} {paksha} Ekadashi")
        out.append({"date": d, "paksha": paksha, "month": label, "name": name})
    return out


def validate(year: int = 2026) -> int:
    gt = json.loads((HERE / "ground_truth.json").read_text())
    loc, truth = gt["location"], gt["dates"]
    rows = compute_year(year, loc["lat"], loc["lon"], loc["tz"], rule="vaishnava")
    got = {r["date"].isoformat(): r["name"] for r in rows}

    tset, gset = set(truth), set(got)
    date_ok = tset == gset
    name_hits = sum(1 for d in tset & gset if got[d] == truth[d])
    print(f"Validation @ {loc['name']} ({gt.get('tradition','?')}) for {year}")
    print(f"  dates: {len(gset & tset)}/{len(tset)} match ({'OK' if date_ok else 'MISMATCH'})")
    print(f"  names: {name_hits}/{len(tset)} match ({'OK' if name_hits == len(tset) else 'MISMATCH'})")
    for d in sorted(tset | gset):
        tn, gn = truth.get(d, "—"), got.get(d, "—")
        if not (d in tset and d in gset and tn == gn):
            print(f"    {d}  truth={tn:<14} got={gn:<14}  <<<")
    return 0 if (date_ok and name_hits == len(tset)) else 1


SIGNIFICANCE = {
    "Kamada": "Fulfills sincere desires and frees one from sins and curses.",
    "Papamochani": "Destroys accumulated sins and undoes the effects of past wrongdoing.",
    "Mohini": "Frees one from the bondage of illusion (maya) and from grief.",
    "Varuthini": "Grants protection, good fortune, and merit in this life and the next.",
    "Nirjala": "The most austere Ekadashi, kept even without water; its merit is said to stand for all the year's Ekadashis.",
    "Apara": "Bestows vast merit and dissolves deep-rooted sins.",
    "Devshayani": "Marks the start of Chaturmas as Lord Vishnu enters his yogic sleep.",
    "Yogini": "Absolves sins; said to equal the merit of feeding thousands of brahmanas.",
    "Putrada": "Observed for the well-being and blessings of one's children.",
    "Pausha Putrada": "Observed for the well-being and blessings of one's children.",
    "Kamika": "Washes away sins; worship of Vishnu on this day is especially auspicious.",
    "Parivartini": "Lord Vishnu turns to his other side in cosmic sleep; also called Vamana Ekadashi.",
    "Aja": "Frees one from the gravest sins and restores lost fortune.",
    "Papankusha": "Grants the merit of severe penances through simple devotion, and liberation.",
    "Indira": "Observed to liberate one's ancestors from lower realms.",
    "Devutthana": "Lord Vishnu awakens from cosmic sleep, ending Chaturmas; begins the wedding season.",
    "Rama": "Destroys sins and grants prosperity; falls just before Diwali.",
    "Mokshada": "Bestows liberation (moksha) and merit for ancestors; coincides with Gita Jayanti. Also called Vaikuntha Ekadashi.",
    "Utpanna": "Commemorates the appearance of goddess Ekadashi, born to defeat the demon Mura.",
    "Saphala": "Brings success and fruition to one's endeavors.",
    "Jaya": "Grants victory and freedom from lower births.",
    "Shattila": "Charity of sesame (til) on this day brings great merit and purification.",
    "Vijaya": "Bestows victory over obstacles and adversaries.",
    "Amalaki": "Worship of the amalaki (myrobalan) tree brings abundant merit and health.",
    "Padmini": "Rare leap-month (Adhika / Purushottama) Ekadashi, exceptionally meritorious.",
    "Parama": "Rare leap-month (Adhika) Ekadashi granting supreme spiritual benefit.",
}
INFO_URL = "https://en.wikipedia.org/wiki/Ekadashi"


def _esc(text: str) -> str:
    return (text.replace("\\", "\\\\").replace("\n", "\\n")
            .replace(",", "\\,").replace(";", "\\;"))


def _fold(line: str) -> str:
    out, ln = [], line
    while len(ln.encode("utf-8")) > 73:
        cut = 73
        while len(ln[:cut].encode("utf-8")) > 73:
            cut -= 1
        out.append(ln[:cut])
        ln = " " + ln[cut:]
    out.append(ln)
    return "\r\n".join(out)


def _event(r: dict) -> list[str]:
    d = r["date"]
    ymd = d.strftime("%Y%m%d")
    name, month, paksha = r["name"], r["month"], r["paksha"]
    sig = SIGNIFICANCE.get(name, "")
    plain = (f"{name} Ekadashi — {month} {paksha} Paksha.\n\n{sig}\n\n"
             "A day of fasting from grains and beans, devoted to Lord Vishnu. "
             "Break the fast (parana) the following morning within the prescribed window.\n\n"
             f"More: {INFO_URL}")
    html = (f"<h3>{name} Ekadashi</h3>"
            f"<p><i>{month} {paksha} Paksha</i></p>"
            f"<p>{sig}</p>"
            "<p>A day of fasting from grains and beans, devoted to Lord Vishnu. "
            "Break the fast (<b>parana</b>) the following morning within the prescribed window.</p>"
            f'<p><a href="{INFO_URL}">More about Ekadashi &raquo;</a></p>')
    dtstamp = f"{d.year}0101T000000Z"
    return [
        "BEGIN:VEVENT",
        f"UID:ekadashi-{ymd}@mateo00003.github.io",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART;VALUE=DATE:{ymd}",
        f"DTEND;VALUE=DATE:{(d + dt.timedelta(days=1)).strftime('%Y%m%d')}",
        _fold(f"SUMMARY:{_esc(name + ' Ekadashi')}"),
        _fold(f"DESCRIPTION:{_esc(plain)}"),
        _fold(f"X-ALT-DESC;FMTTYPE=text/html:{_esc(html)}"),
        f"URL:{INFO_URL}",
        "CATEGORIES:Ekadashi,Fasting",
        "TRANSP:TRANSPARENT",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        _fold(f"DESCRIPTION:{_esc(name + ' Ekadashi is tomorrow — prepare to fast')}"),
        "TRIGGER:-PT6H",  # 18:00 the evening before
        "END:VALARM",
        "END:VEVENT",
    ]


def build_ics(rows: list[dict], calname: str, caldesc: str) -> str:
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0",
        "PRODID:-//mateo00003//Ekadashi Calendar//EN",
        "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
        _fold(f"X-WR-CALNAME:{_esc(calname)}"),
        _fold(f"X-WR-CALDESC:{_esc(caldesc)}"),
        "REFRESH-INTERVAL;VALUE=DURATION:P30D",
        "X-PUBLISHED-TTL:P30D",
    ]
    for r in rows:
        lines += _event(r)
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines) + "\r\n"


def generate(years: list[int], lat: float, lon: float, tzname: str,
             place: str, out: Path) -> int:
    # Self-measurement: never emit a feed from an engine that fails validation.
    if validate(2026) != 0:
        print("ABORT: engine failed validation; not writing calendar.")
        return 1
    rows = []
    for y in years:
        rows += compute_year(y, lat, lon, tzname, rule="vaishnava")
    calname = "Ekadashi (ISKCON / Vaishnava)"
    caldesc = (f"Ekadashi fasting days computed for {place} using the ISKCON / "
               f"Gaudiya Vaishnava rule. {years[0]}-{years[-1]}. Generated from "
               "astronomical computation (github.com/mateo00003).")
    out.write_text(build_ics(rows, calname, caldesc), encoding="utf-8")
    print(f"Wrote {out} — {len(rows)} events, {years[0]}-{years[-1]}, for {place}.")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="check engine against ground truth")
    v.add_argument("--year", type=int, default=2026)
    p = sub.add_parser("print", help="print computed Ekadashis")
    p.add_argument("--year", type=int, default=2026)
    p.add_argument("--lat", type=float, default=47.6062)
    p.add_argument("--lon", type=float, default=-122.3321)
    p.add_argument("--tz", default="America/Los_Angeles")
    g = sub.add_parser("generate", help="write the .ics feed (validates first)")
    g.add_argument("--from", dest="y0", type=int, default=2026)
    g.add_argument("--to", dest="y1", type=int, default=2027)
    g.add_argument("--lat", type=float, default=47.6062)
    g.add_argument("--lon", type=float, default=-122.3321)
    g.add_argument("--tz", default="America/Los_Angeles")
    g.add_argument("--place", default="Seattle, WA")
    g.add_argument("--out", default=str(HERE / "ekadashi.ics"))
    args = ap.parse_args(argv)

    if args.cmd == "validate":
        return validate(args.year)
    if args.cmd == "print":
        for r in compute_year(args.year, args.lat, args.lon, args.tz):
            print(f"{r['date']}  {r['name']:<16} ({r['month']} {r['paksha']})")
        return 0
    if args.cmd == "generate":
        return generate(list(range(args.y0, args.y1 + 1)), args.lat, args.lon,
                        args.tz, args.place, Path(args.out))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
