import time
from typing import Optional, Dict, Any
from types import SimpleNamespace
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
        # 防止同一资产在并发下被重复买入
        if not state.try_acquire_asset_order(asset):
            return False
        # 单边市场限制：如果另一边已买过（或本边已买过），则不再买入
        try:
            if state.was_bought_once(asset):
                logger.info(f"⛔ Skip BUY {asset}: already bought once")
                state.release_asset_order(asset)
                return False
            opposite = state.get_asset_pair(asset)
            if opposite and state.was_bought_once(opposite):
                logger.info(f"⛔ Skip BUY {asset}: opposite side already bought once ({opposite})")
                state.release_asset_order(asset)
                return False
        except Exception:
            # 保护性：状态查询异常时不中断流程
            pass
        # 并发交易插槽原子预留，避免多个线程同时通过上限检查
        reserved = state.try_reserve_trade_slot()
        if not reserved:
            state.release_asset_order(asset)
            return False
        current_price = get_current_price(state, asset)
        if current_price is None:
            state.release_trade_slot()
            state.release_asset_order(asset)
            raise TradingError(f"Failed to get current price for {asset}")
        if SIM_MODE and state.is_simulation_enabled():
            ask_data = get_min_ask_data(asset)
            if ask_data is None:
                state.release_trade_slot()
                state.release_asset_order(asset)
                return False
            min_ask_price = float(ask_data["min_ask_price"])
            min_ask_size = float(ask_data["min_ask_size"])
            liquidity_usd = min_ask_size * min_ask_price
            if liquidity_usd < MIN_LIQUIDITY_REQUIREMENT:
                logger.info(f"⏭️ Skip BUY {asset}: ask liquidity ${liquidity_usd:.4f} < min ${MIN_LIQUIDITY_REQUIREMENT:.4f}")
                state.release_trade_slot()
                state.release_asset_order(asset)
                return False
            max_shares_by_unit = TRADE_UNIT / min_ask_price if min_ask_price > 0 else 0.0
            max_shares_by_balance = state.get_sim_balance() / min_ask_price if min_ask_price > 0 else 0.0
            shares_to_buy = min(min_ask_size, max_shares_by_unit, max_shares_by_balance)
            if shares_to_buy <= 0:
                state.release_trade_slot()
                state.release_asset_order(asset)
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
            # 记录已买入（单边市场策略），避免后续买入对侧
            try:
                state.mark_bought_once(asset)
            except Exception:
                pass
            state.set_last_trade_time(time.time())
            state.release_trade_slot()
            state.release_asset_order(asset)
            logger.info(f"🛒 SIM BUY filled: {shares_to_buy:.4f} shares of {asset} at ${min_ask_price:.4f} | Reason: {reason}")
            return True
        cli = get_client()
        if cli is None:
            logger.error("❌ ClobClient unavailable, skipping BUY")
            state.release_trade_slot()
            state.release_asset_order(asset)
            return False
        # 简化逻辑：买入最优卖价，数量受卖家可卖量与 trade_unit 限制
        ask_data = get_min_ask_data(asset)
        if ask_data is None:
            state.release_trade_slot()
            state.release_asset_order(asset)
            return False
        min_ask_price = float(ask_data["min_ask_price"])
        min_ask_size = float(ask_data["min_ask_size"])
        liquidity_usd = min_ask_size * min_ask_price
        if liquidity_usd < MIN_LIQUIDITY_REQUIREMENT:
            logger.info(f"⏭️ Skip BUY {asset}: ask liquidity ${liquidity_usd:.4f} < min ${MIN_LIQUIDITY_REQUIREMENT:.4f}")
            state.release_trade_slot()
            state.release_asset_order(asset)
            return False
        # 按 trade_unit 限制美元金额；以卖家可卖量限制份额
        max_shares_by_unit = TRADE_UNIT / min_ask_price if min_ask_price > 0 else 0.0
        shares_to_buy = min(min_ask_size, max_shares_by_unit)
        if shares_to_buy <= 0:
            state.release_trade_slot()
            state.release_asset_order(asset)
            return False
        amount_in_dollars = shares_to_buy * min_ask_price
        logger.info(f"📝 Buy Reason: {reason} | Asset: {asset} | BestAsk: ${min_ask_price:.4f} | AskSize: {min_ask_size:.4f} | SharesToBuy: {shares_to_buy:.4f} | AmountUSD: {amount_in_dollars:.4f}")
        if not check_usdc_allowance(state, amount_in_dollars):
            logger.warning(f"⚠️ Insufficient USDC balance/allowance for {asset} | Required: ${amount_in_dollars:.4f}")
            state.release_trade_slot()
            state.release_asset_order(asset)
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
            # 记录已买入（单边市场策略），避免后续买入对侧
            try:
                state.mark_bought_once(asset)
            except Exception:
                pass
            state.set_last_trade_time(time.time())
            state.release_trade_slot()
            state.release_asset_order(asset)
            return True
        else:
            error_msg = response.get("error", "Unknown error")
            logger.warning(f"Failed to place BUY order for {asset}: {error_msg}")
            state.release_trade_slot()
            state.release_asset_order(asset)
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
        # 防止同一资产并发卖出导致重复结算
        if not state.try_acquire_asset_order(asset):
            return False
        max_retries = MAX_RETRIES
        base_delay = BASE_DELAY
        for attempt in range(max_retries):
            try:
                current_price = get_current_price(state, asset)
                if current_price is None:
                    state.release_asset_order(asset)
                    raise TradingError(f"Failed to get current price for {asset}")
                if SIM_MODE and state.is_simulation_enabled():
                    vwap = float(current_price)
                else:
                    cli = get_client()
                    if cli is None:
                        logger.error("❌ ClobClient unavailable, skipping SELL")
                        state.release_asset_order(asset)
                        return False
                    est = estimate_vwap_for_amount(asset, "SELL", TRADE_UNIT, max_levels=5)
                    if est is None:
                        state.release_asset_order(asset)
                        return False
                    vwap = float(est.get("vwap", 0.0))
                active = state.get_active_trades()
                balance = 0.0
                if asset in active:
                    balance = float(getattr(active[asset], "shares", 0.0))
                sell_amount_in_shares = balance
                # 积极卖出：不进行滑点拦截，尽可能成交
                logger.info(f"📝 Sell Reason: {reason} | Asset: {asset} | Current: ${current_price:.4f} | VWAP: ${vwap:.4f} | Amount: {sell_amount_in_shares:.4f}")
                if SIM_MODE and state.is_simulation_enabled():
                    proceeds_usd = 0.0
                    filled_shares = 0.0
                    remaining = float(sell_amount_in_shares)
                    try:
                        cli_ob = get_client()
                        bids = []
                        if cli_ob is not None:
                            ob = cli_ob.get_order_book(asset)
                            bids = list(getattr(ob, "bids", []))
                        if not bids:
                            bid_data = get_max_bid_data(asset)
                            if bid_data is not None:
                                bids = [SimpleNamespace(
                                    price=float(bid_data.get("max_bid_price", 0.0)),
                                    size=float(bid_data.get("max_bid_size", 0.0))
                                )]
                        # 兼容对象或 dict 结构的 bids，先构建可排序的价格键
                        try:
                            sortable = []
                            for lvl in bids:
                                px_key = 0.0
                                try:
                                    val = getattr(lvl, "price", None)
                                    if val is not None:
                                        px_key = float(val)
                                    else:
                                        # 可能是 dict
                                        if isinstance(lvl, dict):
                                            px_key = float(lvl.get("price", 0.0))
                                except Exception:
                                    # 保持默认 0.0
                                    pass
                                sortable.append((px_key, lvl))
                            bids = [item[1] for item in sorted(sortable, key=lambda t: t[0], reverse=True)]
                        except Exception:
                            pass
                        for lvl in bids:
                            if remaining <= 0:
                                break
                            # 提取价格/数量，兼容对象或 dict
                            try:
                                if isinstance(lvl, dict):
                                    px = float(lvl.get("price", 0.0))
                                    sz = float(lvl.get("size", 0.0))
                                else:
                                    px = float(getattr(lvl, "price", 0.0))
                                    sz = float(getattr(lvl, "size", 0.0))
                            except (TypeError, ValueError):
                                continue
                            if px <= 0 or sz <= 0:
                                continue
                            take = min(remaining, sz)
                            proceeds_usd += take * px
                            filled_shares += take
                            remaining -= take
                    except Exception:
                        proceeds_usd = float(sell_amount_in_shares) * float(vwap)
                        filled_shares = float(sell_amount_in_shares)
                        remaining = 0.0
                    if filled_shares <= 0:
                        proceeds_usd = float(sell_amount_in_shares) * float(vwap)
                        filled_shares = float(sell_amount_in_shares)
                        remaining = 0.0
                    state.adjust_sim_balance(proceeds_usd)
                    try:
                        logger.info(f"💼 SIM Balance: ${state.get_sim_balance():.4f}")
                    except Exception:
                        pass
                    state.update_recent_trade(asset, TradeType.SELL)
                    if remaining <= 0:
                        state.remove_active_trade(asset)
                    else:
                        original = active.get(asset)
                        # 防御性：active.get 可能返回 None；仅在对象存在且具备 shares 属性时更新
                        if isinstance(original, TradeInfo) and hasattr(original, "shares"):
                            try:
                                original.shares = float(remaining)
                                state.add_active_trade(asset, original)
                            except Exception:
                                state.remove_active_trade(asset)
                        else:
                            state.remove_active_trade(asset)
                    state.set_last_trade_time(time.time())
                    logger.info(
                        f"🛒 SIM SELL filled: {filled_shares:.4f} shares of {asset} | Proceeds: ${proceeds_usd:.4f} | Remaining: {remaining:.4f} | Reason: {reason}"
                    )
                    state.release_asset_order(asset)
                    return True
                else:
                    order_args = MarketOrderArgs(token_id=str(asset), amount=float(sell_amount_in_shares), side=SELL)
                    signed_order = cli.create_market_order(order_args)
                    response = cli.post_order(signed_order, OrderType.FAK)
                    if response.get("success"):
                        try:
                            filled = float(response.get("data", {}).get("filledAmount", sell_amount_in_shares))
                        except Exception:
                            filled = float(sell_amount_in_shares)
                        logger.info(f"🛒 SELL filled: {filled:.4f} shares of {asset} at ${vwap:.4f} | Reason: {reason}")
                        state.update_recent_trade(asset, TradeType.SELL)
                        # 根据成交份额更新剩余持仓；完全成交移除
                        remaining_shares = max(0.0, balance - filled)
                        if remaining_shares <= 0:
                            state.remove_active_trade(asset)
                        else:
                            original = active.get(asset)
                            if isinstance(original, TradeInfo) and hasattr(original, "shares"):
                                try:
                                    original.shares = float(remaining_shares)
                                    state.add_active_trade(asset, original)
                                except Exception:
                                    state.remove_active_trade(asset)
                            else:
                                state.remove_active_trade(asset)
                        state.set_last_trade_time(time.time())
                        state.release_asset_order(asset)
                        return True
                    else:
                        error_msg = response.get("error", "Unknown error")
                        raise TradingError(f"Failed to place SELL order for {asset}: {error_msg}")
            except Exception as e:
                logger.error(f"❌ Unexpected error in SELL order for {asset}: {str(e)}")
                if attempt == max_retries - 1:
                    state.release_asset_order(asset)
                    raise TradingError(f"Failed to process SELL order after {max_retries} attempts: {e}")
                time.sleep(base_delay * (2 ** attempt))
        return False
    except Exception as e:
        logger.error(f"❌ Error placing SELL order for {asset}: {str(e)}")
        state.release_asset_order(asset)
        raise
