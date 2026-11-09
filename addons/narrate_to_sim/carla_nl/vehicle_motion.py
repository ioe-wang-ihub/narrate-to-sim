"""Compile legacy vehicle settings into the same motion points (Python 3.7)."""
import math


def planned_distance_m(cfg, time_s):
    """Integrate a linear speed profile from the initial speed (Python 3.7)."""
    previous_time, previous_speed = 0.0, cfg['speed_kmh'] / 3.6
    distance = 0.0
    for point in sorted(cfg.get('speed_points', []), key=lambda item: item['time_s']):
        next_time, next_speed = point['time_s'], point['speed_kmh'] / 3.6
        if next_time <= previous_time:
            previous_speed = next_speed
            continue
        if time_s <= next_time:
            span = max(0.0, time_s - previous_time)
            end_speed = previous_speed + (next_speed - previous_speed) * span / (next_time - previous_time)
            return distance + (previous_speed + end_speed) * span / 2
        distance += (previous_speed + next_speed) * (next_time - previous_time) / 2
        previous_time, previous_speed = next_time, next_speed
    return distance + previous_speed * max(0.0, time_s - previous_time)


def planned_vehicle_state(vehicles, role, time_s, ego_speed_kmh=50.0):
    """Road position and speed for obstacle preview/bounds, never live placement."""
    ego = motion_config(dict(vehicles.get('ego') or {}))
    ego.setdefault('speed_kmh', ego_speed_kmh)
    cfg = ego if role == 'ego' else motion_config(dict(vehicles[role]))
    cfg.setdefault('speed_kmh', ego['speed_kmh'])

    def value(points, field, t, initial, anchor=False):
        pairs = sorted((float(p['time_s']), float(p[field])) for p in (points or []))
        if not pairs:
            return initial
        if anchor and pairs[0][0] > 0:
            pairs.insert(0, (0.0, initial))
        if t < pairs[0][0]:
            return initial
        for (a, av), (b, bv) in zip(pairs, pairs[1:]):
            if t <= b and b > a:
                return av + (bv - av) * (t - a) / (b - a)
        return pairs[-1][1]

    def bumper_gap(seconds):
        metres = seconds * max(.001, ego['speed_kmh'] / 3.6)
        return metres + (4.5 if metres > 0 else -4.5 if metres < 0 else 0)

    def position(t):
        if role != 'ego' and (cfg.get('time_gap_points') or cfg.get('gap_points')):
            ego_s = float(ego.get('initial_gap_m', 0)) + planned_distance_m(ego, t)
            if cfg.get('time_gap_points'):
                default = cfg.get('initial_time_gap_s')
                if default is None:
                    default = float(cfg.get('initial_gap_m', 0)) / max(.001, ego['speed_kmh'] / 3.6)
                return ego_s + bumper_gap(value(cfg['time_gap_points'], 'time_gap_s', t, default, anchor=True))
            initial = float(cfg.get('initial_gap_m', 0))
            if cfg.get('initial_time_gap_s') is not None:
                initial = bumper_gap(cfg['initial_time_gap_s'])
            return ego_s + value(cfg['gap_points'], 'gap_m', t, initial, anchor=True)
        initial = float(cfg.get('initial_gap_m', 0))
        if role != 'ego' and cfg.get('initial_time_gap_s') is not None:
            initial = bumper_gap(cfg['initial_time_gap_s'])
        return initial + planned_distance_m(cfg, t)

    if role != 'ego' and (cfg.get('time_gap_points') or cfg.get('gap_points')):
        speed = (position(time_s + .001) - position(time_s)) * 3600
    else:
        speed = value(cfg.get('speed_points'), 'speed_kmh', time_s, cfg['speed_kmh'], anchor=True)
    return position(time_s), max(0.0, speed)


def motion_config(cfg):
    result = dict(cfg)
    if cfg.get('lane_change_enabled') and not cfg.get('lane_change_points'):
        result['lane_change_points'] = [dict(time_s=cfg.get('lane_change_time_s', 8),
                                            target_lane=cfg.get('lane_change_target_lane', 'Middle'),
                                            duration_s=cfg.get('lane_change_duration_s', 3))]
    if not cfg.get('stop_enabled'):
        return result
    start = float(cfg.get('stop_time_s', 14))
    duration = float(cfg.get('stop_duration_s', 2))
    if not math.isfinite(start + duration) or start < 0 or duration <= 0:
        raise ValueError('Stop needs a nonnegative start time and a positive braking duration.')
    if cfg.get('gap_points') or cfg.get('time_gap_points'):
        raise ValueError('A stop conflicts with a distance/headway profile. Use a speed profile '
                         'with a zero-speed point, or remove the stop setting.')
    points = sorted([dict(p) for p in cfg.get('speed_points', [])], key=lambda p: p['time_s'])
    speed = float(cfg.get('speed_kmh', 0))
    previous_t = 0.0
    for point in points:
        if point['time_s'] > start:
            speed += (point['speed_kmh'] - speed) * (start - previous_t) / (point['time_s'] - previous_t)
            break
        previous_t, speed = point['time_s'], point['speed_kmh']
    end = start + duration
    for point in points:
        if start < point['time_s'] <= end:
            expected = speed * (end - point['time_s']) / duration
            if abs(point['speed_kmh'] - expected) > 1e-6:
                raise ValueError('The stop and speed profile request different speeds at {:g}s. '
                                 'Set the stop endpoint to 0 km/h or remove the conflicting stop setting.'
                                 .format(point['time_s']))
    points = [p for p in points if p['time_s'] < start or p['time_s'] > end]
    points.extend([dict(time_s=start, speed_kmh=speed), dict(time_s=end, speed_kmh=0.0)])
    result['speed_points'] = sorted(points, key=lambda p: p['time_s'])
    return result
