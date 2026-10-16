from datetime import datetime
from decimal import Decimal
from typing import Any
from app.replay.instruments import get_instrument


def build_trade_events(trades: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    events = []
    multiplier = get_instrument(symbol).multiplier
    for trade in trades:
        common = {k: trade[k] for k in ['id', 'direction', 'size', 'contract_name']}
        events.append(dict(**common, type='entry', timestamp=trade['entered_at'], price=str(trade['entry_price'])))
        reported = trade.get('reported_pnl')
        pnl = Decimal(str(reported)) if reported is not None else (
            (Decimal(str(trade['exit_price']))-Decimal(str(trade['entry_price']))) * Decimal(str(trade['size'])) *
            multiplier * (1 if trade['direction']=='long' else -1) - Decimal(str(trade.get('fees') or 0)) - Decimal(str(trade.get('commissions') or 0)))
        events.append(dict(**common, type='exit', timestamp=trade['exited_at'], price=str(trade['exit_price']), pnl=str(pnl), estimated=reported is None))
    return sorted(events, key=lambda e: (datetime.fromisoformat(e['timestamp']), e['id'], e['type'] == 'exit'))


def historical_state(events: list[dict[str, Any]], cursor: datetime, price: str | None, symbol: str) -> dict[str, Any]:
    visible = [e for e in events if datetime.fromisoformat(e['timestamp']) <= cursor]
    opened: dict[str, dict[str, Any]] = {}
    realized = Decimal(0)
    for event in visible:
        if event['type'] == 'entry':
            opened[event['id']] = event
        else:
            opened.pop(event['id'], None)
            realized += Decimal(event['pnl'])
    quantity = sum((Decimal(e['size']) * (1 if e['direction']=='long' else -1) for e in opened.values()), Decimal(0))
    unrealized = sum(((Decimal(price)-Decimal(e['price'])) * Decimal(e['size']) * (1 if e['direction']=='long' else -1) * get_instrument(symbol).multiplier for e in opened.values()), Decimal(0)) if price is not None else Decimal(0)
    return dict(events=visible, quantity=str(quantity), realized_pnl=str(realized), unrealized_pnl=str(unrealized), open_trades=list(opened.values()))
