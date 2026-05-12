#!/bin/bash
# setup.sh — one-shot installer for breakout-lab.
#
# Run this once after extracting the project folder on a new Mac/Linux machine.
# It will:
#   1. Verify Python 3.11 is installed
#   2. Create a virtual environment at .venv/
#   3. Install all Python dependencies
#   4. Build the universe (Nifty 500 list)
#   5. Fetch OHLCV data for ~500 tickers (5-15 min, resumable)
#   6. Pull today's bulk + block deals
#   7. Run the quarantine sweep
#   8. Schedule the daily refresh job (launchd on Mac, cron-line printed on Linux)
#   9. Optionally open the dashboard
#
# Total runtime: ~10-20 min (mostly the OHLCV fetch).

set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "  Breakout Lab — one-shot setup"
echo "════════════════════════════════════════════════════════════════════"
echo ""

# ---- 1. Verify Python ----
echo "[1/8] Checking Python 3.11..."
if ! command -v python3.11 &> /dev/null; then
  echo "  ❌ python3.11 not found."
  echo ""
  echo "  Install it first:"
  echo "    macOS:  brew install python@3.11"
  echo "    Linux:  apt install python3.11 python3.11-venv  (Debian/Ubuntu)"
  echo "            yum install python3.11                   (RHEL/Fedora)"
  exit 1
fi
echo "  ✅ $(python3.11 --version)"

# ---- 2. Create venv ----
echo ""
echo "[2/8] Creating virtual environment..."
if [ ! -d .venv ]; then
  python3.11 -m venv .venv
  echo "  ✅ Created .venv/"
else
  echo "  ✅ .venv/ already exists"
fi

# ---- 3. Install dependencies ----
echo ""
echo "[3/8] Installing Python dependencies..."
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r requirements.txt
echo "  ✅ All dependencies installed"

# ---- 4. Build universe ----
echo ""
echo "[4/8] Building Nifty 500 universe..."
if [ -f data/universe_1000cr.csv ]; then
  echo "  ✅ Universe already built (data/universe_1000cr.csv exists)"
  echo "     Re-build later with: .venv/bin/python -m data.build_universe"
else
  .venv/bin/python -m data.build_universe
fi

# ---- 5. Fetch OHLCV ----
echo ""
echo "[5/8] Fetching OHLCV data (5-15 min, resumable)..."
echo "       Progress prints below. Safe to interrupt + re-run."
echo ""
.venv/bin/python -m data.fetch_universe

# ---- 6. Build historical universe snapshots ----
echo ""
echo "[6/8] Building monthly historical universe snapshots..."
.venv/bin/python -m data.build_universe_history > /dev/null
echo "  ✅ 76 monthly snapshots in data/universe_history/"

# ---- 7. Initial deals + quarantine ----
echo ""
echo "[7/8] Pulling today's bulk + block deals + running quarantine sweep..."
.venv/bin/python -m deals.scraper > /dev/null && echo "  ✅ Deals scraped"
.venv/bin/python -m quarantine.run_sweep > /dev/null && echo "  ✅ Quarantine swept"

# ---- 8. Schedule daily refresh ----
echo ""
echo "[8/8] Scheduling daily refresh (Mon-Fri 4:30 PM local time)..."
./setup_daily_refresh.sh

# ---- Done ----
echo ""
echo "════════════════════════════════════════════════════════════════════"
echo "  ✅ Setup complete!"
echo "════════════════════════════════════════════════════════════════════"
echo ""
echo "  Open the dashboard:"
echo "    .venv/bin/streamlit run dashboard/app.py"
echo ""
echo "  Daily refresh runs automatically Mon-Fri 4:30 PM."
echo "  Verify the schedule:    ./setup_daily_refresh.sh --status"
echo "  Remove the schedule:    ./setup_daily_refresh.sh --remove"
echo "  Manually run a refresh: .venv/bin/python daily_refresh.py"
echo ""

# Optionally open dashboard
read -p "  Open the dashboard in your browser now? [Y/n] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Nn]$ ]]; then
  echo "  Booting Streamlit at http://localhost:8501/ ..."
  echo "  (Ctrl+C in the terminal to stop the server.)"
  echo ""
  .venv/bin/streamlit run dashboard/app.py
fi
