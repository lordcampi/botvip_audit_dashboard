# BotVIP Daily AI Reporter automation

This folder contains automation helpers for the independent read-only Daily AI Reporter.

Important safety properties:
- Reads SQLite only through the read-only connection layer.
- Does not modify BotVIP strategy or thresholds.
- Does not touch the BotVIP principal project.
- Generates reports under `reports/YYYY-MM-DD/`.

## Manual run

Windows PowerShell:

```powershell
python .\automation\run_daily_ai_report.py --window daily --output reports --max-ai-chars 95000
```

Linux:

```bash
python3 automation/run_daily_ai_report.py --window daily --output reports --max-ai-chars 95000
```

## Dry-run validation

```powershell
python .\automation\run_daily_ai_report.py --window daily --output reports --dry-run
```

## Windows Task Scheduler example

Program:

```text
powershell.exe
```

Arguments:

```text
-NoProfile -ExecutionPolicy Bypass -File "D:\Dashboard_Bot\botvip_audit_dashboard\automation\run_daily_ai_report.ps1"
```

Trigger:

```text
Daily at 5:00 AM Colombia local time
```

## Linux cron example

Use `daily_ai_report.cron.example` and adjust project path.

Colombia 5:00 AM = 10:00 UTC.

## Linux systemd example

Copy examples after editing `/opt/botvip_audit_dashboard` if needed:

```bash
sudo cp automation/botvip-daily-ai-reporter.service /etc/systemd/system/
sudo cp automation/botvip-daily-ai-reporter.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now botvip-daily-ai-reporter.timer
systemctl list-timers | grep botvip-daily-ai-reporter
```

