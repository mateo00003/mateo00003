# Ekadashi Calendar

A subscribable calendar of **Ekadashi** fasting days — computed from first
principles (sun/moon positions + local sunrise), following the **ISKCON /
Gauḍīya Vaiṣṇava** rule. Defaults to **Seattle, WA**; regenerates for any
location or year.

Because Ekadashi is the 11th lunar day (*tithi*) observed on the civil date it
prevails **at local sunrise**, the dates are location-dependent — they can't be a
fixed recurring rule, and they legitimately differ by ~1 day from the
commonly-published India dates. So we *compute* them.

## Subscribe (one feed → every account & provider)

The feed lives at a stable URL. Subscribe once in each account and it updates
itself when the feed is regenerated.

```
https://raw.githubusercontent.com/mateo00003/mateo00003/main/calendars/ekadashi/ekadashi.ics
```

- **Google Calendar** (any account — personal Gmail, Workspace): Settings →
  *Add calendar* → **From URL** → paste the URL.
- **Apple / iCloud**: File → **New Calendar Subscription** → paste the URL.
- **Outlook / Microsoft 365**: *Add calendar* → **Subscribe from web** → paste
  the URL.

Prefer a one-time copy with no auto-refresh? Download `ekadashi.ics` and use
your app's **Import** instead of Subscribe.

## Regenerate / re-target

```bash
pip install ephem                      # self-contained; no network needed

python3 ekadashi.py validate           # self-check against the reference dates
python3 ekadashi.py print --year 2026  # inspect computed dates for a location
python3 ekadashi.py generate \         # write ekadashi.ics
    --from 2026 --to 2028 \
    --lat 47.6062 --lon -122.3321 --tz America/Los_Angeles --place "Seattle, WA"
```

`generate` **validates before it writes** — it refuses to emit a feed from an
engine that fails the reference check.

## Upkeep runs itself

`.github/workflows/calendar-upkeep.yml` runs monthly (and on any change to the
engine or the prompt stack). It re-checks the assembled system prompt against
its layers, revalidates the engine against `ground_truth.json`, and rolls the
feed's horizon forward to **the current year through +2** — so the calendar
never quietly runs out of dates.

Output is deterministic (each event's `DTSTAMP` derives from its own year, not
from "now"), so an unchanged engine regenerates a byte-identical file and the
workflow stays silent. When something *does* drift, it opens a PR with the
correction rather than waiting to be asked.

## Accuracy & trust

- **Validated 24/24 dates and 24/24 names** against an ISKCON / Vaiṣṇava
  reference for New Delhi 2026 (`ground_truth.json`), including the two
  *vṛddhi* days where Vaiṣṇava and smārta practice diverge (Yogini, Devutthana).
- **Location shift spot-confirmed** for the Pacific timezone (e.g. Shattila
  falls Jan 13 in Seattle vs Jan 14 in Delhi, by direct tithi-window analysis).
- Rare *mahādvādaśī* / leap-month edge cases are handled but not exhaustively
  validated for every future year — for a critical fast, cross-check against
  your local temple. Corrections belong in `ground_truth.json`, which the
  validator enforces.

Each event carries the Ekadashi's significance, a plain-text and an HTML
(`X-ALT-DESC`) description, an info link, and an evening-before reminder.

## How this fits the RSI stack

This calendar is the first live proof of `docs/system-prompt-stack/`:

- **Generator over artifact** — dates are computed, not hardcoded; one command
  re-targets any location/year.
- **Self-measurement shipped in** — `validate` gates `generate`; the feed can't
  ship unvalidated.
- **Capability captured** — the engine works for any sunrise-based lunar
  observance, not just this one.
- **Loop closed in the system, not the human** — the upkeep workflow revalidates
  and re-horizons on a schedule, and surfaces drift as a PR. Nobody has to
  remember to look.
