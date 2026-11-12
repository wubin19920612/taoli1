"""Round-eight floor-only sizing requirements approved by PM on 2026-11-12.

Reuse the exact round-three minimum-order fixtures to make the behavioural
conflict reproducible without editing any existing assertion or production code.
No OI/network behaviour is simulated here: this isolates the last sizing step.
"""
from dataclasses import replace
import unittest

from tools.fill_sim.core import Book
from tools.crossex_exec.policy import review
from test_exec import instrument, setUpModule, tearDownModule


class Round8FloorOnlySizingContractTests(unittest.TestCase):
    @staticmethod
    def inputs():
        instruments = [instrument(), instrument('bitget')]
        books = [Book([['99', '10']], [['100', '10']], 1000),
                 Book([['102', '10']], [['103', '10']], 1000)]
        funding = [dict(rate='0', next=5000, interval=28800, captured_at=1000,
                        history_ok=True, max_abs_24h='0') for _ in range(2)]
        return instruments, books, ['.0002']*2, funding, ['5000000']*2, 1000

    def test_below_final_sell_minimum_rejects_instead_of_bumping(self):
        args = self.inputs()
        args[0][1].spec = replace(args[0][1].spec, min_notional='99.94')
        with self.assertRaisesRegex(ValueError, '^size_too_small$'):
            review(*args, baseline_bps=0)

    def test_below_minimum_quantity_rejects_instead_of_bumping(self):
        args = self.inputs()
        args[0][0].spec = replace(args[0][0].spec, min_qty='.981')
        with self.assertRaisesRegex(ValueError, '^size_too_small$'):
            review(*args, baseline_bps=0)

    def test_exchange_maximum_rejects_even_when_minimums_are_satisfied(self):
        args = self.inputs()
        args[0][1].spec = replace(args[0][1].spec, max_qty='.979')
        with self.assertRaisesRegex(ValueError, '^final_size_above_max$'):
            review(*args, baseline_bps=0, quantity='.980')
