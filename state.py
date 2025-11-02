import time
import logging
from typing import Dict, List, Tuple, Optional
from collections import deque, defaultdict
from threading import Lock, Event

from models import TradeInfo, PositionInfo, TradeType, ValidationError
from config import PRICE_HISTORY_SIZE, KEEP_MIN_SHARES, SIMULATION_MODE, SIM_START_USDC


logger = logging.getLogger("polymarket_bot")


price_update_event = Event()


class ThreadSafeState:
    def __init__(
        self,
        max_price_history_size: int = PRICE_HISTORY_SIZE,
        keep_min_shares: int = KEEP_MIN_SHARES,
    ):
        self._price_history_lock = Lock()
        self._active_trades_lock = Lock()
        self._positions_lock = Lock()
        self._asset_pairs_lock = Lock()
        self._recent_trades_lock = Lock()
        self._last_trade_closed_at_lock = Lock()
        self._initialized_assets_lock = Lock()
        self._last_spike_asset_lock = Lock()
        self._last_spike_price_lock = Lock()
        self._counter_lock = Lock()
        self._order_books_cache_lock = Lock()
        self._shutdown_event = Event()
        self._cleanup_complete = Event()
        self._circuit_breaker_lock = Lock()
        self._max_price_history_size = max_price_history_size

        self._price_history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=max_price_history_size)
        )
        self._active_trades: Dict[str, TradeInfo] = {}
        self._positions: Dict[str, List[PositionInfo]] = {}
        self._asset_pairs: Dict[str, str] = {}
        self._recent_trades: Dict[str, Dict[str, Optional[float]]] = {}
        self._last_trade_closed_at: float = 0
        self._initialized_assets: set = set()
        self._last_spike_asset: Optional[str] = None
        self._last_spike_price: Optional[float] = None
        self._asset_meta_lock = Lock()
        self._asset_meta: Dict[str, Tuple[str, str]] = {}
        self._counter: int = 0
        self._order_books_cache: Dict[str, object] = {}
        self._order_books_updated_at: float = 0.0

        # Simulation mode
        self._simulation_mode: bool = bool(SIMULATION_MODE)
        self._sim_usdc_balance: float = (
            float(SIM_START_USDC) if self._simulation_mode else 0.0
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def cleanup(self) -> None:
        if not self._cleanup_complete.is_set():
            self.shutdown()
            with self._price_history_lock:
                self._price_history.clear()
            with self._active_trades_lock:
                self._active_trades.clear()
            with self._positions_lock:
                self._positions.clear()
            with self._asset_pairs_lock:
                self._asset_pairs.clear()
            with self._recent_trades_lock:
                self._recent_trades.clear()
            with self._order_books_cache_lock:
                self._order_books_cache.clear()
                self._order_books_updated_at = 0.0
            # Do not reset simulation flags; keep balance for post-run inspection
            self._cleanup_complete.set()

    def increment_counter(self) -> int:
        with self._counter_lock:
            self._counter += 1
            return self._counter

    def reset_counter(self) -> None:
        with self._counter_lock:
            self._counter = 0

    def get_counter(self) -> int:
        with self._counter_lock:
            return self._counter

    def shutdown(self) -> None:
        self._shutdown_event.set()

    def is_shutdown(self) -> bool:
        return self._shutdown_event.is_set()

    def wait_for_cleanup(self, timeout: Optional[float] = None) -> bool:
        return self._cleanup_complete.wait(timeout)

    def get_price_history(self, asset_id: str) -> deque:
        with self._price_history_lock:
            return self._price_history.get(asset_id, deque())

    def add_price(
        self,
        asset_id: str,
        timestamp: float,
        price: float,
        eventslug: str,
        outcome: str,
    ) -> None:
        with self._price_history_lock:
            if not isinstance(asset_id, str):
                raise ValidationError(f"Invalid asset_id type: {type(asset_id)}")
            if asset_id not in self._price_history:
                self._price_history[asset_id] = deque(
                    maxlen=self._max_price_history_size
                )
            self._price_history[asset_id].append((timestamp, price, eventslug, outcome))

    def get_active_trades(self) -> Dict[str, TradeInfo]:
        with self._active_trades_lock:
            return dict(self._active_trades)

    def add_active_trade(self, asset_id: str, trade_info: TradeInfo) -> None:
        with self._active_trades_lock:
            self._active_trades[asset_id] = trade_info

    def remove_active_trade(self, asset_id: str) -> None:
        with self._active_trades_lock:
            self._active_trades.pop(asset_id, None)

    def get_positions(self) -> Dict[str, List[PositionInfo]]:
        with self._positions_lock:
            return dict(self._positions)

    def update_positions(self, new_positions: Dict[str, List[PositionInfo]]) -> None:
        if new_positions is None:
            logger.warning("⚠️ Attempted to update positions with None")
            return

        if not isinstance(new_positions, dict):
            logger.error(f"❌ Invalid positions type: {type(new_positions)}")
            return

        try:
            with self._positions_lock:
                valid_positions = {}
                for event_id, positions in new_positions.items():
                    if not isinstance(positions, list):
                        logger.warning(f"⚠️ Invalid positions list for event {event_id}")
                        continue

                    valid_positions[event_id] = []
                    for pos in positions:
                        if not isinstance(pos, PositionInfo):
                            logger.warning(
                                f"⚠️ Invalid position type for event {event_id}"
                            )
                            continue

                        if not pos.asset or not pos.eventslug or not pos.outcome:
                            logger.warning(
                                f"⚠️ Missing required fields in position for event {event_id}"
                            )
                            continue

                        if pos.shares < 0 or pos.avg_price < 0 or pos.current_price < 0:
                            logger.warning(
                                f"⚠️ Invalid numeric values in position for event {event_id}"
                            )
                            continue

                        valid_positions[event_id].append(pos)

                if valid_positions:
                    self._positions = valid_positions
                    logger.info(f"✅ Updated positions: {len(valid_positions)} events")
                else:
                    logger.warning("⚠️ No valid positions to update")

        except Exception as e:
            logger.error(f"❌ Error updating positions: {str(e)}")
            return

    def get_asset_pair(self, asset_id: str) -> Optional[str]:
        with self._asset_pairs_lock:
            return self._asset_pairs.get(asset_id)

    def add_asset_pair(self, asset1: str, asset2: str) -> None:
        with self._asset_pairs_lock:
            self._asset_pairs[asset1] = asset2
            self._asset_pairs[asset2] = asset1
            self._initialized_assets.add(asset1)
            self._initialized_assets.add(asset2)

    def set_asset_meta(self, asset_id: str, eventslug: str, outcome: str) -> None:
        with self._asset_meta_lock:
            self._asset_meta[asset_id] = (eventslug, outcome)

    def get_asset_meta(self, asset_id: str) -> Tuple[str, str]:
        with self._asset_meta_lock:
            return self._asset_meta.get(asset_id, ("", ""))

    def is_initialized(self) -> bool:
        with self._initialized_assets_lock:
            return len(self._initialized_assets) > 0

    def update_recent_trade(self, asset_id: str, trade_type: TradeType) -> None:
        with self._recent_trades_lock:
            if asset_id not in self._recent_trades:
                self._recent_trades[asset_id] = {"buy": None, "sell": None}
            self._recent_trades[asset_id][trade_type.value] = time.time()

    def get_last_trade_time(self) -> float:
        with self._last_trade_closed_at_lock:
            return self._last_trade_closed_at

    def set_last_trade_time(self, timestamp: float) -> None:
        with self._last_trade_closed_at_lock:
            self._last_trade_closed_at = timestamp

    def get_last_spike_info(self) -> Tuple[Optional[str], Optional[float]]:
        with self._last_spike_asset_lock, self._last_spike_price_lock:
            return self._last_spike_asset, self._last_spike_price

    def set_last_spike_info(self, asset: str, price: float) -> None:
        with self._last_spike_asset_lock, self._last_spike_price_lock:
            self._last_spike_asset = asset
            self._last_spike_price = price

    # ---- Order books cache (batch fetch) ----
    def set_order_books_cache(
        self, books_map: Dict[str, object], timestamp: Optional[float] = None
    ) -> None:
        ts = timestamp if timestamp is not None else time.time()
        with self._order_books_cache_lock:
            self._order_books_cache = dict(books_map or {})
            self._order_books_updated_at = ts

    def get_order_books_cache(self) -> Tuple[Dict[str, object], float]:
        with self._order_books_cache_lock:
            return dict(self._order_books_cache), float(self._order_books_updated_at)

    def get_cached_order_book(self, token_id: str) -> Optional[object]:
        with self._order_books_cache_lock:
            return self._order_books_cache.get(token_id)

    def is_order_books_cache_valid(self, ttl_seconds: float) -> bool:
        with self._order_books_cache_lock:
            if self._order_books_updated_at <= 0:
                return False
            return (time.time() - self._order_books_updated_at) <= ttl_seconds

    # ---- Simulation helpers ----
    def is_simulation_mode(self) -> bool:
        return self._simulation_mode

    def get_sim_usdc_balance(self) -> float:
        return float(self._sim_usdc_balance)

    def set_sim_usdc_balance(self, amount: float) -> None:
        try:
            amt = float(amount)
            self._sim_usdc_balance = max(0.0, amt)
            logger.info(f"🧪 设置模拟 USDC 余额：${self._sim_usdc_balance:.2f}")
        except Exception:
            logger.warning("⚠️ 设置模拟余额失败：输入非法")

    def adjust_sim_usdc_balance(self, delta: float) -> None:
        # 非模拟模式下忽略余额调整，避免与真实交易混用
        if not self._simulation_mode:
            logger.info("🧪 忽略非模拟模式的 USDC 余额调整请求")
            return
        try:
            d = float(delta)
            self._sim_usdc_balance = max(0.0, self._sim_usdc_balance + d)
            logger.info(
                f"🧪 模拟 USDC 余额调整：{d:+.2f}，当前=${self._sim_usdc_balance:.2f}"
            )
        except Exception:
            logger.warning("⚠️ 调整模拟余额失败：输入非法")

    def _find_position_obj(self, asset_id: str) -> Optional[PositionInfo]:
        with self._positions_lock:
            for positions in self._positions.values():
                for pos in positions:
                    if pos.asset == asset_id:
                        return pos
        return None

    def upsert_sim_position(
        self,
        asset_id: str,
        eventslug: str,
        outcome: str,
        price: float,
        shares: float,
        current_price: Optional[float] = None,
    ) -> None:
        if not self._simulation_mode:
            return
        try:
            with self._positions_lock:
                logger.info(f"🧪 模拟持仓更新请求 | {asset_id} {eventslug} {outcome} {price} {shares} {current_price}")
                pos = self._find_position_obj(asset_id)
                cp = float(current_price) if current_price is not None else float(price)
                if pos is None:
                    new_pos = PositionInfo(
                        eventslug=str(eventslug or "SimEvent"),
                        outcome=str(outcome or "SimSide"),
                        asset=str(asset_id),
                        avg_price=float(price),
                        shares=float(shares),
                        current_price=cp,
                        initial_value=float(price) * float(shares),
                        current_value=cp * float(shares),
                        pnl=(cp - float(price)) * float(shares),
                        percent_pnl=((cp - float(price)) / float(price))
                        if float(price) > 0
                        else 0.0,
                        realized_pnl=0.0,
                    )
                    key = str(eventslug or "SimEvent")
                    if key not in self._positions:
                        self._positions[key] = []
                    self._positions[key].append(new_pos)
                    # 新增持仓确认日志
                    logger.info(
                        f"🧪 模拟持仓新增 | {new_pos.eventslug} [{new_pos.outcome}] ({new_pos.asset}) | 数量={new_pos.shares:.4f} 均价=${new_pos.avg_price:.4f}"
                    )
                else:
                    ts = float(pos.shares) + float(shares)
                    if ts <= 0:
                        ts = 0.0
                    if ts > 0:
                        pos.avg_price = (
                            float(pos.avg_price) * float(pos.shares)
                            + float(price) * float(shares)
                        ) / ts
                    pos.shares = ts
                    pos.current_price = cp
                    pos.initial_value = float(pos.avg_price) * float(pos.shares)
                    pos.current_value = cp * float(pos.shares)
                    pos.pnl = pos.current_value - pos.initial_value
                    pos.percent_pnl = (
                        (pos.pnl / pos.initial_value) if pos.initial_value > 0 else 0.0
                    )
                    # 更新持仓确认日志
                    logger.info(
                        f"🧪 模拟持仓更新 | {pos.eventslug} [{pos.outcome}] ({pos.asset}) | 新数量={pos.shares:.4f} 新均价=${pos.avg_price:.4f}"
                    )
        except Exception as e:
            logger.error(f"❌ 更新模拟持仓失败：{e}")

    def reduce_sim_position(
        self, asset_id: str, sell_shares: float, sell_price: float
    ) -> bool:
        if not self._simulation_mode:
            return False
        try:
            with self._positions_lock:
                pos = self._find_position_obj(asset_id)
                if pos is None:
                    return False
                sell_qty = float(sell_shares)
                if sell_qty <= 0:
                    return False
                # Respect KEEP_MIN_SHARES implicitly by caller; here just reduce
                if sell_qty > pos.shares:
                    sell_qty = float(pos.shares)
                realized = sell_qty * (float(sell_price) - float(pos.avg_price))
                pos.shares = float(pos.shares) - sell_qty
                pos.current_price = float(sell_price)
                pos.current_value = float(pos.current_price) * float(pos.shares)
                pos.initial_value = float(pos.avg_price) * float(pos.shares)
                pos.realized_pnl = float(pos.realized_pnl) + realized
                pos.pnl = pos.current_value - pos.initial_value
                pos.percent_pnl = (
                    (pos.pnl / pos.initial_value) if pos.initial_value > 0 else 0.0
                )

                # Remove empty position entries to keep state clean
                if pos.shares <= 0:
                    for k, arr in list(self._positions.items()):
                        self._positions[k] = [p for p in arr if p is not pos]
                    # Drop empty event buckets
                    for k, arr in list(self._positions.items()):
                        if not arr:
                            self._positions.pop(k, None)
                return True
        except Exception as e:
            logger.error(f"❌ 减少模拟持仓失败：{e}")
            return False
