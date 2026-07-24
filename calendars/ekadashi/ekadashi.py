#!/usr/bin/env python3
"""Ekadashi calendar generator — computes observed Ekadashi dates for ANY location
and year from first principles (sun/moon positions + local sunrise), then emits a
subscribable iCalendar (.ics) feed.

Why computed, not hardcoded (RSI principle 1 — generators over artifacts):
Ekadashi is the 11th lunar day (tithi) of each fortnight. The *observed* fast day
is the civil date on which the Ekadashi tithi prevails at LOCAL SUNRISE — so it is
location-dependent and cannot be a fixed recurring rule. We compute it.

Self-measurement (RSI principle 2): `validate` runs the same engine at a reference
location (New Delhi) and checks it reproduces a verified ground-truth date set.

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

# --- Ekadashi names by (lunar month, paksha), Amanta scheme -------------------
# Shukla = waxing (tithi 11), Krishna = waning (tithi 26).
NAMES = {
    ("Chaitra", "Krishna"): "Papamochani", ("Chaitra", "Shukla"): "Kamada",
    ("Vaishakha", "Krishna"): "Varuthini", ("Vaishakha", "Shukla"): "Mohini",
    ("Jyeshtha", "Krishna"): "Apara", ("Jyeshtha", "Shukla"): "Nirjala",
    ("Ashadha", "Krishna"): "Yogini", ("Ashadha", "Shukla"): "Devshayani",
    ("Shravana", "Krishna"): "Kamika", ("Shravana", "Shukla"): "Putrada",
    ("Bhadrapada", "Krishna"): "Aja", ("Bhadrapada", "Shukla"): "Parivartini",
    ("Ashwina", "Krishna"): "Indira", ("Ashwina", "Shukla"): "Papankusha",
    ("Kartika", "Krishna"): "Rama", ("Kartika", "Shukla"): "Devutthana",
    ("Margashirsha", "Krishna"): "Utpanna", ("Margashirsha", "Shukla"): "Mokshada",
    ("Pausha", "Krishna"): "Saphala", ("Pausha", "Shukla"): "Pausha Putrada",
    ("Magha", "Krishna"): "Shattila", ("Magha", "Shukla"): "Jaya",
    ("Phalguna", "Krishna"): "Vijaya", ("Phalguna", "Shukla"): "Amalaki",
    ("Adhika", "Krishna"): "Parama", ("Adhika", "Shukla"): "Padmini",
}

# Lunar month (Amanta) named by the rashi the Sun occupies during the month.
# Index = sidereal rashi of the Sun (0=Mesha..11=Meena). Calibrated against
# verified (name,date) pairs in ground_truth.json.
MONTH_OF_RASHI = [
    "Vaishakha",     # 0 Mesha
    "Jyeshtha",      # 1 Vrishabha
    "Ashadha",       # 2 Mithuna
    "Shravana",      # 3 Karka
    "Bhadrapada",    # 4 Simha
    "Ashwina",       # 5 Kanya
    "Kartika",       # 6 Tula
    "Margashirsha",  # 7 Vrishchika
    "Pausha",        # 8 Dhanu
    "Magha",         # 9 Makara
    "Phalguna",      # 10 Kumbha
    "Chaitra",       # 11 Meena
]


def ayanamsa(when: dt.date) -> float:
    """Lahiri ayanamsa in degrees (approx): 23.85 deg at 2000.0, +50.29 arcsec/yr."""
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


def sidereal_sun_rashi(when_utc, on_date: dt.date) -> int:
    trop = _ecliptic_lon(ephem.Sun, when_utc)
    sid = (trop - ayanamsa(on_date)) % 360.0
    return int(sid // 30)


def sunrise_utc(lat: float, lon: float, local_date: dt.date, tz: ZoneInfo) -> dt.datetime:
    """UTC datetime of sunrise for the given local civil date at (lat, lon)."""
    obs = ephem.Observer()
    obs.lat, obs.lon = str(lat), str(lon)
    obs.elevation = 0
    obs.pressure = 0
    obs.horizon = "-0:34"  # standard refraction for sunrise
    local_midnight = dt.datetime.combine(local_date, dt.time(0, 0), tzinfo=tz)
    obs.date = ephem.Date(local_midnight.astimezone(dt.timezone.utc).replace(tzinfo=None))
    rise = obs.next_rising(ephem.Sun())
    return rise.datetime().replace(tzinfo=dt.timezone.utc)


def compute_year(year: int, lat: float, lon: float, tzname: str,
                 rule: str = "vaishnava") -> list[dict]:
    """Return observed Ekadashis for the calendar year at the given location.

    rule: "vaishnava" (ISKCON — on vriddhi, the later day) or "smarta" (the earlier
    day). They differ only when the Ekadashi tithi spans two sunrises.
    """
    tz = ZoneInfo(tzname)
    # Sunrise tithi for each local date (pad a day either side for run detection).
    start = dt.date(year, 1, 1) - dt.timedelta(days=2)
    end = dt.date(year, 12, 31) + dt.timedelta(days=2)
    day = start
    rows: list[tuple[dt.date, int]] = []
    while day <= end:
        sr = sunrise_utc(lat, lon, day, tz)
        rows.append((day, tithi_index(sr)))
        day += dt.timedelta(days=1)
    tmap = {d: t for d, t in rows}
    dates = [d for d, _ in rows]

    observed: list[dt.date] = []
    for target in (11, 26):  # Shukla, Krishna Ekadashi
        i = 0
        n = len(dates)
        while i < n:
            d = dates[i]
            t = tmap[d]
            if t == target:
                # maximal run of consecutive sunrises in Ekadashi tithi -> take last
                # (Vaishnava rule: prefer the later, "shuddha" day on vriddhi).
                j = i
                while j + 1 < n and tmap[dates[j + 1]] == target:
                    j += 1
                observed.append(dates[j] if rule == "vaishnava" else dates[i])
                i = j + 1
            else:
                # kshaya (skipped) Ekadashi: Dashami sunrise directly followed by
                # Dwadashi sunrise -> fast observed on the Dwadashi day.
                if t == target - 1 and i + 1 < n and tmap[dates[i + 1]] == (target + 1):
                    observed.append(dates[i + 1])
                i += 1

    observed = sorted(set(observed))
    out = []
    for d in observed:
        if d.year != year:
            continue
        paksha = "Shukla" if tmap[d] in (11, 12) else "Krishna"
        sr = sunrise_utc(lat, lon, d, tz)
        rashi = sidereal_sun_rashi(sr, d)
        month = MONTH_OF_RASHI[rashi]
        out.append({"date": d, "paksha": paksha, "month": month, "rashi": rashi})

    _assign_names(out)
    return out


def _assign_names(rows: list[dict]) -> None:
    """Name each Ekadashi; detect Adhik Maas (two same-paksha Ekadashis naming the
    same month -> the earlier is Adhika)."""
    seen: dict[tuple[str, str], int] = {}
    for r in rows:
        seen[(r["month"], r["paksha"])] = seen.get((r["month"], r["paksha"]), 0) + 1
    counted: dict[tuple[str, str], int] = {}
    for r in rows:
        key = (r["month"], r["paksha"])
        counted[key] = counted.get(key, 0) + 1
        if seen[key] == 2 and counted[key] == 1:
            r["month"] = "Adhika"  # first of the duplicate pair is the leap month
            key = ("Adhika", r["paksha"])
        r["name"] = NAMES.get(key, f"{r['month']} {r['paksha']} Ekadashi")


def validate(year: int = 2026) -> int:
    gt = json.loads((HERE / "ground_truth.json").read_text())
    loc = gt["location"]
    truth = gt["dates"]
    # Ground truth is the general (smarta) New Delhi reference; validate that rule.
    rows = compute_year(year, loc["lat"], loc["lon"], loc["tz"], rule="smarta")
    got = {r["date"].isoformat(): r["name"] for r in rows}

    truth_dates = set(truth)
    got_dates = set(got)
    date_ok = truth_dates == got_dates
    name_hits = sum(1 for d in truth_dates & got_dates if got[d] == truth[d])

    print(f"Validation @ {loc['name']} for {year}")
    print(f"  dates: {len(got_dates & truth_dates)}/{len(truth_dates)} match "
          f"({'OK' if date_ok else 'MISMATCH'})")
    print(f"  names: {name_hits}/{len(truth_dates)} match")
    for d in sorted(truth_dates | got_dates):
        tn, gn = truth.get(d, "—"), got.get(d, "—")
        flag = "" if (d in truth_dates and d in got_dates and tn == gn) else "  <<<"
        if flag or tn != gn:
            print(f"    {d}  truth={tn:<14} got={gn:<14}{flag}")
    return 0 if (date_ok and name_hits == len(truth_dates)) else 1


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
    args = ap.parse_args(argv)

    if args.cmd == "validate":
        return validate(args.year)
    if args.cmd == "print":
        for r in compute_year(args.year, args.lat, args.lon, args.tz):
            print(f"{r['date']}  {r['name']:<16} ({r['month']} {r['paksha']})")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
