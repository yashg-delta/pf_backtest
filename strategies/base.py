from abc import ABC, abstractmethod
import polars as pl
from datetime import datetime
from typing import Dict, List, Any, Optional

class BaseStrategy(ABC):
    """Abstract base class for all strategies"""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.name = config.get('name', 'unnamed_strategy')

    @abstractmethod
    def generate_signals(
        self,
        current_data: pl.DataFrame,
        historical_data: pl.DataFrame,
        timestamp: datetime
    ) -> pl.DataFrame:
        """
        Generate trading signals

        Returns:
            DataFrame with columns: symbol, signal_strength, direction
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
        """
        Apply filters to signals (e.g., regime filters)

        Returns:
            Filtered signals DataFrame
        """
        pass

    @abstractmethod
    def calculate_positions(
        self,
        signals: pl.DataFrame,
        portfolio_value: float
    ) -> Dict[str, float]:
        """
        Calculate target position weights

        Returns:
            Dict of {symbol: weight} where weights sum to 1.0
        """
        pass
