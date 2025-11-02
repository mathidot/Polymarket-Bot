import time
import logging
from typing import Optional, Dict, Any

from py_clob_client.clob_types import MarketOrderArgs, OrderType
from py_clob_client.order_builder.constants import BUY, SELL

from config import (
    MAX_RETRIES,
    BASE_DELAY,
    KEEP_MIN_SHARES,
    SLIPPAGE_TOLERANCE,
    MIN_LIQUIDITY_REQUIREMENT,
    TRADE_UNIT,
    YOUR_PROXY_WALLET,
    USDC_CONTRACT_ADDRESS,
    POLYMARKET_SETTLEMENT_CONTRACT,
    BOT_TRADER_ADDRESS,
    PRIVATE_KEY,
    COOLDOWN_PERIOD,
    MAX_CONCURRENT_TRADES,
    SIMULATION_MODE,
)
from models import TradingError, TradeInfo, TradeType, PositionInfo
from chain import w3
from api import get_order_book, get_price, create_market_order, post_order
from pricing import get_current_price
from state import ThreadSafeState, price_update_event


logger = logging.getLogger("polymarket_bot")


def ensure_usdc_allowance(required_amount: float) -> bool:
    if SIMULATION_MODE:
        logger.info("🧪 模拟模式：跳过 USDC allowance 检查与授权")
        return True
    max_retries = MAX_RETRIES
    base_delay = BASE_DELAY

    for attempt in range(max_retries):
        try:
            contract = w3.eth.contract(
                address=USDC_CONTRACT_ADDRESS,
                abi=[
                    {
                        "constant": True,
                        "inputs": [
                            {"name": "owner", "type": "address"},
                            {"name": "spender", "type": "address"},
                        ],
                        "name": "allowance",
                        "outputs": [{"name": "", "type": "uint256"}],
                        "payable": False,
                        "stateMutability": "view",
                        "type": "function",
                    },
                    {
                        "constant": False,
                        "inputs": [
                            {"name": "spender", "type": "address"},
                            {"name": "value", "type": "uint256"},
                        ],
                        "name": "approve",
                        "outputs": [{"name": "", "type": "bool"}],
                        "payable": False,
                        "stateMutability": "nonpayable",
                        "type": "function",
                    },
                ],
            )

            # Allowance 必须由实际持有 USDC 的资金账号（YOUR_PROXY_WALLET）授权给结算合约
            current_allowance = contract.functions.allowance(
                YOUR_PROXY_WALLET, POLYMARKET_SETTLEMENT_CONTRACT
            ).call()
            logger.info(f"current_allowance: {current_allowance}")
            required_amount_with_buffer = int(required_amount * 1.1 * 10**6)

            if current_allowance >= required_amount_with_buffer:
                return True

            logger.info(
                f"🔄 Approving USDC allowance... (attempt {attempt + 1}/{max_retries})"
            )

            new_allowance = max(current_allowance, required_amount_with_buffer)
            logger.info(f"new_allowance: {new_allowance}")
            txn = contract.functions.approve(
                POLYMARKET_SETTLEMENT_CONTRACT, new_allowance
            ).build_transaction(
                {
                    "from": YOUR_PROXY_WALLET,
                    "gas": 200000,
                    "gasPrice": w3.eth.gas_price,
                    "nonce": w3.eth.get_transaction_count(YOUR_PROXY_WALLET),
                    "chainId": 137,
                }
            )

            signed_txn = w3.eth.account.sign_transaction(txn, private_key=PRIVATE_KEY)
            tx_hash = w3.eth.send_raw_transaction(signed_txn.raw_transaction)
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

            if receipt.status == 1:
                logger.info(f"✅ USDC allowance updated: {tx_hash.hex()}")
                return True
            else:
                raise TradingError(f"USDC allowance update failed: {tx_hash.hex()}")

        except Exception as e:
            if attempt == max_retries - 1:
                raise TradingError(f"Failed to update USDC allowance: {e}")
            logger.error(
                f"⚠️ Error in USDC allowance update (attempt {attempt + 1}): {e}"
            )
            time.sleep(base_delay * (2 ** attempt))

    return False


def get_min_ask_data(asset: str, allow_price_fallback: bool = False) -> Optional[Dict[str, Any]]:
    try:
        order = get_order_book(asset)
        asks = getattr(order, "asks", None)
        if asks:
            # Robustly select best ask regardless of list ordering
            try:
                best_ask = min(asks, key=lambda a: float(a.price))
            except Exception:
                best_ask = asks[-1]

            buy_price = get_price(asset, "BUY")
            min_ask_price = float(getattr(best_ask, "price", 0))
            min_ask_size = float(getattr(best_ask, "size", 0))
            logger.debug(
                f"min_ask_price: {min_ask_price}, min_ask_size: {min_ask_size}"
            )
            return {
                "buy_price": buy_price,
                "min_ask_price": min_ask_price,
                "min_ask_size": min_ask_size,
                "source": "orderbook",
            }
        else:
            # Optional fallback: use API executable price when orderbook snapshot shows no asks
            if allow_price_fallback:
                try:
                    buy_price = get_price(asset, "BUY")
                    if buy_price is not None and float(buy_price) > 0:
                        logger.debug(
                            f"⚠️ No ask depth for {asset}; using BUY price fallback for signal"
                        )
                        return {
                            "buy_price": buy_price,
                            "min_ask_price": float(buy_price),
                            "min_ask_size": 0.0,
                            "source": "fallback",
                        }
                except Exception:
                    pass
            logger.debug(f"⚠️ No ask data found for {asset}")
            return None
    except Exception as e:
        logger.error(f"❌ Failed to get ask data for {asset}: {str(e)}")
        return None


def get_max_bid_data(asset: str, allow_price_fallback: bool = False) -> Optional[Dict[str, Any]]:
    try:
        order = get_order_book(asset)
        bids = getattr(order, "bids", None)
        if bids:
            # Robustly select best bid regardless of list ordering
            try:
                best_bid = max(bids, key=lambda b: float(b.price))
            except Exception:
                best_bid = bids[-1]

            sell_price = get_price(asset, "SELL")
            max_bid_price = float(getattr(best_bid, "price", 0))
            max_bid_size = float(getattr(best_bid, "size", 0))
            logger.debug(
                f"max_bid_price: {max_bid_price}, max_bid_size: {max_bid_size}"
            )
            return {
                "sell_price": sell_price,
                "max_bid_price": max_bid_price,
                "max_bid_size": max_bid_size,
                "source": "orderbook",
            }
        else:
            # Optional fallback: use API executable price when orderbook snapshot shows no bids
            if allow_price_fallback:
                try:
                    sell_price = get_price(asset, "SELL")
                    if sell_price is not None and float(sell_price) > 0:
                        logger.debug(
                            f"⚠️ No bid depth for {asset}; using SELL price fallback for signal"
                        )
                        return {
                            "sell_price": sell_price,
                            "max_bid_price": float(sell_price),
                            "max_bid_size": 0.0,
                            "source": "fallback",
                        }
                except Exception:
                    pass
            logger.debug(f"⚠️ No bid data found for {asset}")
            return None
    except Exception as e:
        logger.error(f"❌ Failed to get bid data for {asset}: {str(e)}")
        return None


def check_usdc_balance(state: ThreadSafeState, usdc_needed: float) -> bool:
    # 使用状态对象的模拟模式，避免与全局配置不一致
    if state.is_simulation_mode():
        try:
            bal = state.get_sim_usdc_balance()
            logger.info(
                f"💵 [SIM] USDC Balance: ${bal:.2f}, Required: ${usdc_needed:.2f}"
            )
            if bal < usdc_needed:
                logger.warning(
                    f"❌ [SIM] Insufficient USDC balance. Required: ${usdc_needed:.2f}, Available: ${bal:.2f}"
                )
                return False
            return True
        except Exception as e:
            logger.error(f"❌ [SIM] Failed to check USDC balance: {str(e)}")
            return False
    try:
        usdc_contract = w3.eth.contract(
            address=USDC_CONTRACT_ADDRESS,
            abi=[
                {
                    "constant": True,
                    "inputs": [{"name": "account", "type": "address"}],
                    "name": "balanceOf",
                    "outputs": [{"name": "", "type": "uint256"}],
                    "payable": False,
                    "stateMutability": "view",
                    "type": "function",
                }
            ],
        )
        usdc_balance = usdc_contract.functions.balanceOf(YOUR_PROXY_WALLET).call() / 10**6

        logger.info(
            f"💵 USDC Balance: ${usdc_balance:.2f}, Required: ${usdc_needed:.2f}"
        )

        if usdc_balance < usdc_needed:
            logger.warning(
                f"❌ Insufficient USDC balance. Required: ${usdc_needed:.2f}, Available: ${usdc_balance:.2f}"
            )
            return False
        return True

    except Exception as e:
        logger.error(f"❌ Failed to check USDC balance: {str(e)}")
        return False


def find_position_by_asset(positions: dict, asset_id: str) -> Optional[PositionInfo]:
    for event_positions in positions.values():
        for position in event_positions:
            if position.asset == asset_id:
                return position
    return None


def is_recently_bought(state: ThreadSafeState, asset_id: str) -> bool:
    with state._recent_trades_lock:
        if asset_id not in state._recent_trades or state._recent_trades[asset_id]["buy"] is None:
            return False
        now = time.time()
        time_since_buy = now - state._recent_trades[asset_id]["buy"]
        return time_since_buy < COOLDOWN_PERIOD


def is_recently_sold(state: ThreadSafeState, asset_id: str) -> bool:
    with state._recent_trades_lock:
        if asset_id not in state._recent_trades or state._recent_trades[asset_id]["sell"] is None:
            return False
        now = time.time()
        time_since_sell = now - state._recent_trades[asset_id]["sell"]
        return time_since_sell < COOLDOWN_PERIOD


def place_buy_order(state: ThreadSafeState, asset: str, reason: str) -> bool:
    try:
        # Check maximum concurrent trades
        active_trades = state.get_active_trades()
        logger.info(
            f"active_trades----------------------------------------------->{active_trades}"
        )
        if len(active_trades) >= MAX_CONCURRENT_TRADES:
            logger.warning(
                f"🔒 Maximum concurrent trades limit reached ({len(active_trades)}/{MAX_CONCURRENT_TRADES})"
            )
            return False

        # Check USDC presence (simulation or on-chain)
        if state.is_simulation_mode():
            if not check_usdc_balance(state, 0.01):
                logger.info(f"❌ [SIM] No USDC balance available to place buy order for {asset}")
                return False
        else:
            usdc_contract = w3.eth.contract(
                address=USDC_CONTRACT_ADDRESS,
                abi=[
                    {
                        "constant": True,
                        "inputs": [{"name": "account", "type": "address"}],
                        "name": "balanceOf",
                        "outputs": [{"name": "", "type": "uint256"}],
                        "payable": False,
                        "stateMutability": "view",
                        "type": "function",
                    }
                ],
            )
            usdc_balance = usdc_contract.functions.balanceOf(YOUR_PROXY_WALLET).call() / 10**6
            logger.info(f"usdc_balance: {usdc_balance}")
            if not usdc_balance:
                logger.info(
                    f"❌ No USDC balance available to place buy order for {asset}"
                )
                return False

        max_retries = MAX_RETRIES
        base_delay = BASE_DELAY

        for attempt in range(max_retries):
            try:
                current_price = get_current_price(state, asset)
                if current_price is None:
                    raise TradingError(f"Failed to get current price for {asset}")

                # Allow fallback to executable BUY price when orderbook snapshot lacks asks
                min_ask_data = get_min_ask_data(asset, allow_price_fallback=True)
                if min_ask_data is None:
                    logger.warning(f"❌ The {asset} is not tradable, Skipping...")
                    return False

                min_ask_price = float(min_ask_data["min_ask_price"])
                min_ask_size = float(min_ask_data["min_ask_size"])

                # Check liquidity requirement
                if min_ask_size * min_ask_price < MIN_LIQUIDITY_REQUIREMENT:
                    logger.warning(
                        f"🔒 Insufficient liquidity for {asset}. Required: ${MIN_LIQUIDITY_REQUIREMENT}, Available: ${min_ask_size * min_ask_price:.2f}"
                    )
                    return False

                if min_ask_price - current_price > SLIPPAGE_TOLERANCE:
                    logger.warning(
                        f"🔐 Slippage tolerance exceeded for {asset}. Skipping order."
                    )
                    return False

                # Calculate position size based on account balance
                amount_in_dollars = min(TRADE_UNIT, min_ask_size * min_ask_price)

                if not check_usdc_balance(state, amount_in_dollars):
                    raise TradingError(f"Insufficient USDC balance for {asset}")

                if state.is_simulation_mode():
                    # Simulate buy fill immediately with FOK semantics
                    filled_dollars = amount_in_dollars
                    filled_shares = filled_dollars / float(min_ask_price)
                    eventslug, outcome = state.get_asset_meta(asset)
                    # Adjust USDC and upsert position; add defensive logging and trigger snapshot
                    try:
                        logger.info(
                            f"🧪 [SIM] Preparing position write | asset={asset} | price=${min_ask_price:.4f} | shares={filled_shares:.4f} | cp=${current_price:.4f}"
                        )
                        state.adjust_sim_usdc_balance(-filled_dollars)
                        try:
                            state.upsert_sim_position(
                                asset,
                                eventslug,
                                outcome,
                                float(min_ask_price),
                                float(filled_shares),
                                current_price=current_price,
                            )
                        except Exception as upsert_err:
                            logger.error(
                                f"❌ [SIM] upsert_sim_position failed for {asset}: {upsert_err}"
                            )
                        logger.info(
                            f"🧪 [{reason}] [SIM] BUY {filled_shares:.4f} shares of {asset} at ${min_ask_price:.4f}"
                        )
                        # 额外确认：打印当前总持仓数量与刚写入的资产
                        try:
                            pos_map = state.get_positions()
                            total_positions = sum(len(v) for v in pos_map.values())
                            logger.info(
                                f"🧪 [SIM] Position write check | total={total_positions} | added_asset={asset} | shares={filled_shares:.4f} | eventslug={eventslug} | outcome={outcome}"
                            )
                        except Exception:
                            pass
                    finally:
                        # Wake up downstream threads (e.g., positions_log) to reflect the new position
                        try:
                            price_update_event.set()
                        except Exception:
                            pass
                else:
                    if not ensure_usdc_allowance(amount_in_dollars):
                        raise TradingError(f"Failed to ensure USDC allowance for {asset}")

                    order_args = MarketOrderArgs(
                        token_id=str(asset),
                        amount=float(amount_in_dollars),
                        side=BUY,
                    )
                    signed_order = create_market_order(order_args)
                    response = post_order(signed_order, OrderType.FOK)
                    if response.get("success"):
                        filled = response.get("data", {}).get("filledAmount", amount_in_dollars)
                        logger.info(
                            f"🛒 [{reason}] Order placed: BUY {filled:.4f} shares of {asset} at ${min_ask_price:.4f}"
                        )
                    else:
                        error_msg = response.get("error", "Unknown error")
                        raise TradingError(
                            f"Failed to place BUY order for {asset}: {error_msg}"
                        )

                trade_info = TradeInfo(
                    entry_price=min_ask_price,
                    entry_time=time.time(),
                    amount=amount_in_dollars,
                    bot_triggered=True,
                )

                state.update_recent_trade(asset, TradeType.BUY)
                state.add_active_trade(asset, trade_info)
                state.set_last_trade_time(time.time())
                return True

            except TradingError as e:
                logger.error(f"❌ Trading error in BUY order for {asset}: {str(e)}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(base_delay * (2 ** attempt))
            except Exception as e:
                logger.error(
                    f"❌ Unexpected error in BUY order for {asset}: {str(e)}"
                )
                if attempt == max_retries - 1:
                    raise TradingError(
                        f"Failed to process BUY order after {max_retries} attempts: {e}"
                    )
                time.sleep(base_delay * (2 ** attempt))

        return False
    except Exception as e:
        logger.error(
            f"❌ Error placing BUY order for {asset}: {str(e)}", exc_info=True
        )
        raise


def place_sell_order(state: ThreadSafeState, asset: str, reason: str) -> bool:
    try:
        max_retries = MAX_RETRIES
        base_delay = BASE_DELAY

        for attempt in range(max_retries):
            try:
                logger.info(f"🔄 Order attempt {attempt + 1}/{max_retries} for SELL {asset}")

                current_price = get_current_price(state, asset)
                if current_price is None:
                    raise TradingError(f"Failed to get current price for {asset}")

                # Allow fallback to executable SELL price when orderbook snapshot lacks bids
                max_bid_data = get_max_bid_data(asset, allow_price_fallback=True)
                if max_bid_data is None:
                    # Treat missing bid depth as transient: retry with backoff
                    raise TradingError(f"No bid data for {asset}; will retry")

                max_bid_price = float(max_bid_data["max_bid_price"])
                max_bid_size = float(max_bid_data["max_bid_size"])

                if max_bid_size * max_bid_price < MIN_LIQUIDITY_REQUIREMENT:
                    logger.warning(
                        f"🔒 Insufficient liquidity for {asset}. Required: ${MIN_LIQUIDITY_REQUIREMENT}, Available: ${max_bid_size * max_bid_price:.2f}"
                    )
                    return False

                positions = state.get_positions()
                position = find_position_by_asset(positions, asset)
                if not position:
                    logger.warning(f"🙄 No position found for {asset}, Skipping sell...")
                    return False

                balance = float(position.shares)
                avg_price = float(position.avg_price)
                sell_amount_in_shares = balance - KEEP_MIN_SHARES

                if sell_amount_in_shares < 1:
                    logger.warning(f"🙄 No shares to sell for {asset}, Skipping...")
                    return False

                slippage = current_price - max_bid_price
                if slippage > SLIPPAGE_TOLERANCE:
                    logger.warning(
                        f"🔐 Slippage tolerance exceeded for {asset}. Skipping order."
                    )
                    return False

                sell_amount_to_post = min(sell_amount_in_shares, max_bid_size)
                if sell_amount_to_post < 1:
                    logger.warning(
                        f"🔒 Insufficient top-of-book depth for {asset}. Available size: {max_bid_size:.2f}"
                    )
                    return False
                if avg_price > max_bid_price:
                    profit_amount = sell_amount_in_shares * (avg_price - max_bid_price)
                    logger.info(
                        f"balance: {balance}, slippage: {slippage}----You will earn ${profit_amount}"
                    )
                else:
                    loss_amount = sell_amount_in_shares * (max_bid_price - avg_price)
                    logger.info(
                        f"balance: {balance}, slippage: {slippage}----You will lose ${loss_amount}"
                    )

                if SIMULATION_MODE:
                    # Simulate immediate sell
                    filled = sell_amount_to_post
                    state.adjust_sim_usdc_balance(float(filled) * float(max_bid_price))
                    ok = state.reduce_sim_position(asset, float(filled), float(max_bid_price))
                    if not ok:
                        raise TradingError("Failed to reduce simulated position")
                    logger.info(
                        f"🧪 [{reason}] [SIM] SELL {filled:.4f} shares of {asset} at ${max_bid_price:.4f}"
                    )
                else:
                    order_args = MarketOrderArgs(
                        token_id=str(asset),
                        amount=float(sell_amount_to_post),
                        side=SELL,
                    )
                    signed_order = create_market_order(order_args)
                    response = post_order(signed_order, OrderType.FOK)
                    if response.get("success"):
                        filled = response.get("data", {}).get(
                            "filledAmount", sell_amount_to_post
                        )
                        logger.info(
                            f"🛒 [{reason}] Order placed: SELL {filled:.4f} shares of {asset}"
                        )
                    else:
                        error_msg = response.get("error", "Unknown error")
                        raise TradingError(
                            f"Failed to place SELL order for {asset}: {error_msg}"
                        )

                state.update_recent_trade(asset, TradeType.SELL)
                state.remove_active_trade(asset)
                state.set_last_trade_time(time.time())
                return True

            except TradingError as e:
                logger.error(f"❌ Trading error in SELL order for {asset}: {str(e)}")
                if attempt == max_retries - 1:
                    raise
                time.sleep(base_delay * (2 ** attempt))
            except Exception as e:
                logger.error(f"❌ Unexpected error in SELL order for {asset}: {str(e)}")
                if attempt == max_retries - 1:
                    raise TradingError(
                        f"Failed to process SELL order after {max_retries} attempts: {e}"
                    )
                time.sleep(base_delay * (2 ** attempt))

        return False
    except Exception as e:
        logger.error(f"❌ Error placing SELL order for {asset}: {str(e)}")
        raise

    
if __name__ == '__main__':
    min_data = get_min_ask_data("28929654325017891260337264839306034319671562771699628371124118705224608724113")
    print(min_data)
    max_data = get_min_ask_data("43337151523437879906986251357847358178613236712919036009433099963474901213619")
    print(max_data)