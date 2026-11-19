"""Validate contract-level OHLCV and generate reproducible demonstration data."""
import csv
from dataclasses import dataclass
from hashlib import sha256
from io import StringIO
from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS = ["date", "contract", "expiry", "open", "high", "low", "close", "volume"]
SETTLEMENT_COLUMNS = ["date", "contract", "expiry", "settlement"]
CONTRACT_CODE = r"(?:MCL|CL)[FGHJKMNQUVXZ](?:[0-9]{1,2}|[0-9]{4})"
DEMO_CODE = r"DEMO[0-9]{3,}"
EIA_PATH = Path(__file__).resolve().parents[1] / "data" / "eia_wti_settlements.csv"


@dataclass(frozen=True)
class Dataset:
    frame: pd.DataFrame
    label: str
    synthetic: bool
    fingerprint: str
    was_reordered: bool
    calendar: tuple = ()
    calendar_label: str = "Weekday approximation"

    @property
    def settlement_only(self):
        return "settlement" in self.frame.columns

    @property
    def price_column(self):
        return "settlement" if self.settlement_only else "close"

    @property
    def execution(self):
        return "Next settlement proxy" if self.settlement_only else "Next session open"


def validate(data, product=None):
    settlement_only = "settlement" in data.columns and "open" not in data.columns
    columns = SETTLEMENT_COLUMNS if settlement_only else COLUMNS
    missing = set(columns) - set(data.columns)
    if missing:
        raise ValueError(f"Missing columns: {', '.join(sorted(missing))}.")
    if data.columns.has_duplicates:
        raise ValueError("Column names must be unique.")
    df = data[columns].copy()
    if df.empty or df.isna().any().any():
        raise ValueError("Provide at least one row with all required values populated.")

    for col in ("date", "expiry"):
        values = df[col]
        if not pd.api.types.is_datetime64_any_dtype(values):
            values = values.astype(str).str.strip()
            if not values.str.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}").all():
                raise ValueError(f"{col} must use YYYY-MM-DD trading dates.")
        try:
            parsed = pd.to_datetime(values, format="%Y-%m-%d", errors="raise")
        except (ValueError, OverflowError) as error:
            raise ValueError(f"Invalid calendar date in {col}.") from error
        if parsed.dt.tz is not None or parsed.ne(parsed.dt.normalize()).any():
            raise ValueError(f"{col} must contain timezone-free dates without times.")
        df[col] = parsed

    df["contract"] = df["contract"].astype(str).str.strip().str.upper()
    demo = df.contract.str.fullmatch(DEMO_CODE)
    if not (demo | df.contract.str.fullmatch(CONTRACT_CODE)).all():
        raise ValueError("Use outright CL/MCL identifiers such as CLZ25 or MCLF2026.")
    if demo.any() and not demo.all():
        raise ValueError("Use separate datasets for synthetic and market contracts.")
    if not demo.all():
        roots = df.contract.str.extract(r"^(MCL|CL)", expand=False)
        if roots.nunique() != 1 or (product is not None and roots.iloc[0] != product):
            raise ValueError("Input must match a single selected CL or MCL product.")

    for col in columns[3:]:
        try:
            df[col] = pd.to_numeric(df[col], errors="raise")
        except (ValueError, TypeError) as error:
            raise ValueError(f"{col} must be numeric.") from error
        if not np.isfinite(df[col]).all():
            raise ValueError(f"{col} contains a non-finite value.")
    if df.duplicated(["date", "contract"]).any():
        raise ValueError("Duplicate trading date and contract.")
    if not settlement_only:
        if (df.high < df[["open", "close", "low"]].max(axis=1)).any() or (
            df.low > df[["open", "close", "high"]].min(axis=1)
        ).any():
            raise ValueError("Invalid OHLC bounds.")
        if ((df.volume < 0) | (df.volume % 1 != 0)).any():
            raise ValueError("Volume must be a nonnegative whole number.")
    if (df.date > df.expiry).any():
        raise ValueError("A contract has a quote after its last trading date.")

    metadata = df[["contract", "expiry"]].drop_duplicates()
    if metadata.contract.duplicated().any():
        raise ValueError("Each contract must have one consistent expiry.")
    if metadata.expiry.duplicated().any():
        raise ValueError("Each expiry must identify one contract in the selected product.")
    return df.sort_values(["date", "expiry", "contract"]).reset_index(drop=True)


def make_dataset(frame, label, product=None, calendar=(), calendar_label="Weekday approximation"):
    validated = validate(frame, product)
    original_dates = frame["date"].astype(str).str.strip()
    ordered = pd.to_datetime(original_dates, format="%Y-%m-%d").is_monotonic_increasing
    synthetic = validated.contract.str.fullmatch(DEMO_CODE).all()
    csv = validated.to_csv(index=False, date_format="%Y-%m-%d").encode()
    calendar = tuple(calendar)
    if calendar and not pd.DatetimeIndex(validated.date.unique()).isin(calendar).all():
        raise ValueError("The session calendar must include every trading date in this dataset.")
    return Dataset(validated, label, bool(synthetic), sha256(csv).hexdigest(), not ordered, calendar, calendar_label)


def csv_frame(content):
    try:
        reader = csv.reader(StringIO(content.decode("utf-8-sig"), newline=""), strict=True)
        header = next(reader)
        rows = []
        for row in reader:
            if not row:
                continue
            if len(row) != len(header):
                raise ValueError(f"CSV line {reader.line_num} has {len(row)} fields. Expected {len(header)}.")
            rows.append(row)
    except (csv.Error, StopIteration, UnicodeDecodeError) as error:
        raise ValueError("The file must be a readable UTF-8 CSV with one header row.") from error
    return pd.DataFrame(rows, columns=header)


def load_csv(content, filename, product, calendar_content=None):
    calendar = ()
    label = "Weekday approximation"
    if calendar_content is not None:
        sessions = csv_frame(calendar_content)
        if list(sessions) != ["date"] or sessions.empty or not sessions.date.str.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}").all():
            raise ValueError("The session calendar requires one date column in YYYY-MM-DD format.")
        try:
            index = pd.DatetimeIndex(pd.to_datetime(sessions.date, format="%Y-%m-%d")).sort_values()
        except ValueError as error:
            raise ValueError("The session calendar contains an invalid date.") from error
        if not index.is_unique or (index.dayofweek >= 5).any():
            raise ValueError("The session calendar requires distinct weekday trading-session labels.")
        calendar, label = tuple(index), "Uploaded exchange sessions"
    return make_dataset(csv_frame(content), filename, product, calendar, label)


def eia_archive():
    frame = pd.read_csv(EIA_PATH, parse_dates=["date", "expiry"])
    return make_dataset(frame, "EIA WTI settlement archive", "CL", frame.date.unique(), "EIA reported sessions")


def diagnostics(df):
    dates = pd.DatetimeIndex(df.date.unique()).sort_values()
    absent = pd.bdate_range(dates[0], dates[-1]).difference(dates)
    contract_gaps = []
    for contract, group in df.groupby("contract", sort=False):
        first, last = group.date.min(), group.date.max()
        missing = dates[(dates >= first) & (dates <= last)].difference(group.date)
        contract_gaps.extend((contract, d) for d in missing)
    return {
        "rows": len(df),
        "sessions": len(dates),
        "contracts": df.contract.nunique(),
        "weekday_gaps": absent,
        "contract_gaps": pd.DataFrame(contract_gaps, columns=["contract", "date"]),
    }


def demo_data(seed=42, periods=780):
    """Build overlapping, synthetic maturities with changing trend and curve regimes."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=periods)
    x = np.arange(periods)
    spot = 72 + 8 * np.sin(x / 75) + np.cumsum(rng.normal(0, 0.4, periods))
    curve = 0.95 * np.sin(x / 30) + 0.3 + rng.normal(0, 0.14, periods)
    expiries = pd.date_range(dates[0], dates[-1] + pd.Timedelta(days=150), freq="MS")
    rows = []
    for k, month in enumerate(expiries):
        expiry = pd.offsets.BDay().rollback(month + pd.Timedelta(days=19))
        previous_close = None
        for i, date in enumerate(dates):
            days = (expiry - date).days
            if not 0 <= days <= 150:
                continue
            close = round(spot[i] + curve[i] * days / 30 + 0.12 * np.sin(i / 7 + k), 2)
            reference = close if previous_close is None else previous_close
            opening = round(reference + rng.normal(0, 0.13), 2)
            high = round(max(opening, close) + rng.uniform(0.05, 0.5), 2)
            low = round(min(opening, close) - rng.uniform(0.05, 0.5), 2)
            volume = int(1_000 + 30_000 * np.exp(-((days - 24) / 28) ** 2) * rng.uniform(0.9, 1.1))
            rows.append((date, f"DEMO{k:03}", expiry, opening, high, low, close, volume))
            previous_close = close
    return pd.DataFrame(rows, columns=COLUMNS).sort_values(["date", "expiry"]).reset_index(drop=True)
