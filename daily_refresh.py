"""
Daily refresh — runs all the daily jobs in sequence with logging.

Four jobs in order:
  1. Bulk + block deals scraper   (deals/scraper.py)        — ~1 sec, NSE static archive
  2. Universe OHLCV fetch         (data/fetch_universe.py)  — ~5-15 min, ~500 tickers
  3. Quarantine sweep              (quarantine/run_sweep.py) — ~3-5 min across 500 parquets
  4. Publish HTML to GitHub Pages (reports/generate_html_report + git push)
       Updates https://adityajain1987.github.io/breakout-lab-share/ at the same URL.
       Skipped automatically if --no-publish or if the share repo isn't initialised.

Each job logs its outcome. On failure, subsequent jobs still attempt — partial refresh
beats total skip. Output: `logs/refresh_{YYYY-MM-DD}.log`.

Usage:
  cd ~/Desktop/Claude/breakout-lab
  .venv/bin/python daily_refresh.py                # full: deals + OHLCV + quarantine + publish
  .venv/bin/python daily_refresh.py --quick        # deals + quarantine + publish (no OHLCV)
  .venv/bin/python daily_refresh.py --no-publish   # skip the GitHub Pages step

To run automatically every NSE close (4 PM IST = 10:30 UTC), add a cron entry
(macOS launchd via setup_daily_refresh.sh, or Linux cron):
  30 10 * * 1-5 cd /path/to/breakout-lab && .venv/bin/python daily_refresh.py

When this runs daily, neither you nor Amit needs to touch anything — Amit just
keeps the bookmark to the public URL.
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"


def log(msg: str, file_handle) -> None:
    """Write to both stdout and the log file with timestamp."""
    ts = datetime.utcnow().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    file_handle.write(line + "\n")
    file_handle.flush()


def run_step(name: str, func, log_fh) -> dict:
    """Run a refresh step, capturing outcome + duration. Always continues to next step on failure."""
    log(f"--- {name} ---", log_fh)
    t0 = time.time()
    try:
        result = func()
        elapsed = time.time() - t0
        log(f"✓ {name} OK in {elapsed:.1f}s", log_fh)
        if isinstance(result, dict):
            for k, v in result.items():
                log(f"   {k}: {v}", log_fh)
        return {"name": name, "ok": True, "elapsed_s": elapsed, "result": result}
    except Exception as e:
        elapsed = time.time() - t0
        log(f"✗ {name} FAILED in {elapsed:.1f}s — {type(e).__name__}: {e}", log_fh)
        log(f"   traceback:\n{traceback.format_exc()}", log_fh)
        return {"name": name, "ok": False, "elapsed_s": elapsed, "error": str(e)}


def step_deals_scraper() -> dict:
    """Pull today's bulk + block deals from NSE static archive."""
    from deals.scraper import fetch_and_store
    return fetch_and_store()


def step_ohlcv_fetch() -> dict:
    """Fetch missing OHLCV parquets. Resumable — skips already-cached tickers."""
    from data.fetch_universe import OHLCV_DIR, UNIVERSE_CSV, fetch_one
    import pandas as pd

    universe = pd.read_csv(UNIVERSE_CSV)
    fetched = skipped = failed = 0
    for _, row in universe.iterrows():
        sym, yt = row["SYMBOL"], row["YF_TICKER"]
        path = OHLCV_DIR / f"{sym}.parquet"
        if path.exists():
            # Check if it's stale (last row > 1 trading day old)
            try:
                df = pd.read_parquet(path, columns=["close"])
                last_date = df.index[-1]
                staleness_days = (pd.Timestamp.today() - last_date).days
                if staleness_days < 3:
                    skipped += 1
                    continue
            except Exception:
                pass  # corrupt — re-fetch
        df = fetch_one(yt)
        if df is not None and len(df) > 0:
            df.to_parquet(path, compression="snappy")
            fetched += 1
        else:
            failed += 1
    return {"fetched": fetched, "kept_fresh": skipped, "failed": failed}


def step_quarantine_sweep() -> dict:
    """Re-run quarantine checks across all parquets."""
    from quarantine.run_sweep import sweep_per_ticker_checks, sweep_date_level_checks, DEFAULT_DB
    from quarantine.store import init_db
    init_db(DEFAULT_DB)
    s1 = sweep_per_ticker_checks(DEFAULT_DB, ROOT / "data" / "ohlcv")
    s2 = sweep_date_level_checks(DEFAULT_DB, ROOT / "data" / "ohlcv")
    return {**s1, "fno_expiry_emitted": s2.get("n_emitted", 0), "fno_expiry_inserted": s2["n_inserted"]}


def step_decade_breakouts_scan() -> dict:
    """
    Scan Nifty 500 for stocks approaching a >10y-old untouched high. Persists the
    full eligible list to data/decade_breakouts_latest.parquet so the Streamlit page
    has a fast path AND so a failure here is visible in the daily summary.

    Two passes:
      - 2% proximity (the user's default alert window)
      - 10% proximity (wider — populates the watchlist when 2% is empty)
    """
    from analytics.scan_decade_breakouts import scan_decade_breakouts

    # Use the latest bar in NSEI as the asof — same convention as the share page.
    import pandas as pd
    nsei = ROOT / "data" / "ohlcv" / "_NSEI.parquet"
    asof = pd.read_parquet(nsei).index[-1]

    out_path = ROOT / "data" / "decade_breakouts_latest.parquet"
    summary: dict = {"asof": str(asof.date())}

    for pct in (2.0, 10.0):
        result = scan_decade_breakouts(
            asof_date=asof, proximity_pct=pct,
            lookback_years=10, min_history_years=11, top_n=500,
        )
        summary[f"eligible_at_{int(pct)}pct"] = result.n_eligible
        if pct == 10.0:
            # Persist the wider list — the Streamlit page filters in-memory anyway.
            df = result.df.copy()
            if not df.empty:
                df["asof"] = str(asof.date())
                df.to_parquet(out_path, compression="snappy")
            elif out_path.exists():
                out_path.unlink()   # nothing to show today

    summary["n_scanned"] = result.n_scanned
    summary["scan_duration_seconds"] = round(result.scan_duration_seconds, 1)
    return summary


def step_publish_to_github_pages() -> dict:
    """Generate fresh HTML report + push to GitHub Pages so the public URL updates.

    URL: https://adityajain1987.github.io/breakout-lab-share/

    Defensive: pulls --rebase first to avoid the "cannot lock ref" race that hits
    when local + remote drift apart (e.g. the user pushed manually between runs).
    Raises on push failure so the step reports ok=False.
    """
    import subprocess
    import shutil
    from reports.generate_html_report import generate, latest_trading_day

    asof = latest_trading_day()
    out_path = generate(asof, top_n=15, n_features=5)

    share_dir = Path.home() / "Desktop" / "Claude" / "breakout-lab-share"
    if not (share_dir / ".git").exists():
        return {"published": False, "reason": f"share repo not initialised at {share_dir}"}

    # Defensive: pull any remote changes first so push doesn't get rejected
    pull = subprocess.run(["git", "pull", "--rebase", "--autostash"],
                          cwd=share_dir, capture_output=True, text=True)
    pulled = "pulled" if "Successfully" in pull.stdout or "up to date" in pull.stdout.lower() else "no-op"

    # Copy as index.html (the front page) and as a date-stamped archive
    date_tag = out_path.stem.replace("breakout_lab_", "")
    shutil.copy(out_path, share_dir / "index.html")
    shutil.copy(out_path, share_dir / f"{date_tag}.html")

    subprocess.run(["git", "add", "index.html", f"{date_tag}.html"],
                   cwd=share_dir, check=True, capture_output=True)
    diff_result = subprocess.run(["git", "diff", "--cached", "--quiet"],
                                 cwd=share_dir, capture_output=True)
    if diff_result.returncode == 0:
        return {"published": True, "skipped": "no changes since last run", "pull": pulled}

    subprocess.run(["git", "commit", "-m", f"Update report for {date_tag}"],
                   cwd=share_dir, check=True, capture_output=True)
    push_result = subprocess.run(["git", "push"], cwd=share_dir, capture_output=True, text=True)
    if push_result.returncode != 0:
        # Raise so the step is marked failed in the summary (currently it logs
        # ok=True even with a push error; raising fixes that.)
        raise RuntimeError(f"git push failed: {push_result.stderr.strip()[:300]}")

    return {
        "published": True,
        "url": "https://adityajain1987.github.io/breakout-lab-share/",
        "asof": str(asof.date()),
        "report": str(out_path.name),
        "pull": pulled,
    }


def step_bhavcopy_refresh() -> dict:
    """Pull recent Bhavcopy days + rebuild parquets for any new days.

    Bhavcopy is the official NSE same-day data; yfinance India lags 1-3 days.
    This step keeps the Bhavcopy parquet store fresh so scan_universe always has
    today's bar via the fallback path even when yfinance is stale.

    Window: last 7 calendar days (resumable, skips already-downloaded days).
    """
    from datetime import date, timedelta
    from data.fetch_bhavcopy import fetch_range, RAW_DIR
    from data.build_bhavcopy_parquets import build

    end = date.today()
    start = end - timedelta(days=7)
    fetch_summary = fetch_range(start, end, raw_dir=RAW_DIR)

    # Only rebuild parquets if any new files were actually fetched
    if fetch_summary.get("fetched", 0) > 0:
        build_summary = build({"EQ"})
        return {**fetch_summary, "rebuilt_parquets": build_summary["n_tickers"]}
    return {**fetch_summary, "rebuilt_parquets": 0}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true", help="Skip the slow OHLCV fetch (deals + quarantine + publish only)")
    ap.add_argument("--no-publish", action="store_true", help="Skip the GitHub Pages publish step")
    args = ap.parse_args()

    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"refresh_{date.today().isoformat()}.log"

    sys.path.insert(0, str(ROOT))

    with log_path.open("a") as fh:
        log("=" * 60, fh)
        log(f"Daily refresh started at {datetime.utcnow().isoformat()}Z", fh)
        mode_str = "QUICK" if args.quick else "FULL"
        if args.no_publish:
            mode_str += " (no publish)"
        log(f"Mode: {mode_str}", fh)
        log("=" * 60, fh)

        results = []
        results.append(run_step("Deals scraper", step_deals_scraper, fh))
        if not args.quick:
            results.append(run_step("OHLCV fetch (yfinance)", step_ohlcv_fetch, fh))
        results.append(run_step("Bhavcopy refresh (NSE official)", step_bhavcopy_refresh, fh))
        results.append(run_step("Quarantine sweep", step_quarantine_sweep, fh))
        results.append(run_step("Decade-breakouts scan", step_decade_breakouts_scan, fh))
        if not args.no_publish:
            results.append(run_step("Publish to GitHub Pages", step_publish_to_github_pages, fh))

        log("", fh)
        log("=" * 60, fh)
        log("Summary:", fh)
        for r in results:
            status = "✓" if r["ok"] else "✗"
            log(f"  {status}  {r['name']:30s}  {r['elapsed_s']:6.1f}s", fh)
        n_failed = sum(1 for r in results if not r["ok"])
        log(f"\n{n_failed} of {len(results)} steps failed.", fh)
        if not args.no_publish and any(r["name"] == "Publish to GitHub Pages" and r["ok"] for r in results):
            log("\n🌐 Live: https://adityajain1987.github.io/breakout-lab-share/", fh)
        log("=" * 60, fh)

    sys.exit(0 if n_failed == 0 else 1)


if __name__ == "__main__":
    main()
