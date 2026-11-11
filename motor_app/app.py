import time
import queue
import threading
import copy
import math
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import serial
from serial.tools import list_ports
from scrolling import ScrollablePage
from feedback_ui import FeedbackPanel
from settings_store import encode_settings, write_settings, read_settings
from motion import (plan_move, make_step, make_wait, make_home, compile_program,
                    MAX_STEPS_PER_AXIS, MIN_DISTANCE_MM, MAX_DISTANCE_MM, timing_config, scale_steps, jog_command)


class MotorApp:
    def __init__(self, root):
        self.root = root
        root.title('SuperMEXE02 — 軸別動作プログラム')
        width = max(760, min(1080, root.winfo_screenwidth() - 100))
        height = max(480, min(760, root.winfo_screenheight() - 120))
        root.geometry('{}x{}'.format(width, height))
        root.minsize(760, 480)
        self.link = None
        self.ready = self.busy = False
        self.rx = bytearray()
        self.port_results = queue.Queue()
        self.port_scan_busy = False
        self.last_ping = self.last_hello = self.connected_at = 0
        self.last_reply = 0
        self.start_deadline = 0
        self.programs = {axis: [] for axis in 'ABCD'}
        self.active_program = {}
        self.progress = {}
        self.phase = 'idle'
        self.pending_upload = []
        self.expected_ack = None
        self.program_run = False
        self.execution_type = 'single'
        self.jog_held = False
        self.last_jog_keep = 0
        self.port = tk.StringVar()
        self.axis = tk.StringVar(value='A')
        self.direction = tk.StringVar(value='CW')
        self.mode = tk.StringVar(value='speed')
        self.distance = tk.StringVar(value='0.1')
        self.speed = tk.StringVar(value='1.0')
        self.duration = tk.StringVar(value='100.000')
        self.kind = tk.StringVar(value='移動')
        self.repeat_mode = tk.BooleanVar(value=False)
        self.prehome = tk.BooleanVar(value=False)
        self.with_vct = tk.BooleanVar(value=False)
        self.period = tk.StringVar(value='600.000')
        self.bpm = tk.StringVar(value='100')
        self.period_sync = False
        self.pulse_width = tk.StringVar(value='30.000')
        self.delays = {a: tk.StringVar(value='0.000') for a in 'ABCD'}
        self.jog_axis = tk.StringVar(value='A')
        self.jog_speed = tk.StringVar(value='1.0')
        self.settings_folder = Path(__file__).resolve().parent.parent / 'motion_settings'
        self.settings_path = None
        self.settings_name = tk.StringVar(value='設定ファイル：未保存')
        self.status = tk.StringVar(value='未接続')
        self.preview = tk.StringVar()
        frame = ttk.Frame(root, padding=20)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text='直動アクチュエータ / 動作プログラム', font=('Yu Gothic UI', 17, 'bold')).pack(anchor='w')
        ttk.Label(frame, text='軸ごとに動作を追加 → 全軸を同時開始。各軸は登録した順に実行します。').pack(anchor='w', pady=(4, 10))
        connection = ttk.Frame(frame)
        connection.pack(fill='x')
        ttk.Label(connection, text='COMポート').pack(side='left')
        self.ports = ttk.Combobox(connection, textvariable=self.port, width=14, state='readonly')
        self.ports.pack(side='left', padx=8)
        ttk.Button(connection, text='更新', command=self.refresh_ports).pack(side='left')
        self.connect_btn = ttk.Button(connection, text='接続', command=self.toggle_connection)
        self.connect_btn.pack(side='left', padx=8)
        self.global_stop = tk.Button(connection, text='全停止', bg='#a82828', fg='white', command=self.stop, state='disabled')
        self.global_stop.pack(side='right', ipadx=20)
        files = ttk.Frame(frame)
        files.pack(fill='x', pady=(6,0))
        self.save_btn = ttk.Button(files, text='設定を保存…', command=self.save_settings)
        self.save_btn.pack(side='left')
        self.load_btn = ttk.Button(files, text='設定を開く…', command=self.load_settings)
        self.load_btn.pack(side='left', padx=8)
        ttk.Label(files, textvariable=self.settings_name).pack(side='left', padx=6)
        self.settings_buttons = [self.save_btn, self.load_btn]
        ttk.Label(frame, textvariable=self.status).pack(anchor='w', pady=10)
        pages = ttk.Notebook(frame)
        pages.pack(fill='both', expand=True)
        self.work_scroll = ScrollablePage(pages, padding=8)
        self.control_scroll = ScrollablePage(pages, padding=14)
        work_page, control_page = self.work_scroll.content, self.control_scroll.content
        pages.add(self.work_scroll, text='動作の登録・実行')
        pages.add(self.control_scroll, text='周期・ZHOME・手動操作')
        body = ttk.Frame(work_page)
        body.pack(fill='both', expand=True)
        editor = ttk.Frame(body, padding=(0, 0, 14, 0))
        editor.pack(side='left', fill='y')
        settings = ttk.LabelFrame(editor, text='追加する動作', padding=14)
        settings.pack(fill='x')
        self.controls = []
        for row, (label, var, values) in enumerate([
                ('軸', self.axis, ('A', 'B', 'C', 'D')),
                ('方向', self.direction, ('CW', 'CCW'))]):
            ttk.Label(settings, text=label).grid(row=row, column=0, sticky='w', pady=5)
            widget = ttk.Combobox(settings, textvariable=var, values=values, state='readonly', width=16)
            widget.grid(row=row, column=1, sticky='w', padx=12)
            self.controls.append((widget, 'readonly'))
        ttk.Label(settings, text='移動量 [mm]').grid(row=2, column=0, sticky='w', pady=5)
        entry = ttk.Spinbox(settings, textvariable=self.distance, width=19,
                            from_=MIN_DISTANCE_MM, to=MAX_DISTANCE_MM, increment=MIN_DISTANCE_MM, format='%.4f')
        entry.grid(row=2, column=1, sticky='w', padx=12)
        self.controls.append((entry, 'normal'))
        for row, (label, mode, var, unit) in enumerate([
                ('速度を指定', 'speed', self.speed, 'mm/s'),
                ('時間を指定', 'time', self.duration, 'ms')], start=3):
            radio = ttk.Radiobutton(settings, text=label, variable=self.mode, value=mode)
            radio.grid(row=row, column=0, sticky='w', pady=5)
            field = (ttk.Spinbox(settings, textvariable=var, width=19, from_=0.001, to=30000000,
                                 increment=0.001, format='%.3f') if mode=='time'
                     else ttk.Entry(settings, textvariable=var, width=19))
            field.grid(row=row, column=1, padx=12, sticky='w')
            ttk.Label(settings, text=unit).grid(row=row, column=2, sticky='w')
            self.controls.extend([(radio, 'normal'), (field, 'normal')])
        ttk.Label(settings, text='動作の種類').grid(row=5, column=0, sticky='w', pady=5)
        kind_box = ttk.Combobox(settings, textvariable=self.kind, values=('移動','待機','ZHOME'), state='readonly', width=16)
        kind_box.grid(row=5, column=1, padx=12)
        self.controls.append((kind_box, 'readonly'))
        ttk.Label(editor, textvariable=self.preview, wraplength=290).pack(anchor='w', pady=12)
        actions = ttk.Frame(editor)
        actions.pack(fill='x')
        self.add_btn = ttk.Button(actions, text='この動作を追加', command=self.add_step)
        self.add_btn.pack(fill='x', ipady=6)
        self.move_btn = ttk.Button(actions, text='この動作だけ実行', command=self.move, state='disabled')
        self.move_btn.pack(fill='x', pady=8, ipady=4)
        self.stop_btn = tk.Button(actions, text='停止', bg='#a82828', fg='white',
                                  font=('Yu Gothic UI', 12, 'bold'), command=self.stop,
                                  state='disabled', width=12)
        self.stop_btn.pack(fill='x', ipady=5)
        program_panel = ttk.LabelFrame(body, text='軸ごとの動作（各軸32件まで）', padding=12)
        program_panel.pack(side='left', fill='both', expand=True)
        self.notebook = ttk.Notebook(program_panel)
        self.notebook.pack(fill='both', expand=True)
        self.trees = {}
        for axis in 'ABCD':
            tab = ttk.Frame(self.notebook)
            self.notebook.add(tab, text=axis + '軸 (0)')
            tree = ttk.Treeview(tab, columns=('n','direction','distance','mode','value','seconds'), show='headings', height=9)
            for col, title, width in [('n','#',35),('direction','方向',55),('distance','移動量 mm',90),
                                      ('mode','指定',55),('value','速度/時間',115),('seconds','指令時間 ms',115)]:
                tree.heading(col, text=title)
                tree.column(col, width=width, minwidth=30, anchor='center')
            bar = ttk.Scrollbar(tab, orient='vertical', command=tree.yview)
            tree.configure(yscrollcommand=bar.set)
            bar.pack(side='right', fill='y')
            tree.pack(side='left', fill='both', expand=True)
            tree.tag_configure('done', foreground='#167338')
            self.trees[axis] = tree
        self.notebook.bind('<<NotebookTabChanged>>', self.tab_changed)
        row_actions = ttk.Frame(program_panel)
        row_actions.pack(fill='x', pady=10)
        self.program_controls = []
        for text, action in [('上へ', lambda: self.reorder_step(-1)), ('下へ', lambda: self.reorder_step(1)),
                             ('選択を削除', self.remove_step), ('この軸をクリア', self.clear_axis)]:
            button = ttk.Button(row_actions, text=text, command=action)
            button.pack(side='left', padx=(0, 5))
            self.program_controls.append(button)
        self.summary = tk.StringVar()
        ttk.Label(program_panel, textvariable=self.summary, wraplength=620).pack(anchor='w', pady=8)
        self.run_btn = ttk.Button(program_panel, text='登録した動作を実行（全軸同時開始）', command=self.run_program, state='disabled')
        self.run_btn.pack(fill='x', ipady=9)
        self.build_controls(control_page)
        self.feedback = FeedbackPanel(self,pages)
        self.work_scroll.install_wheel_bindings()
        self.control_scroll.install_wheel_bindings()
        info = ttk.Label(frame, text='最小移動0.0004 mm（1パルス）/ 時間入力0.001 ms刻み。1動作6 mm / 合計速度20 mm/sまで。\n'
                  '表示は指令値です。停止はパルス出力の停止で、非常停止装置の代わりにはなりません。',
                  wraplength=1000)
        info.pack(side='bottom', fill='x', pady=(6,3), before=pages)
        self.log = tk.Text(frame, height=2, state='disabled', font=('Consolas', 10))
        self.log.pack(side='bottom', fill='x', before=pages)
        for var in (self.axis, self.direction, self.mode, self.distance, self.speed, self.duration, self.kind):
            var.trace_add('write', self.update_preview)
        for var in (self.repeat_mode, self.prehome, self.with_vct, self.pulse_width, *self.delays.values()):
            var.trace_add('write', lambda *_: self.update_run_button())
        self.period.trace_add('write', self.period_changed)
        self.bpm.trace_add('write', self.bpm_changed)
        self.refresh_ports()
        self.refresh_programs()
        self.update_preview()
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.bind_all('<ButtonRelease-1>', self.release_jog, add='+')
        root.bind('<FocusOut>', lambda _: root.after_idle(self.check_jog_focus), add='+')
        root.bind('<Unmap>', self.release_jog, add='+')
        root.after(100, self.poll)

    def capture_settings(self):
        data = encode_settings(self.programs, self.period.get(), self.pulse_width.get(),
                               self.delay_values(), self.prehome.get(), self.repeat_mode.get(),
                               self.with_vct.get(), self.jog_axis.get(), self.jog_speed.get())
        data['sensor_feedback']=self.feedback.capture()
        return data

    def settings_directory(self, create=False):
        if self.settings_path and self.settings_path.parent.is_dir():
            return self.settings_path.parent
        if create:
            self.settings_folder.mkdir(parents=True, exist_ok=True)
        return self.settings_folder if self.settings_folder.is_dir() else self.settings_folder.parent

    def note_settings_path(self, path):
        self.settings_path = Path(path)
        name = self.settings_path.name
        self.settings_name.set('設定ファイル：' + (name if len(name)<=44 else name[:41]+'…'))

    def save_settings(self):
        if self.busy:
            return
        try:
            data = self.capture_settings()
            path = filedialog.asksaveasfilename(parent=self.root, title='動作設定を保存',
                initialdir=str(self.settings_directory(create=True)),
                initialfile=self.settings_path.name if self.settings_path else '動作設定.json',
                defaultextension='.json', filetypes=[('SuperMEXE02設定 (JSON)','*.json'),('すべてのファイル','*.*')],
                confirmoverwrite=True)
            if not path:
                return
            write_settings(path, data)
            self.note_settings_path(path)
            self.append('設定を保存：' + str(path))
        except (ValueError, OSError) as exc:
            messagebox.showerror('保存できません', str(exc), parent=self.root)

    def load_settings(self):
        if self.busy:
            return
        try:
            path = filedialog.askopenfilename(parent=self.root, title='動作設定を開く',
                initialdir=str(self.settings_directory()),
                filetypes=[('SuperMEXE02設定 (JSON)','*.json'),('すべてのファイル','*.*')])
            if not path:
                return
            state = read_settings(path)  # Validate everything before changing current settings.
            self.feedback.apply(state['sensor_feedback'])
            self.programs = state['programs']
            self.period_sync = True
            try:
                self.period.set('{:.3f}'.format(state['period_us']/1000))
                self.bpm.set('{:.9g}'.format(60000000/state['period_us']))
                self.pulse_width.set('{:.3f}'.format(state['width_us']/1000))
                for axis in 'ABCD':
                    self.delays[axis].set('{:.3f}'.format(state['delays_us'][axis]/1000))
                self.repeat_mode.set(state['repeat'])
                self.prehome.set(state['prehome'])
                self.with_vct.set(state['with_vct'])
                self.jog_axis.set(state['jog_axis'])
                self.jog_speed.set(str(state['jog_speed']))
            finally:
                self.period_sync = False
            self.refresh_programs()
            self.update_preview()
            self.note_settings_path(path)
            self.append('設定を読み込み：' + str(path) + '（実行は開始していません）')
        except (ValueError, OSError) as exc:
            messagebox.showerror('読み込めません', str(exc), parent=self.root)

    def build_controls(self, panel):
        timing = ttk.LabelFrame(panel, text='実行モード / 共通VCT（PA_0）', padding=12)
        timing.pack(fill='x')
        for col, (text, variable) in enumerate([('周期で繰り返す', self.repeat_mode),
                ('最初にZHOME（登録がある軸）', self.prehome), ('通常実行でもVCTを出す', self.with_vct)]):
            box = ttk.Checkbutton(timing, text=text, variable=variable)
            box.grid(row=0, column=col, sticky='w', padx=6, pady=6)
            self.controls.append((box, 'normal'))
        for row, (text, variable) in enumerate([('周期 [ms]', self.period), ('VCTパルス幅 [ms]', self.pulse_width)], 1):
            ttk.Label(timing, text=text).grid(row=row, column=0, sticky='w', padx=6, pady=5)
            entry = ttk.Spinbox(timing, textvariable=variable, width=16,
                                from_=10 if variable is self.period else 1, to=60000, increment=0.001, format='%.3f')
            entry.grid(row=row, column=1, sticky='w')
            self.controls.append((entry, 'normal'))
        self.vct_btn = ttk.Button(timing, text='VCTだけ開始（全停止で終了）', command=self.start_vct, state='disabled')
        bpm_frame = ttk.Frame(timing)
        bpm_frame.grid(row=1, column=2, sticky='w', padx=8)
        ttk.Label(bpm_frame, text='bpm').pack(side='left', padx=4)
        entry = ttk.Entry(bpm_frame, textvariable=self.bpm, width=12)
        entry.pack(side='left')
        self.controls.append((entry,'normal'))
        self.vct_btn.grid(row=2, column=2, padx=8)
        offsets = ttk.Frame(timing)
        offsets.grid(row=3, column=0, columnspan=3, sticky='w', pady=8)
        for axis in 'ABCD':
            ttk.Label(offsets, text=axis+'開始遅延 [ms]').pack(side='left', padx=(6,3))
            entry = ttk.Spinbox(offsets, textvariable=self.delays[axis], width=10,
                                from_=0, to=600000, increment=0.001, format='%.3f')
            entry.pack(side='left')
            self.controls.append((entry, 'normal'))
        ttk.Label(timing, text='繰り返し時は共通VCTが自動で出ます。動作時間 ≤ 周期、各軸の移動合計 = 0 が必要です。').grid(row=4, column=0, columnspan=3, sticky='w', pady=5)
        scale = ttk.Button(timing, text='選択中の軸の時間を周期に合わせる（スケール適用）', command=self.apply_scale)
        scale.grid(row=5, column=0, columnspan=3, sticky='ew', pady=6)
        self.program_controls.append(scale)
        homing = ttk.LabelFrame(panel, text='原点復帰：20 ms信号 + 1秒待機（実位置の完了確認なし）', padding=12)
        homing.pack(fill='x', pady=12)
        self.home_buttons = []
        for axis in 'ABCD':
            button = ttk.Button(homing, text=axis+' ZHOME', command=lambda a=axis: self.home_now(a), state='disabled')
            button.pack(side='left', padx=8)
            self.home_buttons.append(button)
        ttk.Label(panel, text='Cのリレー入力はA6（PA_7）、DはD5（PA_15）へ分離してください。\n'
                  'ZHOMEは開始後にアプリから中断できません。全停止後も待機終了まで次の操作を受け付けません。').pack(anchor='w')
        jog = ttk.LabelFrame(panel, text='手動操作：押している間だけ動作', padding=12)
        jog.pack(fill='x', pady=12)
        axis_box = ttk.Combobox(jog, textvariable=self.jog_axis, values=tuple('ABCD'), state='readonly', width=5)
        axis_box.pack(side='left', padx=6)
        ttk.Label(jog, text='速度 [mm/s]').pack(side='left')
        entry = ttk.Entry(jog, textvariable=self.jog_speed, width=12)
        entry.pack(side='left', padx=6)
        self.controls.extend([(axis_box,'readonly'), (entry,'normal')])
        self.jog_buttons = []
        for direction in ('CW', 'CCW'):
            button = ttk.Button(jog, text=direction+'（長押し）', state='disabled')
            button.pack(side='left', padx=8)
            button.bind('<ButtonPress-1>', lambda e, d=direction: self.start_jog(d))
            self.jog_buttons.append(button)
        ttk.Label(panel, text='手動操作中は移動量の制限を使いません。可動範囲を確認して操作してください。').pack(anchor='w')

    def period_changed(self, *_):
        if self.period_sync:
            return
        self.period_sync = True
        try:
            value = float(self.period.get())
            self.bpm.set('{:.9g}'.format(60000/value) if math.isfinite(value) and value > 0 else '')
        except (ValueError, OverflowError):
            self.bpm.set('')
        finally:
            self.period_sync = False
        self.update_run_button()

    def bpm_changed(self, *_):
        if self.period_sync:
            return
        self.period_sync = True
        try:
            value = float(self.bpm.get())
            self.period.set('{:.3f}'.format(60000/value) if math.isfinite(value) and value > 0 else '')
        except (ValueError, OverflowError):
            self.period.set('')
        finally:
            self.period_sync = False
        self.update_run_button()

    def append(self, text):
        self.log.configure(state='normal')
        self.log.insert('end', time.strftime('%H:%M:%S ') + text + '\n')
        if int(self.log.index('end-1c').split('.')[0]) > 300:
            self.log.delete('1.0', '80.0')
        self.log.see('end')
        self.log.configure(state='disabled')

    def refresh_ports(self):
        if self.link or self.port_scan_busy:
            return
        self.port_scan_busy = True
        self.status.set('COMポートを検索中…')
        def scan():
            try:
                self.port_results.put(([p.device for p in list_ports.comports()], None))
            except Exception as exc:
                self.port_results.put(([], str(exc)))
        threading.Thread(target=scan, daemon=True).start()

    def finish_port_scan(self, values, error):
        self.port_scan_busy = False
        if self.link:
            return
        self.ports['values'] = values
        if self.port.get() not in values:
            self.port.set(values[0] if values else '')
        self.status.set('未接続' if values else 'COMポートなし：ボードを接続し「更新」を押してください。')
        if error:
            self.append('COMポート検索エラー：' + error)

    def current_step(self):
        if self.kind.get() == '待機':
            return make_wait(self.axis.get(), float(self.duration.get())/1000)
        if self.kind.get() == 'ZHOME':
            return make_home(self.axis.get())
        return make_step(self.axis.get(), self.direction.get(), self.distance.get(),
                         self.mode.get(), self.speed.get() if self.mode.get() == 'speed' else float(self.duration.get())/1000)

    def plan(self):
        return self.current_step()['plan']

    def update_preview(self, *_):
        try:
            step = self.current_step()
            p = step['plan']
            if step['kind'] == 'wait':
                self.preview.set('待機：{:.3f} ms（「時間を指定」の入力欄を使用）'.format(p['duration']*1000))
            elif step['kind'] == 'home':
                self.preview.set('ZHOME：20 ms信号 → OFF → 1秒待機。中断できません。')
            else:
                self.preview.set('指令値：{:.4f} mm / {:.4f} mm/s / {:.3f} ms（{}パルス）'.format(
                    p['distance'], p['speed'], p['duration']*1000, p['pulses']))
            valid = True
        except (ValueError, OverflowError) as exc:
            self.preview.set(str(exc))
            valid = False
        self.move_btn.configure(state='normal' if valid and self.ready and not self.busy else 'disabled')
        self.add_btn.configure(state='normal' if valid and not self.busy else 'disabled')
        self.update_run_button()

    def selected_axis(self):
        return 'ABCD'[self.notebook.index(self.notebook.select())]

    def tab_changed(self, _=None):
        self.axis.set(self.selected_axis())

    def refresh_programs(self, selection=None):
        for axis, tree in self.trees.items():
            tree.delete(*tree.get_children())
            for number, step in enumerate(self.programs[axis]):
                p = step['plan']
                kind = step.get('kind','move')
                tree.insert('', 'end', iid=str(number), values=(number+1, step['direction'] if kind=='move' else {'wait':'待機','home':'ZHOME'}[kind],
                    '{:.4f}'.format(p['distance']) if kind=='move' else '-',
                    '速度' if step['mode']=='speed' else ('固定' if kind=='home' else '時間'),
                    ('{} mm/s'.format(step['value']) if step['mode']=='speed' else '{:.3f} ms'.format(step['value']*1000)),
                    '{:.3f}'.format(p['duration']*1000)))
            self.notebook.tab('ABCD'.index(axis), text='{}軸 ({})'.format(axis, len(self.programs[axis])))
        if selection:
            axis, number = selection
            self.notebook.select('ABCD'.index(axis))
            self.trees[axis].selection_set(str(number))
            self.trees[axis].see(str(number))
        self.update_run_button()

    def update_run_button(self):
        try:
            self.config_command()
            _, total, duration = compile_program(self.programs, self.repeat_mode.get(), self.period.get(), self.delay_values())
            totals = ' / '.join('{}: {:.3f}ms'.format(a, sum(s['plan']['duration'] for s in self.programs[a])*1000) for a in 'ABCD')
            self.summary.set(('周期{} msで繰り返し / '.format(self.period.get()) if self.repeat_mode.get() else '') +
                             '{}動作 / 初回完了目安{:.3f} ms\n{}'.format(total, (duration + (1.02 if self.prehome.get() else 0))*1000, totals))
            valid = True
        except ValueError as exc:
            self.summary.set(str(exc))
            valid = False
        self.run_btn.configure(state='normal' if valid and self.ready and not self.busy else 'disabled')
        for button in self.home_buttons + self.jog_buttons + [self.vct_btn]:
            button.configure(state='normal' if self.ready and not self.busy else 'disabled')

    def delay_values(self):
        return {axis: variable.get() for axis, variable in self.delays.items()}

    def config_command(self):
        return timing_config(self.period.get(), self.pulse_width.get(), self.delay_values(),
                             self.prehome.get(), self.repeat_mode.get(), self.with_vct.get())

    def apply_scale(self):
        if self.busy:
            return
        try:
            axis = self.selected_axis()
            scaled = scale_steps(self.programs[axis], self.period.get())
            candidate = dict(self.programs, **{axis: scaled})
            compile_program(candidate)
            self.programs[axis] = scaled
            self.refresh_programs()
        except (ValueError, OverflowError) as exc:
            messagebox.showerror('スケール適用できません', str(exc))

    def add_step(self):
        if self.busy:
            return
        axis = self.axis.get()
        try:
            if len(self.programs[axis]) >= MAX_STEPS_PER_AXIS:
                raise ValueError('各軸の動作は32件までです。')
            step = self.current_step()
            self.programs[axis].append(step)
            self.refresh_programs((axis, len(self.programs[axis])-1))
        except (ValueError, OverflowError) as exc:
            messagebox.showerror('入力を確認してください', str(exc))

    def remove_step(self):
        if self.busy:
            return
        axis = self.selected_axis()
        selected = sorted((int(i) for i in self.trees[axis].selection()), reverse=True)
        for index in selected:
            self.programs[axis].pop(index)
        self.refresh_programs()

    def reorder_step(self, delta):
        if self.busy:
            return
        axis = self.selected_axis()
        selected = self.trees[axis].selection()
        if len(selected) != 1:
            return
        index = int(selected[0])
        target = index + delta
        if 0 <= target < len(self.programs[axis]):
            rows = self.programs[axis]
            rows[index], rows[target] = rows[target], rows[index]
            self.refresh_programs((axis, target))

    def clear_axis(self):
        if not self.busy:
            self.programs[self.selected_axis()] = []
            self.refresh_programs()

    def write(self, text):
        if not self.link:
            raise serial.SerialException('未接続です。')
        payload = (text + '\n').encode('ascii')
        if self.link.write(payload) != len(payload):
            raise serial.SerialException('送信できませんでした。')

    def toggle_connection(self):
        if self.link:
            self.disconnect()
            return
        if not self.port.get():
            messagebox.showinfo('接続', 'ボードを接続し、COMポートを選んでください。')
            return
        device = serial.Serial(port=None, baudrate=115200, timeout=0, write_timeout=0.2)
        try:
            device.dtr = False
            device.rts = False
            device.port = self.port.get()
            device.open()
            device.reset_input_buffer()
            self.link = device
            self.ready = False
            self.rx.clear()
            self.connected_at = time.monotonic()
            self.last_reply = self.connected_at
            self.last_hello = self.last_ping = 0
            self.status.set('接続中：motor用ファームウェアを確認しています…')
            self.connect_btn.configure(text='切断')
            self.ports.configure(state='disabled')
        except (serial.SerialException, OSError) as exc:
            device.close()
            messagebox.showerror('接続できません', str(exc))

    def set_busy(self, value):
        self.busy = value
        if hasattr(self,'feedback'):self.feedback.set_busy(value)
        for control, normal_state in self.controls:
            control.configure(state='disabled' if value else normal_state)
        for control in self.program_controls:
            control.configure(state='disabled' if value else 'normal')
        for control in self.settings_buttons:
            control.configure(state='disabled' if value else 'normal')
        self.update_preview()

    def reset_execution(self, preserve_feedback_position=False):
        self.pending_upload = []
        self.expected_ack = None
        self.start_deadline = 0
        self.phase = 'idle'
        if hasattr(self,'feedback'):
            if not preserve_feedback_position:self.feedback.clear_motor_positions()
            self.feedback.accepting=False
        self.jog_held = False
        try:
            self.root.grab_release()
        except tk.TclError:
            pass

    def disconnect(self):
        if self.link:
            try:
                if self.ready:
                    self.write('STOP')
            except (serial.SerialException, OSError):
                pass
            self.link.close()
        self.link = None
        self.ready = False
        self.reset_execution()
        self.set_busy(False)
        self.stop_btn.configure(state='disabled')
        self.global_stop.configure(state='disabled')
        self.ports.configure(state='readonly')
        self.connect_btn.configure(text='接続')
        self.status.set('未接続')

    def move(self):
        if self.busy or not self.ready:
            return
        try:
            self.execute_step(self.current_step())
        except (ValueError, OverflowError) as exc:
            messagebox.showerror('入力を確認してください', str(exc))
        except (serial.SerialException, OSError) as exc:
            self.connection_error(exc)

    def execute_step(self, step):
        self.begin_execution({step['axis']: [step]})
        self.execution_type = 'single'
        self.phase = 'start'
        cmd = step['plan']['command'].replace('WAIT ', 'WAITNOW ', 1).replace('ZHADD ', 'HOME ', 1)
        self.send_request(cmd)

    def home_now(self, axis):
        if self.busy or not self.ready:
            return
        try:
            self.execute_step(make_home(axis))
        except (serial.SerialException, OSError) as exc:
            self.connection_error(exc)

    def start_vct(self):
        if self.busy or not self.ready:
            return
        try:
            parts = self.config_command()[0].split()
            self.begin_execution({})
            self.execution_type = 'vct'
            self.phase = 'start'
            self.send_request('VCT {} {}'.format(parts[1], parts[2]))
        except (ValueError, OverflowError) as exc:
            messagebox.showerror('VCT設定を確認してください', str(exc))
        except (serial.SerialException, OSError) as exc:
            self.connection_error(exc)

    def start_jog(self, direction):
        if self.busy or not self.ready:
            return
        try:
            command = jog_command(self.jog_axis.get(), direction, self.jog_speed.get())
            self.begin_execution({})
            self.execution_type = 'jog'
            self.jog_held = True
            self.last_jog_keep = 0
            self.phase = 'start'
            self.root.grab_set()
            self.send_request(command)
        except (ValueError, OverflowError) as exc:
            messagebox.showerror('手動操作の設定を確認してください', str(exc))
        except (serial.SerialException, OSError) as exc:
            self.connection_error(exc)

    def release_jog(self, _=None):
        if self.jog_held:
            self.stop()

    def check_jog_focus(self):
        if self.jog_held and self.root.focus_displayof() is None:
            self.stop()

    def begin_execution(self, program, program_run=False):
        self.reset_execution()
        self.program_run = program_run
        self.active_program = copy.deepcopy(program)
        self.progress = {axis: 0 for axis in program}
        for tree in self.trees.values():
            for item in tree.get_children():
                tree.item(item, tags=())
        self.write('PING')
        self.set_busy(True)

    def send_request(self, command, ack=None):
        self.expected_ack = ack
        self.start_deadline = time.monotonic() + 2
        self.write(command)
        self.append('送信 ' + command)

    def run_program(self):
        if self.busy or not self.ready:
            return
        try:
            commands, _, _ = compile_program(self.programs, self.repeat_mode.get(), self.period.get(), self.delay_values())
            commands.append(self.config_command())
            self.begin_execution(self.programs, program_run=True)
            self.execution_type = 'repeat' if self.repeat_mode.get() else 'program'
            self.pending_upload = list(commands)
            self.phase = 'upload'
            self.send_next_upload()
        except (ValueError, OverflowError) as exc:
            messagebox.showerror('動作を確認してください', str(exc))
        except (serial.SerialException, OSError) as exc:
            self.connection_error(exc)

    def start_feedback(self, feedback_commands):
        if self.busy or not self.ready:
            raise ValueError('motorを接続し、停止してから開始してください。')
        if any(self.programs.values()):
            commands, _, _ = compile_program(self.programs,self.repeat_mode.get(),self.period.get(),self.delay_values())
        else:
            commands=[('CLEAR','ACK CLEAR')]
        commands += feedback_commands + [self.config_command()]
        self.begin_execution(self.programs,program_run=True)
        self.execution_type='feedback'
        self.pending_upload=list(commands)
        self.phase='upload'
        self.send_next_upload()

    def send_next_upload(self):
        if self.phase != 'upload':
            return
        if self.pending_upload:
            command, ack = self.pending_upload.pop(0)
            self.status.set('動作を転送中…')
            self.send_request(command, ack)
        else:
            self.phase = 'start'
            self.status.set('全軸の開始応答待ち…')
            self.send_request('FBRUN' if self.execution_type=='feedback' else 'RUN')

    def stop(self):
        if self.ready:
            try:
                self.reset_execution()
                self.phase = 'stop'
                self.set_busy(True)
                self.start_deadline = time.monotonic() + 2
                self.write('STOP')
                self.status.set('全軸の停止応答待ち…')
            except (serial.SerialException, OSError) as exc:
                self.connection_error(exc)

    def receive(self, line):
        self.last_reply = time.monotonic()
        if line.startswith('FBSTAT '):
            if self.ready and self.execution_type=='feedback' and self.phase in ('feedback','stop'):
                self.feedback.motor_line(line)
            return
        if line=='FBREADY ALL':
            if self.execution_type=='feedback' and self.phase=='feedback' and self.busy:
                try:
                    self.feedback.reset_measurements()
                    self.status.set('初期位置移動完了。計測窓を取り直し、有効値が揃ってからPIDを開始します。')
                except OSError as exc:
                    self.append('計測を再開できません：'+str(exc));self.stop()
            return
        if line == 'PONG':
            return
        self.append(line)
        if line.startswith('READY ') and line != 'READY SUPERMEXE02_MOTOR_V6':
            self.connection_error('motor用の新しいコード（V6）を書き込んでください。')
        elif line == 'READY SUPERMEXE02_MOTOR_V6':
            # Establish an idle session before enabling controls.
            self.write('STOP')
            self.ready = True
            self.reset_execution()
            self.phase = 'sync'
            self.start_deadline = time.monotonic() + 2
            self.set_busy(True)
            self.status.set('接続確認・停止応答待ち…')
            self.stop_btn.configure(state='normal')
            self.global_stop.configure(state='normal')
        elif self.ready and line.startswith('ACK '):
            if self.phase == 'upload':
                if line != self.expected_ack:
                    self.append('転送応答が一致しないため中止します。')
                    self.stop()
                else:
                    self.start_deadline = 0
                    self.send_next_upload()
        elif self.ready and line in ('START ALL', 'START REPEAT', 'START VCT', 'START JOG','START FEEDBACK'):
            if self.phase != 'start':
                if self.phase != 'stop':
                    self.stop()
                return
            self.start_deadline = 0
            self.phase = {'START ALL':'run','START REPEAT':'repeat','START VCT':'vct','START JOG':'jog','START FEEDBACK':'feedback'}[line]
            self.status.set({'run':'実行中（ZHOMEを含む場合は固定待機します）', 'repeat':'VCT同期で繰り返し中',
                             'vct':'VCT出力中', 'jog':'手動操作中：ボタンを離すと停止','feedback':'ZHOME・初期位置への移動中。完了後にPIDを開始します。'}[self.phase])
        elif self.ready and line == 'WAIT HOME':
            self.jog_held = False
            self.start_deadline = time.monotonic() + 2
            self.status.set('原点復帰待機中：次の操作は待機終了後に有効になります。')
        elif self.ready and line.startswith('COUNTS '):
            if self.phase in ('repeat','vct','feedback'):
                parts = line.split()
                if len(parts) == 6 and all(p.isdigit() for p in parts[1:]):
                    self.status.set('VCT {}回 / 周期開始 A:{} B:{} C:{} D:{}'.format(*parts[1:]))
        elif self.ready and line.startswith('STEP '):
            if self.phase not in ('run','feedback'):
                return
            try:
                _, axis, number, pulses = line.split()
                number, pulses = int(number), int(pulses)
                rows = self.active_program[axis]
                if number != self.progress[axis] + 1 or number > len(rows) or pulses != rows[number-1]['plan']['pulses']:
                    raise ValueError('step mismatch')
                self.progress[axis] = number
                tree = self.trees[axis]
                if self.program_run and tree.exists(str(number-1)):
                    tree.item(str(number-1), tags=('done',))
                self.status.set('実行中：' + ' / '.join('{} {}/{}'.format(a, self.progress[a], len(self.active_program[a]))
                                                       for a in 'ABCD' if self.active_program.get(a)))
            except (ValueError, KeyError):
                self.append('動作完了の応答が一致しないため中止します。')
                self.stop()
        elif self.ready and line == 'DONE ALL':
            if self.phase != 'run':
                return
            if any(self.progress[a] != len(rows) for a, rows in self.active_program.items()):
                self.append('未完了の動作があるため中止します。')
                self.stop()
                return
            self.reset_execution()
            self.set_busy(False)
            self.status.set('すべての指令パルス出力が完了しました。')
        elif self.ready and line.startswith('STOPPED '):
            self.reset_execution(preserve_feedback_position=True)
            self.set_busy(False)
            self.status.set('停止・待機中（残りの動作も中止）' if line == 'STOPPED USER' else '全軸停止：' + line)
        elif line.startswith('ERR '):
            self.stop()
            self.status.set('装置エラー：' + line)

    def connection_error(self, exc):
        self.append('通信エラー：' + str(exc))
        self.disconnect()
        self.status.set('通信切断。motor側は通信途絶を検出するとパルス出力を停止します。')

    def poll(self):
        try:
            values, error = self.port_results.get_nowait()
            self.finish_port_scan(values, error)
        except queue.Empty:
            pass
        if self.link:
            try:
                now = time.monotonic()
                if now - self.last_ping >= 0.2:
                    self.write('PING')
                    self.last_ping = now
                if self.jog_held and now - self.last_jog_keep >= 0.1:
                    self.write('JKEEP')
                    self.last_jog_keep = now
                if not self.ready and now - self.last_hello >= 0.5:
                    self.write('HELLO')
                    self.last_hello = now
                if not self.ready and now - self.connected_at > 5:
                    raise serial.SerialException('対応ファームウェアの応答がありません。motor用コードを書き込んでください。')
                self.rx.extend(self.link.read(min(self.link.in_waiting, 4096)))
                while b'\n' in self.rx:
                    raw, _, remainder = self.rx.partition(b'\n')
                    self.rx = bytearray(remainder)
                    self.receive(raw.decode('ascii', errors='replace').strip())
                if len(self.rx) > 4096:
                    raise serial.SerialException('受信データが不正です。')
                if self.start_deadline and now > self.start_deadline:
                    self.stop()
                    raise serial.SerialException('指令の応答がありません。全軸の停止指令を送信しました。')
                if self.ready and now - self.last_reply > 3:
                    raise serial.SerialException('装置からの通信応答が途絶えました。')
            except (serial.SerialException, OSError) as exc:
                self.connection_error(exc)
        self.root.after(50, self.poll)

    def close(self):
        self.disconnect()
        self.feedback.close()
        self.root.destroy()


if __name__ == '__main__':
    root = tk.Tk()
    ttk.Style().theme_use('clam')
    MotorApp(root)
    root.mainloop()
