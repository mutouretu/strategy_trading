#!/usr/bin/env bash
set -euo pipefail

workspace_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -n "${STRATEGY_TRADING_PYTHON:-}" ]]; then
  platform_python="${STRATEGY_TRADING_PYTHON}"
elif [[ -x "${workspace_root}/.venv/bin/python" ]]; then
  platform_python="${workspace_root}/.venv/bin/python"
elif [[ -x "${workspace_root}/grid_trading/.venv/bin/python" ]]; then
  platform_python="${workspace_root}/grid_trading/.venv/bin/python"
else
  platform_python="python3"
fi

if ! "${platform_python}" -c 'import pyarrow' >/dev/null 2>&1; then
  echo "test Python lacks PyArrow: ${platform_python}" >&2
  echo "set STRATEGY_TRADING_PYTHON or create a local .venv with project dependencies" >&2
  exit 2
fi

simulator_pythonpath="${workspace_root}/market_simulator:${workspace_root}/market_simulator/packages/market_protocol/src:${workspace_root}/market_simulator/packages/market_simulator/src:${workspace_root}/market_simulator/packages/simulation_runtime/src:${workspace_root}/market_simulator/packages/experiment_system/src:${workspace_root}/market_simulator/packages/metric_system/src"

echo "[1/3] market_simulator"
PYTHONPATH="${simulator_pythonpath}" \
  "${platform_python}" -m unittest discover \
  -s "${workspace_root}/market_simulator/tests" -v

echo "[2/3] strategies_system"
PYTHONPATH="${workspace_root}/strategies_system/src" \
  "${platform_python}" -m unittest discover \
  -s "${workspace_root}/strategies_system/tests" -v

grid_python="${workspace_root}/grid_trading/.venv/bin/python"
if [[ ! -x "${grid_python}" ]]; then
  grid_python="${platform_python}"
fi

echo "[3/3] grid_trading"
(
  cd "${workspace_root}/grid_trading"
  "${grid_python}" -m unittest discover -s tests -v
)
