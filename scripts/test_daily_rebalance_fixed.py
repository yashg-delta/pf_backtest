#!/usr/bin/env python3
"""
Test script demonstrating CORRECT BacktestConfig instantiation that reads rebalance settings from YAML
This fixes the bug where daily rebalancing configs were being treated as weekly
"""

import yaml
import sys
import os
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from framework.data.loader import DataLoader
from framework.data.universe import UniverseSelector
from framework.engine.backtest import BacktestEngine, BacktestConfig
from strategies.ranking_strategy import RankingStrategy
import logging
import time

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

def run_backtest_with_correct_config(config_path: str):
    """
    Run a backtest using CORRECT BacktestConfig instantiation
    (This is the pattern used in run_backtest.py)
    """

    # Load configuration
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    print(f"\n{'='*70}")
    print(f"TEST BACKTEST: {config['name']}")
    print(f"{'='*70}\n")

    print(f"Rebalance config from YAML:")
    print(f"  frequency: {config['rebalance']['frequency']}")
    print(f"  time: {config['rebalance'].get('time', '00:00')}")
    print(f"  day: {config['rebalance'].get('day', 'N/A')}\n")

    # Initialize components
    data_loader = DataLoader('/home/yash.gupta/research/tardis_datasets/data/snowflake')

    universe_selector = UniverseSelector(
        top_n=config['universe']['top_n'],
        volume_lookback_days=config['universe']['volume_lookback_days'],
        min_data_coverage=config['universe']['min_data_coverage']
    )

    strategy = RankingStrategy(config)

    # Extract pre-calculated universe settings
    universe_config = config['universe']
    use_precalculated = universe_config.get('use_precalculated', False)
    universe_file_path = None
    if use_precalculated:
        universe_file_path = universe_config.get('file_path')

    # THIS IS THE FIX: Extract rebalance settings from YAML config
    backtest_config = BacktestConfig(
        start_date=datetime(2022, 1, 1),
        end_date=datetime(2022, 2, 28),  # 2-month test period
        initial_capital=1_000_000,
        transaction_cost_bps=config['execution']['transaction_cost_bps'],
        # ✓ CRITICAL: Read rebalance settings from YAML config
        rebalance_frequency=config['rebalance']['frequency'],
        rebalance_day=config['rebalance'].get('day', 'monday'),
        rebalance_time=config['rebalance'].get('time', '00:00'),
        use_precalculated_universe=use_precalculated,
        universe_file_path=universe_file_path,
        debug_mode=True  # Enable debug output for verification
    )

    print(f"BacktestConfig settings (CORRECTLY READ from YAML):")
    print(f"  rebalance_frequency: {backtest_config.rebalance_frequency}")
    print(f"  rebalance_day: {backtest_config.rebalance_day}")
    print(f"  rebalance_time: {backtest_config.rebalance_time}\n")

    # Run backtest
    try:
        engine = BacktestEngine(
            data_loader=data_loader,
            universe_selector=universe_selector,
            strategy=strategy,
            config=backtest_config,
            strategy_config=config
        )

        print("Running backtest...")
        start_time = time.time()
        results = engine.run()
        elapsed = time.time() - start_time

        # Display results
        print(f"\n{'='*70}")
        print(f"RESULTS (2-month period: 2022-01-01 to 2022-02-28)")
        print(f"{'='*70}")
        print(f"Total time: {elapsed:.1f}s")
        print(f"Total trades: {results.trades.shape[0]}")
        print(f"Portfolio snapshots: {results.portfolio_timeseries.shape[0]}")
        print(f"Final P&L: ${results.portfolio_timeseries['pnl_dollar'][-1]:,.2f}"
              if 'pnl_dollar' in results.portfolio_timeseries.columns
              else f"Final P&L: {results.metrics.get('total_return', 0):.2f}%")
        print(f"Sharpe Ratio: {results.metrics.get('sharpe_ratio', 0):.2f}")

        # Show rebalance frequency impact
        print(f"\n{'-'*70}")
        print("REBALANCE FREQUENCY VERIFICATION")
        print(f"{'-'*70}")

        # Count unique dates in debug output
        if Path('validation/04_target_positions_history.csv').exists():
            import polars as pl
            positions = pl.read_csv('validation/04_target_positions_history.csv')
            unique_dates = len(positions.unique(subset=['timestamp']))
            print(f"Number of rebalance dates: {unique_dates}")
            print(f"Expected for DAILY rebalance: ~59 (all trading days)")
            print(f"Expected for WEEKLY rebalance: ~8 (Mondays only)")

            if unique_dates > 20:
                print(f"\n✓ CORRECT: Daily rebalancing is working! ({unique_dates} rebalances)")
            else:
                print(f"\n✗ ISSUE: Only {unique_dates} rebalances (expected ~59 for daily)")

        # Save results
        output_dir = Path(f"results/{config['name']}_fixed")
        output_dir.mkdir(parents=True, exist_ok=True)

        if not results.trades.is_empty():
            results.trades.write_csv(output_dir / 'trades.csv')

        results.portfolio_timeseries.write_csv(output_dir / 'portfolio_5min.csv')
        results.daily_timeseries.write_csv(output_dir / 'portfolio_daily.csv')

        print(f"\nResults saved to: {output_dir}")

        return results

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return None

if __name__ == "__main__":
    # Test the momentum_1w_daily config which should have DAILY rebalancing
    config_path = 'configs/momentum_1w_daily.yaml'

    if not Path(config_path).exists():
        print(f"Error: {config_path} not found")
        sys.exit(1)

    results = run_backtest_with_correct_config(config_path)

    # Clean up debug files from working directory
    print("\nCleaning up debug files...")
    import shutil
    if Path('validation').exists():
        # Keep validation files for analysis, don't delete
        print("Debug files kept in validation/ directory for analysis")

    print(f"\n{'='*70}")
    print("TEST COMPLETE")
    print(f"{'='*70}\n")
