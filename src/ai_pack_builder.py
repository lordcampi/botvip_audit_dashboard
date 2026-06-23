from __future__ import annotations

import json
from typing import Any


def _json_block(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


def build_ai_prompt() -> str:
    return """Analiza este AI Review Pack de BotVIP/AlphaScalp.

Objetivo:
Ayudar a pulir, graduar y calibrar la estrategia sin aplicar cambios automaticos.

Restricciones:
- No recomendar trading real.
- No cambiar thresholds sin evidencia suficiente.
- No sobreoptimizar con muestra pequena.
- Separar evidencia fuerte, moderada y debil.
- Mantener F4_T11a: PRIMARY_TP_HIT = WIN oficial; runner separado; breakeven no es STOP_LOSS_HIT.
- Proponer solo cambios pequenos, reversibles y medibles.
- Todo cambio requiere aprobacion humana.

Responde:
1. Diagnostico ejecutivo.
2. Que tienen en comun las ganadoras.
3. Que tienen en comun las perdedoras.
4. Que filtros parecen proteger.
5. Que filtros podrian estar bloqueando demasiado.
6. Que near-misses revisar.
7. Que hipotesis tienen evidencia fuerte/moderada/debil.
8. Que experimentos shadow probar.
9. Que cambios NO hacer todavia.
10. Que medir manana para validar.
"""


def build_executive_summary(window_label: str, start_text: str, end_text: str, lifecycle: dict[str, Any], audit: dict[str, Any], blocked_summary: dict[str, Any], hypotheses: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    lines.append("# BotVIP Daily AI Reporter - Executive Summary")
    lines.append("")
    lines.append(f"Window: `{start_text}` -> `{end_text}`")
    lines.append(f"Window label: `{window_label}`")
    lines.append("")
    lines.append("## Core lifecycle")
    for key in [
        "signals_total", "sent_to_telegram", "primary_tp_hit", "real_stop_loss_hit", "breakeven_stop_hit",
        "runner_breakeven_stop_hit", "time_stop_exit", "cancelled_or_expired", "no_progress_exit", "data_gap_events",
        "official_win_rate_tp_vs_sl", "sent_win_rate_primary_tp_over_sent", "avg_net_r",
    ]:
        lines.append(f"- {key}: {lifecycle.get(key)}")
    lines.append("")
    lines.append("## F4_T11a audit")
    lines.append(f"- violations_count: {audit.get('violations_count')}")
    lines.append("- PRIMARY_TP_HIT is treated as official WIN.")
    lines.append("- Breakeven and runner outcomes are separated from real STOP_LOSS.")
    lines.append("")
    lines.append("## Blocked candidates")
    lines.append(f"- candidates_total: {blocked_summary.get('candidates_total')}")
    lines.append(f"- blocked_total: {blocked_summary.get('blocked_total')}")
    lines.append(f"- near_miss_total: {blocked_summary.get('near_miss_total')}")
    lines.append(f"- would_send_total: {blocked_summary.get('would_send_total')}")
    lines.append("")
    lines.append("Top blocked reasons:")
    for reason, count in list((blocked_summary.get("top_blocked_reasons") or {}).items())[:10]:
        lines.append(f"- {reason}: {count}")
    lines.append("")
    lines.append("## Hypotheses generated")
    lines.append(f"- hypotheses_count: {len(hypotheses)}")
    for hyp in hypotheses[:10]:
        lines.append(f"- {hyp.get('hypothesis_id')}: {hyp.get('title')} [{hyp.get('evidence_strength')}]")
    lines.append("")
    lines.append("## Guardrails")
    lines.append("- No automatic strategy changes.")
    lines.append("- No threshold changes from this report alone.")
    lines.append("- Treat small samples as weak evidence.")
    lines.append("- Keep all recommendations shadow/observational until approved by human review.")
    return "\n".join(lines) + "\n"


def build_ai_review_pack(
    window_label: str,
    start_text: str,
    end_text: str,
    executive_summary: str,
    lifecycle: dict[str, Any],
    audit: dict[str, Any],
    blocked_summary: dict[str, Any],
    winners_losers_rows: list[dict[str, Any]],
    funnel_rows: list[dict[str, Any]],
    near_misses: list[dict[str, Any]],
    hypotheses: list[dict[str, Any]],
) -> str:
    prompt = build_ai_prompt()
    sections = [
        "# BotVIP Daily AI Review Pack",
        "",
        "## 1. Contexto del sistema",
        "BotVIP/AlphaScalp en modo shadow observacional. El reporte es batch, read-only, sin cambios automaticos.",
        "",
        "## 2. Ventana analizada",
        f"- label: {window_label}",
        f"- start: {start_text}",
        f"- end: {end_text}",
        "",
        "## 3. Prompt para Copilot/GPT-5",
        prompt,
        "",
        "## 4. Resumen ejecutivo",
        executive_summary,
        "",
        "## 5. Lifecycle metrics",
        "```json\n" + _json_block(lifecycle) + "\n```",
        "",
        "## 6. F4_T11a audit",
        "```json\n" + _json_block(audit) + "\n```",
        "",
        "## 7. Blocked candidates summary",
        "```json\n" + _json_block(blocked_summary) + "\n```",
        "",
        "## 8. Winners vs losers preview",
        "```json\n" + _json_block(winners_losers_rows[:80]) + "\n```",
        "",
        "## 9. OFA/filter funnel",
        "```json\n" + _json_block(funnel_rows) + "\n```",
        "",
        "## 10. Near misses top 80",
        "```json\n" + _json_block(near_misses[:80]) + "\n```",
        "",
        "## 11. Strategy hypotheses",
        "```json\n" + _json_block(hypotheses) + "\n```",
        "",
        "## 12. Restricciones de seguridad",
        "- No aplicar cambios automaticos.",
        "- No tocar BotVIP principal.",
        "- No operar real.",
        "- Cualquier ajuste debe ser pequeno, reversible, medible y aprobado por humano.",
    ]
    return "\n".join(sections) + "\n"
