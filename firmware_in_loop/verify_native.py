"""実機ソースのプロトコル・タイマー・GPIO・停止監視の回帰確認。"""
from server import Environment

def main():
    env=Environment()
    try:
        def cmd(board,text):env.command(board,(text+'\n').encode())
        def step(us):return env.step(us,[10000,20000,30000,40000])
        def read(board):
            b=env.buffers[board];s=b.decode();b.clear();return s
        assert 'READY SUPERMEXE02_MOTOR_V6' in read('motor')
        assert 'READY SUPERMEXE02_SENSOR_V1' in read('sensor')
        cmd('motor','HELLO');cmd('motor','PING');cmd('motor','MOVE A 10 250')
        step(10000)
        reply=read('motor');assert 'PONG' in reply and 'ERR' not in reply,reply
        assert env.positions[0]==10010,env.positions
        assert 'S 1 10000 10000 20000 30000 40000' in read('sensor')
        cmd('sensor','SENSORCFG 1 310000 310000 310000 310000')
        cmd('motor','VCT 600000 30000');step(400000)
        reply=read('sensor');assert 'ACK SENSORCFG' in reply and '\nT ' in reply,reply
        # リレーON 5msと、実ファームでの310ms遅延取得を確認。
        trigger=[line.split() for line in reply.splitlines() if line.startswith('T ')][0]
        assert int(trigger[3])>=327000 and int(trigger[4])-int(trigger[3])<1000,trigger
        cmd('motor','STOP');step(410000);read('motor')
        cmd('motor','CLEAR');cmd('motor','CONFIG 600000 30000 0 0 0 0 0 1 1')
        cmd('motor','FBCFG 0 C 100000 0 0 13000 1500 250 -1 13 0 0 0 0 0')
        cmd('motor','FBCFG 3 D 100000 0 0 13000 1500 250 -1 13 0 0 0 0 0')
        step(420000);reply=read('motor');assert 'ACK FBCFG 0' in reply and 'ERR FB_CONFIG' in reply,reply
        cmd('motor','PING');cmd('motor','FBRUN');step(1500000)
        reply=read('motor');assert 'FBREADY ALL' in reply,reply
        cmd('motor','PING');cmd('motor','FBVAL 0 110 100');step(1610000)
        # Pだけの補正: -1 * 13 * (110-100) = -130 pulses
        step(1690000);assert env.positions[2]==-130,env.positions
        step(3100000);reply=read('motor');assert 'FB_DATA_TIMEOUT' in reply,reply
        print('PASS: unmodified main/header, 10 GPIO pulses, sensor ASCII/ADC, relay-trigger timing, 3-PID limit, -130 PID pulses, feedback timeout')
    finally:env.close()
if __name__=='__main__':main()
