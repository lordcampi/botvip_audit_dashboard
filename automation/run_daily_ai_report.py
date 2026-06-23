from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run BotVIP Daily AI Reporter batch safely.")
    parser.add_argument("--window", default="daily")
    parser.add_argument("--output", default="reports")
    parser.add_argument("--max-ai-chars", type=int, default=120000)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
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
    with log_path.open("a", encoding="utf-8", newline="\n") as log:
        log.write("\n=== daily_ai_report start " + started + " ===\n")
        log.write("cmd: " + " ".join(cmd) + "\n")
        proc = subprocess.run(cmd, cwd=str(root), text=True, capture_output=True)
        if proc.stdout:
            log.write(proc.stdout + "\n")
        if proc.stderr:
            log.write("STDERR:\n" + proc.stderr + "\n")
        log.write("returncode: " + str(proc.returncode) + "\n")
        log.write("=== daily_ai_report end " + datetime.now(timezone.utc).isoformat() + " ===\n")

    if proc.stdout:
        print(proc.stdout)
    if proc.stderr:
        print(proc.stderr, file=sys.stderr)
    return int(proc.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
