import threading
import time
import serial
from feedback_model import Processor

class SensorStream:
    def __init__(self,port,channels):
        self.lock=threading.Lock();self.processor=Processor(channels)
        self.ready=False;self.configuring=True;self.error='';self.last_raw=0;self.stopping=threading.Event()
        self.handshake_deadline=time.monotonic()+5;self.ready_since=0
        self.device=serial.Serial(port=None,baudrate=115200,timeout=0.05,write_timeout=0.2)
        self.device.dtr=False;self.device.rts=False;self.device.port=port;self.device.open()
        self.device.reset_input_buffer()
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()
    def run(self):
        hello=0;configured=False;buffer=bytearray()
        try:
            while not self.stopping.is_set():
                now=time.monotonic()
                if not self.ready and now>self.handshake_deadline:raise ValueError('sensor側の応答がありません。新しいsensorコードを書き込んでください。')
                if not configured and now-hello>0.5:self.device.write(b'HELLO\n');hello=now
                buffer.extend(self.device.read(max(1,min(self.device.in_waiting,8192))))
                if len(buffer)>8192:raise ValueError('センサーの受信が詰まりました。')
                while b'\n' in buffer:
                    raw,_,rest=buffer.partition(b'\n');buffer=bytearray(rest)
                    try:line=raw.decode('ascii',errors='strict').strip()
                    except UnicodeDecodeError:
                        # UART reconfiguration can leave a partial/bad startup frame.
                        # Discard it only before the validated READY handshake.
                        if not configured:continue
                        raise ValueError('センサー通信が文字化けしました（受信先頭: {}）。COMポート・配線・電源を確認して再接続してください。'.format(raw[:12].hex()))
                    if line.startswith('READY '):
                        if line!='READY SUPERMEXE02_SENSOR_V1':raise ValueError('sensorコードの版が異なります。')
                        if self.ready:raise ValueError('sensor側が再起動しました。再接続してください。')
                        channels=self.processor.channels
                        mask=sum(1<<i for i,a in enumerate('abcd') if channels[a]['method']=='trigger')
                        delays=[int(round(channels[a]['delay_ms']*1000)) for a in 'abcd']
                        self.device.write(('SENSORCFG {} {}\n'.format(mask,' '.join(str(v) for v in delays))).encode('ascii'))
                        configured=True
                    elif line=='ACK SENSORCFG' and configured:
                        self.ready_since=time.monotonic();self.ready=True;self.configuring=False
                    elif line.startswith(('FAULT ','ERR ')):raise ValueError('sensor側：'+line)
                    elif self.ready:
                        with self.lock:
                            if self.ready and self.processor.consume(line):self.last_raw=time.monotonic()
        except (ValueError,UnicodeError,OSError,serial.SerialException) as exc:
            if not self.stopping.is_set():self.error=str(exc)
        finally:
            self.ready=False;self.device.close()
    def snapshot(self,period_ms):
        with self.lock:
            fresh=self.ready and time.monotonic()-self.last_raw<=1 and not self.error
            expired=self.ready and time.monotonic()-(self.last_raw or self.ready_since)>1
            error=self.error or ('計測データが途絶えました。' if expired else '')
            return dict(fresh=fresh,values=self.processor.values(period_ms) if fresh else {},
                        volts=dict(self.processor.volts),error=error,ready=self.ready,configuring=self.configuring)
    def reset_processing(self):
        with self.lock:
            channels=self.processor.channels
            self.ready=False;self.configuring=True;self.handshake_deadline=time.monotonic()+5
            self.processor=Processor(channels);self.last_raw=0
            mask=sum(1<<i for i,a in enumerate('abcd') if channels[a]['method']=='trigger')
            delays=[int(round(channels[a]['delay_ms']*1000)) for a in 'abcd']
        self.device.write(('SENSORCFG {} {}\n'.format(mask,' '.join(str(v) for v in delays))).encode('ascii'))
    def close(self):
        self.stopping.set()
        self.thread.join(timeout=0.3)
