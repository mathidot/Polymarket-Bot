import time
import logging
import os
from typing import Optional, Any, List

from config import INIT_PAIR_MODE, ORDERBOOK_CACHE_TTL, ORDERBOOK_CACHE_ENABLED
from state import ThreadSafeState, price_update_event
from market_init import fetch_positions_with_retry
from api import (
    get_price as api_get_price,
    get_order_book as api_get_order_book,
    get_order_books_with_retry,
)


logger = logging.getLogger("polymarket_bot")
_PRICE_UPDATE_VERBOSE = os.getenv('PRICE_UPDATE_VERBOSE', '1').lower() in ('1', 'true', 'yes')


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
    last_log_time = time.time()
    update_count = 0
    initial_update = True

    while not state.is_shutdown():
        try:
            logger.debug("🔄 Updating price history")
            start_time = time.time()
            now = time.time()
            price_updated = False
            current_time = time.time()
            price_updates: List[str] = []

            if INIT_PAIR_MODE == "positions":
                positions = fetch_positions_with_retry()
                if not positions:
                    time.sleep(5)
                    continue
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
                            state.add_price(asset_id, now, price, eventslug, outcome)
                            update_count += 1
                            price_updated = True
                            price_updates.append(
                                f"                                               💸 {outcome} in {eventslug}: ${price:.4f}"
                            )
                        except IndexError:
                            logger.debug(f"⏳ Building price history for {asset_id} - {eventslug}")
                            continue
                        except Exception as e:
                            logger.error(f"❌ Error updating price for asset {asset_id}: {str(e)}")
                            continue
            else:
                # Markets/config modes: update prices using batch order books with cache
                asset_ids = list(state._asset_pairs.keys())
                if not asset_ids:
                    logger.debug("⚠️ No asset pairs available for price updates yet")
                    time.sleep(2)
                    continue

                # Build and use cached batch order books
                tokens_list = list(set(asset_ids))
                books_map = {}
                cache_map, cache_ts = state.get_order_books_cache()
                use_cache = False
                if ORDERBOOK_CACHE_ENABLED and state.is_order_books_cache_valid(ORDERBOOK_CACHE_TTL):
                    if all(t in cache_map for t in tokens_list):
                        books_map = {tid: cache_map.get(tid) for tid in tokens_list}
                        use_cache = True
                        age_ms = (time.time() - cache_ts) * 1000.0
                        logger.debug(f"📚 Using cached order books (pricing) | age={age_ms:.0f}ms | tokens={len(tokens_list)}")
                if not use_cache:
                    try:
                        books_list = get_order_books_with_retry(tokens_list)
                        books_map = {tid: book for tid, book in zip(tokens_list, books_list)}
                        if ORDERBOOK_CACHE_ENABLED:
                            state.set_order_books_cache(books_map)
                    except Exception as e:
                        logger.warning(f"Batch get_order_books retry exhausted (pricing): {e}")

                for asset_id in asset_ids:
                    try:
                        # Prefer best bid/ask from cached order book; fallback to executable prices
                        price = None
                        book = books_map.get(asset_id)
                        best_bid = None
                        best_ask = None
                        if book is not None:
                            bids = getattr(book, "bids", None)
                            asks = getattr(book, "asks", None)
                            if bids:
                                try:
                                    best_bid = max((float(x.price) for x in bids if x and hasattr(x, "price")), default=None)
                                except Exception:
                                    try:
                                        best_bid = float(bids[-1].price)
                                    except Exception:
                                        best_bid = None
                            if asks:
                                try:
                                    best_ask = min((float(x.price) for x in asks if x and hasattr(x, "price")), default=None)
                                except Exception:
                                    try:
                                        best_ask = float(asks[-1].price)
                                    except Exception:
                                        best_ask = None

                        if best_bid is not None and best_ask is not None and best_bid > 0 and best_ask > 0:
                            price = (best_bid + best_ask) / 2.0
                        elif best_bid is not None and best_bid > 0:
                            price = best_bid
                        elif best_ask is not None and best_ask > 0:
                            price = best_ask
                        else:
                            # Fallback: executable price calls if no book data
                            buy_price = None
                            sell_price = None
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
                                # Final fallback: per-token order book mid
                                try:
                                    order_book = api_get_order_book(asset_id)
                                    bids = getattr(order_book, "bids", None)
                                    asks = getattr(order_book, "asks", None)
                                    if bids and asks:
                                        try:
                                            max_bid = max((float(x.price) for x in bids if x and hasattr(x, "price")), default=None)
                                        except Exception:
                                            max_bid = float(bids[-1].price)
                                        try:
                                            min_ask = min((float(x.price) for x in asks if x and hasattr(x, "price")), default=None)
                                        except Exception:
                                            min_ask = float(asks[-1].price)
                                        if max_bid and min_ask and max_bid > 0 and min_ask > 0:
                                            price = (max_bid + min_ask) / 2.0
                                        else:
                                            continue
                                    else:
                                        continue
                                except Exception:
                                    continue

                        eventslug, outcome = state.get_asset_meta(asset_id)
                        state.add_price(asset_id, now, float(price), eventslug, outcome)
                        update_count += 1
                        price_updated = True
                        price_updates.append(
                            f"                                               💸 {outcome} in {eventslug}: ${float(price):.4f}"
                        )
                    except IndexError:
                        logger.debug(f"⏳ Building price history for {asset_id}")
                        continue
                    except Exception as e:
                        logger.error(f"❌ Error updating price for asset {asset_id}: {str(e)}")
                        continue

            # Log price updates every 5 seconds (gate via env toggle)
            if current_time - last_log_time >= 5:
                if price_updates and _PRICE_UPDATE_VERBOSE:
                    logger.info("📊 Price Updates:\n" + "\n".join(price_updates))
                last_log_time = current_time

            if price_updated:
                price_update_event.set()
                if initial_update:
                    initial_update = False
                    logger.info("✅ Initial price data population complete")

            # Log summary every 1 minute
            if update_count >= 60:
                logger.info(
                    f"📊 Price Update Summary | Updates: {update_count} | Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                update_count = 0

            # Ensure we don't run too fast
            elapsed = time.time() - start_time
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)

        except Exception as e:
            logger.error(f"❌ Error in price update: {str(e)}")
            time.sleep(1)