# Price Alert Tool

Reads daily closes from a prices CSV and alert rules from JSON; prints one
line per fired alert. Standard library only; nothing to install (Python
3.9.6, macOS).

## Run it

    python3 alerts.py prices.csv rules.json

stdout carries only alert lines; diagnostics go to stderr. Exit codes: 0 =
ran, 1 = invalid input, 2 = usage error.

## Inputs

Prices CSV — one row per instrument per trading day, any row order:

    date,instrument,close
    2026-09-01,INFY,1520.50

Header names case-insensitive, order free. Dates `YYYY-MM-DD`; closes
finite plain or scientific numbers (`1.52e3`); `NaN`, `inf`, `1_000` and
duplicate `(instrument, date)` rows rejected.

Rules JSON — top-level array, the brief's exact schema:

    [{"id": "r1", "instrument": "INFY", "type": "level", "direction": "below", "level": 1500},
     {"id": "r2", "instrument": "TCS", "type": "ma_cross", "short": 5, "long": 20, "direction": "above"}]

## Alert behavior

- **level** — fires each day the close crosses the level (below: prev >
  level, now <= level; above mirrored); equality counts on the crossing
  day, never anchors; returning re-arms.
- **ma_cross** — fires when the short simple moving average crosses the
  long one on consecutive observations with both windows full
  (observation counts, not calendar days).
- **Output** — one line per alert, two-space gaps, columns padded to the
  widest value; ordered by date then rule id; nothing when no rule fires:

      2026-09-03  r1  INFY  level below 1500

- Rules for unknown instruments warn on stderr and are skipped (exit
  0); names match exactly.

## Tests

    python3 -m unittest -v      # 76 tests

Written before the implementation as its specification.

## How AI was used

Built with Qoder, an AI coding assistant, in staged prompts, each approved
before the next: plan; tests first; implementation; validation; CLI
verification; adversarial review. All code and tests were assistant-drafted
under the candidate's direction, who corrected the mistakes below and
verified each stage. Mistakes, all corrected:

- **Wrong schema first:** the plan used `up`/`down`,
  `short_window`/`long_window`; the brief's schema is `below`/`above`,
  `short`/`long` — regression tests
  `test_unsupported_direction_values_are_rejected`,
  `test_alternative_schema_names_are_rejected`.
- **Level rules read as "fire once ever":** corrected — every genuine
  crossing fires; regression tests
  `test_crossing_again_after_returning_fires_a_new_alert`,
  `test_repeated_crossovers_each_fire`.
- **Overflow on a huge JSON integer level:** `10**1000` raised a raw
  `OverflowError` instead of a clean input error; now rejected;
  regression test `test_oversized_integer_level_is_rejected_cleanly`.

## Validation decisions and limitations

- Invalid data is rejected, never reinterpreted — digit grouping, `NaN`,
  malformed dates, duplicates — and `[]` is valid while a zero-byte rules
  or prices file is an error.
- Limitation: `ma_cross` sums on closes near 1e308 can overflow to
  infinity; ordinary prices are unaffected.
