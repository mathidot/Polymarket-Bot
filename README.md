# PolySpike Trader

A sophisticated automated trading bot for Polymarket that detects price spikes and executes trades on the Polygon network. The bot implements a spike detection strategy with automatic position management and risk controls.

## Strategy Overview

The bot implements the following trading strategy:

1. **Price Spike Detection**
   - Monitors price movements across market pairs
   - Detects significant price spikes above/below threshold
   - Executes trades when spike conditions are met

2. **Position Management**
   - Automatic take-profit and stop-loss execution
   - Position size management based on account balance
   - Minimum liquidity requirements for trade execution
   - Maximum concurrent trades limit

3. **Risk Management**
   - Slippage protection
   - Maximum holding time limits
   - Minimum liquidity requirements
   - Concurrent trade limits
   - USDC balance checks

## Features

- Multi-pair trading support
- Real-time price monitoring
- Automatic spike detection
- Configurable take-profit and stop-loss levels
- USDC balance management
- Automatic API credential refresh
- Comprehensive logging system
- Thread-safe state management
- Error handling and recovery
- Graceful shutdown handling
- Simulation mode (paper trading): no on-chain transactions, configurable starting balance

## Prerequisites

- Python 3.7+
- MetaMask wallet with Polygon network
- USDC balance on Polygon network
- Polymarket account

## Installation

1. Creat venv:
```bash
python -m venv .venv
.venv/Scripts/activate
```

2. Install required Python packages:
```bash
pip install web3==6.11.1
pip install python-dotenv==1.0.0
pip install requests==2.31.0
pip install py-clob-client==0.1.0
pip install halo==0.0.31
pip install colorlog==6.7.0
pip install dotenv
```

3. Create a `.env` file with your configuration:
```env
# Wallet Configuration
PK=your_private_key_here
YOUR_PROXY_WALLET=your_proxy_wallet_address
BOT_TRADER_ADDRESS=your_trader_address
USDC_CONTRACT_ADDRESS=0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
POLYMARKET_SETTLEMENT_CONTRACT=0x56C79347e95530c01A2FC76E732f9566dA16E113

# Trading Parameters
trade_unit=3.0
slippage_tolerance=0.02
pct_profit=0.03
pct_loss=-0.025
cash_profit=1.0
cash_loss=-0.75
spike_threshold=0.02
sold_position_time=120
holding_time_limit=3600
price_history_size=100
cooldown_period=10
keep_min_shares=1
max_concurrent_trades=3
min_liquidity_requirement=10.0
orderbook_cache_enabled=true
orderbook_cache_ttl=1.0
```

## Configuration Parameters

### Wallet Settings
- `PK`: Your wallet's private key
- `YOUR_PROXY_WALLET`: Your Polymarket proxy wallet address
- `BOT_TRADER_ADDRESS`: Your MetaMask wallet address
- `USDC_CONTRACT_ADDRESS`: USDC contract address on Polygon
- `POLYMARKET_SETTLEMENT_CONTRACT`: Polymarket settlement contract address

### Trading Parameters
- `trade_unit`: Base trade size in USDC
- `slippage_tolerance`: Maximum allowed slippage (e.g., 0.02 for 2%)
- `pct_profit`: Take profit threshold (e.g., 0.03 for 3%)
- `pct_loss`: Stop loss threshold (e.g., -0.025 for -2.5%)
- `cash_profit`: Take profit in USDC
- `cash_loss`: Stop loss in USDC
- `spike_threshold`: Minimum price movement to trigger trade
- `sold_position_time`: Cooldown period between trades (seconds)
- `holding_time_limit`: Maximum time to hold a position (seconds)
- `price_history_size`: Number of price points to track
- `cooldown_period`: Retry cooldown for failed orders (seconds)
- `keep_min_shares`: Minimum shares to keep when selling
- `max_concurrent_trades`: Maximum number of concurrent trades
- `min_liquidity_requirement`: Minimum liquidity required to trade (USDC)

### Logging/Output Parameters
- `positions_log_throttle_secs`: Throttle interval (seconds) for real-time positions snapshot printing. Default `2.0`.

### Order Book Cache
- `orderbook_cache_enabled`: Enable batch order book cache reads/writes. Default `true`. Set to `false` to always fetch fresh books.
- `orderbook_cache_ttl`: Cache TTL in seconds for batch order books. Default `1.0`. Set to `0` to effectively disable cache by time.

## Running the Bot

1. Ensure your `.env` file is properly configured
2. Run the bot:
```bash
python test.py
```

### Simulation Mode (Paper Trading)

- Enable simulation mode to test strategies without connecting钱包或提交链上交易。
- Add the following to your `.env`:

```
simulation_mode=true
sim_start_usdc=10000
# 推荐：初始化资产对采用配置/市场模式，而非 positions
init_pair_mode=config
config_interest_json=interest_markets.json  # 或使用 config_interest_slugs

# 可选：初始化模拟持仓（两种方式二选一）
# 方式 A：内联 JSON（数组）
sim_init_positions=[
  {"asset": "516706", "shares": 50, "avg_price": 0.45, "eventslug": "us-election-2024", "outcome": "Yes"},
  {"asset": "516707", "shares": 50, "avg_price": 0.55, "eventslug": "us-election-2024", "outcome": "No"}
]
# 方式 B：指向 JSON 文件路径（内容同上）
sim_init_positions_json=sim_positions.json
# 根据 eventslug 自动配对资产（默认 true）
sim_positions_auto_pair=true
```

- When `simulation_mode=true`:
  - USDC 余额与持仓在内存中维护，可通过 `sim_start_usdc` 设置初始余额。
  - 可通过 `sim_init_positions` 或 `sim_init_positions_json` 预置模拟持仓；支持为同一事件的两边自动建立资产对（`sim_positions_auto_pair=true`）。
  - 买卖逻辑仍使用实时价格与订单簿深度进行风控（滑点与流动性检查），但不会触发真实下单或授权。
  - `ensure_usdc_allowance` 与链上 USDC 余额查询被跳过，所有交易通过状态对象更新。
  - 建议使用 `init_pair_mode=config` 或 `init_pair_mode=markets` 初始化资产。

> 提示：模拟模式下日志会包含 `[SIM]` 标记，便于区分真实与模拟执行路径。

## Logging

The bot maintains detailed logs in the `logs` directory:
- `polymarket_bot.log`: Main log file with all bot activities
- Logs include price updates, trade executions, and error messages
- Color-coded console output for easy monitoring

## Safety Features

- Automatic retry mechanism for failed orders
- USDC balance checks before trades
- Comprehensive error handling and logging
- Transaction receipt verification
- Thread-safe state management
- Graceful shutdown handling

## Important Notes

- Ensure sufficient USDC balance in your wallet
- Monitor the bot's logs regularly
- The bot maintains minimum shares when selling
- Trades are executed with configurable unit size
- API credentials are refreshed hourly
- Price updates occur every second
- Position checks occur every second

## Disclaimer

This bot is for educational purposes only. Trading cryptocurrencies and prediction markets involves significant risk. Use at your own risk and never trade with funds you cannot afford to lose.

## License

[Your License Here]

## Bot Structure

![Bot Architecture Diagram](diagram.png)

1. **State Management**
   - Global variables for tracking trades and prices
   - Price history management
   - Active trade tracking

2. **Trading Logic**
   - Price spike detection
   - Order placement with retries
   - Take-profit and stop-loss management
   - USDC allowance management

3. **Main Loop**
   - Price updates
   - Trade detection
   - Position management
   - API credential refresh

## Contact ME
[Telegram](https://t.me/trust4120)
