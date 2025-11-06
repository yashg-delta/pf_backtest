import duckdb
import polars as pl
from pathlib import Path
from typing import List, Optional, Dict
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class DataLoader:
    """Efficient data loading using DuckDB and Polars"""

    def __init__(self, data_path: str):
        self.data_path = Path(data_path)
        self.conn = duckdb.connect(':memory:')
        self._register_tables()

    def _register_tables(self):
        """Register parquet files as DuckDB views"""
        tables = {
            'trades': 'tardis_trades_5min.parquet',
            'liquidations': 'tardis_liquidations_5min.parquet',
            'derivative': 'tardis_derivative_ticker_5min.parquet'
        }

        for name, file in tables.items():
            file_path = self.data_path / file
            if file_path.exists():
                self.conn.execute(f"""
                    CREATE VIEW {name} AS
                    SELECT * FROM read_parquet('{file_path}')
                """)
                logger.info(f"Registered {name} table from {file}")

    def load_trades_data(
        self,
        start_date: datetime,
        end_date: datetime,
        symbols: Optional[List[str]] = None
    ) -> pl.DataFrame:
        """Load trades data for specified period and symbols"""

        query = f"""
        SELECT * FROM trades
        WHERE BAR_TIMESTAMP >= '{start_date}'
        AND BAR_TIMESTAMP <= '{end_date}'
        """

        if symbols:
            symbols_str = ','.join([f"'{s}'" for s in symbols])
            query += f" AND SYMBOL IN ({symbols_str})"

        query += " ORDER BY BAR_TIMESTAMP, SYMBOL"

        arrow_table = self.conn.execute(query).fetch_arrow_table()
        return pl.from_arrow(arrow_table)

    def load_ratios_data(
        self,
        start_date: datetime,
        end_date: datetime,
        symbols: Optional[List[str]] = None
    ) -> pl.DataFrame:
        """Load BV ratios data"""

        # Handle gzipped CSV
        file_path = self.data_path / 'bv_ratios.csv.gz'

        df = pl.read_csv(file_path, try_parse_dates=True)

        # Filter by date range
        df = df.filter(
            (pl.col("CREATE_TIME") >= start_date) &
            (pl.col("CREATE_TIME") <= end_date)
        )

        # Filter by symbols if provided
        if symbols:
            df = df.filter(pl.col("SYMBOL").is_in(symbols))

        return df.sort("CREATE_TIME", "SYMBOL")

    def load_all_data(
        self,
        start_date: datetime,
        end_date: datetime,
        symbols: Optional[List[str]] = None
    ) -> Dict[str, pl.DataFrame]:
        """Load all available datasets"""

        result = {}

        # Load trades
        result['trades'] = self.load_trades_data(start_date, end_date, symbols)

        # Load liquidations
        try:
            query = f"""
            SELECT * FROM liquidations
            WHERE BAR_TIMESTAMP >= '{start_date}'
            AND BAR_TIMESTAMP <= '{end_date}'
            """
            if symbols:
                symbols_str = ','.join([f"'{s}'" for s in symbols])
                query += f" AND SYMBOL IN ({symbols_str})"

            arrow_table = self.conn.execute(query).fetch_arrow_table()
            result['liquidations'] = pl.from_arrow(arrow_table)
        except:
            logger.warning("Could not load liquidations data")

        # Load ratios
        try:
            result['ratios'] = self.load_ratios_data(start_date, end_date, symbols)
        except:
            logger.warning("Could not load ratios data")

        return result

    def get_unique_symbols(self, start_date: datetime, end_date: datetime) -> List[str]:
        """Get all unique symbols in the date range"""

        query = f"""
        SELECT DISTINCT SYMBOL
        FROM trades
        WHERE BAR_TIMESTAMP >= '{start_date}'
        AND BAR_TIMESTAMP <= '{end_date}'
        ORDER BY SYMBOL
        """

        result = self.conn.execute(query).fetchall()
        return [r[0] for r in result]
