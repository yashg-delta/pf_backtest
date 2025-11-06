import yaml
from datetime import datetime
from pathlib import Path
import logging

from framework.data.loader import DataLoader
from framework.data.universe import UniverseSelector
from framework.engine.backtest import BacktestEngine, BacktestConfig
from framework.analytics.trades import TradeAnalyzer
from framework.analytics.visualizations import Visualizer
from strategies.ranking_strategy import RankingStrategy

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def run_single_backtest(config_path: str):
    """Run a single backtest from config file"""

    # Load configuration
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)

    print(f"\n{'='*60}")
    print(f"Running Strategy: {config['name']}")
    print(f"{'='*60}")

    # Initialize components
    data_loader = DataLoader('/home/yash.gupta/research/tardis_datasets/data/snowflake')

    universe_selector = UniverseSelector(
        top_n=config['universe']['top_n'],
        volume_lookback_days=config['universe']['volume_lookback_days'],
        min_data_coverage=config['universe']['min_data_coverage']
    )

    strategy = RankingStrategy(config)

    backtest_config = BacktestConfig(
        start_date=datetime(2022, 1, 1),
        end_date=datetime(2025, 9, 30),
        initial_capital=1_000_000,
        transaction_cost_bps=config['execution']['transaction_cost_bps'],
        rebalance_frequency=config['rebalance']['frequency'],
        rebalance_day=config['rebalance'].get('day', 'monday'),
        rebalance_time=config['rebalance'].get('time', '00:00')
    )

    # Run backtest
    engine = BacktestEngine(
        data_loader=data_loader,
        universe_selector=universe_selector,
        strategy=strategy,
        config=backtest_config,
        strategy_config=config
    )

    results = engine.run()

    # Display metrics
    print(f"\n{'-'*40}")
    print("Performance Metrics:")
    print(f"{'-'*40}")
    for metric, value in results.metrics.items():
        if isinstance(value, float):
            if 'return' in metric or 'pct' in metric:
                print(f"{metric:30}: {value:>10.2f}%")
            else:
                print(f"{metric:30}: {value:>10.2f}")

    # Save results
    output_dir = Path(f"results/{config['name']}")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Save trade sheet
    if not results.trades.is_empty():
        results.trades.write_csv(output_dir / 'trades.csv')

    # Save timeseries
    results.portfolio_timeseries.write_csv(output_dir / 'portfolio_5min.csv')
    results.daily_timeseries.write_csv(output_dir / 'portfolio_daily.csv')

    # Generate charts
    Visualizer.plot_equity_curve(
        results.portfolio_timeseries,
        save_path=output_dir / 'equity_curve.png'
    )

    Visualizer.plot_drawdown(
        results.portfolio_timeseries,
        save_path=output_dir / 'drawdown.png'
    )

    # Generate monthly matrix
    monthly_matrix = TradeAnalyzer.create_monthly_matrix(results.daily_timeseries)
    if not monthly_matrix.is_empty():
        monthly_matrix.write_csv(output_dir / 'monthly_returns.csv')
        Visualizer.plot_monthly_returns_heatmap(
            monthly_matrix,
            save_path=output_dir / 'monthly_heatmap.png'
        )

    print(f"\nResults saved to: {output_dir}")

    return results

def run_parameter_sweep():
    """Run multiple backtests with different parameters"""

    configs = [
        'configs/momentum_1w_daily.yaml',
        'configs/momentum_1m_weekly.yaml',
        'configs/momentum_diff_with_filter.yaml'
    ]

    all_results = {}

    for config_path in configs:
        results = run_single_backtest(config_path)
        config_name = Path(config_path).stem
        all_results[config_name] = results

    # Compare results
    print(f"\n{'='*60}")
    print("Strategy Comparison:")
    print(f"{'='*60}")
    print(f"{'Strategy':<30} {'Sharpe':<10} {'Max DD':<10} {'Total Ret':<10}")
    print(f"{'-'*60}")

    for name, results in all_results.items():
        metrics = results.metrics
        print(f"{name:<30} "
              f"{metrics.get('sharpe_ratio', 0):<10.2f} "
              f"{metrics.get('max_drawdown', 0):<10.2f}% "
              f"{metrics.get('total_return', 0):<10.2f}%")

# Add this function to run_backtest.py
def run_momentum_strategies():
    """Run the two momentum strategies"""
    
    configs = [
        'configs/momentum_1m_weekly_btc200.yaml',
        'configs/momentum_1w_daily_btc20.yaml'
    ]
    
    for config_path in configs:
        try:
            run_single_backtest(config_path)
        except Exception as e:
            print(f"Error running {config_path}: {e}")
            import traceback
            traceback.print_exc()

# Update the main block
if __name__ == "__main__":
    run_momentum_strategies()

# if __name__ == "__main__":
#     # Run single backtest
#     run_single_backtest('configs/momentum_1w_daily.yaml')

#     # Or run parameter sweep
#     # run_parameter_sweep()
