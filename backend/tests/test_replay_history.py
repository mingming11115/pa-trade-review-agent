from datetime import UTC, datetime, timedelta
from app.replay.history import build_trade_events, historical_state


def test_history_hides_future_and_preserves_reported_pnl():
    t = datetime(2025, 1, 1, tzinfo=UTC)
    trade = dict(id='1', entered_at=t.isoformat(), exited_at=(t+timedelta(minutes=2)).isoformat(),
                 entry_price='100', exit_price='102', direction='long', size='2',
                 reported_pnl='56', fees='1.44', commissions=None, contract_name='MNQH5')
    events = build_trade_events([trade], 'MNQ')
    state = historical_state(events, t+timedelta(minutes=1), '101', 'MNQ')
    assert len(state['events']) == 1
    assert 'pnl' not in state['events'][0]
    assert state['quantity'] == '2'
    result = historical_state(events, t+timedelta(minutes=3), '102', 'MNQ')
    assert result['realized_pnl'] == '56'
    assert result['quantity'] == '0'
