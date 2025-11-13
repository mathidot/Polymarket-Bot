import time
import signal
import sys
import threading
from halo import Halo
from polymarket_bot.logger import logger
from polymarket_bot.state import ThreadSafeState
from polymarket_bot.threads import ThreadManager
from polymarket_bot.detection import wait_for_initialization, update_price_history, detect_and_trade, check_trade_exits
from polymarket_bot.client import refresh_api_credentials
from polymarket_bot.config import REFRESH_INTERVAL

def print_spikebot_banner() -> None:
    banner = r"""
╔════════════════════════════════════════════════════════════════════╗
║                                                                    ║
║   ███████╗██████╗ ██╗██╗  ██╗███████╗██████╗  ██████╗ ████████╗    ║
║   ██╔════╝██╔══██╗██║██║ ██╔╝██╔════╝██╔══██╗██╔═══██╗╚══██╔══╝    ║
║   ███████╗██████╔╝██║█████╔╝ █████╗  ██████╔╝██║   ██║   ██║       ║
║   ╚════██║██╔═══╝ ██║██╔═██╗ ██╔══╝  ██╔══██╗██║   ██║   ██║       ║
║   ███████║██║     ██║██║  ██╗███████╗██████╔╝╚██████╔╝   ██║       ║
║   ╚══════╝╚═╝     ╚═╝╚═╝  ╚═╝╚══════╝╚═════╝  ╚═════╝    ╚═╝       ║
║                                                                    ║
║                  🚀  P O L Y M A R K E T  B O T  🚀                ║
║                                                                    ║
╚════════════════════════════════════════════════════════════════════╝
    """
    print(banner)

def cleanup(state: ThreadSafeState) -> None:
    state.shutdown()
    for thread in threading.enumerate():
        if thread != threading.current_thread():
            thread.join(timeout=5)
    logger.info("✅ Cleanup complete")

def signal_handler(signum: int, frame: any, state: ThreadSafeState) -> None:
    cleanup(state)
    sys.exit(0)

def main() -> None:
    state = ThreadSafeState()
    thread_manager = ThreadManager(state)
    print_spikebot_banner()
    signal.signal(signal.SIGINT, lambda s, f: signal_handler(s, f, state))
    signal.signal(signal.SIGTERM, lambda s, f: signal_handler(s, f, state))
    spinner = Halo(text="Waiting for manual $1 entries on both sides of a market...", spinner="dots")
    spinner.start()
    time.sleep(5)
    logger.info(f"🚀 Spike-detection bot started at {time.strftime('%Y-%m-%d %H:%M:%S')}")
    if not wait_for_initialization(state):
        spinner.fail("❌ Failed to initialize. Exiting.")
        raise ValueError("Failed to initialize bot")
    spinner.succeed("Initialized successfully")
    thread_manager.start_thread("price_update", update_price_history)
    initial_data_wait = 0
    while initial_data_wait < 30:
        if any(state.get_price_history(asset_id) for asset_id in state._price_history.keys()):
            break
        time.sleep(1)
        initial_data_wait += 1
    thread_manager.start_thread("detect_trade", detect_and_trade)
    thread_manager.start_thread("check_exits", check_trade_exits)
    last_refresh_time = time.time()
    refresh_interval = REFRESH_INTERVAL
    last_status_time = time.time()
    while not state.is_shutdown():
        try:
            current_time = time.time()
            if current_time - last_status_time >= 30:
                active_threads = sum(1 for t in thread_manager.threads.values() if t.is_alive())
                logger.info(f"📊 Bot Status | Active Threads: {active_threads}/3 | Price Updates: {len(state._price_history)}")
                last_status_time = current_time
            if current_time - last_refresh_time > refresh_interval:
                if refresh_api_credentials():
                    last_refresh_time = current_time
                else:
                    time.sleep(300)
                    continue
            time.sleep(1)
        except Exception as e:
            logger.error(f"Error in main loop: {e}")
            time.sleep(1)

if __name__ == "__main__":
    main()
