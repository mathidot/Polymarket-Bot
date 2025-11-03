import os
from dotenv import load_dotenv
from web3 import Web3

# Load env once at import
load_dotenv('.env')


def validate_config() -> None:
    # Detect simulation mode early to relax requirements
    sim_mode = os.getenv('simulation_mode', 'false').lower() == 'true'

    # Base required vars (strategy parameters)
    required_vars = {
        'trade_unit': float,
        'slippage_tolerance': float,
        'pct_profit': float,
        'pct_loss': float,
        'cash_profit': float,
        'cash_loss': float,
        'spike_threshold': float,
        'sold_position_time': float,
        'holding_time_limit': float,
        'max_concurrent_trades': int,
        'min_liquidity_requirement': float,
    }

    # Only require on-chain and wallet configs when NOT in simulation mode
    if not sim_mode:
        required_vars.update({
            'YOUR_PROXY_WALLET': str,
            'BOT_TRADER_ADDRESS': str,
            'USDC_CONTRACT_ADDRESS': str,
            'POLYMARKET_SETTLEMENT_CONTRACT': str,
            'PK': str,
        })

    missing = []
    invalid = []

    for var, var_type in required_vars.items():
        value = os.getenv(var)
        if not value:
            missing.append(var)
            continue
        try:
            if var_type == float:
                float(value)
            elif var_type == int:
                int(value)
            elif var_type == str:
                str(value)
        except ValueError:
            invalid.append(var)

    if missing or invalid:
        error_msg = []
        if missing:
            error_msg.append(f"Missing variables: {', '.join(missing)}")
        if invalid:
            error_msg.append(f"Invalid values for: {', '.join(invalid)}")
        raise ValueError(' | '.join(error_msg))


# Validate at import
validate_config()

# Trading parameters
TRADE_UNIT = float(os.getenv('trade_unit'))
SLIPPAGE_TOLERANCE = float(os.getenv('slippage_tolerance'))
PCT_PROFIT = float(os.getenv('pct_profit'))
PCT_LOSS = float(os.getenv('pct_loss'))
CASH_PROFIT = float(os.getenv('cash_profit'))
CASH_LOSS = float(os.getenv('cash_loss'))
# Spike thresholds
# Backward-compatible: if dedicated up/down thresholds are not provided,
# fall back to the single 'spike_threshold'.
SPIKE_THRESHOLD = float(os.getenv('spike_threshold'))
SPIKE_THRESHOLD_UP = float(os.getenv('spike_threshold_up', os.getenv('spike_threshold', '0.02')))
SPIKE_THRESHOLD_DOWN = float(os.getenv('spike_threshold_down', os.getenv('spike_threshold', '0.02')))
SOLD_POSITION_TIME = float(os.getenv('sold_position_time'))
HOLDING_TIME_LIMIT = float(os.getenv('holding_time_limit'))
PRICE_HISTORY_SIZE = int(os.getenv('price_history_size'))
COOLDOWN_PERIOD = int(os.getenv('cooldown_period'))
KEEP_MIN_SHARES = int(os.getenv('keep_min_shares'))
MAX_CONCURRENT_TRADES = int(os.getenv('max_concurrent_trades'))
MIN_LIQUIDITY_REQUIREMENT = float(os.getenv('min_liquidity_requirement'))

# Simulation mode
SIMULATION_MODE = os.getenv('simulation_mode', 'false').lower() == 'true'
SIM_START_USDC = float(os.getenv('sim_start_usdc', '10000'))
SIM_INIT_POSITIONS = os.getenv('sim_init_positions', '')
SIM_INIT_POSITIONS_JSON = os.getenv('sim_init_positions_json', '')
SIM_POSITIONS_AUTO_PAIR = os.getenv('sim_positions_auto_pair', 'true').lower() == 'true'

# Pair initialization
INIT_PAIR_MODE = os.getenv('init_pair_mode', 'positions').lower()
CONFIG_ASSET_PAIRS = os.getenv('config_asset_pairs', '')
CONFIG_INTEREST_SLUGS = os.getenv('config_interest_slugs', '')
CONFIG_INTEREST_JSON = os.getenv('config_interest_json', 'interest_markets.json')
MARKET_FETCH_LIMIT = int(os.getenv('market_fetch_limit', '50'))

# Network and wallet config
WEB3_PROVIDER = 'https://polygon-rpc.com'
YOUR_PROXY_WALLET = Web3.to_checksum_address(os.getenv('YOUR_PROXY_WALLET')) if not SIMULATION_MODE else '0x0000000000000000000000000000000000000000'
BOT_TRADER_ADDRESS = Web3.to_checksum_address(os.getenv('BOT_TRADER_ADDRESS')) if not SIMULATION_MODE else '0x0000000000000000000000000000000000000000'
USDC_CONTRACT_ADDRESS = os.getenv('USDC_CONTRACT_ADDRESS') if not SIMULATION_MODE else ''
POLYMARKET_SETTLEMENT_CONTRACT = os.getenv('POLYMARKET_SETTLEMENT_CONTRACT') if not SIMULATION_MODE else ''
PRIVATE_KEY = os.getenv('PK') if not SIMULATION_MODE else ''

# System/runtime constants
MAX_RETRIES = 3
BASE_DELAY = 1
MAX_ERRORS = 5
API_TIMEOUT = 10
REFRESH_INTERVAL = 3600
THREAD_POOL_SIZE = 3
MAX_QUEUE_SIZE = 1000
THREAD_CHECK_INTERVAL = 5
THREAD_RESTART_DELAY = 2

# Optional strategy parameters for pair-sum arbitrage
# When sum of Yes+No < entry threshold, buy both; when > exit threshold, sell both
ARB_ENTRY_SUM_THRESHOLD = float(os.getenv('arb_entry_sum_threshold', '0.995'))
ARB_EXIT_SUM_THRESHOLD = float(os.getenv('arb_exit_sum_threshold', '1.005'))

# Optional networking config
REQUESTS_VERIFY_SSL = os.getenv('requests_verify_ssl', 'true').lower() != 'false'

# Order book batching and caching (optional)
ORDERBOOK_CACHE_TTL = float(os.getenv('orderbook_cache_ttl', '1.0'))  # seconds
# Enable/disable order book cache usage globally
ORDERBOOK_CACHE_ENABLED = os.getenv('orderbook_cache_enabled', 'true').lower() in ('1', 'true', 'yes')
ORDERBOOK_RETRY_MAX = int(os.getenv('orderbook_retry_max', '3'))
ORDERBOOK_RETRY_BASE_DELAY = float(os.getenv('orderbook_retry_base_delay', '0.2'))  # seconds
ORDERBOOK_RETRY_JITTER_MS = int(os.getenv('orderbook_retry_jitter_ms', '50'))

# Price update performance knobs (optional)
# Number of assets to update per loop (round-robin). Set to <=0 to update all.
PRICE_UPDATE_BATCH_SIZE = int(os.getenv('price_update_batch_size', '20'))
# Minimum interval per loop (seconds). Controls sleep throttle.
PRICE_UPDATE_MIN_INTERVAL = float(os.getenv('price_update_min_interval', '1.0'))
# Whether to allow costly fallbacks (per-token price/orderbook calls) when batch books are missing.
PRICE_UPDATE_FALLBACK_ENABLED = os.getenv('price_update_fallback_enabled', 'true').lower() in ('1', 'true', 'yes')
# Cooperative yielding: insert micro-sleeps during inner loops
PRICE_UPDATE_YIELD_EVERY_N = int(os.getenv('price_update_yield_every_n', '10'))
PRICE_UPDATE_YIELD_SLEEP_MS = int(os.getenv('price_update_yield_sleep_ms', '0'))

# Positions log printing (optional)
POSITIONS_LOG_THROTTLE_SECS = float(os.getenv('positions_log_throttle_secs', '2.0'))

# Market making parameters (optional)
MM_SPREAD_BPS = float(os.getenv('mm_spread_bps', '50'))  # 0.50% absolute spread
MM_ORDER_SIZE = float(os.getenv('mm_order_size', str(TRADE_UNIT)))
MM_MAX_INVENTORY = float(os.getenv('mm_max_inventory', '100'))  # per asset
MM_REFRESH_INTERVAL = int(os.getenv('mm_refresh_interval', '15'))  # seconds
MM_CANCEL_STALE_MS = int(os.getenv('mm_cancel_stale_ms', '60000'))
MM_USE_POST_ONLY = os.getenv('mm_use_post_only', 'true').lower() == 'true'

# Mean reversion parameters (optional)
MR_LOOKBACK = int(os.getenv('mr_lookback', '60'))
MR_ENTRY_Z = float(os.getenv('mr_entry_z', '1.5'))
MR_EXIT_Z = float(os.getenv('mr_exit_z', '0.8'))
MR_MAX_HOLD_SECS = int(os.getenv('mr_max_hold_secs', '600'))