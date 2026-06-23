from __future__ import annotations

from typing import Any


def _is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def audit_f4_t11a_semantics(facts: list[dict[str, Any]]) -> dict[str, Any]:
    signals = [row for row in facts if row.get("record_type") == "signal"]
    violations: list[dict[str, Any]] = []

    for row in signals:
        sid = row.get("signal_id")
        primary_tp = _is_true(row.get("primary_tp_hit"))
        real_sl = _is_true(row.get("real_stop_loss_hit"))
        runner_be = _is_true(row.get("runner_breakeven_stop_hit"))
        breakeven = _is_true(row.get("breakeven_stop_hit"))
        status = str(row.get("status") or "")
        exit_reason = str(row.get("exit_reason") or "")
        official_result = row.get("official_result")
        locked = _is_true(row.get("official_result_locked"))

        if primary_tp and real_sl:
            violations.append({
                "signal_id": sid,
                "type": "primary_tp_and_real_stop_loss_both_true",
                "status": status,
                "exit_reason": exit_reason,
            })
        if runner_be and not primary_tp:
            violations.append({
                "signal_id": sid,
                "type": "runner_breakeven_without_primary_tp",
                "status": status,
                "exit_reason": exit_reason,
            })
        if breakeven and real_sl and exit_reason == "breakeven_stop":
            violations.append({
                "signal_id": sid,
                "type": "breakeven_stop_counted_as_real_stop_loss",
                "status": status,
                "exit_reason": exit_reason,
            })
        if primary_tp and official_result not in {None, "WIN"}:
            violations.append({
                "signal_id": sid,
                "type": "primary_tp_with_non_win_official_result",
                "official_result": official_result,
                "status": status,
                "exit_reason": exit_reason,
            })
        if primary_tp and official_result == "WIN" and not locked:
            violations.append({
                "signal_id": sid,
                "type": "official_win_not_locked",
                "official_result": official_result,
                "status": status,
                "exit_reason": exit_reason,
            })

    return {
        "signals_checked": len(signals),
        "violations_count": len(violations),
        "violations": violations[:100],
        "checks": {
            "runner_cannot_override_official_win": True,
            "breakeven_not_real_stop_loss": True,
            "primary_tp_is_official_win": True,
        },
    }
