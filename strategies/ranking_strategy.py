import polars as pl
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable
import logging

from strategies.base import BaseStrategy
from strategies.filters import RegimeFilter
from factors.calculations import FactorCalculator

logger = logging.getLogger(__name__)

class RankingStrategy(BaseStrategy):
    """Generalized ranking-based strategy that works with any factor"""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)

        # Core parameters
        self.factor_name = config['factor']['name']
        self.factor_params = config['factor']['params']
        self.top_n = config['selection']['top_n']
        self.selection_type = config['selection']['type']  # 'top' or 'bottom'

        # Regime filters
        self.filters = config.get('filters', [])

        # Factor calculation function
        self.factor_func = self._get_factor_function()

    def _get_factor_function(self) -> Callable:
        """Get the appropriate factor calculation function"""

        factor_map = {
            'momentum': FactorCalculator.momentum,
            'volume_ratio': FactorCalculator.volume_ratio,
            'momentum_difference': self._calculate_momentum_difference,
            # Add more factors here
        }

        return factor_map.get(self.factor_name)

    def _calculate_momentum_difference(
        self,
        data: pl.DataFrame,
        **params
    ) -> pl.DataFrame:
        """Calculate momentum difference (e.g., 1M - 1W)"""

        long_period = params.get('long_period', 30)
        short_period = params.get('short_period', 7)

        # Calculate both momentums
        data = FactorCalculator.momentum(data, long_period)
        data = FactorCalculator.momentum(data, short_period)

        # Calculate difference
        return data.with_columns(
            (pl.col(f'momentum_{long_period}d') -
             pl.col(f'momentum_{short_period}d'))
            .alias('momentum_difference')
        )

    def generate_signals(
        self,
        current_data: pl.DataFrame,
        historical_data: pl.DataFrame,
        timestamp: datetime
    ) -> pl.DataFrame:
        """Generate trading signals based on factor ranking"""

        # Calculate lookback start
        lookback_days = self.factor_params.get('lookback_days', 30)
        lookback_start = timestamp - timedelta(days=lookback_days + 1)

        # Get historical data for factor calculation
        factor_data = historical_data.filter(
            (pl.col('BAR_TIMESTAMP') >= lookback_start) &
            (pl.col('BAR_TIMESTAMP') <= timestamp) &
            (pl.col('SYMBOL').is_in(current_data['SYMBOL'].unique()))
        )

        if factor_data.is_empty():
            logger.warning(f"No data for factor calculation at {timestamp}")
            return pl.DataFrame()

        # Calculate factor for current universe
        factor_data = self.factor_func(factor_data, **self.factor_params)

        # Get latest factor values
        # Use date-based filtering to handle normalized timestamps
        current_date = timestamp.date()
        factor_col = self._get_factor_column()
        latest_factors = (
            factor_data
            .filter(pl.col('BAR_TIMESTAMP').dt.date() == current_date)
            .sort(['SYMBOL', 'BAR_TIMESTAMP'])
            .group_by('SYMBOL')
            .agg(pl.last(factor_col).alias(factor_col))
        )

        if latest_factors.is_empty():
            return pl.DataFrame()

        # Filter to only symbols with VALID (non-null) factor values
        factor_col = self._get_factor_column()
        valid_factors = latest_factors.filter(
            pl.col(factor_col).is_not_null()
        )

        # Check if we have at least top_n valid factors
        if len(valid_factors) < self.top_n:
            logger.warning(
                f"Insufficient valid factors at {timestamp}: "
                f"{len(valid_factors)} valid symbols < {self.top_n} required. "
                f"Skipping rebalance."
            )
            return pl.DataFrame()

        # Rank factors (only valid ones)
        valid_factors = valid_factors.with_columns(
            pl.col(factor_col)
            .rank(method='dense', descending=(self.selection_type == 'top'))
            .alias('rank')
        )

        # Select top/bottom N
        signals = valid_factors.filter(
            pl.col('rank') <= self.top_n
        )

        # Add signal strength (normalized rank)
        signals = signals.with_columns(
            (1 - (pl.col('rank') - 1) / self.top_n).alias('signal_strength'),
            pl.lit('long').alias('direction')
        )

        logger.info(f"Generated {len(signals)} signals at {timestamp} (from {len(valid_factors)} valid)")
        return signals

    def _get_factor_column(self) -> str:
        """Get the factor column name"""

        if self.factor_name == 'momentum':
            return f"momentum_{self.factor_params['lookback_days']}d"
        elif self.factor_name == 'momentum_difference':
            return 'momentum_difference'
        elif self.factor_name == 'volume_ratio':
            return f"volume_ratio_{self.factor_params['lookback_days']}d"
        else:
            return self.factor_name

    def apply_filters(
        self,
        signals: pl.DataFrame,
        current_data: pl.DataFrame,
        historical_data: pl.DataFrame,
        timestamp: datetime
    ) -> pl.DataFrame:
        """Apply configured regime filters"""

        filtered_signals = signals

        for filter_config in self.filters:
            filter_type = filter_config['type']

            if filter_type == 'btc_ma':
                filtered_signals = RegimeFilter.btc_ma_filter(
                    filtered_signals,
                    historical_data,
                    timestamp,
                    ma_period=filter_config.get('ma_period', 200)
                )

            elif filter_type == 'volatility':
                filtered_signals = RegimeFilter.volatility_filter(
                    filtered_signals,
                    historical_data,
                    timestamp,
                    vol_threshold=filter_config.get('threshold', 0.5),
                    lookback_days=filter_config.get('lookback_days', 30)
                )

            # Add more filter types here

        if len(filtered_signals) != len(signals):
            logger.info(f"Filtered {len(signals)} -> {len(filtered_signals)} signals")

        return filtered_signals

    def calculate_positions(
        self,
        signals: pl.DataFrame,
        portfolio_value: float
    ) -> Dict[str, float]:
        """Calculate equal-weight positions"""

        if signals.is_empty():
            return {}

        # Equal weighting
        n_positions = len(signals)
        weight = 1.0 / n_positions

        positions = {}
        for row in signals.iter_rows(named=True):
            positions[row['SYMBOL']] = weight

        return positions
