from typing import Optional, Dict, Any
from math import isfinite
from .client import get_client
from .logger import logger

def get_min_ask_data(asset: str) -> Optional[Dict[str, Any]]:
    """获取最小卖价与对应数量。

    Args:
        asset: 资产 token ID。

    Returns:
        包含 `buy_price/min_ask_price/min_ask_size` 的字典，或 None（不可用）。
    """
    try:
        cli = get_client()
        if cli is None:
            logger.error("❌ ClobClient unavailable for ask data")
            return None
        order = cli.get_order_book(asset)
        if order.asks:
            buy_price = cli.get_price(asset, "BUY")
            min_ask_price = order.asks[-1].price
            min_ask_size = order.asks[-1].size
            return {"buy_price": buy_price, "min_ask_price": min_ask_price, "min_ask_size": min_ask_size}
        else:
            return None
    except Exception as e:
        logger.error(f"❌ Failed to get ask data for {asset}: {str(e)}")
        return None

def get_max_bid_data(asset: str) -> Optional[Dict[str, Any]]:
    """获取最大买价与对应数量。

    Args:
        asset: 资产 token ID。

    Returns:
        包含 `sell_price/max_bid_price/max_bid_size` 的字典，或 None（不可用）。
    """
    try:
        cli = get_client()
        if cli is None:
            logger.error("❌ ClobClient unavailable for bid data")
            return None
        order = cli.get_order_book(asset)
        if order.bids:
            sell_price = cli.get_price(asset, "SELL")
            max_bid_price = order.bids[-1].price
            max_bid_size = order.bids[-1].size
            return {"sell_price": sell_price, "max_bid_price": max_bid_price, "max_bid_size": max_bid_size}
        else:
            return None
    except Exception as e:
        logger.error(f"❌ Failed to get bid data for {asset}: {str(e)}")
        return None

def estimate_vwap_for_amount(asset: str, side: str, usd_amount: float, max_levels: int = 5) -> Optional[Dict[str, float]]:
    """估算目标美元量的 VWAP 与可成交深度。

    按最优至次优逐档聚合，直至达到 `usd_amount` 或耗尽 `max_levels`。

    Args:
        asset: 资产 token ID。
        side: 交易方向，"BUY" 或 "SELL"。
        usd_amount: 目标美元成交量。
        max_levels: 聚合档位上限。

    Returns:
        包含 `vwap/available_usd/levels_used` 的字典；不可用返回 None。
    """
    try:
        cli = get_client()
        if cli is None:
            logger.error("❌ ClobClient unavailable for VWAP estimate")
            return None
        ob = cli.get_order_book(asset)
        levels = []
        if side.upper() == "BUY":
            levels = ob.asks or []
        else:
            levels = ob.bids or []
        if not levels:
            return None
        total_shares = 0.0
        total_cost = 0.0
        used_usd = 0.0
        count = 0
        for lvl in levels[:max_levels]:
            try:
                px = float(lvl.price)
                sz = float(lvl.size)
            except (TypeError, ValueError):
                continue
            if not isfinite(px) or not isfinite(sz) or px <= 0 or sz <= 0:
                continue
            level_usd = px * sz
            take_usd = min(usd_amount - used_usd, level_usd)
            if take_usd <= 0:
                break
            take_shares = take_usd / px
            total_shares += take_shares
            total_cost += take_usd
            used_usd += take_usd
            count += 1
            if used_usd >= usd_amount:
                break
        if total_shares <= 0 or total_cost <= 0:
            return None
        vwap = total_cost / total_shares
        return {"vwap": vwap, "available_usd": used_usd, "levels_used": float(count)}
    except Exception as e:
        logger.error(f"❌ Failed VWAP estimate for {asset}: {str(e)}")
        return None
