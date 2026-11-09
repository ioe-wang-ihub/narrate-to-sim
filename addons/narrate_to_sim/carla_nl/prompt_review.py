"""Readable review facts from the generated scenario, without changing its data."""
import math
import re

from .prompt_language import normalize_vehicle_language


def _speed_formatter(prompt):
    # Unit hints affect display only. Match the converted numeric value as well
    # as its actor, so mixed mph/km/h drafts do not relabel an unrelated speed.
    try:
        text = normalize_vehicle_language(prompt)
    except ValueError:
        # Clearing a failed preview must not run the same invalid description
        # through normalization again. Generated values already use km/h.
        text = ''
    text = re.sub(r'"(?:\\.|[^"\\])*"', '', text)
    tokens = re.compile(r'\b(?P<role>ego|vehicle_[1-6])\b|'
                        r'(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>mph|km\s*/?\s*h|kmh|kph|m/s)\b', re.I)
    factors = {'mph': 1.609344, 'km/h': 1, 'm/s': 3.6}
    hints, role = {}, None
    for match in tokens.finditer(text):
        if match['role']:
            if match['role'].lower() == 'ego' and (
                re.search(r"\b(?:of|behind|ahead|initial|the)\s*$", text[:match.start()], re.I)
                or text[match.end():].startswith(("'s", "’s"))
            ):
                continue  # ego is the position/lane reference, not the subject.
            role = match['role'].lower()
        elif role:
            unit = match['unit'].lower() if match['unit'].lower() in factors else 'km/h'
            value = float(match['value']) * factors[unit]
            hints.setdefault(role, []).append((value, unit))

    def speed(role, kmh):
        kmh = float(kmh or 0)
        candidates = hints.get(role, [])
        units = {unit for value, unit in candidates if math.isclose(value, kmh, abs_tol=0.001)}
        if not units:
            units = {unit for _, unit in candidates}
        unit = units.pop() if len(units) == 1 else 'km/h'
        value = kmh / factors[unit]
        return f'{value:g} {unit}'
    return speed


def vehicle_review_lines(scenario, prompt=''):
    """Initial states and chronological actions for all enabled vehicles."""
    speed = _speed_formatter(prompt)
    lines = []
    vehicles = scenario.get('vehicles', {}) or {}
    for role in sorted(vehicles, key=lambda value: (value != 'ego', value)):
        cfg = vehicles[role]
        if role != 'ego' and not cfg.get('enabled'):
            continue
        initial_speed = float(cfg.get('speed_kmh', scenario.get('ego_speed_kmh', 0)) or 0)
        label = role.replace('_', ' ').capitalize()
        state = f"{label}: {cfg.get('lane', 'Middle')} lane · {speed(role, initial_speed)}"
        if role != 'ego':
            gap = cfg.get('initial_time_gap_s')
            unit = 's'
            if gap in (None, ''):
                gap, unit = cfg.get('initial_gap_m', 0), 'm'
            gap = float(gap or 0)
            state += f" · {abs(gap):g}{unit} {'ahead' if gap >= 0 else 'behind'}"
        lines.append(state)
        events = []
        points = cfg.get('lane_change_points') or []
        if not points and cfg.get('lane_change_enabled'):
            points = [{'time_s': cfg.get('lane_change_time_s', 0),
                       'duration_s': cfg.get('lane_change_duration_s', 3),
                       'target_lane': cfg.get('lane_change_target_lane', 'Middle')}]
        for point in points:
            start = float(point.get('time_s', 0))
            end = start + float(point.get('duration_s', 0))
            events.append((start, f"{start:g}–{end:g}s: Change to {point['target_lane']} lane"))
        previous = None
        for point in sorted(cfg.get('speed_points') or [], key=lambda item: item['time_s']):
            time, value = float(point['time_s']), float(point['speed_kmh'])
            if previous is not None and previous[1] != value and time > previous[0]:
                action = 'Slow to' if value < previous[1] else 'Accelerate to'
                events.append((previous[0], f'{previous[0]:g}–{time:g}s: {action} {speed(role, value)}'))
            elif previous is None and value != initial_speed:
                events.append((time, f'{time:g}s: Speed {speed(role, value)}'))
            previous = time, value
        for key, field, unit in [('gap_points', 'gap_m', 'm'), ('time_gap_points', 'time_gap_s', 's')]:
            for point in cfg.get(key) or []:
                time, value = float(point['time_s']), float(point[field])
                events.append((time, f"{time:g}s: Gap {abs(value):g}{unit} {'ahead' if value >= 0 else 'behind'}"))
        if cfg.get('stop_enabled'):
            start = float(cfg.get('stop_time_s', 0))
            end = start + float(cfg.get('stop_duration_s', 0))
            events.append((start, f'{start:g}–{end:g}s: Stop'))
        lines.extend('  ' + event for _, event in sorted(events, key=lambda item: item[0]))
    return lines


def pedestrian_position_text(cfg):
    gap = float(cfg.get('gap_m', 0) or 0)
    direction = 'ahead of' if gap >= 0 else 'behind'
    reference = ('ego at crossing start' if cfg.get('position_reference') == 'ego_at_crossing'
                 else 'the initial ego position')
    return f'{abs(gap):g}m {direction} {reference}'


def pedestrian_review_lines(scenario):
    lines = []
    for role, cfg in sorted((scenario.get('pedestrians') or {}).items()):
        if not cfg.get('enabled'):
            continue
        start, duration = float(cfg['start_time_s']), float(cfg['duration_s'])
        lines.extend([
            f"{role.replace('_', ' ').capitalize()}: Cross {cfg['start_side']} → {cfg['end_side']}",
            f'  {start:g}–{start + duration:g}s: Cross for {duration:g}s',
            f'  Position: {pedestrian_position_text(cfg)}; crossing location stays fixed.',
        ])
    return lines


def scenario_review_lines(scenario, prompt='', summaries=()):
    lines = [f"Scenario duration: {float(scenario.get('total_time_s', 0)):g}s"]
    lines.extend(vehicle_review_lines(scenario, prompt))
    lines.extend(pedestrian_review_lines(scenario))
    from .obstacle_positions import obstacle_position_text
    for role, cfg in (scenario.get('obstacles') or {}).items():
        if cfg.get('enabled') and cfg.get('spawn_mode', 'fixed') == 'fixed':
            lines.append('{}: {} · {} lane'.format(role.replace('_', ' ').capitalize(), cfg.get('kind', 'Obstacle'), cfg.get('lane', 'Middle')))
            lines.append('  {:g}–{:g}s: {}'.format(float(cfg.get('start_time_s', 0)), float(cfg.get('end_time_s', scenario.get('total_time_s', 0))), obstacle_position_text(cfg)))
    # Keep obstacle/pedestrian/TOR details, source-position explanations and the
    # selected start location; replace only the older vehicle summary rows.
    old_vehicle = re.compile(r'^(?:Scenario duration:|Ego:|vehicle_\d+:|pedestrian_\d+:|  (?:lane change:|gap profile:|time-gap profile:|speed profile:))')
    summaries = [line for line in summaries if not any(line.startswith(role + ':') and cfg.get('spawn_mode', 'fixed') == 'fixed'
                                                      for role, cfg in (scenario.get('obstacles') or {}).items())]
    lines.extend(line for line in summaries if not old_vehicle.match(line))
    return lines


def design_event_rows(scenario, events, prompt=''):
    """Readable maneuver rows linked to unchanged visualization event indices."""
    speed = _speed_formatter(prompt)
    rows, consumed = [], set()

    def find(category, time, label, prefix=False):
        return [i for i, event in enumerate(events)
                if event['category'] == category and math.isclose(event['time_s'], time, abs_tol=1e-6)
                and (event['label'].startswith(label) if prefix else event['label'] == label)]

    def add(start, end, actor, description, indices):
        if not indices:
            return
        rows.append({'start_s': start, 'end_s': end, 'actor': actor,
                     'description': description, 'event_indices': tuple(indices)})
        consumed.update(indices)

    for role, cfg in (scenario.get('vehicles') or {}).items():
        if not cfg.get('enabled', role == 'ego'):
            continue
        label = 'ego' if role == 'ego' else role.replace('vehicle_', 'v')
        actor = role.replace('_', ' ').capitalize()
        lane = cfg.get('lane', 'Middle')
        initial_speed = float(cfg.get('speed_kmh', scenario.get('ego_speed_kmh', 0)) or 0)
        add(0, 0, actor, f'{lane} lane · {speed(role, initial_speed)}', find('vehicle', 0, label + ' start'))
        changes = cfg.get('lane_change_points') or []
        if not changes and cfg.get('lane_change_enabled'):
            changes = [{'time_s': cfg.get('lane_change_time_s', 0),
                        'duration_s': cfg.get('lane_change_duration_s', 3),
                        'target_lane': cfg.get('lane_change_target_lane', 'Middle')}]
        for point in sorted(changes, key=lambda item: item['time_s']):
            start = float(point['time_s'])
            end = start + float(point.get('duration_s', 3))
            target = point['target_lane']
            indices = find('lane', start, f'{label} lane change -> {target}')
            indices += find('lane', end, label + ' lane change end')
            add(start, end, actor, f'{lane} → {target}', indices)
            lane = target
        previous = None
        for point in sorted(cfg.get('speed_points') or [], key=lambda item: item['time_s']):
            time, value = float(point['time_s']), float(point['speed_kmh'])
            indices = find('speed', time, label + ' speed -> ', prefix=True)
            if previous is not None and previous[1] != value and time > previous[0]:
                action = 'Brake to' if value < previous[1] else 'Accelerate to'
                add(previous[0], time, actor, f'{action} {speed(role, value)}', previous[2] + indices)
            elif previous is None and value != initial_speed:
                add(time, time, actor, f'Speed {speed(role, value)}', indices)
            previous = time, value, indices
        if cfg.get('stop_enabled'):
            start = float(cfg.get('stop_time_s', 0))
            add(start, start + float(cfg.get('stop_duration_s', 0)), actor,
                f'Brake to {speed(role, 0)}', find('speed', start, label + ' stop'))

    for role, cfg in (scenario.get('pedestrians') or {}).items():
        if not cfg.get('enabled'):
            continue
        start = float(cfg['start_time_s'])
        end = start + float(cfg['duration_s'])
        indices = find('pedestrian', start, role + ' crossing')
        indices += find('pedestrian', end, role + ' crossing ends')
        add(start, end, role.replace('_', ' ').capitalize(),
            f"Cross {cfg['start_side']} → {cfg['end_side']} · {pedestrian_position_text(cfg)}", indices)

    for role, cfg in (scenario.get('obstacles') or {}).items():
        if not cfg.get('enabled'):
            continue
        start = float(cfg.get('start_time_s', 0))
        end = float(cfg.get('end_time_s', scenario.get('total_time_s', 0)))
        indices = find('obstacle', start, role + ' appears')
        indices += find('obstacle', end, role + ' removed')
        if cfg.get('spawn_mode') == 'drop_from_vehicle':
            position = 'Dropped from ' + cfg.get('drop_source_role', 'vehicle_1').replace('_', ' ')
        else:
            from .obstacle_positions import obstacle_position_text
            position = f"{cfg.get('lane', 'Middle')} lane · {obstacle_position_text(cfg)}"
        add(start, end, role.replace('_', ' ').capitalize(),
            f"{cfg.get('kind', 'Obstacle')} · {position} · Present until {end:g}s", indices)

    # Keep all other events (TOR, hazards, profiles, etc.).
    # A bare profile anchor is still visible if it did not join a maneuver.
    for index, event in enumerate(events):
        if index in consumed:
            continue
        label = event['label']
        match = re.match(r'^(ego|v[1-6])\b\s*', label)
        actor = match[1].replace('ego', 'Ego').replace('v', 'Vehicle ') if match else 'Scene'
        description = label[match.end():] if match else label
        if match and event['category'] == 'speed':
            role = 'ego' if match[1] == 'ego' else 'vehicle_' + match[1][1:]
            value = re.search(r'speed -> ([\d.]+)km/h', description)
            if value:
                description = 'Speed ' + speed(role, float(value[1]))
        add(event['time_s'], event['time_s'], actor, description[:1].upper() + description[1:], [index])
    return sorted(rows, key=lambda row: (row['start_s'], min(row['event_indices'])))
