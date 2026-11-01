"""Versioned motion recipes. Times are integer microseconds; no device commands."""
import json
import math
import os
import tempfile
from pathlib import Path
from motion import (PULSES_PER_MM, MAX_STEPS_PER_AXIS, make_step, make_wait,
                    make_home, refresh_step, timing_config, jog_command)

FORMAT = 'SuperMEXE02.motion-settings'
VERSION = 1
MAX_FILE_BYTES = 1024 * 1024


def object_value(value, label):
    if not isinstance(value, dict):
        raise ValueError(label + 'の形式が不正です。')
    return value


def integer(value, label):
    if type(value) is not int:
        raise ValueError(label + 'は整数である必要があります。')
    return value


def number(value, label):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError(label + 'は有限の数である必要があります。')
    return value


def boolean(value, label):
    if type(value) is not bool:
        raise ValueError(label + 'のON/OFF設定が不正です。')
    return value


def decode_settings(data):
    """Validate the whole document before returning data that can update the UI.

    Draft programs may be empty or have an unfinished return stroke. Runtime
    checks remain in compile_program, so an unfinished recipe cannot execute.
    """
    try:
        data = object_value(data, '設定ファイル')
        if data.get('format') != FORMAT or type(data.get('version')) is not int or data['version'] != VERSION:
            raise ValueError('対応しているSuperMEXE02設定ファイルではありません。')
        if integer(data['pulses_per_mm'], '位置換算') != PULSES_PER_MM:
            raise ValueError('ファイルのパルス/mm換算が、この装置の設定と一致しません。')
        timing = object_value(data['timing'], '周期設定')
        period = integer(timing['period_us'], '周期')
        width = integer(timing['vct_width_us'], 'VCT幅')
        offsets = object_value(timing['start_delays_us'], '開始遅延')
        if set(offsets) != set('ABCD'):
            raise ValueError('開始遅延にはA〜Dの4軸を指定してください。')
        offsets = {a: integer(offsets[a], a+'開始遅延') for a in 'ABCD'}
        repeat = boolean(timing['repeat'], '繰り返し')
        prehome = boolean(timing['prehome'], '開始前ZHOME')
        vct = boolean(timing['with_vct'], 'VCT出力')
        timing_config(period/1000, width/1000, {a: offsets[a]/1000 for a in 'ABCD'}, prehome, repeat, vct)
        axes = object_value(data['axes'], '動作一覧')
        if set(axes) != set('ABCD'):
            raise ValueError('動作一覧にはA〜Dの4軸を指定してください。')
        programs = {}
        for axis in 'ABCD':
            rows = axes[axis]
            if not isinstance(rows, list) or len(rows) > MAX_STEPS_PER_AXIS:
                raise ValueError(axis+'軸の動作一覧が不正、または32件を超えています。')
            programs[axis] = []
            for row in rows:
                row = object_value(row, axis+'軸の動作')
                kind = row['type']
                if kind == 'move':
                    pulses = integer(row['pulses'], '移動パルス数')
                    if not 1 <= pulses <= 15000:
                        raise ValueError('移動パルス数は1〜15000にしてください。')
                    mode = row['mode']
                    if mode == 'time':
                        value = integer(row['duration_us'], '移動時間')/1000000
                    elif mode == 'speed':
                        value = number(row['speed_mm_s'], '移動速度')
                    else:
                        raise ValueError('移動の指定方法が不正です。')
                    step = make_step(axis, row['direction'], pulses/PULSES_PER_MM, mode, value)
                elif kind == 'wait':
                    step = make_wait(axis, integer(row['duration_us'], '待機時間')/1000000)
                elif kind == 'home':
                    step = make_home(axis)
                else:
                    raise ValueError('未知の動作が含まれています。')
                programs[axis].append(step)
        jog = object_value(data['jog'], '手動操作')
        jog_axis, jog_speed = jog['axis'], number(jog['speed_mm_s'], '手動速度')
        if not isinstance(jog_axis, str):
            raise ValueError('手動操作の軸が不正です。')
        jog_command(jog_axis, 'CW', jog_speed)
        return dict(programs=programs, period_us=period, width_us=width,
                    delays_us=offsets, repeat=repeat, prehome=prehome,
                    with_vct=vct, jog_axis=jog_axis, jog_speed=jog_speed)
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError('設定ファイルの項目が不足しているか、値の形式が不正です。') from exc


def encode_settings(programs, period_ms, width_ms, delays, prehome, repeat, vct, jog_axis, jog_speed):
    command, _ = timing_config(period_ms, width_ms, delays, prehome, repeat, vct)
    numbers = [int(v) for v in command.split()[1:]]
    axes = {}
    for axis in 'ABCD':
        axes[axis] = []
        for source in programs[axis]:
            if source['axis'] != axis:
                raise ValueError('動作と登録先の軸が一致しません。')
            row = refresh_step(source)
            saved = {'type': row['kind']}
            if row['kind'] == 'move':
                saved.update(direction=row['direction'], pulses=row['plan']['pulses'], mode=row['mode'])
                if row['mode'] == 'time':
                    saved['duration_us'] = row['plan']['duration_us']
                else:
                    saved['speed_mm_s'] = row['value']
            elif row['kind'] == 'wait':
                saved['duration_us'] = row['plan']['duration_us']
            axes[axis].append(saved)
    data = dict(format=FORMAT, version=VERSION, pulses_per_mm=PULSES_PER_MM,
                timing=dict(period_us=numbers[0], vct_width_us=numbers[1],
                            start_delays_us=dict(zip('ABCD',numbers[2:6])),
                            prehome=bool(prehome), repeat=bool(repeat), with_vct=bool(vct)),
                axes=axes, jog=dict(axis=jog_axis, speed_mm_s=float(jog_speed)))
    decode_settings(data)
    return data


def write_settings(path, data):
    decode_settings(data)
    destination = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=str(destination.parent),
                                         prefix='.'+destination.name+'.', suffix='.tmp', delete=False) as stream:
            temporary = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, str(destination))
        temporary = None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def read_settings(path):
    with open(path, 'rb') as stream:
        raw = stream.read(MAX_FILE_BYTES+1)
    if len(raw) > MAX_FILE_BYTES:
        raise ValueError('設定ファイルが大きすぎます（上限1 MB）。')
    try:
        data = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError('UTF-8のJSON設定ファイルとして読み込めません。') from exc
    return decode_settings(data)
