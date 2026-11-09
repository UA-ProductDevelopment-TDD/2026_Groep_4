#!/usr/bin/env bash
# Runs the automated checks: Python tests for the simulator and API, and a syntax check of every web file.
cd "$(dirname "$0")/.." || exit 1
if [ ! -x .venv/bin/python ]; then echo "Run the setup launcher for your system first."; exit 1; fi
echo "=== Python tests ==="
.venv/bin/python -m unittest discover -s app/tests -t app -v || { echo "Some checks FAILED."; exit 1; }
if command -v node >/dev/null 2>&1; then
  echo "=== Web syntax check ==="
  find app/web -name '*.js' -print0 | xargs -0 -n1 node --check || { echo "Some checks FAILED."; exit 1; }
else
  echo "(Node.js not found: skipping the web syntax check)"
fi
echo "All checks passed."
