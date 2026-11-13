"""PCアプリのSerial API境界。仮想名以外を拒否し、実COMには接続しない。"""
import select
import socket
import sys
import time
import types
from types import SimpleNamespace

class SerialException(OSError):pass

class Serial:
    base_port=48200
    def __init__(self,port=None,baudrate=115200,timeout=0,write_timeout=0.2,**kwargs):
        self.port=port;self.timeout=timeout;self.write_timeout=write_timeout
        self.dtr=False;self.rts=False;self.sock=None;self.buffer=bytearray()
        if port:self.open()
    @property
    def is_open(self):return self.sock is not None
    def open(self):
        if self.port not in ('VMCU_MOTOR','VMCU_SENSOR'):raise SerialException('仮想モードではVMCU_MOTOR / VMCU_SENSORのみ接続できます。')
        self.sock=socket.create_connection(('127.0.0.1',self.base_port+(1 if self.port=='VMCU_MOTOR' else 2)),timeout=2)
        self.sock.setblocking(False)
    def pump(self,timeout=0):
        if not self.sock:raise SerialException('port closed')
        if select.select([self.sock],[],[],timeout)[0]:
            data=self.sock.recv(65536)
            if not data:raise SerialException('仮想マイコンが切断されました。')
            self.buffer.extend(data)
    @property
    def in_waiting(self):self.pump();return len(self.buffer)
    def read(self,size=1):
        if size<=0:return b''
        self.pump(0 if self.buffer else (self.timeout or 0))
        data=bytes(self.buffer[:size]);del self.buffer[:size];return data
    def write(self,data):
        if not self.sock:raise SerialException('port closed')
        deadline=time.monotonic()+self.write_timeout;view=memoryview(data);sent=0
        while view:
            if not select.select([],[self.sock],[],max(0,deadline-time.monotonic()))[1]:raise SerialException('virtual write timeout')
            n=self.sock.send(view);sent+=n;view=view[n:]
        return sent
    def reset_input_buffer(self):
        self.pump();self.buffer.clear()
    def close(self):
        if self.sock:
            try:self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass
            self.sock.close();self.sock=None

def install(port=48200):
    Serial.base_port=port
    serial=types.ModuleType('serial');serial.Serial=Serial;serial.SerialException=SerialException
    tools=types.ModuleType('serial.tools');ports=types.ModuleType('serial.tools.list_ports')
    ports.comports=lambda:[SimpleNamespace(device=name,description='Simulink virtual MCU',hwid='VIRTUAL',manufacturer='Simulation') for name in ('VMCU_MOTOR','VMCU_SENSOR')]
    tools.list_ports=ports;serial.tools=tools
    sys.modules.update({'serial':serial,'serial.tools':tools,'serial.tools.list_ports':ports})
