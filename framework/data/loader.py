import duckdb
import polars as pl
from pathlib import Path
from typing import List, Optional, Dict
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class DataLoader:
    """Efficient data loading using DuckDB and Polars"""

    # Default columns to load if not specified
    DEFAULT_COLUMNS = [
        'SYMBOL', 'BAR_TIMESTAMP', 'CLOSE_PRICE', 'VOLUME', 'NOTIONAL_VOLUME'
    ]

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

    def _build_column_list(self, columns: Optional[List[str]]) -> str:
        """Build SQL column list from list of column names"""
        if columns is None:
            columns = self.DEFAULT_COLUMNS
        return ', '.join(columns)

    def load_trades_data(
        self,
        start_date: datetime,
        end_date: datetime,
        symbols: Optional[List[str]] = None,
        columns: Optional[List[str]] = None
    ) -> pl.DataFrame:
        """Load trades data for specified period and symbols

        Args:
            start_date: Start date for data
            end_date: End date for data
            symbols: Optional list of symbols to load
            columns: Optional list of columns to load (defaults to DEFAULT_COLUMNS)
        """

        column_list = self._build_column_list(columns)

        query = f"""
        SELECT {column_list} FROM trades
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
        symbols: Optional[List[str]] = None,
        columns: Optional[List[str]] = None,
        load_liquidations: bool = False,
        load_ratios: bool = False
    ) -> Dict[str, pl.DataFrame]:
        """Load all available datasets

        Args:
            start_date: Start date for data
            end_date: End date for data
            symbols: Optional list of symbols to load
            columns: Optional list of columns to load (defaults to DEFAULT_COLUMNS)
            load_liquidations: Whether to load liquidations data (default: False)
            load_ratios: Whether to load ratios data (default: False)
        """

        result = {}

        # Load trades (always loaded)
        result['trades'] = self.load_trades_data(start_date, end_date, symbols, columns)

        # Load liquidations (only if requested)
        if load_liquidations:
            try:
                column_list = self._build_column_list(columns)
                query = f"""
                SELECT {column_list} FROM liquidations
                WHERE BAR_TIMESTAMP >= '{start_date}'
                AND BAR_TIMESTAMP <= '{end_date}'
                """
                if symbols:
                    symbols_str = ','.join([f"'{s}'" for s in symbols])
                    query += f" AND SYMBOL IN ({symbols_str})"

                arrow_table = self.conn.execute(query).fetch_arrow_table()
                result['liquidations'] = pl.from_arrow(arrow_table)
            except Exception as e:
                logger.warning(f"Could not load liquidations data: {e}")

        # Load ratios (only if requested)
        if load_ratios:
            try:
                result['ratios'] = self.load_ratios_data(start_date, end_date, symbols)
            except Exception as e:
                logger.warning(f"Could not load ratios data: {e}")

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
