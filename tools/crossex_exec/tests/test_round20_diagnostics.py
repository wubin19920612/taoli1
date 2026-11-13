"""Refresh errors expose local constants and traceback metadata, never bodies."""
import json
import unittest
from unittest.mock import patch

from test_exec import setUpModule, tearDownModule
from test_round20_positions import PositionFixture


PRIVATE = 'offline-response-token-and-private-body'


def local_failure():
    raise ValueError('position_margin_mode_unknown')


def secret_failure():
    class UnprintableError(RuntimeError):
        def __str__(self):
            raise AssertionError('exception_body_accessed')
    try:
        raise ValueError(PRIVATE)
    except ValueError as error:
        raise UnprintableError(PRIVATE) from error


class RefreshDiagnosticsTests(PositionFixture, unittest.TestCase):
    def events(self, kind):
        return [json.loads(r['value']) for r in self.store.events() if r['kind']==kind]

    def fail_refresh(self, failure):
        with patch.object(self.engine,'read_account_snapshot',side_effect=failure):
            self.engine.refresh_accounts()

    def test_refresh_event_has_type_local_reason_and_precise_innermost_location(self):
        self.fail_refresh(local_failure)
        event = self.events('ACCOUNT_REFRESH_FAILED')[-1]
        self.assertEqual(event,dict(error_type='ValueError',reason='position_margin_mode_unknown',
            location=__name__+':'+str(local_failure.__code__.co_firstlineno+1)))
        self.assertFalse(self.engine.can_open())
        self.assertIsNone(self.engine.account_cache.value)

    def test_body_chain_and_private_paths_are_not_formatted_or_logged(self):
        self.fail_refresh(secret_failure)
        event = self.events('ACCOUNT_REFRESH_FAILED')[-1]
        self.assertEqual(set(event),{'reason','error_type','location'})
        self.assertEqual(event['error_type'],'UnprintableError')
        self.assertEqual(event['reason'],'account_refresh_unavailable')
        rendered=json.dumps(self.events('ACCOUNT_REFRESH_FAILED')+self.events('ALERT'))
        self.assertNotIn(PRIVATE,rendered)
        self.assertNotIn(self.tmp.name,rendered)

    def test_alerts_deduplicate_each_safe_reason_and_recover_with_fresh_reads(self):
        received=[]
        self.engine.alert_sink=received.append
        for failure in (local_failure,local_failure,secret_failure,local_failure,secret_failure):
            self.fail_refresh(failure)
        self.assertEqual(len(self.events('ACCOUNT_REFRESH_FAILED')),5)
        self.assertEqual(len(self.events('ALERT')),2)
        self.assertEqual(len(received),2)
        self.assertIn('account_snapshot_unavailable',self.store.meta('stop_locks'))
        self.http.responses['/api/v4/crossex/positions']=[]
        self.engine.limits.initialize(self.accounts.snapshot())
        for _ in range(3):
            self.now+=1
            self.engine.refresh_accounts()
        self.assertNotIn('account_snapshot_unavailable',self.store.meta('stop_locks'))
        self.fail_refresh(local_failure)
        self.assertEqual(len(received),3)


if __name__ == '__main__': unittest.main()
