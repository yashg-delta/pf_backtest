import polars as pl
from datetime import datetime, timedelta
from typing import List, Set
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

        # Calculate expected data points (5-min bars)
        expected_points = self.volume_lookback_days * 24 * 12  # 12 bars per hour
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
