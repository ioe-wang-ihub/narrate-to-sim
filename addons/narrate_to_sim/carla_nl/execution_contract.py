"""Checks shared by authoring and the Python 3.7 CARLA runner."""
import math

from .vehicle_motion import motion_config


def validate_execution_data(payload):
    """Reject schedules that the runner would otherwise truncate or ignore."""
    total = float(payload.get('total_time_s', 0))
    if not math.isfinite(total) or total <= 0:
        raise ValueError('Scenario duration must be a positive finite number of seconds.')
    requirements = []

    def timing(label, start, end=None):
        start = float(start)
        end = start if end is None else float(end)
        if not math.isfinite(start + end) or start < 0 or end < start:
            raise ValueError('{}: use a nonnegative start and an end at or after the start.'.format(label))
        if end > total + 1e-6:
            requirements.append((label, end))

    vehicles = payload.get('vehicles', {})
    for role, raw in vehicles.items():
        if not raw.get('enabled', role == 'ego'):
            continue
        if role not in ['ego'] + ['vehicle_{}'.format(i) for i in range(1, 7)]:
            raise ValueError('{} is not a supported vehicle number. Use vehicle_1 through vehicle_6.'.format(role))
        cfg = motion_config(raw)
        profiles = [key for key in ('gap_points', 'time_gap_points', 'speed_points') if cfg.get(key)]
        if len(profiles) > 1:
            raise ValueError('{} has conflicting movement profiles. Choose one of speed, distance, or headway.'.format(role))
        if role == 'ego' and (cfg.get('gap_points') or cfg.get('time_gap_points')):
            raise ValueError('Ego cannot follow a gap/headway to itself. Put the gap profile on vehicle_1 '
                             'through vehicle_6, or use ego speed_points.')
        speed = float(cfg.get('speed_kmh', payload.get('ego_speed_kmh', 0)))
        if not math.isfinite(speed) or speed < 0:
            raise ValueError('{} speed must be finite and 0 km/h or greater.'.format(role))
        previous_end = 0.0
        for point in sorted(cfg.get('lane_change_points', []), key=lambda p: p['time_s']):
            start, duration = float(point['time_s']), float(point['duration_s'])
            if duration <= 0 or start < previous_end - 1e-6:
                raise ValueError('{} lane changes overlap or have no duration. Start each change after the previous one ends.'.format(role))
            timing(role + ' lane change', start, start + duration)
            previous_end = start + duration
        for key in profiles:
            value_key = {'gap_points': 'gap_m', 'time_gap_points': 'time_gap_s', 'speed_points': 'speed_kmh'}[key]
            seen = {}
            for point in cfg[key]:
                t, value = float(point['time_s']), float(point[value_key])
                timing(role + ' ' + key, t)
                if not math.isfinite(value) or (key == 'speed_points' and value < 0):
                    raise ValueError('{} {} contains an invalid value at {:g}s.'.format(role, key, t))
                if t in seen and seen[t] != value:
                    raise ValueError('{} has two different {} values at {:g}s. Keep one value at that time.'.format(role, key, t))
                seen[t] = value
    for role, cfg in payload.get('pedestrians', {}).items():
        if cfg.get('enabled'):
            if cfg.get('position_reference', 'initial_ego') not in ('initial_ego', 'ego_at_crossing'):
                raise ValueError('{} crossing position must refer to initial ego or ego at crossing start.'.format(role))
            if not math.isfinite(float(cfg.get('gap_m', 0))):
                raise ValueError('{} crossing distance must be a finite number of metres.'.format(role))
            start, duration = float(cfg.get('start_time_s', 0)), float(cfg.get('duration_s', 4))
            if duration <= 0:
                raise ValueError('{} crossing duration must be greater than zero.'.format(role))
            timing(role + ' crossing', start, start + duration)
    for role, cfg in payload.get('obstacles', {}).items():
        if not cfg.get('enabled'):
            continue
        if cfg.get('position_reference', 'initial_ego') not in ('initial_ego', 'vehicle_at_spawn'):
            raise ValueError('{} position must refer to an initial road position or a vehicle at appearance.'.format(role))
        if not math.isfinite(float(cfg.get('gap_m', 0))):
            raise ValueError('{} distance must be a finite number of metres.'.format(role))
        if cfg.get('time_gap_s') is not None and not math.isfinite(float(cfg['time_gap_s'])):
            raise ValueError('{} time gap must be finite.'.format(role))
        if cfg.get('spawn_mode', 'fixed') == 'fixed' and cfg.get('position_reference') == 'vehicle_at_spawn':
            source = cfg.get('reference_role')
            if not source or not vehicles.get(source, {}).get('enabled', source == 'ego'):
                raise ValueError('{} needs an explicit enabled reference vehicle (ego or vehicle number). Define {} first.'.format(role, source or 'the reference vehicle'))
        start, end = float(cfg.get('start_time_s', 0)), float(cfg.get('end_time_s', total))
        if end <= start:
            raise ValueError('{} must disappear after it appears; provide a later end time.'.format(role))
        timing(role, start, end)
        if cfg.get('spawn_mode') == 'drop_from_vehicle':
            source = cfg.get('drop_source_role')
            if not vehicles.get(source, {}).get('enabled'):
                raise ValueError('{} needs an enabled source vehicle; define {} before the drop event.'.format(role, source or 'the vehicle'))
    for cue in payload.get('audio_cues', []):
        timing('audio ' + str(cue.get('file_name', '')), cue['time_s'])
    events = payload.get('experiment_events', {}) or {}
    for marker in events.get('custom_markers', []):
        timing(marker.get('event', 'marker'), marker['time_s'])
    for hazard in events.get('hazards', []):
        timing(hazard.get('name', 'hazard'), hazard['onset_s'], hazard.get('end_s'))
    hold = bool(events.get('hold_until_tor_resolution'))
    for window in events.get('tor_windows', []):
        label = window.get('name', 'TOR')
        onset = window.get('alert_onset_s')
        onset = float(window['tor_onset_s'] if onset is None else onset)
        deadline = float(window['tor_deadline_s'])
        # Holding extends an already-fired interaction. An unseen window cannot
        # keep the runner alive, so its actual alert onset must fit the scene.
        timing(label + ' onset', onset)
        if not math.isfinite(deadline) or deadline <= onset:
            raise ValueError('{}: TOR deadline must be finite and after its alert onset.'.format(label))
        if not hold:
            timing(label, onset, deadline)
    for index, request in enumerate(payload.get('takeover_requests', [])):
        label = 'Takeover request {}'.format(index + 1)
        onset = float(request['time_s'])
        timing(label + ' onset', onset)
        timeout = float(request.get('timeout_s', 6))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('{}: timeout must be a positive finite duration.'.format(label))
        budget = request.get('takeover_budget_s')
        if budget is not None and not hold:
            budget = float(budget)
            buffer_s = float(request.get('collision_buffer_s', 3))
            if not math.isfinite(budget + buffer_s) or budget <= 0 or buffer_s < 0:
                raise ValueError('{}: takeover budget must be positive and collision buffer nonnegative and finite.'.format(label))
            timeout = budget - min(buffer_s, budget)
        if not hold:
            timing(label, onset, onset + timeout)
    for index, alert in enumerate(payload.get('tor_alerts', [])):
        timing('TOR alert {} onset'.format(index + 1), alert['time_s'])
    if requirements:
        required = max(end for _, end in requirements)
        error = ValueError('Events extend beyond the scenario duration. Set duration to at least {:g}s '
                           'or move those events earlier.'.format(required))
        error.duration_requirements = requirements
        raise error


def object_path_limits(payload):
    """Include fixed objects and crossing locations ahead AND behind ego."""
    distances = [0.0]
    for group in ('obstacles', 'pedestrians'):
        for cfg in payload.get(group, {}).values():
            if cfg.get('enabled') and cfg.get('spawn_mode', 'fixed') == 'fixed':
                distance = float(cfg.get('gap_m', 0))
                if group == 'obstacles' and cfg.get('position_reference') == 'vehicle_at_spawn':
                    from .obstacle_positions import planned_obstacle_position
                    distance = planned_obstacle_position(cfg, payload.get('vehicles', {}), payload.get('ego_speed_kmh', 50))
                if not math.isfinite(distance):
                    raise ValueError('Object distances must be finite numbers of metres.')
                distances.append(distance)
    return min(distances), max(distances)
