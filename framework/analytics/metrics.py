import numpy as np
import polars as pl
from typing import Dict, Any
import logging

logger = logging.getLogger(__name__)

class MetricsCalculator:
    """Calculate performance metrics"""

    @staticmethod
    def calculate_all_metrics(
        trades_df: pl.DataFrame,
        portfolio_ts: pl.DataFrame,
        daily_ts: pl.DataFrame
    ) -> Dict[str, float]:
        """Calculate all performance metrics"""

        metrics = {}

        # Portfolio metrics from daily data
        if not daily_ts.is_empty():
            daily_returns = daily_ts['pnl_pct'].to_numpy()

            # Basic metrics
            metrics['total_return'] = daily_ts['pnl_pct'][-1] if len(daily_ts) > 0 else 0
            metrics['annualized_return'] = MetricsCalculator._annualized_return(daily_returns)
            metrics['volatility'] = MetricsCalculator._annualized_volatility(daily_returns)
            metrics['sharpe_ratio'] = MetricsCalculator._sharpe_ratio(daily_returns)
            metrics['sortino_ratio'] = MetricsCalculator._sortino_ratio(daily_returns)

            # Drawdown metrics
            dd_stats = MetricsCalculator._drawdown_stats(daily_ts['total_value'].to_numpy())
            metrics.update(dd_stats)

            # Calmar ratio
            if dd_stats['max_drawdown'] != 0:
                metrics['calmar_ratio'] = metrics['annualized_return'] / abs(dd_stats['max_drawdown'])
            else:
                metrics['calmar_ratio'] = 0

        # Trade metrics
        if not trades_df.is_empty():
            trade_metrics = MetricsCalculator._trade_statistics(trades_df)
            metrics.update(trade_metrics)

        return metrics

    @staticmethod
    def _annualized_return(daily_returns: np.ndarray) -> float:
        """Calculate annualized return"""
        if len(daily_returns) == 0:
            return 0

        total_return = (1 + daily_returns[-1] / 100) - 1
        n_days = len(daily_returns)
        years = n_days / 365  # Crypto markets: 365 days per year

        if years > 0:
            annualized = (1 + total_return) ** (1 / years) - 1
            return annualized * 100
        return 0

    @staticmethod
    def _annualized_volatility(daily_returns: np.ndarray) -> float:
        """Calculate annualized volatility"""
        if len(daily_returns) < 2:
            return 0

        daily_pnl_diff = np.diff(daily_returns)
        return np.std(daily_pnl_diff) * np.sqrt(365)  # Crypto markets: 365 days per year

    @staticmethod
    def _sharpe_ratio(daily_returns: np.ndarray, risk_free_rate: float = 0) -> float:
        """Calculate Sharpe ratio"""
        if len(daily_returns) < 2:
            return 0

        daily_pnl_diff = np.diff(daily_returns)
        excess_returns = daily_pnl_diff - risk_free_rate / 365  # Crypto markets: 365 days per year

        if np.std(daily_pnl_diff) > 0:
            return np.mean(excess_returns) / np.std(daily_pnl_diff) * np.sqrt(365)  # Crypto markets: 365 days per year
        return 0

    @staticmethod
    def _sortino_ratio(daily_returns: np.ndarray, risk_free_rate: float = 0) -> float:
        """Calculate Sortino ratio"""
        if len(daily_returns) < 2:
            return 0

        daily_pnl_diff = np.diff(daily_returns)
        excess_returns = daily_pnl_diff - risk_free_rate / 365  # Crypto markets: 365 days per year
        downside_returns = daily_pnl_diff[daily_pnl_diff < 0]

        if len(downside_returns) > 0:
            downside_std = np.std(downside_returns)
            if downside_std > 0:
                return np.mean(excess_returns) / downside_std * np.sqrt(365)  # Crypto markets: 365 days per year
        return 0

    @staticmethod
    def _drawdown_stats(equity_curve: np.ndarray) -> Dict[str, float]:
        """Calculate drawdown statistics"""

        # Calculate running maximum
        running_max = np.maximum.accumulate(equity_curve)

        # Calculate drawdown
        drawdown = (equity_curve - running_max) / running_max

        # Max drawdown
        max_drawdown = np.min(drawdown) * 100 if len(drawdown) > 0 else 0

        # Max drawdown duration
        duration = 0
        max_duration = 0
        in_drawdown = False

        for i in range(len(drawdown)):
            if drawdown[i] < 0:
                if not in_drawdown:
                    in_drawdown = True
                    duration = 1
                else:
                    duration += 1
                max_duration = max(max_duration, duration)
            else:
                in_drawdown = False
                duration = 0

        return {
            'max_drawdown': max_drawdown,
            'max_drawdown_duration_days': max_duration
        }

    @staticmethod
    def _trade_statistics(trades_df: pl.DataFrame) -> Dict[str, float]:
        """Calculate trade-level statistics"""

        # All trades in trades_df are closed trades (matched entry-exit pairs)
        exits = trades_df.filter(pl.col('exit_time').is_not_null())

        if exits.is_empty():
            return {
                'total_trades': 0,
                'win_rate': 0,
                'avg_win': 0,
                'avg_loss': 0,
                'profit_factor': 0,
                'avg_holding_period_hours': 0
            }

        # Calculate P&L for each trade
        trade_pnls = exits['pnl_pct'].to_numpy()

        # Win rate
        wins = trade_pnls[trade_pnls > 0]
        losses = trade_pnls[trade_pnls < 0]

        win_rate = len(wins) / len(trade_pnls) * 100 if len(trade_pnls) > 0 else 0

        # Average win/loss
        avg_win = np.mean(wins) if len(wins) > 0 else 0
        avg_loss = np.mean(losses) if len(losses) > 0 else 0

        # Profit factor
        total_wins = np.sum(wins) if len(wins) > 0 else 0
        total_losses = abs(np.sum(losses)) if len(losses) > 0 else 1
        profit_factor = total_wins / total_losses if total_losses > 0 else 0

        # Holding period
        if 'holding_period_hours' in exits.columns:
            avg_holding = exits['holding_period_hours'].mean()
        else:
            avg_holding = 0

        # Consecutive wins/losses
        consecutive_stats = MetricsCalculator._consecutive_trades(trade_pnls)

        return {
            'total_trades': len(exits),
            'win_rate': win_rate,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'profit_factor': profit_factor,
            'avg_holding_period_hours': avg_holding,
            'max_consecutive_wins': consecutive_stats['max_wins'],
            'max_consecutive_losses': consecutive_stats['max_losses']
        }

    @staticmethod
    def _consecutive_trades(pnls: np.ndarray) -> Dict[str, int]:
        """Calculate consecutive wins/losses"""

        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0

        for pnl in pnls:
            if pnl > 0:
                current_wins += 1
                current_losses = 0
                max_wins = max(max_wins, current_wins)
            elif pnl < 0:
                current_losses += 1
                current_wins = 0
                max_losses = max(max_losses, current_losses)

        return {
            'max_wins': max_wins,
            'max_losses': max_losses
        }
