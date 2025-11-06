import polars as pl
import matplotlib.pyplot as plt
import seaborn as sns
from datetime import datetime
from typing import Optional
import numpy as np

class Visualizer:
    """Generate backtest visualizations"""

    @staticmethod
    def plot_equity_curve(
        portfolio_ts: pl.DataFrame,
        save_path: Optional[str] = None
    ):
        """Plot cumulative P&L curve"""

        fig, ax = plt.subplots(figsize=(14, 7))

        # Convert to numpy for plotting
        timestamps = portfolio_ts['timestamp'].to_numpy()
        equity = portfolio_ts['total_value'].to_numpy()

        ax.plot(timestamps, equity, linewidth=1.5, color='#2E86AB')
        ax.fill_between(timestamps, equity[0], equity, alpha=0.3, color='#2E86AB')

        ax.set_title('Equity Curve', fontsize=16, fontweight='bold')
        ax.set_xlabel('Date', fontsize=12)
        ax.set_ylabel('Portfolio Value ($)', fontsize=12)
        ax.grid(True, alpha=0.3)

        # Format y-axis
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'${x:,.0f}'))

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=100, bbox_inches='tight')

        return fig

    @staticmethod
    def plot_drawdown(
        portfolio_ts: pl.DataFrame,
        save_path: Optional[str] = None
    ):
        """Plot drawdown chart"""

        fig, ax = plt.subplots(figsize=(14, 5))

        # Calculate drawdown
        equity = portfolio_ts['total_value'].to_numpy()
        running_max = np.maximum.accumulate(equity)
        drawdown = ((equity - running_max) / running_max) * 100

        timestamps = portfolio_ts['timestamp'].to_numpy()

        # Plot drawdown
        ax.fill_between(timestamps, 0, drawdown, color='#FF6B6B', alpha=0.7)
        ax.plot(timestamps, drawdown, color='#C92A2A', linewidth=1)

        ax.set_title('Drawdown', fontsize=16, fontweight='bold')
        ax.set_xlabel('Date', fontsize=12)
        ax.set_ylabel('Drawdown (%)', fontsize=12)
        ax.grid(True, alpha=0.3)

        # Add horizontal line at 0
        ax.axhline(y=0, color='black', linewidth=0.5)

        # Format y-axis
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x:.1f}%'))

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=100, bbox_inches='tight')

        return fig

    @staticmethod
    def plot_btc_correlation(
        portfolio_ts: pl.DataFrame,
        btc_data: pl.DataFrame,
        window: int = 30,
        save_path: Optional[str] = None
    ):
        """Plot rolling correlation with BTC"""

        fig, ax = plt.subplots(figsize=(14, 5))

        # Merge portfolio and BTC data
        merged = portfolio_ts.join(
            btc_data.select(['timestamp', 'CLOSE_PRICE']),
            on='timestamp',
            how='inner'
        )

        if merged.is_empty():
            print("No overlapping data for BTC correlation")
            return fig

        # Calculate returns
        merged = merged.with_columns([
            pl.col('total_value').pct_change().alias('portfolio_return'),
            pl.col('CLOSE_PRICE').pct_change().alias('btc_return')
        ])

        # Calculate rolling correlation
        merged = merged.with_columns(
            pl.corr('portfolio_return', 'btc_return')
            .rolling_window(window * 288)  # 288 5-min bars per day
            .alias('correlation')
        )

        # Plot
        timestamps = merged['timestamp'].to_numpy()
        correlation = merged['correlation'].to_numpy()

        ax.plot(timestamps, correlation, linewidth=1.5, color='#7209B7')
        ax.fill_between(timestamps, 0, correlation, alpha=0.3, color='#7209B7')

        ax.set_title(f'Rolling {window}-Day Correlation with BTC', fontsize=16, fontweight='bold')
        ax.set_xlabel('Date', fontsize=12)
        ax.set_ylabel('Correlation', fontsize=12)
        ax.set_ylim(-1, 1)
        ax.grid(True, alpha=0.3)

        # Add horizontal lines
        ax.axhline(y=0, color='black', linewidth=0.5)
        ax.axhline(y=0.5, color='gray', linewidth=0.5, linestyle='--', alpha=0.5)
        ax.axhline(y=-0.5, color='gray', linewidth=0.5, linestyle='--', alpha=0.5)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=100, bbox_inches='tight')

        return fig

    @staticmethod
    def plot_monthly_returns_heatmap(
        monthly_matrix: pl.DataFrame,
        save_path: Optional[str] = None
    ):
        """Plot monthly returns heatmap"""

        fig, ax = plt.subplots(figsize=(14, 6))

        # Convert to numpy matrix
        years = monthly_matrix['year'].to_numpy()
        matrix_data = monthly_matrix.drop('year').to_numpy()

        # Create heatmap
        sns.heatmap(
            matrix_data,
            annot=True,
            fmt='.1f',
            cmap='RdYlGn',
            center=0,
            cbar_kws={'label': 'Return (%)'},
            yticklabels=years,
            xticklabels=['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                        'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
            ax=ax
        )

        ax.set_title('Monthly Returns (%)', fontsize=16, fontweight='bold')
        ax.set_xlabel('')
        ax.set_ylabel('Year', fontsize=12)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=100, bbox_inches='tight')

        return fig
