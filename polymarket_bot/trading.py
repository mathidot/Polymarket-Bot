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
from .config import SIM_MODE, SIM_START_USDC
from .orderbook import get_min_ask_data, get_max_bid_data
from .state import ThreadSafeState
from .pricing import get_current_price
from .orderbook import estimate_vwap_for_amount

def check_usdc_allowance(state: ThreadSafeState, required_amount: float) -> bool:
    """检查 USDC 余额/额度是否满足下单金额。

    Args:
        required_amount: 需要的美元金额。

    Returns:
        True 表示额度充足；False 表示客户端不可用或额度不足。

    Raises:
        TradingError: 客户端调用异常。
    """
    try:
        if SIM_MODE and state.is_simulation_enabled():
            try:
                return state.get_sim_balance() >= float(required_amount)
            except Exception:
                return False
        cli = get_client()
        if cli is None:
            return False
        collateral = cli.get_balance_allowance(params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL))
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
    """执行买入订单（FOK）。
    Args:
        state: 线程安全状态对象。
        asset: 资产 token ID。
        reason: 买入理由，用于日志。

    Returns:
        True 表示下单成功；False 表示跳过或失败。
    """
    try:
        active_trades = state.get_active_trades()
        if len(active_trades) >= MAX_CONCURRENT_TRADES:
            return False
        current_price = get_current_price(state, asset)
        if current_price is None:
            raise TradingError(f"Failed to get current price for {asset}")
        if SIM_MODE and state.is_simulation_enabled():
            ask_data = get_min_ask_data(asset)
            if ask_data is None:
                return False
            min_ask_price = float(ask_data["min_ask_price"])
            min_ask_size = float(ask_data["min_ask_size"])
            liquidity_usd = min_ask_size * min_ask_price
            if liquidity_usd < MIN_LIQUIDITY_REQUIREMENT:
                logger.info(f"⏭️ Skip BUY {asset}: ask liquidity ${liquidity_usd:.4f} < min ${MIN_LIQUIDITY_REQUIREMENT:.4f}")
                return False
            max_shares_by_unit = TRADE_UNIT / min_ask_price if min_ask_price > 0 else 0.0
            max_shares_by_balance = state.get_sim_balance() / min_ask_price if min_ask_price > 0 else 0.0
            shares_to_buy = min(min_ask_size, max_shares_by_unit, max_shares_by_balance)
            if shares_to_buy <= 0:
                return False
            amount_in_dollars = shares_to_buy * min_ask_price
            logger.info(f"📝 Buy Reason: {reason} | Asset: {asset} | BestAsk: ${min_ask_price:.4f} | AskSize: {min_ask_size:.4f} | SharesToBuy: {shares_to_buy:.4f} | AmountUSD: {amount_in_dollars:.4f}")
            state.adjust_sim_balance(-amount_in_dollars)
            try:
                logger.info(f"💼 SIM Balance: ${state.get_sim_balance():.4f}")
            except Exception:
                pass
            trade_info = TradeInfo(entry_price=min_ask_price, entry_time=time.time(), amount=amount_in_dollars, bot_triggered=True, shares=float(shares_to_buy))
            state.update_recent_trade(asset, TradeType.BUY)
            state.add_active_trade(asset, trade_info)
            state.set_last_trade_time(time.time())
            logger.info(f"🛒 SIM BUY filled: {shares_to_buy:.4f} shares of {asset} at ${min_ask_price:.4f} | Reason: {reason}")
            return True
        cli = get_client()
        if cli is None:
            logger.error("❌ ClobClient unavailable, skipping BUY")
            return False
        # 简化逻辑：买入最优卖价，数量受卖家可卖量与 trade_unit 限制
        ask_data = get_min_ask_data(asset)
        if ask_data is None:
            return False
        min_ask_price = float(ask_data["min_ask_price"])
        min_ask_size = float(ask_data["min_ask_size"])
        liquidity_usd = min_ask_size * min_ask_price
        if liquidity_usd < MIN_LIQUIDITY_REQUIREMENT:
            logger.info(f"⏭️ Skip BUY {asset}: ask liquidity ${liquidity_usd:.4f} < min ${MIN_LIQUIDITY_REQUIREMENT:.4f}")
            return False
        # 按 trade_unit 限制美元金额；以卖家可卖量限制份额
        max_shares_by_unit = TRADE_UNIT / min_ask_price if min_ask_price > 0 else 0.0
        shares_to_buy = min(min_ask_size, max_shares_by_unit)
        if shares_to_buy <= 0:
            return False
        amount_in_dollars = shares_to_buy * min_ask_price
        logger.info(f"📝 Buy Reason: {reason} | Asset: {asset} | BestAsk: ${min_ask_price:.4f} | AskSize: {min_ask_size:.4f} | SharesToBuy: {shares_to_buy:.4f} | AmountUSD: {amount_in_dollars:.4f}")
        if not check_usdc_allowance(state, amount_in_dollars):
            logger.warning(f"⚠️ Insufficient USDC balance/allowance for {asset} | Required: ${amount_in_dollars:.4f}")
            return False
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
            logger.warning(f"Failed to place BUY order for {asset}: {error_msg}")
        return False
    except Exception as e:
        logger.error(f"❌ Error placing BUY order for {asset}: {str(e)}", exc_info=True)
        raise

def place_sell_order(state: ThreadSafeState, asset: str, reason: str) -> bool:
    """执行卖出订单（FOK）。

    以活跃交易中的 shares 为基础，按 VWAP 将卖出份额上限限制为 `trade_unit/vwap`；
    滑点超限或深度不足则跳过。

    Args:
        state: 线程安全状态对象。
        asset: 资产 token ID。
        reason: 卖出理由，用于日志。

    Returns:
        True 表示下单成功；False 表示跳过或失败。
    """
    try:
        max_retries = MAX_RETRIES
        base_delay = BASE_DELAY
        for attempt in range(max_retries):
            try:
                current_price = get_current_price(state, asset)
                if current_price is None:
                    raise TradingError(f"Failed to get current price for {asset}")
                if SIM_MODE and state.is_simulation_enabled():
                    vwap = float(current_price)
                else:
                    cli = get_client()
                    if cli is None:
                        logger.error("❌ ClobClient unavailable, skipping SELL")
                        return False
                    est = estimate_vwap_for_amount(asset, "SELL", TRADE_UNIT, max_levels=5)
                    if est is None:
                        return False
                    vwap = float(est.get("vwap", 0.0))
                active = state.get_active_trades()
                balance = 0.0
                if asset in active:
                    balance = float(getattr(active[asset], "shares", 0.0))
                sell_amount_in_shares = balance
                if (current_price - vwap) > SLIPPAGE_TOLERANCE:
                    return False
                logger.info(f"📝 Sell Reason: {reason} | Asset: {asset} | Current: ${current_price:.4f} | VWAP: ${vwap:.4f} | Amount: {sell_amount_in_shares:.4f}")
                if SIM_MODE and state.is_simulation_enabled():
                    proceeds_usd = sell_amount_in_shares * vwap
                    state.adjust_sim_balance(proceeds_usd)
                    try:
                        logger.info(f"💼 SIM Balance: ${state.get_sim_balance():.4f}")
                    except Exception:
                        pass
                    state.update_recent_trade(asset, TradeType.SELL)
                    state.remove_active_trade(asset)
                    state.set_last_trade_time(time.time())
                    logger.info(f"🛒 SIM SELL filled: {sell_amount_in_shares:.4f} shares of {asset} at ${vwap:.4f} | Reason: {reason}")
                    return True
                else:
                    order_args = MarketOrderArgs(token_id=str(asset), amount=float(sell_amount_in_shares), side=SELL)
                    signed_order = cli.create_market_order(order_args)
                    response = cli.post_order(signed_order, OrderType.FOK)
                    if response.get("success"):
                        filled = response.get("data", {}).get("filledAmount", sell_amount_in_shares)
                        logger.info(f"🛒 SELL filled: {filled:.4f} shares of {asset} at ${vwap:.4f} | Reason: {reason}")
                        state.update_recent_trade(asset, TradeType.SELL)
                        state.remove_active_trade(asset)
                        state.set_last_trade_time(time.time())
                        return True
                    else:
                        error_msg = response.get("error", "Unknown error")
                        raise TradingError(f"Failed to place SELL order for {asset}: {error_msg}")
            except Exception as e:
                logger.error(f"❌ Unexpected error in SELL order for {asset}: {str(e)}")
                if attempt == max_retries - 1:
                    raise TradingError(f"Failed to process SELL order after {max_retries} attempts: {e}")
                time.sleep(base_delay * (2 ** attempt))
        return False
    except Exception as e:
        logger.error(f"❌ Error placing SELL order for {asset}: {str(e)}")
        raise
