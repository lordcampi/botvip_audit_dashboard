from __future__ import annotations

from src.f5_t09e_symbol_alpha import build_symbol_not_allowed_shadow_alpha


def test_symbol_not_allowed_alpha_ranks_positive_and_negative_symbols() -> None:
    facts = [
        {"record_type": "candidate", "candidate_id": "1", "symbol": "SUI/USDT:USDT", "side": "LONG", "blocked_reason": "live_guard:ofa_live_symbol_not_allowed", "hypothetical_result": "won", "net_rr": 1.2, "mfe": 1.5, "mae": -0.2},
        {"record_type": "candidate", "candidate_id": "2", "symbol": "LTC/USDT:USDT", "side": "SHORT", "blocked_reason": "live_guard:ofa_live_symbol_not_allowed", "hypothetical_result": "lost", "mfe": 0.1, "mae": -1.0},
        {"record_type": "signal", "signal_id": "99", "symbol": "SUI/USDT:USDT", "net_r": 9.9},
    ]
    result = build_symbol_not_allowed_shadow_alpha(facts=facts, events=[], signals=[], candidates=[])
    assert result["candidate_shadow_denominator"] == 2
    assert result["entity_scope"]["official_signals_counted_here"] == 0
    alpha_symbols = {row["symbol"] for row in result["ranking"]["alpha_potential_symbols"]}
    noisy_symbols = {row["symbol"] for row in result["ranking"]["noisy_or_negative_symbols"]}
    assert "SUI" in alpha_symbols
    assert "LTC" in noisy_symbols


def test_metadata_json_supplies_r_and_context_fields() -> None:
    facts = [{"record_type": "candidate", "candidate_id": "5", "symbol": "BCH/USDT:USDT", "blocked_reason": "symbol_not_in_allowlist"}]
    candidates = [{"id": "5", "metadata_json": '{"side": "LONG", "market_regime": "LOW_VOLATILITY", "btc_trend": "BEARISH", "hypothetical_result": "won", "net_rr": 1.7, "mfe_r": 2.0, "mae_r": -0.3}'}]
    result = build_symbol_not_allowed_shadow_alpha(facts=facts, events=[], signals=[], candidates=candidates)
    bch = result["segments"]["by_symbol"]["BCH"]
    assert bch["sample_size"] == 1
    assert bch["avg_r"] == 1.7
    assert bch["confidence"] == "very_small_sample"
    example = bch["examples"][0]
    assert example["btc_bias_conflict"] is True
    assert example["market_regime"] == "LOW_VOLATILITY"


def test_target_symbols_are_included_even_when_reason_label_varies() -> None:
    facts = [{"record_type": "candidate", "candidate_id": "7", "symbol": "NEAR/USDT:USDT", "blocked_reason": "some_other_shadow_reason", "hypothetical_result": "no_progress"}]
    result = build_symbol_not_allowed_shadow_alpha(facts=facts, events=[], signals=[], candidates=[])
    assert result["matched_rows"] == 1
    assert "NEAR" in result["segments"]["by_symbol"]
