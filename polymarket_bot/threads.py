import time
import threading
from queue import Queue
from concurrent.futures import ThreadPoolExecutor
from .config import THREAD_POOL_SIZE, MAX_QUEUE_SIZE, MAX_ERRORS, THREAD_RESTART_DELAY
from .logger import logger

class ThreadManager:
    def __init__(self, state):
        self.state = state
        self.threads = {}
        self.thread_queues = {}
        self.executor = ThreadPoolExecutor(max_workers=THREAD_POOL_SIZE)
        self.running = True
    def start_thread(self, name: str, target: callable) -> None:
        if name in self.threads and self.threads[name].is_alive():
            return
        queue = Queue(maxsize=MAX_QUEUE_SIZE)
        self.thread_queues[name] = queue
        def thread_wrapper():
            error_count = 0
            consecutive_errors = 0
            while self.running and not self.state.is_shutdown():
                try:
                    target(self.state)
                    error_count = 0
                    consecutive_errors = 0
                    time.sleep(0.1)
                except Exception as e:
                    error_count += 1
                    consecutive_errors += 1
                    logger.error(f"❌ Error in {name} thread: {str(e)}")
                    if consecutive_errors >= MAX_ERRORS:
                        logger.error(f"❌ Too many consecutive errors in {name} thread. Restarting...")
                        time.sleep(THREAD_RESTART_DELAY)
                        consecutive_errors = 0
                    else:
                        time.sleep(1)
        thread = threading.Thread(target=thread_wrapper, daemon=True, name=name)
        thread.start()
        self.threads[name] = thread
        logger.info(f"✅ Started thread: {name}")
    def stop(self) -> None:
        self.running = False
        for thread in self.threads.values():
            if thread.is_alive():
                thread.join(timeout=5)
        self.executor.shutdown(wait=True)
