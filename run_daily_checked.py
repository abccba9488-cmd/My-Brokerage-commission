"""Wrapper around run_daily.py that verifies the report is complete and
retries on failure. This is what the scheduled task actually calls (see
README's 排程 section) — run_daily.py itself stays a single-shot script.

"Complete" means today's reports/YYYY-MM-DD.csv exists and has a row for
every stock in config/stocks.yaml. run_daily.py already tolerates a single
stock failing (it logs and continues), so a transient FinMind hiccup on one
ticker won't crash the whole run — but it *will* produce a report with a
row missing, which this catches and retries.
"""
from __future__ import annotations

import csv
import logging
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).parent
REPORTS_DIR = ROOT / "reports"
CONFIG_PATH = ROOT / "config" / "stocks.yaml"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
RUN_DAILY = ROOT / "run_daily.py"

MAX_ATTEMPTS = 3
RETRY_DELAY_SECONDS = 180  # 3 minutes — gives a transient FinMind outage room to clear
ATTEMPT_TIMEOUT_SECONDS = 480  # 8 minutes — bounds a hung/unresponsive run_daily.py so
# a stuck first attempt can't burn the whole scheduled-task time budget and skip retries.
# 3 * 480s + 2 * 180s = 27.5min, comfortably under the task's 45min execution limit.

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.FileHandler(REPORTS_DIR / "run_daily_checked.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


def expected_stock_codes() -> set[str]:
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return {s["code"] for s in config["stocks"]}


def report_is_complete(run_date: str) -> tuple[bool, set[str]]:
    csv_path = REPORTS_DIR / f"{run_date}.csv"
    if not csv_path.exists():
        return False, expected_stock_codes()
    with open(csv_path, encoding="utf-8-sig") as f:
        present = {row["stock_id"] for row in csv.DictReader(f)}
    missing = expected_stock_codes() - present
    return not missing, missing


def main() -> None:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    run_date = datetime.today().strftime("%Y-%m-%d")
    fail_marker = REPORTS_DIR / f"FAILED_{run_date}.txt"

    for attempt in range(1, MAX_ATTEMPTS + 1):
        logger.info("Attempt %d/%d: running run_daily.py", attempt, MAX_ATTEMPTS)
        try:
            subprocess.run(
                [str(PYTHON), str(RUN_DAILY)], cwd=str(ROOT), check=False, timeout=ATTEMPT_TIMEOUT_SECONDS
            )
        except subprocess.TimeoutExpired:
            logger.warning("Attempt %d timed out after %ds — treating as failed", attempt, ATTEMPT_TIMEOUT_SECONDS)

        complete, missing = report_is_complete(run_date)
        if complete:
            logger.info("Report complete for %s", run_date)
            fail_marker.unlink(missing_ok=True)
            return

        logger.warning("Report incomplete after attempt %d — missing: %s", attempt, sorted(missing))
        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_DELAY_SECONDS)

    fail_marker.write_text(
        f"run_daily.py failed to produce a complete report after {MAX_ATTEMPTS} attempts.\n"
        f"Missing stocks: {sorted(missing)}\n"
        f"Check reports/run_daily_checked.log for details.\n",
        encoding="utf-8",
    )
    logger.error("Giving up after %d attempts. Wrote %s", MAX_ATTEMPTS, fail_marker)
    sys.exit(1)


if __name__ == "__main__":
    main()
