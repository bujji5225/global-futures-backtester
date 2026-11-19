"""Reconstruct delivery months from four public EIA daily rank tables."""
import argparse
from collections import defaultdict
from datetime import date, timedelta
from html.parser import HTMLParser
from pathlib import Path
import csv
import re
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
HISTORY_URL = "https://www.eia.gov/dnav/pet/hist/rclc{}d.htm"
START = date(2018, 1, 2)
END = date(2024, 4, 5)
MONTH_CODES = "FGHJKMNQUVXZ"
MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


class HistoryTable(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cells = None
        self.text = None
        self.values = {}

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.cells = []
        if tag == "td" and self.cells is not None:
            self.text = ""

    def handle_data(self, data):
        if self.text is not None:
            self.text += data

    def handle_endtag(self, tag):
        if tag == "td" and self.text is not None:
            self.cells.append(self.text.strip())
            self.text = None
        if tag == "tr" and self.cells is not None:
            if len(self.cells) >= 6:
                week = re.match(r"([0-9]{4}) ([A-Za-z]{3})-\s*([0-9]{1,2}) to ", self.cells[0])
                if week:
                    year, month, day = week.groups()
                    monday = date(int(year), MONTHS.index(month) + 1, int(day))
                    for i, value in enumerate(self.cells[1:6]):
                        if re.fullmatch(r"-?[0-9]+\.[0-9]+", value):
                            self.values[monday + timedelta(days=i)] = value
            self.cells = None


def add_months(first, n):
    year, month = divmod(first.year * 12 + first.month - 1 + n, 12)
    return date(year, month + 1, 1)


def expiry(delivery, sessions, last_observation):
    # Chapter 200. For deferred expiries past the publication cutoff, weekday
    # projections extend the calendar. Those dates never become prompt in this archive.
    limit = add_months(delivery, -1).replace(day=25)
    previous = []
    while len(previous) < 4:
        if limit in sessions or (limit > last_observation and limit.weekday() < 5):
            previous.append(limit)
        limit -= timedelta(days=1)
    return previous[3]


def make_archive(ranks):
    sessions = set().union(*(set(s) for s in ranks))
    latest = max(sessions)
    if latest != END:
        raise ValueError(f"Unexpected last EIA session {latest}")
    days = sorted(d for d in sessions if START <= d <= END)
    first_month = add_months(START.replace(day=1), 1)
    deliveries = [add_months(first_month, n) for n in range(80)]
    expiries = {d: expiry(d, sessions, latest) for d in deliveries}
    contracts = defaultdict(dict)
    for day in days:
        front = next(i for i, delivery in enumerate(deliveries) if day <= expiries[delivery])
        for rank, series in enumerate(ranks):
            if day in series:
                delivery = deliveries[front + rank]
                code = f"CL{MONTH_CODES[delivery.month - 1]}{delivery.year % 100:02d}"
                contracts[day][code] = (expiries[delivery], series[day])
    fixtures = {
        (date(2020, 4, 20), "CLK20"): (date(2020, 4, 21), "-37.63"),
        (date(2020, 4, 20), "CLM20"): (date(2020, 5, 19), "20.43"),
        (date(2020, 4, 21), "CLK20"): (date(2020, 4, 21), "10.01"),
    }
    for (day, code), expected in fixtures.items():
        if contracts[day].get(code) != expected:
            raise ValueError(f"April 2020 reference data mismatch for {code} on {day}")
    return contracts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path, nargs="*", help="Optional local EIA HTML files in rank order")
    args = parser.parse_args()
    if args.html and len(args.html) != 4:
        parser.error("Supply all four rank HTML files")
    ranks = []
    for rank in range(1, 5):
        table = HistoryTable()
        if args.html:
            content = args.html[rank - 1].read_bytes()
        else:
            with urlopen(HISTORY_URL.format(rank), timeout=30) as response:
                content = response.read()
        table.feed(content.decode("latin-1"))
        if len(table.values) < 1_000:
            raise ValueError(f"Unable to parse EIA Contract {rank}")
        ranks.append(table.values)
    archive = make_archive(ranks)
    with (ROOT / "data" / "eia_wti_settlements.csv").open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(("date", "contract", "expiry", "settlement"))
        for day, contracts in archive.items():
            for code, (last_trade, price) in sorted(contracts.items(), key=lambda row: row[1][0]):
                writer.writerow((day, code, last_trade, price))
    print(f"Wrote {sum(map(len, archive.values())):,} settlements across {len(archive):,} sessions.")


if __name__ == "__main__":
    main()
