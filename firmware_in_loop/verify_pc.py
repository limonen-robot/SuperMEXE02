"""元GUI/計測スレッド/目標演算を実行し、別プロセスのSimulinkとの結合を確認。"""
import argparse
import json
import time
import socket
from pathlib import Path
from launch_pc import load_app
from example_feedback import settings

def main():
    p=argparse.ArgumentParser();p.add_argument('--seconds',type=float,default=25);p.add_argument('--output',default='build/pc_verification.json');args=p.parse_args()
    app,clock=load_app();root=app.tk.Tk();root.withdraw()
    failures=[]
    root.report_callback_exception=lambda kind,value,tb:failures.append(str(value))
    def dialog(title,message,*a,**k):failures.append(title+': '+str(message))
    app.messagebox.showerror=dialog;app.messagebox.showinfo=dialog
    instance=app.MotorApp(root);instance.feedback.apply(settings())
    started=False;connected=False;baseline=None;seen_values=False;seen_pid=False;sent_fbval=False
    wall_deadline=time.monotonic()+180
    try:
        while time.monotonic()<wall_deadline:
            root.update();time.sleep(0.005)
            if not connected and not instance.port_scan_busy and not instance.feedback.scanning:
                instance.port.set('VMCU_MOTOR');instance.toggle_connection()
                instance.feedback.port.set('VMCU_SENSOR');instance.feedback.toggle();connected=True
            stream=instance.feedback.stream
            if connected and instance.ready and not instance.busy and stream and stream.ready and not started:
                with socket.create_connection(('127.0.0.1',48200),timeout=2) as sock:
                    sock.sendall(b'{"op":"info"}\n');info=json.loads(sock.makefile('rb').readline())
                if info['attached']:instance.feedback.start();started=True;baseline=clock.monotonic()
            seen_values|=bool(instance.feedback.last_values)
            seen_pid|=any(state[0]==4 for state in instance.feedback.motor_states.values())
            sent_fbval|=bool(instance.feedback.outputs) and instance.feedback.accepting
            if failures:raise AssertionError(failures)
            if started and clock.monotonic()-baseline>=args.seconds:break
        else:raise AssertionError('Simulinkと接続している状態で検証してください（待機時間超過）。')
        result=dict(started=started,values=seen_values,pid=seen_pid,fbval=sent_fbval,status=instance.status.get(),motor_states=instance.feedback.motor_states,clock_error=clock.error)
        assert started and seen_values and seen_pid and sent_fbval,result
        output=Path(__file__).resolve().parent/args.output;output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print('PASS actual Python GUI + SensorStream + feedback_model + native firmware: '+str(result),flush=True)
    finally:
        instance.close();clock.stopping.set()
if __name__=='__main__':main()
