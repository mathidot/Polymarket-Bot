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

3. Create a `.env` file with your configuration (新增检测与执行配置可选项):
```env
# Wallet Configuration
PK=your_private_key_here
YOUR_PROXY_WALLET=your_proxy_wallet_address
BOT_TRADER_ADDRESS=your_trader_address
USDC_CONTRACT_ADDRESS=0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174
POLYMARKET_SETTLEMENT_CONTRACT=0x56C79347e95530c01A2FC76E732f9566dA16E113

# On-Chain Toggles (API-only trading)
USE_ONCHAIN_APPROVE=false
USE_CHAIN_BALANCE_CHECK=false

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

# 请求与会话
REQUESTS_VERIFY_SSL=true

# 检测窗口与自适应尖刺阈值（可选）
DETECT_LOOKBACK_SAMPLES=20           # 按样本数回看窗口
DETECT_LOOKBACK_SECONDS=0            # 按时间窗口，>0 启用秒模式
DELTA_MODE=samples                   # samples|seconds
PRICE_SOURCE_DETECT=mid              # 检测价源：mid|ask|bid（预留）
DYNAMIC_SPIKE_ENABLE=true            # 启用动态尖刺阈值
SPIKE_VOL_K=1.2                      # 阈值中的波动项系数 k
SPIKE_SPREAD_BUFFER=0.005            # 阈值中的价差缓冲项
DEPTH_USD_TARGET=3.0                 # 深度评估目标美元量，默认=trade_unit
MAX_DEPTH_LEVELS=5                   # 订单簿聚合档位数
MIN_TRIGGER_INTERVAL_SECONDS=15      # 同资产最小触发间隔（防回转）
```

## 配置参数

### Wallet Settings
- `PK`: Your wallet's private key
- `YOUR_PROXY_WALLET`: Your Polymarket proxy wallet address
- `BOT_TRADER_ADDRESS`: Your MetaMask wallet address
- `USDC_CONTRACT_ADDRESS`: USDC contract address on Polygon
- `POLYMARKET_SETTLEMENT_CONTRACT`: Polymarket settlement contract address
- `USE_ONCHAIN_APPROVE`: Set to `false` to skip on-chain USDC approve
- `USE_CHAIN_BALANCE_CHECK`: Set to `false` to avoid on-chain balance reads

### 交易参数
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
- 交易金额上限：每次买入或卖出的美元金额均不超过 `trade_unit`（买入以可成交深度USD与 `trade_unit` 取最小值，卖出按 VWAP 将份额上限限制为 `trade_unit / vwap`）

### 检测与执行改动（新增）
- 固定回看窗口：尖刺 `delta` 使用最近 N 个样本或最近 T 秒的首尾价变化，替代历史首尾计算。
- 自适应阈值：实时计算价差 `spread=ask-bid` 与窗口波动率 `σ`，阈值 `threshold = max(spike_threshold, SPIKE_VOL_K*σ, spread+SPIKE_SPREAD_BUFFER)`。
- 深度与滑点评估：下单前聚合最优前 `MAX_DEPTH_LEVELS` 档，估算 VWAP 与可成交美元深度；若 `DepthUSD < min_liquidity_requirement` 或滑点超出 `slippage_tolerance` 则跳过。
- 日志增强：检测打印 `delta/threshold/spread/sigma/窗口大小`；执行打印 `VWAP/DepthUSD/Slippage/Amount`。
- 交易金额上限：每次买入或卖出的美元金额均不超过 `trade_unit`（买入以可成交深度USD与 `trade_unit` 取最小值，卖出按 VWAP 将份额上限限制为 `trade_unit / vwap`）
- `price_lower_bound`: 尖刺检测后允许交易的价格下界（默认 0.20）
- `price_upper_bound`: 尖刺检测后允许交易的价格上界（默认 0.80）

## 运行方式

- 确保 `.env` 已配置完整
- 启动入口：
```bash
python app.py
```
启动后会输出 Watchlist 汇总日志：`slugs/tokens/pairs` 统计与前 20 条 `token → outcome/slug` 映射，便于确认加载情况。

监控清单文件（仅 Watchlist 模式）：在项目根目录创建 `watchlist_slugs.json`：
```json
{
  "slugs": [
    "fed-decision-in-december"
  ]
}
```

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

## 模块结构

![Bot Architecture Diagram](diagram.png)

`polymarket_bot/config.py`、`logger.py`、`exceptions.py`、`types.py`、`state.py`、`client.py`、`api.py`、`orderbook.py`、`pricing.py`、`trading.py`、`detection.py`、`threads.py` 与 `app.py`
- 监控清单：`watchlist_slugs.json`（按 slug 解析 Market/Token 进行监控，不依赖钱包持仓）

## 测试

使用内置 `unittest` 运行基础单元测试：
```bash
python -m unittest
```
