"""pytest configuration for scripts/tests.

Five files (test_cascade, test_fdr_family, test_id_column_types, test_notify_poller, test_restatement_gate) are standalone scripts (they run at import time, call sys.exit
or open a DuckDB file taken from argv), not pytest modules; collecting them breaks the whole directory run.
Run them directly:  .venv/bin/python scripts/tests/test_cascade.py
"""
collect_ignore = ["test_cascade.py", "test_fdr_family.py", "test_id_column_types.py", "test_notify_poller.py", "test_restatement_gate.py"]
