from main import initialize_clob_client
from dotenv import load_dotenv
from main import validate_config
import os
from web3 import Web3
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import ApiCreds, BalanceAllowanceParams, AssetType
from py_clob_client.constants import AMOY


load_dotenv()
validate_config()

TRADE_UNIT = float(os.getenv("trade_unit"))
SLIPPAGE_TOLERANCE = float(os.getenv("slippage_tolerance"))
PCT_PROFIT = float(os.getenv("pct_profit"))
PCT_LOSS = float(os.getenv("pct_loss"))
CASH_PROFIT = float(os.getenv("cash_profit"))
CASH_LOSS = float(os.getenv("cash_loss"))
SPIKE_THRESHOLD = float(os.getenv("spike_threshold"))
SOLD_POSITION_TIME = float(os.getenv("sold_position_time"))
HOLDING_TIME_LIMIT = float(os.getenv("holding_time_limit"))
PRICE_HISTORY_SIZE = int(os.getenv("price_history_size"))
COOLDOWN_PERIOD = int(os.getenv("cooldown_period"))
KEEP_MIN_SHARES = int(os.getenv("keep_min_shares"))
MAX_CONCURRENT_TRADES = int(os.getenv("max_concurrent_trades"))
MIN_LIQUIDITY_REQUIREMENT = float(os.getenv("min_liquidity_requirement"))
# Web3 and API setup
WEB3_PROVIDER = "https://polygon-rpc.com"
YOUR_PROXY_WALLET = Web3.to_checksum_address(os.getenv("YOUR_PROXY_WALLET"))
BOT_TRADER_ADDRESS = Web3.to_checksum_address(os.getenv("BOT_TRADER_ADDRESS"))
USDC_CONTRACT_ADDRESS = os.getenv("USDC_CONTRACT_ADDRESS")
POLYMARKET_SETTLEMENT_CONTRACT = os.getenv("POLYMARKET_SETTLEMENT_CONTRACT")
PRIVATE_KEY = os.getenv("PK")
USE_ONCHAIN_APPROVE = os.getenv("USE_ONCHAIN_APPROVE", "false").lower() == "true"
USE_CHAIN_BALANCE_CHECK = os.getenv("USE_CHAIN_BALANCE_CHECK", "false").lower() == "true"

if __name__ == '__main__':
    client = initialize_clob_client()
    collateral = client.get_balance_allowance(
        params=BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
    )
    print(collateral)
    print(type(collateral))