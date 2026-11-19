from datetime import UTC, datetime, timedelta
import sys
from threading import Event
from types import SimpleNamespace

import pytest

from src.live_market import DATASET, LiveMarket

DEFINITION = 0x13
BBO_1S = 0xC3
UNDEF_PRICE = (1 << 63) - 1
UNDEF_TIMESTAMP = (1 << 64) - 1
SCALE = 1_000_000_000
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def nanos(value):
    delta = value - EPOCH
    return (delta.days * 86_400 + delta.seconds) * SCALE + delta.microseconds * 1_000


def definition(now, code, instrument_id, asset, instrument_class, strike=0, underlying="", underlying_id=0, action="A"):
    return SimpleNamespace(
        rtype=DEFINITION, raw_symbol=code, instrument_id=instrument_id, asset=asset,
        expiration=nanos(now + timedelta(days=35)), instrument_class=instrument_class,
        strike_price=round(strike * SCALE), underlying=underlying, underlying_id=underlying_id,
        security_update_action=action,
    )


def bbo(now, instrument_id, bid, ask, *, flags=128, bid_size=4, ask_size=3):
    return SimpleNamespace(
        rtype=BBO_1S, instrument_id=instrument_id, ts_recv=nanos(now), ts_event=UNDEF_TIMESTAMP, flags=flags,
        bid_px_00=round(bid * SCALE) if bid is not None else UNDEF_PRICE,
        ask_px_00=round(ask * SCALE) if ask is not None else UNDEF_PRICE,
        bid_sz_00=bid_size, ask_sz_00=ask_size,
    )


class SessionFixture:
    """Deterministic stand-in for the SDK. It never connects to a provider."""

    def __init__(self, **options):
        self.options = options
        self.subscriptions = []
        self.opened = Event()
        self.closed = Event()

    def add_callback(self, record_callback, exception_callback):
        self.receive = record_callback
        self.fail_callback = exception_callback

    def add_reconnect_callback(self, reconnect_callback, exception_callback):
        self.reconnect = reconnect_callback

    def subscribe(self, **request):
        self.subscriptions.append(request)

    def is_connected(self):
        return bool(self.subscriptions) and not self.closed.is_set()

    def start(self):
        pass

    def block_for_close(self, timeout):
        self.opened.set()
        assert self.closed.wait(5)

    def terminate(self):
        self.closed.set()


@pytest.fixture
def live_fixture(monkeypatch):
    sessions = []
    built = Event()

    def factory(**kwargs):
        session = SessionFixture(**kwargs)
        sessions.append(session)
        built.set()
        return session

    monkeypatch.setitem(sys.modules, "databento", SimpleNamespace(Live=factory))
    markets = []

    def open_session(product="CL", futures_symbols=()):
        market = LiveMarket("test-key", product, futures_symbols)
        markets.append(market)
        built.clear()
        assert market.start()
        assert not market.start()
        assert built.wait(2)
        session = sessions[-1]
        assert session.opened.wait(2)
        return market, session

    yield open_session
    for market in markets:
        market.stop()
        market._worker.join(timeout=2)


def test_unconfigured_market_does_not_start(monkeypatch):
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    market = LiveMarket()
    assert not market.start()
    assert not market.stop()
    snapshot = market.snapshot()
    assert snapshot.status == "setup_required"
    assert not snapshot.futures and not snapshot.options


def test_live_join_metadata_timestamps_and_derivative_sides(live_fixture):
    market, session = live_fixture()
    now = datetime.now(UTC) - timedelta(seconds=2)
    # Quote data can arrive earlier than definitions replay.
    session.receive(bbo(now, 1, 72.1, 72.2))
    session.receive(definition(now, "CLZ6", 1, "CL", "F"))
    session.receive(definition(now, "LOZ6 C7350", 2, "LO", "C", strike=73.5, underlying_id=1))
    session.receive(definition(now, "LOZ6 P6800", 3, "LO", "P", strike=68, underlying="CLZ6"))
    session.receive(definition(now, "LOZ6-LOF7", 4, "LO", "T"))
    session.receive(bbo(now, 2, 1.2, 1.25))
    session.receive(bbo(now, 3, 0.1, 0.12))
    session.receive(bbo(now - timedelta(seconds=3), 2, 5.0, 5.05))

    assert session.subscriptions == [
        {"dataset": DATASET, "schema": "definition", "stype_in": "parent", "symbols": ["CL.FUT", "LO.OPT"], "start": 0},
        {"dataset": DATASET, "schema": "bbo-1s", "stype_in": "parent", "symbols": ["CL.FUT", "LO.OPT"]},
    ]
    snapshot = market.snapshot(now=now + timedelta(seconds=1))
    assert snapshot.status == "live"
    assert snapshot.as_of == now
    fut, = snapshot.futures
    assert fut.timestamp == now
    assert fut.mid == pytest.approx(72.15)
    put, call = snapshot.options
    assert (put.right, put.strike, put.underlying_symbol) == ("P", 68, "CLZ6")
    assert (call.right, call.strike, call.underlying_symbol) == ("C", 73.5, "CLZ6")
    assert call.mid == pytest.approx(1.225)
    assert call.age_seconds == 1
    assert market.stop()
    market._worker.join(timeout=2)
    closed = market.snapshot(now=now)
    assert closed.status == "disconnected" and closed.futures[0].quote_status == "offline"
    assert closed.futures[0].mid is None


def test_micro_subscription_and_filtered_futures(live_fixture):
    market, session = live_fixture("MCL", " mclz6, MCLZ6 ")
    now = datetime.now(UTC) - timedelta(seconds=2)
    session.receive(definition(now, "MCLH7", 1, "MCL", "F"))
    session.receive(definition(now, "MCLZ6", 2, "MCL", "F"))
    session.receive(definition(now, "MCOZ6 P6800", 3, "MCO", "P", strike=68, underlying="MCLZ6"))
    session.receive(definition(now, "MCOZ6 C6800", 4, "MCO", "C", strike=68, underlying="MCLZ6", action="D"))
    snapshot = market.snapshot(now=now)
    assert [row.symbol for row in snapshot.futures] == ["MCLZ6"]
    assert [row.symbol for row in snapshot.options] == ["MCOZ6 P6800"]
    assert session.subscriptions[0]["symbols"] == ["MCL.FUT", "MCO.OPT"]
    assert session.subscriptions[1]["stype_in"] == "raw_symbol"
    assert session.subscriptions[1]["symbols"] == ("MCLZ6",)
    assert session.subscriptions[2]["schema"] == "bbo-1s"
    with pytest.raises(ValueError, match="dated futures"):
        LiveMarket("", "MCL", ["CLZ6"])


def test_bad_quotes_and_reconnect_fail_closed(live_fixture):
    market, session = live_fixture()
    now = datetime.now(UTC) - timedelta(seconds=2)
    session.receive(definition(now, "CLZ6", 1, "CL", "F"))

    for bid, ask, flags, size, status in [
        (-36, -35, 128, 4, "fresh"),
        (100, 99, 128, 4, "crossed"),
        (None, 72.4, 128, 4, "one_sided"),
        (72.2, 72.4, 128, 0, "one_sided"),
        (72.2, 72.4, 128 | 4, 4, "quality_flag"),
    ]:
        session.receive(bbo(now, 1, bid, ask, flags=flags, bid_size=size))
        quote, = market.snapshot(now=now).futures
        assert quote.quote_status == status
        assert (quote.mid is not None) == (status == "fresh")

    session.receive(bbo(now, 1, 72.2, 72.4))
    quote, = market.snapshot(stale_after=3, now=now + timedelta(seconds=4)).futures
    assert quote.quote_status == "stale"
    session.reconnect(None, None)
    snapshot = market.snapshot(now=now + timedelta(seconds=1))
    assert snapshot.status == "connecting"
    assert snapshot.futures[0].quote_status == "awaiting_quote"


def test_gateway_error_never_prints_credentials(monkeypatch):
    secret = "fixture-secret"

    class FailingSession(SessionFixture):
        def subscribe(self, **request):
            raise RuntimeError(f"private details: {secret}")

    monkeypatch.setitem(sys.modules, "databento", SimpleNamespace(Live=FailingSession))
    market = LiveMarket(secret)
    assert market.start()
    market._worker.join(timeout=2)
    snapshot = market.snapshot()
    assert snapshot.status == "error"
    assert secret not in snapshot.message
