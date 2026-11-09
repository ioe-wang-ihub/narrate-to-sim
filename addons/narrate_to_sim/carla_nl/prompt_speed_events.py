"""Checked piecewise speed profiles for natural-language driving actions."""
import re

from .prompt_english import RATE
from .vehicle_motion import planned_distance_m

VALUE = r"\d+(?:\.\d+)?\s*(?:km/h|kmh|kph|mph|m/s)\b"


def validate_stationary_hold(cfg, start, end):
    """Check both boundaries and all knots of the complete linear profile."""
    points = [(p["time_s"], p["speed_kmh"]) for p in cfg.get("speed_points", [])]

    def speed_at(time):
        previous_time, previous_speed = 0.0, cfg["speed_kmh"]
        for next_time, next_speed in points:
            if time < next_time:
                if next_time == previous_time:
                    return next_speed
                fraction = (time - previous_time) / (next_time - previous_time)
                return previous_speed + fraction * (next_speed - previous_speed)
            previous_time, previous_speed = next_time, next_speed
        return previous_speed

    times = [start, end] + [time for time, _ in points if start < time < end]
    if any(abs(speed_at(time)) > 1e-6 for time in times):
        raise ValueError("Stationary hold requires zero speed throughout the waiting interval.")


def speed_target(sentence, current_speed, speed):
    without_rate = RATE.sub(" ", sentence)
    target = re.search(rf"\b(to|by)\s+({VALUE})", without_rate, re.I)
    if not target:
        raise ValueError("Missing target speed: specify braking to a speed, reducing by a speed, or coming to a stop. / 请说明目标速度、降速幅度或停车。")
    value = speed(target[2])
    if target[1].lower() == "by":
        if re.search(r'\badjusts speed\b', sentence, re.I):
            raise ValueError('Specify whether to increase or decrease by this amount, or use a final target speed with to.')
        value = current_speed + (value if re.search(r"\baccelerates\b", sentence, re.I) else -value)
    return value


def speed_event_duration(sentence, start, cfg, speed, explicit_duration):
    rates = list(RATE.finditer(sentence))
    if len(rates) != 1 or float(rates[0][1]) <= 0:
        raise ValueError("Specify one positive acceleration/deceleration rate.")
    current = cfg.get("speed_points", [])[-1]["speed_kmh"] if cfg.get("speed_points") else cfg["speed_kmh"]
    target = speed_target(sentence, current, speed)
    increasing = target > current if re.search(r'\badjusts speed\b', sentence, re.I) else bool(re.search(r"\baccelerates\b", sentence, re.I))
    if (increasing and "deceleration" in rates[0][0].lower()) or (not increasing and re.search(r"\bacceleration\b", rates[0][0], re.I)):
        raise ValueError("Conflicting speed action and acceleration/deceleration rate wording.")
    duration = abs(target - current) / 3.6 / float(rates[0][1])
    if duration <= 0:
        raise ValueError("Speed change needs a different target speed.")
    if explicit_duration is not None and abs(duration - explicit_duration) > 1e-6:
        raise ValueError(f"Conflicting rate and event duration: the speed change and rate require {duration:.15g}s, but the supplied duration is {explicit_duration:.15g}s. / 速度差與加減速度需要 {duration:.15g}s，與指定的 {explicit_duration:.15g}s 不符。")
    return duration


def apply_speed_event(cfg, sentence, start, duration, speed, speed_pattern):
    points = cfg.setdefault("speed_points", [])
    if points and start < points[-1]["time_s"]:
        raise ValueError("Speed change overlaps the previous speed change.")
    current = points[-1]["speed_kmh"] if points else cfg["speed_kmh"]
    without_rate = RATE.sub(" ", sentence)
    if len(re.findall(rf"\bfrom\s+({VALUE})", without_rate, re.I)) > 1:
        raise ValueError("Specify one source speed for this speed change.")
    source = re.search(rf"\bfrom\s+({VALUE})", without_rate, re.I)
    if source and abs(speed(source[1]) - current) > 1e-6:
        raise ValueError("Conflicting braking source speed / 与当时的车辆速度不一致。")
    target = speed_target(sentence, current, speed)
    increasing = target > current if re.search(r'\badjusts speed\b', sentence, re.I) else bool(re.search(r"\baccelerates\b", sentence, re.I))
    if target < 0 or (increasing and target <= current) or (not increasing and target >= current):
        raise ValueError("Conflicting acceleration/deceleration and target speed.")
    hold = re.search(rf"\bthen\s+keeps\s+({VALUE})", without_rate, re.I)
    if len(re.findall(rf"\bthen\s+keeps\s+({VALUE})", without_rate, re.I)) > 1:
        raise ValueError("Specify one final speed after this speed change.")
    if hold and abs(speed(hold[1]) - target) > 1e-6:
        raise ValueError("Conflicting final speed.")
    if len(speed_pattern.findall(without_rate)) != 1 + bool(source) + bool(hold):
        raise ValueError("Conflicting speed values: specify one target speed.")
    if not points or points[-1]["time_s"] != start:
        points.append({"time_s": start, "speed_kmh": current})
    points.append({"time_s": start + duration, "speed_kmh": target})
