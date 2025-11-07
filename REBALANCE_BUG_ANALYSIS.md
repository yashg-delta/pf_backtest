# Rebalancing Frequency Bug Analysis & Fix

## Problem Statement

When you asked: **"If we are having a weekly look back and then rebalancing daily, how are there so few trades? Should we be selecting top 10 coins every day?"**

You identified a critical issue: The config specifies **DAILY** rebalancing but only **9 rebalances** were occurring over 59 trading days (weekly pattern on Mondays only).

---

## Root Cause Analysis

### The Issue: BacktestConfig Not Reading YAML Rebalance Settings

The `BacktestConfig` class in `framework/engine/backtest.py` has default values:

```python
@dataclass
class BacktestConfig:
    ...
    rebalance_frequency: str = 'weekly'      # ← DEFAULT is 'weekly'
    rebalance_day: str = 'monday'
    rebalance_time: str = '00:00'
    ...
```

When `BacktestConfig` is instantiated **without** passing the rebalance parameters from the YAML config, it defaults to `weekly` rebalancing.

### Affected Code Locations

**❌ INCORRECT** (Missing rebalance settings):
```python
backtest_config = BacktestConfig(
    start_date=datetime(2022, 1, 1),
    end_date=datetime(2025, 9, 30),
    use_precalculated_universe=universe_config.get('use_precalculated', False),
    universe_file_path=universe_config.get('file_path')
    # ← MISSING: rebalance_frequency, rebalance_day, rebalance_time
)
```

**✓ CORRECT** (Like `run_backtest.py` lines 48-58):
```python
backtest_config = BacktestConfig(
    start_date=datetime(2022, 1, 1),
    end_date=datetime(2025, 9, 30),
    initial_capital=1_000_000,
    transaction_cost_bps=config['execution']['transaction_cost_bps'],
    # ✓ EXTRACT FROM YAML CONFIG
    rebalance_frequency=config['rebalance']['frequency'],
    rebalance_day=config['rebalance'].get('day', 'monday'),
    rebalance_time=config['rebalance'].get('time', '00:00'),
    use_precalculated_universe=use_precalculated,
    universe_file_path=universe_file_path
)
```

---

## Diagnostic Results

### Test: `scripts/diagnose_rebalance_issue.py`

**Config**: `momentum_1w_daily.yaml`
```yaml
rebalance:
  frequency: "daily"
  time: "00:00"
```

**Results**:

| Scenario | Rebalances in Jan-Feb 2022 | Dates |
|----------|---|---|
| **CORRECT** (reads YAML) | 59 rebalances | Every day (2022-01-01 through 2022-02-28) |
| **WRONG** (uses defaults) | 9 rebalances | Only Mondays (2022-01-03, 01-10, 01-17, ..., 02-28) |

### Observed Behavior

With **incorrect** config (weekly default):
- Only 9 rebalance dates
- Only 3 signals generated (sparse opportunit for trading)
- Mondays only pattern matches weekly frequency

With **correct** config (reading YAML):
- 59 rebalance dates
- Signals evaluated every single trading day
- Matches specified "daily" rebalancing

---

## Impact on Results

### Example: momentum_1w_daily Config

**With WEEKLY rebalancing (current bug)**:
```
Rebalance dates: 9 (only Mondays)
Signals generated: 3 (FTMUSDT, ATOMUSDT, IOTXUSDT)
Trades: Limited to weekly rebalance windows
```

**With DAILY rebalancing (after fix)**:
```
Rebalance dates: 59 (every trading day)
Signals generated: More frequent opportunities
Trades: Daily chance to adjust positions
```

The test with correct config shows signals on:
- Jan 19 (SOLBUSD)
- Jan 24 (FTMUSDT)
- Feb 14-20 (Multiple positions)
- And daily evaluation for exits

---

## Files Showing Issue

### ✓ Already Correct: `run_backtest.py` (lines 48-58)
This file is the **reference implementation** and correctly reads rebalance settings from YAML.

### ✗ Examples with Bug:
Background test scripts that directly create BacktestConfig without extracting rebalance settings.

---

## Fix Checklist

When instantiating `BacktestConfig`:

- [ ] Load YAML config: `with open(config_path) as f: config = yaml.safe_load(f)`
- [ ] Extract rebalance settings from YAML:
  ```python
  rebalance_frequency=config['rebalance']['frequency'],
  rebalance_day=config['rebalance'].get('day', 'monday'),
  rebalance_time=config['rebalance'].get('time', '00:00'),
  ```
- [ ] Pass these to BacktestConfig
- [ ] Verify with diagnostic script

---

## Test Files Created

### 1. `scripts/diagnose_rebalance_issue.py`
**Purpose**: Identify rebalance configuration mismatches
**Usage**: `python3 scripts/diagnose_rebalance_issue.py`
**Output**: Detailed comparison showing expected vs actual rebalance dates

### 2. `scripts/test_daily_rebalance_fixed.py`
**Purpose**: Demonstrate correct daily rebalancing behavior
**Usage**: `python3 scripts/test_daily_rebalance_fixed.py`
**Output**: 2-month backtest with correct daily rebalancing configuration
**Results Saved**: `results/momentum_1w_daily_fixed/`

---

## Validation Results

Running `test_daily_rebalance_fixed.py` shows:

```
Rebalance config from YAML:
  frequency: daily
  time: 00:00

BacktestConfig settings (CORRECTLY READ from YAML):
  rebalance_frequency: daily
  rebalance_day: monday
  rebalance_time: 00:00

Rebalancing at 2022-01-01 00:00:00  ← Daily rebalancing starting!
Rebalancing at 2022-01-02 00:00:00
Rebalancing at 2022-01-03 00:00:00
...
Rebalancing at 2022-02-28 00:00:00

Generated signals on Jan 19, Jan 24, Feb 13-20 (multiple daily evaluations)
```

This proves daily rebalancing works correctly when config settings are properly extracted.

---

## Summary

**Root Cause**: Missing rebalance_frequency/day/time parameters when creating BacktestConfig

**Impact**: Strategies configured for daily rebalancing defaulted to weekly, limiting trading opportunities

**Fix**: Always extract and pass rebalance settings from YAML config to BacktestConfig

**Verification**: Diagnostic script confirms 59 daily rebalances (vs 9 weekly) when fix is applied

**Reference**: `run_backtest.py` contains the correct pattern for all future code

---

## Recommended Actions

1. **Verify `run_backtest.py` is being used** as the primary entry point (it's already correct)
2. **Use `scripts/diagnose_rebalance_issue.py`** to check any new test scripts
3. **Always extract rebalance settings** when instantiating BacktestConfig
4. **Test with provided scripts** to validate daily rebalancing is working

