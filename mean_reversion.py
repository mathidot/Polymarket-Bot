import time
import logging
from statistics import mean, pstdev

from state import ThreadSafeState, price_update_event
from trading import place_buy_order, place_sell_order, is_recently_bought, is_recently_sold
from config import MR_LOOKBACK, MR_ENTRY_Z, MR_EXIT_Z, MAX_CONCURRENT_TRADES


logger = logging.getLogger("polymarket_bot")


def _zscore(prices):
    if len(prices) < 3:
        return None, None, None
    mu = mean(prices)
    sigma = pstdev(prices)
    if sigma == 0:
        return None, mu, sigma
    z = (prices[-1] - mu) / sigma
    return z, mu, sigma


def run_mean_reversion(state: ThreadSafeState) -> None:
    last_log = time.time()
    while not state.is_shutdown():
        try:
            if not price_update_event.wait(timeout=1.0):
                continue
            price_update_event.clear()

            assets = list(state._price_history.keys())
            if not assets:
                continue

            now = time.time()
            if now - last_log >= 5:
                logger.info("🔎 Mean Reversion scan tick")
                last_log = now

            active_trades = state.get_active_trades()
            for aid in assets:
                try:
                    history = state.get_price_history(aid)
                    if not history:
                        continue
                    window = history[-MR_LOOKBACK:]
                    prices = [p for (_, p) in window]
                    z, mu, sigma = _zscore(prices)
                    if z is None:
                        continue

                    if z <= -MR_ENTRY_Z and not is_recently_bought(state, aid):
                        if len(active_trades) >= MAX_CONCURRENT_TRADES:
                            continue
                        ok = place_buy_order(state, aid, "Mean reversion entry")
                        if ok:
                            logger.info(
                                f"✅ MR Buy {aid} | z={z:.2f} mu={mu:.4f} sd={sigma:.4f} price={prices[-1]:.4f}"
                            )

                    if z >= MR_ENTRY_Z and not is_recently_sold(state, aid):
                        if len(active_trades) >= MAX_CONCURRENT_TRADES:
                            continue
                        ok = place_sell_order(state, aid, "Mean reversion entry")
                        if ok:
                            logger.info(
                                f"✅ MR Sell {aid} | z={z:.2f} mu={mu:.4f} sd={sigma:.4f} price={prices[-1]:.4f}"
                            )

                    if abs(z) <= MR_EXIT_Z:
                        # Exit logic can be refined; here we rely on check_trade_exits for risk
                        pass

                except Exception as e:
                    logger.debug(f"Mean reversion error for {aid}: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error in run_mean_reversion: {e}")
            time.sleep(1)