"""localhostだけの仮想USB + Simulink入出力。実USBポートは開かない。"""
import argparse
import heapq
import json
import socketserver
import socket
import threading
import time
from pathlib import Path
from vmcu import Board, verify_sources, ROOT

class Environment:
    def __init__(self, trace=None):
        self.sources=verify_sources()
        self.boards={name:Board(name) for name in ('motor','sensor')}
        self.lock=threading.RLock(); self.us=0; self.positions=[10000]*4
        self.clients={}; self.buffers={'motor':bytearray(),'sensor':bytearray()}
        self.relay=[]; self.vct=0; self.last_beat=None; self.period=600000
        self.attached=False; self.ever_attached=False; self.origin=0
        self.trace=open(trace,'w',encoding='utf-8') if trace else None
        self.drain()
    def record(self, kind, **data):
        if self.trace:self.trace.write(json.dumps(dict(t_us=self.us,kind=kind,**data))+'\n')
    def drain(self):
        for name,board in self.boards.items():
            data=board.output()
            if data:
                self.buffers[name].extend(data)
                # 未接続でも長時間シミュレーションで無制限に蓄積しない。
                if len(self.buffers[name])>1048576:del self.buffers[name][:-1048576]
                self.record('tx',board=name,text=data.decode('ascii'))
                if name in self.clients:
                    try:self.clients[name].sendall(data)
                    except OSError:self.clients.pop(name,None)
    def command(self,name,data):
        self.record('rx',board=name,text=data.decode('ascii',errors='replace'))
        self.boards[name].receive(data)
    def step(self,target,adc):
        if target<self.us:raise ValueError('時刻を戻すにはサーバーを再起動してください。')
        if len(adc)!=4 or any(not 0<=v<=65535 for v in adc):raise ValueError('ADCは4入力・0～65535です。')
        self.boards['sensor'].adc(adc)
        while self.us<target:
            until=min(self.us+1000,target)
            self.boards['motor'].advance(until)
            # PinNameはnative/mbed.hの列挙値。電気回路/機構の境界。
            for at,pin,value in self.boards['motor'].edges():
                self.record('gpio',edge_us=at,pin=pin,value=value)
                if value:
                    if pin in (1,9,12,6):self.positions[(1,9,12,6).index(pin)]+=1
                    if pin in (2,5,13,7):self.positions[(2,5,13,7).index(pin)]-=1
                    if pin in (10,11,4,8):heapq.heappush(self.relay,(at+1020000,'home',(10,11,4,8).index(pin),0))
                if pin==0:heapq.heappush(self.relay,(at+(5000 if value else 3000),'vct',0,value))
            while self.relay and self.relay[0][0]<=until:
                at,kind,index,value=heapq.heappop(self.relay)
                self.boards['sensor'].advance(at)
                if kind=='vct':
                    self.vct=value;self.boards['sensor'].digital(9,value)
                    if value:
                        if self.last_beat is not None:self.period=at-self.last_beat
                        self.last_beat=at
                else:self.positions[index]=0
            self.boards['sensor'].advance(until);self.us=until
        self.drain()
        age=None if self.last_beat is None else self.us-self.last_beat
        return dict(t_us=self.us,position_pulses=self.positions,vct=self.vct,beat_age_us=age,period_us=self.period)
    def request(self,req):
        op=req['op']
        if op=='clock':return dict(t_us=self.us)
        if op=='attach':
            if self.attached:raise ValueError('すでにSimulinkが接続しています。')
            self.attached=True;self.ever_attached=True;self.origin=self.us
            return dict(origin_us=self.origin)
        if op=='detach':
            self.command('motor',b'STOP\n');self.step(self.us+1000,[0]*4);self.attached=False
            return dict(ok=True)
        if op=='step':return self.step(int(req['t_us']),req['adc'])
        if op=='command':self.command(req['board'],(req['line']+'\n').encode('ascii'));return dict(ok=True)
        if op=='read':
            b=self.buffers[req['board']];s=b.decode('ascii');b.clear();return dict(text=s)
        if op=='info':return dict(t_us=self.us,attached=self.attached,sources=self.sources,axes=4,pid_loops=3,sensor_channels=4)
        raise ValueError('unknown operation')
    def close(self):
        for b in self.boards.values():b.close()
        if self.trace:self.trace.close()

class RPC(socketserver.StreamRequestHandler):
    def setup(self):
        super().setup();self.request.setsockopt(socket.IPPROTO_TCP,socket.TCP_NODELAY,1)
    def handle(self):
        for raw in self.rfile:
            try:
                with self.server.env.lock:result=self.server.env.request(json.loads(raw))
                reply=dict(ok=True,**result) if 'ok' not in result else result
            except Exception as exc:reply=dict(ok=False,error=str(exc))
            self.wfile.write((json.dumps(reply)+'\n').encode());self.wfile.flush()

class USB(socketserver.BaseRequestHandler):
    def handle(self):
        env=self.server.env;board=self.server.board
        with env.lock:
            if board in env.clients:return
            env.clients[board]=self.request
        try:
            while True:
                data=self.request.recv(8192)
                if not data:break
                with env.lock:env.command(board,data)
        finally:
            with env.lock:
                if env.clients.get(board) is self.request:env.clients.pop(board)

class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address=True;daemon_threads=True

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=48200);p.add_argument('--trace',default=str(ROOT/'build'/'trace.jsonl'))
    args=p.parse_args();env=Environment(args.trace);servers=[]
    try:
        for offset,handler,board in ((0,RPC,None),(1,USB,'motor'),(2,USB,'sensor')):
            s=Server(('127.0.0.1',args.port+offset),handler);s.env=env;s.board=board
            threading.Thread(target=s.serve_forever,daemon=True).start();servers.append(s)
        print('仮想マイコン起動: RPC={} / motor={} / sensor={}'.format(args.port,args.port+1,args.port+2),flush=True)
        # Simulink接続前だけ、GUIの接続・設定に必要な起動処理を実時間で進める。
        # 接続後の時計はSimulinkだけが進める。終了後も勝手に制御を再開しない。
        while True:
            time.sleep(0.001)
            with env.lock:
                if not env.ever_attached:env.step(env.us+1000,[0]*4)
    except KeyboardInterrupt:pass
    finally:
        for s in servers:s.shutdown();s.server_close()
        env.close()
if __name__=='__main__':main()
