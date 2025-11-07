#!/usr/bin/env python3
"""
Diagnostic script to identify rebalance frequency configuration issues
"""

import yaml
import sys
import os
from datetime import datetime
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from framework.utils.calendar import TradingCalendar
from framework.engine.backtest import BacktestConfig

def test_config(config_path: str, debug: bool = True):
    """Test a configuration file for rebalance issues"""

    print(f"\n{'='*70}")
    print(f"DIAGNOSING: {Path(config_path).name}")
    print(f"{'='*70}\n")

    # Load YAML config
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    print(f"Strategy Name: {config['name']}")
    print(f"\nYAML Rebalance Config:")
    print(f"  frequency: {config['rebalance']['frequency']}")
    print(f"  time: {config['rebalance'].get('time', '00:00')}")
    print(f"  day: {config['rebalance'].get('day', 'N/A (not specified)')}")

    # TEST 1: Create BacktestConfig the RIGHT way (like run_backtest.py does)
    print(f"\n{'-'*70}")
    print("TEST 1: Correct BacktestConfig (with rebalance settings)")
    print(f"{'-'*70}")

    backtest_config_correct = BacktestConfig(
        start_date=datetime(2022, 1, 1),
        end_date=datetime(2022, 2, 28),
        rebalance_frequency=config['rebalance']['frequency'],
        rebalance_day=config['rebalance'].get('day', 'monday'),
        rebalance_time=config['rebalance'].get('time', '00:00'),
    )

    calendar_correct = TradingCalendar(
        backtest_config_correct.rebalance_frequency,
        backtest_config_correct.rebalance_day,
        backtest_config_correct.rebalance_time
    )

    print(f"BacktestConfig frequency: {backtest_config_correct.rebalance_frequency}")
    print(f"BacktestConfig day: {backtest_config_correct.rebalance_day}")
    print(f"BacktestConfig time: {backtest_config_correct.rebalance_time}")

    # Get rebalance dates
    rebalance_dates_correct = calendar_correct.get_rebalance_dates(
        datetime(2022, 1, 1),
        datetime(2022, 2, 28)
    )
    print(f"\nRebalance dates in Jan-Feb 2022: {len(rebalance_dates_correct)} dates")
    for dt in rebalance_dates_correct[:10]:  # Show first 10
        print(f"  {dt.strftime('%Y-%m-%d %A')}")
    if len(rebalance_dates_correct) > 10:
        print(f"  ... and {len(rebalance_dates_correct) - 10} more")

    # TEST 2: Create BacktestConfig the WRONG way (missing rebalance settings)
    print(f"\n{'-'*70}")
    print("TEST 2: Incorrect BacktestConfig (MISSING rebalance settings)")
    print(f"{'-'*70}")

    backtest_config_wrong = BacktestConfig(
        start_date=datetime(2022, 1, 1),
        end_date=datetime(2022, 2, 28),
        # NOTE: Missing rebalance_frequency, rebalance_day, rebalance_time!
    )

    calendar_wrong = TradingCalendar(
        backtest_config_wrong.rebalance_frequency,
        backtest_config_wrong.rebalance_day,
        backtest_config_wrong.rebalance_time
    )

    print(f"BacktestConfig frequency (DEFAULTS): {backtest_config_wrong.rebalance_frequency}")
    print(f"BacktestConfig day (DEFAULTS): {backtest_config_wrong.rebalance_day}")
    print(f"BacktestConfig time (DEFAULTS): {backtest_config_wrong.rebalance_time}")

    # Get rebalance dates
    rebalance_dates_wrong = calendar_wrong.get_rebalance_dates(
        datetime(2022, 1, 1),
        datetime(2022, 2, 28)
    )
    print(f"\nRebalance dates in Jan-Feb 2022: {len(rebalance_dates_wrong)} dates")
    for dt in rebalance_dates_wrong:
        print(f"  {dt.strftime('%Y-%m-%d %A')}")

    # SUMMARY
    print(f"\n{'-'*70}")
    print("COMPARISON")
    print(f"{'-'*70}")
    print(f"YAML specifies:   {config['rebalance']['frequency'].upper()} rebalancing")
    print(f"CORRECT config:   {len(rebalance_dates_correct):3d} rebalances (expected)")
    print(f"WRONG config:     {len(rebalance_dates_wrong):3d} rebalances (if using defaults)")

    if len(rebalance_dates_correct) != len(rebalance_dates_wrong):
        print(f"\n⚠️  MISMATCH DETECTED! Config defaults don't match YAML specification!")
        print(f"Issue: rebalance_frequency not being read from YAML config")
        return False
    else:
        print(f"\n✓ Frequencies match (no issue detected)")
        return True

if __name__ == "__main__":
    # Test both configs
    configs_to_test = [
        'configs/momentum_1w_daily.yaml',
        'configs/momentum_1w_daily_btc20.yaml',
    ]

    all_ok = True
    for config_path in configs_to_test:
        if Path(config_path).exists():
            ok = test_config(config_path)
            all_ok = all_ok and ok
        else:
            print(f"\nWarning: {config_path} not found")

    print(f"\n{'='*70}")
    if all_ok:
        print("✓ All configs appear to be correct")
    else:
        print("✗ Configuration issues detected!")
    print(f"{'='*70}\n")
