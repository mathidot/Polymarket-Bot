import time
import threading
import logging
from queue import Queue
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import Future
from typing import Callable, Dict

from pydantic import FutureDate

from config import (
    THREAD_POOL_SIZE,
    MAX_QUEUE_SIZE,
    MAX_ERRORS,
    THREAD_RESTART_DELAY,
)
from state import ThreadSafeState

logger = logging.getLogger("polymarket_bot")


class ThreadManager:
    def __init__(self, state: ThreadSafeState):
        self.state = state
        self.futures = {}
        self.executor = ThreadPoolExecutor(max_workers=THREAD_POOL_SIZE)
        self.running = True

    def start_thread(
        self, name: str, target: Callable[[ThreadSafeState], None]
    ) -> None:
        if name in self.futures:
            return
        future = self.executor.submit(target, self.state)
        self.futures[name] = future
        logger.info(f"✅ Started thread: {name}")

    def stop(self) -> None:
        self.running = False
        try:
            # Signal threads to stop via shared state
            self.state.shutdown()
        except Exception:
            pass
        for name, future in list(self.futures.items()):
            try:
                future.result(timeout=5)
            except Exception:
                logger.debug(f"⚠️ Thread '{name}' did not join cleanly")
        self.executor.shutdown(wait=True)
