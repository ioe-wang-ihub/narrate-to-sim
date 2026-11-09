from __future__ import annotations

import argparse
import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


PAVED_LANE_TYPES = {"driving", "shoulder"}


@dataclass(frozen=True)
class CubicRecord:
    s: float
    a: float
    b: float
    c: float
    d: float

    @classmethod
    def from_element(cls, element: ET.Element, s_attr: str) -> "CubicRecord":
        return cls(
            s=float(element.get(s_attr, "0")),
            a=float(element.get("a", "0")),
            b=float(element.get("b", "0")),
            c=float(element.get("c", "0")),
            d=float(element.get("d", "0")),
        )

    def value_at(self, absolute_s: float) -> float:
        ds = absolute_s - self.s
        return self.a + self.b * ds + self.c * ds * ds + self.d * ds * ds * ds


@dataclass(frozen=True)
class Geometry:
    s: float
    x: float
    y: float
    hdg: float
    length: float
    kind: str
    curvature: float = 0.0

    @classmethod
    def from_element(cls, element: ET.Element) -> "Geometry":
        child = next(iter(element), None)
        kind = child.tag if child is not None else "line"
        curvature = float(child.get("curvature", "0")) if child is not None else 0.0
        return cls(
            s=float(element.get("s", "0")),
            x=float(element.get("x", "0")),
            y=float(element.get("y", "0")),
            hdg=float(element.get("hdg", "0")),
            length=float(element.get("length", "0")),
            kind=kind,
            curvature=curvature,
        )

    def point_at(self, road_s: float) -> tuple[float, float, float]:
        ds = max(0.0, min(self.length, road_s - self.s))
        if self.kind == "arc" and abs(self.curvature) > 1e-12:
            hdg = self.hdg + ds * self.curvature
            x = self.x + (math.sin(hdg) - math.sin(self.hdg)) / self.curvature
            y = self.y - (math.cos(hdg) - math.cos(self.hdg)) / self.curvature
            return x, y, hdg
        return (
            self.x + ds * math.cos(self.hdg),
            self.y + ds * math.sin(self.hdg),
            self.hdg,
        )


@dataclass(frozen=True)
class Lane:
    lane_id: int
    lane_type: str
    widths: tuple[CubicRecord, ...]

    @classmethod
    def from_element(cls, element: ET.Element) -> "Lane":
        widths = tuple(
            sorted(
                (CubicRecord.from_element(width, "sOffset") for width in element.findall("width")),
                key=lambda record: record.s,
            )
        )
        return cls(
            lane_id=int(element.get("id", "0")),
            lane_type=element.get("type", "none"),
            widths=widths,
        )

    def width_at(self, section_s: float, road_s: float) -> float:
        if not self.widths:
            return 0.0
        local_s = max(0.0, road_s - section_s)
        record = latest_record(self.widths, local_s)
        return max(0.0, record.value_at(local_s))


@dataclass(frozen=True)
class LaneSection:
    s: float
    left: tuple[Lane, ...]
    right: tuple[Lane, ...]


@dataclass(frozen=True)
class Road:
    road_id: str
    length: float
    geometries: tuple[Geometry, ...]
    lane_offsets: tuple[CubicRecord, ...]
    sections: tuple[LaneSection, ...]


def latest_record(records: tuple[CubicRecord, ...], s: float) -> CubicRecord:
    latest = records[0]
    for record in records[1:]:
        if record.s <= s + 1e-9:
            latest = record
        else:
            break
    return latest


def parse_roads(xodr_path: Path) -> list[Road]:
    root = ET.parse(xodr_path).getroot()
    roads: list[Road] = []
    for road_element in root.findall("road"):
        lanes_element = road_element.find("lanes")
        if lanes_element is None:
            continue

        lane_offsets = tuple(
            sorted(
                (
                    CubicRecord.from_element(lane_offset, "s")
                    for lane_offset in lanes_element.findall("laneOffset")
                ),
                key=lambda record: record.s,
            )
        ) or (CubicRecord(0.0, 0.0, 0.0, 0.0, 0.0),)

        sections: list[LaneSection] = []
        for section_element in lanes_element.findall("laneSection"):
            left = tuple(
                sorted(
                    (
                        Lane.from_element(lane_element)
                        for lane_element in section_element.findall("left/lane")
                    ),
                    key=lambda lane: lane.lane_id,
                )
            )
            right = tuple(
                sorted(
                    (
                        Lane.from_element(lane_element)
                        for lane_element in section_element.findall("right/lane")
                    ),
                    key=lambda lane: abs(lane.lane_id),
                )
            )
            sections.append(
                LaneSection(
                    s=float(section_element.get("s", "0")),
                    left=left,
                    right=right,
                )
            )

        roads.append(
            Road(
                road_id=road_element.get("id", ""),
                length=float(road_element.get("length", "0")),
                geometries=tuple(
                    sorted(
                        (
                            Geometry.from_element(geometry)
                            for geometry in road_element.findall("planView/geometry")
                        ),
                        key=lambda geometry: geometry.s,
                    )
                ),
                lane_offsets=lane_offsets,
                sections=tuple(sorted(sections, key=lambda section: section.s)),
            )
        )
    return roads


def road_point(road: Road, road_s: float) -> tuple[float, float, float]:
    geometry = road.geometries[0]
    for candidate in road.geometries[1:]:
        if candidate.s <= road_s + 1e-9:
            geometry = candidate
        else:
            break
    return geometry.point_at(road_s)


def lateral_point(road: Road, road_s: float, lateral_offset: float) -> tuple[float, float]:
    x, y, hdg = road_point(road, road_s)
    return (
        x - math.sin(hdg) * lateral_offset,
        y + math.cos(hdg) * lateral_offset,
    )


def sample_values(start_s: float, end_s: float, step_m: float) -> list[float]:
    if end_s <= start_s:
        return []
    values = [start_s]
    s = start_s
    while s + step_m < end_s:
        s += step_m
        values.append(s)
    if end_s - values[-1] > 1e-6:
        values.append(end_s)
    return values


def lane_offset_at(road: Road, road_s: float) -> float:
    record = latest_record(road.lane_offsets, road_s)
    return record.value_at(road_s)


def side_offset_pair(
    section: LaneSection,
    road: Road,
    lane: Lane,
    road_s: float,
    side: str,
) -> tuple[float, float]:
    offset = lane_offset_at(road, road_s)
    if side == "left":
        inner = offset
        for other in section.left:
            if other.lane_id >= lane.lane_id:
                break
            inner += other.width_at(section.s, road_s)
        outer = inner + lane.width_at(section.s, road_s)
        return inner, outer

    inner = offset
    for other in section.right:
        if abs(other.lane_id) >= abs(lane.lane_id):
            break
        inner -= other.width_at(section.s, road_s)
    outer = inner - lane.width_at(section.s, road_s)
    return inner, outer


def lane_polygon(
    road: Road,
    section: LaneSection,
    section_end_s: float,
    lane: Lane,
    side: str,
    step_m: float,
) -> list[tuple[float, float]]:
    samples = sample_values(section.s, section_end_s, step_m)
    if len(samples) < 2:
        return []

    inner_points: list[tuple[float, float]] = []
    outer_points: list[tuple[float, float]] = []
    for road_s in samples:
        inner, outer = side_offset_pair(section, road, lane, road_s, side)
        if abs(outer - inner) < 1e-4:
            continue
        inner_points.append(lateral_point(road, road_s, inner))
        outer_points.append(lateral_point(road, road_s, outer))

    if len(inner_points) < 2 or len(outer_points) < 2:
        return []
    return inner_points + list(reversed(outer_points))


def build_paved_polygons(
    roads: list[Road],
    step_m: float,
) -> tuple[list[list[tuple[float, float]]], list[list[tuple[float, float]]], dict[str, int]]:
    driving_polygons: list[list[tuple[float, float]]] = []
    shoulder_polygons: list[list[tuple[float, float]]] = []
    counts = {"roads": len(roads), "driving_lanes": 0, "shoulder_lanes": 0}

    for road in roads:
        if not road.sections or not road.geometries:
            continue
        for index, section in enumerate(road.sections):
            section_end_s = road.length
            if index + 1 < len(road.sections):
                section_end_s = min(section_end_s, road.sections[index + 1].s)

            for side_name, lanes in (("left", section.left), ("right", section.right)):
                for lane in lanes:
                    if lane.lane_type not in PAVED_LANE_TYPES:
                        continue
                    polygon = lane_polygon(road, section, section_end_s, lane, side_name, step_m)
                    if not polygon:
                        continue
                    if lane.lane_type == "driving":
                        driving_polygons.append(polygon)
                        counts["driving_lanes"] += 1
                    else:
                        shoulder_polygons.append(polygon)
                        counts["shoulder_lanes"] += 1

    return driving_polygons, shoulder_polygons, counts


def parse_spawn(config_path: Path) -> tuple[float, float, float] | None:
    if not config_path.exists():
        return None
    text = config_path.read_text(errors="ignore")
    match = re.search(
        r"SpawnEgoVehicleTransform=\(X=([-+0-9.]+),\s*Y=([-+0-9.]+).*?Y=([-+0-9.]+)\s*\|",
        text,
    )
    if match is None:
        return None
    x_cm, y_cm, yaw_deg = (float(group) for group in match.groups())
    # DReyeVR config stores UE coordinates in centimeters. The OpenDRIVE map
    # axes rendered below use meters and a flipped Y axis.
    return x_cm / 100.0, -y_cm / 100.0, -yaw_deg


def render_map(
    driving_polygons: list[list[tuple[float, float]]],
    shoulder_polygons: list[list[tuple[float, float]]],
    output_base: Path,
    title: str,
    spawn: tuple[float, float, float] | None,
    counts: dict[str, int],
) -> None:
    import matplotlib.pyplot as plt
    from matplotlib.collections import PolyCollection
    from matplotlib.lines import Line2D

    output_base.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(12, 10), dpi=180)
    fig.patch.set_facecolor("#f7f5ef")
    ax.set_facecolor("#f7f5ef")

    if shoulder_polygons:
        ax.add_collection(
            PolyCollection(
                shoulder_polygons,
                facecolors="#b7b7b0",
                edgecolors="#9b9b94",
                linewidths=0.08,
                zorder=1,
            )
        )
    if driving_polygons:
        ax.add_collection(
            PolyCollection(
                driving_polygons,
                facecolors="#3b3d40",
                edgecolors="#27292c",
                linewidths=0.08,
                zorder=2,
            )
        )

    all_points = [
        point
        for polygon in driving_polygons + shoulder_polygons
        for point in polygon
    ]
    if spawn is not None:
        spawn_x, spawn_y, _ = spawn
        all_points.append((spawn_x, spawn_y))
    min_x = min(x for x, _ in all_points)
    max_x = max(x for x, _ in all_points)
    min_y = min(y for _, y in all_points)
    max_y = max(y for _, y in all_points)
    pad = max(max_x - min_x, max_y - min_y) * 0.035
    ax.set_xlim(min_x - pad, max_x + pad)
    ax.set_ylim(min_y - pad, max_y + pad)
    ax.set_aspect("equal", adjustable="box")

    if spawn is not None:
        spawn_x, spawn_y, yaw_deg = spawn
        yaw = math.radians(yaw_deg)
        ax.scatter([spawn_x], [spawn_y], s=42, c="#d5272f", zorder=5)
        ax.arrow(
            spawn_x,
            spawn_y,
            22.0 * math.cos(yaw),
            22.0 * math.sin(yaw),
            width=1.4,
            head_width=8.0,
            head_length=10.0,
            length_includes_head=True,
            color="#d5272f",
            zorder=5,
        )
        ax.text(
            spawn_x + 12.0,
            spawn_y + 12.0,
            "DReyeVR spawn",
            color="#a1131a",
            fontsize=8,
            weight="bold",
            zorder=6,
        )

    ax.set_title(title, fontsize=14, weight="bold", pad=12)
    ax.set_xlabel("OpenDRIVE X (m)")
    ax.set_ylabel("OpenDRIVE Y (m)")
    ax.grid(color="#dedbd2", linewidth=0.35)
    ax.legend(
        handles=[
            Line2D([0], [0], color="#3b3d40", lw=7, label="driving lanes"),
            Line2D([0], [0], color="#b7b7b0", lw=7, label="shoulders"),
            Line2D([0], [0], marker="o", color="w", markerfacecolor="#d5272f", markersize=7, label="ego spawn"),
        ],
        loc="lower right",
        frameon=True,
        framealpha=0.92,
    )

    note = (
        f"Source: Town04_Opt.xodr | roads={counts['roads']} | "
        f"driving lane sections={counts['driving_lanes']} | "
        f"shoulder lane sections={counts['shoulder_lanes']}"
    )
    fig.text(0.01, 0.01, note, fontsize=7, color="#555555")
    fig.tight_layout(rect=(0, 0.02, 1, 1))

    fig.savefig(output_base.with_suffix(".png"))
    fig.savefig(output_base.with_suffix(".svg"))
    plt.close(fig)


def default_xodr_path(repo_root: Path) -> Path:
    candidates = [
        repo_root / "carlaCache" / "0.9.13" / "Carla" / "Maps" / "OpenDrive" / "Town04_Opt.xodr",
        repo_root / "CARLA_0.9.16" / "CarlaUE4" / "Content" / "Carla" / "Maps" / "OpenDrive" / "Town04_Opt.xodr",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Render Town04_Opt paved road surfaces from OpenDRIVE lane geometry."
    )
    parser.add_argument("--xodr", type=Path, default=default_xodr_path(repo_root))
    parser.add_argument(
        "--output-base",
        type=Path,
        default=repo_root / "Carla_YJC_version" / "outputs" / "maps" / "town04_opt_paved_roads",
    )
    parser.add_argument(
        "--spawn-config",
        type=Path,
        default=repo_root
        / "carla-dreyevr-0.9.13"
        / "Unreal"
        / "CarlaUE4"
        / "Config"
        / "DReyeVRConfig.ini",
    )
    parser.add_argument("--step-m", type=float, default=2.0)
    args = parser.parse_args()

    roads = parse_roads(args.xodr)
    driving_polygons, shoulder_polygons, counts = build_paved_polygons(roads, args.step_m)
    spawn = parse_spawn(args.spawn_config)
    render_map(
        driving_polygons,
        shoulder_polygons,
        args.output_base,
        "Town04_Opt paved road surface",
        spawn,
        counts,
    )

    metadata = {
        "xodr": str(args.xodr),
        "output_png": str(args.output_base.with_suffix(".png")),
        "output_svg": str(args.output_base.with_suffix(".svg")),
        "step_m": args.step_m,
        "paved_lane_types": sorted(PAVED_LANE_TYPES),
        "counts": counts,
        "spawn_m": spawn,
    }
    metadata_path = args.output_base.with_suffix(".json")
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
