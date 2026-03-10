# =============================================================================
# geometry.py
# Converts Speckle DataObject geometry → IFC IfcFacetedBrep + IfcLocalPlacement
#
# Key facts:
#   - After specklepy receive(), vertices and faces are FLAT Python lists
#   - displayValue is an array of Mesh objects
#   - Units are in mm (for Revit), scale to metres for IFC
#   - Vertices are in absolute world coordinates
# =============================================================================

import ifcopenshell
from collections import defaultdict
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
# Geometry validation helpers (GEM111 + BRP002 fixes)
# --------------------------------------------------------------------------- #

# Minimum distance in mm below which two vertices are considered identical (GEM111).
_VERTEX_MERGE_TOL = 0.01  # 0.01 mm


def snap_coord(v: float) -> int:
    """Snap a coordinate to integer grid at _VERTEX_MERGE_TOL resolution."""
    return round(v / _VERTEX_MERGE_TOL)


def _find_connected_components(snapped_faces: list) -> list:
    """
    Union-Find: group face indices into connected components.
    Two faces are connected if they share an edge (pair of snapped vertex keys).
    Returns list of components, each a list of face indices.

    BRP002 requires all faces in an IfcClosedShell to form ONE component.
    If multiple components exist, each must become a separate IfcClosedShell.
    """
    n = len(snapped_faces)
    if n == 0:
        return []

    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    # Map each edge to the first face that used it, then union subsequent faces
    edge_to_face = {}
    for fi, keys in enumerate(snapped_faces):
        for i in range(len(keys)):
            edge = frozenset([keys[i], keys[(i + 1) % len(keys)]])
            if edge in edge_to_face:
                union(fi, edge_to_face[edge])
            else:
                edge_to_face[edge] = fi

    groups: dict = defaultdict(list)
    for fi in range(n):
        groups[find(fi)].append(fi)
    return list(groups.values())


def build_ifc_breps(ifc, verts_scaled: list, face_groups: list) -> list:
    """
    Build a list of IfcFacetedBrep from scaled (x,y,z) vertices and face index groups.

    GEM111 fix: skip faces with near-duplicate vertices (snapped to same grid cell).
    BRP002 fix: split faces into connected components; each component → its own
                IfcClosedShell → IfcFacetedBrep so every shell is arc-wise connected.

    verts_scaled: flat list of already-scaled floats [x0,y0,z0, x1,y1,z1, ...]
    face_groups:  list of index lists [[i,j,k], [i,j,k,l], ...]
    Returns: list of IfcFacetedBrep (one per connected component, never empty).
    """
    # Pass 1: validate faces and build snapped key lists for connectivity analysis
    valid_faces = []    # list of (pts_raw, snapped_keys)
    for indices in face_groups:
        try:
            pts_raw = []
            snapped = []
            degenerate = False
            seen = set()

            for i in indices:
                x = float(verts_scaled[i * 3])
                y = float(verts_scaled[i * 3 + 1])
                z = float(verts_scaled[i * 3 + 2])
                key = (snap_coord(x), snap_coord(y), snap_coord(z))
                if key in seen:
                    degenerate = True
                    break
                seen.add(key)
                pts_raw.append((x, y, z))
                snapped.append(key)

            if degenerate or len(pts_raw) < 3:
                continue

            valid_faces.append((pts_raw, snapped))
        except Exception:
            continue

    if not valid_faces:
        return []

    # Pass 2: split into connected components (BRP002)
    snapped_only = [f[1] for f in valid_faces]
    components = _find_connected_components(snapped_only)

    # Pass 3: build one IfcFacetedBrep per component
    breps = []
    for component_indices in components:
        ifc_faces = []
        for fi in component_indices:
            pts_raw, _ = valid_faces[fi]
            try:
                pts = [ifc.createIfcCartesianPoint([x, y, z]) for x, y, z in pts_raw]
                poly  = ifc.createIfcPolyLoop(pts)
                bound = ifc.createIfcFaceOuterBound(poly, True)
                ifc_faces.append(ifc.createIfcFace([bound]))
            except Exception:
                continue

        if not ifc_faces:
            continue

        shell = ifc.createIfcClosedShell(ifc_faces)
        breps.append(ifc.createIfcFacetedBrep(shell))

    return breps


# Keep old name as alias so instances.py import works unchanged
def build_ifc_faces(ifc, verts_scaled: list, face_groups: list) -> list:
    """Legacy wrapper — returns flat list of IfcFace (no connectivity splitting)."""
    # Used only as a fallback; callers should prefer build_ifc_breps directly.
    breps = build_ifc_breps(ifc, verts_scaled, face_groups)
    # Return the faces from all shells combined (for callers that need face lists)
    faces = []
    for brep in breps:
        faces.extend(brep.Outer.CfsFaces)
    return faces


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
         → [3, 0, 1, 2, 3, ...] returned as-is
      2. List of DataChunk objects (raw from server before deserialization)
         → each chunk's .data list is concatenated

    Both cases are handled so this function is always safe to call.
    """
    if not raw:
        return []

    result = []
    for item in raw:
        if item is None:
            continue
        # Plain number — already flat
        if isinstance(item, (int, float)):
            result.append(item)
            continue
        # DataChunk — unwrap .data
        speckle_type = getattr(item, "speckle_type", "") or ""
        if "DataChunk" in speckle_type:
            chunk_data = _get(item, "data") or _get(item, "@data")
            if chunk_data:
                result.extend(list(chunk_data))
        else:
            # Unknown — try iterating (handles nested lists)
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
    while i < len(faces_raw):
        n = int(faces_raw[i])
        if n == 0:
            n = 3
        elif n == 1:
            n = 4
        end = i + 1 + n
        if end > len(faces_raw):
            break
        indices = [int(faces_raw[i + 1 + j]) for j in range(n)]
        decoded.append(indices)
        i = end
    return decoded


# --------------------------------------------------------------------------- #
# Bounding box + placement
# --------------------------------------------------------------------------- #

def compute_origin(flat_verts: list) -> tuple:
    """
    Compute placement origin from scaled vertex list (metres).
    X, Y = bounding box centroid
    Z = minimum Z (bottom face of element — more natural for IFC)
    """
    xs = flat_verts[0::3]
    ys = flat_verts[1::3]
    zs = flat_verts[2::3]
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    cz = min(zs)
    return cx, cy, cz


def _make_placement(ifc, x: float, y: float, z: float):
    """Create an IfcLocalPlacement at absolute world coordinates (metres)."""
    origin = ifc.createIfcCartesianPoint([x, y, z])
    z_axis = ifc.createIfcDirection([0.0, 0.0, 1.0])
    x_axis = ifc.createIfcDirection([1.0, 0.0, 0.0])
    a2p    = ifc.createIfcAxis2Placement3D(origin, z_axis, x_axis)
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
    Creates one IfcFacetedBrep per mesh so each can carry its own material style.
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
    mesh_cache = []   # [(verts_list, ms)] or None per mesh
    all_scaled = []
    for mesh in meshes:
        raw_verts = _get(mesh, "vertices") or []
        verts = unwrap_chunks(list(raw_verts))
        if not verts:
            mesh_cache.append(None)
            continue
        ms = _resolve_scale(mesh, obj_scale)
        mesh_cache.append((verts, ms))
        for i in range(0, len(verts) - 2, 3):
            all_scaled.extend([
                float(verts[i])   * ms,
                float(verts[i+1]) * ms,
                float(verts[i+2]) * ms,
            ])

    if not all_scaled:
        return None, None

    ox, oy, oz = compute_origin(all_scaled)

    # ------------------------------------------------------------------ #
    # Pass 2: one brep per mesh — reuse cached verts, only unpack faces
    # ------------------------------------------------------------------ #
    brep_items = []

    for mesh, cached in zip(meshes, mesh_cache):
        if cached is None:
            continue
        verts, ms = cached
        raw_faces = _get(mesh, "faces") or []
        faces_raw = unwrap_chunks(list(raw_faces))

        if not faces_raw:
            continue

        try:
            face_groups = decode_faces(faces_raw)
        except Exception as e:
            print(f"  ⚠️  Face decode error: {e}")
            continue

        # Build pre-scaled vertex list (relative to origin) for this mesh
        verts_scaled = []
        for vi in range(0, len(verts) - 2, 3):
            verts_scaled.append(float(verts[vi])   * ms - ox)
            verts_scaled.append(float(verts[vi+1]) * ms - oy)
            verts_scaled.append(float(verts[vi+2]) * ms - oz)

        mesh_breps = build_ifc_breps(ifc, verts_scaled, face_groups)

        if not mesh_breps:
            continue

        # Apply material style to every component brep of this mesh
        if material_manager:
            mesh_app_id = _get(mesh, "applicationId")
            if mesh_app_id:
                for brep in mesh_breps:
                    material_manager.apply_to_item(brep, str(mesh_app_id))

        brep_items.extend(mesh_breps)

    if not brep_items:
        return None, None

    # ------------------------------------------------------------------ #
    # Assemble IfcShapeRepresentation + IfcLocalPlacement
    # ------------------------------------------------------------------ #
    rep = ifc.createIfcShapeRepresentation(
        ContextOfItems=body_context,
        RepresentationIdentifier="Body",
        RepresentationType="Brep",
        Items=brep_items,
    )
    placement = _make_placement(ifc, ox, oy, oz)

    return rep, placement