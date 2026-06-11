from __future__ import annotations

import asyncio
import threading

from prototype.v2.async_run import reset_loop, run_coro


def test_run_coro_thread_local() -> None:
    async def add(a: int, b: int) -> int:
        return a + b

    assert run_coro(add(1, 2)) == 3
    reset_loop()


def test_concurrent_threads_do_not_share_loop() -> None:
    loops: list[int] = []
    lock = threading.Lock()

    async def capture() -> None:
        loop = asyncio.get_running_loop()
        with lock:
            loops.append(id(loop))

    def worker() -> None:
        run_coro(capture())
        reset_loop()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(set(loops)) == 4
