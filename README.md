# Price Alert Tool

A Python CLI tool that reads historical closing prices from CSV and alert rules from JSON. It uses the standard library only (Python 3.9.6, macOS); no installation required.

## Run

```bash
python3 alerts.py prices.csv rules.json
python3 -m unittest -v
```

The test suite contains 76 tests (verify with the command above). Exit codes: `0` success, `1` invalid input, `2` usage error. Alerts go to stdout; warnings and errors go to stderr.

## Inputs and behaviour

- **CSV:** `date,instrument,close`; rows may be unordered. Dates must be `YYYY-MM-DD`; closes must be finite numbers. Invalid dates, `NaN`, `inf`, `1_000`, and duplicate instrument/date rows are rejected.
- **JSON:** a top-level array using the brief's schema, e.g. `{"id":"r1","instrument":"INFY","type":"level","direction":"below","level":1500}` or `{"id":"r2","instrument":"TCS","type":"ma_cross","short":5,"long":20,"direction":"above"}`.
- **Level:** alerts on each genuine crossing in the specified direction; equality counts on the crossing day but does not anchor a new crossing.
- **MA crossover:** compares short and long simple moving averages on consecutive observations with complete windows.
- Alerts are ordered by date then rule ID. Unknown instruments produce a warning and are skipped.

## AI-assisted development

I used Qoder through staged prompts: plan the design, write tests first, implement the requirements, harden validation, verify CLI output, review edge cases, and perform a final audit. I reviewed the generated work and corrected these issues:

1. **Wrong schema:** the initial plan used `up`/`down` and `short_window`/`long_window`. I corrected it to the brief's `below`/`above` and `short`/`long` schema and added regression tests.
2. **Incorrect crossing interpretation:** the initial plan treated a level rule as firing only once ever. I corrected it so every genuine crossing can fire and added regression tests.
3. **Integer overflow:** a level of `10**1000` caused an uncaught `OverflowError`. I fixed validation to return a clean input error and added a regression test.

## Validation and limitation

Tests cover alert logic, input validation, and CLI behaviour. Empty rules (`[]`) are valid; zero-byte input files are errors. Extremely large closing prices near `1e308` may overflow during moving-average summation.