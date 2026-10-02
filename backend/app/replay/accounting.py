from decimal import Decimal
from app.replay.contracts import EngineState, InstrumentSpec, ReplayFill


def apply_fill(state: EngineState, fill: ReplayFill, instrument: InstrumentSpec) -> None:
    """Apply a fill to a private engine copy; never rounds internal money."""
    p, a = state.position, state.account
    signed = fill.quantity if fill.side == 'buy' else -fill.quantity
    if p.quantity == 0 or (p.quantity > 0) == (signed > 0):
        p.average_price = (p.average_price * abs(p.quantity) + fill.price * abs(signed)) / (abs(p.quantity) + abs(signed))
        p.entry_fees += fill.fee
    else:
        gross = (fill.price - p.average_price) * (1 if p.quantity > 0 else -1) * fill.quantity * instrument.multiplier
        entry_fee = p.entry_fees * Decimal(fill.quantity) / Decimal(abs(p.quantity))
        p.entry_fees -= entry_fee
        fill.realized_pnl = gross - entry_fee - fill.fee
        a.realized_pnl += gross
        a.cash += gross
        a.closed_trades += 1
        a.wins += int(fill.realized_pnl > 0)
    a.cash -= fill.fee
    a.fees += fill.fee
    p.quantity += signed
    if not p.quantity:
        p.average_price = Decimal('0')
        p.entry_fees = Decimal('0')
        p.stop_loss = p.take_profit = None


def mark_to_market(state: EngineState, price: Decimal, instrument: InstrumentSpec) -> None:
    a, p = state.account, state.position
    state.last_price = price
    a.unrealized_pnl = (price - p.average_price) * p.quantity * instrument.multiplier
    a.equity = a.cash + a.unrealized_pnl
    a.peak_equity = max(a.peak_equity, a.equity)
    a.max_drawdown = max(a.max_drawdown, a.peak_equity - a.equity)
