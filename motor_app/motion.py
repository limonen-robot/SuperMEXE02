"""Translate relative motion settings to counted pulse commands."""
import math

PULSES_PER_MM = 2500
MIN_DISTANCE_MM = 1 / PULSES_PER_MM
MAX_DISTANCE_MM = 6.0
MAX_SPEED_MM_S = 20.0  # Software cap; not a mechanical maximum-speed rating.
MIN_HALF_US = 10
MAX_HALF_US = 1000000
MAX_STEPS_PER_AXIS = 32
MAX_COMBINED_EDGE_RATE = 100000
HOME_SECONDS = 1.02
MAX_WAIT_SECONDS = 600


def axis_check(axis):
    if axis not in 'ABCD' or len(axis) != 1:
        raise ValueError('軸をA〜Dから選んでください。')


def microseconds(value, low, high, label):
    value = float(value)
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError('{}は{}〜{}の範囲で指定してください。'.format(label, low, high))
    return int(math.floor(value * 1000000 + 0.5))


def plan_move(axis, direction, distance, mode, value):
    distance, value = float(distance), float(value)
    if axis not in 'ABCD' or len(axis) != 1:
        raise ValueError('軸をA〜Dから選んでください。')
    if direction not in ('CW', 'CCW') or mode not in ('speed', 'time'):
        raise ValueError('方向または指定方法が不正です。')
    if not math.isfinite(distance) or not MIN_DISTANCE_MM <= distance <= MAX_DISTANCE_MM:
        raise ValueError('移動量は1パルス（0.0004 mm）以上、6 mm以下にしてください。')
    if not math.isfinite(value) or value <= 0:
        raise ValueError('速度・時間は0より大きい有限の数にしてください。')
    pulses = int(math.floor(distance * PULSES_PER_MM + 0.5))
    if pulses < 1:
        raise ValueError('移動量が1パルス（0.0004 mm）未満です。')
    actual_mm = pulses / PULSES_PER_MM
    duration_us = microseconds(value, 0.000001, 2*pulses*MAX_HALF_US/1000000, '移動時間[秒]') if mode == 'time' else None
    requested_speed = value if mode == 'speed' else actual_mm / (duration_us/1000000)
    if requested_speed > MAX_SPEED_MM_S + 1e-12:
        raise ValueError('速度上限は20 mm/sです。時間指定では時間を長くしてください。')
    if mode == 'speed':
        half = 1000000 / (2 * PULSES_PER_MM * value)
        if not math.isfinite(half) or half > MAX_HALF_US:
            raise ValueError('指定速度が低すぎます。')
        half = max(MIN_HALF_US, int(math.floor(half + 0.5)))
        duration_us = 2 * half * pulses
    else:
        half = duration_us // (2 * pulses)
        if half < MIN_HALF_US:
            raise ValueError('指定時間が短すぎます。1パルスにつき最低20 µs必要です。')
    duration = duration_us / 1000000
    signed = pulses if direction == 'CW' else -pulses
    command = ('MOVET {} {} {}'.format(axis, signed, duration_us) if duration_us % (2*pulses)
               else 'MOVE {} {} {}'.format(axis, signed, half))
    return dict(command=command, duration_us=duration_us, min_half_us=half,
                pulses=pulses, distance=actual_mm, duration=duration,
                speed=actual_mm / duration)


def make_step(axis, direction, distance, mode, value):
    plan = plan_move(axis, direction, distance, mode, value)
    return dict(kind='move', axis=axis, direction=direction, distance=float(distance),
                mode=mode, value=float(value), plan=plan)


def make_wait(axis, seconds):
    axis_check(axis)
    us = microseconds(seconds, 0.000001, MAX_WAIT_SECONDS, '待機時間[秒]')
    return dict(kind='wait', axis=axis, direction='-', distance=0, mode='time', value=us/1000000,
                plan=dict(command='WAIT {} {}'.format(axis, us), duration_us=us, pulses=0, distance=0,
                          duration=us/1000000, speed=0))


def make_home(axis):
    axis_check(axis)
    return dict(kind='home', axis=axis, direction='-', distance=0, mode='home', value=HOME_SECONDS,
                plan=dict(command='ZHADD ' + axis, duration_us=1020000, pulses=0, distance=0, duration=HOME_SECONDS, speed=0))


def refresh_step(row):
    kind = row.get('kind', 'move')
    if kind == 'wait':
        return make_wait(row['axis'], row['value'])
    if kind == 'home':
        return make_home(row['axis'])
    if kind != 'move':
        raise ValueError('動作の種類が不正です。')
    return make_step(row['axis'], row['direction'], row['distance'], row['mode'], row['value'])


def timing_config(period_ms, width_ms, delays=None, prehome=False, repeat=False, vct=False):
    period = microseconds(float(period_ms)/1000, 0.01, 60, '周期[秒]')
    width = microseconds(float(width_ms)/1000, 0.001, 60, 'VCTパルス幅[秒]')
    if width >= period:
        raise ValueError('VCTパルス幅は周期より短くしてください。')
    delays = delays or {}
    offsets = [microseconds(float(delays.get(a, 0))/1000, 0, 600, '開始遅延[秒]') for a in 'ABCD']
    numbers = [period, width] + offsets + [int(bool(prehome)), int(bool(repeat)), int(bool(vct))]
    return 'CONFIG ' + ' '.join(str(n) for n in numbers), 'ACK CONFIG'


def jog_command(axis, direction, speed):
    p = plan_move(axis, direction, 1/PULSES_PER_MM, 'speed', speed)
    half = p['min_half_us']
    return 'JOG {} {}'.format(axis, half if direction == 'CW' else -half)


def compile_program(program, repeat=False, period_ms=600, delays=None):
    """Validate all steps and prepare an acknowledged upload, then a single RUN."""
    commands = [('CLEAR', 'ACK CLEAR')]
    total_steps = 0
    edge_rate = 0
    max_duration = 0.0
    period_us = microseconds(float(period_ms)/1000, 0.01, 60, '周期[秒]')
    delays = delays or {}
    for axis in 'ABCD':
        rows = program.get(axis, [])
        if len(rows) > MAX_STEPS_PER_AXIS:
            raise ValueError('各軸の動作は32件まで登録できます。')
        fastest_half = None
        duration = 0.0
        duration_us = 0
        net_pulses = 0
        for number, row in enumerate(rows, 1):
            if row['axis'] != axis:
                raise ValueError('動作の登録先と軸が一致しません。')
            row = refresh_step(row)
            p = row['plan']
            if row['kind'] == 'move':
                half = p['min_half_us']
                fastest_half = half if fastest_half is None else min(fastest_half, half)
                net_pulses += p['pulses'] if row['direction'] == 'CW' else -p['pulses']
            elif repeat and row['kind'] == 'home':
                raise ValueError('繰り返し動作中のZHOMEは使えません。「最初にZHOME」を使用してください。')
            commands.append((p['command'].replace('MOVET ', 'ADDT ', 1).replace('MOVE ', 'ADD ', 1), 'ACK ADD {} {}'.format(axis, number)))
            duration += p['duration']
            duration_us += p['duration_us']
            total_steps += 1
        if repeat and net_pulses != 0:
            raise ValueError('{}軸：往復の移動パルス数の合計が0ではありません。'.format(axis))
        if repeat and duration_us > period_us:
            raise ValueError('{}軸：動作時間が周期を超えています。スケール適用または周期変更が必要です。'.format(axis))
        if fastest_half is not None:
            edge_rate += (1000000 + fastest_half - 1) // fastest_half
        delay = microseconds(float(delays.get(axis, 0))/1000, 0, 600, '開始遅延[秒]') / 1000000
        max_duration = max(max_duration, duration + delay if rows else 0)
    if not total_steps:
        raise ValueError('動作を追加してください。')
    if edge_rate > MAX_COMBINED_EDGE_RATE:
        raise ValueError('同時実行では各軸の最高速度の合計を20 mm/s以下にしてください。')
    return commands, total_steps, max_duration


def scale_steps(rows, period_ms):
    """Fit one axis into a cycle, preserving distance and fixed homing times."""
    target = microseconds(float(period_ms)/1000, 0.01, 60, '周期[秒]')
    rows = [refresh_step(row) for row in rows]
    if not rows:
        raise ValueError('この軸に動作を追加してください。')
    fixed = sum(int(round(row['plan']['duration']*1000000)) for row in rows if row['kind']=='home')
    scalable = sum(row['plan']['duration']*1000000 for row in rows if row['kind']!='home')
    if scalable <= 0 or target <= fixed:
        raise ValueError('固定のZHOME時間を除いたスケール対象時間がありません。')
    ratio = (target-fixed)/scalable
    result = []
    for row in rows:
        if row['kind'] == 'home':
            result.append(row)
        elif row['kind'] == 'wait':
            us = int(math.floor(row['plan']['duration']*1000000*ratio))
            result.append(make_wait(row['axis'], us/1000000))
        else:
            pulses = row['plan']['pulses']
            us = int(math.floor(row['plan']['duration_us']*ratio))
            if not MIN_HALF_US*2*pulses <= us <= MAX_HALF_US*2*pulses:
                raise ValueError('スケール後の速度が設定範囲外です。周期を変更してください。')
            seconds = us/1000000
            result.append(make_step(row['axis'], row['direction'], row['plan']['distance'], 'time', seconds))
    if sum(int(round(r['plan']['duration']*1000000)) for r in result) > target:
        raise ValueError('丸め後の動作時間が周期を超えています。')
    return result
