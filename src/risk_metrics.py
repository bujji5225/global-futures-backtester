"""Account performance and completed-position metrics."""
import numpy as np
import pandas as pd


def metrics(daily, round_trips, capital):
    equity = daily.equity
    previous = equity.shift(fill_value=capital)
    returns = equity / previous - 1 if (previous > 0).all() else pd.Series(dtype=float)
    std = returns.std()
    wins = round_trips.loc[round_trips.net_pnl > 0, "net_pnl"]
    losses = round_trips.loc[round_trips.net_pnl < 0, "net_pnl"]
    factor = float(wins.sum() / -losses.sum()) if len(losses) else np.inf if len(wins) else None
    return {
        "Net P&L": float(equity.iloc[-1] - capital),
        "Return": float(equity.iloc[-1] / capital - 1),
        "Sharpe": float(returns.mean() / std * np.sqrt(252)) if std > 0 else None,
        "Annualized volatility": float(std * np.sqrt(252)) if pd.notna(std) else None,
        "Max drawdown": float(daily.drawdown.min()),
        "Max drawdown (USD)": float(daily.drawdown_usd.min()),
        "Trades": len(round_trips),
        "Win rate": len(wins) / len(round_trips) if len(round_trips) else None,
        "Profit factor": factor,
        "Average win": float(wins.mean()) if len(wins) else None,
        "Average loss": float(losses.mean()) if len(losses) else None,
        "Costs": float(daily.cost.sum()),
        "Exposure": float(daily.exposed.mean()),
        "Average gross notional": float(daily.gross_exposure.mean()),
        "Peak gross notional": float(daily.gross_exposure.max()),
        "Risk-blocked sessions": int(daily.risk_blocked.sum()),
    }
