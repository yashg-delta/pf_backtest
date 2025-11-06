import polars as pl
from typing import List, Dict, Any
from datetime import datetime

class TradeAnalyzer:
    """Analyze and format trade data"""

    @staticmethod
    def create_trade_sheet(trades: List[Dict[str, Any]]) -> pl.DataFrame:
        """Create formatted trade sheet with P&L calculations"""

        if not trades:
            return pl.DataFrame()

        # Group trades by symbol to match entries with exits
        trades_df = pl.DataFrame(trades)

        # Separate entries and exits
        entries = trades_df.filter(pl.col('side') == 'buy')
        exits = trades_df.filter(pl.col('side') == 'sell')

        trade_records = []

        for exit in exits.iter_rows(named=True):
            # Find corresponding entry
            symbol_entries = entries.filter(
                (pl.col('symbol') == exit['symbol']) &
                (pl.col('timestamp') < exit['timestamp'])
            )

            if not symbol_entries.is_empty():
                # Use FIFO - first entry
                entry = symbol_entries.sort('timestamp').row(0, named=True)

                # Calculate P&L
                entry_value = entry['quantity'] * entry['price']
                exit_value = exit['quantity'] * exit['price']
                pnl_dollar = exit_value - entry_value - entry['transaction_cost'] - exit['transaction_cost']
                pnl_pct = (pnl_dollar / entry_value) * 100 if entry_value > 0 else 0

                # Calculate holding period
                holding_period = (exit['timestamp'] - entry['timestamp']).total_seconds() / 3600

                trade_records.append({
                    'symbol': exit['symbol'],
                    'entry_time': entry['timestamp'],
                    'exit_time': exit['timestamp'],
                    'entry_price': entry['price'],
                    'exit_price': exit['price'],
                    'quantity': min(entry['quantity'], exit['quantity']),
                    'notional_value': entry_value,
                    'pnl_dollar': pnl_dollar,
                    'pnl_pct': pnl_pct,
                    'holding_period_hours': holding_period,
                    'entry_cost': entry['transaction_cost'],
                    'exit_cost': exit['transaction_cost'],
                    'total_cost': entry['transaction_cost'] + exit['transaction_cost']
                })

        if not trade_records:
            return pl.DataFrame()

        return pl.DataFrame(trade_records).sort('exit_time')

    @staticmethod
    def aggregate_to_daily(portfolio_ts: pl.DataFrame) -> pl.DataFrame:
        """Aggregate 5-minute data to daily"""

        if portfolio_ts.is_empty():
            return pl.DataFrame()

        daily = (
            portfolio_ts
            .with_columns(
                pl.col('timestamp').dt.truncate('1d').alias('date')
            )
            .group_by('date')
            .agg([
                pl.col('total_value').last().alias('total_value'),
                pl.col('pnl').last().alias('pnl'),
                pl.col('pnl_pct').last().alias('pnl_pct'),
                pl.col('position_count').last().alias('position_count'),
                pl.col('cumulative_costs').last().alias('cumulative_costs')
            ])
            .sort('date')
        )

        return daily

    @staticmethod
    def create_monthly_matrix(daily_ts: pl.DataFrame) -> pl.DataFrame:
        """Create year x month returns matrix"""

        if daily_ts.is_empty():
            return pl.DataFrame()

        # Calculate daily returns
        daily_returns = daily_ts.with_columns(
            (pl.col('total_value').pct_change() * 100).alias('daily_return')
        )

        # Add year and month columns
        monthly = (
            daily_returns
            .with_columns([
                pl.col('date').dt.year().alias('year'),
                pl.col('date').dt.month().alias('month')
            ])
            .group_by(['year', 'month'])
            .agg(
                # Compound monthly return
                ((1 + pl.col('daily_return') / 100).product() - 1) * 100
                .alias('monthly_return')
            )
        )

        # Pivot to matrix format
        matrix = monthly.pivot(
            values='monthly_return',
            index='year',
            columns='month'
        ).sort('year')

        # Rename columns to month names
        month_names = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                      'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

        for i in range(1, 13):
            if str(i) in matrix.columns:
                matrix = matrix.rename({str(i): month_names[i-1]})

        return matrix
