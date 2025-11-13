import time
from typing import Optional
from .state import ThreadSafeState, price_update_event
from .logger import logger
from .api import fetch_positions_with_retry
from .config import PRICE_LOWER_BOUND, PRICE_UPPER_BOUND, SPIKE_THRESHOLD, CASH_PROFIT, CASH_LOSS, PCT_PROFIT, PCT_LOSS, HOLDING_TIME_LIMIT
from .trading import place_buy_order, place_sell_order
from .pricing import get_current_price

def update_price_history(state: ThreadSafeState) -> None:
    last_log_time = time.time()
    update_count = 0
    initial_update = True
    while not state.is_shutdown():
        try:
            start_time = time.time()
            now = time.time()
            positions = fetch_positions_with_retry()
            if not positions:
                time.sleep(5)
                continue
            state.update_positions(positions)
            price_updated = False
            current_time = time.time()
            price_updates = []
            for event_id, assets in positions.items():
                for asset in assets:
                    try:
                        eventslug = asset.eventslug
                        outcome = asset.outcome
                        asset_id = asset.asset
                        price = asset.current_price
                        if not asset_id:
                            continue
                        state.add_price(asset_id, now, price, eventslug, outcome)
                        update_count += 1
                        price_updated = True
                        price_updates.append(f"                                               💸 {outcome} in {eventslug}: ${price:.4f}")
                    except Exception:
                        continue
            if current_time - last_log_time >= 5:
                logger.info("📊 Price Updates:\n" + "\n".join(price_updates))
                last_log_time = current_time
            if price_updated:
                price_update_event.set()
                if initial_update:
                    initial_update = False
                    logger.info("✅ Initial price data population complete")
            if update_count >= 60:
                logger.info(f"📊 Price Update Summary | Updates: {update_count} | Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
                update_count = 0
            elapsed = time.time() - start_time
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)
        except Exception as e:
            logger.error(f"❌ Error in price update: {str(e)}")
            time.sleep(1)

def detect_and_trade(state: ThreadSafeState) -> None:
    last_log_time = time.time()
    scan_count = 0
    while not state.is_shutdown():
        try:
            if price_update_event.wait(timeout=1.0):
                price_update_event.clear()
                if not any(state.get_price_history(asset_id) for asset_id in state._price_history.keys()):
                    continue
                positions_copy = state.get_positions()
                scan_count += 1
                current_time = time.time()
                if current_time - last_log_time >= 5:
                    logger.info(f"🔍 Scanning Markets | Scan #{scan_count} | Active Positions: {len(positions_copy)}")
                    last_log_time = current_time
                for asset_id in list(state._price_history.keys()):
                    try:
                        history = state.get_price_history(asset_id)
                        if len(history) < 2:
                            continue
                        old_price = history[0][1]
                        new_price = history[-1][1]
                        if old_price == 0 or new_price == 0:
                            logger.warning(f"⚠️ Skipping asset {asset_id} due to zero price - Old: ${old_price:.4f}, New: ${new_price:.4f}")
                            continue
                        delta = (new_price - old_price) / old_price
                        if abs(delta) > SPIKE_THRESHOLD:
                            if new_price < PRICE_LOWER_BOUND or new_price > PRICE_UPPER_BOUND:
                                continue
                            from .state import TradeType
                            def is_recently_bought(state: ThreadSafeState, asset_id: str) -> bool:
                                with state._recent_trades_lock:
                                    if asset_id not in state._recent_trades or state._recent_trades[asset_id]["buy"] is None:
                                        return False
                                    now = time.time()
                                    from .config import COOLDOWN_PERIOD
                                    time_since_buy = now - state._recent_trades[asset_id]["buy"]
                                    return time_since_buy < COOLDOWN_PERIOD
                            def is_recently_sold(state: ThreadSafeState, asset_id: str) -> bool:
                                with state._recent_trades_lock:
                                    if asset_id not in state._recent_trades or state._recent_trades[asset_id]["sell"] is None:
                                        return False
                                    now = time.time()
                                    from .config import COOLDOWN_PERIOD
                                    time_since_sell = now - state._recent_trades[asset_id]["sell"]
                                    return time_since_sell < COOLDOWN_PERIOD
                            opposite = state.get_asset_pair(asset_id)
                            if not opposite:
                                continue
                            if delta > 0 and not is_recently_bought(state, asset_id):
                                if place_buy_order(state, asset_id, "Spike detected"):
                                    place_sell_order(state, opposite, "Opposite trade")
                            elif delta < 0 and not is_recently_sold(state, asset_id):
                                if place_sell_order(state, asset_id, "Spike detected"):
                                    place_buy_order(state, opposite, "Opposite trade")
                    except Exception as e:
                        logger.error(f"❌ Error processing asset {asset_id}: {str(e)}")
                        continue
        except Exception as e:
            logger.error(f"❌ Error in detect_and_trade: {str(e)}")
            time.sleep(1)

 

def check_trade_exits(state: ThreadSafeState) -> None:
    last_log_time = time.time()
    while not state.is_shutdown():
        try:
            active_trades = state.get_active_trades()
            if active_trades:
                current_time = time.time()
                if current_time - last_log_time >= 30:
                    logger.info(f"📈 Active Trades | Count: {len(active_trades)} | Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
                    last_log_time = current_time
            for asset_id, trade in active_trades.items():
                try:
                    positions_copy = state.get_positions()
                    position = None
                    for event_positions in positions_copy.values():
                        for p in event_positions:
                            if p.asset == asset_id:
                                position = p
                    if not position:
                        continue
                    current_price = get_current_price(state, asset_id)
                    if current_price is None:
                        continue
                    current_time = time.time()
                    last_traded = trade.entry_time
                    avg_price = position.avg_price
                    remaining_shares = position.shares
                    cash_profit = (current_price - avg_price) * remaining_shares
                    pct_profit = (current_price - avg_price) / avg_price
                    if current_time - last_traded > HOLDING_TIME_LIMIT:
                        place_sell_order(state, asset_id, "Holding time limit")
                        state.remove_active_trade(asset_id)
                        state.set_last_trade_time(time.time())
                    if cash_profit >= CASH_PROFIT or pct_profit > PCT_PROFIT:
                        place_sell_order(state, asset_id, "Take profit")
                        state.remove_active_trade(asset_id)
                        state.set_last_trade_time(time.time())
                    if cash_profit <= CASH_LOSS or pct_profit < PCT_LOSS:
                        place_sell_order(state, asset_id, "Stop loss")
                        state.remove_active_trade(asset_id)
                        state.set_last_trade_time(time.time())
                except Exception as e:
                    logger.error(f"❌ Error checking trade exit for {asset_id}: {str(e)}")
                    continue
            time.sleep(1)
        except Exception as e:
            logger.error(f"❌ Error in check_trade_exits: {str(e)}")
            time.sleep(1)

def wait_for_initialization(state: ThreadSafeState) -> bool:
    max_retries = 60
    retry_count = 0
    while retry_count < max_retries and not state.is_shutdown():
        try:
            positions = fetch_positions_with_retry()
            for event_id, sides in positions.items():
                if len(sides) % 2 == 0 and len(sides) > 1:
                    ids = [s.asset for s in sides]
                    state.add_asset_pair(ids[0], ids[1])
            if state.is_initialized():
                return True
            retry_count += 1
            time.sleep(2)
        except Exception as e:
            logger.error(f"❌ Error during initialization: {str(e)}")
            retry_count += 1
            time.sleep(2)
    return False
