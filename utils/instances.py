# =============================================================================
# instances.py
# Handles Speckle InstanceProxy objects from both:
#
# FORMAT A — Revit connector (our actual use case):
#   _units      = "mm"
#   transform   = 16 floats, row-major, translation in MM
#   definitionId = 64-char uppercase hex hash (matches object id[:32] in tree)
#   The definition object lives somewhere in the object tree.
#
# FORMAT B — speckleifc IFC→Speckle converter:
#   units       = "m"
#   transform   = 16 floats, row-major, translation in METRES
#   definitionId = "DEFINITION:{meshAppId}"
#   Definition geometry lives in root → Collection("definitionGeometry")
#
# We detect the format by the definitionId prefix.
#
# Performance: uses IfcRepresentationMap + IfcMappedItem so that all instances
# sharing the same definition reference a single copy of the geometry.
# =============================================================================

import math
from specklepy.objects.base import Base
from utils.geometry import _get, unwrap_chunks, decode_faces, _UNIT_SCALES, build_ifc_facesets, _get_shared


def is_instance(obj) -> bool:
    """Returns True if this object is a Speckle InstanceProxy."""
    return _get(obj, "transform") is not None and _get(obj, "definitionId") is not None


def _is_ifc_format(definition_id: str) -> bool:
    """True if this is speckleifc format (definitionId starts with 'DEFINITION:')."""
    return definition_id.startswith("DEFINITION:")


def build_definition_map(root: Base) -> dict:
    """
    Build a unified definition map that handles both formats.

    Returns dict with keys:
      "by_id"       : {obj_id_lower[:32] → object}   for Revit format
      "by_app_id"   : {applicationId_lower → object}  for Revit format
      "ifc_proxies" : {"DEFINITION:xxx" → proxy}      for IFC format
      "ifc_meshes"  : {meshAppId → Mesh}               for IFC format
    """
    by_id      = {}
    by_app_id  = {}
    ifc_proxies = {}
    ifc_meshes  = {}

    # --- Walk entire tree for Revit format ---
    _collect_all(root, by_id, by_app_id, depth=0)

    # --- Extract speckleifc structures for IFC format ---
    proxies_raw = _get(root, "instanceDefinitionProxies")
    if proxies_raw:
        for proxy in (proxies_raw if isinstance(proxies_raw, list) else [proxies_raw]):
            app_id = _get(proxy, "applicationId")
            if app_id:
                ifc_proxies[app_id] = proxy            # original case (for IFC format)
                ifc_proxies[app_id.lower()] = proxy    # lowercase (for Revit format)

    elements = _get(root, "elements") or _get(root, "@elements") or []
    for child in (elements if isinstance(elements, list) else []):
        if (_get(child, "name") or "") == "definitionGeometry":
            geom_elements = _get(child, "elements") or _get(child, "@elements") or []
            for mesh in (geom_elements if isinstance(geom_elements, list) else []):
                mesh_app_id = _get(mesh, "applicationId")
                if mesh_app_id:
                    ifc_meshes[mesh_app_id] = mesh

    print(f"   Objects indexed by id:     {len(by_id)}")
    print(f"   Objects indexed by appId:  {len(by_app_id)}")
    print(f"   IFC definition proxies:    {len(ifc_proxies)}")
    print(f"   IFC definition meshes:     {len(ifc_meshes)}")

    return {
        "by_id":       by_id,
        "by_app_id":   by_app_id,
        "ifc_proxies": ifc_proxies,
        "ifc_meshes":  ifc_meshes,
    }


def _collect_all(obj, by_id: dict, by_app_id: dict, depth: int):
    if obj is None or depth > 25:
        return

    obj_id = _get(obj, "id")
    if obj_id and isinstance(obj_id, str):
        key = obj_id.lower()
        by_id[key] = obj
        # Also store truncated — definitionId (64 chars) matches id (32 chars)
        if len(key) == 32:
            by_id[key] = obj
        elif len(key) > 32:
            by_id[key[:32]] = obj

    app_id = _get(obj, "applicationId")
    if app_id and isinstance(app_id, str):
        by_app_id[app_id.lower()] = obj

    for key in ["elements", "@elements", "displayValue", "@displayValue",
                "objects", "@objects", "definition", "@definition"]:
        try:
            children = obj[key]
            if children is None:
                continue
            if not isinstance(children, list):
                children = [children]
            for child in children:
                _collect_all(child, by_id, by_app_id, depth + 1)
        except Exception:
            continue


def _get_revit_meshes(definition_id: str, definition_map: dict) -> list:
    """
    Revit format:
      definitionId (64-char hex) → InstanceDefinitionProxy.applicationId
      proxy.objects[0] is a UUID applicationId → find mesh by applicationId
    """
    from utils.geometry import get_display_meshes

    # Step 1: find the InstanceDefinitionProxy by its applicationId (case-insensitive)
    ifc_proxies = definition_map.get("ifc_proxies", {})
    proxy = ifc_proxies.get(definition_id) or ifc_proxies.get(definition_id.lower())
    if proxy is None:
        return []

    # Step 2: get the mesh applicationIds from proxy.objects
    object_ids = _get(proxy, "objects") or []
    if not isinstance(object_ids, list):
        object_ids = list(object_ids)

    # Step 3: look up each mesh by applicationId
    by_app_id = definition_map.get("by_app_id", {})
    meshes = []
    for oid in object_ids:
        obj = by_app_id.get(str(oid).lower())
        if obj is not None:
            # The found object may itself be a mesh, or contain displayValue meshes
            found_meshes = get_display_meshes(obj)
            if found_meshes:
                meshes.extend(found_meshes)
            else:
                # It IS the mesh directly
                meshes.append(obj)
    return meshes


def _get_ifc_meshes(definition_id: str, definition_map: dict) -> list:
    """
    IFC format: definitionId = "DEFINITION:224058_mat0"
    Look up proxy → objects list → meshes from ifc_meshes dict.
    """
    ifc_proxies = definition_map.get("ifc_proxies", {})
    ifc_meshes  = definition_map.get("ifc_meshes", {})

    proxy = ifc_proxies.get(definition_id)
    if proxy is None:
        return []

    object_ids = _get(proxy, "objects") or []
    result = []
    for oid in (object_ids if isinstance(object_ids, list) else [object_ids]):
        mesh = ifc_meshes.get(str(oid))
        if mesh is not None:
            result.append(mesh)
    return result


def _resolve_instance_scale(obj, stream_scale: float) -> float:
    """
    Resolve scale for the transform translation.
    Tries bracket access for '_units' (Revit uses underscore).
    IFC format instances have units="m" → scale=1.0 (no scaling).
    """
    for key in ["units", "_units"]:
        try:
            units = obj[key]
            if units and isinstance(units, str):
                s = _UNIT_SCALES.get(units.lower().strip())
                if s is not None:
                    return s
        except Exception:
            pass
    return stream_scale


# Stats
_stats = {"found": 0, "not_found": 0}

# Cache: mesh id → (verts_scaled, face_groups) to avoid re-unpacking
# AND re-scaling the same definition mesh across many instances that share it.
_mesh_data_cache: dict = {}

# Cache: definition_id → IfcRepresentationMap (or None if no geometry)
# All instances sharing the same definition reuse one geometry copy.
_rep_map_cache: dict = {}

# Shared identity placement for all instances (keyed by ifc file id)
_identity_placement_cache: dict[int, object] = {}


_MM_SCALES = {
    "mm": 1.0, "millimeter": 1.0, "millimeters": 1.0,
    "cm": 10.0, "centimeter": 10.0,
    "m": 1000.0, "meter": 1000.0, "meters": 1000.0,
    "ft": 304.8, "in": 25.4,
}


# --------------------------------------------------------------------------- #
# IfcRepresentationMap builder — geometry created once per definition
# --------------------------------------------------------------------------- #

def _build_rep_map(ifc, body_context, meshes: list, ifc_format: bool,
                   material_manager=None):
    """
    Build an IfcRepresentationMap from definition meshes.
    Geometry is in local coordinates (mm, no instance transform applied).
    Returns IfcRepresentationMap or None if no valid geometry.
    """
    geom_items = []

    for mesh in meshes:
        mesh_id = _get(mesh, "id") or _get(mesh, "applicationId")
        if mesh_id and mesh_id in _mesh_data_cache:
            verts_local, face_groups = _mesh_data_cache[mesh_id]
        else:
            raw_verts = _get(mesh, "vertices") or []
            raw_faces = _get(mesh, "faces") or []
            verts     = unwrap_chunks(list(raw_verts))
            faces_raw = unwrap_chunks(list(raw_faces))
            if not verts or not faces_raw:
                continue

            mesh_units = _get(mesh, "units") or _get(mesh, "_units") or ("m" if ifc_format else "mm")
            ms = _MM_SCALES.get(mesh_units.lower().strip(), 1.0)

            try:
                face_groups = decode_faces(faces_raw)
            except Exception as e:
                print(f"  ⚠️  Instance face decode: {e}")
                continue

            # Scale vertices once and cache the result
            verts_local = [float(v) * ms for v in verts]

            if mesh_id:
                _mesh_data_cache[mesh_id] = (verts_local, face_groups)

        mesh_facesets = build_ifc_facesets(ifc, verts_local, face_groups)

        if not mesh_facesets:
            continue

        # Apply material style to each faceset
        if material_manager:
            mesh_app_id = _get(mesh, "applicationId")
            if mesh_app_id:
                for fs in mesh_facesets:
                    material_manager.apply_to_item(fs, str(mesh_app_id))

        geom_items.extend(mesh_facesets)

    if not geom_items:
        return None

    # Mapping origin = identity (local coords origin) — reuse shared origin
    shared = _get_shared(ifc)
    a2p    = ifc.createIfcAxis2Placement3D(shared["origin_0"], None, None)

    # The mapped representation holds the actual geometry
    mapped_rep = ifc.createIfcShapeRepresentation(
        ContextOfItems=body_context,
        RepresentationIdentifier="Body",
        RepresentationType="Tessellation",
        Items=geom_items,
    )

    return ifc.createIfcRepresentationMap(a2p, mapped_rep)


# --------------------------------------------------------------------------- #
# Transform → IfcCartesianTransformationOperator3D
# --------------------------------------------------------------------------- #

def _vec_magnitude(x, y, z):
    return math.sqrt(x*x + y*y + z*z)


def _make_transform_operator(ifc, t: list, ts: float):
    """
    Convert a row-major 4x4 matrix + translation scale into an
    IfcCartesianTransformationOperator3DnonUniform.

    t:  16 floats, row-major [r00,r01,r02,tx, r10,r11,r12,ty, r20,r21,r22,tz, 0,0,0,1]
    ts: scale factor for translation components (e.g. 1000.0 for m→mm)

    The matrix acts as: p' = M * p + translation, where M rows are:
      row0 = (t[0], t[1], t[2])
      row1 = (t[4], t[5], t[6])
      row2 = (t[8], t[9], t[10])

    IfcCartesianTransformationOperator axes represent the COLUMNS of M:
      Axis1 = column 0 = where local X maps → (t[0], t[4], t[8])
      Axis2 = column 1 = where local Y maps → (t[1], t[5], t[9])
      Axis3 = column 2 = where local Z maps → (t[2], t[6], t[10])

    Returns the IFC entity, or None if the transform is degenerate.
    """
    # Extract COLUMNS of the 3x3 rotation/scale sub-matrix
    ax1 = (float(t[0]), float(t[4]), float(t[8]))    # column 0: X-axis direction
    ax2 = (float(t[1]), float(t[5]), float(t[9]))    # column 1: Y-axis direction
    ax3 = (float(t[2]), float(t[6]), float(t[10]))   # column 2: Z-axis direction

    s1 = _vec_magnitude(*ax1)
    s2 = _vec_magnitude(*ax2)
    s3 = _vec_magnitude(*ax3)

    if s1 < 1e-10 or s2 < 1e-10 or s3 < 1e-10:
        return None  # degenerate transform

    # Normalized direction vectors
    d1 = ifc.createIfcDirection([ax1[0]/s1, ax1[1]/s1, ax1[2]/s1])
    d2 = ifc.createIfcDirection([ax2[0]/s2, ax2[1]/s2, ax2[2]/s2])
    d3 = ifc.createIfcDirection([ax3[0]/s3, ax3[1]/s3, ax3[2]/s3])

    # Translation, scaled to mm
    tx = float(t[3])  * ts
    ty = float(t[7])  * ts
    tz = float(t[11]) * ts
    origin = ifc.createIfcCartesianPoint([tx, ty, tz])

    # Use non-uniform variant to handle mirrors and non-uniform scale
    return ifc.createIfcCartesianTransformationOperator3DnonUniform(
        d1,      # Axis1
        d2,      # Axis2
        origin,  # LocalOrigin
        s1,      # Scale
        d3,      # Axis3
        s2,      # Scale2
        s3,      # Scale3
    )


# --------------------------------------------------------------------------- #
# Main conversion — IfcMappedItem approach
# --------------------------------------------------------------------------- #

def instance_to_ifc(ifc, body_context, obj: Base, definition_map: dict,
                    scale: float = 1.0, material_manager=None):
    """
    Convert a Speckle InstanceProxy → (IfcShapeRepresentation, IfcLocalPlacement).

    Strategy: create geometry once per definition as an IfcRepresentationMap,
    then reference it via IfcMappedItem + IfcCartesianTransformationOperator3D
    for each instance. This avoids duplicating geometry across instances.
    """
    transform_raw = _get(obj, "transform")
    if not transform_raw:
        return None, None
    t = list(transform_raw)
    if len(t) != 16:
        return None, None

    definition_id = _get(obj, "definitionId") or ""
    ifc_format    = _is_ifc_format(definition_id)

    # Translation scale: IFC format transform is in metres → convert to mm
    # Revit format transform is already in mm (same as IFC file units)
    ts = 1000.0 if ifc_format else _resolve_instance_scale(obj, scale)

    # Identity placement (transform is encoded in the MappedItem) — shared across all instances
    fid = id(ifc)
    if fid not in _identity_placement_cache:
        shared = _get_shared(ifc)
        a2p = ifc.createIfcAxis2Placement3D(shared["origin_0"], None, None)
        _identity_placement_cache[fid] = ifc.createIfcLocalPlacement(PlacementRelTo=None, RelativePlacement=a2p)
    placement = _identity_placement_cache[fid]

    # --- Get or build IfcRepresentationMap (cached per definition_id) ---
    if definition_id not in _rep_map_cache:
        if ifc_format:
            meshes = _get_ifc_meshes(definition_id, definition_map)
        else:
            meshes = _get_revit_meshes(definition_id, definition_map)

        if not meshes:
            _stats["not_found"] += 1
            _rep_map_cache[definition_id] = None
            return None, placement

        _stats["found"] += 1
        _rep_map_cache[definition_id] = _build_rep_map(
            ifc, body_context, meshes, ifc_format, material_manager
        )
    else:
        # Track stats even for cached definitions
        if _rep_map_cache[definition_id] is not None:
            _stats["found"] += 1
        else:
            _stats["not_found"] += 1

    rep_map = _rep_map_cache[definition_id]
    if rep_map is None:
        return None, placement

    # --- Build transform operator from instance's 4x4 matrix ---
    transform_op = _make_transform_operator(ifc, t, ts)
    if transform_op is None:
        return None, placement

    # --- Create IfcMappedItem referencing the shared geometry ---
    mapped_item = ifc.createIfcMappedItem(rep_map, transform_op)

    rep = ifc.createIfcShapeRepresentation(
        ContextOfItems=body_context,
        RepresentationIdentifier="Body",
        RepresentationType="MappedRepresentation",
        Items=[mapped_item],
    )
    return rep, placement


def get_definition_object(obj: Base, definition_map: dict):
    """
    Resolve the definition's source object for an InstanceProxy.
    Returns the first object referenced by the definition proxy, which
    carries the proper category/type info. Returns None if not found.
    """
    definition_id = _get(obj, "definitionId") or ""
    if not definition_id:
        return None

    ifc_proxies = definition_map.get("ifc_proxies", {})
    proxy = ifc_proxies.get(definition_id) or ifc_proxies.get(definition_id.lower())
    if proxy is None:
        return None

    object_ids = _get(proxy, "objects") or []
    if not isinstance(object_ids, list):
        object_ids = list(object_ids)
    if not object_ids:
        return None

    by_app_id = definition_map.get("by_app_id", {})
    source = by_app_id.get(str(object_ids[0]).lower())
    return source


def print_instance_stats():
    total = _stats["found"] + _stats["not_found"]
    print(f"  Instance resolution: {_stats['found']}/{total} definitions found")
    if _stats["not_found"] > 0:
        print(f"  ⚠️  {_stats['not_found']} instances had no definition geometry")


def reset_caches():
    """Reset module-level caches (call at start of each export run)."""
    _mesh_data_cache.clear()
    _rep_map_cache.clear()
    _identity_placement_cache.clear()
    _stats["found"] = 0
    _stats["not_found"] = 0
