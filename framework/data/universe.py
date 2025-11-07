import polars as pl
from datetime import datetime, timedelta
from typing import List, Set, Optional
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

class UniverseSelector:
    """Select tradable universe based on dollar volume"""

    def __init__(
        self,
        top_n: int = 50,
        volume_lookback_days: int = 14,
        min_data_coverage: float = 0.95
    ):
        self.top_n = top_n
        self.volume_lookback_days = volume_lookback_days
        self.min_data_coverage = min_data_coverage

    def calculate_dollar_volume(self, data: pl.DataFrame) -> pl.DataFrame:
        """Calculate average dollar volume for each symbol"""

        return data.group_by("SYMBOL").agg([
            pl.col("NOTIONAL_VOLUME").mean().alias("avg_dollar_volume"),
            pl.col("NOTIONAL_VOLUME").count().alias("data_points")
        ])

    def select_universe(
        self,
        data: pl.DataFrame,
        current_date: datetime
    ) -> List[str]:
        """Select top N symbols by dollar volume"""

        # Calculate lookback period
        lookback_start = current_date - timedelta(days=self.volume_lookback_days)

        # Filter data for lookback period
        lookback_data = data.filter(
            (pl.col("BAR_TIMESTAMP") >= lookback_start) &
            (pl.col("BAR_TIMESTAMP") < current_date)
        )

        if lookback_data.is_empty():
            logger.warning(f"No data available for universe selection on {current_date}")
            return []

        # Calculate average dollar volume
        volume_stats = self.calculate_dollar_volume(lookback_data)

        # Calculate expected data points (daily bars: 1 per day)
        expected_points = self.volume_lookback_days
        min_required_points = int(expected_points * self.min_data_coverage)

        # Filter by data coverage
        volume_stats = volume_stats.filter(
            pl.col("data_points") >= min_required_points
        )

        # Select top N by volume
        top_symbols = (
            volume_stats
            .sort("avg_dollar_volume", descending=True)
            .head(self.top_n)
            .select("SYMBOL")
            .to_series()
            .to_list()
        )

        logger.info(f"Selected {len(top_symbols)} symbols for {current_date.date()}")
        return top_symbols


class UniverseLoader:
    """Load pre-calculated trading universe from parquet file"""

    def __init__(self, universe_file_path: str):
        """
        Load pre-calculated universe file

        Args:
            universe_file_path: Path to parquet file with pre-calculated universe
                               Schema: {rebalance_date: datetime, symbols: list[str]}

        Raises:
            FileNotFoundError: If universe file doesn't exist
        """
        file_path = Path(universe_file_path)

        if not file_path.exists():
            raise FileNotFoundError(f"Universe file not found: {file_path}")

        # Load parquet file
        self.df = pl.read_parquet(file_path)

        # Validate schema (accept either 'rebalance_date' or 'date')
        date_col = None
        if 'rebalance_date' in self.df.columns:
            date_col = 'rebalance_date'
        elif 'date' in self.df.columns:
            date_col = 'date'

        if not date_col or 'symbols' not in self.df.columns:
            raise ValueError(
                f"Invalid universe file schema. Expected columns: "
                f"'rebalance_date' or 'date', 'symbols'. Got: {self.df.columns}"
            )

        # Check if is_valid column exists
        has_validity_flag = 'is_valid' in self.df.columns

        # Rename 'date' to 'rebalance_date' if needed for consistency
        if date_col == 'date':
            self.df = self.df.rename({'date': 'rebalance_date'})

        # Create indices for fast lookups
        self.df = self.df.sort('rebalance_date')

        # Index for symbols: rebalance_date -> symbols list
        self._date_index = {row['rebalance_date']: row['symbols']
                           for row in self.df.to_dicts()}

        # Index for validity: rebalance_date -> is_valid flag
        if has_validity_flag:
            self._validity_index = {row['rebalance_date']: row['is_valid']
                                   for row in self.df.to_dicts()}
        else:
            # If no validity column, assume all dates are valid
            self._validity_index = {date: True for date in self._date_index.keys()}

        # Calculate superset of all unique symbols
        all_symbols_set = set()
        for symbols_list in self.df['symbols']:
            all_symbols_set.update(symbols_list)

        self._all_symbols = sorted(list(all_symbols_set))

        logger.info(f"Loaded universe from {file_path}")
        logger.info(f"Total dates: {len(self._date_index)}")
        logger.info(f"Unique symbols: {len(self._all_symbols)}")

    def get_universe(self, rebalance_date: datetime) -> List[str]:
        """
        Get list of symbols for specific rebalance date

        Args:
            rebalance_date: Rebalance date (datetime, typically at 00:00 UTC)

        Returns:
            List of symbols for this date. Empty list if date not found.

        Notes:
            - If exact date not found, returns empty list (no interpolation)
            - Caller should handle missing dates gracefully
        """
        # Convert datetime to date for lookup (universe file stores dates, not datetimes)
        lookup_date = rebalance_date.date() if hasattr(rebalance_date, 'date') else rebalance_date

        # Try exact match first
        if lookup_date in self._date_index:
            return self._date_index[lookup_date]

        # No match found - return empty list
        logger.warning(f"No universe found for date {rebalance_date}, returning empty list")
        return []

    def is_valid_date(self, rebalance_date: datetime) -> bool:
        """
        Check if a rebalance date is valid (has sufficient symbols)

        Args:
            rebalance_date: Rebalance date to check

        Returns:
            True if date is valid (≥50 symbols), False otherwise
        """
        # Convert datetime to date for lookup
        lookup_date = rebalance_date.date() if hasattr(rebalance_date, 'date') else rebalance_date

        # Return validity flag (defaults to False if date not in index)
        return self._validity_index.get(lookup_date, False)

    def get_all_symbols(self) -> List[str]:
        """
        Get superset of ALL unique symbols across entire universe file

        This is the list of symbols to load upfront in the data loader.
        By loading this superset, we avoid recalculating the universe during
        backtesting and can do fast lookups instead.

        Returns:
            Sorted list of ~80-100 unique symbols that appear across all dates
        """
        return self._all_symbols

    def get_symbols_for_period(self, start_date: datetime, end_date: datetime) -> List[str]:
        """
        Get superset of unique symbols that appear in universe within a specific date range

        This method filters the universe to only include dates within [start_date, end_date]
        and returns the unique symbols from those dates. This is more efficient than loading
        all symbols from the entire universe history.

        Args:
            start_date: Start of backtest period
            end_date: End of backtest period

        Returns:
            Sorted list of unique symbols that appear in universe during the date range

        Example:
            For January 2022 backtest, this returns ~60 symbols instead of 439 symbols
            from the entire universe file history.
        """
        # Convert datetimes to dates for comparison
        start = start_date.date() if hasattr(start_date, 'date') else start_date
        end = end_date.date() if hasattr(end_date, 'date') else end_date

        # Collect symbols from dates within the range
        period_symbols_set = set()
        for date, symbols_list in self._date_index.items():
            if start <= date <= end:
                period_symbols_set.update(symbols_list)

        period_symbols = sorted(list(period_symbols_set))

        logger.info(f"Extracted {len(period_symbols)} unique symbols for period {start} to {end}")

        return period_symbols

    def get_statistics(self) -> dict:
        """Get statistics about the universe file"""
        num_symbols_per_date = [len(s) for s in self.df['symbols']]

        return {
            'total_dates': len(self._date_index),
            'unique_symbols': len(self._all_symbols),
            'avg_symbols_per_date': sum(num_symbols_per_date) / len(num_symbols_per_date),
            'min_symbols_per_date': min(num_symbols_per_date),
            'max_symbols_per_date': max(num_symbols_per_date),
            'date_range': (min(self._date_index.keys()), max(self._date_index.keys()))
        }
