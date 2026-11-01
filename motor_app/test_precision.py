import unittest
import test_app
from motion import (MIN_DISTANCE_MM, plan_move, make_wait, make_step,
                    timing_config, compile_program, scale_steps)


class PrecisionTests(unittest.TestCase):
    def test_one_pulse_is_the_minimum_distance(self):
        self.assertEqual(MIN_DISTANCE_MM,0.0004)
        self.assertEqual(plan_move('A','CW',0.0004,'speed',1)['pulses'],1)
        for distance in (0.0001,0.0002,0.0003999):
            with self.assertRaises(ValueError):
                plan_move('A','CW',distance,'speed',1)

    def test_single_pulse_odd_microsecond_duration(self):
        p = plan_move('C','CCW',0.0004,'time',0.000021)
        self.assertEqual(p['command'],'MOVET C -1 21')
        self.assertEqual(p['duration_us'],21)
        self.assertEqual(p['min_half_us'],10)
        self.assertEqual(p['duration'],0.000021)
        with self.assertRaises(ValueError):
            plan_move('C','CW',0.0004,'time',0.000019)

    def test_time_is_not_rounded_to_a_whole_pulse_period(self):
        row = make_step('A','CW',0.1,'time',0.100001)
        self.assertEqual(row['plan']['duration_us'],100001)
        self.assertEqual(compile_program({'A':[row]})[0][-1],('ADDT A 250 100001','ACK ADD A 1'))

    def test_wait_and_configuration_retain_one_microsecond(self):
        self.assertEqual(make_wait('D',0.000001)['plan']['command'],'WAIT D 1')
        self.assertEqual(timing_config(600.001,30.001,{'B':0.001})[0],
                         'CONFIG 600001 30001 0 1 0 0 0 0 0')

    def test_cycle_overrun_by_one_microsecond_is_rejected(self):
        rows = [make_step('A','CW',0.0004,'time',0.050001),
                make_step('A','CCW',0.0004,'time',0.050001)]
        compile_program({'A':rows},True,100.002)
        with self.assertRaises(ValueError):
            compile_program({'A':rows},True,100.001)

    def test_scaling_keeps_microsecond_total(self):
        rows = [make_step('A','CW',0.1,'time',0.1),make_step('A','CCW',0.1,'time',0.1)]
        scaled = scale_steps(rows,600.002)
        self.assertEqual([s['plan']['duration_us'] for s in scaled],[300001,300001])

    def test_long_total_time_is_not_limited_to_signed_32_bit(self):
        p = plan_move('B','CW',1,'time',2147.483649)
        self.assertEqual(p['command'],'MOVET B 2500 2147483649')


class PrecisionGuiTests(unittest.TestCase):
    setUp = test_app.AppTests.setUp
    tearDown = test_app.AppTests.tearDown
    connect = test_app.AppTests.connect

    def test_millisecond_editor_preserves_001_ms(self):
        self.connect()
        self.app.distance.set('0.0004')
        self.app.mode.set('time')
        self.app.duration.set('0.021')
        self.assertIn('0.021 ms',self.app.preview.get())
        self.app.move()
        self.assertEqual(self.fake.sent[-1],'MOVET A 1 21')

    def test_minimum_wait_is_visible_in_preview_and_list(self):
        self.app.kind.set('待機')
        self.app.duration.set('0.001')
        self.app.add_step()
        self.assertIn('0.001 ms',self.app.preview.get())
        self.assertEqual(self.app.trees['A'].item('0','values')[-1],'0.001')
        self.assertEqual(self.app.programs['A'][0]['plan']['command'],'WAIT A 1')


if __name__ == '__main__':
    unittest.main()
