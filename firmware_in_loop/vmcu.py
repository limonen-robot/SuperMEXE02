"""実機のmain.cppからビルドしたDLLを実行する。PIDやコマンド解釈は実装しない。"""
import ctypes as C
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

class Board:
    def __init__(self, name):
        self.lib = C.CDLL(str(ROOT / 'build' / (name + '.dll')))
        self.lib.vm_advance.argtypes = [C.c_int64]
        self.lib.vm_input.argtypes = [C.c_char_p, C.c_int]
        self.lib.vm_output.argtypes = [C.c_void_p, C.c_int]
        self.lib.vm_edges.argtypes = [C.POINTER(C.c_int64), C.c_int]
        if not self.lib.vm_start():
            raise RuntimeError('firmware main failed: ' + name)
    def advance(self, us):
        if self.lib.vm_advance(int(us)):
            raise RuntimeError('virtual MCU time must increase')
    def receive(self, data):
        self.lib.vm_input(data, len(data))
    def output(self):
        chunks=[]
        while True:
            b=C.create_string_buffer(16384)
            n=self.lib.vm_output(b,len(b))
            if not n: return b''.join(chunks)
            chunks.append(b.raw[:n])
    def edges(self):
        result=[]
        while True:
            b=(C.c_int64*(4096*3))()
            n=self.lib.vm_edges(b,4096)
            result.extend(tuple(b[i*3:i*3+3]) for i in range(n))
            if n<4096:return result
    def adc(self, values):
        for i,v in enumerate(values): self.lib.vm_adc(i,int(v))
    def digital(self, pin, value): self.lib.vm_digital(pin,value)
    def close(self): self.lib.vm_stop()

def verify_sources():
    manifest=json.loads((ROOT/'build'/'sources.json').read_text(encoding='utf-8-sig'))
    current=set()
    for folder in ('G431KB_motor','G431KB_sensor'):
        for path in (ROOT.parent/folder).rglob('*'):
            if path.is_file() and path.suffix in ('.c','.cpp','.h','.hpp') and not {'mbed-os','BUILD','cmake_build'}.intersection(path.parts):
                current.add(path.relative_to(ROOT.parent).as_posix())
    if current!=set(manifest):raise RuntimeError('ソースファイル構成が変わりました。build.ps1で再ビルドしてください。')
    for name,digest in manifest.items():
        if hashlib.sha256((ROOT.parent/name).read_bytes()).hexdigest().upper()!=digest:
            raise RuntimeError('ソースが変わりました。build.ps1で再ビルドしてください: '+name)
    return manifest
