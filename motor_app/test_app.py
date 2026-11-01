import tkinter as tk
import unittest
from unittest.mock import patch
from app import MotorApp


class FakeLink:
    def __init__(self):
        self.sent = []
        self.closed = False

    def write(self, payload):
        self.sent.append(payload.decode('ascii').strip())
        return len(payload)

    def close(self):
        self.closed = True


class AppTests(unittest.TestCase):
    def setUp(self):
        self.port_mock = patch('app.list_ports.comports', return_value=[])
        self.port_mock.start()
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = MotorApp(self.root)
        self.app.speed.set('0.1')
        self.app.duration.set('1000.000')
        self.fake = FakeLink()
        self.app.link = self.fake

    def tearDown(self):
        self.app.close()
        self.port_mock.stop()

    def connect(self):
        self.app.receive('READY SUPERMEXE02_MOTOR_V4')
        self.assertEqual(self.fake.sent, ['STOP'])
        self.app.receive('STOPPED USER')

    def add(self, axis='A', direction='CW'):
        self.app.axis.set(axis)
        self.app.direction.set(direction)
        self.app.add_step()

    def upload_two_axes(self):
        self.add('A')
        self.add('A', 'CCW')
        self.add('B')
        self.app.run_program()
        self.assertEqual(self.fake.sent[-1], 'CLEAR')
        self.app.receive('ACK CLEAR')
        self.assertEqual(self.fake.sent[-1], 'ADD A 250 2000')
        self.app.receive('ACK ADD A 1')
        self.assertEqual(self.fake.sent[-1], 'ADD A -250 2000')
        self.app.receive('ACK ADD A 2')
        self.assertEqual(self.fake.sent[-1], 'ADD B 250 2000')
        self.assertNotIn('RUN', self.fake.sent)
        self.app.receive('ACK ADD B 1')
        self.assertTrue(self.fake.sent[-1].startswith('CONFIG '))
        self.app.receive('ACK CONFIG')
        self.assertEqual(self.fake.sent[-1], 'RUN')
        self.app.receive('START ALL')

    def test_no_movement_before_handshake(self):
        self.app.move()
        self.app.run_program()
        self.assertEqual(self.fake.sent, [])

    def test_single_move_and_double_click(self):
        self.connect()
        self.app.move()
        self.app.move()
        self.assertEqual(self.fake.sent.count('MOVE A 250 2000'), 1)
        self.app.receive('START ALL')
        self.app.receive('STEP A 1 250')
        self.assertTrue(self.app.busy)
        self.app.receive('DONE ALL')
        self.assertFalse(self.app.busy)

    def test_parallel_execution_waits_for_all_steps(self):
        self.connect()
        self.upload_two_axes()
        self.app.receive('STEP B 1 250')
        self.assertTrue(self.app.busy)
        self.app.receive('STEP A 1 250')
        self.app.receive('STEP A 2 250')
        self.app.receive('DONE ALL')
        self.assertFalse(self.app.busy)
        self.assertEqual(len(self.app.programs['A']), 2)
        self.assertEqual(self.fake.sent.count('RUN'), 1)

    def test_stop_during_upload_cannot_start_remaining_motion(self):
        self.connect()
        self.add()
        self.app.run_program()
        self.app.stop()
        self.app.receive('ACK CLEAR')
        self.app.receive('STOPPED USER')
        self.assertNotIn('RUN', self.fake.sent)
        self.assertFalse(any(s.startswith('ADD ') for s in self.fake.sent))
        self.assertFalse(self.app.busy)

    def test_stop_during_run_cancels_all_axes(self):
        self.connect()
        self.upload_two_axes()
        self.app.stop()
        self.app.receive('STEP A 1 250')
        self.app.receive('DONE ALL')
        self.assertTrue(self.app.busy)  # only STOPPED acknowledges stop
        self.app.receive('STOPPED USER')
        self.assertFalse(self.app.busy)
        self.assertEqual(self.fake.sent[-1], 'STOP')

    def test_mismatched_upload_ack_aborts(self):
        self.connect()
        self.add()
        self.app.run_program()
        self.app.receive('ACK ADD B 1')
        self.assertEqual(self.fake.sent[-1], 'STOP')
        self.app.receive('ACK CLEAR')
        self.assertNotIn('RUN', self.fake.sent)

    def test_device_error_aborts_upload(self):
        self.connect()
        self.add()
        self.app.run_program()
        self.app.receive('ERR RANGE')
        self.assertEqual(self.fake.sent[-1], 'STOP')
        self.assertEqual(self.app.phase, 'stop')

    def test_incomplete_done_aborts(self):
        self.connect()
        self.upload_two_axes()
        self.app.receive('DONE ALL')
        self.assertEqual(self.fake.sent[-1], 'STOP')

    def test_reboot_aborts_and_does_not_resume(self):
        self.connect()
        self.upload_two_axes()
        self.app.receive('READY SUPERMEXE02_MOTOR_V4')
        self.app.receive('STOPPED USER')
        self.assertFalse(self.app.busy)
        self.assertEqual(self.fake.sent.count('RUN'), 1)
        self.assertEqual(self.fake.sent[-1], 'STOP')

    def test_offline_edit_reorder_and_delete(self):
        self.add('C')
        self.add('C', 'CCW')
        self.app.reorder_step(-1)
        self.assertEqual(self.app.programs['C'][0]['direction'], 'CCW')
        self.app.remove_step()
        self.assertEqual(len(self.app.programs['C']), 1)
        self.app.clear_axis()
        self.assertEqual(self.app.programs['C'], [])
        self.assertEqual(self.fake.sent, [])

    def test_edit_during_execution_is_blocked(self):
        self.connect()
        self.upload_two_axes()
        self.app.add_step()
        self.app.remove_step()
        self.app.clear_axis()
        self.assertEqual(len(self.app.programs['A']), 2)
        self.assertEqual(len(self.app.programs['B']), 1)

    def test_legacy_firmware_is_rejected(self):
        self.app.receive('READY SUPERMEXE02_MOTOR_V1')
        self.assertFalse(self.app.ready)
        self.assertTrue(self.fake.closed)

    def test_disconnect_sends_stop(self):
        self.connect()
        self.app.disconnect()
        self.assertTrue(self.fake.closed)
        self.assertEqual(self.fake.sent[-1], 'STOP')


if __name__ == '__main__':
    unittest.main()


