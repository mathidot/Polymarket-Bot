from dataclasses import dataclass
from enum import Enum


# Exceptions
class BotError(Exception):
    pass


class ConfigurationError(BotError):
    pass


class NetworkError(BotError):
    pass


class TradingError(BotError):
    pass


class ValidationError(BotError):
    pass


# Data models
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


class TradeType(Enum):
    BUY = "buy"
    SELL = "sell"