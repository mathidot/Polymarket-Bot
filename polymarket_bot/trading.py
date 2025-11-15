import time
from typing import Optional, Dict, Any
from py_clob_client.clob_types import MarketOrderArgs, OrderType, BalanceAllowanceParams, AssetType
from py_clob_client.order_builder.constants import BUY, SELL
from .client import get_client, web3
from .logger import logger
from .exceptions import TradingError
from .types import TradeInfo
from .types import TradeType
from .config import USE_CHAIN_BALANCE_CHECK, USDC_CONTRACT_ADDRESS, YOUR_PROXY_WALLET
from .config import MAX_RETRIES, BASE_DELAY, MAX_CONCURRENT_TRADES, MIN_LIQUIDITY_REQUIREMENT, SLIPPAGE_TOLERANCE, TRADE_UNIT
from .orderbook import get_min_ask_data, get_max_bid_data
from .state import ThreadSafeState
from .pricing import get_current_price

def check_usdc_allowance(required_amount: float) -> bool:
    try:
        collateral = client.get_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
        current_balance = collateral.get('balance', 0)
        try:
            current_balance = float(current_balance)
        except (TypeError, ValueError):
            current_balance = 0.0
        try:
            required = float(required_amount)
        except (TypeError, ValueError):
            required = 0.0
        if current_balance >= required:
            return True
    except Exception as e:
        raise TradingError(f"Failed to update USDC allowance: {e}")
    return False

def place_buy_order(state: ThreadSafeState, asset: str, reason: str) -> bool:
    try:
        active_trades = state.get_active_trades()
        if len(active_trades) >= MAX_CONCURRENT_TRADES:
            return False
        if USE_CHAIN_BALANCE_CHECK:
            usdc_contract = web3.eth.contract(address=USDC_CONTRACT_ADDRESS, abi=[{"constant": True, "inputs": [{"name": "account", "type": "address"}],"name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}],"payable": False, "stateMutability": "view", "type": "function"}])
            usdc_balance = usdc_contract.functions.balanceOf(YOUR_PROXY_WALLET).call() / 10**6
            if not usdc_balance:
                return False
        max_retries = MAX_RETRIES
        base_delay = BASE_DELAY
        for attempt in range(max_retries):
            try:
                current_price = get_current_price(state, asset)
                if current_price is None:
                    raise TradingError(f"Failed to get current price for {asset}")
                cli = get_client()
                if cli is None:
                    logger.error("❌ ClobClient unavailable, skipping BUY")
                    return False
                min_ask_data = get_min_ask_data(asset)
                if min_ask_data is None:
                    return False
                min_ask_price = float(min_ask_data["min_ask_price"])
                min_ask_size = float(min_ask_data["min_ask_size"])
                if min_ask_size * min_ask_price < MIN_LIQUIDITY_REQUIREMENT:
                    return False
                if min_ask_price - current_price > SLIPPAGE_TOLERANCE:
                    return False
                amount_in_dollars = min(TRADE_UNIT, min_ask_size * min_ask_price)
                logger.info(f"📝 Buy Reason: {reason} | Asset: {asset} | Current: ${current_price:.4f} | Ask: ${min_ask_price:.4f} | Liquidity: ${min_ask_size * min_ask_price:.2f} | Slippage: {(min_ask_price - current_price):.4f} | Amount: {amount_in_dollars:.4f}")
                if not check_usdc_allowance(amount_in_dollars):
                    raise TradingError(f"Failed to ensure USDC allowance for {asset}")
                order_args = MarketOrderArgs(token_id=str(asset), amount=float(amount_in_dollars), side=BUY)
                signed_order = cli.create_market_order(order_args)
                response = cli.post_order(signed_order, OrderType.FOK)
                if response.get("success"):
                    filled = response.get("data", {}).get("filledAmount", amount_in_dollars)
                    logger.info(f"🛒 BUY filled: {filled:.4f} shares of {asset} at ${min_ask_price:.4f} | Reason: {reason}")
                    trade_info = TradeInfo(entry_price=min_ask_price, entry_time=time.time(), amount=amount_in_dollars, bot_triggered=True, shares=float(filled))
                    state.update_recent_trade(asset, TradeType.BUY)
                    state.add_active_trade(asset, trade_info)
                    state.set_last_trade_time(time.time())
                    return True
                else:
                    error_msg = response.get("error", "Unknown error")
                    raise TradingError(f"Failed to place BUY order for {asset}: {error_msg}")
            except TradingError as e:
                logger.error(f"❌ Trading error in BUY order for {asset}: {str(e)}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(base_delay * (2 ** attempt))
            except Exception as e:
                logger.error(f"❌ Unexpected error in BUY order for {asset}: {str(e)}")
                if attempt == max_retries - 1:
                    raise TradingError(f"Failed to process BUY order after {max_retries} attempts: {e}")
                time.sleep(base_delay * (2 ** attempt))
        return False
    except Exception as e:
        logger.error(f"❌ Error placing BUY order for {asset}: {str(e)}", exc_info=True)
        raise

def place_sell_order(state: ThreadSafeState, asset: str, reason: str) -> bool:
    try:
        max_retries = MAX_RETRIES
        base_delay = BASE_DELAY
        for attempt in range(max_retries):
            try:
                current_price = get_current_price(state, asset)
                if current_price is None:
                    raise TradingError(f"Failed to get current price for {asset}")
                cli = get_client()
                if cli is None:
                    logger.error("❌ ClobClient unavailable, skipping SELL")
                    return False
                max_bid_data = get_max_bid_data(asset)
                if max_bid_data is None:
                    return False
                max_bid_price = float(max_bid_data["max_bid_price"])
                max_bid_size = float(max_bid_data["max_bid_size"])
                active = state.get_active_trades()
                balance = 0.0
                avg_price = 0.0
                if asset in active:
                    balance = float(getattr(active[asset], "shares", 0.0))
                    avg_price = float(getattr(active[asset], "entry_price", 0.0))
                sell_amount_in_shares = balance
                if sell_amount_in_shares < 1:
                    continue
                logger.info(f"📝 Sell Reason: {reason} | Asset: {asset} | Current: ${current_price:.4f} | Bid: ${max_bid_price:.4f} | Amount: {sell_amount_in_shares:.4f}")
                order_args = MarketOrderArgs(token_id=str(asset), amount=float(sell_amount_in_shares), side=SELL)
                signed_order = cli.create_market_order(order_args)
                response = cli.post_order(signed_order, OrderType.FOK)
                if response.get("success"):
                    filled = response.get("data", {}).get("filledAmount", sell_amount_in_shares)
                    logger.info(f"🛒 SELL filled: {filled:.4f} shares of {asset} at ${max_bid_price:.4f} | Reason: {reason}")
                    state.update_recent_trade(asset, TradeType.SELL)
                    state.remove_active_trade(asset)
                    state.set_last_trade_time(time.time())
                    return True
                else:
                    error_msg = response.get("error", "Unknown error")
                    raise TradingError(f"Failed to place SELL order for {asset}: {error_msg}")
            except TradingError as e:
                logger.error(f"❌ Trading error in SELL order for {asset}: {str(e)}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(base_delay * (2 ** attempt))
            except Exception as e:
                logger.error(f"❌ Unexpected error in SELL order for {asset}: {str(e)}")
                if attempt == max_retries - 1:
                    raise TradingError(f"Failed to process SELL order after {max_retries} attempts: {e}")
                time.sleep(base_delay * (2 ** attempt))
        return False
    except Exception as e:
        logger.error(f"❌ Error placing SELL order for {asset}: {str(e)}")
        raise
