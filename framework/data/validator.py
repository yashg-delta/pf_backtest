import polars as pl
from typing import Dict, List, Tuple
import logging

logger = logging.getLogger(__name__)

class DataValidator:
    """Validate data quality and completeness"""

    @staticmethod
    def validate_trades_data(data: pl.DataFrame) -> Tuple[bool, List[str]]:
        """Validate trades data"""
        issues = []

        # Check for required columns
        required_cols = [
            'SYMBOL', 'BAR_TIMESTAMP', 'CLOSE_PRICE',
            'VOLUME', 'NOTIONAL_VOLUME'
        ]
        missing_cols = set(required_cols) - set(data.columns)
        if missing_cols:
            issues.append(f"Missing columns: {missing_cols}")

        # Check for nulls in critical columns
        for col in ['CLOSE_PRICE', 'VOLUME']:
            if col in data.columns:
                null_count = data[col].null_count()
                if null_count > 0:
                    issues.append(f"{null_count} null values in {col}")

        # Check for zero/negative prices
        if 'CLOSE_PRICE' in data.columns:
            invalid_prices = data.filter(pl.col('CLOSE_PRICE') <= 0).height
            if invalid_prices > 0:
                issues.append(f"{invalid_prices} rows with invalid prices")

        # Check data continuity
        if not data.is_empty():
            time_diffs = (
                data.select('BAR_TIMESTAMP')
                .unique()
                .sort('BAR_TIMESTAMP')
                .select(
                    pl.col('BAR_TIMESTAMP').diff().dt.total_minutes()
                )
            )

            # Should be 5-minute bars
            irregular_gaps = time_diffs.filter(
                (pl.col('BAR_TIMESTAMP') != 5) &
                (pl.col('BAR_TIMESTAMP').is_not_null())
            ).height

            if irregular_gaps > 0:
                issues.append(f"{irregular_gaps} irregular time gaps")

        is_valid = len(issues) == 0
        return is_valid, issues

    @staticmethod
    def clean_data(data: pl.DataFrame) -> pl.DataFrame:
        """Clean and prepare data"""

        # Remove rows with null prices or volumes
        cleaned = data.filter(
            pl.col('CLOSE_PRICE').is_not_null() &
            pl.col('VOLUME').is_not_null() &
            (pl.col('CLOSE_PRICE') > 0) &
            (pl.col('VOLUME') >= 0)
        )

        # Sort by timestamp and symbol
        cleaned = cleaned.sort(['BAR_TIMESTAMP', 'SYMBOL'])

        rows_removed = data.height - cleaned.height
        if rows_removed > 0:
            logger.info(f"Removed {rows_removed} invalid rows")

        return cleaned
