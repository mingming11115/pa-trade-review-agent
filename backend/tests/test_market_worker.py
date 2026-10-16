import asyncio
from types import SimpleNamespace
import pytest


@pytest.mark.asyncio
async def test_worker_once_executes_one_round_and_disposes(monkeypatch):
    from app.market import worker
    events=[]
    class Collector:
        def __init__(self,settings):pass
        async def run_once(self):events.append('once')
        async def run_forever(self):events.append('loop')
        def stop(self):events.append('stop')
    async def dispose():events.append('dispose')
    monkeypatch.setattr(worker,'MinuteCollector',Collector)
    monkeypatch.setattr(worker,'get_settings',lambda:SimpleNamespace(collector_enabled=True,collector_mode='external',provider_configured=True))
    monkeypatch.setattr(worker,'engine',SimpleNamespace(dispose=dispose))
    await worker.run_worker(once=True)
    assert events==['once','dispose']


@pytest.mark.asyncio
async def test_worker_rejects_disabled_or_missing_configuration(monkeypatch):
    from app.market import worker
    monkeypatch.setattr(worker,'get_settings',lambda:SimpleNamespace(collector_enabled=False,provider_configured=True))
    with pytest.raises(ValueError,match='disabled'):
        await worker.run_worker(once=True)


@pytest.mark.asyncio
async def test_worker_signal_cancels_active_collection_and_disposes(monkeypatch):
    from app.market import worker
    events=[]
    callbacks={}
    loop=asyncio.get_running_loop()
    monkeypatch.setattr(loop,'add_signal_handler',lambda signum,callback:callbacks.setdefault(signum,callback))
    monkeypatch.setattr(loop,'remove_signal_handler',lambda signum:callbacks.pop(signum,None))
    class Collector:
        def __init__(self,settings):pass
        async def run_forever(self):
            try:
                events.append('running')
                callbacks[worker.signal.SIGTERM]()
                await asyncio.Event().wait()
            finally:events.append('cancelled')
        def stop(self):events.append('stop')
    async def dispose():events.append('dispose')
    monkeypatch.setattr(worker,'MinuteCollector',Collector)
    monkeypatch.setattr(worker,'get_settings',lambda:SimpleNamespace(collector_enabled=True,provider_configured=True,live_ws_enabled=False))
    monkeypatch.setattr(worker,'engine',SimpleNamespace(dispose=dispose))
    await asyncio.wait_for(worker.run_worker(),timeout=1)
    assert events==['running','stop','cancelled','dispose'] and callbacks=={}
