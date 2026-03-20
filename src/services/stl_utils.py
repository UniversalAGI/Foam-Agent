"""Utilities for STL file handling in snappyHexMesh workflows.

Provides bounding-box parsing, unit detection, orientation analysis,
and file copying so that the meshing node can prepare STL files
before input_writer runs.
"""

import os
import re
import shutil
import struct
from typing import Dict, List, Optional, Tuple


def parse_stl_bounding_box(stl_path: str) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    """Parse an STL file and return (min_xyz, max_xyz) bounding box.

    Handles both ASCII and binary STL formats.
    """
    with open(stl_path, "rb") as f:
        header = f.read(80)

    # Heuristic: ASCII STLs start with 'solid '
    is_ascii = header[:6] == b"solid " and b"\n" in header
    if is_ascii:
        return _parse_ascii_stl_bounds(stl_path)
    return _parse_binary_stl_bounds(stl_path)


def _parse_ascii_stl_bounds(stl_path: str) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    mins = [float("inf")] * 3
    maxs = [float("-inf")] * 3
    vertex_re = re.compile(r"vertex\s+([-+eE\d.]+)\s+([-+eE\d.]+)\s+([-+eE\d.]+)", re.IGNORECASE)
    with open(stl_path, "r", errors="replace") as f:
        for line in f:
            m = vertex_re.search(line)
            if m:
                for i in range(3):
                    v = float(m.group(i + 1))
                    if v < mins[i]:
                        mins[i] = v
                    if v > maxs[i]:
                        maxs[i] = v
    return tuple(mins), tuple(maxs)  # type: ignore[return-value]


def _parse_binary_stl_bounds(stl_path: str) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
    mins = [float("inf")] * 3
    maxs = [float("-inf")] * 3
    with open(stl_path, "rb") as f:
        f.seek(80)  # skip header
        num_triangles = struct.unpack("<I", f.read(4))[0]
        for _ in range(num_triangles):
            f.read(12)  # skip normal
            for _v in range(3):
                x, y, z = struct.unpack("<fff", f.read(12))
                for i, val in enumerate((x, y, z)):
                    if val < mins[i]:
                        mins[i] = val
                    if val > maxs[i]:
                        maxs[i] = val
            f.read(2)  # attribute byte count
    return tuple(mins), tuple(maxs)  # type: ignore[return-value]


def detect_stl_units(bounds_min: Tuple[float, float, float], bounds_max: Tuple[float, float, float]) -> str:
    """Guess whether STL coordinates are in meters or millimeters.

    Heuristic: if the largest extent exceeds 100, assume millimeters.
    """
    extents = [abs(bounds_max[i] - bounds_min[i]) for i in range(3)]
    max_extent = max(extents)
    return "millimeters" if max_extent > 100 else "meters"


def detect_vertical_axis(bounds_min: Tuple[float, float, float], bounds_max: Tuple[float, float, float]) -> str:
    """Guess the vertical axis based on extent ratios.

    Heuristic: report the axis with the smallest extent as a candidate
    for vertical (many CFD objects like cars/drones are wider/longer than tall).
    For tall objects (e.g., bioreactors, towers) the tallest axis is vertical.
    We fall back to 'y' as the OpenFOAM convention if ambiguous.
    """
    extents = [abs(bounds_max[i] - bounds_min[i]) for i in range(3)]
    labels = ["x", "y", "z"]
    sorted_axes = sorted(zip(extents, labels))
    min_ext, min_axis = sorted_axes[0]
    max_ext, max_axis = sorted_axes[2]

    # If the tallest axis is > 3x the shortest, it's likely vertical (e.g. bioreactor)
    if max_ext > 3 * min_ext:
        return max_axis
    # Otherwise, default to y (OpenFOAM convention) or the shortest axis
    return "y" if "y" in [a for _, a in sorted_axes[:2]] else min_axis


def analyze_stl_orientation(
    stl_path: str,
    bounds_min: Tuple[float, float, float],
    bounds_max: Tuple[float, float, float],
    vertical_axis: str = "y",
) -> Optional[str]:
    """Analyze cross-sections to determine which end of the STL is the
    aerodynamic front (tapered/narrow) vs rear (blunt/wide).

    Slices the geometry near each end of the longest horizontal axis and
    compares approximate cross-sectional areas.  Returns a human-readable
    string suitable for injection into LLM prompts, or None if analysis
    is inconclusive.
    """
    try:
        extents = [abs(bounds_max[i] - bounds_min[i]) for i in range(3)]
        axis_labels = ["x", "y", "z"]
        axis_idx = {"x": 0, "y": 1, "z": 2}
        vert_idx = axis_idx.get(vertical_axis, 1)

        # Find the longest *horizontal* axis (skip the vertical one)
        horiz = [(extents[i], axis_labels[i], i) for i in range(3) if i != vert_idx]
        horiz.sort(reverse=True)
        long_ext, long_axis, long_idx = horiz[0]

        if long_ext < 1e-6:
            return None

        # Cross-section axes (the two non-long axes)
        cross_axes = [i for i in range(3) if i != long_idx]

        # Slice at 25% from each end
        lo = bounds_min[long_idx] + 0.25 * long_ext  # near minus end
        hi = bounds_max[long_idx] - 0.25 * long_ext  # near plus end

        lo_area = _estimate_cross_section_area(stl_path, long_idx, lo, cross_axes)
        hi_area = _estimate_cross_section_area(stl_path, long_idx, hi, cross_axes)

        if lo_area is None or hi_area is None or (lo_area < 1e-9 and hi_area < 1e-9):
            return None

        ratio = max(lo_area, hi_area) / max(min(lo_area, hi_area), 1e-12)
        if ratio < 1.15:
            # Cross-sections are roughly equal — can't determine orientation
            return None

        upper_long = long_axis.upper()
        if lo_area > hi_area:
            blunt_end = f"negative {upper_long}"
            tapered_end = f"positive {upper_long}"
        else:
            blunt_end = f"positive {upper_long}"
            tapered_end = f"negative {upper_long}"

        lines = [
            f"Cross-section analysis along {upper_long} axis (longest horizontal):",
            f"  - Near -{upper_long} end ({upper_long}={lo:.4f}): approx area = {lo_area:.6f}",
            f"  - Near +{upper_long} end ({upper_long}={hi:.4f}): approx area = {hi_area:.6f}",
            f"  - BLUNT/WIDE end (likely rear): {blunt_end}",
            f"  - TAPERED/NARROW end (likely front/nose): {tapered_end}",
            f"  → For external aero, the inlet (freestream) should face the TAPERED end.",
            f"  → Flow should travel TOWARD the tapered ({tapered_end}) end,",
            f"    i.e. from the {blunt_end} side toward the {tapered_end} side.",
        ]
        return "\n".join(lines)

    except Exception:
        return None


def _estimate_cross_section_area(
    stl_path: str,
    slice_axis: int,
    slice_pos: float,
    cross_axes: List[int],
) -> Optional[float]:
    """Estimate the cross-sectional extent at a given slice position along an axis.

    For efficiency, scans binary STL triangles and collects vertices near
    the slice position, then returns the bounding-box area of those vertices
    in the two cross-section axes.  This is a fast approximation — not an
    exact cross-sectional area — but sufficient to compare relative sizes.
    """
    tolerance_frac = 0.05  # 5% of total extent as slice thickness

    try:
        with open(stl_path, "rb") as f:
            header = f.read(80)
            is_ascii = header[:6] == b"solid " and b"\n" in header

        if is_ascii:
            return _cross_section_ascii(stl_path, slice_axis, slice_pos,
                                        cross_axes, tolerance_frac)
        return _cross_section_binary(stl_path, slice_axis, slice_pos,
                                     cross_axes, tolerance_frac)
    except Exception:
        return None


def _cross_section_binary(
    stl_path: str, axis: int, pos: float,
    cross: List[int], tol_frac: float,
) -> Optional[float]:
    mins = [float("inf")] * 3
    maxs = [float("-inf")] * 3
    with open(stl_path, "rb") as f:
        f.seek(80)
        n = struct.unpack("<I", f.read(4))[0]
        # First pass: find extent along slice axis for tolerance calc
        positions = []
        data_start = f.tell()
        for _ in range(n):
            f.read(12)  # normal
            for _ in range(3):
                coords = struct.unpack("<fff", f.read(12))
                for i in range(3):
                    if coords[i] < mins[i]: mins[i] = coords[i]
                    if coords[i] > maxs[i]: maxs[i] = coords[i]
            f.read(2)

    extent = maxs[axis] - mins[axis]
    tol = extent * tol_frac

    # Second pass: collect cross-section extents
    cs_mins = [float("inf")] * 2
    cs_maxs = [float("-inf")] * 2
    found = False
    with open(stl_path, "rb") as f:
        f.seek(84)
        for _ in range(n):
            f.read(12)
            for _ in range(3):
                coords = struct.unpack("<fff", f.read(12))
                if abs(coords[axis] - pos) <= tol:
                    found = True
                    for j, ci in enumerate(cross):
                        if coords[ci] < cs_mins[j]: cs_mins[j] = coords[ci]
                        if coords[ci] > cs_maxs[j]: cs_maxs[j] = coords[ci]
            f.read(2)

    if not found:
        return None
    return (cs_maxs[0] - cs_mins[0]) * (cs_maxs[1] - cs_mins[1])


def _cross_section_ascii(
    stl_path: str, axis: int, pos: float,
    cross: List[int], tol_frac: float,
) -> Optional[float]:
    # Get full extent first
    bmin, bmax = _parse_ascii_stl_bounds(stl_path)
    extent = bmax[axis] - bmin[axis]
    tol = extent * tol_frac

    cs_mins = [float("inf")] * 2
    cs_maxs = [float("-inf")] * 2
    found = False
    vertex_re = re.compile(r"vertex\s+([-+eE\d.]+)\s+([-+eE\d.]+)\s+([-+eE\d.]+)", re.IGNORECASE)
    with open(stl_path, "r", errors="replace") as f:
        for line in f:
            m = vertex_re.search(line)
            if m:
                coords = [float(m.group(i + 1)) for i in range(3)]
                if abs(coords[axis] - pos) <= tol:
                    found = True
                    for j, ci in enumerate(cross):
                        if coords[ci] < cs_mins[j]: cs_mins[j] = coords[ci]
                        if coords[ci] > cs_maxs[j]: cs_maxs[j] = coords[ci]
    if not found:
        return None
    return (cs_maxs[0] - cs_mins[0]) * (cs_maxs[1] - cs_mins[1])


def build_stl_context(stl_paths: List[str]) -> str:
    """Build a text block describing all STL files for injection into LLM prompts.

    Returns a formatted string with filenames, bounding boxes, detected units,
    and the vertical axis.
    """
    if not stl_paths:
        return ""

    lines = [
        "=== STL GEOMETRY METADATA (auto-detected, DO NOT override) ===",
        "The following STL files are pre-loaded at constant/triSurface/.",
        "Do NOT generate STL geometry inline. Reference these filenames in snappyHexMeshDict.",
        "",
    ]
    all_mins = [float("inf")] * 3
    all_maxs = [float("-inf")] * 3

    for path in stl_paths:
        name = os.path.basename(path)
        try:
            bmin, bmax = parse_stl_bounding_box(path)
            for i in range(3):
                if bmin[i] < all_mins[i]:
                    all_mins[i] = bmin[i]
                if bmax[i] > all_maxs[i]:
                    all_maxs[i] = bmax[i]
            lines.append(
                f"  - {name}: bounds ({bmin[0]:.4f}, {bmin[1]:.4f}, {bmin[2]:.4f}) to ({bmax[0]:.4f}, {bmax[1]:.4f}, {bmax[2]:.4f})"
            )
        except Exception as e:
            lines.append(f"  - {name}: (could not parse bounds: {e})")

    units = detect_stl_units(tuple(all_mins), tuple(all_maxs))  # type: ignore[arg-type]
    vert = detect_vertical_axis(tuple(all_mins), tuple(all_maxs))  # type: ignore[arg-type]

    lines.append("")
    lines.append(f"Overall bounding box: ({all_mins[0]:.4f}, {all_mins[1]:.4f}, {all_mins[2]:.4f}) to ({all_maxs[0]:.4f}, {all_maxs[1]:.4f}, {all_maxs[2]:.4f})")
    ext_x = abs(all_maxs[0] - all_mins[0])
    ext_y = abs(all_maxs[1] - all_mins[1])
    ext_z = abs(all_maxs[2] - all_mins[2])
    lines.append(f"Axis extents: X={ext_x:.4f}, Y={ext_y:.4f}, Z={ext_z:.4f}")
    lines.append(f"Detected coordinate units: {units}")
    lines.append(f"Likely vertical axis (heuristic): {vert} — MUST follow user requirement if it specifies otherwise.")
    lines.append(
        "IMPORTANT: blockMeshDict domain MUST enclose this bounding box with appropriate margin (2-5x object size for external aero)."
    )
    lines.append(
        f"IMPORTANT: Gravity direction should be (0, -9.81, 0) m/s^2 for Y-up or (0, 0, -9.81) for Z-up, as specified in the user requirement."
    )

    # Cross-section orientation analysis for each STL
    for path in stl_paths:
        try:
            bmin, bmax = parse_stl_bounding_box(path)
            orientation = analyze_stl_orientation(path, bmin, bmax, vert)
            if orientation:
                lines.append("")
                lines.append(f"=== ORIENTATION ANALYSIS for {os.path.basename(path)} ===")
                lines.append(orientation)
        except Exception:
            pass

    lines.append("")
    return "\n".join(lines)


def copy_stls_to_trisurface(stl_paths: List[str], case_dir: str) -> List[str]:
    """Copy STL files into case_dir/constant/triSurface/.

    Returns the list of destination paths.
    """
    tri_dir = os.path.join(case_dir, "constant", "triSurface")
    os.makedirs(tri_dir, exist_ok=True)
    destinations = []
    for src in stl_paths:
        dst = os.path.join(tri_dir, os.path.basename(src))
        shutil.copy2(src, dst)
        print(f"Copied STL: {src} → {dst}")
        destinations.append(dst)
    return destinations


def find_stl_files(directory: str) -> List[str]:
    """Recursively find all .stl files in a directory."""
    stl_files = []
    if os.path.isfile(directory) and directory.lower().endswith(".stl"):
        return [directory]
    if not os.path.isdir(directory):
        return []
    for root, _dirs, files in os.walk(directory):
        for f in files:
            if f.lower().endswith(".stl"):
                stl_files.append(os.path.join(root, f))
    return sorted(stl_files)
