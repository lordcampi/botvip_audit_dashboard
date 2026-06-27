"""F5_T12 Strategy Change Readiness tests (CORRECTED)."""
from __future__ import annotations
import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from src.ai_reporter.f5_t12_strategy_readiness import (
    build_f5_t12_strategy_readiness,
    MAX_DIGEST_CHARS,
    _build_denominators,
    _build_pf_core,
    _build_loss_top,
    _build_no_progress_core,
    _build_risk_context_candidates,
    _build_guard_value,
    _build_data_quality,
    _build_source_of_truth_note,
    _build_digest_consistency_checks,
    _build_human_checklist,
)

def _life(**o):
    return {"signals_total":67,"sent_to_telegram":38,"candidates_total":250,"events_total":334,"facts_total":500,**o}
def _t02(**o):
    return {"profit_factor_diagnostics":{"sent_only":{"count":38,"r_values_count":38,"gross_profit_r":5.374818,"gross_loss_r":-22.192483,"avg_r":-0.44257,"profit_factor":0.242191,"status":"losing_segment"},"all_signals":{"count":67,"r_values_count":38,"gross_profit_r":5.374818,"gross_loss_r":-22.192483,"avg_r":-0.44257,"profit_factor":0.242191,"status":"losing_segment"}},"data_quality_score_by_signal":{"score_counts":{"DATA_GOOD":60,"DATA_BAD":7},"reason_counts":{"missing_mfe":3,"missing_mae":4}},**o}
def _lc(**o):
    return {"official_signal_denominator":67,"total_loss_abs_r":22.19,"by_dimension":{"outcome":{"top_loss_segments":[{"segment":"loss","count":20,"gross_loss_abs_r":22.19}]},"symbol":{"top_loss_segments":[]},"side":{"top_loss_segments":[]},"zone":{"top_loss_segments":[]}},**o}
def _np(**o):
    return {"official_signal_denominator":67,"official_no_progress_count":12,"mfe_mae_recovery":{"mfe_known":10,"mae_known":9,"missing_mfe_or_mae":2},"bucket_counts":{"a":5,"b":3},"segments":{"by_symbol":{"XRP":{"count":3},"LINK":{"count":2},"ADA":{"count":2},"NEAR":{"count":2},"DOGE":{"count":1},"ETH":{"count":1},"SUI":{"count":1}}},"mfe_known_count":10,"mae_known_count":9,**o}
def _gm(**o):
    return {"candidate_shadow_denominator":250,"matched_guard_rows":45,"matrix_by_guard":{"g1":{"rows":20,"avoided_losses_r":10,"missed_winners_r":3,"net_guard_value_r":7},"g2":{"rows":10,"avoided_losses_r":2,"missed_winners_r":6,"net_guard_value_r":-4}},**o}
def _facts():
    return [{"record_type":"signal","signal_id":1,"sent_to_telegram":True}]


class TestPfCore:
    def test_uses_t02_sent_only(self):
        r = _build_pf_core(_t02())
        assert r["sent_only"]["count"] == 38
        assert r["sent_only"]["profit_factor"] == 0.242191
        assert r["all_signals"]["count"] == 67
    def test_empty(self):
        r = _build_pf_core({})
        assert r["sent_only"]["count"] == 0

class TestNoProgress:
    def test_top_symbols_non_zero(self):
        r = _build_no_progress_core(_np())
        for s in r["top_symbols"]:
            assert s["count"] > 0, f"{s['symbol']} count 0"
    def test_empty(self):
        r = _build_no_progress_core({})
        assert r["top_symbols"] == []

class TestDenominators:
    def test_events_not_zero(self):
        r = _build_denominators(_life())
        assert r["events"] == 334
    def test_sent_to_telegram(self):
        r = _build_denominators(_life())
        assert r["sent_to_telegram"] == 38

class TestDataQuality:
    def test_separates_scopes(self):
        r = _build_data_quality(_t02(), _np(), _lc())
        assert "raw_t02_data_quality_score_by_signal" in r
        assert "recovered_f5_t09_mfe_mae_recovery" in r
        assert r["recovered_f5_t09_mfe_mae_recovery"]["mfe_known"] == 10

class TestSourceOfTruth:
    def test_present(self):
        r = _build_source_of_truth_note()
        assert "pf_core_source" in r

class TestConsistencyChecks:
    def test_all_present(self):
        den = _build_denominators(_life())
        pf = _build_pf_core(_t02())
        np = _build_no_progress_core(_np())
        r = _build_digest_consistency_checks(den, pf, np)
        assert len(r["consistency_checks"]) == 10
    def test_c01_c06_pass(self):
        den = _build_denominators(_life())
        pf = _build_pf_core(_t02())
        np = _build_no_progress_core(_np())
        r = _build_digest_consistency_checks(den, pf, np)
        c = {x["id"]:x for x in r["consistency_checks"]}
        assert c["C01"]["passed"]
        assert c["C06"]["passed"]

class TestFullBuild:
    def test_read_only_true(self):
        r = build_f5_t12_strategy_readiness(lifecycle=_life(), facts=_facts(), t02_diagnostics=_t02(), loss_contribution=_lc(), no_progress_v3=_np(), guard_matrix=_gm())
        assert r["json"]["read_only"] is True
    def test_json_under_limit(self):
        r = build_f5_t12_strategy_readiness(lifecycle=_life(), facts=_facts(), t02_diagnostics=_t02(), loss_contribution=_lc(), no_progress_v3=_np(), guard_matrix=_gm())
        j = json.dumps(r["json"], ensure_ascii=False, default=str)
        assert len(j) < MAX_DIGEST_CHARS
    def test_md_under_limit(self):
        r = build_f5_t12_strategy_readiness(lifecycle=_life(), facts=_facts(), t02_diagnostics=_t02(), loss_contribution=_lc(), no_progress_v3=_np(), guard_matrix=_gm())
        assert len(r["markdown"]) < MAX_DIGEST_CHARS
    def test_pf_core_correct(self):
        r = build_f5_t12_strategy_readiness(lifecycle=_life(), facts=_facts(), t02_diagnostics=_t02(), loss_contribution=_lc(), no_progress_v3=_np(), guard_matrix=_gm())
        j = r["json"]
        assert j["sections"]["pf_core"]["sent_only"]["count"] == 38
        assert j["sections"]["pf_core"]["sent_only"]["profit_factor"] == 0.242191
    def test_events_correct(self):
        r = build_f5_t12_strategy_readiness(lifecycle=_life(), facts=_facts(), t02_diagnostics=_t02(), loss_contribution=_lc(), no_progress_v3=_np(), guard_matrix=_gm())
        assert r["json"]["sections"]["denominators"]["events"] == 334
    def test_np_top_symbols_non_zero(self):
        r = build_f5_t12_strategy_readiness(lifecycle=_life(), facts=_facts(), t02_diagnostics=_t02(), loss_contribution=_lc(), no_progress_v3=_np(), guard_matrix=_gm())
        for s in r["json"]["sections"]["no_progress_core"]["top_symbols"]:
            assert s["count"] > 0
    def test_source_of_truth_present(self):
        r = build_f5_t12_strategy_readiness(lifecycle=_life(), facts=_facts(), t02_diagnostics=_t02(), loss_contribution=_lc(), no_progress_v3=_np(), guard_matrix=_gm())
        assert "source_of_truth_note" in r["json"]["sections"]
    def test_no_large_json(self):
        r = build_f5_t12_strategy_readiness(lifecycle=_life(), facts=_facts(), t02_diagnostics=_t02(), loss_contribution=_lc(), no_progress_v3=_np(), guard_matrix=_gm())
        j = json.dumps(r["json"], ensure_ascii=False, default=str)
        assert "representative_examples" not in j
    def test_empty_inputs(self):
        r = build_f5_t12_strategy_readiness(lifecycle={}, facts=[], t02_diagnostics={}, loss_contribution={}, no_progress_v3={}, guard_matrix={})
        assert r["json"]["read_only"] is True
        j = json.dumps(r["json"], ensure_ascii=False, default=str)
        assert len(j) < MAX_DIGEST_CHARS
    def test_deterministic(self):
        kw = dict(lifecycle=_life(), facts=_facts(), t02_diagnostics=_t02(), loss_contribution=_lc(), no_progress_v3=_np(), guard_matrix=_gm())
        r1 = build_f5_t12_strategy_readiness(**kw)
        r2 = build_f5_t12_strategy_readiness(**kw)
        assert json.dumps(r1["json"]) == json.dumps(r2["json"])
