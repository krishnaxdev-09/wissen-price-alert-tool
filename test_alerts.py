"""Test suite for the Price Alert Tool (Wissen campus assessment).

This suite was written *before* the implementation, as its specification.
It defines the contract that `alerts.py` must satisfy:

    alerts.load_prices(path)  -> {instrument: [(datetime.date, float), ...], ...}
                                 validated, per-instrument series sorted by date
    alerts.load_rules(path)   -> [Rule, ...]  (validated, file order preserved)
    alerts.find_alerts(prices, rules) -> [Alert, ...] sorted by (date, rule_id)
                                 Alert has .date, .rule_id, .instrument,
                                 .description (e.g. "level below 1500",
                                 "ma_cross 5/20 above")
    alerts.InputError         -> raised for every invalid input; the message
                                 names the offending file / line / rule / field
    alerts.main(argv)         -> int exit code
                                 0 = ran (with or without alerts)
                                 1 = invalid input (message on stderr)
                                 2 = command-line usage error (argparse)

    python alerts.py prices.csv rules.json
        prints one line per alert on stdout, nothing else; errors and warnings
        go to stderr only.

Expected values in this suite were computed by hand (the arithmetic is
documented next to each fixture); they are not copied from the implementation.
`series()` puts each fixture's closes on consecutive weekdays, and
2026-09-07 is a Monday (2026-09-01 is a Tuesday).

Run:  python -m unittest -v        (or: python test_alerts.py)
"""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

# --- Interim guard -----------------------------------------------------------
# The suite is written before the implementation; this lets it be collected
# (as skipped) while alerts.py does not exist yet, without masking any real
# import error that occurs once the file is present.
try:
    import alerts
except ModuleNotFoundError as exc:
    if exc.name != "alerts":
        raise
    alerts = None


def setUpModule():
    if alerts is None:
        raise unittest.SkipTest(
            "alerts.py is not implemented yet; this suite is its specification"
        )


ALERTS_PY = Path(__file__).resolve().with_name("alerts.py")


def weekdays(start, count):
    """First `count` weekdays (Mon-Fri) starting from `start` (inclusive)."""
    days, day = [], start
    while len(days) < count:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


def series(closes, start=date(2026, 9, 7)):
    """[(date, close), ...] for `closes` on consecutive weekdays from `start`."""
    return list(zip(weekdays(start, len(closes)), [float(c) for c in closes]))


def level_rule(rule_id, instrument, direction, level):
    """A level rule using the brief's exact field names."""
    return {
        "id": rule_id,
        "instrument": instrument,
        "type": "level",
        "direction": direction,
        "level": level,
    }


def ma_rule(rule_id, instrument, direction, short, long):
    """An ma_cross rule using the brief's exact field names."""
    return {
        "id": rule_id,
        "instrument": instrument,
        "type": "ma_cross",
        "short": short,
        "long": long,
        "direction": direction,
    }


class ToolTestCase(unittest.TestCase):
    """Temp-file helpers shared by all test classes."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_dir = Path(tmp.name)

    def write_file(self, name, content):
        """Write text (str) or raw bytes to the temp dir; return the path."""
        path = self.tmp_dir / name
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return str(path)

    def write_prices(self, csv_text):
        return self.write_file("prices.csv", csv_text)

    def write_rules(self, rule_dicts):
        return self.write_file("rules.json", json.dumps(rule_dicts))

    def csv_text(self, rows, header="date,instrument,close"):
        """CSV text from header + rows (each row a tuple of strings)."""
        lines = [header] + [",".join(str(cell) for cell in row) for row in rows]
        return "\n".join(lines) + "\n"

    def find_alert_tuples(self, prices_by_instrument, rule_dicts):
        """Run the pipeline and flatten alerts to comparable tuples."""
        rules = alerts.load_rules(self.write_rules(rule_dicts))
        found = alerts.find_alerts(prices_by_instrument, rules)
        return [(a.date, a.rule_id, a.instrument, a.description) for a in found]

    def assertInputError(self, func, *fragments):
        """Assert func() raises InputError whose message contains `fragments`."""
        with self.assertRaises(alerts.InputError) as ctx:
            func()
        message = str(ctx.exception).lower()
        for fragment in fragments:
            self.assertIn(fragment.lower(), message)

    def run_tool(self, args, cwd=None):
        """Run `python alerts.py <args...>` in a subprocess."""
        command = [sys.executable, str(ALERTS_PY)] + list(args)
        return subprocess.run(command, capture_output=True, text=True, cwd=cwd)

    def run_tool_with(self, prices_text, rule_dicts):
        """Write both inputs, then run the tool with the required two args."""
        return self.run_tool([self.write_prices(prices_text),
                              self.write_rules(rule_dicts)])


class TestLevelRules(ToolTestCase):
    """Level crossing semantics: every genuine crossing fires once, on the
    transition day; nothing fires while the price stays on one side."""

    def test_below_rule_fires_when_close_drops_through_level(self):
        # The brief's own INFY rows: 1534.10 (above 1500) then 1498.00 (below).
        prices = {"INFY": series([1520.50, 1534.10, 1498.00], start=date(2026, 9, 1))}
        got = self.find_alert_tuples(prices, [level_rule("r1", "INFY", "below", 1500)])
        self.assertEqual(
            got, [(date(2026, 9, 3), "r1", "INFY", "level below 1500")]
        )

    def test_above_rule_fires_when_close_rises_through_level(self):
        prices = {"INFY": series([1490.0, 1510.0])}
        got = self.find_alert_tuples(prices, [level_rule("r1", "INFY", "above", 1500)])
        self.assertEqual(
            got, [(date(2026, 9, 8), "r1", "INFY", "level above 1500")]
        )

    def test_no_repeat_while_price_stays_below_level(self):
        # 1510 -> 1490 fires; the three following days stay below -> no repeat.
        prices = {"INFY": series([1510.0, 1490.0, 1480.0, 1470.0])}
        got = self.find_alert_tuples(prices, [level_rule("r1", "INFY", "below", 1500)])
        self.assertEqual(
            got, [(date(2026, 9, 8), "r1", "INFY", "level below 1500")]
        )

    def test_crossing_again_after_returning_fires_a_new_alert(self):
        # 1510 -> 1490 fire; 1490 -> 1480 no; 1480 -> 1510 no (wrong direction);
        # 1510 -> 1490 fire again.
        prices = {"INFY": series([1510.0, 1490.0, 1480.0, 1510.0, 1490.0])}
        got = self.find_alert_tuples(prices, [level_rule("r1", "INFY", "below", 1500)])
        self.assertEqual(
            got,
            [
                (date(2026, 9, 8), "r1", "INFY", "level below 1500"),
                (date(2026, 9, 11), "r1", "INFY", "level below 1500"),
            ],
        )

    def test_first_observation_cannot_trigger_a_crossing(self):
        # A crossing needs a previous day; starting beyond the level never fires.
        below_start = {"INFY": series([1490.0, 1480.0, 1470.0])}
        above_start = {"INFY": series([1510.0, 1520.0, 1530.0])}
        self.assertEqual(
            self.find_alert_tuples(below_start, [level_rule("r1", "INFY", "below", 1500)]),
            [],
        )
        self.assertEqual(
            self.find_alert_tuples(above_start, [level_rule("r1", "INFY", "above", 1500)]),
            [],
        )

    def test_below_rule_fires_when_close_reaches_level_exactly(self):
        # Revised plan: previous close strictly above, current close == level
        # counts as the crossing day.
        prices = {"INFY": series([1510.0, 1500.0])}
        got = self.find_alert_tuples(prices, [level_rule("r1", "INFY", "below", 1500)])
        self.assertEqual(
            got, [(date(2026, 9, 8), "r1", "INFY", "level below 1500")]
        )

    def test_below_rule_does_not_fire_when_previous_close_was_at_level(self):
        # Revised plan: a close exactly at the level is neither side, so it
        # cannot anchor a crossing (1500 -> 1490 -> 1480 fires nothing).
        prices = {"INFY": series([1500.0, 1490.0, 1480.0])}
        got = self.find_alert_tuples(prices, [level_rule("r1", "INFY", "below", 1500)])
        self.assertEqual(got, [])

    def test_above_rule_fires_when_close_reaches_level_exactly(self):
        prices = {"INFY": series([1490.0, 1500.0])}
        got = self.find_alert_tuples(prices, [level_rule("r1", "INFY", "above", 1500)])
        self.assertEqual(
            got, [(date(2026, 9, 8), "r1", "INFY", "level above 1500")]
        )

    def test_above_rule_does_not_fire_when_previous_close_was_at_level(self):
        prices = {"INFY": series([1500.0, 1510.0])}
        got = self.find_alert_tuples(prices, [level_rule("r1", "INFY", "above", 1500)])
        self.assertEqual(got, [])

    def test_no_repeat_while_price_sits_at_level_after_crossing(self):
        # 1510 -> 1500 fires on the arrival day; 1500 -> 1500 re-checks the
        # pair (equal previous close cannot anchor) and must not fire again.
        prices = {"INFY": series([1510.0, 1500.0, 1500.0])}
        got = self.find_alert_tuples(prices, [level_rule("r1", "INFY", "below", 1500)])
        self.assertEqual(
            got, [(date(2026, 9, 8), "r1", "INFY", "level below 1500")]
        )

    def test_instrument_histories_are_independent(self):
        # INFY crosses down through 1500; TCS starts below and stays below,
        # so only INFY's rule fires even though both use the same level.
        prices = {
            "INFY": series([1510.0, 1490.0]),
            "TCS": series([1400.0, 1300.0]),
        }
        got = self.find_alert_tuples(
            prices,
            [level_rule("r1", "INFY", "below", 1500),
             level_rule("r2", "TCS", "below", 1500)],
        )
        self.assertEqual(
            got, [(date(2026, 9, 8), "r1", "INFY", "level below 1500")]
        )

    def test_out_of_order_csv_rows_give_the_same_result(self):
        shuffled = self.write_prices(self.csv_text(
            [("2026-09-03", "INFY", "1490.0"),
             ("2026-09-01", "INFY", "1510.0"),
             ("2026-09-02", "INFY", "1520.0")]))
        sorted_rows = self.write_prices(self.csv_text(
            [("2026-09-01", "INFY", "1510.0"),
             ("2026-09-02", "INFY", "1520.0"),
             ("2026-09-03", "INFY", "1490.0")]))
        loaded = alerts.load_prices(shuffled)
        # load_prices must deliver each instrument's series in date order.
        self.assertEqual(
            loaded["INFY"],
            [(date(2026, 9, 1), 1510.0),
             (date(2026, 9, 2), 1520.0),
             (date(2026, 9, 3), 1490.0)],
        )
        rule = [level_rule("r1", "INFY", "below", 1500)]
        expected = [(date(2026, 9, 3), "r1", "INFY", "level below 1500")]
        self.assertEqual(self.find_alert_tuples(loaded, rule), expected)
        self.assertEqual(
            self.find_alert_tuples(alerts.load_prices(sorted_rows), rule), expected
        )

    def test_same_date_alerts_are_sorted_by_rule_id(self):
        # Both rules fire on 2026-09-08; the file lists r2 first, so a
        # correct implementation must still print r1 first.
        prices = {
            "INFY": series([1510.0, 1490.0]),
            "TCS": series([1480.0, 1520.0]),
        }
        got = self.find_alert_tuples(
            prices,
            [level_rule("r2", "TCS", "above", 1500),
             level_rule("r1", "INFY", "below", 1500)],
        )
        self.assertEqual(
            got,
            [
                (date(2026, 9, 8), "r1", "INFY", "level below 1500"),
                (date(2026, 9, 8), "r2", "TCS", "level above 1500"),
            ],
        )

    def test_fractional_level_is_shown_as_written(self):
        # Level printed in the description without reformatting: 1500.5, and
        # the equality rule applies to it too (previous 1510 > 1500.5).
        prices = {"INFY": series([1510.0, 1500.5])}
        got = self.find_alert_tuples(prices, [level_rule("r1", "INFY", "below", 1500.5)])
        self.assertEqual(
            got, [(date(2026, 9, 8), "r1", "INFY", "level below 1500.5")]
        )


class TestMovingAverageRules(ToolTestCase):
    """SMA crossover semantics: simple average over the last N available
    observations; a crossover is a transition between consecutive valid MA
    values; full windows are required; every crossover fires once."""

    def test_upward_crossover_fires_on_the_crossover_day(self):
        # closes:  10,  10,  10,   8,   13     (2026-09-07 .. 09-11)
        # SMA(2):   -,  10,  10,   9,   10.5
        # SMA(3):   -,   -,  10,   9.33, 10.33
        # Day 5: SMA(2) 9 -> 10.5 crosses SMA(3) 9.33 -> 10.33 from below.
        prices = {"TCS": series([10.0, 10.0, 10.0, 8.0, 13.0])}
        got = self.find_alert_tuples(
            prices, [ma_rule("r2", "TCS", "above", 2, 3)]
        )
        self.assertEqual(
            got, [(date(2026, 9, 11), "r2", "TCS", "ma_cross 2/3 above")]
        )

    def test_downward_crossover_fires_on_the_crossover_day(self):
        # closes:  10,  10,  10,   12,  7
        # SMA(2):   -,  10,  10,   11,  9.5
        # SMA(3):   -,   -,  10,   10.67, 9.67
        prices = {"TCS": series([10.0, 10.0, 10.0, 12.0, 7.0])}
        got = self.find_alert_tuples(
            prices, [ma_rule("r2", "TCS", "below", 2, 3)]
        )
        self.assertEqual(
            got, [(date(2026, 9, 11), "r2", "TCS", "ma_cross 2/3 below")]
        )

    def test_moving_averages_need_full_windows(self):
        # 3 observations for a long window of 3: the long MA exists only on
        # day 3, so no crossover can be evaluated yet -> nothing fires.
        prices = {"TCS": series([10.0, 10.0, 20.0])}
        got = self.find_alert_tuples(
            prices, [ma_rule("r2", "TCS", "above", 2, 3)]
        )
        self.assertEqual(got, [])

    def test_insufficient_history_is_silent_and_safe(self):
        prices = {"TCS": series([10.0, 10.0, 10.0, 10.0])}
        got = self.find_alert_tuples(
            prices, [ma_rule("r2", "TCS", "above", 2, 20)]
        )
        self.assertEqual(got, [])

    def test_crossover_detected_on_first_day_both_windows_are_valid(self):
        # closes:  12,  10,  10,   14      (4 observations, short 2, long 3)
        # SMA(2):   -,  11,  10,   12
        # SMA(3):   -,   -,  10.67, 11.33
        # Day 3 is the first evaluable day (long MA valid on days 2 and 3).
        prices = {"TCS": series([12.0, 10.0, 10.0, 14.0])}
        got = self.find_alert_tuples(
            prices, [ma_rule("r2", "TCS", "above", 2, 3)]
        )
        self.assertEqual(
            got, [(date(2026, 9, 10), "r2", "TCS", "ma_cross 2/3 above")]
        )

    def test_no_new_alert_while_short_stays_above_after_crossover(self):
        # After the day-5 crossover, the short MA keeps rising (10.5, 13.5,
        # 14.5 vs long 10.33, 11.67, 14.0) but does not cross again -> one alert.
        prices = {"TCS": series([10.0, 10.0, 10.0, 8.0, 13.0, 14.0, 15.0])}
        got = self.find_alert_tuples(
            prices, [ma_rule("r2", "TCS", "above", 2, 3)]
        )
        self.assertEqual(
            got, [(date(2026, 9, 11), "r2", "TCS", "ma_cross 2/3 above")]
        )

    def test_repeated_crossovers_each_fire(self):
        # closes:  10, 10, 10,  8,  13,  2,  26   (2026-09-07 .. 09-15)
        # SMA(2):   -, 10, 10,  9,  10.5, 7.5, 14
        # SMA(3):   -,  -, 10,  9.33, 10.33, 7.67, 13.67
        # Up-cross on day 5 (9 < 9.33 -> 10.5 >= 10.33), short dips below
        # (7.5 < 7.67), then up-crosses again on day 7.
        prices = {"TCS": series([10.0, 10.0, 10.0, 8.0, 13.0, 2.0, 26.0])}
        got = self.find_alert_tuples(
            prices, [ma_rule("r2", "TCS", "above", 2, 3)]
        )
        self.assertEqual(
            got,
            [
                (date(2026, 9, 11), "r2", "TCS", "ma_cross 2/3 above"),
                (date(2026, 9, 15), "r2", "TCS", "ma_cross 2/3 above"),
            ],
        )

    def test_equality_on_the_crossing_day_counts(self):
        # short 1, long 3.  closes: 10, 8, 8, 8
        # SMA(1): 10, 8, 8, 8 ;  SMA(3): -, -, 8.67, 8.0
        # Day 3: short 8 < long 8.67 (strictly below); day 4: short 8 == long 8
        # -> reaching equality from below fires (revised plan).
        prices = {"TCS": series([10.0, 8.0, 8.0, 8.0])}
        got = self.find_alert_tuples(
            prices, [ma_rule("r2", "TCS", "above", 1, 3)]
        )
        self.assertEqual(
            got, [(date(2026, 9, 10), "r2", "TCS", "ma_cross 1/3 above")]
        )

    def test_equality_on_the_previous_day_does_not_anchor(self):
        # closes: 10, 8, 9, 10 ; SMA(1): 10, 8, 9, 10 ; SMA(3): -, -, 9, 9
        # Day 3: short == long (9 == 9) -> not strictly below; day 4: short is
        # above but the previous day was not on the other side -> no alert.
        prices = {"TCS": series([10.0, 8.0, 9.0, 10.0])}
        got = self.find_alert_tuples(
            prices, [ma_rule("r2", "TCS", "above", 1, 3)]
        )
        self.assertEqual(got, [])

    def test_no_alert_merely_because_short_is_above_long(self):
        # closes: 10, 30, 30, 30 ; SMA(2): -, 20, 30, 30 ; SMA(3): -, -, 23.33, 30
        # The short MA is above the long from its first valid observation, but
        # it never crossed *during* the series -> a "currently above" check
        # must not fire; only a consecutive-observation crossover counts.
        prices = {"TCS": series([10.0, 30.0, 30.0, 30.0])}
        got = self.find_alert_tuples(
            prices, [ma_rule("r2", "TCS", "above", 2, 3)]
        )
        self.assertEqual(got, [])

    def test_instruments_do_not_share_moving_average_history(self):
        # TCS fires its day-5 crossover; INFY has its own 3 observations and
        # its own (also non-firing) rule. Mixing histories by date would move
        # TCS's crossover day and is detected by the exact expected date.
        prices = {
            "TCS": series([10.0, 10.0, 10.0, 8.0, 13.0]),
            "INFY": series([50.0, 40.0, 30.0]),
        }
        got = self.find_alert_tuples(
            prices,
            [ma_rule("r2", "TCS", "above", 2, 3),
             ma_rule("r3", "INFY", "above", 2, 3)],
        )
        self.assertEqual(
            got, [(date(2026, 9, 11), "r2", "TCS", "ma_cross 2/3 above")]
        )

    def test_five_twenty_windows_fire_on_a_hand_computed_day(self):
        # The brief's 5/20 windows.  23 weekdays from 2026-09-01.
        # closes: days 1-16 = 100, days 17-20 = 90, day 21 = 100,
        #         day 22 = 110, day 23 = 120.
        # SMA(5):  day20=92, 21=92, 22=96, 23=102
        # SMA(20): day20=98, 21=98, 22=98.5, 23=99.5
        # Day 21 is the first evaluable day (long MA valid on days 20 and 21;
        # the long MA first exists on day 20).  The crossover is on day 23:
        # previously 96 < 98.5, now 102 >= 99.5.
        closes = [100.0] * 16 + [90.0] * 4 + [100.0, 110.0, 120.0]
        prices = {"TCS": series(closes, start=date(2026, 9, 1))}
        got = self.find_alert_tuples(
            prices, [ma_rule("r2", "TCS", "above", 5, 20)]
        )
        self.assertEqual(
            got, [(date(2026, 10, 1), "r2", "TCS", "ma_cross 5/20 above")]
        )

    def test_unsorted_input_rows_do_not_change_the_result(self):
        shuffled = self.write_prices(self.csv_text(
            [("2026-09-10", "TCS", "8"),
             ("2026-09-07", "TCS", "10"),
             ("2026-09-11", "TCS", "13"),
             ("2026-09-08", "TCS", "10"),
             ("2026-09-09", "TCS", "10")]))
        sorted_rows = self.write_prices(self.csv_text(
            [("2026-09-07", "TCS", "10"),
             ("2026-09-08", "TCS", "10"),
             ("2026-09-09", "TCS", "10"),
             ("2026-09-10", "TCS", "8"),
             ("2026-09-11", "TCS", "13")]))
        rule = [ma_rule("r2", "TCS", "above", 2, 3)]
        expected = [(date(2026, 9, 11), "r2", "TCS", "ma_cross 2/3 above")]
        self.assertEqual(
            self.find_alert_tuples(alerts.load_prices(shuffled), rule), expected
        )
        self.assertEqual(
            self.find_alert_tuples(alerts.load_prices(sorted_rows), rule), expected
        )


class TestPricesCsvValidation(ToolTestCase):
    """Real-data failure modes for the prices file: loader must fail fast with
    a message that identifies the problem, or accept documented edge cases."""

    def test_missing_prices_file_raises_input_error(self):
        self.assertInputError(
            lambda: alerts.load_prices(str(self.tmp_dir / "does_not_exist.csv"))
        )

    def test_header_must_contain_date_instrument_close(self):
        wrong_name = self.write_prices(
            "day,instrument,close\n2026-09-01,INFY,100\n")
        no_close = self.write_prices(
            "date,instrument\n2026-09-01,INFY\n")
        data_as_header = self.write_prices(
            "2026-09-01,INFY,100\n2026-09-02,INFY,110\n")
        self.assertInputError(lambda: alerts.load_prices(wrong_name), "date")
        self.assertInputError(lambda: alerts.load_prices(no_close), "close")
        self.assertInputError(lambda: alerts.load_prices(data_as_header), "date")

    def test_invalid_dates_are_rejected_with_the_line_number(self):
        for bad_date in ("2026-13-40", "03/01/2026"):
            with self.subTest(bad_date=bad_date):
                path = self.write_prices(self.csv_text(
                    [("2026-09-01", "INFY", "100"),
                     (bad_date, "INFY", "110")]))
                self.assertInputError(lambda: alerts.load_prices(path), "line 3")

    def test_invalid_close_values_are_rejected(self):
        # "NaN" and "inf" parse as floats, so they must be rejected as
        # non-finite explicitly; "abc" and "" are not numbers at all.
        for bad_close in ("abc", "", "NaN", "inf"):
            with self.subTest(bad_close=bad_close):
                path = self.write_prices(self.csv_text(
                    [("2026-09-01", "INFY", bad_close)]))
                self.assertInputError(lambda: alerts.load_prices(path), "line 2")

    def test_negative_and_zero_prices_are_valid(self):
        # Documented decision: any finite float is a valid price.
        path = self.write_prices(self.csv_text(
            [("2026-09-01", "INFY", "-5"),
             ("2026-09-02", "INFY", "0")]))
        loaded = alerts.load_prices(path)
        self.assertEqual(
            loaded["INFY"],
            [(date(2026, 9, 1), -5.0), (date(2026, 9, 2), 0.0)],
        )

    def test_empty_instrument_is_rejected(self):
        path = self.write_prices(self.csv_text([("2026-09-01", "", "100")]))
        self.assertInputError(lambda: alerts.load_prices(path), "line 2")

    def test_rows_with_the_wrong_number_of_fields_are_rejected(self):
        missing_close = self.write_prices(
            "date,instrument,close\n2026-09-01,INFY\n")
        extra_field = self.write_prices(
            "date,instrument,close\n2026-09-01,INFY,100,999\n")
        self.assertInputError(lambda: alerts.load_prices(missing_close), "line 2")
        self.assertInputError(lambda: alerts.load_prices(extra_field), "line 2")

    def test_duplicate_instrument_date_rows_are_rejected(self):
        # One row per instrument per trading day: conflicting duplicates are
        # ambiguous and must fail rather than silently pick one.
        path = self.write_prices(self.csv_text(
            [("2026-09-01", "INFY", "100"),
             ("2026-09-01", "INFY", "101")]))
        self.assertInputError(lambda: alerts.load_prices(path), "duplicate")

    def test_same_date_for_different_instruments_is_fine(self):
        path = self.write_prices(self.csv_text(
            [("2026-09-01", "INFY", "100"),
             ("2026-09-01", "TCS", "200")]))
        loaded = alerts.load_prices(path)
        self.assertEqual(set(loaded), {"INFY", "TCS"})

    def test_header_only_file_loads_as_empty(self):
        loaded = alerts.load_prices(self.write_prices("date,instrument,close\n"))
        self.assertEqual(loaded, {})

    def test_extra_columns_are_ignored(self):
        path = self.write_prices(
            "date,instrument,close,volume\n2026-09-01,INFY,100,12345\n")
        self.assertEqual(
            alerts.load_prices(path)["INFY"],
            [(date(2026, 9, 1), 100.0)],
        )

    def test_column_order_is_flexible(self):
        path = self.write_prices(
            "close,date,instrument\n100,2026-09-01,INFY\n")
        self.assertEqual(
            alerts.load_prices(path)["INFY"],
            [(date(2026, 9, 1), 100.0)],
        )

    def test_bom_crlf_and_blank_lines_are_tolerated(self):
        # A UTF-8 BOM, Windows line endings and a stray blank line: real-world
        # exports contain these; values must still parse correctly.
        content = (
            b"\xef\xbb\xbfdate,instrument,close\r\n"
            b"2026-09-01,INFY,100\r\n"
            b"\r\n"
            b"2026-09-02,INFY,110\r\n"
        )
        loaded = alerts.load_prices(self.write_file("prices.csv", content))
        self.assertEqual(
            loaded["INFY"],
            [(date(2026, 9, 1), 100.0), (date(2026, 9, 2), 110.0)],
        )

    def test_empty_prices_file_is_rejected(self):
        # A zero-byte file has no header at all; header-only is fine (see
        # test_header_only_file_loads_as_empty) but a truly empty file is not.
        self.assertInputError(
            lambda: alerts.load_prices(self.write_prices("")), "empty"
        )

    def test_underscore_digit_grouping_is_rejected(self):
        # float("1_0") == 10.0 in Python, but "1_0" is not a plain price and
        # must not be silently reinterpreted as grouped digits.
        for bad_close in ("1_0", "1_000.50"):
            with self.subTest(bad_close=bad_close):
                path = self.write_prices(self.csv_text(
                    [("2026-09-01", "INFY", bad_close)]))
                self.assertInputError(
                    lambda: alerts.load_prices(path), "line 2", "close"
                )

    def test_plain_decimal_sign_and_exponent_forms_are_accepted(self):
        # Validation decision: ordinary decimal text, an optional sign, an
        # optional leading dot and scientific notation stay valid prices.
        path = self.write_prices(self.csv_text(
            [("2026-09-01", "INFY", "1.52e3"),
             ("2026-09-02", "INFY", ".5"),
             ("2026-09-03", "INFY", "-0.5")]))
        self.assertEqual(
            alerts.load_prices(path)["INFY"],
            [(date(2026, 9, 1), 1520.0),
             (date(2026, 9, 2), 0.5),
             (date(2026, 9, 3), -0.5)],
        )

    def test_oversized_csv_field_is_reported_as_malformed(self):
        # The csv module raises its own error for fields over its size limit;
        # that must surface as a clear InputError, not a traceback.
        path = self.write_prices(
            "date,instrument,close\n2026-09-01,INFY," + "9" * 200000 + "\n"
        )
        self.assertInputError(lambda: alerts.load_prices(path), "malformed")


class TestRulesValidation(ToolTestCase):
    """The rules file uses the brief's exact schema; anything else fails with
    a message that names the offending rule and field."""

    def test_missing_rules_file_raises_input_error(self):
        self.assertInputError(
            lambda: alerts.load_rules(str(self.tmp_dir / "does_not_exist.json"))
        )

    def test_malformed_json_is_rejected(self):
        path = self.write_file("rules.json", "{not json")
        self.assertInputError(lambda: alerts.load_rules(path))

    def test_top_level_must_be_a_json_array(self):
        path = self.write_file("rules.json", '{"rules": []}')
        self.assertInputError(lambda: alerts.load_rules(path), "array")

    def test_rules_must_be_objects(self):
        self.assertInputError(
            lambda: alerts.load_rules(self.write_rules([42])), "object"
        )

    def test_missing_required_fields_are_rejected(self):
        cases = [
            ({"instrument": "INFY", "type": "level",
              "direction": "below", "level": 1500}, "id"),
            ({"id": "r1", "type": "level",
              "direction": "below", "level": 1500}, "instrument"),
            ({"id": "r1", "instrument": "INFY",
              "direction": "below", "level": 1500}, "type"),
            ({"id": "r1", "instrument": "INFY", "type": "level",
              "level": 1500}, "direction"),
            ({"id": "r1", "instrument": "INFY", "type": "level",
              "direction": "below"}, "level"),
            ({"id": "r2", "instrument": "TCS", "type": "ma_cross",
              "long": 20, "direction": "above"}, "short"),
            ({"id": "r2", "instrument": "TCS", "type": "ma_cross",
              "short": 5, "direction": "above"}, "long"),
        ]
        for rule, missing_field in cases:
            with self.subTest(missing_field=missing_field):
                path = self.write_rules([rule])
                self.assertInputError(
                    lambda: alerts.load_rules(path), missing_field
                )

    def test_unsupported_type_is_rejected(self):
        rule = {"id": "r1", "instrument": "INFY", "type": "trend",
                "direction": "below", "level": 1500}
        self.assertInputError(
            lambda: alerts.load_rules(self.write_rules([rule])), "type"
        )

    def test_unsupported_direction_values_are_rejected(self):
        # The brief's schema uses "below"/"above"; "up"/"down" and casing
        # variants are not part of it.
        for direction in ("up", "down", "Down", "BELOW"):
            with self.subTest(direction=direction):
                rule = level_rule("r1", "INFY", direction, 1500)
                self.assertInputError(
                    lambda: alerts.load_rules(self.write_rules([rule])),
                    "direction",
                )

    def test_alternative_schema_names_are_rejected(self):
        # Guards against the wrong schema: short_window/long_window instead
        # of short/long is a missing "short" field.
        rule = {"id": "r2", "instrument": "TCS", "type": "ma_cross",
                "short_window": 5, "long_window": 20, "direction": "above"}
        self.assertInputError(
            lambda: alerts.load_rules(self.write_rules([rule])), "short"
        )

    def test_invalid_level_values_are_rejected(self):
        # A JSON string or null is not a number; `true` is a bool, not 1.
        for bad_level in ("1500", True, None):
            with self.subTest(level=bad_level):
                rule = level_rule("r1", "INFY", "below", bad_level)
                self.assertInputError(
                    lambda: alerts.load_rules(self.write_rules([rule])), "level"
                )

    def test_invalid_window_values_are_rejected(self):
        cases = [("short", 0), ("short", -1), ("short", 2.5),
                 ("long", 0), ("long", True), ("long", "20")]
        for field, bad_value in cases:
            with self.subTest(field=field, value=bad_value):
                rule = ma_rule("r2", "TCS", "above", 5, 20)
                rule[field] = bad_value
                self.assertInputError(
                    lambda: alerts.load_rules(self.write_rules([rule])), field
                )

    def test_short_window_must_be_smaller_than_long(self):
        for short, long in ((20, 20), (21, 20)):
            with self.subTest(short=short, long=long):
                rule = ma_rule("r2", "TCS", "above", short, long)
                self.assertInputError(
                    lambda: alerts.load_rules(self.write_rules([rule])),
                    "short", "long",
                )

    def test_duplicate_rule_ids_are_rejected(self):
        rules = [level_rule("r1", "INFY", "below", 1500),
                 level_rule("r1", "TCS", "below", 100)]
        self.assertInputError(
            lambda: alerts.load_rules(self.write_rules(rules)), "duplicate"
        )

    def test_unknown_rule_keys_are_ignored(self):
        rule = level_rule("r1", "INFY", "below", 1500)
        rule["note"] = "extra keys are ignored by design"
        prices = {"INFY": series([1510.0, 1490.0])}
        got = self.find_alert_tuples(prices, [rule])
        self.assertEqual(
            got, [(date(2026, 9, 8), "r1", "INFY", "level below 1500")]
        )

    def test_empty_rule_array_is_allowed_and_fires_nothing(self):
        rules = alerts.load_rules(self.write_rules([]))
        self.assertEqual(rules, [])
        self.assertEqual(alerts.find_alerts({"INFY": series([1510.0, 1490.0])}, rules), [])

    def test_rule_for_instrument_absent_from_prices_yields_no_alerts(self):
        # Configuration/data mismatch must not crash detection; the CLI layer
        # additionally warns on stderr for this case (see TestCommandLine).
        prices = {"INFY": series([1510.0, 1490.0])}
        got = self.find_alert_tuples(prices, [level_rule("r9", "ZZZZ", "below", 100)])
        self.assertEqual(got, [])

    def test_empty_rules_file_is_rejected(self):
        # An empty file is not the valid empty rule list; "[]" is (see
        # test_empty_rule_array_is_allowed_and_fires_nothing).
        self.assertInputError(
            lambda: alerts.load_rules(self.write_file("rules.json", "")),
            "json",
        )

    def test_nan_and_infinity_levels_are_rejected(self):
        # Python's json parser accepts NaN/Infinity literals; a level that is
        # not a finite number cannot be compared against prices.
        for bad_level in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(level=bad_level):
                rule = level_rule("r1", "INFY", "below", bad_level)
                self.assertInputError(
                    lambda: alerts.load_rules(self.write_rules([rule])), "level"
                )

    def test_oversized_integer_level_is_rejected_cleanly(self):
        # A JSON integer beyond float range (10**1000) cannot be converted
        # to float, so math.isfinite() raises OverflowError on it; that must
        # surface as a normal InputError, never as a raw OverflowError.
        huge = level_rule("r1", "INFY", "below", 10 ** 1000)
        self.assertInputError(
            lambda: alerts.load_rules(self.write_rules([huge])),
            "rule 1", "level", "finite",
        )
        # A large integer within float range must still be accepted, kept
        # exactly as written.
        big = level_rule("r1", "INFY", "below", 10 ** 300)
        self.assertEqual(
            alerts.load_rules(self.write_rules([big]))[0].level, 10 ** 300
        )

    def test_whitespace_only_id_and_instrument_are_rejected(self):
        blank_id = level_rule("   ", "INFY", "below", 1500)
        self.assertInputError(
            lambda: alerts.load_rules(self.write_rules([blank_id])), "id"
        )
        blank_instrument = level_rule("r1", "\t", "below", 1500)
        self.assertInputError(
            lambda: alerts.load_rules(self.write_rules([blank_instrument])),
            "instrument",
        )

    def test_non_string_id_and_instrument_are_rejected(self):
        numeric_id = level_rule(1, "INFY", "below", 1500)
        self.assertInputError(
            lambda: alerts.load_rules(self.write_rules([numeric_id])), "id"
        )
        numeric_instrument = level_rule("r1", 5, "below", 1500)
        self.assertInputError(
            lambda: alerts.load_rules(self.write_rules([numeric_instrument])),
            "instrument",
        )


class TestCommandLine(ToolTestCase):
    """End-to-end runs of `python alerts.py prices.csv rules.json`:
    exact stdout, stderr separation, and exit codes."""

    def test_required_command_prints_exact_alerts(self):
        # INFY uses the brief's rows (fire on 2026-09-03); TCS uses the
        # small 2/3 crossover fixture (fire on 2026-09-11, arithmetic in
        # TestMovingAverageRules).  Expected stdout, byte for byte:
        # columns are padded to the widest printed value with two-space gaps.
        prices_text = self.csv_text([
            ("2026-09-01", "INFY", "1520.50"),
            ("2026-09-02", "INFY", "1534.10"),
            ("2026-09-03", "INFY", "1498.00"),
            ("2026-09-07", "TCS", "10"),
            ("2026-09-08", "TCS", "10"),
            ("2026-09-09", "TCS", "10"),
            ("2026-09-10", "TCS", "8"),
            ("2026-09-11", "TCS", "13"),
        ])
        rules = [level_rule("r1", "INFY", "below", 1500),
                 ma_rule("r2", "TCS", "above", 2, 3)]
        result = self.run_tool_with(prices_text, rules)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout,
            "2026-09-03  r1  INFY  level below 1500\n"
            "2026-09-11  r2  TCS   ma_cross 2/3 above\n",
        )
        self.assertEqual(result.stderr, "")

    def test_columns_align_to_widest_values_and_ids_sort_as_strings(self):
        # Rule ids "r10"/"r2" and instruments "BB"/"A" exercise both the
        # column padding and the documented string ordering ("r10" < "r2");
        # the file lists r2 first, so sorting is what puts r10 on top.
        prices_text = self.csv_text([
            ("2026-09-01", "BB", "10"),
            ("2026-09-02", "BB", "25"),
            ("2026-09-01", "A", "60"),
            ("2026-09-02", "A", "40"),
        ])
        rules = [level_rule("r2", "A", "below", 50),
                 level_rule("r10", "BB", "above", 20)]
        result = self.run_tool_with(prices_text, rules)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout,
            "2026-09-02  r10  BB  level above 20\n"
            "2026-09-02  r2   A   level below 50\n",
        )
        self.assertEqual(result.stderr, "")

    def test_empty_stdout_when_no_rule_fires(self):
        prices_text = self.csv_text([
            ("2026-09-01", "INFY", "1490"),
            ("2026-09-02", "INFY", "1480"),
        ])
        result = self.run_tool_with(
            prices_text, [level_rule("r1", "INFY", "below", 1500)]
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_header_only_csv_and_empty_rules_exit_zero(self):
        result = self.run_tool_with("date,instrument,close\n", [])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertEqual(result.stderr, "")

    def test_invalid_inputs_report_on_stderr_with_exit_code_1(self):
        bad_prices = self.csv_text([("2026-09-01", "INFY", "abc")])
        good_rule = [level_rule("r1", "INFY", "below", 1500)]
        result = self.run_tool_with(bad_prices, good_rule)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("error", result.stderr.lower())
        self.assertIn("line 2", result.stderr)

        good_prices = self.csv_text([("2026-09-01", "INFY", "100")])
        bad_rule = [level_rule("r1", "INFY", "up", 1500)]  # invalid direction
        result = self.run_tool_with(good_prices, bad_rule)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")

    def test_missing_file_is_reported_on_stderr(self):
        result = self.run_tool(
            [str(self.tmp_dir / "missing.csv"),
             self.write_rules([level_rule("r1", "INFY", "below", 1500)])]
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertNotEqual(result.stderr, "")

    def test_missing_arguments_show_usage_error(self):
        result = self.run_tool([])
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, "")
        self.assertIn("usage", result.stderr.lower())

    def test_unknown_instrument_warns_on_stderr_and_keeps_running(self):
        prices_text = self.csv_text([
            ("2026-09-07", "INFY", "1510"),
            ("2026-09-08", "INFY", "1490"),
        ])
        rules = [level_rule("r1", "INFY", "below", 1500),
                 level_rule("r9", "ZZZZ", "above", 100)]
        result = self.run_tool_with(prices_text, rules)
        self.assertEqual(result.returncode, 0)
        # The valid rule still fires; the missing instrument is only warned
        # about, on stderr, and never mixed into the alert output.
        self.assertEqual(
            result.stdout, "2026-09-08  r1  INFY  level below 1500\n"
        )
        self.assertIn("ZZZZ", result.stderr)
        self.assertIn("warn", result.stderr.lower())

    def test_brief_sample_output_is_reproduced_exactly(self):
        # The brief's example output, byte for byte:
        #   2026-09-03  r1  INFY  level below 1500
        #   2026-09-24  r2  TCS   ma_cross 5/20 above
        # INFY uses the brief's rows.  TCS is engineered so the 5/20 golden
        # cross lands exactly on 2026-09-24 (21st weekday from 2026-08-27):
        #   closes: 16 x 100 (08-27..09-17), 4 x 90 (09-18..09-23), 140
        #   long window first full on 09-23; SMA(5) 92 < SMA(20) 98 there,
        #   then SMA(5) (360+140)/5 = 100 >= SMA(20) (1860+140)/20 = 100.
        days = weekdays(date(2026, 8, 27), 21)
        closes = [100.0] * 16 + [90.0] * 4 + [140.0]
        rows = [("2026-09-01", "INFY", "1520.50"),
                ("2026-09-02", "INFY", "1534.10"),
                ("2026-09-03", "INFY", "1498.00")]
        rows += [(day.isoformat(), "TCS", f"{close:g}")
                 for day, close in zip(days, closes)]
        result = self.run_tool_with(
            self.csv_text(rows),
            [level_rule("r1", "INFY", "below", 1500),
             ma_rule("r2", "TCS", "above", 5, 20)],
        )
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout,
            "2026-09-03  r1  INFY  level below 1500\n"
            "2026-09-24  r2  TCS   ma_cross 5/20 above\n",
        )
        self.assertEqual(result.stderr, "")

    def test_relative_paths_resolve_from_the_working_directory(self):
        # The brief's command form: `python alerts.py prices.csv rules.json`,
        # with both files given relative to the directory the tool runs in.
        self.write_prices(self.csv_text([
            ("2026-09-01", "INFY", "1520.50"),
            ("2026-09-02", "INFY", "1534.10"),
            ("2026-09-03", "INFY", "1498.00"),
        ]))
        self.write_rules([level_rule("r1", "INFY", "below", 1500)])
        result = self.run_tool(["prices.csv", "rules.json"], cwd=self.tmp_dir)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout, "2026-09-03  r1  INFY  level below 1500\n"
        )
        self.assertEqual(result.stderr, "")

    def test_paths_containing_spaces_work(self):
        # Inputs often live in folders like "My Documents"; the paths travel
        # through argv untouched and must simply work.
        folder = self.tmp_dir / "prices and rules"
        folder.mkdir()
        prices = self.write_file(
            "prices and rules/prices file.csv",
            self.csv_text([("2026-09-01", "INFY", "1510"),
                           ("2026-09-02", "INFY", "1490")]),
        )
        rules = self.write_file(
            "prices and rules/rules file.json",
            json.dumps([level_rule("r1", "INFY", "below", 1500)]),
        )
        result = self.run_tool([prices, rules])
        self.assertEqual(result.returncode, 0)
        self.assertEqual(
            result.stdout, "2026-09-02  r1  INFY  level below 1500\n"
        )
        self.assertEqual(result.stderr, "")

    def test_directory_instead_of_input_file_is_reported(self):
        # An unreadable input path (a directory here) must produce a clean
        # error on stderr with exit code 1, never a traceback.
        result = self.run_tool([str(self.tmp_dir), str(self.tmp_dir)])
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertIn("error", result.stderr.lower())
        self.assertNotIn("Traceback", result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
