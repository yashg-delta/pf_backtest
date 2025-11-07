"""
Universe Generator Script - Optimized with Vectorization

Generates a parquet file containing the top 50 liquid symbols for each rebalance date.
Uses Polars vectorized operations for 20-50x performance improvement.

Key optimization: Pre-aggregate 5-min bars to daily, then use rolling windows.
This reduces data by 288x (5-min → daily) while maintaining mathematical equivalence.

Expected output: data/universe/universe_top50_14d_daily.parquet
Expected runtime: 1-2 minutes (vs 45+ minutes with loop-based approach)
"""

import polars as pl
from pathlib import Path
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class UniverseGeneratorOptimized:
    """Generate pre-calculated trading universe using vectorized operations"""

    # Universe selection parameters
    TOP_N = 50
    LOOKBACK_DAYS = 14  # Require exactly 14 days of continuous data
    MIN_COMPLETE_DAYS = 14  # Must have data on all 14 days (100% coverage)
    BARS_PER_DAY = 1  # Daily bars: 1 bar per day

    def __init__(self, data_path: str):
        """
        Initialize universe generator

        Args:
            data_path: Path to data directory containing tardis_trades_5min.parquet
        """
        self.data_path = Path(data_path)

    def _get_bar_count_threshold(self) -> int:
        """Calculate minimum bars required for 100% complete coverage (14 consecutive days)"""
        return self.MIN_COMPLETE_DAYS * self.BARS_PER_DAY

    def generate_universe(self, start_date: datetime, end_date: datetime, output_path: str):
        """
        Generate universe file using vectorized operations

        Algorithm:
        1. Load trades data (parquet)
        2. Aggregate 5-min bars to daily (group by symbol, date)
           - Sum notional volume per day
           - Count bars per day
        3. Calculate 14-day rolling metrics (vectorized)
           - Rolling sum of daily volumes
           - Rolling sum of daily bars
           - Weighted average = rolling_sum(volume) / rolling_sum(bars)
        4. Filter by minimum bars >= 3,830 (95% of 14 days * 288 bars)
        5. Rank symbols by volume within each date
        6. Select top 50 per date
        7. Save to parquet

        Mathematical equivalence:
        - Original: AVG(NOTIONAL_VOLUME) across all 5-min bars in 14-day window
        - Optimized: SUM(daily_volumes) / SUM(daily_bars) = equivalent!

        Args:
            start_date: Start date for universe generation
            end_date: End date for universe generation
            output_path: Path to save parquet file
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        logger.info("=" * 80)
        logger.info("UNIVERSE GENERATOR (OPTIMIZED)")
        logger.info("=" * 80)
        logger.info(f"Date range: {start_date} to {end_date}")
        logger.info(f"Parameters: top_n={self.TOP_N}, lookback={self.LOOKBACK_DAYS}d, "
                   f"min_complete_days={self.MIN_COMPLETE_DAYS} (100% coverage)")

        # Step 1: Load trades data
        logger.info("\nStep 1: Loading trades data...")
        trades_file = self.data_path / 'tardis_trades_daily.parquet'

        if not trades_file.exists():
            raise FileNotFoundError(f"Trades file not found: {trades_file}")

        trades = pl.scan_parquet(trades_file)  # Lazy load
        logger.info(f"Loaded trades from {trades_file}")

        # Filter by date range
        trades = trades.filter(
            (pl.col('BAR_TIMESTAMP') >= start_date) &
            (pl.col('BAR_TIMESTAMP') <= end_date)
        )

        # Step 2: Aggregate to daily
        logger.info("\nStep 2: Aggregating 5-min bars to daily...")
        daily = trades.with_columns(
            # Cast Decimal to float for rolling operations
            pl.col('NOTIONAL_VOLUME').cast(pl.Float64).alias('notional_volume_f64')
        ).group_by(
            pl.col('BAR_TIMESTAMP').dt.date().alias('date'),
            'SYMBOL'
        ).agg(
            pl.col('notional_volume_f64').sum().alias('daily_volume_sum'),
            pl.col('notional_volume_f64').count().alias('bars_per_day')
        ).collect()

        logger.info(f"Aggregated to daily: {daily.shape[0]} symbol-days")

        # Step 3: Calculate 14-day rolling metrics
        logger.info("\nStep 3: Calculating 14-day rolling metrics (vectorized)...")

        # Sort by symbol and date for proper rolling calculations
        daily = daily.sort(['SYMBOL', 'date'])

        # Window size in rows for 14 days of rolling
        rolling_window = self.LOOKBACK_DAYS

        # Calculate rolling metrics per symbol
        daily = daily.with_columns([
            # Rolling sum of volume over 14-day window
            pl.col('daily_volume_sum')
            .rolling_sum(window_size=rolling_window)
            .over('SYMBOL')
            .alias('volume_14d_sum'),

            # Rolling sum of bars over 14-day window
            pl.col('bars_per_day')
            .rolling_sum(window_size=rolling_window)
            .over('SYMBOL')
            .alias('bars_14d_total'),
        ])

        # Calculate weighted average: total_volume / total_bars
        daily = daily.with_columns(
            (pl.col('volume_14d_sum') / pl.col('bars_14d_total'))
            .alias('avg_volume_14d')
        )

        logger.info(f"Calculated rolling metrics")

        # Step 4: Mark valid symbols (have complete data coverage)
        logger.info("\nStep 4: Marking symbols with valid data coverage...")

        bar_threshold = self._get_bar_count_threshold()
        logger.info(f"Minimum bars required: {bar_threshold} (100% coverage of {self.LOOKBACK_DAYS} days)")

        # Mark valid symbols (have sufficient history)
        daily = daily.with_columns(
            (pl.col('bars_14d_total') >= bar_threshold).alias('has_valid_data')
        )

        valid_symbols = daily.filter(pl.col('has_valid_data')).shape[0]
        logger.info(f"Symbol-days with valid coverage: {valid_symbols}/{daily.shape[0]}")

        # Step 5: Rank ALL symbols per date (even those without full coverage)
        logger.info("\nStep 5: Ranking symbols per date...")

        # Only rank symbols with valid data
        ranked = daily.with_columns(
            pl.when(pl.col('has_valid_data'))
            .then(
                pl.col('avg_volume_14d')
                .rank(descending=True)
                .over('date')
            )
            .otherwise(None)
            .alias('rank')
        )

        # Select top 50 valid symbols per date
        universe = ranked.filter(
            (pl.col('rank').is_not_null()) &
            (pl.col('rank') <= self.TOP_N)
        )

        # Step 6: Generate ALL dates in range
        logger.info("\nStep 6: Generating universe for all dates in range...")

        # Create complete date range
        date_range = pl.date_range(
            start_date.date(),
            end_date.date(),
            interval='1d',
            eager=True
        ).alias('date')

        all_dates = pl.DataFrame({'date': date_range})

        # Group selected symbols by date
        universe_by_date = universe.group_by('date').agg(
            pl.col('SYMBOL').sort_by(pl.col('rank')).alias('symbols'),
            pl.col('SYMBOL').len().alias('num_symbols')
        )

        # Join with all dates to include missing dates
        universe_by_date = all_dates.join(
            universe_by_date,
            on='date',
            how='left'
        )

        # Fill nulls with empty lists and 0 counts
        universe_by_date = universe_by_date.with_columns([
            pl.col('symbols').fill_null([]).alias('symbols'),
            pl.col('num_symbols').fill_null(0).alias('num_symbols')
        ])

        # Add is_valid column: True if we have at least TOP_N symbols
        universe_by_date = universe_by_date.with_columns(
            (pl.col('num_symbols') >= self.TOP_N).alias('is_valid')
        ).sort('date')

        # Count valid vs invalid dates
        valid_dates = universe_by_date.filter(pl.col('is_valid')).shape[0]
        invalid_dates = universe_by_date.filter(~pl.col('is_valid')).shape[0]

        logger.info(f"Total dates: {universe_by_date.shape[0]}")
        logger.info(f"Valid dates (≥{self.TOP_N} symbols): {valid_dates}")
        logger.info(f"Invalid dates (<{self.TOP_N} symbols): {invalid_dates}")

        # Step 7: Save to parquet
        logger.info(f"\nStep 7: Saving to {output_path}...")
        universe_by_date.write_parquet(output_path)

        # Step 8: Print statistics
        logger.info("\n" + "=" * 80)
        logger.info("GENERATION COMPLETE")
        logger.info("=" * 80)

        stats = {
            'total_dates': universe_by_date.shape[0],
            'valid_dates': valid_dates,
            'invalid_dates': invalid_dates,
            'avg_symbols_all': universe_by_date['num_symbols'].mean(),
            'avg_symbols_valid': universe_by_date.filter(pl.col('is_valid'))['num_symbols'].mean() if valid_dates > 0 else 0,
            'min_symbols': universe_by_date['num_symbols'].min(),
            'max_symbols': universe_by_date['num_symbols'].max(),
        }

        for key, val in stats.items():
            logger.info(f"{key:20}: {val:.1f}")

        # Calculate unique symbols
        all_symbols = set()
        for symbols_list in universe_by_date['symbols']:
            all_symbols.update(symbols_list)

        logger.info(f"{'unique_symbols':20}: {len(all_symbols)}")
        logger.info(f"{'output_file':20}: {output_path}")
        logger.info(f"{'output_size':20}: {output_path.stat().st_size / 1e6:.1f} MB")

        return universe_by_date


def main():
    """Generate universe file for 2022-01-01 to 2025-09-30"""

    # Configuration
    data_path = '/home/yash.gupta/research/tardis_datasets/data/snowflake'
    output_path = '/home/yash.gupta/research/pf_backtest/data/universe/universe_top50_14d_daily.parquet'

    start_date = datetime(2022, 1, 1)
    end_date = datetime(2025, 9, 30)

    # Generate universe
    generator = UniverseGeneratorOptimized(data_path)
    df = generator.generate_universe(start_date, end_date, output_path)


if __name__ == '__main__':
    main()
