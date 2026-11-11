import ast
import bisect
import copy
import csv
import math
from collections import deque
from motion import PULSES_PER_MM, plan_move

def finite(x,lo=-1e9,hi=1e9):
    if isinstance(x,bool):raise ValueError('数値を指定してください。')
    v=float(x)
    if not math.isfinite(v) or not lo<=v<=hi:raise ValueError('数値が範囲外です（{}〜{}）。'.format(lo,hi))
    return v

def defaults():
    return {'channels':{a:dict(name=a,unit='',gain=1,method='mean',window_ms=1200,delay_ms=310,beats=1) for a in 'abcd'},
            'loops':[dict(enabled=False,axis='BCD'[i],current='bca'[i],formula='a-d' if i<2 else 'b-d',table=[],
                period_ms=7500,initial_mm=0,min_mm=None,max_mm=5.2,step_mm=0.6,speed=0.8,
                polarity=-1,kp=13,ki=0.05,kd=1,deadband=1.8,ratio=0.03,tau=5) for i in range(3)]}

class Formula:
    def __init__(self,text,current):
        if not isinstance(text,str) or len(text)>256:raise ValueError('数式は256文字までです。')
        try:self.tree=ast.parse(text,mode='eval')
        except SyntaxError:raise ValueError('数式を確認してください。')
        nodes=list(ast.walk(self.tree))
        if len(nodes)>100:raise ValueError('数式が複雑すぎます。')
        allowed=(ast.Expression,ast.BinOp,ast.UnaryOp,ast.Add,ast.Sub,ast.Mult,ast.Div,ast.UAdd,ast.USub,ast.Num,ast.Name,ast.Load)
        # Constant replaces Num in newer Python versions.
        if hasattr(ast,'Constant'):allowed=allowed+(ast.Constant,)
        for n in nodes:
            if not isinstance(n,allowed):raise ValueError('四則演算・括弧・数値・a〜dのみ使用できます。')
            if isinstance(n,ast.Name) and (n.id not in 'abcd' or len(n.id)!=1 or n.id==current):
                raise ValueError('数式には現在値と同じ系列を含めず、残りのa〜dを使用してください。')
            if isinstance(n,ast.Num):finite(n.n)
            if hasattr(ast,'Constant') and isinstance(n,ast.Constant):
                if type(n.value) not in (int,float):raise ValueError('数式の定数は数値のみです。')
                finite(n.value)
        self.names={n.id for n in nodes if isinstance(n,ast.Name)}
    def calculate(self,values):
        def calc(n):
            if isinstance(n,ast.Expression):return calc(n.body)
            if isinstance(n,ast.Num):return finite(n.n)
            if isinstance(n,ast.Name):return finite(values[n.id])
            if isinstance(n,ast.UnaryOp):return calc(n.operand)*(-1 if isinstance(n.op,ast.USub) else 1)
            a,b=calc(n.left),calc(n.right)
            if isinstance(n.op,ast.Add):r=a+b
            elif isinstance(n.op,ast.Sub):r=a-b
            elif isinstance(n.op,ast.Mult):r=a*b
            else:
                if b==0:raise ValueError('数式がゼロ除算になりました。')
                r=a/b
            return finite(r)
        return finite(calc(self.tree))

def validate_table(rows):
    if not isinstance(rows,list) or len(rows)>10000:raise ValueError('変換表は最大10000行です。')
    out=[]
    for row in rows:
        if not isinstance(row,(list,tuple)) or len(row)!=2:raise ValueError('変換表はalpha,betaの2列です。')
        pair=[finite(row[0]),finite(row[1])]
        if out and pair[0]<=out[-1][0]:raise ValueError('alphaは重複なしの昇順にしてください。')
        out.append(pair)
    if out and len(out)<2:raise ValueError('変換表には2行以上必要です。')
    return out

def read_table(path):
    with open(path,encoding='utf-8-sig',newline='') as stream:
        reader=csv.reader(stream)
        if next(reader,None)!=['alpha','beta']:raise ValueError('CSVの先頭行は alpha,beta にしてください。')
        rows=[]
        for row in reader:
            if not row:continue
            if len(rows)>=10000:raise ValueError('変換表は最大10000行です。')
            rows.append(row)
    result=validate_table(rows)
    if not result:raise ValueError('変換表が空です。')
    return result

def check_trigger_capacity(channels,period_ms):
    period=finite(period_ms,10,60000)
    pending=sum(int(math.ceil(ch['delay_ms']/period))+1 for ch in channels.values() if ch['method']=='trigger')
    if pending>120:raise ValueError('VCT後取得の予約数が上限を超えます。遅延を短くするかVCT周期を長くしてください。')
    # Conservative ASCII frame sizes; leave UART room for commands and timing variation.
    trigger_count=sum(ch['method']=='trigger' for ch in channels.values())
    if 6400+72*trigger_count*1000/period>9000:
        raise ValueError('VCT後取得の通信量が上限を超えます。取得系列を減らすかVCT周期を長くしてください。')

def lookup(rows,x):
    if not rows:raise ValueError('変換表CSVを読み込んでください。')
    i=bisect.bisect_left(rows,[x,-math.inf])
    if i==0:return rows[0][1]
    if i==len(rows):return rows[-1][1]
    a,b=rows[i-1],rows[i]
    return finite(a[1]+(b[1]-a[1])*(x-a[0])/(b[0]-a[0]))

def normalize(data):
    try:
        data=copy.deepcopy(data)
        if set(data['channels'])!=set('abcd') or len(data['loops'])!=3:raise ValueError('4系列・3制御の設定が必要です。')
        for a,ch in data['channels'].items():
            for key in ('name','unit'):
                if not isinstance(ch[key],str) or len(ch[key])>40:raise ValueError('系列名・単位は40文字までです。')
            ch['gain']=finite(ch['gain'])
            if ch['method'] not in ('mean','min','max','trigger'):raise ValueError('取得方法が不正です。')
            ch['window_ms']=finite(ch['window_ms'],10,60000)
            ch['delay_ms']=finite(ch['delay_ms'],0,60000)
            beats=finite(ch['beats'],1,100)
            if int(beats)!=beats:raise ValueError('拍平均数は整数です。')
            ch['beats']=int(beats)
        used=set();used_current=set()
        for loop in data['loops']:
            if type(loop['enabled']) is not bool:raise ValueError('有効設定が不正です。')
            if loop['axis'] not in 'ABCD' or len(loop['axis'])!=1 or loop['current'] not in 'abcd' or len(loop['current'])!=1:raise ValueError('軸・現在値を選択してください。')
            Formula(loop['formula'],loop['current'])
            loop['table']=validate_table(loop['table'])
            # V6 removes the CCW position limit, including values in old recipes.
            loop['min_mm']=None
            for key,lo,hi in [('period_ms',100,600000),('initial_mm',-6,6),('max_mm',-6,6),
                ('step_mm',0.0004,6),('speed',0.0002,20),('kp',0,1e6),('ki',0,1e6),('kd',0,1e6),
                ('deadband',0,1e9),('ratio',0,1),('tau',0,600)]:loop[key]=finite(loop[key],lo,hi)
            if loop['initial_mm']>loop['max_mm']:raise ValueError('初期位置を位置上限以下にしてください。')
            if loop['polarity'] not in (-1,1):raise ValueError('制御方向は±1です。')
            loop['polarity']=int(loop['polarity'])
            if loop['enabled']:
                if loop['axis'] in used:raise ValueError('同じ軸を複数の制御で使用できません。')
                if loop['current'] in used_current:raise ValueError('現在値の系列は制御ごとに別のものを選んでください。')
                used.add(loop['axis'])
                used_current.add(loop['current'])
        return data
    except (KeyError,TypeError,OverflowError) as exc:raise ValueError('センサー・制御設定の形式が不正です。') from exc

def control_commands(data,programs):
    data=normalize(data);commands=[];rate=0
    for i,f in enumerate(data['loops']):
        if not f['enabled']:continue
        if programs.get(f['axis']):raise ValueError('{}軸は通常運転に登録されています。'.format(f['axis']))
        if not f['table']:raise ValueError('制御{}のCSV変換表が未設定です。'.format(i+1))
        pulse=lambda mm:int(round(mm*PULSES_PER_MM))
        half=plan_move(f['axis'],'CW',0.0004,'speed',f['speed'])['min_half_us']
        rate+=(1000000+half-1)//half
        vals=[i,f['axis'],int(round(f['period_ms']*1000)),pulse(f['initial_mm']),0,pulse(f['max_mm']),pulse(f['step_mm']),half,f['polarity']]
        vals += [f[k] for k in ('kp','ki','kd','deadband','ratio','tau')]
        commands.append(('FBCFG '+' '.join(str(v) for v in vals),'ACK FBCFG '+str(i)))
    if not commands:raise ValueError('フィードバック制御を有効にしてください。')
    for rows in programs.values():
        halves=[s['plan']['min_half_us'] for s in rows if s.get('kind','move')=='move']
        if halves:rate+=(1000000+min(halves)-1)//min(halves)
    if rate>100000:raise ValueError('通常運転と制御軸の最高速度の合計を20 mm/s以下にしてください。')
    return commands

class Processor:
    def __init__(self,channels):
        self.channels=copy.deepcopy(channels);self.samples={a:deque() for a in 'abcd'}
        self.triggers={a:deque(maxlen=ch['beats']) for a,ch in channels.items()}
        self.volts={a:None for a in 'abcd'};self.clock=None;self.first=None;self.last_trigger={};self.sequence=0
    def consume(self,line):
        p=line.split()
        if p and p[0]=='S':
            if len(p)!=7:raise ValueError('計測フレームが不正です。')
            seq,t=int(p[1]),int(p[2]);raw=[int(x) for x in p[3:]]
            if any(not 0<=v<=65535 for v in raw) or t<0:raise ValueError('ADC値が不正です。')
            if self.clock is not None and (t<=self.clock or t-self.clock>50000 or seq!=self.sequence+1):raise ValueError('計測が欠落・再起動しました。再接続してください。')
            self.clock=t;self.sequence=seq
            if self.first is None:self.first=t
            for a,v in zip('abcd',raw):
                ch=self.channels[a];volts=v*3.3/65535;self.volts[a]=volts
                q=self.samples[a];q.append((t,finite(volts*ch['gain'])))
                cutoff=t-int(ch['window_ms']*1000)
                while q and q[0][0]<cutoff:q.popleft()
            return True
        if p and p[0]=='T':
            if len(p)!=6:raise ValueError('VCT計測フレームが不正です。')
            beat,ch,due,sampled,raw=map(int,p[1:])
            if not 0<=ch<4 or not 0<=raw<=65535 or beat<0 or due<0 or sampled<due or (self.clock is not None and sampled>self.clock+1000000):raise ValueError('VCT計測値が不正です。')
            a='abcd'[ch];self.triggers[a].append(raw*3.3/65535*self.channels[a]['gain']);self.last_trigger[a]=sampled
        return False
    def values(self,period_ms):
        out={}
        if self.clock is None:return out
        for a,ch in self.channels.items():
            if ch['method']=='trigger':
                q=self.triggers[a]
                ttl=int((2*period_ms+ch['delay_ms'])*1000)
                if len(q)<ch['beats'] or self.clock-self.last_trigger.get(a,0)>max(1000000,ttl):continue
                out[a]=sum(q)/len(q)
            else:
                if self.clock-self.first<int(ch['window_ms']*1000):continue
                values=[v for _,v in self.samples[a]]
                if not values:continue
                out[a]=(sum(values)/len(values) if ch['method']=='mean' else min(values) if ch['method']=='min' else max(values))
        return out
