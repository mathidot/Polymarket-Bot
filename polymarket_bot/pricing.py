from typing import Optional
from .state import ThreadSafeState

def get_current_price(state: ThreadSafeState, asset_id: str) -> Optional[float]:
    try:
        history = state.get_price_history(asset_id)
        if not history:
            return None
        return history[-1][1]
    except Exception:
        return None
