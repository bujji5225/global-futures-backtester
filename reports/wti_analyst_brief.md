# WTI calendar spread | Analyst brief

**Data** — EIA WTI settlement archive · EIA reported sessions · `4c7dacdb264d`

## Research question

A nearby-minus-deferred WTI spread that has moved far from its recent average may revert. Compare the net outcomes of entries signaled in contango and backwardation.

Compare synchronized maturities. Contango is nearby below deferred. Backwardation is nearby above deferred. Storage constraints and expiry pressure can change the relationship between the legs.

## Method

5 disjoint test windows from 2018-12-20 to 2023-12-21. Each uses 252 preceding development sessions and 252 test sessions. Frozen rules use a 20-session Z-score, entry 1.5, exit 0.25, and a 5-session buffer under the expiry buffer rule. The benchmark stays long nearby and short deferred under the same constraints. Each test starts with $100,000 and 1 contract(s) per leg. Commission is $2.5 per side. Base slippage is 1 tick(s), with 2 added in the stress run.

## Findings

- Net P&L summed across independent tests was **-$14,510** for spread reversion, **-$11,460** for the benchmark, and **-$21,870** under higher slippage.
- The base case completed **92** spreads. Best window 1 returned $20. Weakest window 2 returned -$8,190. The deepest single-window drawdown was -10.2%.
- Backwardation produced 58 completed spreads at -$106 average net P&L per spread.
- Contango produced 34 completed spreads at -$245 average net P&L per spread.
- Worst base-case spread, CLK20, CLM20, entered 2020-03-13, exited 2020-04-14, and returned -$6,860 after $50 in costs.

## Decision and limitations

Compare consistency across windows, trade counts by regime, and sensitivity to execution before trusting a signal. The regime summaries describe entries in each state. Sample sizes and exposure can differ.

The source provides daily published settlements. Fills use the next settlement as a proxy. Intraday prices, traded volume, and executable bid/ask spreads are unavailable.

[EIA NYMEX futures data and methodology](https://www.eia.gov/dnav/pet/PET_PRI_FUT_S1_W.htm)

## Analyst decision

I would stay flat on this rule under these assumptions. Four of five test windows lost money, the only positive window earned $20, and the base case lagged the rolling spread benchmark. The weakest individual trade was a long May/short June position. The roll on April 14 closed it before May settled below zero on April 20, yet the trade still lost $6,860. I would revisit the thesis after testing a pre-specified expiry or inventory filter against a fresh sample with executable quotes.
