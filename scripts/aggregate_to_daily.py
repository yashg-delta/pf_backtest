"""
Daily Data Aggregation Script

Aggregates 5-minute OHLCV bars into proper daily bars with normalized timestamps.

This script:
1. Loads 5-minute parquet data
2. Groups by (SYMBOL, DATE)
3. Aggregates OHLCV:
   - OPEN: First bar's open price
   - HIGH: Maximum high price
   - LOW: Minimum low price
   - CLOSE: Last bar's close price
   - VOLUME: Sum of volume
   - NOTIONAL_VOLUME: Sum of notional volume
4. Normalizes timestamp to 00:00:00 for each date
5. Saves to tardis_trades_daily.parquet

Expected runtime: 2-5 minutes for full dataset (2021-2025)
Expected output size: ~15-20 MB
"""

import polars as pl
from pathlib import Path
from datetime import datetime, time
import logging

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class DailyAggregator:
    """Aggregate 5-minute bars to daily OHLCV"""

    def __init__(self, data_path: str):
        """
        Initialize aggregator

        Args:
            data_path: Path to data directory containing tardis_trades_5min.parquet
        """
        self.data_path = Path(data_path)
        self.input_file = self.data_path / 'tardis_trades_5min.parquet'
        self.output_file = self.data_path / 'tardis_trades_daily.parquet'

    def aggregate_to_daily(self):
        """
        Aggregate 5-minute bars to daily OHLCV

        Algorithm:
        1. Load 5-min data with lazy scan
        2. Add date column (extract from BAR_TIMESTAMP)
        3. Sort by SYMBOL, DATE, BAR_TIMESTAMP for correct first/last
        4. Group by (SYMBOL, DATE) and aggregate:
           - OPEN: first OPEN_PRICE
           - HIGH: max HIGH_PRICE
           - LOW: min LOW_PRICE
           - CLOSE: last CLOSE_PRICE
           - VOLUME: sum VOLUME
           - NOTIONAL_VOLUME: sum NOTIONAL_VOLUME
        5. Create normalized timestamp (date at 00:00:00)
        6. Sort by SYMBOL, BAR_TIMESTAMP
        7. Save to parquet
        """
        logger.info("=" * 80)
        logger.info("DAILY DATA AGGREGATION")
        logger.info("=" * 80)

        if not self.input_file.exists():
            raise FileNotFoundError(f"Input file not found: {self.input_file}")

        logger.info(f"Input:  {self.input_file}")
        logger.info(f"Output: {self.output_file}")
        logger.info(f"Input size: {self.input_file.stat().st_size / 1e9:.2f} GB")

        # Step 1: Load 5-minute data (lazy)
        logger.info("\nStep 1: Loading 5-minute data (lazy scan)...")
        df = pl.scan_parquet(self.input_file)

        # Step 2: Add date column
        logger.info("Step 2: Extracting date column...")
        df = df.with_columns(
            pl.col('BAR_TIMESTAMP').dt.date().alias('date')
        )

        # Step 3: Sort for correct first/last aggregation
        logger.info("Step 3: Sorting by SYMBOL, date, BAR_TIMESTAMP...")
        df = df.sort(['SYMBOL', 'date', 'BAR_TIMESTAMP'])

        # Step 4: Aggregate to daily
        logger.info("Step 4: Aggregating to daily OHLCV...")
        daily = df.group_by(['SYMBOL', 'date']).agg([
            # OHLC
            pl.col('OPEN_PRICE').first().alias('OPEN_PRICE'),
            pl.col('HIGH_PRICE').max().alias('HIGH_PRICE'),
            pl.col('LOW_PRICE').min().alias('LOW_PRICE'),
            pl.col('CLOSE_PRICE').last().alias('CLOSE_PRICE'),

            # Volume
            pl.col('VOLUME').sum().alias('VOLUME'),
            pl.col('NOTIONAL_VOLUME').sum().alias('NOTIONAL_VOLUME'),

            # Metadata: count of 5-min bars (for validation)
            pl.col('BAR_TIMESTAMP').count().alias('bar_count')
        ])

        # Step 5: Create normalized timestamp (00:00:00)
        logger.info("Step 5: Creating normalized timestamps...")
        daily = daily.with_columns(
            pl.col('date').cast(pl.Datetime).alias('BAR_TIMESTAMP')
        )

        # Step 6: Select final columns and sort
        logger.info("Step 6: Selecting columns and sorting...")
        daily = daily.select([
            'SYMBOL',
            'BAR_TIMESTAMP',
            'OPEN_PRICE',
            'HIGH_PRICE',
            'LOW_PRICE',
            'CLOSE_PRICE',
            'VOLUME',
            'NOTIONAL_VOLUME',
            'bar_count'  # Keep for validation
        ]).sort(['SYMBOL', 'BAR_TIMESTAMP'])

        # Collect (execute lazy operations)
        logger.info("Step 7: Collecting results (executing lazy operations)...")
        daily = daily.collect()

        logger.info(f"Aggregated {daily.shape[0]} daily bars")

        # Step 8: Save to parquet
        logger.info(f"\nStep 8: Saving to {self.output_file}...")
        daily.write_parquet(self.output_file)

        # Step 9: Print statistics
        logger.info("\n" + "=" * 80)
        logger.info("AGGREGATION COMPLETE")
        logger.info("=" * 80)

        stats = {
            'total_bars': daily.shape[0],
            'unique_symbols': daily['SYMBOL'].n_unique(),
            'date_range_start': daily['BAR_TIMESTAMP'].min(),
            'date_range_end': daily['BAR_TIMESTAMP'].max(),
            'avg_bars_per_day': daily['bar_count'].mean(),
            'min_bars_per_day': daily['bar_count'].min(),
            'max_bars_per_day': daily['bar_count'].max(),
        }

        for key, val in stats.items():
            if isinstance(val, (int, float)):
                logger.info(f"{key:25}: {val:,.1f}")
            else:
                logger.info(f"{key:25}: {val}")

        logger.info(f"{'output_size':25}: {self.output_file.stat().st_size / 1e6:.1f} MB")

        # Step 10: Validate sample
        logger.info("\n" + "=" * 80)
        logger.info("SAMPLE VALIDATION")
        logger.info("=" * 80)

        # Check BTCUSDT on 2022-01-01
        sample = daily.filter(
            (pl.col('SYMBOL') == 'BTCUSDT') &
            (pl.col('BAR_TIMESTAMP').dt.date() == pl.date(2022, 1, 1))
        )

        if len(sample) > 0:
            logger.info("BTCUSDT on 2022-01-01:")
            logger.info(f"  Timestamp: {sample['BAR_TIMESTAMP'][0]}")
            logger.info(f"  OPEN:  {sample['OPEN_PRICE'][0]:,.2f}")
            logger.info(f"  HIGH:  {sample['HIGH_PRICE'][0]:,.2f}")
            logger.info(f"  LOW:   {sample['LOW_PRICE'][0]:,.2f}")
            logger.info(f"  CLOSE: {sample['CLOSE_PRICE'][0]:,.2f}")
            logger.info(f"  VOLUME: {sample['VOLUME'][0]:,.2f}")
            logger.info(f"  NOTIONAL: {sample['NOTIONAL_VOLUME'][0]:,.2f}")
            logger.info(f"  Bars aggregated: {sample['bar_count'][0]}")

            # Compare with expected (from investigation)
            logger.info("\nExpected values (from 5-min data):")
            logger.info("  OPEN:  46,210.57")
            logger.info("  HIGH:  47,943.77")
            logger.info("  LOW:   46,210.55")
            logger.info("  CLOSE: 47,704.35")
            logger.info("  VOLUME: 179,433.58")
            logger.info("  NOTIONAL: 8,461,832,661.66")
        else:
            logger.warning("Sample validation failed: BTCUSDT 2022-01-01 not found")

        return daily


def main():
    """Aggregate 5-minute data to daily"""

    # Configuration
    data_path = '/home/yash.gupta/research/tardis_datasets/data/snowflake'

    # Run aggregation
    aggregator = DailyAggregator(data_path)
    daily_df = aggregator.aggregate_to_daily()


if __name__ == '__main__':
    main()
