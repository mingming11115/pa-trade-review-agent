"""Unified collection service: python -m app.market.worker [--once]."""
from __future__ import annotations
import argparse
import asyncio
import logging
import signal

from app.core.config import get_settings
from app.core.database import engine
from app.core.logging_context import TraceContextFilter
from app.market.service import MinuteCollector
from app.market.live import RealtimeTradeCollector


async def run_worker(*, once: bool = False) -> None:
    settings=get_settings()
    if not settings.collector_enabled:
        raise ValueError("Market collector is disabled")
    if not settings.provider_configured:
        raise ValueError("Historical provider is not configured")
    collector=MinuteCollector(settings)
    tasks=[]
    stop=asyncio.Event()
    loop=asyncio.get_running_loop()
    installed=[]
    try:
        if once:
            await collector.run_once()
            if getattr(collector,"last_round_errors",0):
                raise RuntimeError("Market collection failed; inspect persisted job states")
            return
        for signum in (signal.SIGTERM,signal.SIGINT):
            loop.add_signal_handler(signum,stop.set)
            installed.append(signum)
        tasks.append(asyncio.create_task(collector.run_forever(),name="pa-minute-collector"))
        live=None
        if getattr(settings,"live_ws_enabled",False):
            live=RealtimeTradeCollector(settings)
            tasks.append(asyncio.create_task(live.run_forever(),name="pa-live-collector"))
        waiter=asyncio.create_task(stop.wait(),name="pa-collector-stop")
        done,_=await asyncio.wait([*tasks,waiter],return_when=asyncio.FIRST_COMPLETED)
        if waiter not in done:
            for task in done:
                task.result()
        collector.stop()
        if live:
            live.stop()
        waiter.cancel()
        await asyncio.gather(waiter,return_exceptions=True)
    finally:
        for signum in installed:
            loop.remove_signal_handler(signum)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.wait_for(asyncio.gather(*tasks,return_exceptions=True),timeout=15)
        await engine.dispose()


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true')
    args=parser.parse_args()
    handler=logging.StreamHandler()
    handler.addFilter(TraceContextFilter())
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s trace_id=%(trace_id)s run_id=%(run_id)s %(name)s %(message)s'))
    logging.basicConfig(level=get_settings().log_level,handlers=[handler])
    try:
        asyncio.run(run_worker(once=args.once))
    except (ValueError,RuntimeError) as exc:
        logging.getLogger(__name__).error("worker stopped error=%s",exc)
        raise SystemExit(1) from exc


if __name__=='__main__':
    main()
