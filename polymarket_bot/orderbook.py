from typing import Optional, Dict, Any
from .client import client
from .logger import logger

def get_min_ask_data(asset: str) -> Optional[Dict[str, Any]]:
    try:
        order = client.get_order_book(asset)
        if order.asks:
            buy_price = client.get_price(asset, "BUY")
            min_ask_price = order.asks[-1].price
            min_ask_size = order.asks[-1].size
            return {"buy_price": buy_price, "min_ask_price": min_ask_price, "min_ask_size": min_ask_size}
        else:
            return None
    except Exception as e:
        logger.error(f"❌ Failed to get ask data for {asset}: {str(e)}")
        return None

def get_max_bid_data(asset: str) -> Optional[Dict[str, Any]]:
    try:
        order = client.get_order_book(asset)
        if order.bids:
            sell_price = client.get_price(asset, "SELL")
            max_bid_price = order.bids[-1].price
            max_bid_size = order.bids[-1].size
            return {"sell_price": sell_price, "max_bid_price": max_bid_price, "max_bid_size": max_bid_size}
        else:
            return None
    except Exception as e:
        logger.error(f"❌ Failed to get bid data for {asset}: {str(e)}")
        return None
