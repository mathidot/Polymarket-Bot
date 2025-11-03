import os
import csv
import time
import argparse
import logging
from typing import List, Tuple, Optional, Dict

from log import setup_logging
# 确保在导入依赖前启用模拟模式（影响 config 读取）
os.environ.setdefault("simulation_mode", "true")
from state import ThreadSafeState, price_update_event
from threads import ThreadManager
import trading as trading_mod
import strategy

# Utilities for CSV parsing
def _normalize_header(name: str) -> str:
    return name.strip().strip('"').strip("'").lower()


def load_price_series(csv_path: str) -> List[Tuple[int, float]]:
    """
    Load a single price series from CSV with columns:
    - Date (UTC) [optional]
    - Timestamp (UTC) [preferred]
    - Price (required)

    Returns list of (timestamp_int, price_float), sorted by timestamp.
    """
    series: List[Tuple[int, float]] = []
    with open(csv_path, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = { _normalize_header(h): h for h in reader.fieldnames or [] }
        ts_key = headers.get("timestamp (utc)") or headers.get("timestamp")
        date_key = headers.get("date (utc)") or headers.get("date")
        price_key = headers.get("price") or headers.get("yes") or headers.get("priceyes")

        if price_key is None:
            raise ValueError("CSV 缺少价格列，如 'Price' 或 'PriceYes'.")

        for row in reader:
            price_str = row.get(price_key, "").strip().strip('"')
            if not price_str:
                continue
            try:
                price = float(price_str)
            except ValueError:
                continue

            ts_val: Optional[int] = None
            if ts_key:
                raw = row.get(ts_key, "").strip().strip('"')
                if raw:
                    try:
                        ts_val = int(float(raw))
                    except ValueError:
                        ts_val = None
            if ts_val is None and date_key:
                # Fallback: attempt to parse date; assume "%m-%d-%Y %H:%M"
                # Users should prefer Timestamp (UTC). Here we skip complex parsing.
                # If date only, we cannot reliably parse without datetime; skip.
                pass

            if ts_val is None:
                # Skip rows without usable timestamp
                continue

            series.append((ts_val, price))

    # sort by timestamp
    series.sort(key=lambda x: x[0])
    return series


def load_dual_price_series(csv_yes: str, csv_no: str) -> List[Tuple[int, float, float]]:
    """
    Load YES and NO price series from two CSV files and align by timestamp.
    If a timestamp exists in only one file, the missing side is complemented as 1 - price.
    Returns list of (timestamp_int, yes_price, no_price) sorted by timestamp.
    """
    yes_series = load_price_series(csv_yes)
    no_series = load_price_series(csv_no)

    yes_map: Dict[int, float] = { ts: p for ts, p in yes_series }
    no_map: Dict[int, float] = { ts: p for ts, p in no_series }
    all_ts = sorted(set(yes_map.keys()) | set(no_map.keys()))

    aligned: List[Tuple[int, float, float]] = []
    for ts in all_ts:
        yp = yes_map.get(ts)
        np = no_map.get(ts)
        if yp is None and np is None:
            continue
        if yp is None and np is not None:
            yp = max(0.0, min(1.0, 1.0 - np))
        if np is None and yp is not None:
            np = max(0.0, min(1.0, 1.0 - yp))
        aligned.append((ts, float(yp), float(np)))

    return aligned


class _MockOrderEntry:
    def __init__(self, price: float, size: float) -> None:
        self.price = float(price)
        self.size = float(size)


class _MockOrderBook:
    def __init__(self, bids: List[_MockOrderEntry], asks: List[_MockOrderEntry]) -> None:
        self.bids = bids
        self.asks = asks


def run_backtest(
    csv: Optional[str] = None,
    csv_yes: Optional[str] = None,
    csv_no: Optional[str] = None,
    granularity: str = "hour",
    sleep_sec: float = 0.02,
    start_usdc: Optional[float] = None,
):
    os.environ["simulation_mode"] = "true"

    logger = setup_logging()
    logger.info("🔧 回测启动 | 粒度=%s", granularity)

    state = ThreadSafeState()
    if start_usdc is not None:
        try:
            state.set_sim_usdc_balance(float(start_usdc))
        except Exception:
            logger.warning("⚠️ 起始 USDC 金额设置失败，沿用默认值")
    tm = ThreadManager(state)

    # 在本函数内定义资产配对构造器，避免外部依赖
    def build_yes_no_asset_pair(local_state: ThreadSafeState, base_symbol: str = "BT", eventslug: str = "BacktestEvent") -> Tuple[str, str]:
        yes_id = f"{base_symbol}_YES"
        no_id = f"{base_symbol}_NO"
        local_state.add_asset_pair(yes_id, no_id)
        local_state.set_asset_meta(yes_id, eventslug, "YES")
        local_state.set_asset_meta(no_id, eventslug, "NO")
        return yes_id, no_id

    # 构建资产配对
    yes_id, no_id = build_yes_no_asset_pair(state, base_symbol="BT")
    # 在回测中为 trading 模块打补丁，提供离线订单簿与价格
    _current_mid: Dict[str, float] = {yes_id: 0.5, no_id: 0.5}

    def _mock_get_order_book(asset: str) -> _MockOrderBook:
        m = float(_current_mid.get(asset, 0.5))
        bids = [
            _MockOrderEntry(price=max(0.0, m - 0.02), size=200.0),
            _MockOrderEntry(price=max(0.0, m - 0.01), size=100.0),
        ]
        asks = [
            _MockOrderEntry(price=min(1.0, m + 0.01), size=100.0),
            _MockOrderEntry(price=min(1.0, m + 0.02), size=200.0),
        ]
        return _MockOrderBook(bids=bids, asks=asks)

    def _mock_get_price(asset: str, side: str) -> float:
        m = float(_current_mid.get(asset, 0.5))
        if str(side).upper() == "BUY":
            return float(min(1.0, max(0.0, m + 0.01)))
        return float(min(1.0, max(0.0, m - 0.01)))

    # 替换 trading 模块内引用的函数（strategy/trading 内部使用该名称）
    trading_mod.get_order_book = _mock_get_order_book  # type: ignore
    trading_mod.get_price = _mock_get_price  # type: ignore

    # Load series
    ticks_dual: Optional[List[Tuple[int, float, float]]] = None
    ticks_single: Optional[List[Tuple[int, float]]] = None

    if csv_yes and csv_no:
        ticks_dual = load_dual_price_series(csv_yes, csv_no)
        logger.info("📥 加载双序列：YES=%s | NO=%s | 条数=%d", csv_yes, csv_no, len(ticks_dual))
    elif csv:
        ticks_single = load_price_series(csv)
        logger.info("📥 加载单序列：CSV=%s | 条数=%d", csv, len(ticks_single))
    else:
        raise ValueError("请提供 --csv 或同时提供 --csv_yes 与 --csv_no")

    # 启动策略相关线程（与 main.py 保持一致的目标函数）
    tm.start_thread("detect_trade", strategy.detect_and_trade)
    tm.start_thread("check_exits", strategy.check_trade_exits)
    tm.start_thread("positions_log", strategy.print_positions_realtime)

    # Feed prices
    if ticks_dual is not None:
        for ts, yp, np in ticks_dual:
            # 更新离线中间价
            _current_mid[yes_id] = float(max(0.0, min(1.0, yp)))
            _current_mid[no_id] = float(max(0.0, min(1.0, np)))

            # 写入价格到状态历史，供 pricing.get_current_price 使用
            state.add_price(yes_id, ts, float(yp), "BacktestEvent", "YES")
            state.add_price(no_id, ts, float(np), "BacktestEvent", "NO")

            # 触发事件，唤醒策略线程
            price_update_event.set()
            time.sleep(sleep_sec)
    else:
        for ts, yp in ticks_single or []:
            np = max(0.0, min(1.0, 1.0 - yp))
            _current_mid[yes_id] = float(max(0.0, min(1.0, yp)))
            _current_mid[no_id] = float(max(0.0, min(1.0, np)))

            state.add_price(yes_id, ts, float(yp), "BacktestEvent", "YES")
            state.add_price(no_id, ts, float(np), "BacktestEvent", "NO")

            price_update_event.set()
            time.sleep(sleep_sec)

    # 强制清仓：确保回测结束时无持仓
    try:
        positions_map_liq = state.get_positions()
        for _, arr in positions_map_liq.items():
            for p in list(arr):
                try:
                    shares = float(getattr(p, "shares", 0) or 0)
                    if shares <= 0:
                        continue
                    sell_price = None
                    try:
                        from trading import get_max_bid_data
                        bid = get_max_bid_data(p.asset, allow_price_fallback=True)
                        if bid and bid.get("max_bid_price") is not None:
                            sell_price = float(bid.get("max_bid_price"))
                    except Exception:
                        sell_price = None
                    if sell_price is None or sell_price <= 0:
                        sell_price = float(
                            _current_mid.get(
                                p.asset,
                                float(getattr(p, "current_price", 0) or getattr(p, "avg_price", 0) or 0),
                            )
                        )
                    if sell_price <= 0:
                        sell_price = float(getattr(p, "avg_price", 0) or 0)
                    proceeds = shares * sell_price
                    state.adjust_sim_usdc_balance(proceeds)
                    ok = state.reduce_sim_position(p.asset, shares, sell_price)
                    if ok:
                        logger.info(
                            f"🔚 [强制清仓] SELL {shares:.4f} {p.asset} at ${sell_price:.4f}"
                        )
                    else:
                        logger.warning(f"⚠️ 强制清仓失败 | {p.asset}")
                except Exception as e:
                    logger.error(f"❌ 强制清仓异常 | {getattr(p, 'asset', 'NA')}: {e}")
    except Exception as e:
        logger.error(f"❌ 强制清仓阶段失败: {e}")

    # 汇总结果（清仓后）
    positions_map = state.get_positions()
    total_positions = sum(len(v) for v in positions_map.values())
    try:
        usdc = state.get_sim_usdc_balance()
    except Exception:
        usdc = 0.0
    agg_current = 0.0
    agg_realized = 0.0
    agg_unrealized = 0.0
    for _, arr in positions_map.items():
        for p in arr:
            try:
                agg_current += float(getattr(p, "current_value", 0) or 0)
                agg_realized += float(getattr(p, "realized_pnl", 0) or 0)
                agg_unrealized += float(getattr(p, "pnl", 0) or 0)
            except Exception:
                continue

    logger.info(
        "🧪 回测完成 | 持仓数=%d | USDC=%.2f | 当前价值=%.2f | 已实现PnL=%.2f | 未实现PnL=%.2f",
        total_positions, usdc, agg_current, agg_realized, agg_unrealized,
    )

    # 终止所有线程：先通知状态关闭，再唤醒等待，再等待线程退出
    try:
        state.shutdown()
    except Exception:
        pass
    try:
        price_update_event.set()
    except Exception:
        pass
    try:
        tm.stop()
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Polymarket Spike Bot 回测模块")
    parser.add_argument("--csv", type=str, default=None, help="单序列CSV文件，包含 Price 列（用于 YES）")
    parser.add_argument("--csv_yes", type=str, default=None, help="YES 序列 CSV 文件")
    parser.add_argument("--csv_no", type=str, default=None, help="NO 序列 CSV 文件")
    parser.add_argument("--granularity", type=str, default="hour", choices=["minute", "hour", "day"], help="数据粒度标签")
    parser.add_argument("--sleep", type=float, default=0.02, help="每个tick的处理休眠秒数")
    parser.add_argument("--start_usdc", type=float, default=None, help="模拟模式下的起始 USDC 余额，例如 5000")
    args = parser.parse_args()

    run_backtest(
        csv=args.csv,
        csv_yes=args.csv_yes,
        csv_no=args.csv_no,
        granularity=args.granularity,
        sleep_sec=args.sleep,
        start_usdc=args.start_usdc,
    )


if __name__ == "__main__":
    main()