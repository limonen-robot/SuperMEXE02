"""元のmotor_app/app.pyを使い、通信とアプリの監視時計だけ仮想環境へ接続。"""
import argparse
import importlib
import json
import socket
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from virtual_serial import install

class SimulationTime:
    def __init__(self,port):
        self.value=10.0;self.port=port;self.stopping=threading.Event();self.error=''
        deadline=time.monotonic()+15
        while True:
            try:self.update();break
            except OSError:
                if time.monotonic()>=deadline:raise
                time.sleep(0.1)
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def update(self):
        with socket.create_connection(('127.0.0.1',self.port),timeout=2) as s:
            s.sendall(b'{"op":"clock"}\n')
            answer=json.loads(s.makefile('rb').readline())
            self.value=10+answer['t_us']/1e6
    def run(self):
        while not self.stopping.wait(0.01):
            try:self.update()
            except OSError as exc:self.error=str(exc)
    def monotonic(self):return self.value

def load_app(port=48200):
    install(port)
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'motor_app'))
    app=importlib.import_module('app')
    clock=SimulationTime(port)
    proxy=SimpleNamespace(**{name:getattr(time,name) for name in dir(time) if not name.startswith('_')})
    proxy.monotonic=clock.monotonic
    # 標準ライブラリやソケットの実時間は変更せず、アプリの監視時計のみ差し替える。
    for name in ('app','sensor_stream','feedback_ui'):
        module=importlib.import_module(name)
        if hasattr(module,'time'):module.time=proxy
    return app,clock

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=48200);p.add_argument('--example',action='store_true');args=p.parse_args()
    app,clock=load_app(args.port)
    root=app.tk.Tk();app.ttk.Style().theme_use('clam');instance=app.MotorApp(root)
    if args.example:
        from example_feedback import settings
        instance.feedback.apply(settings())
    root.title(root.title()+' [仮想マイコン / 実COM接続なし]')
    try:root.mainloop()
    finally:clock.stopping.set()
if __name__=='__main__':main()
