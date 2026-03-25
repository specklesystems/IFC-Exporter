# =============================================================================
# curves.py
# Converts Speckle 2D curve geometry (Polycurve, Line, Arc, Circle, Polyline)
# into IFC IfcIndexedPolyCurve representations.
#
# Curve types in segments:
#   - Objects.Geometry.Line     → start/end Points → IfcLineIndex
#   - Objects.Geometry.Arc      → startPoint/midPoint/endPoint → IfcArcIndex
#   - Objects.Geometry.Circle   → converted to arc segments
#   - Objects.Geometry.Polyline → point sequence → IfcLineIndex chains
#
# The result is an IfcIndexedPolyCurve with IfcCartesianPointList3D.
# =============================================================================

import ifcopenshell
import ifcopenshell.api
from specklepy.objects.base import Base
from utils.helpers import _get, MM_SCALES
from utils.geometry import _get_shared, _make_placement


# Speckle types that are curve geometry
_CURVE_TYPES = {"Line", "Arc", "Circle", "Ellipse", "Polycurve", "Polyline", "Curve"}


def is_curve(obj) -> bool:
    """Return True if this object is a Speckle curve type."""
    speckle_type = _get(obj, "speckle_type") or ""
    return any(ct in speckle_type for ct in _CURVE_TYPES)


def _resolve_scale(obj, fallback: float) -> float:
    """Resolve unit scale for a curve object."""
    units = _get(obj, "units")
    if units and isinstance(units, str):
        return MM_SCALES.get(units.lower().strip(), fallback)
    return fallback


def _point_coords(pt, scale: float) -> tuple:
    """Extract (x, y, z) from a Speckle Point, scaled to mm and rounded."""
    x = round(float(_get(pt, "x") or 0) * scale, 3)
    y = round(float(_get(pt, "y") or 0) * scale, 3)
    z = round(float(_get(pt, "z") or 0) * scale, 3)
    return x, y, z


def _extract_polycurve(obj, scale: float) -> tuple:
    """
    Extract points and segment indices from a Polycurve.

    Returns (points_3d, segments) where:
      points_3d: list of [x, y, z] coordinate lists
      segments:  list of IfcLineIndex/IfcArcIndex-compatible tuples
                 ("line", [i, j]) or ("arc", [i, mid, j])  (1-based)
    """
    segments_raw = _get(obj, "segments") or []
    if not isinstance(segments_raw, list):
        segments_raw = list(segments_raw)
    if not segments_raw:
        return [], []

    obj_scale = _resolve_scale(obj, scale)
    points = []       # list of [x, y, z]
    point_map = {}    # (rounded_x, rounded_y, rounded_z) -> 1-based index
    ifc_segments = []

    def _add_point(pt, seg_scale: float) -> int:
        """Add a point and return its 1-based index (deduplicating nearby points)."""
        x, y, z = _point_coords(pt, seg_scale)
        # Snap to 0.01mm grid for deduplication
        key = (round(x * 100), round(y * 100), round(z * 100))
        if key in point_map:
            return point_map[key]
        idx = len(points) + 1  # 1-based for IFC
        points.append([x, y, z])
        point_map[key] = idx
        return idx

    for seg in segments_raw:
        if seg is None:
            continue
        seg_type = (_get(seg, "speckle_type") or "").split(".")[-1]
        seg_scale = _resolve_scale(seg, obj_scale)

        if seg_type == "Line":
            start_pt = _get(seg, "start")
            end_pt = _get(seg, "end")
            if start_pt is None or end_pt is None:
                continue
            i = _add_point(start_pt, seg_scale)
            j = _add_point(end_pt, seg_scale)
            if i != j:
                ifc_segments.append(("line", [i, j]))

        elif seg_type == "Arc":
            start_pt = _get(seg, "startPoint")
            mid_pt = _get(seg, "midPoint")
            end_pt = _get(seg, "endPoint")
            if start_pt is None or mid_pt is None or end_pt is None:
                continue
            i = _add_point(start_pt, seg_scale)
            m = _add_point(mid_pt, seg_scale)
            j = _add_point(end_pt, seg_scale)
            if i != j and i != m and m != j:
                ifc_segments.append(("arc", [i, m, j]))

        elif seg_type == "Polyline":
            raw_value = _get(seg, "value") or []
            if not raw_value:
                continue
            values = list(raw_value) if not isinstance(raw_value, list) else raw_value
            indices = []
            for vi in range(0, len(values) - 2, 3):
                x = round(float(values[vi]) * seg_scale, 3)
                y = round(float(values[vi + 1]) * seg_scale, 3)
                z = round(float(values[vi + 2]) * seg_scale, 3)
                key = (round(x * 100), round(y * 100), round(z * 100))
                if key in point_map:
                    idx = point_map[key]
                else:
                    idx = len(points) + 1
                    points.append([x, y, z])
                    point_map[key] = idx
                indices.append(idx)
            if len(indices) >= 2:
                ifc_segments.append(("line", indices))

    return points, ifc_segments


def _extract_single_line(obj, scale: float) -> tuple:
    """Extract a single Line as points + segment."""
    obj_scale = _resolve_scale(obj, scale)
    start_pt = _get(obj, "start")
    end_pt = _get(obj, "end")
    if start_pt is None or end_pt is None:
        return [], []
    sx, sy, sz = _point_coords(start_pt, obj_scale)
    ex, ey, ez = _point_coords(end_pt, obj_scale)
    return [[sx, sy, sz], [ex, ey, ez]], [("line", [1, 2])]


def _extract_single_arc(obj, scale: float) -> tuple:
    """Extract a single Arc as points + segment."""
    obj_scale = _resolve_scale(obj, scale)
    start_pt = _get(obj, "startPoint")
    mid_pt = _get(obj, "midPoint")
    end_pt = _get(obj, "endPoint")
    if start_pt is None or mid_pt is None or end_pt is None:
        return [], []
    sx, sy, sz = _point_coords(start_pt, obj_scale)
    mx, my, mz = _point_coords(mid_pt, obj_scale)
    ex, ey, ez = _point_coords(end_pt, obj_scale)
    return [[sx, sy, sz], [mx, my, mz], [ex, ey, ez]], [("arc", [1, 2, 3])]


def extract_curve_data(obj, scale: float = 1.0) -> tuple:
    """
    Extract curve points and segments from any supported curve type.
    Returns (points_3d, segments) or ([], []) if not a curve.
    """
    speckle_type = (_get(obj, "speckle_type") or "").split(".")[-1]

    if speckle_type == "Polycurve":
        return _extract_polycurve(obj, scale)
    elif speckle_type == "Line":
        return _extract_single_line(obj, scale)
    elif speckle_type == "Arc":
        return _extract_single_arc(obj, scale)
    return [], []


def build_ifc_curve(ifc, points: list, segments: list):
    """
    Build an IfcIndexedPolyCurve from points and segment descriptors.

    points:   list of [x, y, z] coordinates
    segments: list of ("line", [indices]) or ("arc", [indices])

    Returns IfcIndexedPolyCurve or None.
    """
    if not points or not segments:
        return None

    point_list = ifc.createIfcCartesianPointList3D(points)

    ifc_segments = []
    for seg_type, indices in segments:
        if seg_type == "arc":
            ifc_segments.append(ifc.create_entity("IfcArcIndex", indices))
        else:
            ifc_segments.append(ifc.create_entity("IfcLineIndex", indices))

    if not ifc_segments:
        return None

    return ifc.createIfcIndexedPolyCurve(
        Points=point_list,
        Segments=ifc_segments,
        SelfIntersect=False,
    )


def get_display_curves(obj) -> list:
    """
    Collect curve objects from an object's displayValue, or the object itself.
    Returns a list of curve objects (Polycurve, Line, Arc, etc.).
    """
    curves = []
    for key in ["displayValue", "@displayValue", "_displayValue"]:
        display = _get(obj, key)
        if display is None:
            continue
        items = display if isinstance(display, list) else [display]
        for item in items:
            if item is not None and is_curve(item):
                curves.append(item)
        if curves:
            break

    # Fallback: the object itself is a curve
    if not curves and is_curve(obj):
        curves.append(obj)

    return curves


def curve_to_ifc(
    ifc: ifcopenshell.file,
    body_context,
    obj: Base,
    scale: float = 1.0,
    material_manager=None,
) -> tuple:
    """
    Convert a Speckle object with curve geometry -> (IfcShapeRepresentation, IfcLocalPlacement).
    Looks for curves in displayValue first, then checks the object itself.
    Creates one IfcIndexedPolyCurve per curve item.
    Returns (None, None) if no usable curve geometry.
    """
    curves = get_display_curves(obj)
    if not curves:
        return None, None

    obj_app_id = _get(obj, "applicationId")
    obj_scale = _resolve_scale(obj, scale)

    # Collect curve data and compute origin incrementally
    curve_cache = []
    xmin = ymin = zmin = float("inf")
    xmax = ymax = float("-inf")
    has_points = False

    for curve_obj in curves:
        points, segments = extract_curve_data(curve_obj, obj_scale)
        if points and segments:
            curve_cache.append((points, segments))
            has_points = True
            for p in points:
                x, y, z = p[0], p[1], p[2]
                if x < xmin: xmin = x
                if x > xmax: xmax = x
                if y < ymin: ymin = y
                if y > ymax: ymax = y
                if z < zmin: zmin = z
        else:
            curve_cache.append(None)

    if not has_points:
        return None, None

    ox = (xmin + xmax) / 2.0
    oy = (ymin + ymax) / 2.0
    oz = zmin

    # Build IFC curve entities
    geom_items = []
    for i, cached in enumerate(curve_cache):
        if cached is None:
            continue
        points, segments = cached

        offset_points = [
            [p[0] - ox, p[1] - oy, p[2] - oz] for p in points
        ]

        curve_entity = build_ifc_curve(ifc, offset_points, segments)
        if curve_entity is None:
            continue

        # Apply material
        if material_manager:
            curve_app_id = _get(curves[i], "applicationId") or obj_app_id
            if curve_app_id:
                material_manager.apply_to_item(curve_entity, str(curve_app_id))

        geom_items.append(curve_entity)

    if not geom_items:
        return None, None

    rep = ifc.createIfcShapeRepresentation(
        ContextOfItems=body_context,
        RepresentationIdentifier="Body",
        RepresentationType="Curve3D",
        Items=geom_items,
    )
    placement = _make_placement(ifc, ox, oy, oz)
    return rep, placement


def build_curve_rep_map(ifc, body_context, obj, scale: float = 1.0,
                        material_manager=None, fallback_app_ids: list = None,
                        definition_id: str = None):
    """
    Build an IfcRepresentationMap from a curve definition object.
    Used for instance-based curve geometry (shared across instances).
    Returns IfcRepresentationMap or None.
    """
    points, segments = extract_curve_data(obj, scale)
    if not points or not segments:
        return None

    curve_entity = build_ifc_curve(ifc, points, segments)
    if curve_entity is None:
        return None

    # Apply material (3-tier: object app_id -> fallbacks -> definition)
    if material_manager:
        app_id = _get(obj, "applicationId")
        style = material_manager.get_style_with_fallbacks(
            primary_app_id=str(app_id) if app_id else None,
            fallback_app_ids=fallback_app_ids,
            definition_id=definition_id,
        )
        if style:
            try:
                ifcopenshell.api.run(
                    "style.assign_item_style", ifc,
                    item=curve_entity, style=style,
                )
                material_manager._apply_count += 1
            except Exception:
                pass

    shared = _get_shared(ifc)
    a2p = ifc.createIfcAxis2Placement3D(shared["origin_0"], None, None)

    mapped_rep = ifc.createIfcShapeRepresentation(
        ContextOfItems=body_context,
        RepresentationIdentifier="Body",
        RepresentationType="Curve3D",
        Items=[curve_entity],
    )

    return ifc.createIfcRepresentationMap(a2p, mapped_rep)
