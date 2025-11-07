"""
Aggregate 5-minute OHLCV data to daily bars
Reads tardis_trades_5min.parquet and creates tardis_trades_daily.parquet
"""

import polars as pl
import logging
from pathlib import Path
from datetime import datetime

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Data paths
DATA_DIR = Path('/home/yash.gupta/research/tardis_datasets/data/snowflake')
INPUT_FILE = DATA_DIR / 'tardis_trades_5min.parquet'
OUTPUT_FILE = DATA_DIR / 'tardis_trades_daily.parquet'

def aggregate_to_daily():
    """Aggregate 5-minute bars to daily OHLCV"""

    logger.info(f"Reading 5-minute data from {INPUT_FILE}")

    # Load 5-min data
    df = pl.read_parquet(INPUT_FILE)
    logger.info(f"Loaded {len(df):,} rows of 5-minute data")
    logger.info(f"Columns: {df.columns}")

    # Extract date from BAR_TIMESTAMP for grouping
    df = df.with_columns(
        pl.col('BAR_TIMESTAMP').dt.date().alias('date')
    )

    # Aggregate to daily
    logger.info("Aggregating to daily...")
    daily = (
        df
        .group_by(['date', 'SYMBOL'])
        .agg([
            pl.col('BAR_TIMESTAMP').first().alias('BAR_TIMESTAMP'),  # First timestamp of day
            pl.col('OPEN_PRICE').first().alias('OPEN_PRICE'),
            pl.col('HIGH_PRICE').max().alias('HIGH_PRICE'),
            pl.col('LOW_PRICE').min().alias('LOW_PRICE'),
            pl.col('CLOSE_PRICE').last().alias('CLOSE_PRICE'),
            pl.col('VOLUME').sum().alias('VOLUME'),
            pl.col('NOTIONAL_VOLUME').sum().alias('NOTIONAL_VOLUME'),
        ])
        .sort(['SYMBOL', 'BAR_TIMESTAMP'])
    )

    # Drop the temporary date column
    daily = daily.drop('date')

    logger.info(f"Aggregated to {len(daily):,} daily bars ({len(daily) / len(df) * 100:.1f}% of original)")
    logger.info(f"Date range: {daily['BAR_TIMESTAMP'].min()} to {daily['BAR_TIMESTAMP'].max()}")
    logger.info(f"Unique symbols: {daily['SYMBOL'].n_unique()}")

    # Save daily data
    logger.info(f"Saving daily data to {OUTPUT_FILE}")
    daily.write_parquet(OUTPUT_FILE)

    logger.info(f"SUCCESS: Daily aggregation complete!")
    logger.info(f"Output file size: {OUTPUT_FILE.stat().st_size / 1024 / 1024:.1f} MB")

    return daily

if __name__ == '__main__':
    try:
        daily = aggregate_to_daily()

        # Print schema
        print("\n" + "="*60)
        print("DAILY DATA SCHEMA")
        print("="*60)
        print(daily.schema)

        # Print sample
        print("\n" + "="*60)
        print("SAMPLE DATA (first 5 rows)")
        print("="*60)
        print(daily.head())

    except Exception as e:
        logger.error(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
