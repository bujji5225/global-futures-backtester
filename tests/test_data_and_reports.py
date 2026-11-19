from io import BytesIO
import json
from zipfile import ZipFile

import pandas as pd
import pytest

from src.data import COLUMNS, demo_data, diagnostics, load_csv, make_dataset, validate
from src.engine import Settings
from src.reports import Run, evidence_bundle, list_experiments, load_experiment, markdown, save_experiment
from src.research import chronological_split, evaluate
from src.strategies import Strategy


@pytest.fixture
def market():
    return pd.DataFrame([
        ("2020-04-20", "CLM20", "2020-05-19", -10, -8, -12, -11, 40),
        ("2020-04-22", "CLM20", "2020-05-19", 5, 7, 3, 4, 10),
    ], columns=COLUMNS)


@pytest.mark.parametrize("column,value,match", [
    ("date", "2020-40-20", "Invalid calendar date"),
    ("date", "2020-04-20T00:00:00", "must use YYYY-MM-DD"),
    ("high", -13, "OHLC"),
    ("volume", -1, "whole number"),
    ("volume", 0.5, "whole number"),
    ("contract", "CL=F", "outright"),
    ("expiry", "2020-04-19", "after its last trading"),
])
def test_invalid_values_rejected(market, column, value, match):
    market[column] = market[column].astype(object)
    market.loc[0, column] = value
    with pytest.raises(ValueError, match=match):
        validate(market)


def test_duplicate_date_contract_is_rejected(market):
    with pytest.raises(ValueError, match="Duplicate"):
        validate(pd.concat([market, market.iloc[:1]]))


def test_import_sorts_reports_gaps_and_preserves_negative_prices(market):
    data = load_csv(market.iloc[::-1].to_csv(index=False).encode(), "wti.csv", "CL")
    assert data.was_reordered and not data.synthetic
    assert data.frame.open.tolist() == [-10, 5]
    assert diagnostics(data.frame)["weekday_gaps"].tolist() == [pd.Timestamp("2020-04-21")]
    assert data.fingerprint == make_dataset(market, "same records").fingerprint
    with pytest.raises(ValueError, match="selected CL or MCL"):
        load_csv(market.to_csv(index=False).encode(), "wti.csv", "MCL")


def test_malformed_csv_width_is_rejected(market):
    csv = market.to_csv(index=False).strip() + ",unexpected\n"
    with pytest.raises(ValueError, match="fields"):
        load_csv(csv.encode(), "bad.csv", "CL")


def test_report_preserves_provenance_and_roundtrips_sqlite(tmp_path):
    dataset = make_dataset(demo_data(periods=60), "Seed 42")
    split = chronological_split(dataset.frame, dataset.frame.date.min(), dataset.frame.date.max())
    results = evaluate(dataset.frame, Strategy(), Settings(), split)
    run = Run("CL", dataset.label, dataset.fingerprint, dataset.synthetic, {"Momentum": results}, split)
    report = markdown(run, notes="Reviewed leg-level entries.")
    assert "Synthetic demonstration" in report
    assert "Reviewed leg-level entries." in report
    assert dataset.fingerprint in report
    payload = evidence_bundle(run, report)
    with ZipFile(BytesIO(payload)) as archive:
        assert "01-momentum/holdout/trades.csv" in archive.namelist()
        assert archive.read("research-report.md").decode() == report
        metadata = json.loads(archive.read("experiment.json"))
        assert metadata["synthetic"]
        assert metadata["dataset_sha256"] == dataset.fingerprint
    path = tmp_path / "experiments.sqlite3"
    assert not list_experiments(path)
    identifier = save_experiment(run, report, payload, path)
    assert list_experiments(path)[0][0] == identifier
    assert load_experiment(identifier, path) == (report, payload)
