import time
import logging
import os
from typing import Optional, Any, List

from config import (
    INIT_PAIR_MODE,
    ORDERBOOK_CACHE_TTL,
    ORDERBOOK_CACHE_ENABLED,
    PRICE_UPDATE_BATCH_SIZE,
    PRICE_UPDATE_MIN_INTERVAL,
    PRICE_UPDATE_FALLBACK_ENABLED,
    PRICE_UPDATE_YIELD_EVERY_N,
    PRICE_UPDATE_YIELD_SLEEP_MS,
)
from state import ThreadSafeState, price_update_event
from market_init import fetch_positions_with_retry
from api import (
    get_price as api_get_price,
    get_order_book as api_get_order_book,
    get_order_books_with_retry,
)

logger = logging.getLogger("polymarket_bot")
_PRICE_UPDATE_VERBOSE = os.getenv("PRICE_UPDATE_VERBOSE", "1").lower() in (
    "1",
    "true",
    "yes",
)


def get_current_price(state: ThreadSafeState, asset_id: str) -> Optional[float]:
    try:
        history = state.get_price_history(asset_id)
        if not history:
            logger.debug(f"⏳ No price history available for {asset_id}")
            return None
        return history[-1][1]
    except IndexError:
        logger.debug(f"⏳ Building price history for {asset_id}")
        return None
    except Exception as e:
        logger.error(f"❌ Error getting current price for {asset_id}: {str(e)}")
        return None


def update_price_history(state: ThreadSafeState) -> None:
    # Gate by configurable minimum interval to avoid overwork when thread manager calls frequently
    while not state.is_shutdown():
        try:
            logger.debug("🔄 Updating price history")
            current_time = time.time()
            price_updates: List[str] = []
            price_updated = False
            if INIT_PAIR_MODE == "positions":
                positions = fetch_positions_with_retry()
                if not positions:
                    return
                state.update_positions(positions)

                for event_id, assets in positions.items():
                    for asset in assets:
                        try:
                            eventslug = asset.eventslug
                            outcome = asset.outcome
                            asset_id = asset.asset
                            price = asset.current_price

                            if not asset_id:
                                continue

                            logger.info(
                                f"Updating price for {asset_id} - {eventslug} - {outcome} to ${price:.4f}"
                            )
                            state.add_price(
                                asset_id, current_time, price, eventslug, outcome
                            )
                            price_updated = True
                            price_updates.append(
                                f"                                               💸 {outcome} in {eventslug}: ${price:.4f}"
                            )
                        except IndexError:
                            logger.debug(
                                f"⏳ Building price history for {assets} - {event_id}"
                            )
                            continue
                        except Exception as e:
                            logger.error(
                                f"❌ Error updating price for asset {asset}: {str(e)}"
                            )
                            continue
            else:
                # Markets/config modes: update prices using batch order books with cache
                asset_ids_all = list(state._asset_pairs.keys())
                if not asset_ids_all:
                    logger.debug("⚠️ No asset pairs available for price updates yet")
                    return

                # Build and use cached batch order books for selected batch
                tokens_list = list(set(asset_ids_all))
                try:
                    books_list = get_order_books_with_retry(tokens_list)
                    books_map = {
                        tid: book for tid, book in zip(tokens_list, books_list)
                    }
                except Exception as e:
                    logger.warning(
                        f"Batch get_order_books retry exhausted (pricing): {e}"
                    )

                for idx, asset_id in enumerate(asset_ids_all, start=1):
                    try:
                        # Prefer best bid/ask from cached order book; fallback to executable prices
                        price = None
                        book = api_get_order_book(asset_id)
                        best_bid = None
                        best_ask = None
                        if book is not None:
                            bids = getattr(book, "bids", None)
                            asks = getattr(book, "asks", None)
                            if bids:
                                try:
                                    best_bid = max(
                                        (
                                            float(x.price)
                                            for x in bids
                                            if x and hasattr(x, "price")
                                        ),
                                        default=None,
                                    )
                                except Exception:
                                    try:
                                        best_bid = float(bids[-1].price)
                                    except Exception:
                                        best_bid = None
                            if asks:
                                try:
                                    best_ask = min(
                                        (
                                            float(x.price)
                                            for x in asks
                                            if x and hasattr(x, "price")
                                        ),
                                        default=None,
                                    )
                                except Exception:
                                    try:
                                        best_ask = float(asks[-1].price)
                                    except Exception:
                                        best_ask = None

                        if (
                            best_bid is not None
                            and best_ask is not None
                            and best_bid > 0
                            and best_ask > 0
                        ):
                            price = (best_bid + best_ask) / 2.0
                        elif best_bid is not None and best_bid > 0:
                            price = best_bid
                        elif best_ask is not None and best_ask > 0:
                            price = best_ask
                        else:
                            buy_price = None
                            sell_price = None
                            if PRICE_UPDATE_FALLBACK_ENABLED:
                                try:
                                    buy_price = api_get_price(asset_id, "BUY")
                                except Exception:
                                    buy_price = None
                                try:
                                    sell_price = api_get_price(asset_id, "SELL")
                                except Exception:
                                    sell_price = None

                            def to_number(x):
                                try:
                                    if x is None:
                                        return None
                                    return float(x)
                                except Exception:
                                    return None

                            b = to_number(buy_price)
                            s = to_number(sell_price)
                            if b is not None and s is not None and b > 0 and s > 0:
                                price = (b + s) / 2.0
                            elif b is not None and b > 0:
                                price = b
                            elif s is not None and s > 0:
                                price = s
                            else:
                                continue

                        eventslug, outcome = state.get_asset_meta(asset_id)
                        state.add_price(
                            asset_id, time.time(), float(price), eventslug, outcome
                        )
                        price_updated = True
                        price_updates.append(
                            f"                                               💸 {outcome} in {eventslug}: ${float(price):.4f}"
                        )
                        logger.info(price_updates)
                    except IndexError:
                        logger.debug(f"⏳ Building price history for {asset_id}")
                        continue
                    except Exception as e:
                        logger.error(
                            f"❌ Error updating price for asset {asset_id}: {str(e)}"
                        )
                        continue

            if price_updated:
                price_update_event.set()
                time.sleep(0.5)

        except Exception as e:
            logger.error(f"❌ Error in price update: {str(e)}")
            # Do not sleep here; thread manager controls cadence
