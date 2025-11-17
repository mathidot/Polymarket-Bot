"""价格读取模块。"""
from typing import Optional
from .state import ThreadSafeState

def get_current_price(state: ThreadSafeState, asset_id: str) -> Optional[float]:
    """获取资产的最新价格。

    Args:
        state: 线程安全状态对象。
        asset_id: 资产 token ID。

    Returns:
        最新价格或 None（无历史时）。
    """
    try:
        history = state.get_price_history(asset_id)
        if not history:
            return None
        return history[-1][1]
    except Exception:
        return None
