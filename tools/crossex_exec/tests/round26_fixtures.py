"""File-backed, offline worlds for round 26 recovery proofs."""
import tempfile
from pathlib import Path

from round15_simulator import ProgramWorld


def world(test, *, opened=True):
    tmp = tempfile.TemporaryDirectory()
    test.addCleanup(tmp.cleanup)
    result = ProgramWorld(path=Path(tmp.name)/'round26.db')
    test.addCleanup(result.close)
    result.market.current_funding = result.funding
    result.advance()
    if opened:
        result.open()
        result.advance()
    return result


def halt(w):
    w.store.set_meta('state', 'HALTING')
    w.store.set_meta('halt_reason', 'total_loss')
    w.engine.tick()


def release(w):
    return w.engine.rebaseline(confirm='total_loss', reason='verified_program_flat',
                              source='cli', release_halt=True)
