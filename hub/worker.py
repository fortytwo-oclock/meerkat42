"""Single background-worker process for a horizontally scaled Hub deployment."""
from __future__ import annotations

import asyncio
import logging
import os
import signal

from hub.billing.delivery import delivery_loop
from hub.config import get_settings
from hub.notify.dispatcher import dispatch_loop
from hub.services.liveness import rollup_loop, watchdog_loop
from hub.timeseries import SqlMetricStore
from hub.ws.bus import bus

log = logging.getLogger("hub.worker")


async def run() -> None:
    settings = get_settings()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for name in ("SIGINT", "SIGTERM"):
        signal_value = getattr(signal, name, None)
        if signal_value is not None:
            try:
                loop.add_signal_handler(signal_value, stop.set)
            except NotImplementedError:  # pragma: no cover - Windows local run
                pass
    await bus.start(
        settings.redis_url,
        prefix=settings.redis_prefix,
        lease_seconds=settings.agent_lease_seconds,
        refresh_seconds=settings.agent_lease_refresh_seconds,
    )
    tasks = [
        asyncio.create_task(watchdog_loop(stop)),
        asyncio.create_task(rollup_loop(SqlMetricStore(), stop)),
        asyncio.create_task(dispatch_loop(stop)),
        asyncio.create_task(delivery_loop(stop)),
    ]
    log.info("background worker ready")
    try:
        await stop.wait()
    finally:
        stop.set()
        await asyncio.gather(*tasks, return_exceptions=True)
        await bus.stop()


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper())
    asyncio.run(run())
