"""Round eight additive summary contract; existing assertions stay untouched."""
import tempfile
import unittest
from pathlib import Path

from tools.crossex_exec.api_data import JournalView
from tools.crossex_exec.storage import Store
from test_exec import setUpModule, tearDownModule


NEW_REASONS = ('size_too_small', 'oi_missing', 'mapping_ratio_mismatch')


class Round8SummaryContractTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'exec.db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.view = JournalView(self.path, {}, clock=lambda: 1794355200.0)

    def test_new_skip_counters_are_present_and_zero_without_history(self):
        counts = self.view.get('summary', {})['opening_skip_counts']
        self.assertEqual({reason: counts.get(reason) for reason in NEW_REASONS},
                         dict.fromkeys(NEW_REASONS, 0))

    def test_new_skip_counters_deduplicate_and_survive_restart(self):
        for reason in NEW_REASONS:
            self.store.claim(reason, {'received_at': 1794355200.0})
            self.store.skip(reason, reason)
            self.store.skip(reason, reason)
        with Store(self.path) as reopened:
            for reason in NEW_REASONS:
                reopened.skip(reason, reason)
        counts = self.view.get('summary', {})['opening_skip_counts']
        self.assertEqual({reason: counts.get(reason) for reason in NEW_REASONS},
                         dict.fromkeys(NEW_REASONS, 1))
        self.assertEqual(len([event for event in self.store.events()
                              if event['kind'] == 'SKIP']), 3)
