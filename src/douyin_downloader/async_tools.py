from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import suppress


async def run_in_thread_cancellation_safe[**P, T](
    function: Callable[P, T],
    *args: P.args,
    **kwargs: P.kwargs,
) -> T:
    """Wait for submitted thread work to finish before propagating cancellation."""
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:  # noqa: BLE001 - cancellation remains primary
                break
        if task.done() and not task.cancelled():
            with suppress(Exception):
                task.result()
        raise
