import copy
import unittest
from motion import (make_step, make_wait, make_home, compile_program,
                    timing_config, scale_steps, jog_command)
import test_app
from unittest.mock import patch


class ModelFeatureTests(unittest.TestCase):
    def test_wait_and_home_are_real_queue_items(self):
        rows = [make_wait('C', 0.25), make_home('C')]
        commands, count, duration = compile_program({'C': rows})
        self.assertEqual(commands, [('CLEAR','ACK CLEAR'), ('WAIT C 250000','ACK ADD C 1'), ('ZHADD C','ACK ADD C 2')])
        self.assertEqual(count, 2)
        self.assertAlmostEqual(duration, 1.27)
        self.assertEqual(rows[1]['plan']['pulses'], 0)

    def test_repeat_requires_zero_integer_pulses_and_time_fit(self):
        out = make_step('A','CW',0.1,'time',0.2)
        back = make_step('A','CCW',0.1,'time',0.2)
        compile_program({'A':[out, make_wait('A',0.2), back]}, True, 600)
        for rows in ([out], [out, make_step('A','CCW',0.1004,'time',0.2)],
                     [out, make_wait('A',0.201), back]):
            with self.assertRaises(ValueError):
                compile_program({'A':rows}, True, 600)

    def test_phase_delay_can_exceed_cycle_without_extending_body(self):
        rows = [make_step('A','CW',0.1,'time',0.1), make_step('A','CCW',0.1,'time',0.1)]
        self.assertAlmostEqual(compile_program({'A':rows}, True, 600, {'A':630})[2], 0.83)

    def test_home_is_not_inserted_into_repeat(self):
        with self.assertRaises(ValueError):
            compile_program({'A':[make_home('A')]}, True)
        command, ack = timing_config(600,30,{'A':630},True,True,False)
        self.assertEqual(command,'CONFIG 600000 30000 630000 0 0 0 1 1 0')
        self.assertEqual(ack,'ACK CONFIG')

    def test_timing_ranges(self):
        for period, width in [(600,600),(0,30),(600,0),(float('nan'),30),(600,float('inf'))]:
            with self.assertRaises(ValueError):
                timing_config(period,width)
        with self.assertRaises(ValueError):
            timing_config(600,30,{'D':-1})
        for seconds in (0,-1,601,float('nan')):
            with self.assertRaises(ValueError):
                make_wait('A',seconds)

    def test_scaling_preserves_stroke_and_wait_ratio(self):
        rows = [make_step('A','CW',0.1,'time',0.1), make_wait('A',0.2),
                make_step('A','CCW',0.1,'time',0.1)]
        before = copy.deepcopy(rows)
        scaled = scale_steps(rows,800)
        self.assertEqual(rows,before)
        self.assertAlmostEqual(sum(r['plan']['duration'] for r in scaled),0.8)
        self.assertEqual([r['plan']['pulses'] for r in scaled],[250,0,250])
        self.assertAlmostEqual(scaled[1]['plan']['duration'],0.4)
        compile_program({'A':scaled},True,800)

    def test_home_duration_is_never_scaled(self):
        rows = [make_home('D'),make_wait('D',1)]
        scaled = scale_steps(rows,2020)
        self.assertEqual(scaled[0]['plan']['duration'],1.02)
        self.assertEqual(scaled[1]['plan']['duration'],1)
        with self.assertRaises(ValueError):
            scale_steps(rows,600)

    def test_scale_rejects_impossible_speed(self):
        with self.assertRaises(ValueError):
            scale_steps([make_step('A','CW',6,'time',1)],10)

    def test_jog_direction_and_cap(self):
        self.assertEqual(jog_command('D','CCW',1),'JOG D -200')
        self.assertEqual(jog_command('A','CW',20),'JOG A 10')
        with self.assertRaises(ValueError):
            jog_command('A','CW',21)


class GuiFeatureTests(unittest.TestCase):
    setUp = test_app.AppTests.setUp
    tearDown = test_app.AppTests.tearDown
    connect = test_app.AppTests.connect
    add = test_app.AppTests.add
    def test_wait_registration_ignores_unused_move_inputs(self):
        self.app.kind.set('待機')
        self.app.distance.set('not a number')
        self.app.duration.set('2000.000')
        self.app.add_step()
        self.assertEqual(self.app.programs['A'][0]['kind'],'wait')
        self.assertEqual(self.app.programs['A'][0]['plan']['duration'],2)

    def test_home_button_uses_explicit_home_command(self):
        self.connect()
        self.app.home_now('D')
        self.assertEqual(self.fake.sent[-1],'HOME D')
        self.app.receive('START ALL')
        self.app.stop()
        self.app.receive('WAIT HOME')
        self.assertTrue(self.app.busy)
        self.assertEqual(self.app.phase,'stop')
        self.app.receive('DONE ALL')
        self.assertTrue(self.app.busy)
        self.app.receive('STOPPED USER')
        self.assertFalse(self.app.busy)

    def test_repeat_upload_and_counts(self):
        self.connect()
        self.add('A')
        self.add('A','CCW')
        self.app.period.set('2500')
        self.app.repeat_mode.set(True)
        self.app.prehome.set(True)
        self.app.run_program()
        self.app.receive('ACK CLEAR')
        self.app.receive('ACK ADD A 1')
        self.app.receive('ACK ADD A 2')
        self.assertEqual(self.fake.sent[-1],'CONFIG 2500000 30000 0 0 0 0 1 1 0')
        self.app.receive('ACK CONFIG')
        self.assertEqual(self.fake.sent[-1],'RUN')
        self.app.receive('START REPEAT')
        self.app.receive('COUNTS 10 10 0 0 0')
        self.assertEqual(self.app.phase,'repeat')
        self.assertTrue(self.app.busy)
        self.app.stop()
        self.assertFalse(self.app.jog_held)

    def test_unbalanced_repeat_is_disabled_during_editing(self):
        self.connect()
        self.add()
        self.app.repeat_mode.set(True)
        self.assertEqual(str(self.app.run_btn['state']),'disabled')
        self.assertIn('合計が0',self.app.summary.get())

    def test_vct_only_has_no_motor_command(self):
        self.connect()
        self.app.start_vct()
        self.assertEqual(self.fake.sent[-1],'VCT 600000 30000')
        self.app.receive('START VCT')
        self.assertEqual(self.app.phase,'vct')
        self.assertFalse(any(s.startswith(('MOVE ','HOME ','JOG ')) for s in self.fake.sent))

    def test_jog_release_stops_even_before_start_reply(self):
        self.connect()
        with patch.object(self.root,'grab_set'):
            self.app.start_jog('CW')
        self.assertTrue(self.app.jog_held)
        self.app.release_jog()
        self.assertFalse(self.app.jog_held)
        self.assertEqual(self.fake.sent[-1],'STOP')
        self.app.receive('START JOG')
        self.assertEqual(self.app.phase,'stop')

    def test_jog_focus_loss_stops(self):
        self.connect()
        with patch.object(self.root,'grab_set'):
            self.app.start_jog('CCW')
        self.app.receive('START JOG')
        with patch.object(self.root,'focus_displayof',return_value=None):
            self.app.check_jog_focus()
        self.assertEqual(self.fake.sent[-1],'STOP')

    def test_scale_button_preserves_registered_order(self):
        self.add('B')
        self.add('B','CCW')
        self.app.period.set('600')
        self.app.apply_scale()
        self.assertEqual([s['direction'] for s in self.app.programs['B']],['CW','CCW'])
        self.assertAlmostEqual(sum(s['plan']['duration'] for s in self.app.programs['B']),0.6)

    def test_period_and_bpm_are_linked_without_scaling_motion(self):
        self.add()
        before = copy.deepcopy(self.app.programs)
        self.app.bpm.set('120')
        self.assertEqual(float(self.app.period.get()),500)
        self.app.period.set('750')
        self.assertEqual(float(self.app.bpm.get()),80)
        self.assertEqual(self.app.programs,before)

    def test_invalid_bpm_does_not_leave_run_enabled(self):
        self.connect()
        self.add()
        self.app.bpm.set('0')
        self.assertEqual(str(self.app.run_btn['state']),'disabled')


if __name__ == '__main__':
    unittest.main()

