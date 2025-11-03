import time
import logging
from typing import Optional

from config import (
    SPIKE_THRESHOLD_UP,
    SPIKE_THRESHOLD_DOWN,
    HOLDING_TIME_LIMIT,
    CASH_PROFIT,
    PCT_PROFIT,
    CASH_LOSS,
    PCT_LOSS,
    ARB_ENTRY_SUM_THRESHOLD,
    ARB_EXIT_SUM_THRESHOLD,
    MAX_CONCURRENT_TRADES,
    ORDERBOOK_CACHE_TTL,
    POSITIONS_LOG_THROTTLE_SECS,
    ORDERBOOK_CACHE_ENABLED,
)
import log
from state import ThreadSafeState, price_update_event
from pricing import get_current_price
from api import get_order_book, get_order_books_with_retry
from trading import (
    find_position_by_asset,
    get_min_ask_data,
    get_max_bid_data,
    place_buy_order,
    place_sell_order,
)

logger = logging.getLogger("polymarket_bot")


def detect_and_trade(state: ThreadSafeState) -> None:
    last_log_time = time.time()
    scan_count = 0

    while not state.is_shutdown():
        logger.info("detect_and_trade tick")
        try:
            if price_update_event.wait(timeout=0.2):
                price_update_event.clear()

                if not any(
                    state.get_price_history(asset_id)
                    for asset_id in state._price_history.keys()
                ):
                    logger.info("⏳ Waiting for price history to be populated...")
                    continue

                positions_copy = state.get_positions()
                scan_count += 1

                current_time = time.time()
                if current_time - last_log_time >= 5:
                    logger.info(
                        f"🔍 Scanning Markets | Scan #{scan_count} | Active Positions: {len(positions_copy)}"
                    )
                    last_log_time = current_time

                for asset_id in list(state._price_history.keys()):
                    try:
                        history = state.get_price_history(asset_id)
                        if len(history) < 2:
                            continue

                        old_price = history[0][1]
                        new_price = history[-1][1]

                        if old_price == 0 or new_price == 0:
                            logger.warning(
                                f"⚠️ Skipping asset {asset_id} due to zero price - Old: ${old_price:.4f}, New: ${new_price:.4f}"
                            )
                            continue

                        delta = (new_price - old_price) / old_price
                        logger.info(f"Asset {asset_id} price change: {delta:.2%}")

                        # 买入逻辑：当价格涨幅超过指定阈值，快速买入（移除冷却期与对侧配对交易）
                        if delta > SPIKE_THRESHOLD_UP:
                            if new_price < 0.20 or new_price > 0.80:
                                continue
                            logger.info(
                                f"🟨 Spike Detected | Asset: {asset_id} | Delta: {delta:.2%} | Price: ${new_price:.4f}"
                            )
                            logger.info(
                                f"🟢 Buy Signal | Asset: {asset_id} | Price: ${new_price:.4f}"
                            )
                            place_buy_order(state, asset_id, "Spike detected")

                        # 下跌保护：当价格下跌超过指定阈值，若有持仓则立即卖出
                        if delta < -SPIKE_THRESHOLD_DOWN:
                            try:
                                positions_copy_local = state.get_positions()
                                position = find_position_by_asset(positions_copy_local, asset_id)
                                if position:
                                    logger.info(
                                        f"🛡️ Downward Spike Protection | Asset: {asset_id} | Delta: {delta:.2%} | Price: ${new_price:.4f}"
                                    )
                                    place_sell_order(state, asset_id, "Downward spike protection")
                            except Exception:
                                pass

                        # 即时卖出逻辑：当产生一定利润时（当前最佳卖价超过买入均价阈值），立即卖出
                        try:
                            positions_copy_local = state.get_positions()
                            position = find_position_by_asset(positions_copy_local, asset_id)
                            if position:
                                bid_data = get_max_bid_data(asset_id, allow_price_fallback=True)
                                current_sellable = None
                                if bid_data and bid_data.get("max_bid_price") is not None:
                                    current_sellable = float(bid_data.get("max_bid_price"))
                                else:
                                    # 回退到最新价格
                                    current_sellable = float(new_price)

                                avg_price = float(position.avg_price)
                                cash_profit = (current_sellable - avg_price) * float(position.shares)
                                pct_profit = ((current_sellable - avg_price) / avg_price) if avg_price > 0 else 0.0

                                if cash_profit >= CASH_PROFIT or pct_profit >= PCT_PROFIT:
                                    logger.info(
                                        f"🎯 Instant Take Profit | Asset: {asset_id} | Profit: ${cash_profit:.2f} ({pct_profit:.2%}) | Sellable=${current_sellable:.4f} | Avg=${avg_price:.4f}"
                                    )
                                    place_sell_order(state, asset_id, "Instant take profit")
                                # 即时止损：当损失超过阈值，立即卖出
                                if cash_profit <= CASH_LOSS or pct_profit <= PCT_LOSS:
                                    logger.info(
                                        f"⛔ Instant Stop Loss | Asset: {asset_id} | Loss: ${cash_profit:.2f} ({pct_profit:.2%}) | Sellable=${current_sellable:.4f} | Avg=${avg_price:.4f}"
                                    )
                                    place_sell_order(state, asset_id, "Instant stop loss")
                        except Exception:
                            # 防御：卖出逻辑异常不影响整体扫描
                            pass

                    except IndexError:
                        logger.debug(f"⏳ Building price history for {asset_id}")
                        continue
                    except Exception as e:
                        logger.error(f"❌ Error processing asset {asset_id}: {str(e)}")
                        continue
        except Exception as e:
            logger.error(f"❌ Error in detect_and_trade: {str(e)}")
            time.sleep(0.5)


def check_trade_exits(state: ThreadSafeState) -> None:
    last_log_time = time.time()

    while not state.is_shutdown():
        try:
            active_trades = state.get_active_trades()
            if active_trades:
                current_time = time.time()
                if current_time - last_log_time >= 30:
                    logger.info(
                        f"📈 Active Trades | Count: {len(active_trades)} | Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
                    )
                    last_log_time = current_time

            for asset_id, trade in active_trades.items():
                try:
                    positions_copy = state.get_positions()
                    position = find_position_by_asset(positions_copy, asset_id)
                    if not position:
                        continue

                    # 使用最优卖价（最佳买盘）作为可成交价格基准
                    bid_data = None
                    try:
                        bid_data = get_max_bid_data(asset_id, allow_price_fallback=True)
                    except Exception:
                        bid_data = None
                    if not bid_data or bid_data.get("max_bid_price") is None:
                        continue
                    best_bid_price = float(bid_data.get("max_bid_price"))
                    if best_bid_price <= 0:
                        continue

                    current_time = time.time()
                    last_traded = trade.entry_time
                    avg_price = position.avg_price
                    remaining_shares = position.shares
                    cash_profit = (best_bid_price - avg_price) * remaining_shares
                    pct_profit = (
                        ((best_bid_price - avg_price) / avg_price)
                        if avg_price > 0
                        else 0.0
                    )

                    if current_time - last_traded > HOLDING_TIME_LIMIT:
                        logger.info(
                            f"⏰ Holding Time Limit Hit | Asset: {asset_id} | Holding Time: {current_time - last_traded:.2f} seconds | BestBid=${best_bid_price:.4f} | Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                        place_sell_order(state, asset_id, "Holding time limit")
                        state.remove_active_trade(asset_id)
                        state.set_last_trade_time(time.time())

                    if cash_profit >= CASH_PROFIT or pct_profit > PCT_PROFIT:
                        logger.info(
                            f"🎯 Take Profit Hit | Asset: {asset_id} | Profit: ${cash_profit:.2f} ({pct_profit:.2%}) | BestBid=${best_bid_price:.4f} | Avg=${avg_price:.4f} | Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                        place_sell_order(state, asset_id, "Take profit")
                        state.remove_active_trade(asset_id)
                        state.set_last_trade_time(time.time())

                    if cash_profit <= CASH_LOSS or pct_profit < PCT_LOSS:
                        logger.info(
                            f"🔴 Stop Loss Hit | Asset: {asset_id} | Loss: ${cash_profit:.2f} ({pct_profit:.2%}) | BestBid=${best_bid_price:.4f} | Avg=${avg_price:.4f} | Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
                        )
                        place_sell_order(state, asset_id, "Stop loss")
                        state.remove_active_trade(asset_id)
                        state.set_last_trade_time(time.time())

                except Exception as e:
                    logger.error(
                        f"❌ Error checking trade exit for {asset_id}: {str(e)}"
                    )
                    continue
        except Exception as e:
            logger.error(f"❌ Error in check_trade_exits: {e}")
            time.sleep(1)


def _pair_processed_once(a: str, b: Optional[str]) -> bool:
    try:
        if not b:
            return True
        return a < b
    except Exception:
        return True


def detect_pair_sum_arbitrage(state: ThreadSafeState) -> None:
    logger.info("🔍 Starting pair sum arbitrage detection")
    last_log_time = time.time()
    scan_count = 0

    while not state.is_shutdown():
        try:
            if price_update_event.wait(timeout=0.2):
                price_update_event.clear()

                asset_ids = list(state._asset_pairs.keys())
                if not asset_ids:
                    logger.debug("⏳ Waiting for asset pairs to initialize...")
                    continue
                logger.debug(f"🔍 Scan #{scan_count} | Asset pairs: {asset_ids}")
                scan_count += 1
                now = time.time()
                if now - last_log_time >= 5:
                    logger.debug(
                        f"🔍 Arbitrage Scan | Scan #{scan_count} | Pairs: {len(asset_ids) // 2}"
                    )
                    last_log_time = now

                tokens_has_fetched: set[str] = set()
                active_trades = state.get_active_trades()
                for a in asset_ids:
                    if a in tokens_has_fetched:
                        continue
                    da = get_min_ask_data(a, allow_price_fallback=True)
                    pa = float(da.get("min_ask_price", 0)) if da else 0
                    tokens_has_fetched.add(a)
                    b = state.get_asset_pair(a)
                    db = get_min_ask_data(b, allow_price_fallback=True)
                    pb = float(db.get("min_ask_price", 0)) if db else 0
                    tokens_has_fetched.add(b)
                    if pa <= 0 or pb <= 0:
                        continue
                    s = pa + pb
                    logger.info(f"Pair {a}↔{b} | best_asks={pa} {pb}")
                    logger.info(f"Pair {a}↔{b} | best_asks_sum={s:.4f}")
                    # 实时打印最佳卖价（可卖出的最佳价格）及其汇总
                    if s < ARB_ENTRY_SUM_THRESHOLD:
                        # Require capacity for two trades
                        if len(active_trades) + 2 > MAX_CONCURRENT_TRADES:
                            logger.debug(
                                f"⛔ Skip entry for pair {a}↔{b}: active_trades would exceed limit"
                            )
                            continue

                        # Skip if either side is recently traded to avoid churn
                        if is_recently_bought(state, a) or is_recently_bought(state, b):
                            continue

                        logger.info(
                            f"🟡 Pair Mispricing Detected | {a}+{b} best_asks_sum={s:.4f} < {ARB_ENTRY_SUM_THRESHOLD:.4f} | buy both"
                        )

                        ok_a = place_buy_order(state, a, "Pair-sum arbitrage entry")
                        ok_b = place_buy_order(state, b, "Pair-sum arbitrage entry")

                        if ok_a and ok_b:
                            logger.info(
                                f"✅ Entered pair {a} & {b} | best_asks=({pa:.4f}, {pb:.4f}) sum={s:.4f}"
                            )
                        else:
                            logger.warning(
                                f"⚠️ Partial entry for pair {a} & {b} (ok_a={ok_a}, ok_b={ok_b}); will manage via exits"
                            )
        except Exception as e:
            logger.error(f"❌ Error in detect_pair_sum_arbitrage: {e}")
            time.sleep(1)


def check_pair_sum_arbitrage_exits(state: ThreadSafeState) -> None:
    last_log_time = time.time()

    while not state.is_shutdown():
        try:
            asset_ids = list(state._asset_pairs.keys())
            if not asset_ids:
                time.sleep(1)
                continue

            now = time.time()
            if now - last_log_time >= 30:
                logger.info(
                    f"📈 Arbitrage Exit Check | Pairs: {len(asset_ids) // 2} | Time: {time.strftime('%Y-%m-%d %H:%M:%S')}"
                )
                last_log_time = now

            # 批量抓取订单簿，提升出场判断速度（带缓存与重试）
            tokens_to_fetch = set()
            for a in asset_ids:
                b = state.get_asset_pair(a)
                if not _pair_processed_once(a, b):
                    continue
                if not b:
                    continue
                tokens_to_fetch.add(a)
                tokens_to_fetch.add(b)

            books_map = {}
            if tokens_to_fetch:
                tokens_list = list(tokens_to_fetch)
                cache_map, cache_ts = state.get_order_books_cache()
                use_cache = False
                if ORDERBOOK_CACHE_ENABLED and state.is_order_books_cache_valid(
                    ORDERBOOK_CACHE_TTL
                ):
                    if all(t in cache_map for t in tokens_list):
                        books_map = {tid: cache_map.get(tid) for tid in tokens_list}
                        use_cache = True
                        age_ms = (time.time() - cache_ts) * 1000.0
                        logger.debug(
                            f"📚 Using cached order books (exit) | age={age_ms:.0f}ms | tokens={len(tokens_list)}"
                        )
                if not use_cache:
                    try:
                        books_list = get_order_books_with_retry(tokens_list)
                        books_map = {
                            tid: book for tid, book in zip(tokens_list, books_list)
                        }
                        if ORDERBOOK_CACHE_ENABLED:
                            state.set_order_books_cache(books_map)
                    except Exception as e:
                        logger.warning(
                            f"Batch get_order_books retry exhausted (exit): {e}"
                        )

            for a in asset_ids:
                b = state.get_asset_pair(a)
                if not _pair_processed_once(a, b):
                    continue
                if not b:
                    continue

                try:
                    # 优先用批量订单簿最优买价，缺失时降级
                    qa = None
                    qb = None
                    book_a = books_map.get(a)
                    if book_a and getattr(book_a, "bids", None):
                        try:
                            qa = max(
                                (
                                    float(x.price)
                                    for x in book_a.bids
                                    if x and hasattr(x, "price")
                                ),
                                default=None,
                            )
                        except Exception:
                            qa = None
                    book_b = books_map.get(b)
                    if book_b and getattr(book_b, "bids", None):
                        try:
                            qb = max(
                                (
                                    float(x.price)
                                    for x in book_b.bids
                                    if x and hasattr(x, "price")
                                ),
                                default=None,
                            )
                        except Exception:
                            qb = None

                    if qa is None:
                        da = get_max_bid_data(a, allow_price_fallback=True)
                        qa = float(da.get("max_bid_price", 0)) if da else 0
                    if qb is None:
                        db = get_max_bid_data(b, allow_price_fallback=True)
                        qb = float(db.get("max_bid_price", 0)) if db else 0

                    if qa is None:
                        da = get_max_bid_data(a, allow_price_fallback=True)
                        qa = float(da.get("max_bid_price", 0)) if da else 0
                    if qb is None:
                        db = get_max_bid_data(b, allow_price_fallback=True)
                        qb = float(db.get("max_bid_price", 0)) if db else 0

                    if qa <= 0 or qb <= 0:
                        continue
                    s = qa + qb

                    # Both sides should have positions to close as a pair
                    positions = state.get_positions()
                    pos_a = find_position_by_asset(positions, a)
                    pos_b = find_position_by_asset(positions, b)

                    if not pos_a or not pos_b:
                        continue

                    # 新退出条件：当前卖出价之和 > 买入均价之和
                    entry_sum = float(getattr(pos_a, "avg_price", 0) or 0) + float(
                        getattr(pos_b, "avg_price", 0) or 0
                    )
                    if s > entry_sum:
                        logger.info(
                            f"🎯 Pair Exit | {a}+{b} sell_sum={s:.4f} > entry_sum={entry_sum:.4f} | selling both"
                        )
                        sa = place_sell_order(state, a, "Pair-sum arbitrage exit")
                        sb = place_sell_order(state, b, "Pair-sum arbitrage exit")
                        logger.info(
                            f"Pair exit results for {a} & {b}: sell_a={sa}, sell_b={sb}"
                        )

                except Exception as e:
                    logger.error(f"❌ Error checking arbitrage exit for {a}↔{b}: {e}")
                    continue

            time.sleep(1)

        except Exception as e:
            logger.error(f"❌ Error in check_pair_sum_arbitrage_exits: {e}")
            time.sleep(1)


def print_positions_realtime(state: ThreadSafeState) -> None:
    """实时打印当前持仓快照。

    - 绑定价格更新事件，节流输出（默认每2秒最多一次）。
    - 展示每条持仓的：事件、方向、资产ID、数量、均价、现价、当前价值、未实现收益与百分比、已实现收益。
    - 聚合显示总当前价值、总未实现/已实现盈亏。
    """
    last_print_time = 0.0
    throttle_seconds = float(POSITIONS_LOG_THROTTLE_SECS)

    def _print_snapshot() -> None:
        positions_map = state.get_positions()
        total_positions = sum(len(v) for v in positions_map.values())

        if total_positions == 0:
            logger.info("📒 持仓快照 | 当前无持仓")
            return

        lines = []
        agg_current_value = 0.0
        agg_unrealized_pnl = 0.0
        agg_realized_pnl = 0.0

        for event_id, positions in positions_map.items():
            for p in positions:
                try:
                    agg_current_value += float(p.current_value)
                    agg_unrealized_pnl += float(p.pnl)
                    agg_realized_pnl += float(p.realized_pnl)
                    # 读取该资产的最优卖价（最佳买盘）
                    best_bid_price_str = "NA"
                    try:
                        bid_data = get_max_bid_data(p.asset, allow_price_fallback=True)
                        if bid_data and bid_data.get("max_bid_price") is not None:
                            best_bid_price_val = float(bid_data.get("max_bid_price"))
                            if best_bid_price_val > 0:
                                best_bid_price_str = f"${best_bid_price_val:.4f}"
                    except Exception:
                        # 忽略最优卖价查询异常，保证整体输出不中断
                        pass
                    lines.append(
                        f" • {p.eventslug} [{p.outcome}] ({p.asset}) | 数量={p.shares:.2f} 均价=${p.avg_price:.4f} 现价=${p.current_price:.4f} 最优卖价={best_bid_price_str} 价值=${p.current_value:.2f} PnL=${p.pnl:.2f} ({p.percent_pnl:.2%}) 已实现=${p.realized_pnl:.2f}"
                    )
                except Exception:
                    # 防御型：单条异常不影响整体输出
                    continue

        header = f"📒 持仓快照 | 数量={total_positions} | 总价值=${agg_current_value:.2f} | 未实现PnL=${agg_unrealized_pnl:.2f} | 已实现PnL=${agg_realized_pnl:.2f}"
        logger.info(header)
        for ln in lines:
            logger.info(ln)

    while not state.is_shutdown():
        try:
            # 事件触发优先：有价格更新立即尝试打印（节流）
            triggered = price_update_event.wait(timeout=1.0)
            # 注意：不要在打印线程中清除事件，避免与检测线程竞争导致其错过触发

            now = time.time()
            if now - last_print_time >= throttle_seconds:
                last_print_time = now
                _print_snapshot()

        except Exception as e:
            logger.error(f"❌ 持仓打印线程错误: {e}")
            time.sleep(1)
