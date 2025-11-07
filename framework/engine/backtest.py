import polars as pl
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
import logging

from framework.data.loader import DataLoader
from framework.data.universe import UniverseSelector, UniverseLoader
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
    use_precalculated_universe: bool = False
    universe_file_path: Optional[str] = None
    debug_mode: bool = False

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
        self.debug_mode = config.debug_mode

        # Initialize debug directory if debug_mode is enabled
        if self.debug_mode:
            from pathlib import Path
            Path('validation').mkdir(parents=True, exist_ok=True)

        # Initialize universe loader if using pre-calculated universe
        self.universe_loader = None
        if config.use_precalculated_universe:
            if not config.universe_file_path:
                raise ValueError("universe_file_path required when use_precalculated_universe=True")
            self.universe_loader = UniverseLoader(config.universe_file_path)
            logger.info(f"Using pre-calculated universe from {config.universe_file_path}")

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
        self.data_by_date = {}  # Index for O(1) lookups instead of O(N) filtering

        # Debug tracking lists (only used when debug_mode=True)
        self.debug_universe_by_date = []
        self.debug_rebalance_dates = []  # Track all executed rebalance dates
        self.debug_skipped_rebalance_dates = []  # Track skipped rebalance dates (no signals)
        self.debug_invalid_universe_dates = []  # Track invalid universe dates (insufficient data)
        self.debug_top_signals_by_date = {}  # Dict mapping date -> list of top signals
        self.debug_target_positions_by_date = {}  # Dict mapping date -> target positions dict
        self.debug_trading_activity = {}  # Dict mapping date -> {num_entries, num_exits, total_trades}
        self.universe_validation_index = {}  # Dict mapping date -> is_valid flag (loaded from validation CSV)

    def run(self) -> BacktestResults:
        """Run the backtest with daily consolidation"""
        import time
        logger.info(f"Starting backtest from {self.config.start_date} to {self.config.end_date}")

        # Load all data upfront and store as instance variable
        t0 = time.time()
        self.all_data = self._load_all_data()
        logger.info(f"Data loading took {time.time() - t0:.1f}s")

        # Generate universe validation CSV early (if using pre-calculated universe)
        if self.universe_loader and self.debug_mode:
            self._generate_universe_validation_csv()

        # Create date index for O(1) EOD data lookups
        t0 = time.time()
        self._create_date_index(self.all_data['trades'])
        logger.info(f"Date indexing took {time.time() - t0:.1f}s ({len(self.data_by_date)} unique dates)")

        # Get all trading timestamps for calendar checks
        t0 = time.time()
        all_timestamps = self._get_trading_timestamps(self.all_data['trades'])

        # Get EOD timestamps only (~1,355 for 3+ years)
        eod_timestamps = self._get_eod_timestamps(all_timestamps)
        logger.info(f"Processing {len(eod_timestamps)} trading days (timestamp extraction took {time.time() - t0:.1f}s)")

        # Main loop: iterate through trading days only (not 5-min bars)
        rebalance_count = 0
        loop_start = time.time()
        last_day_index = len(eod_timestamps) - 1

        for i, eod_ts in enumerate(eod_timestamps):
            # Get data at end of day using O(1) date index lookup
            eod_date = eod_ts.date()
            eod_data = self.data_by_date.get(eod_date, pl.DataFrame())

            # Skip rebalancing on the LAST day - use it only for final liquidation
            is_last_day = (i == last_day_index)

            # Update portfolio values FIRST with current EOD prices
            # This ensures position sizing uses the most recent market values
            self._update_portfolio_values(eod_data)

            # Check if this day is a rebalance day (construct 00:00 timestamp for the date)
            # Rebalances are scheduled at 00:00, so check if today's 00:00 would be a rebalance time
            rebalance_time_for_day = eod_ts.replace(hour=0, minute=0, second=0, microsecond=0)
            if self.calendar.is_rebalance_time(rebalance_time_for_day) and not is_last_day:
                t_reb = time.time()
                self._rebalance(eod_data, self.all_data['trades'], rebalance_time_for_day)
                rebalance_count += 1
                if rebalance_count % 50 == 0:
                    elapsed = time.time() - loop_start
                    logger.info(f"Progress: Day {i}/{len(eod_timestamps)} ({eod_ts.date()}), {rebalance_count} rebalances, {elapsed:.1f}s elapsed")

            # Record daily portfolio snapshot
            self._record_snapshot(eod_ts, eod_data)

        logger.info(f"Main loop completed in {time.time() - loop_start:.1f}s ({rebalance_count} rebalances)")

        # Close all open positions at the end of backtest period
        if self.current_positions:
            logger.info(f"Closing {len(self.current_positions)} open positions at end of backtest")
            final_timestamp = eod_timestamps[-1]  # Last EOD timestamp
            final_data = self.data_by_date.get(final_timestamp.date(), pl.DataFrame())

            if not final_data.is_empty():
                # Generate sell orders for all open positions
                final_orders = []
                for symbol, position in self.current_positions.items():
                    final_orders.append({
                        'symbol': symbol,
                        'side': 'sell',
                        'quantity': position['quantity'],
                        'order_type': 'final_exit'
                    })

                # Execute final liquidation trades
                final_trades = self.execution.execute_orders(
                    final_orders,
                    final_data,
                    final_timestamp,
                    self.portfolio.current_capital
                )

                if final_trades:
                    logger.info(f"Executed {len(final_trades)} final liquidation trades")

                    # Update positions (should be empty after this)
                    self.current_positions = self.execution.update_positions(
                        self.current_positions,
                        final_trades
                    )

                    # Record trades
                    self.all_trades.extend(final_trades)

                    # Update portfolio with final liquidation
                    for trade in final_trades:
                        self.portfolio.process_trade(trade)

                    # Record final snapshot after liquidation
                    self._update_portfolio_values(final_data)
                    self._record_snapshot(final_timestamp, final_data)

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

        # If using pre-calculated universe, get superset of symbols to load
        # Extract symbols that appear in universe during the backtest period only
        symbols_to_load = None
        if self.universe_loader:
            symbols_to_load = self.universe_loader.get_symbols_for_period(
                self.config.start_date,
                self.config.end_date
            )
            logger.info(f"Loading {len(symbols_to_load)} symbols from pre-calculated universe superset for period {self.config.start_date.date()} to {self.config.end_date.date()}")

        data = self.data_loader.load_all_data(
            self.config.start_date,
            self.config.end_date,
            symbols=symbols_to_load,
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

    def _create_date_index(self, data: pl.DataFrame):
        """Create O(1) lookup index mapping date -> DataFrame for that date"""
        # Group data by date and store each date's data separately
        data_with_date = data.with_columns(
            pl.col('BAR_TIMESTAMP').dt.date().alias('_date')
        )

        for date_group in data_with_date.group_by('_date'):
            date = date_group[0][0]  # Extract the date value
            df = date_group[1].drop('_date')  # Remove helper column
            self.data_by_date[date] = df

    def _generate_universe_validation_csv(self):
        """Generate universe validation CSV and load it for validity checks"""
        logger.info("Generating universe validation CSV...")

        # Filter pre-calculated universe for backtest date range
        universe_df = self.universe_loader.df.filter(
            (pl.col('rebalance_date') >= self.config.start_date.date()) &
            (pl.col('rebalance_date') <= self.config.end_date.date())
        )

        # Calculate is_valid flag: True if date has >= 50 symbols, False otherwise
        universe_df = universe_df.with_columns(
            (pl.col('symbols').list.len() >= 50).alias('is_valid')
        )

        # Get all unique symbols from the period-specific universe
        period_symbols = sorted(self.universe_loader.get_symbols_for_period(
            self.config.start_date,
            self.config.end_date
        ))

        # Convert symbols list column to wide format (one column per symbol)
        universe_wide = universe_df.with_columns([
            pl.col('symbols').list.contains(symbol).cast(pl.Int8).alias(symbol)
            for symbol in period_symbols
        ]).select(['rebalance_date', 'is_valid'] + period_symbols)

        # Rename for consistency with other validation files
        universe_wide = universe_wide.rename({'rebalance_date': 'timestamp'})

        # Save validation CSV
        universe_wide.write_csv('validation/02_universe_by_date.csv')
        logger.info(f"Saved: 02_universe_by_date.csv ({len(period_symbols)} symbols, {len(universe_wide)} dates)")

        # Load validation index for validity checks
        # Create dict mapping date -> is_valid flag
        validation_data = universe_df.select(['rebalance_date', 'is_valid']).to_dicts()
        self.universe_validation_index = {
            row['rebalance_date']: row['is_valid']
            for row in validation_data
        }
        logger.info(f"Loaded universe validation index with {len(self.universe_validation_index)} dates")

    def _get_trading_timestamps(self, data: pl.DataFrame) -> List[datetime]:
        """Get all unique trading timestamps"""
        return (
            data.select('BAR_TIMESTAMP')
            .unique()
            .sort('BAR_TIMESTAMP')
            .to_series()
            .to_list()
        )

    def _get_eod_timestamps(self, timestamps: List[datetime]) -> List[datetime]:
        """Get last timestamp of each trading day"""
        if not timestamps:
            return []

        eod_timestamps = []
        current_date = timestamps[0].date()
        last_timestamp_of_day = timestamps[0]

        for ts in timestamps:
            ts_date = ts.date()
            if ts_date == current_date:
                last_timestamp_of_day = ts
            else:
                eod_timestamps.append(last_timestamp_of_day)
                current_date = ts_date
                last_timestamp_of_day = ts

        # Don't forget the last day
        eod_timestamps.append(last_timestamp_of_day)
        return eod_timestamps

    def _rebalance(
        self,
        current_data: pl.DataFrame,
        historical_data: pl.DataFrame,
        timestamp: datetime
    ):
        """Execute rebalancing logic"""
        logger.info(f"Rebalancing at {timestamp}")

        # Check validity of universe date (if using pre-calculated universe)
        if self.universe_loader:
            # Use validation index if available (loaded from validation CSV)
            # Otherwise fallback to checking source parquet file
            lookup_date = timestamp.date() if hasattr(timestamp, 'date') else timestamp

            if self.universe_validation_index:
                is_valid = self.universe_validation_index.get(lookup_date, False)
            else:
                is_valid = self.universe_loader.is_valid_date(timestamp)

            if not is_valid:
                logger.warning(f"Universe invalid for {timestamp} - insufficient data (skipping rebalance)")
                if self.debug_mode:
                    self.debug_invalid_universe_dates.append(timestamp)
                return

        # Select universe from pre-calculated file OR calculate on-the-fly
        if self.universe_loader:
            self.current_universe = self.universe_loader.get_universe(timestamp)
            logger.info(f"Retrieved {len(self.current_universe)} symbols from pre-calculated universe")
        else:
            self.current_universe = self.universe_selector.select_universe(
                historical_data,
                timestamp
            )
            logger.info(f"Calculated {len(self.current_universe)} symbols on-the-fly")

        if not self.current_universe:
            logger.warning(f"No universe selected for {timestamp}")
            return

        # Debug: Save universe composition
        if self.debug_mode:
            self.debug_universe_by_date.append({
                'timestamp': timestamp,
                'symbols': self.current_universe,
                'num_symbols': len(self.current_universe)
            })

        # Filter data for universe
        universe_data = current_data.filter(
            pl.col('SYMBOL').is_in(self.current_universe)
        )

        # Debug: Track rebalance date and calculate top 10 ranked assets
        if self.debug_mode:
            # Calculate momentum for ALL universe symbols to show complete ranking
            from factors.calculations import FactorCalculator
            lookback_days = self.strategy_config.get('factor', {}).get('params', {}).get('lookback_days', 7)

            # Filter historical data for universe symbols
            historical_universe_data = historical_data.filter(
                pl.col('SYMBOL').is_in(self.current_universe)
            )

            # Calculate momentum using the full historical data
            universe_with_factor = FactorCalculator.momentum(
                historical_universe_data,
                lookback_days=lookback_days,
                price_col='CLOSE_PRICE'
            )

            # Filter to current DATE (not exact timestamp) and get most recent bar per symbol
            # This handles the case where different symbols have bars at different times on the same day
            momentum_col = f'momentum_{lookback_days}d'
            current_date = timestamp.date()
            current_momentum = (
                universe_with_factor
                .filter(pl.col('BAR_TIMESTAMP').dt.date() == current_date)
                .sort(['SYMBOL', 'BAR_TIMESTAMP'])  # Sort by symbol then timestamp
                .group_by('SYMBOL')  # Get the LAST bar for each symbol on this date
                .agg(pl.last(momentum_col).alias(momentum_col))  # Take the last (most recent) momentum value
            )

            # Rank all symbols by momentum (descending), EXCLUDING NaN values
            if not current_momentum.is_empty() and momentum_col in current_momentum.columns:
                # Filter OUT rows with NaN/None momentum (not enough historical data)
                valid_momentum = current_momentum.filter(
                    pl.col(momentum_col).is_not_null()
                )

                if not valid_momentum.is_empty():
                    # Log for debugging
                    total_symbols = len(current_momentum)
                    valid_symbols = len(valid_momentum)
                    logger.info(f"At {timestamp}: {valid_symbols}/{total_symbols} symbols have valid {lookback_days}d momentum")

                    ranked_by_factor = valid_momentum.sort(momentum_col, descending=True)

                    # Save all top 10 (or all available if less than 10)
                    top_10_signals = []
                    for rank, row in enumerate(ranked_by_factor.head(10).iter_rows(named=True), 1):
                        factor_val = row.get(momentum_col, 0.0)
                        top_10_signals.append({
                            'rank': rank,
                            'symbol': row['SYMBOL'],
                            'factor_value': factor_val
                        })

                    self.debug_top_signals_by_date[timestamp] = top_10_signals
                else:
                    logger.warning(f"At {timestamp}: NO symbols have valid momentum - all values are NaN!")

        # Generate signals
        signals = self.strategy.generate_signals(
            universe_data,
            historical_data,
            timestamp
        )

        # Check if we have any valid signals
        if signals.is_empty():
            logger.warning(f"No valid signals at {timestamp} - SKIPPING REBALANCE")
            if self.debug_mode:
                self.debug_skipped_rebalance_dates.append(timestamp)
            return

        # Debug: Log signals
        if self.debug_mode:
            self.debug_rebalance_dates.append(timestamp)
            logger.info(f"Generated {len(signals)} signals at {timestamp}")
            logger.info(f"Signals preview: {signals.select(['SYMBOL', 'signal_strength']).head(5)}")

        # Apply filters
        filtered_signals = self.strategy.apply_filters(
            signals,
            universe_data,
            historical_data,
            timestamp
        )

        if self.debug_mode:
            logger.info(f"After filters: {len(filtered_signals)} signals remain")

        # Calculate current total portfolio value for position sizing
        # This is cash + market value of all positions (i.e., current equity)
        current_total_value = self.portfolio.cash + sum(
            pos.get('market_value', pos['quantity'] * pos.get('current_price', pos['avg_price']))
            for pos in self.current_positions.values()
        )

        # Calculate target positions based on current total equity
        target_positions = self.strategy.calculate_positions(
            filtered_signals,
            current_total_value
        )

        # Debug: Save target positions by date
        logger.info(f"[POSITION SIZING] Cash: ${self.portfolio.cash:,.2f}, Positions value: ${sum(pos.get('market_value', 0) for pos in self.current_positions.values()):,.2f}, Total: ${current_total_value:,.2f}")

        if self.debug_mode:
            logger.info(f"Current total portfolio value: ${current_total_value:,.2f}")
            logger.info(f"Target positions: {target_positions}")
            self.debug_target_positions_by_date[timestamp] = target_positions

        # Generate and execute orders
        # NOTE: Execution engine needs FULL current_data, not just universe_data
        # because we need prices for symbols being EXITED that may no longer be in universe
        orders = self.execution.generate_orders(
            self.current_positions,
            target_positions,
            current_data  # Use full data, not universe_data
        )

        # Debug: Log orders
        if self.debug_mode and orders:
            logger.info(f"Generated {len(orders)} orders at {timestamp}")
            for order in orders[:3]:  # Log first 3 orders
                logger.info(f"  Order: {order.get('symbol')} {order.get('side')} qty={order.get('quantity', order.get('target_weight'))}")

        if orders:
            # Store previous positions before execution
            previous_positions_symbols = set(self.current_positions.keys())

            # CRITICAL: Separate exits and entries for correct position sizing
            # 1. Execute exits first to free up cash
            # 2. Recalculate equity with updated cash
            # 3. Execute entries based on NEW equity (after exits)

            exit_orders = [o for o in orders if o['side'] == 'sell']
            entry_orders = [o for o in orders if o['side'] == 'buy']

            all_trades = []

            # Phase 1: Execute exits
            if exit_orders:
                exit_trades = self.execution.execute_orders(
                    exit_orders,
                    current_data,
                    timestamp,
                    current_total_value  # Not used for exits (uses quantity)
                )
                all_trades.extend(exit_trades)

                # Update positions and cash after exits
                self.current_positions = self.execution.update_positions(
                    self.current_positions,
                    exit_trades
                )
                for trade in exit_trades:
                    self.portfolio.process_trade(trade)

            # Phase 2: Recalculate equity after exits, then execute entries
            if entry_orders:
                # Recalculate total equity after exits (cash has increased)
                equity_after_exits = self.portfolio.cash + sum(
                    pos.get('market_value', pos['quantity'] * pos.get('current_price', pos['avg_price']))
                    for pos in self.current_positions.values()
                )

                logger.info(f"[POSITION SIZING AFTER EXITS] Cash: ${self.portfolio.cash:,.2f}, Total: ${equity_after_exits:,.2f}")

                # CRITICAL: Equal-weight new entries across available cash
                # We need to recalculate weights for new entries only:
                # - Divide cash by number of NEW entries (not total positions)
                # - Each new entry gets: cash / num_new_entries
                num_new_entries = len(entry_orders)
                cash_per_entry = self.portfolio.cash / num_new_entries

                # Update target_weight for each entry order to be equal-weighted across cash
                for order in entry_orders:
                    order['target_weight'] = 1.0 / num_new_entries

                logger.info(f"[EXEC] Executing {num_new_entries} entry orders, ${cash_per_entry:,.2f} per entry")

                entry_trades = self.execution.execute_orders(
                    entry_orders,
                    current_data,
                    timestamp,
                    self.portfolio.cash  # Pass total cash, weights already adjusted
                )
                all_trades.extend(entry_trades)

                # Update positions after entries
                self.current_positions = self.execution.update_positions(
                    self.current_positions,
                    entry_trades
                )
                for trade in entry_trades:
                    self.portfolio.process_trade(trade)

            trades = all_trades

            if self.debug_mode:
                logger.info(f"Executed {len(trades)} trades at {timestamp}")

            # NOTE: Positions and portfolio already updated in the two-phase execution above
            # No need to update again here

            # Calculate trading activity based on ACTUAL executed trades
            if self.debug_mode:
                current_positions_symbols = set(self.current_positions.keys())

                num_entries = len(current_positions_symbols - previous_positions_symbols)
                num_exits = len(previous_positions_symbols - current_positions_symbols)
                total_trades = num_entries + num_exits

                self.debug_trading_activity[timestamp] = {
                    'num_entries': num_entries,
                    'num_exits': num_exits,
                    'total_trades': total_trades
                }

                logger.info(f"Trading activity: {num_entries} entries, {num_exits} exits, {total_trades} total trades")

            # Append all trades to master list
            for trade in trades:
                self.all_trades.append(trade)

    def _update_portfolio_values(self, current_data: pl.DataFrame):
        """Update portfolio with current market prices (vectorized)"""
        if not self.current_positions:
            return

        # Vectorized: use join instead of looping and filtering
        # Extract just the symbols and prices we need
        positions_list = list(self.current_positions.keys())
        prices_df = current_data.filter(
            pl.col('SYMBOL').is_in(positions_list)
        ).select(['SYMBOL', 'CLOSE_PRICE']).unique(subset=['SYMBOL'], keep='last')

        # Create lookup dictionary from prices
        price_lookup = {}
        for row in prices_df.iter_rows(named=True):
            price_lookup[row['SYMBOL']] = row['CLOSE_PRICE']

        # Update positions with prices
        for symbol, position in self.current_positions.items():
            if symbol in price_lookup:
                current_price = price_lookup[symbol]
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

        # Debug: Save intermediate files
        if self.debug_mode:
            logger.info("Saving debug intermediate files...")

            # 1. Save first 100 rows of raw data (from backtest data)
            self.all_data['trades'].write_csv('validation/01_raw_data.csv')
            logger.info("Saved: 01_raw_data.csv")

            # 2. Universe validation CSV already generated early in run() method
            # Just log that it was already saved
            if self.universe_loader:
                logger.info("Universe validation CSV already generated and saved")
            elif self.debug_universe_by_date:
                # Fallback: use debug tracking if not using pre-calculated universe
                all_symbols = sorted(set(
                    symbol for record in self.debug_universe_by_date
                    for symbol in record['symbols']
                ))

                universe_rows = []
                for record in self.debug_universe_by_date:
                    row = {'timestamp': record['timestamp']}
                    symbols_set = set(record['symbols'])
                    for symbol in all_symbols:
                        row[symbol] = 1 if symbol in symbols_set else 0
                    universe_rows.append(row)

                universe_df = pl.DataFrame(universe_rows)
                universe_df.write_csv('validation/02_universe_by_date.csv')
                logger.info(f"Saved: 02_universe_by_date.csv ({len(all_symbols)} symbols, {len(universe_rows)} dates)")

            # 2b. Save top signals ranking with weights - top 10 assets ranked 1-10 per rebalance date
            if self.debug_rebalance_dates:
                # Create one row per rebalance date
                pivot_data = []
                for rebalance_date in self.debug_rebalance_dates:
                    row_data = {'timestamp': rebalance_date}

                    # Initialize all 10 rank columns (symbol, factor, and weight)
                    for i in range(1, 11):
                        row_data[f'r{i}'] = None
                        row_data[f'r{i}_factor'] = None
                        row_data[f'r{i}_wt'] = None

                    # Fill in the ranks that exist for this date
                    if rebalance_date in self.debug_top_signals_by_date:
                        signals_list = self.debug_top_signals_by_date[rebalance_date]
                        for sig in signals_list:
                            rank = sig['rank']
                            symbol = sig['symbol']
                            factor_value = sig['factor_value']
                            if rank <= 10:
                                row_data[f'r{rank}'] = symbol
                                row_data[f'r{rank}_factor'] = factor_value

                    # Add position weights from target_positions
                    target_positions = self.debug_target_positions_by_date.get(rebalance_date, {})
                    for i in range(1, 11):
                        symbol = row_data.get(f'r{i}')
                        if symbol and symbol in target_positions:
                            row_data[f'r{i}_wt'] = target_positions[symbol]

                    # Add num_positions
                    row_data['num_positions'] = len(target_positions)

                    # Add trading activity metrics
                    activity = self.debug_trading_activity.get(rebalance_date, {})
                    row_data['num_entries'] = activity.get('num_entries', 0)
                    row_data['num_exits'] = activity.get('num_exits', 0)
                    row_data['total_trades'] = activity.get('total_trades', 0)

                    pivot_data.append(row_data)

                top_signals_pivot_df = pl.DataFrame(pivot_data)

                # Round numeric columns to 2 decimals
                numeric_cols = [col for col in top_signals_pivot_df.columns if col.endswith('_factor') or col.endswith('_wt')]
                for col in numeric_cols:
                    top_signals_pivot_df = top_signals_pivot_df.with_columns(
                        pl.col(col).round(2)
                    )

                top_signals_pivot_df.write_csv('validation/02b_top_10_ranked_signals.csv')
                logger.info(f"Saved: 02b_top_10_ranked_signals.csv ({len(self.debug_rebalance_dates)} rebalance dates)")

            # 6. Save raw trades (before matching)
            if self.all_trades:
                raw_trades_df = pl.DataFrame(self.all_trades)

                # Round numeric columns to 2 decimals
                numeric_cols = [col for col in raw_trades_df.columns
                               if raw_trades_df[col].dtype in [pl.Float64, pl.Float32]]
                for col in numeric_cols:
                    raw_trades_df = raw_trades_df.with_columns(
                        pl.col(col).round(2)
                    )

                raw_trades_df.write_csv('validation/06_trades_raw.csv')
                logger.info("Saved: 06_trades_raw.csv")

            # 7. Save detailed portfolio snapshots
            if self.portfolio_snapshots:
                snapshots_df = pl.DataFrame(self.portfolio_snapshots)

                # Round numeric columns to 2 decimals
                numeric_cols = [col for col in snapshots_df.columns
                               if snapshots_df[col].dtype in [pl.Float64, pl.Float32]]
                for col in numeric_cols:
                    snapshots_df = snapshots_df.with_columns(
                        pl.col(col).round(2)
                    )

                snapshots_df.write_csv('validation/07_portfolio_snapshots_detailed.csv')
                logger.info("Saved: 07_portfolio_snapshots_detailed.csv")

            logger.info("Debug file saving complete!")

        return BacktestResults(
            trades=trades_df,
            portfolio_timeseries=portfolio_ts,
            daily_timeseries=daily_ts,
            metrics=metrics,
            positions_history=self.positions_history
        )
