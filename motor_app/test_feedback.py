import copy
import json
import tempfile
import queue
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import test_app
from feedback_model import defaults,normalize,Formula,lookup,read_table,Processor,control_commands,check_trigger_capacity
from motion import make_step
from settings_store import decode_settings
from test_settings import sample_settings
from sensor_stream import SensorStream


class SensorLinkTests(unittest.TestCase):
    def setUp(self):
        class Device:
            def __init__(self):self.rx=queue.Queue();self.sent=[];self.closed=False
            def open(self):pass
            def close(self):self.closed=True
            def reset_input_buffer(self):pass
            @property
            def in_waiting(self):return 1
            def write(self,data):self.sent.append(data)
            def read(self,count):
                try:return self.rx.get(timeout=.01)
                except queue.Empty:return b''
        self.device=Device()
        self.mock=patch('sensor_stream.serial.Serial',return_value=self.device);self.mock.start()
        self.stream=SensorStream('FAKE',defaults()['channels'])

    def tearDown(self):self.stream.close();self.mock.stop()

    def wait_for(self,condition):
        end=time.monotonic()+1
        while not condition() and time.monotonic()<end:time.sleep(.005)
        self.assertTrue(condition())

    def test_handshake_and_reset_discard_old_measurements(self):
        self.device.rx.put(b'READY SUPERMEXE02_SENSOR_V1\nACK SENSORCFG\nS 1 10000 0 0 0 0\n')
        self.wait_for(lambda:self.stream.last_raw>0)
        self.assertTrue(self.stream.snapshot(600)['fresh'])
        self.stream.reset_processing()
        self.assertFalse(self.stream.snapshot(600)['ready'])
        self.assertIsNone(self.stream.processor.clock)
        self.device.rx.put(b'S 2 20000 0 0 0 0\nACK SENSORCFG\nS 100 1000000 0 0 0 0\n')
        self.wait_for(lambda:self.stream.processor.clock==1000000)
        self.assertEqual(self.stream.processor.first,1000000)
        self.assertTrue(self.device.sent[-1].startswith(b'SENSORCFG '))

    def test_wrong_board_is_rejected_and_closed(self):
        self.device.rx.put(b'READY SUPERMEXE02_MOTOR_V6\n')
        self.wait_for(lambda:self.device.closed)
        self.assertTrue(self.stream.error)
        self.assertFalse(self.stream.ready)

    def test_startup_corruption_resynchronizes_before_handshake(self):
        self.device.rx.put(b'\x9a\xff\npartial startup\nREADY SUPERMEXE02_SENSOR_V1\nACK SENSORCFG\nS 1 10000 0 0 0 0\n')
        self.wait_for(lambda:self.stream.last_raw>0)
        self.assertTrue(self.stream.ready)
        self.assertEqual(self.stream.error,'')

    def test_corruption_after_handshake_is_still_a_fault(self):
        self.device.rx.put(b'READY SUPERMEXE02_SENSOR_V1\nACK SENSORCFG\nS 1 10000 0 0 0 0\n\x9a\n')
        self.wait_for(lambda:self.device.closed)
        self.assertIn('文字化け',self.stream.error)
        self.assertFalse(self.stream.ready)

    def test_unrequested_config_ack_cannot_skip_ready_validation(self):
        self.device.rx.put(b'ACK SENSORCFG\n')
        self.wait_for(lambda:self.device.rx.empty())
        self.assertFalse(self.stream.ready)
        self.device.rx.put(b'READY SUPERMEXE02_SENSOR_V1\nACK SENSORCFG\nS 1 10000 0 0 0 0\n')
        self.wait_for(lambda:self.stream.last_raw>0)
        self.assertTrue(self.stream.ready)

    def test_missing_first_sample_is_reported(self):
        self.device.rx.put(b'READY SUPERMEXE02_SENSOR_V1\nACK SENSORCFG\n')
        self.wait_for(lambda:self.stream.ready)
        self.stream.ready_since=time.monotonic()-2
        self.assertTrue(self.stream.snapshot(600)['error'])

    def test_lost_sample_sequence_is_a_fault(self):
        self.device.rx.put(b'READY SUPERMEXE02_SENSOR_V1\nACK SENSORCFG\nS 1 10000 0 0 0 0\nS 3 30000 0 0 0 0\n')
        self.wait_for(lambda:self.device.closed)
        self.assertIn('欠落',self.stream.error)


class FeedbackModelTests(unittest.TestCase):
    def test_formula_and_current_exclusion(self):
        self.assertEqual(Formula('(a-d)*2/(c+1)','b').calculate(dict(a=10,b=3,c=1,d=2)),8)
        for text in ('a+b','__import__("os")','a**2','[a]','"123"','True'):
            with self.assertRaises(ValueError):Formula(text,'b')
        with self.assertRaises(ValueError):Formula('a/d','b').calculate(dict(a=1,d=0))

    def test_csv_interpolation_and_endpoints(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'table.csv';path.write_text('alpha,beta\n0,10\n10,30\n',encoding='utf-8-sig')
            rows=read_table(path)
            self.assertEqual(lookup(rows,5),20)
            self.assertEqual(lookup(rows,-1),10)
            self.assertEqual(lookup(rows,20),30)
            path.write_text('alpha,beta\n1,2\n1,3',encoding='utf-8')
            with self.assertRaises(ValueError):read_table(path)

    def test_processor_window_gain_and_negative_max(self):
        channels=defaults()['channels']
        for ch in channels.values():ch['window_ms']=20
        channels['a']['gain']=2
        channels['b']['gain']=-1;channels['b']['method']='max'
        channels['c']['method']='min'
        p=Processor(channels)
        for seq,raw in [(1,0),(2,32768),(3,65535)]:p.consume('S {} {} {} {} {} {}'.format(seq,(seq-1)*10000,raw,raw,raw,raw))
        values=p.values(600)
        self.assertAlmostEqual(values['a'],3.3,places=4)
        self.assertEqual(values['b'],0)
        self.assertEqual(values['c'],0)
        with self.assertRaises(ValueError):p.consume('S 5 30000 0 0 0 0')

    def test_trigger_averaging_and_expiry(self):
        channels=defaults()['channels'];channels['a'].update(method='trigger',beats=2)
        p=Processor(channels);p.consume('S 1 10000 0 0 0 0')
        p.consume('T 1 0 10000 10001 0')
        self.assertNotIn('a',p.values(600))
        p.consume('T 2 0 20000 20001 65535')
        self.assertEqual(p.values(600)['a'],1.65)
        for seq in range(2,203):p.consume('S {} {} 0 0 0 0'.format(seq,seq*10000))
        self.assertNotIn('a',p.values(600))

    def test_initial_position_and_gains_are_in_controller_packet(self):
        data=defaults();f=data['loops'][0];f.update(enabled=True,axis='C',initial_mm=4,table=[[0,0],[100,100]])
        commands=control_commands(data,dict(A=[],B=[],C=[],D=[]))
        parts=commands[0][0].split()
        self.assertEqual(parts[:6],['FBCFG','0','C','7500000','10000','0'])
        self.assertEqual(len(parts),16)
        self.assertEqual(commands[0][1],'ACK FBCFG 0')
        with self.assertRaises(ValueError):control_commands(data,dict(C=[make_step('C','CW',.1,'speed',1)]))

    def test_invalid_initial_position_axis_and_current_duplicates(self):
        data=defaults();data['loops'][0]['initial_mm']=6
        with self.assertRaises(ValueError):normalize(data)
        data=defaults()
        for loop in data['loops'][:2]:loop.update(enabled=True,axis='C')
        with self.assertRaises(ValueError):normalize(data)
        data=defaults();data['loops'][0]['enabled']=True;data['loops'][1].update(enabled=True,current='b')
        with self.assertRaises(ValueError):normalize(data)

    def test_legacy_lower_position_limit_is_removed_on_load_and_save(self):
        data=sample_settings();cfg=defaults()
        for loop in cfg['loops']:loop['min_mm']=-5.2
        data['sensor_feedback']=cfg
        state=decode_settings(data)
        self.assertTrue(all(f['min_mm'] is None for f in state['sensor_feedback']['loops']))
        self.assertEqual(normalize(state['sensor_feedback']),state['sensor_feedback'])

    def test_backward_compatible_settings_and_table_embedding(self):
        data=sample_settings()
        self.assertFalse(any(f['enabled'] for f in decode_settings(data)['sensor_feedback']['loops']))
        cfg=defaults();cfg['loops'][0]['table']=[[0,1],[2,3]];data['sensor_feedback']=cfg
        restored=decode_settings(json.loads(json.dumps(data)))
        self.assertEqual(restored['sensor_feedback']['loops'][0]['table'],[[0,1],[2,3]])

    def test_trigger_reservation_capacity(self):
        cfg=defaults()['channels'];cfg['a'].update(method='trigger',delay_ms=630)
        check_trigger_capacity(cfg,600)
        for ch in cfg.values():ch.update(method='trigger',delay_ms=60000)
        with self.assertRaises(ValueError):check_trigger_capacity(cfg,600)

    def test_trigger_bandwidth_is_checked_before_start(self):
        cfg=defaults()['channels']
        for ch in cfg.values():ch.update(method='trigger',delay_ms=0)
        check_trigger_capacity(cfg,600)
        with self.assertRaises(ValueError):check_trigger_capacity(cfg,10)


class FeedbackGuiTests(unittest.TestCase):
    setUp=test_app.AppTests.setUp
    tearDown=test_app.AppTests.tearDown
    connect=test_app.AppTests.connect

    def test_feedback_upload_starts_only_after_all_acks(self):
        self.connect();self.app.programs={a:[] for a in 'ABCD'}
        self.app.start_feedback([('FBCFG example','ACK FBCFG 0')])
        self.assertEqual(self.fake.sent[-1],'CLEAR')
        self.app.receive('ACK CLEAR');self.assertEqual(self.fake.sent[-1],'FBCFG example')
        self.app.receive('ACK FBCFG 0');self.assertTrue(self.fake.sent[-1].startswith('CONFIG '))
        self.app.receive('ACK CONFIG');self.assertEqual(self.fake.sent[-1],'FBRUN')
        self.app.receive('START FEEDBACK');self.assertEqual(self.app.phase,'feedback')
        self.app.receive('FBREADY ALL')
        self.app.receive('FBSTAT 0 4 10000')
        self.assertEqual(self.app.feedback.motor_states[0],(4,10000))

    def test_saved_feedback_is_restored_without_starting_or_connecting(self):
        self.app.feedback.tables[0]=[[0,0],[10,20]]
        self.app.feedback.loops[0]['initial_mm'].set('1.25')
        data=self.app.capture_settings();state=decode_settings(data)
        self.app.feedback.apply(state['sensor_feedback'])
        self.assertEqual(self.app.feedback.loops[0]['initial_mm'].get(),'1.25')
        self.assertIsNone(self.app.feedback.stream)
        self.assertEqual(self.fake.sent,[])

    def test_sensor_only_refresh_does_not_send_motor_commands(self):
        self.app.feedback.pump()
        self.assertEqual(self.fake.sent,[])
        self.assertEqual(len(self.app.feedback.value_table.get_children()),4)

    def test_sensor_fault_stops_feedback_but_not_idle_monitor(self):
        class FakeStream:
            def snapshot(self,period):return dict(fresh=False,values={},volts={},error='unplugged',ready=False)
            def close(self):pass
        self.connect();self.app.feedback.stream=FakeStream();self.app.execution_type='feedback';self.app.phase='feedback';self.app.set_busy(True)
        self.app.feedback.tick()
        self.assertEqual(self.fake.sent[-1],'STOP')

    def test_processed_target_and_current_are_fed_and_plotted(self):
        class FakeStream:
            def snapshot(self,period):return dict(fresh=True,values=dict(a=100,b=10,c=20,d=20),volts={},error='',ready=True)
            def close(self):pass
        self.connect();panel=self.app.feedback
        panel.config['loops'][0].update(enabled=True,axis='C',current='b',formula='a-d',table=[[0,0],[100,50]])
        panel.stream=FakeStream();self.app.execution_type='feedback';self.app.phase='feedback';self.app.set_busy(True)
        panel.accepting=True
        panel.pump()
        self.assertEqual(panel.outputs[0],(40,10))
        self.assertEqual(self.fake.sent[-1],'FBVAL 0 40 10')
        self.assertEqual(panel.history[0][-1][1:],(40,10))

    def test_feedback_graph_history_is_bounded(self):
        panel=self.app.feedback
        for i in range(1000):panel.history[0].append((i,1,2))
        self.assertEqual(len(panel.history[0]),600)

    def test_position_beyond_old_lower_limit_is_displayed_and_preserved_on_stop(self):
        self.connect();self.app.execution_type='feedback';self.app.phase='feedback';self.app.set_busy(True)
        self.app.receive('FBSTAT 0 4 -20000')
        self.assertIn('-8.0000 mm',self.app.feedback.position_labels[0].get())
        self.app.stop()
        self.app.receive('FBSTAT 0 0 -20300');self.app.receive('STOPPED USER')
        self.assertEqual(self.app.feedback.motor_states[0],(0,-20300))
        self.assertIn('-8.1200 mm',self.app.feedback.position_labels[0].get())
        self.assertIn('停止',self.app.feedback.position_labels[0].get())
        self.app.feedback.loops[0]['axis'].set('D')
        self.assertIn('-- mm',self.app.feedback.position_labels[0].get())
        self.app.disconnect();self.assertIn('-- mm',self.app.feedback.position_labels[0].get())

    def test_homing_position_is_not_presented_as_known(self):
        self.app.feedback.motor_line('FBSTAT 1 1 0')
        self.assertIn('-- mm',self.app.feedback.position_labels[1].get())
        self.assertIn('原点復帰中',self.app.feedback.position_labels[1].get())

    def test_loading_old_file_does_not_restore_lower_limit_or_old_position(self):
        cfg=defaults()
        for f in cfg['loops']:f['min_mm']=-5.2
        self.app.feedback.motor_line('FBSTAT 0 4 -20000')
        self.app.feedback.apply(cfg)
        self.assertNotIn('min_mm',self.app.feedback.loops[0])
        self.assertIsNone(self.app.feedback.capture()['loops'][0]['min_mm'])
        self.assertIn('-- mm',self.app.feedback.position_labels[0].get())

    def test_measurements_are_gated_until_initial_motion_finishes(self):
        class FakeStream:
            def __init__(self):self.resets=0
            def snapshot(self,period):return dict(fresh=True,values=dict(a=100,b=10,c=20,d=20),volts={},error='',ready=True)
            def reset_processing(self):self.resets+=1
            def close(self):pass
        self.connect();panel=self.app.feedback;panel.stream=FakeStream()
        panel.config['loops'][0].update(enabled=True,current='b',formula='a-d',table=[[0,0],[100,50]])
        self.app.execution_type='feedback';self.app.phase='feedback';self.app.set_busy(True)
        panel.pump();self.assertFalse(any(s.startswith('FBVAL') for s in self.fake.sent))
        self.app.receive('FBREADY ALL');self.assertEqual(panel.stream.resets,1)
        panel.pump();self.assertEqual(self.fake.sent[-1],'FBVAL 0 40 10')
        self.app.stop();self.app.receive('FBREADY ALL');self.assertEqual(panel.stream.resets,1)

    def test_sensor_reconfiguration_wait_does_not_stop_the_run(self):
        class FakeStream:
            def snapshot(self,period):return dict(fresh=False,values={},volts={},error='',ready=False,configuring=True)
            def close(self):pass
        self.connect();self.app.feedback.stream=FakeStream()
        self.app.execution_type='feedback';self.app.phase='feedback';self.app.set_busy(True)
        count=len(self.fake.sent);self.app.feedback.pump()
        self.assertEqual(len(self.fake.sent),count)


if __name__=='__main__':unittest.main()
