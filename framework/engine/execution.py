import polars as pl
from datetime import datetime
from typing import Dict, List, Optional, Any
import logging

logger = logging.getLogger(__name__)

class ExecutionEngine:
    """Handle order generation and execution"""

    def __init__(self, transaction_cost_bps: float = 3):
        self.transaction_cost_bps = transaction_cost_bps

    def generate_orders(
        self,
        current_positions: Dict[str, Dict],
        target_positions: Dict[str, float],
        current_data: pl.DataFrame
    ) -> List[Dict[str, Any]]:
        """Generate orders to move from current to target positions"""

        orders = []

        # Get current symbols and target symbols
        current_symbols = set(current_positions.keys())
        target_symbols = set(target_positions.keys())

        # Exit positions not in target
        for symbol in current_symbols - target_symbols:
            orders.append({
                'symbol': symbol,
                'side': 'sell',
                'quantity': current_positions[symbol]['quantity'],
                'order_type': 'exit'
            })

        # Enter new positions
        for symbol in target_symbols - current_symbols:
            orders.append({
                'symbol': symbol,
                'side': 'buy',
                'target_weight': target_positions[symbol],
                'order_type': 'entry'
            })

        # Adjust existing positions (optional - for rebalancing)
        # For now, we hold positions until exit

        return orders

    def execute_orders(
        self,
        orders: List[Dict],
        current_data: pl.DataFrame,
        timestamp: datetime,
        portfolio_value: float
    ) -> List[Dict[str, Any]]:
        """Execute orders and return trades"""

        # Debug: log the portfolio value being used for sizing
        logger.info(f"[EXEC] Executing {len(orders)} orders with portfolio_value=${portfolio_value:,.2f}")

        trades = []

        for order in orders:
            symbol_data = current_data.filter(pl.col('SYMBOL') == order['symbol'])

            if symbol_data.is_empty():
                logger.warning(f"No data for {order['symbol']} at {timestamp}")
                continue

            # Get execution price (close price)
            exec_price = symbol_data['CLOSE_PRICE'][0]

            # Calculate quantity for buys
            if order['side'] == 'buy':
                position_value = portfolio_value * order['target_weight']
                quantity = position_value / exec_price
            else:
                quantity = order['quantity']

            # Calculate transaction cost
            trade_value = quantity * exec_price
            transaction_cost = trade_value * (self.transaction_cost_bps / 10000)

            # Create trade record
            trade = {
                'timestamp': timestamp,
                'symbol': order['symbol'],
                'side': order['side'],
                'quantity': quantity,
                'price': exec_price,
                'value': trade_value,
                'transaction_cost': transaction_cost,
                'order_type': order['order_type']
            }

            trades.append(trade)
            logger.debug(f"Executed {order['side']} {quantity:.4f} {order['symbol']} @ {exec_price:.4f}")

        return trades

    def update_positions(
        self,
        current_positions: Dict[str, Dict],
        trades: List[Dict]
    ) -> Dict[str, Dict]:
        """Update positions based on executed trades"""

        positions = dict(current_positions)

        for trade in trades:
            symbol = trade['symbol']

            if trade['side'] == 'buy':
                if symbol in positions:
                    # Add to existing position
                    pos = positions[symbol]
                    new_quantity = pos['quantity'] + trade['quantity']
                    new_cost = (pos['quantity'] * pos['avg_price'] +
                               trade['quantity'] * trade['price'])
                    pos['quantity'] = new_quantity
                    pos['avg_price'] = new_cost / new_quantity if new_quantity > 0 else 0
                else:
                    # New position
                    positions[symbol] = {
                        'quantity': trade['quantity'],
                        'avg_price': trade['price'],
                        'entry_time': trade['timestamp']
                    }

            else:  # sell
                if symbol in positions:
                    pos = positions[symbol]
                    pos['quantity'] -= trade['quantity']

                    # Remove position if fully closed
                    if pos['quantity'] <= 0.0001:  # Small threshold for floating point
                        del positions[symbol]

        return positions
