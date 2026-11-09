"""Obstacle reference semantics shared by authoring and route bounds (Python 3.7)."""
from .vehicle_motion import planned_vehicle_state


def obstacle_prompt_position(cfg):
    dynamic = cfg.get('position_reference') == 'vehicle_at_spawn'
    seconds = cfg.get('time_gap_s') if dynamic else None
    value = float(seconds if seconds is not None else cfg.get('gap_m', 0))
    reference = cfg.get('reference_role') if dynamic else 'the initial ego position'
    return '{:g} {} {} {}'.format(abs(value), 'seconds' if seconds is not None else 'metres',
                                  'ahead of' if value >= 0 else 'behind', reference)


def obstacle_position_text(cfg):
    text = obstacle_prompt_position(cfg)
    if cfg.get('position_reference') == 'vehicle_at_spawn':
        text += ' at appearance ({:g}s); then fixed on the road'.format(float(cfg.get('start_time_s', 0)))
        if cfg.get('time_gap_s') is not None:
            text += '; seconds use that vehicle\'s speed at appearance'
    return text


def planned_obstacle_position(cfg, vehicles, ego_speed_kmh=50.0):
    gap = float(cfg.get('gap_m', 0))
    if cfg.get('position_reference') != 'vehicle_at_spawn':
        return gap
    role = cfg.get('reference_role')
    if role != 'ego' and role not in vehicles:
        raise ValueError('Define reference vehicle {} before placing the obstacle.'.format(role))
    position, speed = planned_vehicle_state(vehicles, role, float(cfg.get('start_time_s', 0)), ego_speed_kmh)
    if cfg.get('time_gap_s') is not None:
        gap = float(cfg['time_gap_s']) * speed / 3.6
    return position + gap
