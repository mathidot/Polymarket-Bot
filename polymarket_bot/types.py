from dataclasses import dataclass
from enum import Enum

class TradeType(Enum):
    BUY = "buy"
    SELL = "sell"

@dataclass
class TradeInfo:
    entry_price: float
    entry_time: float
    amount: float
    bot_triggered: bool

@dataclass
class PositionInfo:
    eventslug: str
    outcome: str
    asset: str
    avg_price: float
    shares: float
    current_price: float
    initial_value: float
    current_value: float
    pnl: float
    percent_pnl: float
    realized_pnl: float
