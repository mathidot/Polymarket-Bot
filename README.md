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

# 概率阈值策略（可选）
PROB_THRESHOLD_STRATEGY_ENABLE=false # 启用“概率阈值入场/止损”策略
PROB_ENTRY_THRESHOLD=0.80            # 入场价格阈值（例如 0.80=80%）
PROB_STOP_THRESHOLD=0.60             # 止损价格阈值（例如 0.60=60%）
```

4. 并发与频率（可选）
```env
# 采价并发与周期
FETCH_INTERVAL_MS=200                  # 每轮采价的最小周期，毫秒
FETCH_CONCURRENCY=4                    # 采价并发线程数

# 检测/退出并发
DETECT_CONCURRENCY=4                   # 检测并发线程数（按资产）
EXIT_CONCURRENCY=4                     # 退出并发线程数（按交易）

# 价格新鲜度
PRICE_FRESHNESS_SECONDS=2.5            # 超过该秒数未更新则跳过该资产
```

5. 模拟模式（可选）
```env
SIM_MODE=true                          # 启用模拟账户，不发真实订单
SIM_START_USDC=100.0                   # 初始模拟 USDC 余额
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
- 交易金额/份额：买入美元金额不超过 `trade_unit`（以可成交深度USD与 `trade_unit` 取最小值）；卖出为一次性全清仓

### 检测与执行改动（新增）
- 两点检测（默认）：主检测函数仅比较“上一个价格 vs 当前价格”的相对变化 `delta = (p1-p0)/p0`。
- 窗口检测（可选）：备用函数支持按最近 N 个样本或 T 秒计算窗口 `delta` 并结合动态阈值；仅在需要时启用。
- 自适应阈值：实时计算价差 `spread=ask-bid` 与窗口波动率 `σ`，阈值 `threshold = max(spike_threshold, SPIKE_VOL_K*σ, spread+SPIKE_SPREAD_BUFFER)`。
- 并发检测：按资产并行处理，受 `DETECT_CONCURRENCY` 控制；退出检查按交易并行，受 `EXIT_CONCURRENCY` 控制。
- 采价并发与周期：按 `FETCH_CONCURRENCY` 并行抓取价格；以 `FETCH_INTERVAL_MS` 控制最小轮询周期；拿不到价格不重试，当前轮直接跳过。
- 买入逻辑简化：买入使用最优卖价与卖家可卖量，美元金额不超过 `trade_unit`；卖出侧按 VWAP 将份额上限限制为 `trade_unit / vwap`。
- 买入逻辑与卖出行为：买入使用最优卖价与卖家可卖量，美元金额不超过 `trade_unit`；卖出为一次性全清仓。
- 日志增强：检测打印 `delta/threshold/spread/sigma/窗口大小`；执行打印买卖理由与成交详情。
- `price_lower_bound`: 尖刺检测后允许交易的价格下界（默认 0.20）
- `price_upper_bound`: 尖刺检测后允许交易的价格上界（默认 0.80）

### 模拟模式
- `SIM_MODE`: 启用后买卖均在本地模拟，不调用真实下单接口
- `SIM_START_USDC`: 初始模拟余额；买入扣减余额，卖出增加余额
- 买入：使用最优卖价与卖家可卖量，美元金额不超过 `trade_unit`，且不超过当前模拟余额
- 卖出：以当前价格作为成交价，一次性全清仓
- 余额/额度不足：仅打印告警并跳过该笔交易，不报错、不重试

### 概率阈值策略
- 入场：当某个选项最新价格达到 `PROB_ENTRY_THRESHOLD`（如 0.80）时买入；每个选项只买一次
- 止损：当该选项最新价格低于等于 `PROB_STOP_THRESHOLD`（如 0.60）时卖出止损
- 金额：买入金额由 `trade_unit` 控制（例如 `trade_unit=5` 即买入 5 USDC）
- 线程：启用后运行独立线程 `prob_strategy`（检测）与 `prob_exits`（退出），替代默认尖刺检测与退出线程
- 价格过滤：遵守 `price_lower_bound/price_upper_bound` 与 `PRICE_FRESHNESS_SECONDS` 新鲜度校验

## 运行方式

- 确保 `.env` 已配置完整
- 启动入口：
```bash
python app.py
```
启动后会输出 Watchlist 汇总日志：`slugs/tokens/pairs` 统计与前 20 条 `token → outcome/slug` 映射，便于确认加载情况。

启用概率阈值策略：在 `.env` 设定
```env
PROB_THRESHOLD_STRATEGY_ENABLE=true
PROB_ENTRY_THRESHOLD=0.80
PROB_STOP_THRESHOLD=0.60
trade_unit=5
min_liquidity_requirement=50
```
随后运行入口启动即可生效。

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
- 价格采集周期由 `FETCH_INTERVAL_MS` 控制（默认 200ms），并以 `FETCH_CONCURRENCY` 并发拉取
- 检测与退出评估按事件驱动与每秒轮询混合执行，受并发参数控制
- Watchlist 使用 Gamma API 解析 `slug → markets → tokens`，避免 `www.polymarket.com/api/events/...` 的 404 问题
- 请求会话使用 `requests.Session + Retry` 并可通过 `REQUESTS_VERIFY_SSL` 控制证书验证
 - 模拟模式启用时不会发出真实订单，日志将包含 `SIM BUY` / `SIM SELL` 标记

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
