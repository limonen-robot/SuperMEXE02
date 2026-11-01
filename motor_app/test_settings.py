import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import test_app
from motion import make_step, make_wait, make_home
from settings_store import encode_settings, decode_settings, read_settings, write_settings


def sample_settings():
    programs = {a: [] for a in 'ABCD'}
    programs['A'] = [make_step('A','CW',0.0004,'time',0.000021), make_wait('A',0.000001), make_home('A')]
    programs['D'] = [make_step('D','CCW',0.1,'speed',1)]
    return encode_settings(programs,600.001,30.001,{'B':0.001},True,False,True,'D','1.5')


class SettingsStoreTests(unittest.TestCase):
    def test_json_roundtrip_keeps_pulses_and_microseconds(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'往復の設定.json'
            write_settings(path,sample_settings())
            state = read_settings(path)
            self.assertEqual(state['period_us'],600001)
            self.assertEqual(state['delays_us']['B'],1)
            self.assertEqual(state['programs']['A'][0]['plan']['command'],'MOVET A 1 21')
            self.assertEqual(state['programs']['A'][1]['plan']['duration_us'],1)
            self.assertEqual(state['programs']['A'][2]['kind'],'home')
            self.assertEqual(state['programs']['D'][0]['direction'],'CCW')
            self.assertEqual(state['jog_speed'],1.5)
            data = json.loads(path.read_text(encoding='utf-8'))
            self.assertNotIn('command',data['axes']['A'][0])

    def test_empty_or_incomplete_recipe_can_be_saved_as_draft(self):
        data = sample_settings()
        data['timing']['repeat'] = True
        decode_settings(data)  # Not yet a runnable repeat; saving an editing draft is allowed.
        data['axes'] = {a: [] for a in 'ABCD'}
        self.assertTrue(decode_settings(data)['repeat'])

    def test_invalid_schema_version_conversion_and_types_are_rejected(self):
        mutations = [lambda d: d.update(version=2), lambda d: d.update(version=True),
                     lambda d: d.update(pulses_per_mm=5000),
                     lambda d: d['axes']['A'][0].update(pulses=True),
                     lambda d: d['timing'].update(period_us=600000.1),
                     lambda d: d['timing'].update(repeat='false'),
                     lambda d: d['jog'].update(speed_mm_s=float('nan')),
                     lambda d: d['axes']['A'].append({'type':'execute','command':'RUN'}),
                     lambda d: d['axes'].update(E=[])]
        for change in mutations:
            data = sample_settings()
            change(data)
            with self.assertRaises(ValueError):
                decode_settings(data)

    def test_existing_file_survives_failed_replace(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'settings.json'
            write_settings(path,sample_settings())
            before = path.read_bytes()
            data = sample_settings()
            data['timing']['period_us'] = 700001
            with patch('settings_store.os.replace',side_effect=PermissionError('locked')):
                with self.assertRaises(PermissionError):
                    write_settings(path,data)
            self.assertEqual(path.read_bytes(),before)
            self.assertEqual(list(Path(folder).iterdir()),[path])

    def test_corrupt_and_oversized_files_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'bad.json'
            for raw in (b'{broken', b'x'*(1024*1024+1)):
                path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    read_settings(path)


class SettingsGuiTests(unittest.TestCase):
    setUp = test_app.AppTests.setUp
    tearDown = test_app.AppTests.tearDown
    connect = test_app.AppTests.connect
    add = test_app.AppTests.add

    def test_native_save_and_load_restore_settings_without_device_commands(self):
        self.connect()
        self.add('A')
        self.add('A','CCW')
        self.app.period.set('2500.001')
        self.app.pulse_width.set('30.001')
        self.app.delays['D'].set('0.001')
        self.app.repeat_mode.set(True)
        self.app.prehome.set(True)
        self.app.with_vct.set(True)
        self.app.jog_axis.set('B')
        self.app.jog_speed.set('1.23')
        expected = self.app.capture_settings()
        sent = list(self.fake.sent)
        with tempfile.TemporaryDirectory() as folder:
            self.app.settings_folder = Path(folder)/'motion_settings'
            target = Path(folder)/'好きな名前.json'
            with patch('app.filedialog.asksaveasfilename',return_value=str(target)) as dialog, patch('app.messagebox.showerror') as error:
                self.app.save_settings()
                self.assertEqual(error.call_args, None)
                self.assertEqual(dialog.call_args[1]['defaultextension'],'.json')
                self.assertTrue(dialog.call_args[1]['confirmoverwrite'])
                self.assertEqual(dialog.call_args[1]['initialdir'],str(self.app.settings_folder))
            self.app.programs = {a: [] for a in 'ABCD'}
            self.app.period.set('600')
            with patch('app.filedialog.askopenfilename',return_value=str(target)), patch('app.messagebox.showerror') as error:
                self.app.load_settings()
                self.assertEqual(error.call_args, None)
            self.assertEqual(self.app.capture_settings(),expected)
            self.assertEqual(self.fake.sent,sent)
            self.assertFalse(self.app.busy)
            self.assertEqual(self.app.period.get(),'2500.001')
            self.assertEqual(self.app.settings_path,target)

    def test_cancel_does_not_change_settings(self):
        self.add()
        before = copy.deepcopy(self.app.programs)
        with tempfile.TemporaryDirectory() as folder:
            self.app.settings_folder = Path(folder)
            with patch('app.filedialog.asksaveasfilename',return_value=''):
                self.app.save_settings()
            with patch('app.filedialog.askopenfilename',return_value=''):
                self.app.load_settings()
            self.assertEqual(list(Path(folder).iterdir()),[])
        self.assertEqual(self.app.programs,before)
        self.assertIsNone(self.app.settings_path)
        self.assertEqual(self.fake.sent,[])

    def test_invalid_load_preserves_current_settings(self):
        self.add('C')
        before = self.app.capture_settings()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/'broken.json'
            data = sample_settings()
            data['axes']['D'] = [{'type':'move','pulses':0,'mode':'time','duration_us':100,'direction':'CW'}]
            path.write_text(json.dumps(data),encoding='utf-8')
            with patch('app.filedialog.askopenfilename',return_value=str(path)), patch('app.messagebox.showerror') as error:
                self.app.load_settings()
                error.assert_called_once()
        self.assertEqual(self.app.capture_settings(),before)
        self.assertEqual(self.fake.sent,[])

    def test_file_dialogs_are_disabled_while_running(self):
        self.app.set_busy(True)
        with patch('app.filedialog.asksaveasfilename') as save, patch('app.filedialog.askopenfilename') as load:
            self.app.save_settings()
            self.app.load_settings()
            save.assert_not_called()
            load.assert_not_called()
        self.assertEqual(str(self.app.save_btn['state']),'disabled')
        self.assertEqual(str(self.app.load_btn['state']),'disabled')


if __name__ == '__main__':
    unittest.main()
