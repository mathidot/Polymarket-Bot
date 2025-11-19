import time
from collections import deque, defaultdict
from threading import Lock, Event
from typing import Dict, List, Tuple, Optional
from .types import TradeInfo, PositionInfo
from .types import TradeType
from .exceptions import ValidationError
from .config import PRICE_HISTORY_SIZE, KEEP_MIN_SHARES
from .logger import logger

price_update_event = Event()

class ThreadSafeState:
    """线程安全的全局状态容器。

    维护价格历史、活跃交易、资产配对与最近交易时间等信息；
    所有读写通过内部锁保护，适用于多线程环境。
    """
    def __init__(self, max_price_history_size: int = PRICE_HISTORY_SIZE, keep_min_shares: int = KEEP_MIN_SHARES):
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
        self._shutdown_event = Event()
        self._cleanup_complete = Event()
        self._max_price_history_size = max_price_history_size
        self._price_history: Dict[str, deque] = defaultdict(lambda: deque(maxlen=max_price_history_size))
        self._active_trades: Dict[str, TradeInfo] = {}
        self._positions: Dict[str, List[PositionInfo]] = {}
        self._asset_pairs: Dict[str, str] = {}
        self._recent_trades: Dict[str, Dict[str, Optional[float]]] = {}
        self._last_trade_closed_at: float = 0
        self._initialized_assets: set = set()
        self._last_spike_asset: Optional[str] = None
        self._last_spike_price: Optional[float] = None
        self._counter: int = 0
        self._watchlist_tokens: List[str] = []
        self._token_meta: Dict[str, Tuple[str, str]] = {}
        self._sim_lock = Lock()
        self._sim_enabled: bool = False
        self._sim_usdc_balance: float = 0.0
        self._bought_once_lock = Lock()
        self._bought_once: set = set()
    def cleanup(self) -> None:
        """清理内部状态并标记清理完成。"""
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
    def add_price(self, asset_id: str, timestamp: float, price: float, eventslug: str, outcome: str) -> None:
        """向价格历史写入一条记录。"""
        with self._price_history_lock:
            if not isinstance(asset_id, str):
                raise ValidationError(f"Invalid asset_id type: {type(asset_id)}")
            if asset_id not in self._price_history:
                self._price_history[asset_id] = deque(maxlen=self._max_price_history_size)
            self._price_history[asset_id].append((timestamp, price, eventslug, outcome))
    def get_active_trades(self) -> Dict[str, TradeInfo]:
        """返回活跃交易的快照副本。"""
        with self._active_trades_lock:
            return dict(self._active_trades)
    def add_active_trade(self, asset_id: str, trade_info: TradeInfo) -> None:
        """新增或更新指定资产的活跃交易。"""
        with self._active_trades_lock:
            self._active_trades[asset_id] = trade_info
    def remove_active_trade(self, asset_id: str) -> None:
        """移除指定资产的活跃交易。"""
        with self._active_trades_lock:
            self._active_trades.pop(asset_id, None)
    def get_positions(self) -> Dict[str, List[PositionInfo]]:
        with self._positions_lock:
            return dict(self._positions)
    def update_positions(self, new_positions: Dict[str, List[PositionInfo]]) -> None:
        """更新钱包持仓（在 Watchlist 模式下可忽略）。"""
        if new_positions is None:
            return
        if not isinstance(new_positions, dict):
            return
        try:
            with self._positions_lock:
                valid_positions = {}
                for event_id, positions in new_positions.items():
                    if not isinstance(positions, list):
                        continue
                    valid_positions[event_id] = []
                    for pos in positions:
                        if not isinstance(pos, PositionInfo):
                            continue
                        if not pos.asset or not pos.eventslug or not pos.outcome:
                            continue
                        if pos.shares < 0 or pos.avg_price < 0 or pos.current_price < 0:
                            continue
                        valid_positions[event_id].append(pos)
                if valid_positions:
                    self._positions = valid_positions
        except Exception:
            return
    def get_asset_pair(self, asset_id: str) -> Optional[str]:
        with self._asset_pairs_lock:
            return self._asset_pairs.get(asset_id)
    def add_asset_pair(self, asset1: str, asset2: str) -> None:
        """登记资产配对（互指）。"""
        with self._asset_pairs_lock:
            self._asset_pairs[asset1] = asset2
            self._asset_pairs[asset2] = asset1
            self._initialized_assets.add(asset1)
            self._initialized_assets.add(asset2)

    def set_watchlist(self, tokens: List[str], meta: Dict[str, Tuple[str, str]]) -> None:
        """设置监控 token 列表及其元数据（slug/outcome）。"""
        self._watchlist_tokens = tokens
        self._token_meta = meta

    def enable_simulation(self, start_balance_usd: float) -> None:
        with self._sim_lock:
            self._sim_enabled = True
            try:
                self._sim_usdc_balance = float(start_balance_usd)
            except Exception:
                self._sim_usdc_balance = 0.0

    def is_simulation_enabled(self) -> bool:
        with self._sim_lock:
            return self._sim_enabled

    def get_sim_balance(self) -> float:
        with self._sim_lock:
            return float(self._sim_usdc_balance)

    def adjust_sim_balance(self, delta_usd: float) -> float:
        with self._sim_lock:
            try:
                self._sim_usdc_balance = float(self._sim_usdc_balance) + float(delta_usd)
            except Exception:
                pass
            return float(self._sim_usdc_balance)

    def get_watchlist_tokens(self) -> List[str]:
        """获取监控的 token ID 列表。"""
        return list(self._watchlist_tokens)

    def was_bought_once(self, asset_id: str) -> bool:
        with self._bought_once_lock:
            return asset_id in self._bought_once

    def mark_bought_once(self, asset_id: str) -> None:
        with self._bought_once_lock:
            self._bought_once.add(asset_id)

    def get_token_meta(self, token_id: str) -> Tuple[Optional[str], Optional[str]]:
        """返回 token 的 (slug, outcome) 元信息。"""
        m = self._token_meta.get(token_id)
        if not m:
            return None, None
        return m
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
