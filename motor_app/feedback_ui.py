import copy
import csv
import time
import threading
import queue
import tkinter as tk
from tkinter import ttk,messagebox,filedialog
from serial.tools import list_ports
from collections import deque
from scrolling import ScrollablePage
from feedback_model import defaults,normalize,Formula,lookup,read_table,control_commands,check_trigger_capacity
from sensor_stream import SensorStream

class FeedbackPanel:
    def __init__(self,app,pages):
        self.app=app;self.stream=None;self.config=defaults();self.tables=[[],[],[]]
        self.controls=[];self.history=[deque(maxlen=600) for _ in range(3)]
        self.last_values={};self.outputs={};self.motor_states={};self.last_feed=0;self.started_at=time.monotonic()
        self.port_results=queue.Queue();self.scanning=False
        self.accepting=False
        self.sensor_page=ScrollablePage(pages,padding=12);pages.add(self.sensor_page,text='センサー設定')
        self.fb_page=ScrollablePage(pages,padding=12);pages.add(self.fb_page,text='フィードバック')
        self.monitor_page=ScrollablePage(pages,padding=12);pages.add(self.monitor_page,text='モニター')
        sensor=self.sensor_page.content
        row=ttk.Frame(sensor);row.pack(fill='x',pady=8)
        self.port=tk.StringVar();self.sensor_status=tk.StringVar(value='sensor未接続')
        ttk.Label(row,text='sensor用COM').pack(side='left')
        self.port_box=ttk.Combobox(row,textvariable=self.port,state='readonly',width=14);self.port_box.pack(side='left',padx=8)
        ttk.Button(row,text='ポート更新',command=self.refresh_ports).pack(side='left')
        self.connect_btn=ttk.Button(row,text='センサー接続・設定適用',command=self.toggle);self.connect_btn.pack(side='left',padx=8)
        ttk.Label(sensor,textvariable=self.sensor_status,wraplength=900).pack(anchor='w')
        ttk.Label(sensor,text='a=PA_0 / b=PA_1 / c=PA_4 / d=PA_5。motor PA_0 → sensor PB_0、GND共通。\n'
                  '取得100 Hz。gainは1 Vあたりの倍率。窓10〜60000 ms、VCT後0〜60000 ms、拍平均1〜100。').pack(anchor='w',pady=10)
        grid=ttk.Frame(sensor);grid.pack(anchor='w')
        keys=('name','unit','gain','method','window_ms','delay_ms','beats')
        for col,title in enumerate(('系列/ピン','表示名','単位','倍率 / V','方法','窓 ms','VCT後 ms','拍平均')):
            ttk.Label(grid,text=title).grid(row=0,column=col,padx=6,pady=6)
        self.channels={}
        for row,a in enumerate('abcd',1):
            self.channels[a]={};ttk.Label(grid,text=a).grid(row=row,column=0)
            for col,key in enumerate(keys,1):
                var=tk.StringVar(value=str(self.config['channels'][a][key]));self.channels[a][key]=var
                widget=(ttk.Combobox(grid,textvariable=var,values=('mean','max','min','trigger'),state='readonly',width=9)
                        if key=='method' else ttk.Entry(grid,textvariable=var,width=11))
                widget.grid(row=row,column=col,padx=4,pady=5);self.controls.append((widget,'readonly' if key=='method' else 'normal'))
        ttk.Label(sensor,text='mean=移動平均 / max=窓MAX / min=窓MIN / trigger=VCT後の値。設定変更後は一度切断して再接続してください。').pack(anchor='w',pady=10)
        fb=self.fb_page.content
        ttk.Label(fb,text='PIDはmotor側で計算します。ZHOME → 初期位置 → 計測準備 → PIDの順に開始します。').pack(anchor='w',pady=8)
        tabs=ttk.Notebook(fb);tabs.pack(fill='both',expand=True)
        self.loops=[];self.table_labels=[];self.position_labels=[]
        labels=[('axis','出力軸'),('current','現在値系列'),('formula','αの数式'),('period_ms','PID周期 ms'),
                ('initial_mm','初期位置 mm'),('min_mm','CCW方向の位置下限'),('max_mm','位置上限 mm'),('step_mm','1回の最大補正 mm'),
                ('speed','移動速度 mm/s'),('polarity','制御方向 ±1'),('kp','Kp [pulse/単位]'),('ki','Ki [pulse/(単位·s)]'),
                ('kd','Kd [pulse·s/単位]'),('deadband','最小デッドバンド'),('ratio','デッドバンド比率'),('tau','微分LPF時定数 s')]
        for i in range(3):
            panel=ttk.Frame(tabs,padding=10);tabs.add(panel,text='制御'+str(i+1))
            variables={};self.loops.append(variables)
            enabled=tk.BooleanVar(value=False);variables['enabled']=enabled
            box=ttk.Checkbutton(panel,text='この制御を有効にする',variable=enabled);box.grid(row=0,column=0,columnspan=4,sticky='w');self.controls.append((box,'normal'))
            for index,(key,label) in enumerate(labels):
                r=index//2+1;c=(index%2)*2
                ttk.Label(panel,text=label).grid(row=r,column=c,sticky='w',padx=8,pady=5)
                if key=='min_mm':
                    ttk.Label(panel,text='制限なし').grid(row=r,column=c+1,sticky='w',padx=8)
                    continue
                var=tk.StringVar(value=str(self.config['loops'][i][key]));variables[key]=var
                values=tuple('ABCD') if key=='axis' else tuple('abcd') if key=='current' else ('-1','1') if key=='polarity' else None
                widget=ttk.Combobox(panel,textvariable=var,values=values,state='readonly',width=15) if values else ttk.Entry(panel,textvariable=var,width=20)
                widget.grid(row=r,column=c+1,sticky='ew',padx=8);self.controls.append((widget,'readonly' if values else 'normal'))
            position=tk.StringVar(value='指令積算位置：-- mm（原点未確定）');self.position_labels.append(position)
            ttk.Label(panel,textvariable=position,font=('Yu Gothic UI',12,'bold')).grid(row=9,column=0,columnspan=4,sticky='w',padx=8,pady=8)
            variables['axis'].trace_add('write',lambda *args,n=i:self.clear_motor_positions(n))
            button=ttk.Button(panel,text='変換表CSVを読み込む',command=lambda n=i:self.load_csv(n));button.grid(row=10,column=0,columnspan=2,sticky='w',pady=10);self.controls.append((button,'normal'))
            text=tk.StringVar(value='変換表：未設定');self.table_labels.append(text);ttk.Label(panel,textvariable=text).grid(row=10,column=2,columnspan=2,sticky='w')
        self.start_btn=ttk.Button(fb,text='フィードバック＋登録済み通常運転を開始',command=self.start);self.start_btn.pack(fill='x',pady=12,ipady=8)
        ttk.Label(fb,text='制御方向 −1：正方向移動で現在値が減る（締めるほど流量が減る場合）。+1：増える場合。\n'
                  'CSVはUTF-8、先頭 alpha,beta、alpha昇順・重複なし・2点以上。線形補間し、範囲外は端の値。\n'
                  '通常運転に登録済みの軸は制御に使えません。位置は指令上の値です。').pack(anchor='w')
        monitor=self.monitor_page.content
        self.value_table=ttk.Treeview(monitor,columns=('name','volts','value','method'),show='headings',height=4)
        for key,title in [('name','系列・表示名'),('volts','電圧 V'),('value','処理済み現在値'),('method','取得方法')]:self.value_table.heading(key,text=title)
        self.value_table.pack(fill='x',pady=8)
        self.loop_table=ttk.Treeview(monitor,columns=('id','alpha','target','current','error','position','state'),show='headings',height=3)
        for key,title in [('id','制御'),('alpha','α'),('target','β / 目標値'),('current','現在値'),('error','目標−現在'),('position','指令位置 mm'),('state','状態')]:self.loop_table.heading(key,text=title);self.loop_table.column(key,width=120)
        self.loop_table.pack(fill='x',pady=8)
        self.charts=[]
        for i in range(3):
            ttk.Label(monitor,text='制御{}：青=目標値 / 赤=処理済み現在値（直近120秒、5 Hz描画）'.format(i+1)).pack(anchor='w')
            canvas=tk.Canvas(monitor,height=165,width=850,bg='white',highlightthickness=1);canvas.pack(fill='x',pady=6);self.charts.append(canvas)
        for page in (self.sensor_page,self.fb_page,self.monitor_page):page.install_wheel_bindings()
        app.root.after(200,self.tick)

    def refresh_ports(self):
        if self.stream or self.scanning:return
        self.scanning=True
        def scan():
            try:self.port_results.put([p.device for p in list_ports.comports()])
            except OSError:self.port_results.put([])
        threading.Thread(target=scan,daemon=True).start()
    def capture(self):
        data={'channels':{a:{k:v.get() for k,v in values.items()} for a,values in self.channels.items()},'loops':[]}
        for i,variables in enumerate(self.loops):
            row={k:v.get() for k,v in variables.items()};row['polarity']=int(row['polarity']);row['table']=copy.deepcopy(self.tables[i]);data['loops'].append(row)
        return normalize(data)
    def apply(self,data):
        data=normalize(data)
        if self.stream:self.stream.close();self.stream=None;self.connect_btn.configure(text='センサー接続・設定適用')
        for a,ch in data['channels'].items():
            for k,v in ch.items():self.channels[a][k].set(str(v))
        for i,loop in enumerate(data['loops']):
            for k,v in loop.items():
                if k in self.loops[i]:self.loops[i][k].set(v if k=='enabled' else str(v))
            self.tables[i]=loop['table'];self.table_labels[i].set('変換表：{}点'.format(len(self.tables[i])))
        self.config=data;self.outputs={};self.last_values={};self.clear_motor_positions();self.sensor_status.set('設定を復元しました。センサーは未接続です。')
        for q in self.history:q.clear()
    def load_csv(self,i):
        if self.app.busy:return
        path=filedialog.askopenfilename(parent=self.app.root,title='alpha,beta変換表',filetypes=[('UTF-8 CSV','*.csv')])
        if not path:return
        try:self.tables[i]=read_table(path);self.table_labels[i].set('{}点 / {}'.format(len(self.tables[i]),path.rsplit('/',1)[-1]))
        except (ValueError,OSError,csv.Error) as exc:messagebox.showerror('CSVを読めません',str(exc))
    def toggle(self):
        if self.stream:
            if self.app.execution_type=='feedback' and self.app.busy:self.app.stop()
            self.stream.close();self.stream=None;self.connect_btn.configure(text='センサー接続・設定適用');self.sensor_status.set('sensor未接続');return
        if self.app.busy:return
        try:
            if not self.port.get():raise ValueError('sensorのCOMポートを選んでください。')
            if self.app.link and self.port.get()==self.app.port.get():raise ValueError('motorとは別のCOMポートを選んでください。')
            self.config=self.capture();check_trigger_capacity(self.config['channels'],self.app.period.get())
            self.stream=SensorStream(self.port.get(),self.config['channels'])
            self.connect_btn.configure(text='センサー切断');self.sensor_status.set('sensor接続中…')
            for q in self.history:q.clear()
        except (ValueError,OSError) as exc:messagebox.showerror('センサー接続できません',str(exc))
    def start(self):
        try:
            if not self.stream or not self.stream.ready:raise ValueError('sensorを先に接続してください。')
            config=self.capture()
            check_trigger_capacity(config['channels'],self.app.period.get())
            if config['channels']!=self.config['channels']:raise ValueError('取得設定が変わっています。センサーを切断・再接続してください。')
            commands=control_commands(config,self.app.programs);self.config=config
            self.app.start_feedback(commands)
        except (ValueError,OSError) as exc:messagebox.showerror('制御を開始できません',str(exc))
    def set_busy(self,busy):
        for widget,state in self.controls:widget.configure(state='disabled' if busy else state)
        self.start_btn.configure(state='disabled' if busy else 'normal')
    def motor_line(self,line):
        p=line.split()
        try:
            if len(p)==4 and p[0]=='FBSTAT' and 0<=int(p[1])<3:
                i,state,position=map(int,p[1:]);self.motor_states[i]=(state,position)
                name={0:'停止',1:'原点復帰中',2:'初期位置へ移動中',3:'計測待ち',4:'PID中',5:'原点未確定'}.get(state,'状態不明')
                self.position_labels[i].set('指令積算位置：{}（{}）'.format('-- mm' if state in (1,5) else '{:.4f} mm / {} pulse'.format(position/2500,position),name))
        except ValueError:pass
    def clear_motor_positions(self,i=None):
        if i is None:self.motor_states.clear();labels=self.position_labels
        else:self.motor_states.pop(i,None);labels=[self.position_labels[i]]
        for text in labels:text.set('指令積算位置：-- mm（原点未確定）')
    def reset_measurements(self):
        self.accepting=True;self.outputs={}
        if self.stream:self.stream.reset_processing()
    def draw(self,i):
        canvas=self.charts[i]
        if not canvas.winfo_ismapped():return
        canvas.delete('plot');points=list(self.history[i]);w=max(canvas.winfo_width(),200);h=165
        if not points:return
        ys=[v for _,r,c in points for v in (r,c) if v is not None]
        if not ys:return
        low,high=min(ys),max(ys);pad=max((high-low)*0.1,abs(high)*0.02,0.01);low-=pad;high+=pad
        right=time.monotonic();left=right-120
        canvas.create_text(5,8,text='{:.4g}'.format(high),anchor='nw',tags='plot')
        canvas.create_text(5,h-8,text='{:.4g}'.format(low),anchor='sw',tags='plot')
        for series,color in [(1,'#2369b9'),(2,'#c34343')]:
            coords=[]
            for point in points:
                if point[series] is None:
                    if len(coords)>=4:canvas.create_line(*coords,fill=color,tags='plot')
                    coords=[];continue
                coords += [55+(point[0]-left)/120*(w-65),10+(high-point[series])/(high-low)*(h-20)]
            if len(coords)>=4:canvas.create_line(*coords,fill=color,tags='plot')
    def tick(self):
        try:
            self.pump()
        except (ValueError,KeyError,OSError) as exc:
            self.sensor_status.set(str(exc))
            if self.app.busy and self.app.execution_type=='feedback':self.app.stop()
        self.app.root.after(200,self.tick)
    def pump(self):
        try:
            ports=self.port_results.get_nowait();self.scanning=False;self.port_box['values']=ports
            if self.port.get() not in ports:self.port.set(next((p for p in ports if p!=self.app.port.get()),ports[0] if ports else ''))
        except queue.Empty:pass
        period=float(self.app.period.get() or 600)
        snap=self.stream.snapshot(period) if self.stream else dict(fresh=False,values={},volts={},error='',ready=False)
        self.last_values=snap['values'];now=time.monotonic()
        if self.stream:self.sensor_status.set(snap['error'] or ('計測中 / 有効値 '+','.join(snap['values']) if snap['fresh'] else '接続確認・計測待ち'))
        self.value_table.delete(*self.value_table.get_children());self.loop_table.delete(*self.loop_table.get_children())
        for a,ch in self.config['channels'].items():
            fmt=lambda v:'--' if v is None else '{:.6g}'.format(v)
            self.value_table.insert('','end',values=(a+' / '+ch['name'],fmt(snap['volts'].get(a)),fmt(snap['values'].get(a))+' '+ch['unit'],ch['method']))
        self.outputs={}
        for i,f in enumerate(self.config['loops']):
            target=current=alpha=None;state='無効' if not f['enabled'] else '計測待ち'
            if f['enabled']:
                formula=Formula(f['formula'],f['current'])
                if snap['fresh'] and (formula.names|{f['current']})<=set(snap['values']):
                    alpha=formula.calculate(snap['values']);target=lookup(f['table'],alpha);current=snap['values'][f['current']]
                    self.outputs[i]=(target,current);state='入力準備完了'
            position=self.motor_states.get(i,(0,None))[1]
            if i in self.motor_states:
                code=self.motor_states[i][0]
                state={0:'停止',1:'原点復帰中',2:'初期移動中',3:'計測待ち',4:'PID中',5:'原点未確定'}.get(code,'初期化中')
                if code in (1,5):position=None
            fmt=lambda x:'--' if x is None else '{:.6g}'.format(x)
            self.loop_table.insert('','end',values=(i+1,fmt(alpha),fmt(target),fmt(current),fmt(None if target is None else target-current),fmt(None if position is None else position/2500),state))
            self.history[i].append((now,target,current))
            while self.history[i] and self.history[i][0][0]<now-120:self.history[i].popleft()
            self.draw(i)
        if self.app.busy and self.app.execution_type=='feedback':
            if not snap['fresh'] and self.app.phase=='feedback' and self.stream and (snap['error'] or (not snap['ready'] and not snap.get('configuring',False))):raise ValueError('計測通信が停止しました。')
            if self.app.phase=='feedback' and self.accepting:
                for i,(target,current) in self.outputs.items():self.app.write('FBVAL {} {:.8g} {:.8g}'.format(i,target,current))
    def close(self):
        if self.stream:self.stream.close();self.stream=None
