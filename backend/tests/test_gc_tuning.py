"""Run real freeze tests in a child, preserving the runner's permanent heap."""

import gc
import subprocess
import sys
from pathlib import Path


def test_gc_tuning_isolated_process():
    before = gc.get_threshold(), gc.get_freeze_count()
    script = """
import gc
import sys
import pytest

# CPython 3.12 moves immortal objects back into the permanent pool on collect.
before = gc.get_threshold(), gc.get_freeze_count()
result = pytest.main(['-q', 'tests/gc_tuning_cases.py'])
gc.collect()
after = gc.get_threshold(), gc.get_freeze_count()
print('GC_STATE_RESTORED', before, after)
assert before == after
sys.exit(result)
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "GC_STATE_RESTORED" in result.stdout
    assert (gc.get_threshold(), gc.get_freeze_count()) == before
