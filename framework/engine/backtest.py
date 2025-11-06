import polars as pl
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import logging

from framework.data.loader import DataLoader
from framework.data.universe import UniverseSelector
from framework.data.validator import DataValidator
from framework.engine.portfolio import PortfolioTracker
from framework.engine.execution import ExecutionEngine
from framework.utils.calendar import TradingCalendar
from strategies.base import BaseStrategy

logger = logging.getLogger(__name__)

@dataclass
class BacktestConfig:
    start_date: datetime
    end_date: datetime
    initial_capital: float = 1_000_000
    transaction_cost_bps: float = 3
    rebalance_frequency: str = 'weekly'
    rebalance_day: str = 'monday'
    rebalance_time: str = '00:00'

@dataclass
class BacktestResults:
    trades: pl.DataFrame
    portfolio_timeseries: pl.DataFrame
    daily_timeseries: pl.DataFrame
    metrics: Dict[str, float]
    positions_history: List[Dict]

class BacktestEngine:
    """Main backtest engine orchestrating the simulation"""

    def __init__(
        self,
        data_loader: DataLoader,
        universe_selector: UniverseSelector,
        strategy: BaseStrategy,
        config: BacktestConfig,
        strategy_config: Optional[Dict[str, Any]] = None
    ):
        self.data_loader = data_loader
        self.universe_selector = universe_selector
        self.strategy = strategy
        self.config = config
        self.strategy_config = strategy_config or {}

        self.portfolio = PortfolioTracker(config.initial_capital)
        self.execution = ExecutionEngine(config.transaction_cost_bps)
        self.calendar = TradingCalendar(
            config.rebalance_frequency,
            config.rebalance_day,
            config.rebalance_time
        )

        self.current_positions = {}
        self.current_universe = []
        self.all_trades = []
        self.portfolio_snapshots = []
        self.positions_history = []

    def run(self) -> BacktestResults:
        """Run the backtest"""
        logger.info(f"Starting backtest from {self.config.start_date} to {self.config.end_date}")

        # Load all data upfront
        all_data = self._load_all_data()

        # Get unique timestamps
        timestamps = self._get_trading_timestamps(all_data['trades'])

        # Main backtest loop
        for timestamp in timestamps:
            # Get current bar data
            current_data = all_data['trades'].filter(
                pl.col('BAR_TIMESTAMP') == timestamp
            )

            # Update portfolio with current prices
            self._update_portfolio_values(current_data)

            # Check if it's rebalance time
            if self.calendar.is_rebalance_time(timestamp):
                self._rebalance(current_data, all_data['trades'], timestamp)

            # Record snapshot
            self._record_snapshot(timestamp, current_data)

        # Generate results
        return self._generate_results()

    def _load_all_data(self) -> Dict[str, pl.DataFrame]:
        """Load and validate all required data"""
        logger.info("Loading data...")

        # Extract columns from strategy config if available
        columns = self.strategy_config.get('data', {}).get('required_columns', None)

        # Extract optional data loading flags
        load_liquidations = self.strategy_config.get('data', {}).get('load_liquidations', False)
        load_ratios = self.strategy_config.get('data', {}).get('load_ratios', False)

        if columns:
            logger.info(f"Loading selective columns: {columns}")

        data = self.data_loader.load_all_data(
            self.config.start_date,
            self.config.end_date,
            columns=columns,
            load_liquidations=load_liquidations,
            load_ratios=load_ratios
        )

        # Validate and clean trades data
        is_valid, issues = DataValidator.validate_trades_data(data['trades'])
        if not is_valid:
            logger.warning(f"Data validation issues: {issues}")

        data['trades'] = DataValidator.clean_data(data['trades'])

        return data

    def _get_trading_timestamps(self, data: pl.DataFrame) -> List[datetime]:
        """Get all unique trading timestamps"""
        return (
            data.select('BAR_TIMESTAMP')
            .unique()
            .sort('BAR_TIMESTAMP')
            .to_series()
            .to_list()
        )

    def _rebalance(
        self,
        current_data: pl.DataFrame,
        historical_data: pl.DataFrame,
        timestamp: datetime
    ):
        """Execute rebalancing logic"""
        logger.info(f"Rebalancing at {timestamp}")

        # Select universe
        self.current_universe = self.universe_selector.select_universe(
            historical_data,
            timestamp
        )

        if not self.current_universe:
            logger.warning(f"No universe selected for {timestamp}")
            return

        # Filter data for universe
        universe_data = current_data.filter(
            pl.col('SYMBOL').is_in(self.current_universe)
        )

        # Generate signals
        signals = self.strategy.generate_signals(
            universe_data,
            historical_data,
            timestamp
        )

        # Apply filters
        filtered_signals = self.strategy.apply_filters(
            signals,
            universe_data,
            historical_data,
            timestamp
        )

        # Calculate target positions
        target_positions = self.strategy.calculate_positions(
            filtered_signals,
            self.portfolio.current_capital
        )

        # Generate and execute orders
        orders = self.execution.generate_orders(
            self.current_positions,
            target_positions,
            universe_data
        )

        if orders:
            trades = self.execution.execute_orders(
                orders,
                universe_data,
                timestamp,
                self.portfolio.current_capital
            )

            # Update positions
            self.current_positions = self.execution.update_positions(
                self.current_positions,
                trades
            )

            # Update portfolio
            for trade in trades:
                self.portfolio.process_trade(trade)
                self.all_trades.append(trade)

    def _update_portfolio_values(self, current_data: pl.DataFrame):
        """Update portfolio with current market prices"""
        if not self.current_positions:
            return

        for symbol, position in self.current_positions.items():
            symbol_data = current_data.filter(pl.col('SYMBOL') == symbol)
            if not symbol_data.is_empty():
                current_price = symbol_data['CLOSE_PRICE'][0]
                position['current_price'] = current_price
                position['market_value'] = position['quantity'] * current_price

    def _record_snapshot(self, timestamp: datetime, current_data: pl.DataFrame):
        """Record portfolio snapshot"""
        snapshot = self.portfolio.get_snapshot(
            timestamp,
            self.current_positions,
            current_data
        )
        self.portfolio_snapshots.append(snapshot)

        # Record positions
        if self.current_positions:
            self.positions_history.append({
                'timestamp': timestamp,
                'positions': dict(self.current_positions)
            })

    def _generate_results(self) -> BacktestResults:
        """Generate backtest results"""
        from framework.analytics.trades import TradeAnalyzer
        from framework.analytics.metrics import MetricsCalculator

        # Create trade sheet
        trades_df = TradeAnalyzer.create_trade_sheet(self.all_trades)

        # Create portfolio timeseries
        portfolio_ts = pl.DataFrame(self.portfolio_snapshots)

        # Create daily timeseries
        daily_ts = TradeAnalyzer.aggregate_to_daily(portfolio_ts)

        # Calculate metrics
        metrics = MetricsCalculator.calculate_all_metrics(
            trades_df,
            portfolio_ts,
            daily_ts
        )

        return BacktestResults(
            trades=trades_df,
            portfolio_timeseries=portfolio_ts,
            daily_timeseries=daily_ts,
            metrics=metrics,
            positions_history=self.positions_history
        )
