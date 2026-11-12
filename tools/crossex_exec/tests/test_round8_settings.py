"""Persistent, atomic, strictly typed opening configuration."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.crossex_exec.storage import Store
from test_exec import setUpModule, tearDownModule


class SettingsTests(unittest.TestCase):
    def setUp(self):
        from tools.crossex_exec.settings import Settings
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'exec.db'
        self.store = Store(self.path)
        self.addCleanup(self.store.close)
        self.settings = Settings(self.store, clock=lambda: 1000)

    def test_defaults_and_every_boundary_are_persistent_and_audited(self):
        from tools.crossex_exec.settings import SCHEMA
        initial = self.settings.read()
        self.assertEqual(initial['version'], 0)
        self.assertEqual(initial['values']['order_usd_max'], 50)
        self.assertEqual(initial['values']['min_quote_volume_usd'], 1000000)
        for name, rule in SCHEMA.items():
            for bound in ('min', 'max'):
                value = rule[bound]
                if value is None:
                    continue
                with self.subTest(name=name, bound=bound):
                    before = self.settings.read()
                    result = self.settings.update({name: value}, source='admin_api')
                    self.assertEqual(result['values'][name], value)
                    self.assertEqual(result['version'], before['version']+1)
                    audit = self.settings.audit()[-1]
                    self.assertEqual(audit['old'], {name: before['values'][name]})
                    self.assertEqual(audit['new'], {name: value})
                    self.assertEqual((audit['at'], audit['source']), (1000, 'admin_api'))
                    with Store(self.path) as reopened:
                        from tools.crossex_exec.settings import Settings
                        self.assertEqual(Settings(reopened).read(), result)

    def test_every_out_of_range_value_and_wrong_type_is_atomic(self):
        from tools.crossex_exec.settings import SCHEMA
        for name, rule in SCHEMA.items():
            invalid = [True, None, str(rule['default']), [], {}, float('nan'), float('inf')]
            invalid += [rule['min']-1] if rule['min'] is not None else []
            invalid += [rule['max']+1] if rule['max'] is not None else []
            if rule['integer']:
                invalid.append(rule['default']+.5)
            for value in invalid:
                with self.subTest(name=name, value=value):
                    before = self.settings.read()
                    audit = self.settings.audit()
                    with self.assertRaises(ValueError):
                        self.settings.update(dict(order_usd_max=40, **{name: value})
                                             if name != 'order_usd_max' else {name: value}, source='admin_api')
                    self.assertEqual(self.settings.read(), before)
                    self.assertEqual(self.settings.audit(), audit)
        with self.assertRaises(ValueError):
            self.settings.update({'unknown': 2}, source='admin_api')

    def test_snapshot_is_detached_and_unreadable_table_falls_back_with_alert(self):
        old = self.settings.read()
        self.settings.update({'order_usd_max': 30}, source='admin_api')
        self.assertEqual(old['values']['order_usd_max'], 50)
        real_meta = self.store.meta
        with patch.object(self.store, 'meta', side_effect=lambda k, d=None:
                          (_ for _ in ()).throw(ValueError('unreadable')) if k == 'runtime_config' else real_meta(k, d)):
            fallback = self.settings.read()
        self.assertEqual(fallback['values']['order_usd_max'], 50)
        self.assertTrue(fallback['fallback'])
        self.assertTrue(any(e['kind'] == 'CONFIG_DEFAULTS_ALERT' for e in self.store.events()))

    def test_audit_failure_rolls_back_config_too(self):
        before = self.settings.read()
        with patch.object(self.store, 'event', side_effect=RuntimeError('write failure')):
            with self.assertRaises(RuntimeError):
                self.settings.update({'order_usd_max': 30}, source='admin_api')
        self.assertEqual(self.settings.read(), before)
        self.assertEqual(self.settings.audit(), [])
