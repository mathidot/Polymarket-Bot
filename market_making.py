import time
import logging
from typing import Optional

from py_clob_client.clob_types import OrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from state import ThreadSafeState
from api import get_order_book, create_limit_order, post_order
from config import (
    MM_SPREAD_BPS,
    MM_ORDER_SIZE,
    MM_MAX_INVENTORY,
    MM_REFRESH_INTERVAL,
)

logger = logging.getLogger("polymarket_bot")


def _best_prices(asset_id: str) -> Optional[tuple[float, float]]:
    try:
        ob = get_order_book(asset_id)
        bids = getattr(ob, "bids", None)
        asks = getattr(ob, "asks", None)
        if not bids or not asks:
            return None
        try:
            best_bid = max(bids, key=lambda b: float(b.price))
            best_ask = min(asks, key=lambda a: float(a.price))
        except Exception:
            best_bid = bids[-1]
            best_ask = asks[-1]
        return float(best_bid.price), float(best_ask.price)
    except Exception as e:
        logger.debug(f"Orderbook unavailable for {asset_id}: {e}")
        return None


def _compute_quotes(best_bid: float, best_ask: float) -> tuple[float, float]:
    spread = MM_SPREAD_BPS / 10000.0
    mid = (best_bid + best_ask) / 2.0
    half = spread / 2.0
    bid_price = max(0.001, min(best_bid, mid - half))
    ask_price = min(0.999, max(best_ask, mid + half))
    return bid_price, ask_price


def run_passive_market_making(state: ThreadSafeState) -> None:
    last_log = time.time()
    while not state.is_shutdown():
        try:
            asset_ids = list(state._asset_pairs.keys())
            if not asset_ids:
                time.sleep(1)
                continue

            now = time.time()
            if now - last_log >= 30:
                logger.info("🛠️ Passive Market Making tick")
                last_log = now

            positions = state.get_positions()
            for asset_id in asset_ids:
                try:
                    bp = _best_prices(asset_id)
                    if not bp:
                        continue
                    best_bid, best_ask = bp
                    bid_price, ask_price = _compute_quotes(best_bid, best_ask)

                    pos = next((p for p in positions if p.asset_id == asset_id), None)
                    shares = pos.shares if pos else 0.0
                    if shares > MM_MAX_INVENTORY:
                        logger.debug(
                            f"Skip quoting {asset_id}: inventory {shares:.2f} > {MM_MAX_INVENTORY}"
                        )
                        continue

                    try:
                        buy_order = OrderArgs(
                            price=bid_price,
                            size=MM_ORDER_SIZE,
                            side=BUY,
                            token_id=asset_id,
                        )
                        signed_buy = create_limit_order(buy_order)
                        post_order(signed_buy, OrderType.GTC)
                        sell_order = OrderArgs(
                            price=ask_price,
                            size=MM_ORDER_SIZE,
                            side=SELL,
                            token_id=asset_id,
                        )
                        signed_sell = create_limit_order(sell_order)
                        post_order(signed_sell, OrderType.GTC)
                        logger.info(
                            f"📥 Quoted {asset_id} | bid={bid_price:.4f} size={MM_ORDER_SIZE:.2f} | ask={ask_price:.4f} size={MM_ORDER_SIZE:.2f}"
                        )
                    except Exception as e:
                        logger.debug(f"Quote placement failed for {asset_id}: {e}")

                except Exception as e:
                    logger.debug(f"Market making error for {asset_id}: {e}")
                    continue

            time.sleep(MM_REFRESH_INTERVAL)
        except Exception as e:
            logger.error(f"Error in run_passive_market_making: {e}")
            time.sleep(1)
