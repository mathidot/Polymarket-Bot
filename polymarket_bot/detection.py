import time
import statistics
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
from .state import ThreadSafeState, price_update_event
from .logger import logger
from .config import PRICE_LOWER_BOUND, PRICE_UPPER_BOUND, SPIKE_THRESHOLD
from .config import CASH_PROFIT, CASH_LOSS, PCT_PROFIT, PCT_LOSS, HOLDING_TIME_LIMIT
from .config import DYNAMIC_SPIKE_ENABLE, SPIKE_VOL_K, SPIKE_SPREAD_BUFFER
from .config import DELTA_MODE, DETECT_LOOKBACK_SECONDS, DETECT_LOOKBACK_SAMPLES
from .config import PRICE_FRESHNESS_SECONDS
from .config import COOLDOWN_PERIOD
from .config import FETCH_INTERVAL_MS, FETCH_CONCURRENCY, DETECT_CONCURRENCY, EXIT_CONCURRENCY
from .config import SIM_MODE
from .trading import place_buy_order, place_sell_order
from .pricing import get_current_price
from .client import get_client
from .slug_source import load_watchlist_slugs, resolve_tokens_from_watchlist

def update_price_history(state: ThreadSafeState) -> None:
    """基于 watchlist 持续采集价格并写入历史。

    价格来源优先使用订单簿中间价，失败回退到侧向价。写入成功后触发价格更新事件。

    Args:
        state: 线程安全状态对象。
    """
    last_log_time = time.time()
    update_count = 0
    initial_update = True
    while not state.is_shutdown():
        try:
            start_time = time.time()
            now = time.time()
            tokens = state.get_watchlist_tokens()
            if not tokens:
                time.sleep(1)
                continue
            price_updated = False
            current_time = time.time()
            price_updates = []

            def _fetch_and_store(token_id: str):
                price_local = None
                es, out = state.get_token_meta(token_id)
                try:
                    cli_local = get_client()
                    if cli_local is None:
                        raise RuntimeError("ClobClient unavailable")
                    ob = cli_local.get_order_book(token_id)
                    if ob.bids and ob.asks:
                        best_bid = max(ob.bids, key=lambda lvl: float(lvl.price))
                        best_ask = min(ob.asks, key=lambda lvl: float(lvl.price))
                        price_local = (float(best_bid.price) + float(best_ask.price)) / 2.0
                except Exception:
                    price_local = None
                if price_local is None:
                    try:
                        cli_local = get_client()
                        if cli_local is None:
                            raise RuntimeError("ClobClient unavailable")
                        price_local = float(cli_local.get_price(token_id, "BUY"))
                    except Exception:
                        price_local = None
                if price_local is None:
                    logger.debug(f"Skip token {token_id}: price unavailable this cycle")
                    return None
                state.add_price(token_id, now, float(price_local), es or "", out or "")
                return f"                                               💸 {out or ''} in {es or ''} | token {token_id}: ${float(price_local):.4f}"

            with ThreadPoolExecutor(max_workers=FETCH_CONCURRENCY) as ex:
                for res in ex.map(_fetch_and_store, tokens):
                    if res:
                        price_updates.append(res)
                        update_count += 1
                        price_updated = True
            if current_time - last_log_time >= 5:
                if price_updates:
                    logger.info("📊 Price Updates:\n" + "\n".join(price_updates))
                    logger.info("📊 Account balance Updates:\n" + "\n".join(price_updates))
                else:
                    logger.info(f"📊 Price Updates: none | tokens={len(tokens)}")
                if SIM_MODE and state.is_simulation_enabled():
                    try:
                        logger.info(f"💼 SIM Balance: ${state.get_sim_balance():.4f}")
                    except Exception:
                        pass
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
            target_sec = max(0.0, (FETCH_INTERVAL_MS / 1000.0) - elapsed)
            if target_sec > 0:
                time.sleep(target_sec)
        except Exception as e:
            logger.error(f"❌ Error in price update: {str(e)}")
            time.sleep(1)


def detect_and_trade(state: ThreadSafeState) -> None:
    """尖刺检测与交易执行。

    只看当前价格和上一个时间点的价格
    触发后执行主腿与对冲腿交易，并遵守冷却与价格区间限制。

    Args:
        state: 线程安全状态对象。
    """
    last_log_time = time.time()
    scan_count = 0
    while not state.is_shutdown():
        try:
            if price_update_event.wait(timeout=1.0):
                price_update_event.clear()
                if not any(state.get_price_history(asset_id) for asset_id in state._price_history.keys()):
                    continue
                scan_count += 1
                current_time = time.time()
                if current_time - last_log_time >= 5:
                    logger.info(f"🔍 Scanning Markets | Scan #{scan_count} | Tracked Assets: {len(state._price_history)}")
                    last_log_time = current_time
                assets = list(state._price_history.keys())
                def _process_asset(asset_id: str):
                    try:
                        history = state.get_price_history(asset_id)
                        if not history or len(history) < 2:
                            logger.warning(f"⚠️ length of history: {len(history)}, length is not enough, skip")
                            return
                        last_ts = float(history[-1][0])
                        if time.time() - last_ts > PRICE_FRESHNESS_SECONDS:
                            logger.warning("⚠️ holding time is too long, skip")
                            return
                        old_price = float(history[-2][1])
                        new_price = float(history[-1][1])
                        if old_price == 0 or new_price == 0:
                            logger.warning(f"⚠️ Skipping asset {asset_id} due to zero price - Old: ${old_price:.4f}, New: ${new_price:.4f}")
                            return
                        delta = (new_price - old_price) / old_price
                        if abs(delta) > SPIKE_THRESHOLD:
                            if new_price < PRICE_LOWER_BOUND or new_price > PRICE_UPPER_BOUND:
                                return
                            def is_recently_bought(s: ThreadSafeState, aid: str) -> bool:
                                with s._recent_trades_lock:
                                    if aid not in s._recent_trades or s._recent_trades[aid]["buy"] is None:
                                        return False
                                    now2 = time.time()
                                    return (now2 - s._recent_trades[aid]["buy"]) < COOLDOWN_PERIOD
                            opposite = state.get_asset_pair(asset_id)
                            if delta > 0 and not is_recently_bought(state, asset_id):
                                place_buy_order(state, asset_id, "Spike up")
                            elif delta < 0 and opposite is not None and  not is_recently_bought(state, opposite):
                                place_buy_order(state, opposite, "Spike down → buy opposite")
                    except Exception as e:
                        logger.error(f"❌ Error processing asset {asset_id}: {str(e)}")
                        return
                with ThreadPoolExecutor(max_workers=DETECT_CONCURRENCY) as ex:
                    list(ex.map(_process_asset, assets))
        except Exception as e:
            logger.error(f"❌ Error in detect_and_trade: {str(e)}")
            time.sleep(1)

def detect_and_trade_window(state: ThreadSafeState) -> None:
    """尖刺检测与交易执行。

    使用固定回看窗口计算 `delta`，结合动态阈值（spread/σ）判断；
    触发后执行主腿与对冲腿交易，并遵守冷却与价格区间限制。

    Args:
        state: 线程安全状态对象。
    """
    last_log_time = time.time()
    scan_count = 0
    while not state.is_shutdown():
        try:
            if price_update_event.wait(timeout=1.0):
                price_update_event.clear()
                if not any(state.get_price_history(asset_id) for asset_id in state._price_history.keys()):
                    continue
                scan_count += 1
                current_time = time.time()
                if current_time - last_log_time >= 5:
                    logger.info(f"🔍 Scanning Markets | Scan #{scan_count} | Tracked Assets: {len(state._price_history)}")
                    last_log_time = current_time
                assets = list(state._price_history.keys())
                def _process_asset(aid: str):
                    try:
                        history = state.get_price_history(aid)
                        if not history or len(history) < 2:
                            return
                        last_ts = float(history[-1][0])
                        if time.time() - last_ts > PRICE_FRESHNESS_SECONDS:
                            return
                        logger.info(f"history: {history}")
                        delta_info = compute_delta_from_history(history)
                        window_delta, first_px, last_px, window_len = delta_info
                        if window_delta is None:
                            return
                        threshold = SPIKE_THRESHOLD
                        spread, sigma = compute_spread_sigma(aid, history)
                        if DYNAMIC_SPIKE_ENABLE:
                            threshold = max(SPIKE_THRESHOLD, SPIKE_VOL_K * sigma, spread + SPIKE_SPREAD_BUFFER)
                        new_price = float(history[-1][1])
                        logger.info(f"deltaInfo: {delta_info}, window_delta: {window_delta}, spread: {spread}, sigma: {sigma}, threshold: {threshold}")
                        if abs(window_delta) > threshold:
                            if new_price < PRICE_LOWER_BOUND or new_price > PRICE_UPPER_BOUND:
                                return
                            def is_recently_bought(s: ThreadSafeState, tid: str) -> bool:
                                with s._recent_trades_lock:
                                    if tid not in s._recent_trades or s._recent_trades[tid]["buy"] is None:
                                        return False
                                    now2 = time.time()
                                    return (now2 - s._recent_trades[tid]["buy"]) < COOLDOWN_PERIOD
                            opposite = state.get_asset_pair(aid)
                            if window_delta > 0 and not is_recently_bought(state, aid):
                                place_buy_order(state, aid, f"Spike up | delta={window_delta:.4f} | thr={threshold:.4f} | spread={spread:.4f} | sigma={sigma:.4f} | win={int(window_len)}")
                            elif window_delta < 0 and opposite and not is_recently_bought(state, opposite):
                                place_buy_order(state, opposite, f"Spike down → buy opposite | delta={window_delta:.4f} | thr={threshold:.4f} | spread={spread:.4f} | sigma={sigma:.4f} | win={int(window_len)}")
                    except Exception as e:
                        logger.error(f"❌ Error processing asset {aid}: {str(e)}")
                        return
                with ThreadPoolExecutor(max_workers=DETECT_CONCURRENCY) as ex:
                    list(ex.map(_process_asset, assets))
        except Exception as e:
            logger.error(f"❌ Error in detect_and_trade: {str(e)}")
            time.sleep(1)

def check_trade_exits(state: ThreadSafeState) -> None:
    """周期检查活跃交易的止盈/止损与超时退出。

    Args:
        state: 线程安全状态对象。
    """
    last_log_time = time.time()
    while not state.is_shutdown():
        try:
            active_trades = state.get_active_trades()
            if active_trades:
                current_time = time.time()
                if current_time - last_log_time >= 30:
                    logger.info(f"📈 Active Trades | Count: {len(active_trades)} | Time: {time.strftime('%Y-%m-%d %H:%M:%S')}")
                    last_log_time = current_time
            items = list(active_trades.items())
            def _process_exit(item):
                asset_id, trade = item
                try:
                    current_price = get_current_price(state, asset_id)
                    if current_price is None:
                        return
                    now3 = time.time()
                    last_traded = trade.entry_time
                    avg_price = trade.entry_price
                    remaining_shares = getattr(trade, "shares", 0.0)
                    cash_profit = (current_price - avg_price) * remaining_shares
                    pct_profit = (current_price - avg_price) / avg_price if avg_price else 0.0
                    if now3 - last_traded > HOLDING_TIME_LIMIT:
                        place_sell_order(state, asset_id, "Holding time limit")
                        state.remove_active_trade(asset_id)
                        state.set_last_trade_time(time.time())
                        return
                    if cash_profit >= CASH_PROFIT or pct_profit > PCT_PROFIT:
                        place_sell_order(state, asset_id, "Take profit")
                        state.remove_active_trade(asset_id)
                        state.set_last_trade_time(time.time())
                        return
                    if cash_profit <= CASH_LOSS or pct_profit < PCT_LOSS:
                        place_sell_order(state, asset_id, "Stop loss")
                        state.remove_active_trade(asset_id)
                        state.set_last_trade_time(time.time())
                        return
                except Exception as e:
                    logger.error(f"❌ Error checking trade exit for {asset_id}: {str(e)}")
                    return
            if items:
                with ThreadPoolExecutor(max_workers=EXIT_CONCURRENCY) as ex:
                    list(ex.map(_process_exit, items))
            time.sleep(1)
        except Exception as e:
            logger.error(f"❌ Error in check_trade_exits: {str(e)}")
            time.sleep(1)

def wait_for_initialization(state: ThreadSafeState) -> bool:
    """初始化监控：解析 slugs → markets → tokens，并建立配对关系。

    Args:
        state: 线程安全状态对象。

    Returns:
        True 表示初始化成功；False 表示重试耗尽。
    """
    max_retries = 60
    retry_count = 0
    while retry_count < max_retries and not state.is_shutdown():
        try:
            slugs = load_watchlist_slugs("watchlist_slugs.json")
            pairs, meta = resolve_tokens_from_watchlist(slugs)
            for a, b in pairs.items():
                state.add_asset_pair(a, b)
            tokens = list(meta.keys())
            state.set_watchlist(tokens, meta)
            logger.info(f"🔎 Watchlist Summary | slugs={len(slugs)} | tokens={len(tokens)} | pairs={len(pairs)//2}")
            if tokens:
                preview = []
                for t in tokens[:20]:
                    es, out = state.get_token_meta(t)
                    preview.append(f"  - token={t} | outcome={out or ''} | slug={es or ''}")
                logger.info("\n" + "\n".join(preview))
            if state.is_initialized() and tokens:
                return True
            retry_count += 1
            time.sleep(2)
        except Exception as e:
            logger.error(f"❌ Error during initialization: {str(e)}")
            retry_count += 1
            time.sleep(2)
    return False

def compute_delta_from_history(history) -> tuple:
    """按配置窗口计算价格变化 `delta`。

    Args:
        history: 价格历史 deque[(timestamp, price, eventslug, outcome)]。

    Returns:
        (delta, first_px, last_px, window_len)；不可用返回 (None, None, None, 0)。
    """
    try:
        now = time.time()
        if DELTA_MODE == "seconds" and DETECT_LOOKBACK_SECONDS > 0:
            cutoff = now - DETECT_LOOKBACK_SECONDS
            window = [h for h in history if h[0] >= cutoff]
        else:
            window = list(history)[-DETECT_LOOKBACK_SAMPLES:]
        if len(window) < 2:
            return None, None, None, 0
        first_px = float(window[0][1])
        last_px = float(window[-1][1])
        if first_px <= 0 or last_px <= 0:
            return None, None, None, 0
        delta = (last_px - first_px) / first_px
        return delta, first_px, last_px, len(window)
    except Exception:
        return None, None, None, 0

def compute_spread_sigma(asset_id: str, history) -> tuple:
    """计算当前价差与窗口波动率。

    Args:
        asset_id: 资产 token ID。
        history: 价格历史。

    Returns:
        (spread, sigma)。
    """
    spread = 0.0
    try:
        cli = get_client()
        if cli:
            ob = cli.get_order_book(asset_id)
            if ob.bids and ob.asks:
                bid = float(ob.bids[-1].price)
                ask = float(ob.asks[-1].price)
                spread = max(0.0, ask - bid)
    except Exception:
        spread = 0.0
    try:
        window = list(history)[-max(3, min(len(history), DETECT_LOOKBACK_SAMPLES))]
        # build returns for sigma
        rets = []
        for i in range(1, len(window)):
            p0 = float(window[i-1][1])
            p1 = float(window[i][1])
            if p0 > 0 and p1 > 0:
                rets.append((p1 - p0) / p0)
        sigma = statistics.pstdev(rets) if len(rets) >= 2 else 0.0
    except Exception:
        sigma = 0.0
    return spread, sigma
