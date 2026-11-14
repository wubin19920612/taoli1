import unittest

from tools.crossex_exec.engine import review_failure_details


def raised(error):
    try:
        raise error
    except Exception as caught:
        return caught


class ReviewFailureDetailsTests(unittest.TestCase):
    def test_local_reason_code_is_recorded(self):
        d=review_failure_details(raised(ValueError('actual_fee_missing')))
        self.assertEqual((d['error_type'],d['cause']),('ValueError','actual_fee_missing'))
        self.assertIn('test_review_failure_details:',d['location'])

    def test_free_text_and_other_types_are_not_echoed(self):
        for error in (ValueError('Invalid response {"msg": "secret"}'),RuntimeError('position_limit'),
                      KeyError('crossex|BINANCE_FUTURE_X_USDT'),ValueError('a','b')):
            d=review_failure_details(raised(error))
            self.assertNotIn('cause',d)
            self.assertEqual(set(d),{'error_type','location'})

    def test_missing_key_name_is_recorded(self):
        self.assertEqual(review_failure_details(raised(KeyError('initial_margin')))['cause'],'initial_margin')


if __name__=='__main__':
    unittest.main()
