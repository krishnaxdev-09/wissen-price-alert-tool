"""Price Alert Tool (Wissen campus assessment).

Reads daily closes from a prices CSV and alert rules from a JSON file, then
prints one line per alert, ordered by date and then rule id:

    python alerts.py prices.csv rules.json

Contract implemented here (see the README for the full list of documented
decisions):

- Prices CSV: header with columns ``date,instrument,close`` (names matched
  case-insensitively, column order free, extra columns ignored, blank lines
  tolerated). Dates are strict ``YYYY-MM-DD``; the close must be a finite
  number in plain decimal or scientific text (underscore digit grouping,
  NaN and infinity are rejected). One row per instrument per trading day;
  rows may appear in any order and are sorted per instrument internally.
- Rules JSON: a top-level array of objects using the brief's exact schema:
  ``id``, ``instrument``, ``type`` ("level" or "ma_cross"), ``direction``
  ("below" or "above"), plus ``level`` for level rules or ``short``/``long``
  for ma_cross rules.
- A ``level`` rule fires on each day the close crosses the level in the
  given direction: the previous close must be strictly on the other side
  (below: prev > level and now <= level; above: prev < level and now >=
  level). A close exactly at the level counts on the crossing day but never
  anchors one, so staying at the level cannot re-fire.
- An ``ma_cross`` rule fires on the day the short simple moving average
  crosses the long one, comparing consecutive observations on which both
  averages exist (above: prev short < prev long and now short >= now long;
  below mirrored). Averages need a full window of available observations.
- stdout carries only alert lines; errors and warnings go to stderr only.
  Exit codes: 0 = ran (with or without alerts), 1 = invalid input,
  2 = command-line usage error.

Standard library only; runs with a bare ``python alerts.py ...``.
"""

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import date
from typing import Dict, List, Optional, Tuple, Union

REQUIRED_COLUMNS = ("date", "instrument", "close")

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")

# Plain decimal or scientific notation, ASCII digits only. float() alone is
# too eager: it also accepts Python-literal digit grouping like "1_0"
# (= 10.0), which is not price text and would be silently reinterpreted.
_CLOSE_RE = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


class InputError(Exception):
    """Raised when a prices or rules file is missing or invalid."""


@dataclass(frozen=True)
class Rule:
    """One validated rule from the rules JSON (the brief's exact schema)."""

    id: str
    instrument: str
    type: str
    direction: str
    level: Optional[Union[int, float]] = None
    short: Optional[int] = None
    long: Optional[int] = None


@dataclass(frozen=True)
class Alert:
    """One fired alert, ready to print."""

    date: date
    rule_id: str
    instrument: str
    description: str


def load_prices(path: str) -> Dict[str, List[Tuple[date, float]]]:
    """Load and validate the prices CSV file.

    Returns ``{instrument: [(date, close), ...]}`` with each instrument's
    observations sorted by date. Raises InputError naming the file and the
    physical line number for anything malformed.
    """
    try:
        with open(path, "r", encoding="utf-8-sig", newline="") as handle:
            return _parse_prices(handle, str(path))
    except OSError as exc:
        raise InputError(f"{path}: could not read file: {exc}") from exc
    except UnicodeDecodeError as exc:  # e.g. a binary file given by mistake
        raise InputError(
            f"{path}: prices file is not valid UTF-8 text: {exc}"
        ) from exc
    except csv.Error as exc:  # e.g. a field above the csv size limit
        raise InputError(f"{path}: malformed CSV: {exc}") from exc


def _parse_prices(handle, path: str) -> Dict[str, List[Tuple[date, float]]]:
    reader = csv.reader(handle)

    # First non-blank row is the header; look columns up by name so that
    # order does not matter.
    header = None
    for row in reader:
        if any(cell.strip() for cell in row):
            header = [cell.strip().lower() for cell in row]
            break
    if header is None:
        raise InputError(
            f"{path}: file is empty; expected a header row with columns: "
            + ", ".join(REQUIRED_COLUMNS)
        )

    column_index: Dict[str, int] = {}
    for index, name in enumerate(header):
        if name in column_index and name in REQUIRED_COLUMNS:
            raise InputError(
                f"{path}: line {reader.line_num}: duplicate '{name}' "
                "column in the header"
            )
        column_index.setdefault(name, index)
    missing = [name for name in REQUIRED_COLUMNS if name not in column_index]
    if missing:
        raise InputError(
            f"{path}: header is missing required column(s): "
            f"{', '.join(missing)} (found: {', '.join(header)})"
        )

    rows: Dict[str, List[Tuple[date, float]]] = {}
    seen: Dict[Tuple[str, date], int] = {}
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue  # tolerate blank lines, as produced by some exports
        line_no = reader.line_num
        if len(row) != len(header):
            raise InputError(
                f"{path}: line {line_no}: expected {len(header)} columns "
                f"matching the header but found {len(row)}"
            )
        date_text = row[column_index["date"]].strip()
        instrument = row[column_index["instrument"]].strip()
        close_text = row[column_index["close"]].strip()

        if not instrument:
            raise InputError(f"{path}: line {line_no}: empty instrument")
        day = _parse_date(date_text, path, line_no)
        if (instrument, day) in seen:
            raise InputError(
                f"{path}: line {line_no}: duplicate row for {instrument} on "
                f"{date_text} (already seen on line {seen[(instrument, day)]})"
            )
        seen[(instrument, day)] = line_no
        close = _parse_close(close_text, path, line_no)
        rows.setdefault(instrument, []).append((day, close))

    return {
        instrument: sorted(series, key=lambda observation: observation[0])
        for instrument, series in rows.items()
    }


def _parse_date(text: str, path: str, line_no: int) -> date:
    if _DATE_RE.fullmatch(text):
        try:
            return date.fromisoformat(text)
        except ValueError:
            pass  # well-shaped but impossible, e.g. 2026-13-40
    raise InputError(
        f"{path}: line {line_no}: invalid date '{text}'; expected YYYY-MM-DD"
    )


def _parse_close(text: str, path: str, line_no: int) -> float:
    if not _CLOSE_RE.fullmatch(text):
        raise InputError(
            f"{path}: line {line_no}: invalid close '{text}'; "
            "expected a plain number"
        )
    value = float(text)
    if not math.isfinite(value):
        raise InputError(
            f"{path}: line {line_no}: invalid close '{text}'; "
            "must be finite (no NaN or infinity)"
        )
    return value


def load_rules(path: str) -> List[Rule]:
    """Load and validate the rules JSON (a top-level array of rule objects).

    File order is preserved. Raises InputError naming the rule number and
    the offending field for anything that does not match the brief's schema.
    """
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except OSError as exc:
        raise InputError(f"{path}: could not read file: {exc}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise InputError(f"{path}: invalid JSON: {exc}") from exc

    if not isinstance(data, list):
        raise InputError(
            f"{path}: top level must be a JSON array of rules, "
            f"found {_type_name(data)}"
        )

    rules: List[Rule] = []
    seen_ids = set()
    for number, raw in enumerate(data, start=1):
        rule = _parse_rule(raw, path, number)
        if rule.id in seen_ids:
            raise InputError(
                f"{path}: rule {number}: duplicate rule id '{rule.id}'"
            )
        seen_ids.add(rule.id)
        rules.append(rule)
    return rules


def _parse_rule(raw, path: str, number: int) -> Rule:
    where = f"{path}: rule {number}"
    if not isinstance(raw, dict):
        raise InputError(f"{where}: must be a JSON object, found {_type_name(raw)}")

    for field in ("id", "instrument", "type"):
        if field not in raw:
            raise InputError(f"{where}: missing required field '{field}'")
    rule_id = raw["id"]
    instrument = raw["instrument"]
    rule_type = raw["type"]

    if not isinstance(rule_id, str) or not rule_id.strip():
        raise InputError(f"{where}: 'id' must be a non-empty string")
    if not isinstance(instrument, str) or not instrument.strip():
        raise InputError(f"{where}: 'instrument' must be a non-empty string")
    if rule_type not in ("level", "ma_cross"):
        raise InputError(
            f"{where}: unsupported type {rule_type!r}; expected 'level' or 'ma_cross'"
        )
    if "direction" not in raw:
        raise InputError(f"{where}: missing required field 'direction'")
    direction = raw["direction"]
    if direction not in ("below", "above"):
        raise InputError(
            f"{where}: 'direction' must be 'below' or 'above', got {direction!r}"
        )

    if rule_type == "level":
        if "level" not in raw:
            raise InputError(f"{where}: missing required field 'level'")
        level = raw["level"]
        if isinstance(level, bool) or not isinstance(level, (int, float)):
            raise InputError(f"{where}: 'level' must be a number, got {level!r}")
        try:
            finite = math.isfinite(level)
        except OverflowError:
            # An int beyond float range (e.g. 10**1000) cannot be converted
            # to float; it can never be a price level, so reject it like
            # any other non-finite number instead of letting the raw
            # OverflowError escape.
            finite = False
        if not finite:
            raise InputError(
                f"{where}: 'level' must be a finite number, got {level!r}"
            )
        return Rule(
            id=rule_id, instrument=instrument, type=rule_type,
            direction=direction, level=level,
        )

    for field in ("short", "long"):
        if field not in raw:
            raise InputError(f"{where}: missing required field '{field}'")
    short = raw["short"]
    long = raw["long"]
    for field, value in (("short", short), ("long", long)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise InputError(
                f"{where}: '{field}' must be a positive integer "
                f"(a number of observations), got {value!r}"
            )
    if short >= long:
        raise InputError(
            f"{where}: 'short' window ({short}) must be smaller than "
            f"'long' window ({long})"
        )
    return Rule(
        id=rule_id, instrument=instrument, type=rule_type,
        direction=direction, short=short, long=long,
    )


def _type_name(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def find_alerts(
    prices: Dict[str, List[Tuple[date, float]]], rules: List[Rule]
) -> List[Alert]:
    """Evaluate every rule against the loaded prices.

    Returns alerts sorted by (date, rule id); rule ids compare as plain
    strings. Rules whose instrument has no prices produce no alerts.
    """
    alerts: List[Alert] = []
    for rule in rules:
        series = prices.get(rule.instrument)
        if not series:
            continue
        if rule.type == "level":
            alerts.extend(_level_alerts(rule, series))
        elif rule.type == "ma_cross":
            alerts.extend(_ma_cross_alerts(rule, series))
    alerts.sort(key=lambda alert: (alert.date, alert.rule_id))
    return alerts


def _level_alerts(rule: Rule, series: List[Tuple[date, float]]) -> List[Alert]:
    """Every genuine crossing, on its transition day.

    The previous close must be strictly on the other side of the level, so
    equality never anchors a crossing; the crossing day itself counts when
    the close reaches the level exactly. Staying on one side (or sitting at
    the level) never repeats an alert; returning to the other side re-arms.
    """
    alerts = []
    for (_, prev_close), (day, close) in zip(series, series[1:]):
        if rule.direction == "below":
            crossing = prev_close > rule.level and close <= rule.level
        else:
            crossing = prev_close < rule.level and close >= rule.level
        if crossing:
            alerts.append(
                Alert(day, rule.id, rule.instrument,
                      f"level {rule.direction} {rule.level}")
            )
    return alerts


def _sma(closes: List[float], index: int, window: int) -> Optional[float]:
    """Simple moving average of the last `window` closes up to `index`.

    None until a full window of available observations exists.
    """
    if index + 1 < window:
        return None
    return sum(closes[index - window + 1:index + 1]) / window


def _ma_cross_alerts(rule: Rule, series: List[Tuple[date, float]]) -> List[Alert]:
    """Crossovers, detected between consecutive valid moving averages.

    A crossover needs both averages defined on the day itself and on the
    previous day, so the earliest possible alert is on the day the long
    window is first full. Every crossover fires; equality is on the
    crossing day's side of the comparison (">=" / "<=").
    """
    closes = [close for _, close in series]
    alerts = []
    prev_short = prev_long = None
    for index in range(len(closes)):
        short_ma = _sma(closes, index, rule.short)
        long_ma = _sma(closes, index, rule.long)
        if None not in (prev_short, prev_long, short_ma, long_ma):
            if rule.direction == "above":
                crossing = prev_short < prev_long and short_ma >= long_ma
            else:
                crossing = prev_short > prev_long and short_ma <= long_ma
            if crossing:
                alerts.append(
                    Alert(series[index][0], rule.id, rule.instrument,
                          f"ma_cross {rule.short}/{rule.long} {rule.direction}")
                )
        prev_short, prev_long = short_ma, long_ma
    return alerts


def _format_alerts(alerts: List[Alert]) -> str:
    """One line per alert: date, rule id, instrument, description.

    Columns are separated by two spaces and padded to the widest printed
    value, matching the alignment of the brief's sample output.
    """
    if not alerts:
        return ""
    id_width = max(len(alert.rule_id) for alert in alerts)
    instrument_width = max(len(alert.instrument) for alert in alerts)
    lines = [
        f"{alert.date.isoformat()}  {alert.rule_id:<{id_width}}  "
        f"{alert.instrument:<{instrument_width}}  {alert.description}"
        for alert in alerts
    ]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    """Run the command-line tool; return the exit code (0, 1 or 2)."""
    parser = argparse.ArgumentParser(
        prog="alerts.py",
        description="Print price alerts for a prices CSV (date,instrument,close) "
                    "given alert rules as JSON.",
    )
    parser.add_argument("prices", help="path to the prices CSV file")
    parser.add_argument("rules", help="path to the rules JSON file")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:  # argparse already printed usage or help
        return exc.code if isinstance(exc.code, int) else 0

    try:
        prices = load_prices(args.prices)
        rules = load_rules(args.rules)
    except InputError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    for rule in rules:
        if rule.instrument not in prices:
            print(
                f"warning: rule '{rule.id}' references instrument "
                f"'{rule.instrument}', which has no price data; skipping",
                file=sys.stderr,
            )

    sys.stdout.write(_format_alerts(find_alerts(prices, rules)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
