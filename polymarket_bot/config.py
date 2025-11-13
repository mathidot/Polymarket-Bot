import os
from dotenv import load_dotenv

load_dotenv(".env")

def _require(vars_types):
    missing = []
    invalid = []
    for var, var_type in vars_types.items():
        value = os.getenv(var)
        if not value:
            missing.append(var)
            continue
        try:
            if var_type is float:
                float(value)
            elif var_type is int:
                int(value)
            else:
                str(value)
        except ValueError:
            invalid.append(var)
    if missing or invalid:
        raise ValueError("Missing variables: " + ", ".join(missing) + " | Invalid values for: " + ", ".join(invalid))

_require({
    "trade_unit": float,
    "slippage_tolerance": float,
    "pct_profit": float,
    "pct_loss": float,
    "cash_profit": float,
    "cash_loss": float,
    "spike_threshold": float,
    "sold_position_time": float,
    "YOUR_PROXY_WALLET": str,
    "BOT_TRADER_ADDRESS": str,
    "USDC_CONTRACT_ADDRESS": str,
    "POLYMARKET_SETTLEMENT_CONTRACT": str,
    "PK": str,
    "holding_time_limit": float,
    "max_concurrent_trades": int,
    "min_liquidity_requirement": float,
})

TRADE_UNIT = float(os.getenv("trade_unit"))
SLIPPAGE_TOLERANCE = float(os.getenv("slippage_tolerance"))
PCT_PROFIT = float(os.getenv("pct_profit"))
PCT_LOSS = float(os.getenv("pct_loss"))
CASH_PROFIT = float(os.getenv("cash_profit"))
CASH_LOSS = float(os.getenv("cash_loss"))
SPIKE_THRESHOLD = float(os.getenv("spike_threshold"))
SOLD_POSITION_TIME = float(os.getenv("sold_position_time"))
HOLDING_TIME_LIMIT = float(os.getenv("holding_time_limit"))
PRICE_HISTORY_SIZE = int(os.getenv("price_history_size", "120"))
COOLDOWN_PERIOD = int(os.getenv("cooldown_period", "30"))
KEEP_MIN_SHARES = int(os.getenv("keep_min_shares", "0"))
MAX_CONCURRENT_TRADES = int(os.getenv("max_concurrent_trades"))
MIN_LIQUIDITY_REQUIREMENT = float(os.getenv("min_liquidity_requirement"))
PRICE_LOWER_BOUND = float(os.getenv("price_lower_bound", "0.20"))
PRICE_UPPER_BOUND = float(os.getenv("price_upper_bound", "0.80"))

WEB3_PROVIDER = "https://polygon-rpc.com"
YOUR_PROXY_WALLET = os.getenv("YOUR_PROXY_WALLET")
BOT_TRADER_ADDRESS = os.getenv("BOT_TRADER_ADDRESS")
USDC_CONTRACT_ADDRESS = os.getenv("USDC_CONTRACT_ADDRESS")
POLYMARKET_SETTLEMENT_CONTRACT = os.getenv("POLYMARKET_SETTLEMENT_CONTRACT")
PRIVATE_KEY = os.getenv("PK")
USE_ONCHAIN_APPROVE = os.getenv("USE_ONCHAIN_APPROVE", "false").lower() == "true"
USE_CHAIN_BALANCE_CHECK = os.getenv("USE_CHAIN_BALANCE_CHECK", "false").lower() == "true"

MAX_RETRIES = 3
BASE_DELAY = 1
MAX_ERRORS = 5
API_TIMEOUT = 10
REFRESH_INTERVAL = 3600
THREAD_POOL_SIZE = 3
MAX_QUEUE_SIZE = 1000
THREAD_CHECK_INTERVAL = 5
THREAD_RESTART_DELAY = 2
