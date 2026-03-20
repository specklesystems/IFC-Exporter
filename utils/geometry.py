# =============================================================================
# geometry.py
# Converts Speckle DataObject geometry → IFC IfcPolygonalFaceSet + IfcLocalPlacement
#
# Key facts:
#   - After specklepy receive(), vertices and faces are FLAT Python lists
#   - displayValue is an array of Mesh objects
#   - Units are in mm (for Revit), scale to metres for IFC
#   - Vertices are in absolute world coordinates
#   - Uses IfcPolygonalFaceSet (indexed vertices) instead of IfcFacetedBrep
#     for compact output — each vertex stored once, not once per face.
# =============================================================================

import math

import ifcopenshell
from specklepy.objects.base import Base


# Scale factors → MILLIMETRES (IFC file is declared as mm)
_UNIT_SCALES = {
    "mm": 1.0,    "millimeter": 1.0,    "millimeters": 1.0,
    "cm": 10.0,   "centimeter": 10.0,   "centimeters": 10.0,
    "m":  1000.0, "meter": 1000.0,      "meters": 1000.0,
    "ft": 304.8,  "foot": 304.8,        "feet": 304.8,
    "in": 25.4,   "inch": 25.4,         "inches": 25.4,
}


# --------------------------------------------------------------------------- #
# Geometry validation helpers (GEM111 fix)
# --------------------------------------------------------------------------- #

# Minimum distance in mm below which two vertices are considered identical (GEM111).
_VERTEX_MERGE_TOL = 0.01  # 0.01 mm
_INV_TOL = 1.0 / _VERTEX_MERGE_TOL  # pre-computed: multiply instead of divide


def build_ifc_facesets(ifc, verts_scaled: list, face_groups: list) -> list:
    """
    Build a list of IfcPolygonalFaceSet from scaled (x,y,z) vertices and face index groups.

    Uses IfcCartesianPointList3D + IfcIndexedPolygonalFace for compact output.
    Vertices are deduplicated via snap grid so each unique position is stored once.

    GEM111 fix: skip faces with near-duplicate vertices (snapped to same grid cell).

    verts_scaled: flat list of already-scaled floats [x0,y0,z0, x1,y1,z1, ...]
    face_groups:  list of index lists [[i,j,k], [i,j,k,l], ...]
    Returns: list of IfcPolygonalFaceSet (typically one, empty on failure).
    """
    snap_to_idx = {}   # snap_key → 0-based index in deduped_verts
    deduped_verts = [] # [[x, y, z], ...] — lists for direct IFC use
    inv_tol = _INV_TOL

    # Validate faces and remap indices to deduplicated vertex list
    valid_faces = []  # list of (idx0+1, idx1+1, ...) tuples (1-based for IFC)
    vert_len = len(verts_scaled)
    for indices in face_groups:
        if indices is None:
            continue
        if not isinstance(indices, (list, tuple)):
            continue

        remapped = []
        seen_snaps = set()
        degenerate = False
        invalid = False

        for i in indices:
            if not isinstance(i, int):
                invalid = True
                break

            i3 = i * 3
            if i3 < 0 or i3 + 2 >= vert_len:
                invalid = True
                break

            x = verts_scaled[i3]
            y = verts_scaled[i3 + 1]
            z = verts_scaled[i3 + 2]
            key = (round(x * inv_tol), round(y * inv_tol), round(z * inv_tol))
            if key in seen_snaps:
                degenerate = True
                break
            seen_snaps.add(key)

            idx = snap_to_idx.get(key)
            if idx is None:
                idx = len(deduped_verts)
                snap_to_idx[key] = idx
                deduped_verts.append([x, y, z])
            remapped.append(idx + 1)  # 1-based for IFC

        if invalid or degenerate or len(remapped) < 3:
            continue
        valid_faces.append(remapped)

    if not valid_faces or not deduped_verts:
        return []

    # Build IFC entities
    try:
        point_list = ifc.createIfcCartesianPointList3D(deduped_verts)
        ifc_faces = [
            ifc.createIfcIndexedPolygonalFace(fi) for fi in valid_faces
        ]
        faceset = ifc.createIfcPolygonalFaceSet(point_list, None, ifc_faces, None)
        return [faceset]
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# Safe data access helpers
# --------------------------------------------------------------------------- #

def _get(obj, key, default=None):
    """
    Safe access for specklepy Base objects.
    Tries attribute access first, then bracket access.
    """
    try:
        val = getattr(obj, key, None)
        if val is not None:
            return val
    except Exception:
        pass
    try:
        val = obj[key]
        if val is not None:
            return val
    except Exception:
        pass
    return default


def unwrap_chunks(raw) -> list:
    """
    Flatten a Speckle data array into a plain Python list of numbers.

    Handles two cases:
      1. Already flat list of numbers (after specklepy receive deserializes)
         → returned as-is (fast path)
      2. List of DataChunk objects (raw from server before deserialization)
         → each chunk's .data list is concatenated
    """
    if not raw:
        return []

    # Fast path: if first item is a number, assume all items are numbers
    first = raw[0]
    if isinstance(first, (int, float)):
        return raw

    # Slow path: DataChunk objects or mixed content
    result = []
    for item in raw:
        if item is None:
            continue
        if isinstance(item, (int, float)):
            result.append(item)
            continue
        speckle_type = getattr(item, "speckle_type", "") or ""
        if "DataChunk" in speckle_type:
            chunk_data = _get(item, "data") or _get(item, "@data")
            if chunk_data:
                result.extend(list(chunk_data))
        else:
            try:
                result.extend(list(item))
            except Exception:
                pass
    return result


def _resolve_scale(obj, stream_scale: float) -> float:
    """Resolve unit scale: obj.units → stream fallback."""
    units = _get(obj, "units")
    if units and isinstance(units, str):
        return _UNIT_SCALES.get(units.lower().strip(), stream_scale)
    return stream_scale


# --------------------------------------------------------------------------- #
# Mesh extraction
# --------------------------------------------------------------------------- #

def _is_mesh(item) -> bool:
    """
    Detect if a specklepy object is a Mesh.
    Uses speckle_type string — more reliable than hasattr on Base objects.
    """
    if item is None:
        return False
    speckle_type = _get(item, "speckle_type") or ""
    if "Mesh" in speckle_type:
        return True
    # Fallback: has both vertices and faces data
    verts = _get(item, "vertices")
    faces = _get(item, "faces")
    return verts is not None and faces is not None


def get_display_meshes(obj: Base) -> list:
    """
    Extract all Mesh objects from a DataObject's displayValue.
    displayValue is always an array per the Speckle schema docs.
    """
    meshes = []

    for key in ["displayValue", "@displayValue"]:
        display = _get(obj, key)
        if display is None:
            continue
        items = display if isinstance(display, list) else [display]
        for item in items:
            if _is_mesh(item):
                meshes.append(item)
        if meshes:
            break  # found meshes, don't check @displayValue too

    # Fallback: object itself is a Mesh
    if not meshes and _is_mesh(obj):
        speckle_type = _get(obj, "speckle_type") or ""
        if "Mesh" in speckle_type:
            meshes.append(obj)

    return meshes


def get_display_instances(obj: Base) -> list:
    """
    Extract InstanceProxy objects from a DataObject's displayValue.

    Per the official speckleifc converter, every IFC element's displayValue
    contains InstanceProxy objects (not raw meshes). Each InstanceProxy has:
      - transform:    16-float row-major matrix, translation in metres
      - definitionId: "DEFINITION:{meshAppId}" string
      - units:        "m"

    Raw meshes do NOT appear in displayValue in IFC→Speckle exports.
    """
    instances = []
    for key in ["displayValue", "@displayValue"]:
        display = _get(obj, key)
        if display is None:
            continue
        items = display if isinstance(display, list) else [display]
        for item in items:
            if item is None:
                continue
            transform     = _get(item, "transform")
            definition_id = _get(item, "definitionId")
            if transform is not None and definition_id is not None:
                instances.append(item)
        if instances:
            break
    return instances


# --------------------------------------------------------------------------- #
# Curve detection & extraction (Lines, Arcs)
# --------------------------------------------------------------------------- #

def _is_line(item) -> bool:
    """Detect Objects.Geometry.Line (but not Polyline)."""
    if item is None:
        return False
    st = _get(item, "speckle_type") or ""
    return "Line" in st and "Polyline" not in st


def _is_arc(item) -> bool:
    """Detect Objects.Geometry.Arc."""
    if item is None:
        return False
    st = _get(item, "speckle_type") or ""
    return "Arc" in st


def get_display_curves(obj: Base) -> list:
    """Extract Line and Arc objects from a DataObject's displayValue."""
    curves = []
    for key in ["displayValue", "@displayValue"]:
        display = _get(obj, key)
        if display is None:
            continue
        items = display if isinstance(display, list) else [display]
        for item in items:
            if _is_line(item) or _is_arc(item):
                curves.append(item)
        if curves:
            break
    return curves


def _point_coords(pt, fallback_scale: float) -> tuple:
    """Extract (x, y, z) from a Speckle Point, scaled to mm."""
    scale = _resolve_scale(pt, fallback_scale)
    x = float(_get(pt, "x") or 0.0) * scale
    y = float(_get(pt, "y") or 0.0) * scale
    z = float(_get(pt, "z") or 0.0) * scale
    return x, y, z


def _arc_to_points(arc, scale: float, num_segments: int = 8) -> list:
    """
    Approximate a Speckle Arc as a list of (x, y, z) points in mm.
    Uses plane origin (center), radius, and domain angles for parametric sampling.
    Falls back to start/mid/end points if plane data is missing.
    """
    plane = _get(arc, "plane")
    radius = _get(arc, "radius")
    domain = _get(arc, "domain")

    if not plane or not radius or not domain:
        points = []
        for key in ["startPoint", "midPoint", "endPoint"]:
            pt = _get(arc, key)
            if pt:
                points.append(_point_coords(pt, scale))
        return points if len(points) >= 2 else []

    origin = _get(plane, "origin")
    xdir = _get(plane, "xdir")
    ydir = _get(plane, "ydir")

    if not origin or not xdir or not ydir:
        points = []
        for key in ["startPoint", "midPoint", "endPoint"]:
            pt = _get(arc, key)
            if pt:
                points.append(_point_coords(pt, scale))
        return points if len(points) >= 2 else []

    cx, cy, cz = _point_coords(origin, scale)
    # Direction vectors are unitless — do not scale
    dxx = float(_get(xdir, "x") or 0.0)
    dxy = float(_get(xdir, "y") or 0.0)
    dxz = float(_get(xdir, "z") or 0.0)
    dyx = float(_get(ydir, "x") or 0.0)
    dyy = float(_get(ydir, "y") or 0.0)
    dyz = float(_get(ydir, "z") or 0.0)

    r = float(radius) * scale
    t_start = float(_get(domain, "start") or 0.0)
    t_end = float(_get(domain, "end") or 0.0)

    points = []
    for i in range(num_segments + 1):
        t = t_start + (t_end - t_start) * i / num_segments
        cos_t = math.cos(t)
        sin_t = math.sin(t)
        x = cx + r * (cos_t * dxx + sin_t * dyx)
        y = cy + r * (cos_t * dxy + sin_t * dyy)
        z = cz + r * (cos_t * dxz + sin_t * dyz)
        points.append((x, y, z))
    return points


def curves_to_ifc(
    ifc: ifcopenshell.file,
    body_context,
    obj: Base,
    scale: float = 0.001,
    material_manager=None,
) -> tuple:
    """
    Convert Speckle Line/Arc objects in displayValue to IFC curve geometry.
    Lines → IfcPolyline (2 points), Arcs → IfcPolyline (sampled points).
    Wrapped in IfcGeometricCurveSet.
    Returns (IfcShapeRepresentation, IfcLocalPlacement) or (None, None).
    """
    curves = get_display_curves(obj)
    if not curves:
        return None, None

    obj_scale = _resolve_scale(obj, scale)
    polylines = []
    all_points = []

    for curve in curves:
        cs = _resolve_scale(curve, obj_scale)

        if _is_line(curve):
            start = _get(curve, "start")
            end = _get(curve, "end")
            if not start or not end:
                continue
            p1 = _point_coords(start, cs)
            p2 = _point_coords(end, cs)
            all_points.extend([p1, p2])
            polylines.append([p1, p2])

        elif _is_arc(curve):
            pts = _arc_to_points(curve, cs)
            if len(pts) >= 2:
                all_points.extend(pts)
                polylines.append(pts)

    if not polylines or not all_points:
        return None, None

    # Compute origin from all curve points
    xs = [p[0] for p in all_points]
    ys = [p[1] for p in all_points]
    zs = [p[2] for p in all_points]
    ox = (min(xs) + max(xs)) / 2.0
    oy = (min(ys) + max(ys)) / 2.0
    oz = min(zs)

    # Build IfcPolylines offset from origin
    ifc_polylines = []
    for pts in polylines:
        ifc_points = [
            ifc.createIfcCartesianPoint([p[0] - ox, p[1] - oy, p[2] - oz])
            for p in pts
        ]
        ifc_polylines.append(ifc.createIfcPolyline(ifc_points))

    if not ifc_polylines:
        return None, None

    curve_set = ifc.createIfcGeometricCurveSet(ifc_polylines)

    rep = ifc.createIfcShapeRepresentation(
        ContextOfItems=body_context,
        RepresentationIdentifier="Body",
        RepresentationType="GeometricCurveSet",
        Items=[curve_set],
    )
    placement = _make_placement(ifc, ox, oy, oz)
    return rep, placement


# --------------------------------------------------------------------------- #
# Face decoding
# --------------------------------------------------------------------------- #

def decode_faces(faces_raw: list) -> list:
    """
    Decode Speckle's run-length encoded face list into vertex index groups.
    Format: [n, i0, i1, ..., n, i0, i1, ...]
      n=0 → triangle (legacy), n=1 → quad (legacy), n≥3 → n-gon
    """
    decoded = []
    i = 0
    total = len(faces_raw)
    # Check if values are already ints (common after unwrap_chunks)
    already_int = total > 0 and isinstance(faces_raw[0], int)
    while i < total:
        n = faces_raw[i] if already_int else int(faces_raw[i])
        if n == 0:
            n = 3
        elif n == 1:
            n = 4
        end = i + 1 + n
        if end > total:
            break
        if already_int:
            decoded.append(faces_raw[i + 1:end])
        else:
            decoded.append([int(v) for v in faces_raw[i + 1:end]])
        i = end
    return decoded


# --------------------------------------------------------------------------- #
# Bounding box + placement
# --------------------------------------------------------------------------- #

def compute_origin(flat_verts: list) -> tuple:
    """
    Compute placement origin from scaled vertex list (mm).
    X, Y = bounding box centroid
    Z = minimum Z (bottom face of element — more natural for IFC)
    Single-pass to avoid creating 3 sliced copies of a large list.
    """
    x0 = flat_verts[0]
    y0 = flat_verts[1]
    z0 = flat_verts[2]
    xmin = xmax = x0
    ymin = ymax = y0
    zmin = z0
    for i in range(3, len(flat_verts) - 2, 3):
        x = flat_verts[i]
        y = flat_verts[i + 1]
        z = flat_verts[i + 2]
        if x < xmin:
            xmin = x
        elif x > xmax:
            xmax = x
        if y < ymin:
            ymin = y
        elif y > ymax:
            ymax = y
        if z < zmin:
            zmin = z
    return (xmin + xmax) / 2.0, (ymin + ymax) / 2.0, zmin


# Cache for shared IFC direction/point entities (keyed by ifc file id)
_shared_entities: dict[int, dict] = {}


def _get_shared(ifc):
    """Return (or create) shared IfcDirection and IfcCartesianPoint entities for this file."""
    fid = id(ifc)
    if fid not in _shared_entities:
        _shared_entities[fid] = {
            "z_axis": ifc.createIfcDirection([0.0, 0.0, 1.0]),
            "x_axis": ifc.createIfcDirection([1.0, 0.0, 0.0]),
            "origin_0": ifc.createIfcCartesianPoint([0.0, 0.0, 0.0]),
        }
    return _shared_entities[fid]


def _make_placement(ifc, x: float, y: float, z: float):
    """Create an IfcLocalPlacement at absolute world coordinates (metres)."""
    shared = _get_shared(ifc)
    origin = ifc.createIfcCartesianPoint([x, y, z])
    a2p    = ifc.createIfcAxis2Placement3D(origin, shared["z_axis"], shared["x_axis"])
    return ifc.createIfcLocalPlacement(PlacementRelTo=None, RelativePlacement=a2p)


# --------------------------------------------------------------------------- #
# Main conversion
# --------------------------------------------------------------------------- #

def mesh_to_ifc(
    ifc: ifcopenshell.file,
    body_context,
    obj: Base,
    scale: float = 0.001,
    material_manager=None,
) -> tuple:
    """
    Convert a Speckle DataObject → (IfcShapeRepresentation, IfcLocalPlacement).
    Creates one IfcPolygonalFaceSet per mesh so each can carry its own material style.
    Returns (None, None) if no usable geometry is found.
    """
    meshes = get_display_meshes(obj)
    if not meshes:
        return None, None

    obj_scale = _resolve_scale(obj, scale)

    # ------------------------------------------------------------------ #
    # Pass 1: unpack vertices once per mesh, collect all scaled coords
    #         to compute world origin. Cache (verts, ms) for Pass 2.
    # ------------------------------------------------------------------ #
    mesh_cache = []   # [(verts_list, ms, scaled)] or None per mesh
    all_scaled = []
    for mesh in meshes:
        raw_verts = _get(mesh, "vertices") or []
        verts = unwrap_chunks(raw_verts if isinstance(raw_verts, list) else list(raw_verts))
        if not verts:
            mesh_cache.append(None)
            continue
        ms = _resolve_scale(mesh, obj_scale)
        # Pre-scale vertices once, reuse in Pass 2
        scaled = [float(v) * ms for v in verts]
        mesh_cache.append((verts, ms, scaled))
        all_scaled.extend(scaled)

    if not all_scaled:
        return None, None

    ox, oy, oz = compute_origin(all_scaled)

    # ------------------------------------------------------------------ #
    # Pass 2: one faceset per mesh — reuse cached verts, only unpack faces
    # ------------------------------------------------------------------ #
    geom_items = []

    for mesh, cached in zip(meshes, mesh_cache):
        if cached is None:
            continue
        verts, ms, scaled = cached
        raw_faces = _get(mesh, "faces") or []
        faces_raw = unwrap_chunks(raw_faces if isinstance(raw_faces, list) else list(raw_faces))

        if not faces_raw:
            continue

        try:
            face_groups = decode_faces(faces_raw)
        except Exception as e:
            print(f"  ⚠️  Face decode error: {e}")
            continue

        # Offset pre-scaled vertices relative to origin (flat list, no tuples)
        n = len(scaled)
        verts_scaled = [0.0] * n
        for vi in range(0, n, 3):
            verts_scaled[vi]     = scaled[vi]     - ox
            verts_scaled[vi + 1] = scaled[vi + 1] - oy
            verts_scaled[vi + 2] = scaled[vi + 2] - oz

        mesh_facesets = build_ifc_facesets(ifc, verts_scaled, face_groups)

        if not mesh_facesets:
            continue

        # Apply material style to every faceset of this mesh
        if material_manager:
            mesh_app_id = _get(mesh, "applicationId")
            if mesh_app_id:
                for fs in mesh_facesets:
                    material_manager.apply_to_item(fs, str(mesh_app_id))

        geom_items.extend(mesh_facesets)

    if not geom_items:
        return None, None

    # ------------------------------------------------------------------ #
    # Assemble IfcShapeRepresentation + IfcLocalPlacement
    # ------------------------------------------------------------------ #
    rep = ifc.createIfcShapeRepresentation(
        ContextOfItems=body_context,
        RepresentationIdentifier="Body",
        RepresentationType="Tessellation",
        Items=geom_items,
    )
    placement = _make_placement(ifc, ox, oy, oz)

    return rep, placement