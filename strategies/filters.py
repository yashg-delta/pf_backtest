import polars as pl
from datetime import datetime, timedelta
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

class RegimeFilter:
    """Reusable regime filters for strategies"""

    @staticmethod
    def btc_ma_filter(
        signals: pl.DataFrame,
        historical_data: pl.DataFrame,
        timestamp: datetime,
        ma_period: int = 200,
        symbol: str = 'BTCUSDT'
    ) -> pl.DataFrame:
        """Filter signals based on BTC moving average"""

        # Calculate MA lookback
        lookback_start = timestamp - timedelta(days=ma_period + 1)

        # Get BTC data
        btc_data = historical_data.filter(
            (pl.col('SYMBOL') == symbol) &
            (pl.col('BAR_TIMESTAMP') >= lookback_start) &
            (pl.col('BAR_TIMESTAMP') <= timestamp)
        )

        if btc_data.is_empty():
            logger.warning(f"No BTC data for MA filter at {timestamp}")
            return signals

        # Calculate MA
        btc_ma = btc_data['CLOSE_PRICE'].mean()

        # Get current BTC price
        current_btc = btc_data.filter(
            pl.col('BAR_TIMESTAMP') == timestamp
        )

        if current_btc.is_empty():
            return signals

        current_price = current_btc['CLOSE_PRICE'][0]

        # Apply filter
        if current_price > btc_ma:
            logger.info(f"BTC above MA: {current_price:.2f} > {btc_ma:.2f}")
            return signals
        else:
            logger.info(f"BTC below MA: {current_price:.2f} < {btc_ma:.2f} - Filtering all signals")
            return pl.DataFrame()  # Return empty DataFrame

    @staticmethod
    def volatility_filter(
        signals: pl.DataFrame,
        historical_data: pl.DataFrame,
        timestamp: datetime,
        vol_threshold: float = 0.5,
        lookback_days: int = 30
    ) -> pl.DataFrame:
        """Filter based on market volatility"""

        # Calculate market volatility (using BTC as proxy)
        lookback_start = timestamp - timedelta(days=lookback_days)

        btc_data = historical_data.filter(
            (pl.col('SYMBOL') == 'BTCUSDT') &
            (pl.col('BAR_TIMESTAMP') >= lookback_start) &
            (pl.col('BAR_TIMESTAMP') <= timestamp)
        )

        if btc_data.is_empty():
            return signals

        # Calculate returns
        returns = btc_data.select(
            pl.col('CLOSE_PRICE').pct_change().alias('returns')
        )

        # Annualized volatility (daily data: 365 days per year for crypto markets)
        volatility = returns['returns'].std() * (365 ** 0.5)

        if volatility < vol_threshold:
            return signals
        else:
            logger.info(f"High volatility: {volatility:.2%} - Filtering signals")
            return pl.DataFrame()
