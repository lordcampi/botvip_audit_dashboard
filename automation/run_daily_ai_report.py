from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.telegram_delivery import send_report_zip_if_enabled


def main() -> int:
    parser = argparse.ArgumentParser(description="Run BotVIP Daily AI Reporter batch safely.")
    parser.add_argument("--window", default="daily")
    parser.add_argument("--output", default="reports")
    parser.add_argument("--max-ai-chars", type=int, default=95000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = ROOT
    logs = root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "daily_ai_report_runner.log"

    cmd = [
        sys.executable,
        str(root / "daily_ai_report.py"),
        "--window", args.window,
        "--output", args.output,
        "--max-ai-chars", str(args.max_ai_chars),
    ]
    if args.dry_run:
        cmd.append("--dry-run")

    started = datetime.now(timezone.utc).isoformat()
    telegram_sent = False
    telegram_error = None

    with log_path.open("a", encoding="utf-8", newline="\n") as log:
        log.write("\n=== daily_ai_report start " + started + " ===\n")
        log.write("cmd: " + " ".join(cmd) + "\n")
        proc = subprocess.run(cmd, cwd=str(root), text=True, capture_output=True)
        if proc.stdout:
            log.write(proc.stdout + "\n")
        if proc.stderr:
            log.write("STDERR:\n" + proc.stderr + "\n")
        if proc.returncode == 0 and not args.dry_run:
            try:
                telegram_sent = send_report_zip_if_enabled(root, proc.stdout)
                log.write("telegram_sent: " + str(telegram_sent) + "\n")
            except Exception as exc:
                telegram_error = str(exc)
                log.write("telegram_error: " + telegram_error + "\n")
        log.write("returncode: " + str(proc.returncode) + "\n")
        log.write("=== daily_ai_report end " + datetime.now(timezone.utc).isoformat() + " ===\n")

    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    if telegram_sent:
        print("Telegram delivery OK")
    if telegram_error:
        print("Telegram delivery WARNING: " + telegram_error, file=sys.stderr)
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())


