# WTI futures research report

- **Product** — CL
- **Data** — Synthetic demonstration · WTI demo · seed 42
- **Dataset SHA-256** — `bd072943e826357ac837d323d5f921772d501bb04f7c6d001b7e0238a8a1070b`
- **Development** — 2023-01-02 to 2025-02-03
- **Holdout** — 2025-02-04 to 2025-12-26

## Hypothesis

- **Momentum** — A crossover of short and long moving averages can capture directional persistence in the selected maturity.
- **Mean reversion** — Large deviations from a contract's recent mean can revert before transaction costs absorb the move.
- **Calendar spread** — Large deviations in the nearby-minus-deferred price spread can revert across synchronized maturities.

## Methodology

Strategies use information through each session close. Targets execute at the next available open. Both periods start flat with the same initial capital and can use earlier observations to warm up indicators. Any open position is liquidated at the final session close of each period.

Contract selection uses a weekday expiry buffer and, when enabled, prior-session volume. Quotes and dollar P&L remain separate by maturity. Every leg pays commission and modeled tick slippage on both sides. A calendar trade groups the two opposite legs for win-rate and profit-factor calculations.

### Momentum

SMA 10/40. 1 contract(s) per leg, 1000 units per contract, $0.01 tick, $2.5 commission and 1 tick(s) slippage per side. Starting equity $100,000.00, assumed margin $6,000.00 per contract, 3× gross leverage cap and 25.0% drawdown halt. Roll rule expiry buffer with a 5-weekday expiry buffer.

| Metric | Development | Holdout |
| --- | ---: | ---: |
| Net P&L | $16,355.00 | $22,020.00 |
| Return | 16.4% | 22.0% |
| Sharpe | 1.20 | 3.38 |
| Annualized volatility | 6.0% | 6.4% |
| Max drawdown | -5.2% | -2.8% |
| Max drawdown (USD) | -$5,995.00 | -$3,555.00 |
| Trades | 37 | 14 |
| Win rate | 51.4% | 71.4% |
| Profit factor | 2.01 | 7.29 |
| Average win | $1,711.84 | $2,552.00 |
| Average loss | -$898.33 | -$875.00 |
| Costs | $925.00 | $350.00 |
| Exposure | 92.7% | 100.0% |
| Average gross notional | $65,148.72 | $66,710.68 |
| Peak gross notional | $81,040.00 | $76,950.00 |
| Risk-blocked sessions | 0 | 0 |

### Mean reversion

Z-score window 20, entry 1.5, exit 0.25. 1 contract(s) per leg, 1000 units per contract, $0.01 tick, $2.5 commission and 1 tick(s) slippage per side. Starting equity $100,000.00, assumed margin $6,000.00 per contract, 3× gross leverage cap and 25.0% drawdown halt. Roll rule expiry buffer with a 5-weekday expiry buffer.

| Metric | Development | Holdout |
| --- | ---: | ---: |
| Net P&L | -$12,770.00 | -$16,470.00 |
| Return | -12.8% | -16.5% |
| Sharpe | -1.02 | -2.80 |
| Annualized volatility | 6.0% | 6.8% |
| Max drawdown | -15.7% | -20.2% |
| Max drawdown (USD) | -$15,930.00 | -$20,365.00 |
| Trades | 40 | 14 |
| Win rate | 55.0% | 42.9% |
| Profit factor | 0.60 | 0.19 |
| Average win | $870.91 | $660.00 |
| Average loss | -$1,773.89 | -$2,553.75 |
| Costs | $1,000.00 | $350.00 |
| Exposure | 66.8% | 74.4% |
| Average gross notional | $47,134.58 | $49,502.56 |
| Peak gross notional | $81,040.00 | $76,950.00 |
| Risk-blocked sessions | 0 | 0 |

### Calendar spread

Z-score window 20, entry 1.5, exit 0.25. 1 contract(s) per leg, 1000 units per contract, $0.01 tick, $2.5 commission and 1 tick(s) slippage per side. Starting equity $100,000.00, assumed margin $6,000.00 per contract, 3× gross leverage cap and 25.0% drawdown halt. Roll rule expiry buffer with a 5-weekday expiry buffer.

| Metric | Development | Holdout |
| --- | ---: | ---: |
| Net P&L | -$210.00 | -$580.00 |
| Return | -0.2% | -0.6% |
| Sharpe | -0.02 | -0.17 |
| Annualized volatility | 2.9% | 3.3% |
| Max drawdown | -3.9% | -2.2% |
| Max drawdown (USD) | -$4,010.00 | -$2,245.00 |
| Trades | 54 | 24 |
| Win rate | 53.7% | 54.2% |
| Profit factor | 0.98 | 0.90 |
| Average win | $297.24 | $416.92 |
| Average loss | -$353.20 | -$545.45 |
| Costs | $2,700.00 | $1,200.00 |
| Exposure | 71.2% | 69.2% |
| Average gross notional | $101,284.36 | $93,417.09 |
| Peak gross notional | $163,390.00 | $154,340.00 |
| Risk-blocked sessions | 0 | 0 |

## Risk analysis

Sharpe and account volatility use daily equity returns and 252 sessions per year, with zero risk-free return. Drawdown uses closing equity and the starting capital as the initial high-water mark. Gross notional sums absolute leg exposures, and the margin check assumes no spread offsets.

The execution model uses daily opens and closes, fixed slippage, and weekday roll buffers. Holiday gaps and contract-specific liquidity require review against the data provider's calendar. Signal fills and margin constraints are simulated.

## Conclusions

- **Momentum** recorded $22,020.00 holdout net P&L, -2.8% maximum drawdown, and 14 completed trade(s).
- **Mean reversion** recorded -$16,470.00 holdout net P&L, -20.2% maximum drawdown, and 14 completed trade(s).
- **Calendar spread** recorded -$580.00 holdout net P&L, -2.2% maximum drawdown, and 24 completed trade(s).

The dataset uses generated prices and volumes. These outcomes illustrate the simulation workflow.
