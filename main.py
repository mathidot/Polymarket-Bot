import time
import threading
import logging
import signal
import sys
from typing import Any

from halo import Halo

from log import setup_logging
from models import ConfigurationError
from config import (
    INIT_PAIR_MODE,
    MARKET_FETCH_LIMIT,
    CONFIG_ASSET_PAIRS,
    REFRESH_INTERVAL,
    SIMULATION_MODE,
)
import api as api_mod
import state as state_mod
import market_init
import pricing
import strategy
import threads as thread_mod


logger = setup_logging()


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


def cleanup(state: state_mod.ThreadSafeState) -> None:
    logger.info("🔄 Starting cleanup...")
    try:
        state.shutdown()
        for thread in threading.enumerate():
            if thread != threading.current_thread():
                thread.join(timeout=5)
                if thread.is_alive():
                    logger.warning(f"Thread {thread.name} did not finish in time")
                    if hasattr(thread, "_stop"):
                        try:
                            thread._stop()
                        except Exception:
                            pass
        if not state.wait_for_cleanup(timeout=10):
            logger.warning("Cleanup did not complete in time")
        logger.info("✅ Cleanup complete")
    except Exception as e:
        logger.error(f"Error during cleanup: {e}")
        raise


def signal_handler(signum: int, frame: Any, state: state_mod.ThreadSafeState) -> None:
    logger.info(f"Received signal {signum}. Initiating shutdown...")
    cleanup(state)
    sys.exit(0)


def main() -> None:
    state = None
    thread_manager = None
    try:
        state = state_mod.ThreadSafeState()
        # 启动时打印模拟模式与余额信息，便于排查模式不一致问题
        try:
            logger.info(
                f"🧪 Simulation Mode | config={SIMULATION_MODE} | state={state.is_simulation_mode()} | start_usdc=${state.get_sim_usdc_balance():.2f}"
            )
        except Exception:
            pass
        thread_manager = thread_mod.ThreadManager(state)
        print_spikebot_banner()

        try:
            signal.signal(signal.SIGINT, lambda s, f: signal_handler(s, f, state))
            signal.signal(signal.SIGTERM, lambda s, f: signal_handler(s, f, state))
        except Exception:
            pass

        if INIT_PAIR_MODE == "positions":
            spinner_text = "Waiting for manual $1 entries on both sides of a market..."
        elif INIT_PAIR_MODE == "markets":
            spinner_text = "Initializing asset pairs from market list..."
        elif INIT_PAIR_MODE == "config":
            spinner_text = "Initializing asset pairs from config..."
        else:
            spinner_text = "Initializing asset pairs..."
        spinner = Halo(text=spinner_text, spinner="dots")
        spinner.start()
        time.sleep(1)
        logger.info(
            f"🚀 Spike-detection bot started at {time.strftime('%Y-%m-%d %H:%M:%S')}"
        )

        if INIT_PAIR_MODE == "markets":
            logger.info(
                f"🔧 Pair Mode: markets | market_fetch_limit={MARKET_FETCH_LIMIT}"
            )
            logger.info("💡 Tip: adjust 'market_fetch_limit' in .env to control scope")
        elif INIT_PAIR_MODE == "config":
            logger.info(
                f"🔧 Pair Mode: config | config_asset_pairs='{CONFIG_ASSET_PAIRS}'"
            )
            logger.info("💡 Format: config_asset_pairs=idA:idB,idC:idD (token IDs)")

        if not market_init.wait_for_initialization(state):
            spinner.fail("❌ Failed to initialize. Exiting.")
            raise ConfigurationError("Failed to initialize bot")

        spinner.succeed("Initialized successfully")

        # Load initial simulated positions from .env if configured
        if SIMULATION_MODE:
            try:
                applied = market_init.load_sim_positions_from_config(state)
                if applied > 0:
                    logger.info(
                        f"[SIM] Loaded {applied} initial simulated positions from .env"
                    )
                else:
                    logger.info("[SIM] No initial simulated positions to load")
            except Exception as e:
                logger.warning(f"[SIM] Failed to load initial simulated positions: {e}")

        thread_targets = {
            "price_update": pricing.update_price_history,
            # Spike detection strategy
            "detect_trade": strategy.detect_and_trade,
            # Risk exits (take profit / stop loss / holding time)
            "check_exits": strategy.check_trade_exits,
            # Real-time holdings snapshot printer
            "positions_log": strategy.print_positions_realtime,
        }

        logger.info("🔄 Starting price update thread...")
        thread_manager.start_thread("price_update", thread_targets["price_update"])

        logger.info("⏳ Waiting for initial price data...")
        initial_data_wait = 0
        while initial_data_wait < 30 and not state.is_shutdown():
            if any(
                state.get_price_history(aid)
                for aid in list(state._price_history.keys())
            ):
                logger.info("✅ Initial price data received")
                break
            time.sleep(1)
            initial_data_wait += 1
            if initial_data_wait % 5 == 0:
                logger.info(
                    f"⏳ Still waiting for initial price data... ({initial_data_wait}/30 seconds)"
                )

        if initial_data_wait >= 30:
            logger.warning("⚠️ No initial price data received after 30 seconds")

        logger.info("🔄 Starting trading threads...")
        thread_manager.start_thread("detect_trade", thread_targets["detect_trade"])
        thread_manager.start_thread("check_exits", thread_targets["check_exits"])
        thread_manager.start_thread("positions_log", thread_targets["positions_log"])

        last_refresh_time = time.time()
        refresh_interval = REFRESH_INTERVAL
        last_status_time = time.time()

        while not state.is_shutdown():
            try:
                current_time = time.time()

                if current_time - last_status_time >= 30:
                    active_threads = sum(
                        1 for t in thread_manager.futures.values() if t.running()
                    )
                    logger.info(
                        f"📊 Bot Status | Active Threads: {active_threads}/4 | Price Updates: {len(state._price_history)}"
                    )
                    last_status_time = current_time

                if not SIMULATION_MODE and (
                    current_time - last_refresh_time > refresh_interval
                ):
                    if api_mod.refresh_api_credentials():
                        last_refresh_time = current_time
                    else:
                        logger.warning(
                            "⚠️ Failed to refresh API credentials. Will retry in 5 minutes."
                        )
                        time.sleep(300)
                        continue

                for name, future in thread_manager.futures.items():
                    if not future.running():
                        logger.warning(f"⚠️ Thread {name} has died. Restarting...")
                        target = thread_targets.get(name)
                        if target is not None:
                            thread_manager.start_thread(name, target)
                        else:
                            logger.error(
                                f"❌ No target found for thread {name}; cannot restart."
                            )

                time.sleep(1)

            except Exception as e:
                logger.error(f"Error in main loop: {e}")
                time.sleep(1)

    except KeyboardInterrupt:
        logger.info("👋 Shutting down gracefully...")
    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
    finally:
        if thread_manager:
            thread_manager.stop()
        if state:
            cleanup(state)


if __name__ == "__main__":
    main()
