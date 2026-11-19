# Global Futures Trading Strategy & Backtesting Engine

A local Python research simulator for NYMEX WTI futures. It supports standard CL and micro MCL contract data and opens with a historical WTI settlement archive from the U.S. Energy Information Administration.

## Run locally

Use Python 3.11 or newer. Run these commands from the project directory.

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Open [the local dashboard](http://127.0.0.1:8501). This workspace was verified with the installed Anaconda Python. To use that installation directly, run `/opt/anaconda3/bin/python3 -m streamlit run app.py` from the project directory.

1. Start in **Market Explorer** to inspect individual maturities and data coverage.
2. Open **Calendar Spread Lab** to chart the May and June 2020 contracts. Run the walk-forward study, inspect an individual spread, and export the analyst brief and evidence.
3. In **Expiry Replay**, choose a May contract roll buffer and record decisions before revealing each next settlement. Compare early and late rolls after completing the replay.
4. Use **Strategy Builder** and **Backtest Results** for a single development/holdout run. **Strategy Comparison** runs all three strategies under the same settings.
5. In **Research Report**, download a single-run or comparison report and evidence ZIP, or archive the experiment locally in SQLite.

## Advanced futures and options

**Advanced Futures** evaluates two pre-specified strategies over the same chronological test windows as a simple SMA momentum benchmark. The comparison includes higher slippage, per-window drawdown, trade counts, exposure, and reproducible ZIP exports.

- **Donchian breakout** compares the latest close with the preceding high/low channel plus a trailing volatility buffer. A shorter opposite channel handles exits. Settlement-only history uses prior settlements and absolute daily price changes as proxies. [Channel rules published by CME](https://www.cmegroup.com/content/dam/cmegroup/education/files/jpm-momentum-strategies-2015-04-15.pdf)
- **Curve carry** scales the nearby-minus-deferred price difference to 30 calendar days between expiries. The directional model takes a position in the nearby contract when the curve slope meets the configured threshold. Dollar normalization also supports zero and negative futures prices. [Koijen et al., Carry](https://www.nber.org/papers/w19325)

**Options Lab** supports bull call spreads, bear put spreads, iron condors, and long straddles on WTI futures. Choose a live chain, an illustrative chain, or upload a single static quote snapshot. Buys use the ask, sells use the bid, and the payoff includes opening fees. The lab shows a dollar payoff curve, break-evens, and expiry scenarios through negative underlying prices. The expiry model excludes early exercise, subsequent underlying futures positions, and option portfolio margin. Older snapshots remain labeled static. [CME WTI products](https://www.cmegroup.com/markets/energy/wti-crude-oil-futures.html)

```csv
symbol,underlying,expiry,right,strike,bid,ask
SAMPLE-C70,CLG27,2027-01-15,C,70,2.22,2.34
SAMPLE-C72,CLG27,2027-01-15,C,72,1.29,1.42
```

Quotes use USD per barrel. Option symbols must be unique. Every strategy uses the same expiry and dated underlying across all legs. The rows above are examples.

## Connect real-time WTI market data

This workspace integrates the **Databento CME Globex MDP 3.0** feed for CL/MCL futures and standard LO/MCO options. Databento is a data-only path with documented Python streaming, dated instrument definitions, and bid/ask fields for both asset types. The published CME Standard plan is USD 199 per month. For research without a subscription, use the included EIA archive and options examples. CME also publishes delayed quotes in its browser for reference. [Databento coverage](https://databento.com/docs/knowledge-base/datasets/glbx-mdp3) · [Plan pricing and licensing](https://databento.com/pricing/) · [CME delayed quotes](https://www.cmegroup.com/market-data/browse-data/delayed-quotes.html)

1. Obtain Databento CME live entitlement and an API key from your provider account.
2. Install the optional live requirements and supply the key privately to the local app process.

   ```sh
   python -m pip install -r requirements-live.txt
   printf 'Databento API key: '
   read -rs DATABENTO_API_KEY
   printf '\n'
   export DATABENTO_API_KEY
   python -m streamlit run app.py
   ```

3. Choose CL or MCL and open **Live Markets**, then select **Connect feed**. **Options Lab → Live feed** can reuse the same connection for that product. Use **Stop feed** to stop consumption.

The provider delivers one-second bid/ask snapshots and the dashboard refreshes every five seconds. Quotes retain their provider timestamps. Live calculations require fresh two-sided quotes, and multi-leg pricing requires quote timestamps within 12 seconds and sufficient displayed size at the modeled price. **Live curve carry** uses two dated futures. **Live Donchian breakout** compares the midpoint with a prior-session channel and requires recent completed bars for the same dated contract. Daily backtests continue to use the selected historical dataset. This is a local research application with read-only market data.

## Historical data

**EIA settlement archive** is the default CL dataset. It contains four successive delivery months across 1,583 dated sessions from January 2, 2018 through April 5, 2024. The archive publishes daily settlements. Its backtests mark positions and proxy execution at the next settlement. Opening prices and volume are unavailable in this mode.

Contract symbols and last trading dates are reconstructed from the published rank series and the NYMEX expiry rule, using EIA reported sessions as the holiday proxy. The final deferred expiries extend beyond the EIA publication cutoff and use weekday projections. CME clearing notices independently confirm the May and June 2020 expiries used in the replay. Use exchange-published schedules to check any date-sensitive strategy. [EIA source table](https://www.eia.gov/dnav/pet/PET_PRI_FUT_S1_W.htm) · [EIA delivery-rank definitions](https://www.eia.gov/dnav/pet/TblDefs/pet_pri_fut_tbldef2.asp) · [NYMEX Chapter 200](https://www.cmegroup.com/content/dam/cmegroup/rulebook/NYMEX/2/200.pdf) · [May 2020 CME advisory](https://www.cmegroup.com/notices/clearing/2020/03/Chadv20-098.pdf) · [June 2020 CME advisory](https://www.cmegroup.com/notices/clearing/2020/04/Chadv20-161Revised.pdf)

Rebuild the archive from source with `python scripts/build_eia_archive.py`. It can also accept four local EIA daily HTML downloads in rank order.

### Upload exchange or vendor data

Select **CL** or **MCL** in the sidebar. Choose **Contract CSV** and upload a UTF-8 file with one row per trading date and contract. A licensed historical contract-level export from [CME DataMine](https://www.cmegroup.com/datamine.html) or another authorized vendor can be converted to this schema.

```csv
date,contract,expiry,open,high,low,close,volume
2025-10-01,CLZ25,2025-11-20,62.50,63.10,62.20,62.91,126300
2025-10-01,CLF26,2025-12-19,62.78,63.35,62.46,63.12,54100
2025-10-02,CLZ25,2025-11-20,62.84,63.41,62.56,63.29,129803
2025-10-02,CLF26,2025-12-19,63.06,63.62,62.80,63.47,55800
```

The rows above illustrate formatting. Use verified dates and quotes in research. `date` is the trading session label and `expiry` is the contract's last trading date. Both use `YYYY-MM-DD`. Use complete outright identifiers such as `CLZ25` or `MCLF2026`, with a single product and a unique expiry per contract. WTI prices are in USD per barrel. Negative prices are accepted. Volume is a count of contracts.

For settlement-only uploads use `date,contract,expiry,settlement`. The optional exchange sessions CSV has one column named `date`, with every scheduled trading session in chronological scope. Include the period before the first observation through the relevant contract expiries, including sessions when your quote file is missing a bar. Roll buffers count these sessions when available and use weekdays outside this calendar's coverage. The **Synthetic demo** is available for both products.

The importer rejects duplicate keys, malformed dates, mixed products, blank required values, invalid OHLC ranges, and inconsistent expiries. It sorts out-of-order dates and displays missing weekdays and gaps within individual histories. An absent weekday may be an exchange holiday. The simulator keeps prices unfilled and raises an error if an open position cannot be marked or exited safely. Overlapping maturities are needed for spread research and realistic rolls.

| Product | Contract size | Tick | Value per tick | Settlement |
| --- | ---: | ---: | ---: | --- |
| CL | 1,000 barrels | $0.01 | $10 | Physical |
| MCL | 100 barrels | $0.01 | $1 | Financial |

See [CME's CL and MCL contract comparison](https://www.cmegroup.com/education/articles-and-reports/micro-wti-crude-oil-futures-faq) and [CME's guide to expiration and rolls](https://www.cmegroup.com/education/courses/introduction-to-futures/understanding-futures-expiration-contract-roll).

## How the simulation works

- **Momentum** compares trailing fast and slow SMAs for the selected maturity. **Mean reversion** uses the trailing Z-score of that contract's close. **Calendar spread** uses the Z-score of nearby minus deferred prices on synchronized sessions and takes opposite positions in the two contracts.
- For OHLCV, close-based signals can first affect positions at the next observed session's open. The engine marks held legs from their last close to the new open, applies fills and costs, then marks open legs to the session close. Settlement-based signals first execute at the following settlement under the proxy model. Each leg's gross P&L is the signed quantity times the change in its own price times the contract multiplier.
- Rolls respect the configured session buffer before expiry. The optional OHLCV volume rule can advance to a later maturity when its prior-session volume exceeds the nearer contract's. Holds and fills always retain contract identity. For spread pair changes, the simulated account closes the full existing spread before opening a new one.
- Commission and tick slippage are charged on every contract side. Opening checks apply user-assumed per-contract margin and a gross leverage cap to equity after estimated execution costs. Calendar spread margins use both legs without offsets. A drawdown breach at close triggers exit at the next observed open. A breach at open triggers an exit at that open. The drawdown halt ends trading for that period.
- The dashboard defaults to a chronological 70/30 development/holdout split. Each segment starts with fresh capital and flat positions. Indicators and roll selection can use earlier sessions for warm-up. Fix parameters using the development period before interpreting the holdout. The split performs one backtest per segment.
- Sharpe and volatility use daily equity returns, 252 sessions per year, and zero risk-free return. Drawdown uses closing equity and includes starting equity. Win rate and profit factor use net completed positions, grouping both calendar legs. Daily exposure shows positions held during the session, including exposure on the final session before planned liquidation.
- Execution uses assumed slippage and margin. Historical margin schedules, intraday order books, and executable prices require additional data and modeling.

## Interview walkthrough

1. **Curve** — Open Calendar Spread Lab on CLK20 / CLM20. Explain why the legs had very different settlement prices in April 2020, and how a long nearby/short deferred spread responds when nearby falls faster. [CFTC market reconstruction](https://www.cftc.gov/media/5296/InterimStaffReportNYMEX_WTICrudeOil/download)
2. **Trade** — Run the default five-window study and open Trade walkthrough. It defaults to the weakest base-case spread. Read the signal date, contract signs, and P&L after costs. Compare that exit with the 1-session and 5-session policies in Expiry Replay.
3. **Evidence** — Show the walk-forward table and the analyst brief. Each test starts with fresh capital and rules fixed across windows. Development and test dates are chronological, the benchmark stays long the same rolling spread, and the stress case adds two ticks per contract side. Regime statistics use the spread observed on the entry signal date. The independent window P&Ls are summed rather than compounded.

## Project layout

| Path | Role |
| --- | --- |
| `app.py`, `src/dashboard_research.py` | Streamlit workspace, curve research, and historical replay |
| `src/data.py` | CSV import, validation, diagnostics, and seeded synthetic data |
| `src/contract_specs.py` | CL and MCL conventions |
| `src/strategies.py`, `src/rollover.py` | Signals and forward maturity selection |
| `src/engine.py`, `src/risk_metrics.py` | Fills, equity, round trips, and risk |
| `src/research.py`, `src/replay.py`, `src/reports.py` | Walk-forward tests, decision replay, exports, and SQLite archive |
| `tests/` | Calculation and validation tests |
| [`reports/sample_research_report.md`](reports/sample_research_report.md) | Reproducible synthetic CL comparison |
| [`reports/wti_analyst_brief.md`](reports/wti_analyst_brief.md) | Findings from five historical test windows |

The evidence ZIP holds the split, dataset fingerprint, exact settings, and daily, fill, trade, and rollover CSVs for each evaluation. The source dataset stays local. Saved ZIPs and reports are stored in `data/experiments.sqlite3`.

```sh
python -m pytest -q
```
