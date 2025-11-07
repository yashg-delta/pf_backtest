# Portfolio Backtest Framework - Development Guide

## Project Purpose

This is a **high-performance cryptocurrency portfolio backtesting framework** designed to test ranking-based trading strategies on cryptographic assets (primarily trading Crypto USD perpetual futures). The framework simulates realistic trading scenarios including transaction costs, position management, and portfolio rebalancing on daily OHLCV data.

**Key Design Goals:**
- Config-driven architecture for easy strategy experimentation
- Memory-efficient data loading using DuckDB and Polars
- Support for complex factor calculations with cross-sectional ranking
- Extensible filter system for regime-based trading
- Comprehensive analytics and visualization of backtest results

---

## Architecture Overview

### High-Level Data Flow

```
Config File (YAML)
    ↓
DataLoader (DuckDB/Polars) → Load daily OHLCV data
    ↓
UniverseSelector → Select top-N liquid symbols
    ↓
BacktestEngine (Main Orchestrator)
    ├─→ Strategy: Generate signals based on factors
    ├─→ Strategy: Apply regime filters
    ├─→ Strategy: Calculate target positions
    ├─→ ExecutionEngine: Generate orders
    ├─→ ExecutionEngine: Execute trades with costs
    └─→ PortfolioTracker: Update P&L and positions
    ↓
TradeAnalyzer → Format trades and aggregate to daily
    ↓
MetricsCalculator → Compute performance metrics
    ↓
Visualizer → Generate charts and heatmaps
    ↓
Results (CSV files, PNG charts, metrics)
```

### Module Structure

```
framework/
├── data/
│   ├── loader.py         # DuckDB-based data loading with selective columns
│   ├── universe.py       # Universe selection by dollar volume
│   └── validator.py      # Data quality checks and cleaning
├── engine/
│   ├── backtest.py       # Main BacktestEngine orchestrator
│   ├── portfolio.py      # PortfolioTracker for P&L and positions
│   └── execution.py      # ExecutionEngine for order generation/execution
├── analytics/
│   ├── metrics.py        # Performance metrics (Sharpe, Sortino, Calmar, etc.)
│   ├── trades.py         # Trade sheet generation and aggregation
│   └── visualizations.py # Equity curves, drawdown, monthly heatmaps
└── utils/
    └── calendar.py       # Trading calendar for rebalancing schedule

strategies/
├── base.py               # Abstract BaseStrategy class
├── ranking_strategy.py   # Concrete RankingStrategy implementation
└── filters.py            # RegimeFilter: BTC MA, volatility filters

factors/
└── calculations.py       # FactorCalculator: momentum, volume_ratio, z-score

configs/
└── *.yaml               # Strategy configuration files

run_backtest.py         # Entry point - orchestrates entire workflow
```

---

## Framework Components

### 1. Data Module

#### DataLoader (`framework/data/loader.py`)

**Purpose:** Efficiently load cryptocurrency OHLCV data using DuckDB as query engine.

**5 Core Required Columns:**
- `SYMBOL`: Asset identifier (e.g., 'BTCUSDT')
- `BAR_TIMESTAMP`: Datetime of daily bar (one per trading day)
- `CLOSE_PRICE`: Close price
- `VOLUME`: Total volume
- `NOTIONAL_VOLUME`: Volume in USD (dollar volume)

**Key Methods:**
```python
loader = DataLoader('/path/to/data')

# Load trades data with selective columns
trades = loader.load_trades_data(
    start_date=datetime(2022, 1, 1),
    end_date=datetime(2025, 9, 30),
    symbols=['BTCUSDT', 'ETHUSDT'],
    columns=['SYMBOL', 'BAR_TIMESTAMP', 'CLOSE_PRICE', 'NOTIONAL_VOLUME']
)

# Load all data with optional liquidations/ratios
all_data = loader.load_all_data(
    start_date=start_date,
    end_date=end_date,
    columns=specific_columns,  # Optional: selective loading
    load_liquidations=True,    # Optional: if available
    load_ratios=True          # Optional: if available
)
```

**Selective Column Loading System:**
- Default columns: `[SYMBOL, BAR_TIMESTAMP, CLOSE_PRICE, VOLUME, NOTIONAL_VOLUME]`
- Specify custom columns in config → strategy only loads needed data
- Reduces memory footprint for large time periods
- Example: If strategy only needs close prices, skip VOLUME columns

**Data Sources (DuckDB Views):**
- `tardis_trades_daily.parquet` - Main trading data (daily OHLCV bars)
- `tardis_liquidations_5min.parquet` - Liquidation events (optional, 5-min granularity)
- `tardis_derivative_ticker_5min.parquet` - Derivative metrics (optional, 5-min granularity)
- `bv_ratios.csv.gz` - Buy/sell volume ratios (optional)

#### UniverseSelector (`framework/data/universe.py`)

**Purpose:** Select top-N most liquid symbols for each rebalancing.

**Selection Criteria:**
- Top-N by average notional volume (dollar volume)
- Minimum data coverage: Required number of daily bars
  - Expected: `lookback_days` bars (1 bar per trading day)
  - Filter: Keep symbols with ≥ `min_data_coverage` * expected bars

**Example:**
```python
selector = UniverseSelector(
    top_n=50,                    # Top 50 assets
    volume_lookback_days=14,    # 14-day lookback for volume
    min_data_coverage=0.95      # Must have 95% of bars
)

universe = selector.select_universe(
    data=all_data,
    current_date=timestamp
)
# Returns: ['BTCUSDT', 'ETHUSDT', ...] (up to 50 symbols)
```

#### DataValidator (`framework/data/validator.py`)

**Purpose:** Ensure data quality before backtest.

**Validation Checks:**
- All 5 core columns present
- No null values in CLOSE_PRICE and VOLUME
- Positive prices only
- Daily bar continuity (detect missing trading days)

**Usage:**
```python
is_valid, issues = DataValidator.validate_trades_data(trades_df)
if issues:
    logger.warning(f"Data issues: {issues}")

clean_data = DataValidator.clean_data(trades_df)
# Removes rows with invalid prices/volumes and sorts by timestamp
```

---

### 2. Engine Module

#### BacktestEngine (`framework/engine/backtest.py`)

**Core Orchestrator** - Controls entire backtest simulation.

**Key Responsibilities:**
1. Load data for entire period (upfront)
2. Iterate through all daily timestamps
3. Update portfolio with current market prices
4. Check rebalance schedule
5. Execute rebalancing logic (signals → positions → trades)
6. Record portfolio snapshots
7. Generate final results

**Main Loop:**
```python
for timestamp in all_timestamps:
    # Update portfolio prices
    current_data = get_bar_data(timestamp)
    update_portfolio_values(current_data)
    
    # Rebalance if scheduled
    if calendar.is_rebalance_time(timestamp):
        universe = select_universe(data, timestamp)
        signals = strategy.generate_signals(data, timestamp)
        filtered_signals = strategy.apply_filters(signals)
        target_positions = strategy.calculate_positions(filtered_signals)
        execute_rebalancing(target_positions)
    
    # Record snapshot
    record_portfolio_snapshot(timestamp)
```

**Configuration (`BacktestConfig`):**
```python
config = BacktestConfig(
    start_date=datetime(2022, 1, 1),
    end_date=datetime(2025, 9, 30),
    initial_capital=1_000_000,
    transaction_cost_bps=3,         # 3 basis points per trade
    rebalance_frequency='weekly',   # 'daily', 'weekly', 'monthly'
    rebalance_day='monday',         # Day of week
    rebalance_time='00:00'          # HH:MM format
)
```

#### PortfolioTracker (`framework/engine/portfolio.py`)

**Purpose:** Track portfolio state and P&L.

**Tracks:**
- Cash balance (updated by trades)
- Open positions (quantity, avg price, entry time)
- Cumulative P&L and transaction costs
- Position values at market prices

**Interface:**
```python
portfolio = PortfolioTracker(initial_capital=1_000_000)

# Process each trade
portfolio.process_trade({
    'symbol': 'BTCUSDT',
    'side': 'buy',
    'quantity': 0.5,
    'price': 45000,
    'transaction_cost': 67.50  # In USD
})

# Get portfolio state at any timestamp
snapshot = portfolio.get_snapshot(
    timestamp=current_time,
    positions=current_positions,  # Dict of open positions
    current_data=bar_data
)
# Returns: {
#   'timestamp': ...,
#   'cash': 999932.50,
#   'positions_value': 22500,
#   'total_value': 1022432.50,
#   'pnl': 22432.50,
#   'pnl_pct': 2.24,
#   ...
# }
```

#### ExecutionEngine (`framework/engine/execution.py`)

**Purpose:** Generate and execute orders with realistic costs.

**Order Generation Logic:**
- Exit positions NOT in target universe
- Enter new positions IN target universe
- Handle partial fills (quantity mismatch)

**Trade Execution:**
```python
execution = ExecutionEngine(transaction_cost_bps=3)

# Generate orders from current to target positions
orders = execution.generate_orders(
    current_positions={'BTCUSDT': {...}},
    target_positions={'ETHUSDT': 0.5, 'ADAUSDT': 0.5},
    current_data=bar_data
)
# Output: [
#   {'symbol': 'BTCUSDT', 'side': 'sell', 'quantity': 0.5, ...},
#   {'symbol': 'ETHUSDT', 'side': 'buy', 'target_weight': 0.5, ...},
#   ...
# ]

# Execute orders at current prices
trades = execution.execute_orders(
    orders=orders,
    current_data=bar_data,
    timestamp=rebalance_time,
    portfolio_value=1_000_000
)
# Each trade includes transaction costs: cost = notional * (bps / 10000)

# Update position tracking
positions = execution.update_positions(
    current_positions=positions,
    trades=trades
)
```

---

### 3. Strategy Module

#### BaseStrategy (Abstract Class) (`strategies/base.py`)

**Interface:** All strategies must implement these methods:

```python
from abc import ABC, abstractmethod

class BaseStrategy(ABC):
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.name = config.get('name')
    
    @abstractmethod
    def generate_signals(
        self, 
        current_data: pl.DataFrame,
        historical_data: pl.DataFrame,
        timestamp: datetime
    ) -> pl.DataFrame:
        """
        Returns: DataFrame with columns:
        - SYMBOL: The asset
        - signal_strength: 0-1 score (how strong the signal)
        - direction: 'long' or 'short'
        """
        pass
    
    @abstractmethod
    def apply_filters(
        self,
        signals: pl.DataFrame,
        current_data: pl.DataFrame,
        historical_data: pl.DataFrame,
        timestamp: datetime
    ) -> pl.DataFrame:
        """Returns: Filtered signals DataFrame (can be empty)"""
        pass
    
    @abstractmethod
    def calculate_positions(
        self,
        signals: pl.DataFrame,
        portfolio_value: float
    ) -> Dict[str, float]:
        """Returns: {symbol: weight} where sum(weights) = 1.0"""
        pass
```

#### RankingStrategy (`strategies/ranking_strategy.py`)

**Concrete Implementation:** General-purpose ranking strategy.

**How It Works:**
1. Calculate factor for all symbols in universe
2. Rank symbols by factor value
3. Select top-N (or bottom-N for reversal)
4. Apply optional regime filters
5. Equal-weight selected symbols

**Supported Factors:**
- `momentum`: Price change over lookback period
- `volume_ratio`: Buy vs sell volume ratio
- `momentum_difference`: Long-period momentum minus short-period
- Extensible via `FactorCalculator`

**Example Signal Generation:**
```python
strategy = RankingStrategy(config)

# Factor calculation:
# - Get last 30 days of data
# - Calculate 7-day momentum for each symbol
# - Rank by momentum (descending)
# - Select top 10
# - Apply BTC MA filter

signals = strategy.generate_signals(
    current_data=bar_data_for_timestamp,
    historical_data=all_data,
    timestamp=rebalance_time
)
# Returns: 
# ┌──────────┬──────────────┬───────────────┬───────────┐
# │ SYMBOL   ┆ momentum_7d  ┆ rank          ┆ direction │
# ├──────────┼──────────────┼───────────────┼───────────┤
# │ ETHUSDT  ┆ 0.085        ┆ 1             ┆ long      │
# │ ADAUSDT  ┆ 0.062        ┆ 2             ┆ long      │
# │ ...      ┆ ...          ┆ ...           ┆ ...       │
# └──────────┴──────────────┴───────────────┴───────────┘

# Position sizing (equal-weight):
# If 10 signals → 10% each
# If 5 signals → 20% each
positions = strategy.calculate_positions(
    signals=filtered_signals,
    portfolio_value=1_000_000
)
# Returns: {'ETHUSDT': 0.1, 'ADAUSDT': 0.1, ...}
```

#### RegimeFilter (`strategies/filters.py`)

**Purpose:** Filter signals based on market regime conditions.

**Available Filters:**

1. **BTC Moving Average Filter**
   ```python
   # Only trade when BTC price > 200-day MA
   RegimeFilter.btc_ma_filter(
       signals=signals,
       historical_data=all_data,
       timestamp=current_time,
       ma_period=200
   )
   ```

2. **Volatility Filter**
   ```python
   # Only trade when market volatility < threshold
   # Uses BTC 30-day rolling volatility as proxy
   RegimeFilter.volatility_filter(
       signals=signals,
       historical_data=all_data,
       timestamp=current_time,
       vol_threshold=0.5,      # Max annualized vol
       lookback_days=30
   )
   ```

**Adding New Filters:**
- Implement new method in `RegimeFilter` class
- Add to config as `filters: [{'type': 'new_filter_name', 'param1': value}]`

---

### 4. Factors Module

#### FactorCalculator (`factors/calculations.py`)

**Purpose:** Compute price/volume based factors using vectorized operations.

**Available Factors:**

1. **Momentum**
   ```python
   # Price change over lookback period
   data = FactorCalculator.momentum(
       data=ohlcv_df,
       lookback_days=7,
       price_col='CLOSE_PRICE'
   )
   # Adds column: momentum_7d (return from 7 days ago)
   ```

2. **Volume Ratio (Buy vs Sell)**
   ```python
   # Requires BUY_VOLUME and SELL_VOLUME columns
   data = FactorCalculator.volume_ratio(
       data=ohlcv_df,
       lookback_days=30
   )
   # Adds column: volume_ratio_30d
   ```

3. **Z-Score (Standardized)**
   ```python
   # Rolling z-score: (value - mean) / std
   data = FactorCalculator.zscore(
       data=ohlcv_df,
       column='CLOSE_PRICE',
       lookback_days=20
   )
   # Adds column: CLOSE_PRICE_zscore_20d
   ```

4. **Cross-Sectional Rank**
   ```python
   # Rank factors at each timestamp (1 = best)
   data = FactorCalculator.rank_cross_sectional(
       data=ohlcv_df,
       column='momentum_7d',
       ascending=False  # High momentum = rank 1
   )
   # Adds column: momentum_7d_rank
   ```

**Key Implementation Detail:**
- Uses `.over('SYMBOL')` for rolling calculations per symbol
- Uses `.over('BAR_TIMESTAMP')` for cross-sectional ranking
- Leverages Polars vectorization for performance
- 1 bar per trading day (daily OHLCV data)

**Adding New Factors:**
1. Add static method to `FactorCalculator`
2. Register in `RankingStrategy._get_factor_function()`
3. Reference in config: `factor: {name: 'new_factor', params: {...}}`

---

### 5. Analytics Module

#### MetricsCalculator (`framework/analytics/metrics.py`)

**Performance Metrics Computed:**

**Returns:**
- `total_return`: Final P&L as percentage
- `annualized_return`: Return scaled to 252-day year
- `volatility`: Annualized daily return volatility
- `sharpe_ratio`: Excess return per unit of risk (risk_free_rate=0)
- `sortino_ratio`: Like Sharpe but only penalizes downside volatility
- `calmar_ratio`: Annualized return / max drawdown

**Drawdown:**
- `max_drawdown`: Maximum peak-to-trough decline (%)
- `max_drawdown_duration_days`: Longest recovery period

**Trade Statistics:**
- `total_trades`: Number of closed round-trip trades
- `win_rate`: Percentage of profitable trades
- `avg_win`: Average profit on winning trades (%)
- `avg_loss`: Average loss on losing trades (%)
- `profit_factor`: Total wins / total losses
- `max_consecutive_wins/losses`: Streak statistics

**Example:**
```python
metrics = MetricsCalculator.calculate_all_metrics(
    trades_df=all_trades,
    portfolio_ts=daily_portfolio_timeseries,
    daily_ts=daily_portfolio_timeseries
)

print(f"Sharpe Ratio: {metrics['sharpe_ratio']:.2f}")
print(f"Max Drawdown: {metrics['max_drawdown']:.2f}%")
print(f"Win Rate: {metrics['win_rate']:.1f}%")
```

#### TradeAnalyzer (`framework/analytics/trades.py`)

**Functions:**

1. **Create Trade Sheet**
   ```python
   trades_df = TradeAnalyzer.create_trade_sheet(all_trades)
   # Matches buys with sells (FIFO)
   # Calculates P&L, holding period, transaction costs
   # Outputs: entry_time, exit_time, entry_price, exit_price, pnl_pct, etc.
   ```

2. **Aggregate to Daily**
   ```python
   daily_ts = TradeAnalyzer.aggregate_to_daily(portfolio_daily_ts)
   # Groups daily snapshots by date
   # Last value of day used for daily metrics
   ```

3. **Create Monthly Returns Matrix**
   ```python
   monthly_matrix = TradeAnalyzer.create_monthly_matrix(daily_ts)
   # Pivots to year x month format
   # Useful for identifying seasonal patterns
   # Output: Excel-style matrix with months as columns, years as rows
   ```

#### Visualizer (`framework/analytics/visualizations.py`)

**Chart Types:**

1. **Equity Curve**
   ```python
   Visualizer.plot_equity_curve(
       portfolio_ts=daily_timeseries,
       save_path='results/equity_curve.png'
   )
   # Shows cumulative portfolio value over time
   ```

2. **Drawdown Chart**
   ```python
   Visualizer.plot_drawdown(
       portfolio_ts=daily_timeseries,
       save_path='results/drawdown.png'
   )
   # Shows underwater plot (peak-to-trough decline)
   ```

3. **Monthly Returns Heatmap**
   ```python
   Visualizer.plot_monthly_returns_heatmap(
       monthly_matrix=monthly_matrix,
       save_path='results/monthly_heatmap.png'
   )
   # Red-yellow-green heatmap of monthly returns
   ```

4. **BTC Correlation Chart**
   ```python
   Visualizer.plot_btc_correlation(
       portfolio_ts=portfolio_timeseries,
       btc_data=btc_prices,
       window=30,  # 30-day rolling correlation
       save_path='results/correlation.png'
   )
   ```

---

## Configuration System

### Config File Structure (YAML)

```yaml
# configs/my_strategy.yaml

name: "momentum_1w_daily"  # Strategy identifier

# Data loading - what columns to load
data:
  required_columns:
    - SYMBOL
    - BAR_TIMESTAMP
    - CLOSE_PRICE
    - VOLUME
    - NOTIONAL_VOLUME
  load_liquidations: false  # Optional: load extra data
  load_ratios: false

# Universe: top-N liquid symbols
universe:
  top_n: 50                    # Trade top 50 assets
  volume_lookback_days: 14     # Based on 2-week volume
  min_data_coverage: 0.95      # Must have 95% of data points

# Factor configuration
factor:
  name: "momentum"             # Which factor to use
  params:
    lookback_days: 7           # 7-day momentum
    price_col: "CLOSE_PRICE"

# Selection: which signals to act on
selection:
  type: "top"                  # 'top' for long, 'bottom' for reversal
  top_n: 10                    # Trade top 10 signals

# Rebalancing schedule
rebalance:
  frequency: "daily"           # 'daily', 'weekly', 'monthly'
  day: "monday"                # For weekly/monthly
  time: "00:00"                # HH:MM format (UTC)

# Optional regime filters
filters:
  - type: "btc_ma"             # Use BTC moving average filter
    ma_period: 200
  # - type: "volatility"
  #   threshold: 0.5
  #   lookback_days: 30

# Execution costs
execution:
  transaction_cost_bps: 3      # 3 basis points per trade
```

### Config-Driven Architecture Benefits

1. **Easy Strategy Iteration:** Change params without coding
2. **Reproducibility:** Config files version-controlled
3. **Parameter Sweeps:** Run multiple configs in loop
4. **Modularity:** Strategies, filters, factors independent

---

## Development Workflow

### Adding a New Strategy

1. **Create strategy class** (`strategies/my_strategy.py`):
   ```python
   from strategies.base import BaseStrategy
   
   class MyStrategy(BaseStrategy):
       def generate_signals(self, current_data, historical_data, timestamp):
           # Implement signal generation
           pass
       
       def apply_filters(self, signals, current_data, historical_data, timestamp):
           # Implement filtering logic
           pass
       
       def calculate_positions(self, signals, portfolio_value):
           # Implement position sizing
           pass
   ```

2. **Update run_backtest.py** to instantiate your strategy:
   ```python
   strategy = MyStrategy(config)  # Instead of RankingStrategy
   ```

3. **Create config file** (`configs/my_strategy.yaml`)

4. **Run backtest:**
   ```bash
   python run_backtest.py
   ```

### Adding a New Factor

1. **Add to FactorCalculator** (`factors/calculations.py`):
   ```python
   @staticmethod
   def my_factor(data: pl.DataFrame, lookback_days: int) -> pl.DataFrame:
       bars_lookback = lookback_days  # 1 bar per trading day
       return data.with_columns(
           (some_calculation)
           .over('SYMBOL')
           .alias(f'my_factor_{lookback_days}d')
       )
   ```

2. **Register in RankingStrategy** (`strategies/ranking_strategy.py`):
   ```python
   factor_map = {
       'momentum': FactorCalculator.momentum,
       'my_factor': FactorCalculator.my_factor,  # Add here
       ...
   }
   ```

3. **Use in config:**
   ```yaml
   factor:
     name: "my_factor"
     params:
       lookback_days: 20
   ```

### Adding a New Filter

1. **Add to RegimeFilter** (`strategies/filters.py`):
   ```python
   @staticmethod
   def my_filter(signals, historical_data, timestamp, **kwargs) -> pl.DataFrame:
       # Filter logic
       return filtered_signals or pl.DataFrame()  # Empty if all filtered
   ```

2. **Add to apply_filters** in `RankingStrategy`:
   ```python
   elif filter_type == 'my_filter':
       filtered_signals = RegimeFilter.my_filter(
           filtered_signals,
           historical_data,
           timestamp,
           param1=filter_config.get('param1')
       )
   ```

3. **Use in config:**
   ```yaml
   filters:
     - type: "my_filter"
       param1: value
   ```

### Running Backtests

**Single Backtest:**
```bash
python run_backtest.py
# Runs configs in run_momentum_strategies() function
```

**Custom Run:**
```python
from run_backtest import run_single_backtest

results = run_single_backtest('configs/my_strategy.yaml')
# Results saved to results/my_strategy/*.csv and *.png
```

**Multiple Parameter Sweeps:**
```python
configs = [
    'configs/momentum_1w.yaml',
    'configs/momentum_2w.yaml',
    'configs/momentum_1m.yaml',
]

for config_path in configs:
    run_single_backtest(config_path)
```

---

## Key Implementation Details

### Selective Column Loading

**Memory Optimization Strategy:**
```
Without selective loading:
- Load all columns for 3+ years = huge memory footprint
- Slow data processing

With selective loading:
- Strategy specifies only needed columns in config
- DuckDB loads only those columns
- ~50% memory reduction typical
```

**Example:**
```yaml
# Momentum strategy only needs prices, not volumes
data:
  required_columns:
    - SYMBOL
    - BAR_TIMESTAMP
    - CLOSE_PRICE
    - NOTIONAL_VOLUME  # For universe selection
```

### Data Flow Performance

1. **Parquet Files → DuckDB:** Fast read with column filtering
2. **DuckDB → Polars:** Arrow zero-copy transfer
3. **Polars Operations:** Vectorized, single-threaded but CPU-efficient
4. **No unnecessary copies:** Arrow integration avoids data duplication

### Daily Bar Structure

- **1 bar per trading day:** Each bar represents one full trading day
- **Used in calculations:**
  ```python
  bars_lookback = lookback_days  # 1 bar = 1 trading day
  ```
- **Rebalancing timestamps:** Exact match to bar timestamps (daily)
- **Backtesting granularity:** Daily resolution throughout

### Timestamp Handling

- All timestamps in UTC
- Backed from exact bar timestamps (not interpolated)
- Format: `datetime` objects in Python
- Sorting critical for factor calculations (by timestamp then symbol)

### Transaction Cost Model

**Execution Cost per Trade:**
```python
transaction_cost = notional_value * (transaction_cost_bps / 10000)

# Example: Buy 0.5 BTC at $45,000 with 3 bps cost
notional = 0.5 * 45000 = $22,500
cost = 22500 * (3 / 10000) = $6.75
```

**Impact:**
- Deducted from cash balance
- Included in trade P&L calculations
- Summed in cumulative_costs metric

---

## Important Files Quick Reference

| File | Purpose | Key Classes/Functions |
|------|---------|----------------------|
| `run_backtest.py` | Entry point | `run_single_backtest()`, `run_parameter_sweep()` |
| `framework/engine/backtest.py` | Main orchestrator | `BacktestEngine`, `BacktestResults` |
| `strategies/ranking_strategy.py` | Ranking-based strategy | `RankingStrategy` |
| `factors/calculations.py` | Factor calculations | `FactorCalculator` |
| `framework/data/loader.py` | Data loading | `DataLoader` |
| `framework/data/universe.py` | Universe selection | `UniverseSelector` |
| `framework/engine/execution.py` | Order execution | `ExecutionEngine` |
| `framework/engine/portfolio.py` | P&L tracking | `PortfolioTracker` |
| `framework/analytics/metrics.py` | Performance metrics | `MetricsCalculator` |
| `framework/analytics/trades.py` | Trade analysis | `TradeAnalyzer` |
| `framework/analytics/visualizations.py` | Charts/plots | `Visualizer` |
| `strategies/filters.py` | Regime filters | `RegimeFilter` |
| `framework/data/validator.py` | Data validation | `DataValidator` |
| `framework/utils/calendar.py` | Rebalance schedule | `TradingCalendar` |

---

## Debugging & Troubleshooting

### Logging

Framework uses Python's logging module:
```python
import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Key log messages:
# - "Loading data..."
# - "Loading selective columns: [...]"
# - "Registered [table] from parquet"
# - "Selected N symbols for [date]"
# - "Rebalancing at [timestamp]"
# - "Generated M signals at [timestamp]"
# - "Filtered M -> K signals"
# - "Removed X invalid rows"
```

### Common Issues

**No signals generated:**
- Check data availability for lookback period
- Verify symbols in universe have price data
- Review factor calculation for NaN values

**Universe selection fails:**
- Insufficient data coverage (< min_data_coverage)
- Time period outside data range
- Check min_data_coverage parameter

**Transaction costs too high:**
- Verify transaction_cost_bps is reasonable (1-10 typical)
- Check portfolio turnover (% of capital rebalanced)

**P&L doesn't match:**
- Confirm all trades recorded
- Check transaction cost deductions
- Verify position updates after each trade

---

## Performance Considerations

### Memory Usage
- Entire backtest period loaded into memory
- Use selective columns to reduce footprint
- Larger data periods may require more RAM

### Execution Speed
- Typical backtest: 3-5 minutes for 3+ years, 50 symbols, daily rebalance
- Bottleneck: Strategy factor calculations per rebalance
- Optimization: Use vectorized Polars operations

### Best Practices
- Test on smaller date ranges first
- Use tmux/nohup for long backtests (> 60 seconds)
- Monitor memory with `top` or `htop`
- Log rebalance points to verify schedule accuracy

---

## Example: Complete Workflow

```python
# 1. Define config (YAML)
# configs/my_momentum.yaml created

# 2. Run backtest
python run_backtest.py

# 3. View results
# results/my_momentum/
# ├── equity_curve.png          # Portfolio value over time
# ├── drawdown.png              # Underwater plot
# ├── monthly_heatmap.png       # Year x month returns
# ├── portfolio_daily_detailed.csv  # Daily portfolio snapshots
# ├── portfolio_daily.csv       # Daily aggregation
# └── trades.csv                # All round-trip trades

# 4. Analyze in Python
import polars as pl
from framework.analytics.metrics import MetricsCalculator

trades = pl.read_csv('results/my_momentum/trades.csv')
daily = pl.read_csv('results/my_momentum/portfolio_daily.csv')

print(f"Total Return: {daily['pnl_pct'][-1]:.2f}%")
print(f"Win Rate: {(trades.filter(pl.col('pnl_dollar') > 0).height / len(trades) * 100):.1f}%")
```

---

## Summary

This framework provides a **production-ready backtesting system** for crypto trading strategies:

- **Extensible:** Add strategies, factors, filters easily via config or code
- **Efficient:** DuckDB/Polars for fast data handling
- **Realistic:** Transaction costs, position management, rebalance scheduling
- **Analytical:** Comprehensive metrics, trade analysis, visualizations
- **Reproducible:** Config-driven, version-controlled parameters

Use this guide to understand the architecture, develop new strategies, and iterate on trading ideas systematically.
