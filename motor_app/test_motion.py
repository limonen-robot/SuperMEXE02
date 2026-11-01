import unittest
from motion import plan_move


class MotionTests(unittest.TestCase):
    def test_speed_and_time_produce_same_pulses(self):
        speed = plan_move('A', 'CW', '0.1', 'speed', '0.1')
        timed = plan_move('A', 'CW', '0.1', 'time', '1')
        self.assertEqual(speed['command'], 'MOVE A 250 2000')
        self.assertEqual(speed, timed)

    def test_reverse_and_max_speed(self):
        p = plan_move('D', 'CCW', 6, 'speed', 20)
        self.assertEqual(p['command'], 'MOVE D -15000 10')
        self.assertEqual(p['duration'], 0.3)

    def test_time_cannot_bypass_speed_cap(self):
        with self.assertRaises(ValueError):
            plan_move('B', 'CW', 1, 'time', 0.01)

    def test_new_speed_range_and_time_equivalence(self):
        speed = plan_move('B', 'CW', 1, 'speed', 20)
        timed = plan_move('B', 'CW', 1, 'time', 0.05)
        self.assertEqual(speed, timed)
        self.assertEqual(speed['command'], 'MOVE B 2500 10')
        for value in (20.001, 40):
            with self.assertRaises(ValueError):
                plan_move('B', 'CW', 1, 'speed', value)

    def test_invalid_numbers_and_ranges(self):
        for distance, value in [('nan', '1'), ('inf', '1'), ('1', 'nan'),
                                ('1', 'inf'), ('0', '1'), ('6.1', '1'),
                                ('-1', '1'), ('1', '0'), ('0.00001', '0.1')]:
            with self.subTest(distance=distance, value=value):
                with self.assertRaises(ValueError):
                    plan_move('A', 'CW', distance, 'speed', value)

    def test_preview_matches_integer_pulse_timing(self):
        p = plan_move('C', 'CW', 0.12345, 'time', 1.2345)
        self.assertAlmostEqual(p['distance'], p['pulses'] / 2500)
        self.assertEqual(p['duration_us'],1234500)
        self.assertEqual(p['command'],'MOVET C 309 1234500')
        self.assertEqual(p['duration'],p['duration_us']/1000000)


if __name__ == '__main__':
    unittest.main()
