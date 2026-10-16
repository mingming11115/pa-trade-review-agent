from datetime import UTC, datetime, timedelta
from app.replay.contracts import MinuteSnapshot
from app.replay.market import visible_bars
import pytest
from app.core.errors import AppError


def test_aggregate_only_closed_minutes():
    t = datetime(2025, 1, 1, tzinfo=UTC)
    minutes = [MinuteSnapshot(timestamp=t + timedelta(minutes=i), open=100, high=100 + i, low=99, close=100) for i in range(5)]
    minutes[-1].high = 999
    bars = visible_bars(minutes, t + timedelta(minutes=2), '5m', 2000)
    assert len(bars) == 1
    assert bars[0]['high'] == 101
    assert bars[0]['is_partial'] is True


@pytest.mark.anyio
async def test_empty_search_is_bounded_and_advances_windows():
    from app.replay.market import ReplayMarket
    calls = []
    class Empty(ReplayMarket):
        async def load(self, symbol, start, end):
            calls.append((start,end))
            return []
    t = datetime(2025,1,1,tzinfo=UTC)
    with pytest.raises(AppError):
        await Empty().next_minute('ES',t)
    assert len(calls)==5
    assert calls[-1][1]==t+timedelta(days=7)
    assert all(a[1]==b[0] for a,b in zip(calls,calls[1:]))


@pytest.mark.anyio
async def test_known_unconfirmed_minute_blocks_advance():
    from app.replay.market import ReplayMarket
    t=datetime(2025,1,1,tzinfo=UTC)
    class Provisional(ReplayMarket):
        async def load(self,symbol,start,end):
            return [MinuteSnapshot(timestamp=t,open=100,high=101,low=99,close=100,confirmed=False),
                    MinuteSnapshot(timestamp=t+timedelta(minutes=1),open=101,high=102,low=100,close=101)]
    with pytest.raises(AppError,match='未确认'):
        await Provisional().next_minute('ES',t)


@pytest.mark.anyio
async def test_next_minute_reads_local_single_minute_before_wider_search(monkeypatch):
    from app.replay.market import ReplayMarket
    from app.replay.contracts import MinuteSnapshot
    from datetime import UTC, datetime, timedelta
    cursor=datetime(2025,1,1,tzinfo=UTC)
    requests=[]
    async def load(self,symbol,start,end):
        requests.append((start,end))
        return [MinuteSnapshot(timestamp=start,open=100,high=101,low=99,close=100)]
    monkeypatch.setattr(ReplayMarket,'load',load)
    await ReplayMarket().next_minute('ES',cursor)
    assert requests==[(cursor,cursor+timedelta(minutes=1))]
