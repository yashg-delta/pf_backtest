import polars as pl
from datetime import datetime
from typing import Dict, Any, Optional
import logging

logger = logging.getLogger(__name__)

class PortfolioTracker:
    """Track portfolio positions and P&L"""

    def __init__(self, initial_capital: float):
        self.initial_capital = initial_capital
        self.current_capital = initial_capital
        self.cash = initial_capital
        self.cumulative_pnl = 0
        self.cumulative_costs = 0

    def process_trade(self, trade: Dict[str, Any]):
        """Process a trade and update portfolio"""

        # Update cash
        trade_value = trade['quantity'] * trade['price']
        trade_cost = trade['transaction_cost']

        if trade['side'] == 'buy':
            self.cash -= (trade_value + trade_cost)
        else:  # sell
            self.cash += (trade_value - trade_cost)

        self.cumulative_costs += trade_cost

    def get_snapshot(
        self,
        timestamp: datetime,
        positions: Dict[str, Dict],
        current_data: pl.DataFrame
    ) -> Dict[str, Any]:
        """Get portfolio snapshot"""

        # Calculate position values
        total_position_value = 0
        position_pnl = 0

        for symbol, pos in positions.items():
            symbol_data = current_data.filter(pl.col('SYMBOL') == symbol)
            if not symbol_data.is_empty():
                current_price = symbol_data['CLOSE_PRICE'][0]
                market_value = pos['quantity'] * current_price
                total_position_value += market_value

                # Calculate P&L
                cost_basis = pos['quantity'] * pos['avg_price']
                position_pnl += (market_value - cost_basis)

        # Total portfolio value
        total_value = self.cash + total_position_value

        # Calculate returns
        self.cumulative_pnl = total_value - self.initial_capital
        pnl_pct = (self.cumulative_pnl / self.initial_capital) * 100

        return {
            'timestamp': timestamp,
            'cash': self.cash,
            'positions_value': total_position_value,
            'total_value': total_value,
            'pnl': self.cumulative_pnl,
            'pnl_pct': pnl_pct,
            'cumulative_costs': self.cumulative_costs,
            'position_count': len(positions)
        }
