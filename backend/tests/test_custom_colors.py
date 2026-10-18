import unittest
from backend.services import ai
from backend.tests import test_ai_request as request_tests
from backend.design import colors, engine


class CustomColorTests(unittest.TestCase):
    def test_specific_shades_and_unknown_names_are_preserved(self):
        behavior = request_tests.RequestBehaviorTests(); behavior.setUp()
        for name in ('sage', 'dusty pink', 'biru pastel', 'biru elektrik', 'ocean mist'):
            result, trace = behavior.propose('Aku mau depan '+name, request_tests.response_v2({'front_color': name}, {'front_color': name}))
            self.assertTrue(trace['live_model_used'], name)
            self.assertEqual(result['proposal']['preferences']['front_color'], name)
        self.assertEqual(ai._quoted_color('depan dusty pink'), 'dusty pink')
        self.assertFalse(colors.same_family('dusty pink', 'pink'))

    def test_custom_named_swatch_matches_request_and_keeps_actual_hex(self):
        self.assertTrue(colors.same_family('ocean mist', 'ocean mist (#53AEC1)'))
        self.assertEqual(colors.resolve('ocean mist (#53AEC1)')['hex'], '#53AEC1')
        self.assertEqual(colors.resolve('ocean mist (#53AEC1)')['name'], 'ocean mist')
        self.assertFalse(colors.same_family('ocean mist', 'blue'))
        data = engine.fixture_data()
        data['materials'][0]['color'] = 'ocean mist (#53AEC1)'
        data['request']['preferences']['front_color'] = 'ocean mist'
        self.assertIsNone(colors.stock_warning('ocean mist', data['materials']))
        self.assertEqual(engine.validate_input(data)['materials'][0]['color'], 'ocean mist (#53AEC1)')

    def test_unknown_color_offers_real_stock_without_fake_nearest(self):
        data = engine.fixture_data()
        data['materials'][0]['available'] = False
        warning = colors.stock_warning('warna unik pilihan saya', data['materials'])
        self.assertNotIn('nearest', [a['id'] for a in warning['actions']])
        choices = [a for a in warning['actions'] if a['id'] == 'choose']
        self.assertGreater(len(choices), 0)
        for choice in choices:
            self.assertIn(choice['value'], [m['color'] for m in data['materials'] if m['available']])
        self.assertIn('pilihan saya', warning['text'])

    def test_known_color_substitution_is_still_rejected(self):
        behavior = request_tests.RequestBehaviorTests(); behavior.setUp()
        result, trace = behavior.propose('depan dusty pink', request_tests.response_v2({'front_color': 'blue'}, {'front_color': 'dusty pink'}))
        self.assertIsNone(result['proposal'])
