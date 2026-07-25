from __future__ import annotations

"""Tests ensuring Streamlit navigation contains only SWING pages after R2.1."""

import os
import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PAGES_DIR = PROJECT_ROOT / "pages"


def _py_files(directory: Path) -> list[str]:
    """Return sorted Python file names in directory, excluding pycache."""
    if not directory.exists():
        return []
    return sorted(
        f.name
        for f in directory.iterdir()
        if f.suffix == ".py" and f.name not in ("__init__.py",)
        and "__pycache__" not in f.parts
    )


# ---------------------------------------------------------------------------
# 1. No legacy pages exist
# ---------------------------------------------------------------------------
class TestNoLegacyPages:
    """Verify all OFA / F4 / F5 / legacy pages are gone."""

    LEGACY_PAGE_NAMES = {
        "1_Overview.py",
        "2_F4_T11a_Lifecycle_Audit.py",
        "3_Events_Explorer.py",
        "4_Signals_Explorer.py",
        "5_OFA_Funnel.py",
        "6_Rejection_Analysis.py",
        "7_Strategy_Calibration_Lab.py",
        "8_Symbol_Performance.py",
        "9_Export_Reports.py",
        "10_F5_T12_Calibration.py",
    }

    def test_no_legacy_pages_in_directory(self):
        existing = set(_py_files(PAGES_DIR))
        conflict = existing & self.LEGACY_PAGE_NAMES
        assert len(conflict) == 0, (
            f"Legacy pages still present: {sorted(conflict)}"
        )

    def test_only_swing_page_exists(self):
        existing = set(_py_files(PAGES_DIR))
        assert existing == {"1_Swing_Strategy_Review.py"}, (
            f"Unexpected pages found: {existing}"
        )

    def test_swing_page_is_first_in_menu(self):
        """The SWING page should be pages/1_* so it appears first."""
        existing = _py_files(PAGES_DIR)
        assert "1_Swing_Strategy_Review.py" in existing

    def test_no_duplicate_swing_page(self):
        """Only one version of the SWING page should exist."""
        swing_pages = [f for f in _py_files(PAGES_DIR) if "Swing_Strategy_Review" in f]
        assert len(swing_pages) == 1, f"Multiple SWING pages: {swing_pages}"


# ---------------------------------------------------------------------------
# 2. app.py audit
# ---------------------------------------------------------------------------
class TestAppPy:
    """Verify app.py is SWING-only and does NOT import SQLite or legacy modules."""

    FORBIDDEN_MODULES = {
        "src.db",
        "src.schema",
        "src.loaders",
        "src.metrics",
        "src.charts",
        "src.reports",
        "src.parsers",
        "sqlite3",
    }

    FORBIDDEN_FUNCTIONS = {
        "connect_readonly",
        "get_db_path",
        "db_exists",
        "discover_schema",
        "load_events_cached",
        "load_signals_cached",
    }

    def test_app_py_compiles(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "app", PROJECT_ROOT / "app.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

    def test_app_py_no_sqlite_imports(self):
        """app.py must NOT import SQLite or legacy src modules."""
        app_path = PROJECT_ROOT / "app.py"
        with open(app_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name not in self.FORBIDDEN_MODULES, (
                        f"app.py imports forbidden module: {alias.name}"
                    )
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    assert node.module not in self.FORBIDDEN_MODULES, (
                        f"app.py imports forbidden module: {node.module}"
                    )

    def test_app_py_no_postgresql_connection(self):
        """app.py must NOT open PostgreSQL connections."""
        app_path = PROJECT_ROOT / "app.py"
        source = app_path.read_text(encoding="utf-8")
        for name in self.FORBIDDEN_FUNCTIONS:
            assert name not in source, (
                f"app.py references forbidden function: {name}"
            )


# ---------------------------------------------------------------------------
# 3. SWING page integrity
# ---------------------------------------------------------------------------
class TestSwingPageIntegrity:
    """Verify the SWING page compiles and uses R1 read-only layer."""

    def test_swing_page_compiles(self):
        import importlib.util
        page_path = PAGES_DIR / "1_Swing_Strategy_Review.py"
        assert page_path.exists(), f"SWING page not found: {page_path}"
        spec = importlib.util.spec_from_file_location(
            "swing_page", page_path
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

    def test_swing_page_uses_r1(self):
        """The SWING page must import from swing_dashboard_service (R2)."""
        page_path = PAGES_DIR / "1_Swing_Strategy_Review.py"
        source = page_path.read_text(encoding="utf-8")
        assert "swing_dashboard_service" in source, (
            "SWING page must import from src.swing_dashboard_service"
        )

    def test_swing_page_no_sqlite_import(self):
        page_path = PAGES_DIR / "1_Swing_Strategy_Review.py"
        with open(page_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        forbidden = {"src.db", "sqlite3"}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module and node.module in forbidden:
                    pytest.fail(f"SWING page imports forbidden module: {node.module}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in forbidden:
                        pytest.fail(f"SWING page imports forbidden module: {alias.name}")
        # Also verify no references to legacy function names in source
        source = page_path.read_text(encoding="utf-8")
        assert "get_db_path" not in source
        assert "connect_readonly" not in source  # SQLite variant


# ---------------------------------------------------------------------------
# 4. Legacy modules still exist (only pages were removed)
# ---------------------------------------------------------------------------
class TestLegacyModulesPreserved:
    """Legacy src/ modules used by daily_ai_report.py must still exist."""

    def test_daily_ai_report_exists(self):
        assert (PROJECT_ROOT / "daily_ai_report.py").exists()

    def test_legacy_modules_still_present(self):
        legacy_modules = [
            "src/daily_facts.py",
            "src/report_writer.py",
            "src/ai_pack_builder.py",
            "src/telegram_delivery.py",
            "src/safe_zip_chunking.py",
        ]
        for mod in legacy_modules:
            assert (PROJECT_ROOT / mod).exists(), f"Missing: {mod}"