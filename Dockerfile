FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

RUN python -m py_compile daily_ai_report.py automation/run_daily_ai_report.py

CMD ["python", "automation/run_daily_ai_report.py", "--window", "daily", "--output", "reports", "--max-ai-chars", "120000"]
