"""Read-only Databento BBO for dated NYMEX WTI futures and futures options."""
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
import os
import re
from threading import Event, Lock, Thread
from time import monotonic

DATASET = "GLBX.MDP3"
SOURCE = f"Databento {DATASET}"
PRODUCTS = {"CL": "LO", "MCL": "MCO"}
QUOTE_SCHEMA = "bbo-1s"
# DBN record codes and fixed-point scale are stable across SDK upgrades.
_DEFINITION = 0x13
_ERROR = 0x15
_SYSTEM = 0x17
_BBO_1S = 0xC3
_UNDEF_PRICE = (1 << 63) - 1
_UNDEF_TIMESTAMP = (1 << 64) - 1
_UNDEF_ID = (1 << 32) - 1
_PRICE_SCALE = 1_000_000_000
_BAD_QUOTE_FLAGS = (1 << 3) | (1 << 2)
_BOOK_LIMIT = 36_000
_PENDING_LIMIT = 3_000
_HEARTBEAT_SECONDS = 5
_CONTACT_TIMEOUT = 3 * _HEARTBEAT_SECONDS
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class _Definition:
    instrument_id: int
    symbol: str
    kind: str
    expiry: datetime
    right: str | None
    strike: float | None
    underlying_id: int | None
    underlying_symbol: str | None


@dataclass(frozen=True)
class _BBO:
    timestamp: datetime
    bid: float | None
    ask: float | None
    bid_size: int
    ask_size: int
    quality_flag: bool


@dataclass(frozen=True)
class QuoteRow:
    instrument_id: int
    symbol: str
    kind: str
    expiry: datetime
    right: str | None
    strike: float | None
    underlying_id: int | None
    underlying_symbol: str | None
    bid: float | None
    ask: float | None
    bid_size: int
    ask_size: int
    timestamp: datetime | None
    age_seconds: float | None
    quote_status: str

    @property
    def mid(self):
        if self.quote_status != "fresh" or self.bid is None or self.ask is None:
            return None
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class MarketSnapshot:
    status: str
    message: str
    as_of: datetime | None
    futures: tuple[QuoteRow, ...] = ()
    options: tuple[QuoteRow, ...] = ()
    source: str = SOURCE


def _text(value):
    if isinstance(value, bytes):
        value = value.decode("ascii", errors="replace")
    return value.strip("\0 ").upper() if isinstance(value, str) else ""


def _character(value):
    """Read either the DBN enum or the raw one-byte field."""
    if isinstance(value, str):
        return value.upper()
    if isinstance(value, bytes):
        return _text(value)
    if value is not None:
        try:
            return chr(int(value)).upper()
        except (TypeError, ValueError, OverflowError):
            return str(value).upper()
    return ""


def _timestamp(nanoseconds):
    if nanoseconds is None or not 0 < nanoseconds < _UNDEF_TIMESTAMP:
        return None
    return _EPOCH + timedelta(microseconds=nanoseconds // 1_000)


def _price(raw):
    if raw is None or raw == _UNDEF_PRICE:
        return None
    return raw / _PRICE_SCALE


def _level_size(raw):
    return raw if raw is not None and 0 < raw < _UNDEF_ID else 0


class _QuoteBook:
    """Retain only supported outright definitions and the latest book per instrument."""

    def __init__(self, product, futures_symbols):
        self._product = product
        self._option_product = PRODUCTS[product]
        self._future_pattern = re.compile(rf"{product}[FGHJKMNQUVXZ][0-9]{{1,4}}")
        self._futures_symbols = frozenset(futures_symbols)
        self._instruments = {}
        self._quotes = {}
        self._pending = OrderedDict()
        self._lock = Lock()

    def on_record(self, record, rtype):
        with self._lock:
            if rtype == _DEFINITION:
                self._definition(record)
            elif rtype == _BBO_1S:
                self._quote(record)

    def clear_quotes(self):
        with self._lock:
            self._quotes.clear()
            self._pending.clear()

    def _definition(self, record):
        instrument_id = int(record.instrument_id)
        if _character(record.security_update_action) in {"D", "DELETE"}:
            self._instruments.pop(instrument_id, None)
            self._quotes.pop(instrument_id, None)
            self._pending.pop(instrument_id, None)
            return

        symbol = _text(record.raw_symbol)
        asset = _text(record.asset)
        instrument_class = _character(record.instrument_class)
        expiry = _timestamp(record.expiration)
        if not symbol or expiry is None:
            return

        right = strike = underlying_id = underlying_symbol = None
        if instrument_class in {"F", "FUTURE"} and asset == self._product and self._future_pattern.fullmatch(symbol):
            kind = "future"
        elif instrument_class in {"C", "CALL", "P", "PUT"} and asset == self._option_product:
            kind = "option"
            right, strike = instrument_class[0], _price(record.strike_price)
            if strike is None:
                return
            raw_underlying_id = record.underlying_id
            underlying_id = raw_underlying_id if 0 < raw_underlying_id < _UNDEF_ID else None
            underlying_symbol = _text(record.underlying)
            if not self._future_pattern.fullmatch(underlying_symbol):
                underlying_symbol = None
        else:
            self._pending.pop(instrument_id, None)
            return

        if instrument_id not in self._instruments and len(self._instruments) >= _BOOK_LIMIT:
            return
        definition = _Definition(instrument_id, symbol, kind, expiry, right, strike, underlying_id, underlying_symbol)
        existing = self._instruments.get(instrument_id)
        if existing is not None and existing.symbol != symbol:
            self._quotes.pop(instrument_id, None)
        self._instruments[instrument_id] = definition
        pending = self._pending.pop(instrument_id, None)
        if pending is not None:
            self._quotes[instrument_id] = pending

    def _quote(self, record):
        # BBO-1s ts_event is the last TRADE time and can be undefined for valid quotes.
        timestamp = _timestamp(record.ts_recv)
        if timestamp is None:
            return

        # Current DBN exposes flat fields. Some existing clients expose only levels.
        if hasattr(record, "bid_px_00"):
            bid, ask = record.bid_px_00, record.ask_px_00
            bid_size, ask_size = record.bid_sz_00, record.ask_sz_00
        else:
            if not record.levels:
                return
            level = record.levels[0]
            bid, ask = level.bid_px, level.ask_px
            bid_size, ask_size = level.bid_sz, level.ask_sz
        instrument_id = int(record.instrument_id)
        book = self._quotes if instrument_id in self._instruments else self._pending
        quality_flag = bool(int(record.flags) & _BAD_QUOTE_FLAGS) or timestamp > datetime.now(UTC) + timedelta(seconds=2)
        prior = book.get(instrument_id)
        if prior is not None and not prior.quality_flag and not quality_flag and prior.timestamp > timestamp:
            return
        bid_size, ask_size = _level_size(bid_size), _level_size(ask_size)
        book[instrument_id] = _BBO(
            timestamp, _price(bid) if bid_size else None, _price(ask) if ask_size else None,
            bid_size, ask_size, quality_flag,
        )
        if book is self._pending:
            self._pending.move_to_end(instrument_id)
            if len(self._pending) > _PENDING_LIMIT:
                self._pending.popitem(last=False)

    def rows(self, now, stale_after, connected):
        with self._lock:
            instruments = self._instruments.copy()
            quotes = self._quotes.copy()
        futures, options = [], []
        for instrument_id, definition in instruments.items():
            if definition.expiry <= now:
                continue
            if definition.kind == "future" and self._futures_symbols and definition.symbol not in self._futures_symbols:
                continue
            book = quotes.get(instrument_id)
            age = (now - book.timestamp).total_seconds() if book else None
            if book is None:
                status = "awaiting_quote"
            elif not connected:
                status = "offline"
            elif book.quality_flag or age < -2:
                status = "quality_flag"
            elif age > stale_after:
                status = "stale"
            elif book.bid is None or book.ask is None:
                status = "one_sided"
            elif book.bid > book.ask:
                status = "crossed"
            elif definition.kind == "option" and book.bid < 0:
                status = "quality_flag"
            else:
                status = "fresh"
            underlying_symbol = definition.underlying_symbol
            underlying = instruments.get(definition.underlying_id)
            if underlying is not None and underlying.kind == "future":
                if underlying_symbol is not None and underlying_symbol != underlying.symbol:
                    status = "quality_flag"
                else:
                    underlying_symbol = underlying.symbol
            row = QuoteRow(
                instrument_id=instrument_id, symbol=definition.symbol, kind=definition.kind,
                expiry=definition.expiry, right=definition.right, strike=definition.strike,
                underlying_id=definition.underlying_id, underlying_symbol=underlying_symbol,
                bid=book.bid if book else None, ask=book.ask if book else None,
                bid_size=book.bid_size if book else 0, ask_size=book.ask_size if book else 0,
                timestamp=book.timestamp if book else None, age_seconds=max(0.0, age) if age is not None else None,
                quote_status=status,
            )
            (futures if definition.kind == "future" else options).append(row)
        futures.sort(key=lambda row: (row.expiry, row.symbol))
        options.sort(key=lambda row: (row.expiry, row.strike, row.right, row.symbol))
        return tuple(futures), tuple(options)


class LiveMarket:
    """One bounded read-only SDK session with explicitly invalidated offline quotes."""

    def __init__(self, api_key=None, product="CL", futures_symbols=()):
        if product not in PRODUCTS:
            raise ValueError("Select CL or MCL for NYMEX WTI data.")
        if isinstance(futures_symbols, str):
            futures_symbols = futures_symbols.split(",")
        symbols = tuple(dict.fromkeys(s.strip().upper() for s in futures_symbols if s.strip()))
        pattern = rf"{product}[FGHJKMNQUVXZ][0-9]{{1,4}}"
        if len(symbols) > 24 or any(re.fullmatch(pattern, s) is None for s in symbols):
            raise ValueError("Specify up to 24 dated futures symbols for the selected product, such as CLZ6.")

        self.product = product
        self.futures_symbols = symbols
        self._key = (api_key if api_key is not None else os.getenv("DATABENTO_API_KEY", "")).strip()
        self._lock = Lock()
        self._worker = None
        self._client = None
        self._cancel = Event()
        self._book = _QuoteBook(product, symbols)
        self._last_contact = None
        self._warning = ""
        if self._key:
            self._status, self._message = "disconnected", "Start the real-time market-data connection."
        else:
            self._status = "setup_required"
            self._message = "Set DATABENTO_API_KEY in the app environment with a GLBX.MDP3 live entitlement."

    def start(self):
        """Start asynchronously; read connection progress with snapshot()."""
        with self._lock:
            if not self._key or (self._worker is not None and self._worker.is_alive()):
                return False
            self._cancel.clear()
            self._book = _QuoteBook(self.product, self.futures_symbols)
            self._status, self._message = "connecting", "Connecting and loading instrument definitions."
            self._last_contact, self._warning = None, ""
            self._worker = Thread(target=self._run, name=f"wti-databento-{self.product}", daemon=True)
            self._worker.start()
            return True

    def stop(self):
        """Invalidate quotes immediately and request producer shutdown."""
        with self._lock:
            if self._worker is None or not self._worker.is_alive() or self._cancel.is_set():
                return False
            self._cancel.set()
            self._status, self._message = "stopping", "Stopping the market-data connection."
            client = self._client
        if client is not None:
            self._terminate(client)
        return True

    def snapshot(self, stale_after=90, *, now=None):
        if not math.isfinite(stale_after) or stale_after <= 0:
            raise ValueError("The freshness cutoff must be positive and finite.")
        now = datetime.now(UTC) if now is None else now
        with self._lock:
            status, message, book = self._status, self._message, self._book
            last_contact, warning, client = self._last_contact, self._warning, self._client
        connected = status == "live" and client is not None and client.is_connected()
        healthy = connected and last_contact is not None and monotonic() - last_contact <= _CONTACT_TIMEOUT
        futures, options = book.rows(now, stale_after, healthy)
        as_of = max((q.timestamp for rows in (futures, options) for q in rows if q.timestamp), default=None)
        if status == "live":
            if not connected or (last_contact is not None and not healthy):
                status, message = "stale", "No recent feed contact. Pricing calculations are paused."
            elif as_of is None:
                status, message = "connecting", "Connected. Waiting for definitions and quote updates."
            elif not any(q.quote_status == "fresh" for rows in (futures, options) for q in rows):
                status, message = "stale", "Connected. No current two-sided quotes satisfy the freshness cutoff."
        return MarketSnapshot(status, f"{message} {warning}".strip(), as_of, futures, options)

    def _run(self):
        client = None
        status, message = "disconnected", "The market-data session ended. Start to reconnect."
        try:
            import databento

            client = databento.Live(
                key=self._key, heartbeat_interval_s=_HEARTBEAT_SECONDS, reconnect_policy="reconnect",
            )
            future_parent = f"{self.product}.FUT"
            option_parent = f"{PRODUCTS[self.product]}.OPT"
            client.add_callback(self._record, self._callback_error)
            client.add_reconnect_callback(self._reconnected, self._callback_error)
            client.subscribe(
                dataset=DATASET, schema="definition", stype_in="parent",
                symbols=[future_parent, option_parent], start=0,
            )
            with self._lock:
                self._client = client
            if self._cancel.is_set():
                return
            if self.futures_symbols:
                client.subscribe(dataset=DATASET, schema=QUOTE_SCHEMA, stype_in="raw_symbol", symbols=self.futures_symbols)
                symbols = [option_parent]
            else:
                symbols = [future_parent, option_parent]
            client.subscribe(dataset=DATASET, schema=QUOTE_SCHEMA, stype_in="parent", symbols=symbols)
            with self._lock:
                if self._cancel.is_set():
                    return
                client.start()
                self._last_contact = monotonic()
                self._status, self._message = "live", "Streaming one-second exchange BBO."
            client.block_for_close(timeout=None)
        except ImportError:
            status, message = "setup_required", "Install the Databento Python client to connect."
        except Exception:
            # SDK and gateway exceptions may contain request details. Use safe status text.
            status, message = "error", "Databento connection ended with an error. Check the API key, live entitlement, and network access."
        finally:
            if client is not None:
                self._terminate(client)
            with self._lock:
                self._client = None
                if self._cancel.is_set():
                    self._status, self._message = "disconnected", "The market-data connection is stopped."
                else:
                    self._status, self._message = status, message

    @staticmethod
    def _terminate(client):
        try:
            if client.is_connected():
                client.terminate()
        except Exception:
            # A session may have already closed during cancellation.
            pass

    def _record(self, record):
        with self._lock:
            if self._cancel.is_set():
                return
            self._last_contact = monotonic()
            rtype = int(record.rtype)
            if rtype == _ERROR:
                self._warning = "The provider reported a subscription error. Check the selected contracts and entitlement."
            elif rtype == _SYSTEM and int(record.code) == 2:
                self._warning = "The provider reported a slow feed. Check quote timestamps."
            book = self._book
        book.on_record(record, rtype)

    def _reconnected(self, _old, _new):
        with self._lock:
            self._book.clear_quotes()
            self._last_contact = monotonic()
            self._warning = ""

    def _callback_error(self, _exception):
        with self._lock:
            self._book.clear_quotes()
            self._warning = "A feed record could not be decoded. Waiting for new prices."
