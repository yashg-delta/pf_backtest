import polars as pl
import numpy as np
from numba import njit
from typing import Optional

class FactorCalculator:
    """Calculate various factors for strategy signals"""
    
    @staticmethod
    def momentum(
        data: pl.DataFrame,
        lookback_days: int,
        price_col: str = 'CLOSE_PRICE'
    ) -> pl.DataFrame:
        """Calculate momentum factor"""
        
        bars_lookback = lookback_days * 288  # 288 5-min bars per day
        
        return data.with_columns([
            ((pl.col(price_col) / pl.col(price_col).shift(bars_lookback)) - 1)
            .over('SYMBOL')
            .alias(f'momentum_{lookback_days}d')
        ])
    
    @staticmethod
    def volume_ratio(
        data: pl.DataFrame,
        lookback_days: int
    ) -> pl.DataFrame:
        """Calculate buy/sell volume ratio"""
        
        bars_lookback = lookback_days * 288
        
        return data.with_columns([
            (pl.col('BUY_VOLUME').rolling_sum(bars_lookback) / 
             pl.col('SELL_VOLUME').rolling_sum(bars_lookback))
            .over('SYMBOL')
            .alias(f'volume_ratio_{lookback_days}d')
        ])
    
    @staticmethod
    @njit
    def rolling_zscore_numba(values: np.ndarray, window: int) -> np.ndarray:
        """Numba-accelerated rolling z-score"""
        
        n = len(values)
        z_scores = np.full(n, np.nan)
        
        for i in range(window, n):
            window_data = values[i-window:i]
            mean = np.mean(window_data)
            std = np.std(window_data)
            
            if std > 0:
                z_scores[i] = (values[i] - mean) / std
            else:
                z_scores[i] = 0
        
        return z_scores
    
    @staticmethod
    def zscore(
        data: pl.DataFrame,
        column: str,
        lookback_days: int
    ) -> pl.DataFrame:
        """Calculate rolling z-score"""
        
        bars_lookback = lookback_days * 288
        
        return data.with_columns([
            ((pl.col(column) - pl.col(column).rolling_mean(bars_lookback)) /
             pl.col(column).rolling_std(bars_lookback))
            .over('SYMBOL')
            .alias(f'{column}_zscore_{lookback_days}d')
        ])
    
    @staticmethod
    def rank_cross_sectional(
        data: pl.DataFrame,
        column: str,
        ascending: bool = True
    ) -> pl.DataFrame:
        """Rank factors cross-sectionally at each timestamp"""
        
        return data.with_columns([
            pl.col(column)
            .rank(method='dense', descending=not ascending)
            .over('BAR_TIMESTAMP')
            .alias(f'{column}_rank')
        ])