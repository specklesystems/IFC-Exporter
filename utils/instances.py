# =============================================================================
# instances.py
# Handles Speckle InstanceProxy objects from both:
#
# FORMAT A -- Revit connector (our actual use case):
#   _units      = "mm"
#   transform   = 16 floats, row-major, translation in MM
#   definitionId = 64-char uppercase hex hash (matches object id[:32] in tree)
#   The definition object lives somewhere in the object tree.
#
# FORMAT B -- speckleifc IFC->Speckle converter:
#   units       = "m"
#   transform   = 16 floats, row-major, translation in METRES
#   definitionId = "DEFINITION:{meshAppId}"
#   Definition geometry lives in root -> Collection("definitionGeometry")
#
# We detect the format by the definitionId prefix.
#
# Performance: uses IfcRepresentationMap + IfcMappedItem so that all instances
# sharing the same definition reference a single copy of the geometry.
# =============================================================================

import hashlib
import math
import struct
import ifcopenshell.api
from specklepy.objects.base import Base
from utils.helpers import _get, MM_SCALES
from utils.geometry import unwrap_chunks, decode_faces, build_ifc_facesets, _get_shared, _is_mesh
from utils.curves import is_curve, build_curve_rep_map


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
      "by_id"             : {obj_id_lower[:32] -> object}   for Revit format
      "by_app_id"         : {applicationId_lower -> object}  for Revit format
      "ifc_proxies"       : {"DEFINITION:xxx" -> proxy}      for IFC format
      "ifc_meshes"        : {meshAppId -> Mesh}               for IFC format
      "definition_sources": set of applicationId (lowercase) that are definition
                            geometry sources -- these should be skipped during export
    """
    by_id      = {}
    by_app_id  = {}
    ifc_proxies = {}
    ifc_meshes  = {}
    definition_sources = set()

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
            # Collect all objects referenced by this proxy as definition sources
            object_ids = _get(proxy, "objects") or []
            for oid in (object_ids if isinstance(object_ids, list) else [object_ids]):
                if oid:
                    definition_sources.add(str(oid).lower())

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
    print(f"   Definition sources:        {len(definition_sources)}")

    return {
        "by_id":              by_id,
        "by_app_id":          by_app_id,
        "ifc_proxies":        ifc_proxies,
        "ifc_meshes":         ifc_meshes,
        "definition_sources": definition_sources,
    }


def _collect_all(obj, by_id: dict, by_app_id: dict, depth: int):
    if obj is None or depth > 25:
        return

    obj_id = _get(obj, "id")
    if obj_id and isinstance(obj_id, str):
        key = obj_id.lower()
        by_id[key] = obj
        # Also store truncated -- definitionId (64 chars) matches id (32 chars)
        if len(key) == 32:
            by_id[key] = obj
        elif len(key) > 32:
            by_id[key[:32]] = obj

    app_id = _get(obj, "applicationId")
    if app_id and isinstance(app_id, str):
        by_app_id[app_id.lower()] = obj

    for key in ["elements", "@elements", "_elements",
                "displayValue", "@displayValue", "_displayValue",
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


def _get_definition_source_object(definition_id: str, definition_map: dict):
    """Resolve the first source object referenced by a definition proxy."""
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
    return by_app_id.get(str(object_ids[0]).lower())


def _get_revit_meshes(definition_id: str, definition_map: dict) -> tuple:
    """
    Revit format:
      definitionId (64-char hex) -> InstanceDefinitionProxy.applicationId
      proxy.objects[0] is a UUID applicationId -> find mesh by applicationId

    Returns (meshes, app_ids) where app_ids are all applicationIds encountered
    in the resolution chain (definition objects, geometry objects) for material fallback.
    """
    from utils.geometry import get_display_meshes

    # Step 1: find the InstanceDefinitionProxy by its applicationId (case-insensitive)
    ifc_proxies = definition_map.get("ifc_proxies", {})
    proxy = ifc_proxies.get(definition_id) or ifc_proxies.get(definition_id.lower())
    if proxy is None:
        return [], []

    # Step 2: get the mesh applicationIds from proxy.objects
    object_ids = _get(proxy, "objects") or []
    if not isinstance(object_ids, list):
        object_ids = list(object_ids)

    # Step 3: look up each mesh by applicationId, collecting all encountered app IDs
    by_app_id = definition_map.get("by_app_id", {})
    meshes = []
    encountered_app_ids = []
    for oid in object_ids:
        obj = by_app_id.get(str(oid).lower())
        if obj is not None:
            # Collect this object's applicationId
            obj_aid = _get(obj, "applicationId")
            if obj_aid:
                encountered_app_ids.append(str(obj_aid))
            # Also collect applicationIds from displayValue items (BrepX, etc.)
            for key in ["displayValue", "@displayValue", "_displayValue"]:
                display = _get(obj, key)
                if display:
                    items = display if isinstance(display, list) else [display]
                    for item in items:
                        item_aid = _get(item, "applicationId")
                        if item_aid:
                            encountered_app_ids.append(str(item_aid))
                    break
            # The found object may itself be a mesh, or contain displayValue meshes
            found_meshes = get_display_meshes(obj)
            if found_meshes:
                meshes.extend(found_meshes)
            elif _is_mesh(obj):
                meshes.append(obj)
    return meshes, encountered_app_ids


def _get_ifc_meshes(definition_id: str, definition_map: dict) -> tuple:
    """
    IFC format: definitionId = "DEFINITION:224058_mat0"
    Look up proxy -> objects list -> meshes from ifc_meshes dict.
    Returns (meshes, []) -- no extra app_ids needed, mesh applicationIds match directly.
    """
    ifc_proxies = definition_map.get("ifc_proxies", {})
    ifc_meshes  = definition_map.get("ifc_meshes", {})

    proxy = ifc_proxies.get(definition_id)
    if proxy is None:
        return [], []

    object_ids = _get(proxy, "objects") or []
    result = []
    for oid in (object_ids if isinstance(object_ids, list) else [object_ids]):
        mesh = ifc_meshes.get(str(oid))
        if mesh is not None:
            result.append(mesh)
    return result, []


def _resolve_instance_scale(obj, stream_scale: float) -> float:
    """
    Resolve scale for the transform translation.
    Tries bracket access for '_units' (Revit uses underscore).
    IFC format instances have units="m" -> scale=1.0 (no scaling).
    """
    for key in ["units", "_units"]:
        try:
            units = obj[key]
            if units and isinstance(units, str):
                s = MM_SCALES.get(units.lower().strip())
                if s is not None:
                    return s
        except Exception:
            pass
    return stream_scale


# Stats
_stats = {"found": 0, "not_found": 0}

# Cache: mesh id -> (verts_scaled, face_groups) to avoid re-unpacking
# AND re-scaling the same definition mesh across many instances that share it.
_mesh_data_cache: dict = {}

# Cache: definition_id -> IfcRepresentationMap (or None if no geometry)
# All instances sharing the same definition reuse one geometry copy.
_rep_map_cache: dict = {}

# Cache: geometry content hash -> IfcRepresentationMap
# Enables sharing across different definitionIds that have identical geometry.
_geometry_hash_cache: dict = {}

# Shared identity placement for all instances (keyed by ifc file id)
_identity_placement_cache: dict[int, object] = {}


# --------------------------------------------------------------------------- #
# Geometry content hashing
# --------------------------------------------------------------------------- #

def _hash_mesh_data(mesh_data_list: list, material_key: str = "") -> str:
    """Compute a content hash from mesh geometry data for deduplication.

    mesh_data_list: list of (verts_local, face_groups) tuples
    material_key:   string identifying the material (included in hash)
    Returns: hex digest string
    """
    h = hashlib.md5(usedforsecurity=False)
    for verts_local, face_groups in mesh_data_list:
        # Hash rounded vertices as packed floats (faster than str conversion)
        for i in range(0, len(verts_local), 3):
            h.update(struct.pack("3f",
                round(verts_local[i], 3),
                round(verts_local[i+1], 3),
                round(verts_local[i+2], 3),
            ))
        # Hash face indices
        for face in face_groups:
            h.update(struct.pack(f"{len(face)}i", *face))
        # Separator between meshes
        h.update(b"|")
    if material_key:
        h.update(material_key.encode())
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# IfcRepresentationMap builder -- geometry created once per definition
# --------------------------------------------------------------------------- #

def _collect_mesh_data(meshes: list, ifc_format: bool) -> list:
    """Unpack, scale, and cache mesh vertex/face data.

    Returns list of (mesh_obj, verts_local, face_groups) tuples.
    """
    result = []
    for mesh in meshes:
        mesh_id = _get(mesh, "id") or _get(mesh, "applicationId")
        if mesh_id and mesh_id in _mesh_data_cache:
            verts_local, face_groups = _mesh_data_cache[mesh_id]
        else:
            raw_verts = _get(mesh, "vertices") or []
            raw_faces = _get(mesh, "faces") or []
            verts     = unwrap_chunks(raw_verts if isinstance(raw_verts, list) else list(raw_verts))
            faces_raw = unwrap_chunks(raw_faces if isinstance(raw_faces, list) else list(raw_faces))
            if not verts or not faces_raw:
                continue

            mesh_units = _get(mesh, "units") or _get(mesh, "_units") or ("m" if ifc_format else "mm")
            ms = MM_SCALES.get(mesh_units.lower().strip(), 1.0)

            try:
                face_groups = decode_faces(faces_raw)
            except Exception as e:
                print(f"  Warning: Instance face decode: {e}")
                continue

            verts_local = [float(v) * ms for v in verts]

            if mesh_id:
                _mesh_data_cache[mesh_id] = (verts_local, face_groups)

        result.append((mesh, verts_local, face_groups))
    return result


def _resolve_material_key(meshes_data: list, material_manager, fallback_app_ids, definition_id) -> str:
    """Build a material cache key string for geometry hashing."""
    if not material_manager:
        return ""
    parts = []
    for mesh, _, _ in meshes_data:
        mesh_app_id = _get(mesh, "applicationId")
        style = material_manager.get_style_with_fallbacks(
            primary_app_id=str(mesh_app_id) if mesh_app_id else None,
            fallback_app_ids=fallback_app_ids,
            definition_id=definition_id,
        )
        parts.append(str(id(style)) if style else "")
    return "|".join(parts)


def _build_rep_map(ifc, body_context, meshes: list, ifc_format: bool,
                   material_manager=None, fallback_app_ids: list = None,
                   definition_id: str = None):
    """
    Build an IfcRepresentationMap from definition meshes.
    Uses content-based hashing to reuse identical geometry across different
    definitionIds. Returns IfcRepresentationMap or None if no valid geometry.
    """
    # Step 1: Collect and cache raw mesh data (no IFC entities created yet)
    meshes_data = _collect_mesh_data(meshes, ifc_format)
    if not meshes_data:
        return None

    # Step 2: Compute content hash to check for identical geometry
    mat_key = _resolve_material_key(meshes_data, material_manager, fallback_app_ids, definition_id)
    geom_hash = _hash_mesh_data(
        [(verts, faces) for _, verts, faces in meshes_data],
        material_key=mat_key,
    )

    if geom_hash in _geometry_hash_cache:
        return _geometry_hash_cache[geom_hash]

    # Step 3: No match -- build IFC geometry entities
    geom_items = []

    for mesh, verts_local, face_groups in meshes_data:
        mesh_facesets = build_ifc_facesets(ifc, verts_local, face_groups)
        if not mesh_facesets:
            continue

        if material_manager:
            mesh_app_id = _get(mesh, "applicationId")
            style = material_manager.get_style_with_fallbacks(
                primary_app_id=str(mesh_app_id) if mesh_app_id else None,
                fallback_app_ids=fallback_app_ids,
                definition_id=definition_id,
            )
            if style:
                for fs in mesh_facesets:
                    try:
                        ifcopenshell.api.run(
                            "style.assign_item_style", ifc,
                            item=fs, style=style,
                        )
                        material_manager._apply_count += 1
                    except Exception:
                        pass

        geom_items.extend(mesh_facesets)

    if not geom_items:
        _geometry_hash_cache[geom_hash] = None
        return None

    shared = _get_shared(ifc)
    a2p    = ifc.createIfcAxis2Placement3D(shared["origin_0"], None, None)

    mapped_rep = ifc.createIfcShapeRepresentation(
        ContextOfItems=body_context,
        RepresentationIdentifier="Body",
        RepresentationType="Tessellation",
        Items=geom_items,
    )

    rep_map = ifc.createIfcRepresentationMap(a2p, mapped_rep)
    _geometry_hash_cache[geom_hash] = rep_map
    return rep_map


# --------------------------------------------------------------------------- #
# Transform -> IfcCartesianTransformationOperator3D
# --------------------------------------------------------------------------- #

def _vec_magnitude(x, y, z):
    return math.sqrt(x*x + y*y + z*z)


# Cache: rounded direction tuple -> IfcDirection entity (keyed by ifc file id)
_direction_cache: dict[int, dict] = {}

def _get_or_create_direction(ifc, dx, dy, dz):
    """Return a cached IfcDirection or create and cache a new one."""
    fid = id(ifc)
    if fid not in _direction_cache:
        _direction_cache[fid] = {}
    cache = _direction_cache[fid]
    # Round to 6 decimals -- sufficient for unit vectors
    key = (round(dx, 6), round(dy, 6), round(dz, 6))
    if key not in cache:
        cache[key] = ifc.createIfcDirection([key[0], key[1], key[2]])
    return cache[key]


def _make_transform_operator(ifc, t: list, ts: float):
    """
    Convert a row-major 4x4 matrix + translation scale into an
    IfcCartesianTransformationOperator3DnonUniform.

    t:  16 floats, row-major [r00,r01,r02,tx, r10,r11,r12,ty, r20,r21,r22,tz, 0,0,0,1]
    ts: scale factor for translation components (e.g. 1000.0 for m->mm)

    IfcCartesianTransformationOperator axes represent the COLUMNS of M:
      Axis1 = column 0 = where local X maps -> (t[0], t[4], t[8])
      Axis2 = column 1 = where local Y maps -> (t[1], t[5], t[9])
      Axis3 = column 2 = where local Z maps -> (t[2], t[6], t[10])

    Always uses the non-uniform variant with explicit Axis3 to ensure
    correct orientation for all transform types (mirrors, non-orthogonal, etc.).

    Returns the IFC entity, or None if the transform is degenerate.
    """
    # Extract COLUMNS of the 3x3 rotation/scale sub-matrix
    ax1 = (float(t[0]), float(t[4]), float(t[8]))
    ax2 = (float(t[1]), float(t[5]), float(t[9]))
    ax3 = (float(t[2]), float(t[6]), float(t[10]))

    s1 = _vec_magnitude(*ax1)
    s2 = _vec_magnitude(*ax2)
    s3 = _vec_magnitude(*ax3)

    if s1 < 1e-10 or s2 < 1e-10 or s3 < 1e-10:
        return None  # degenerate transform

    # Normalized direction vectors -- reuse cached IfcDirection entities
    d1 = _get_or_create_direction(ifc, ax1[0]/s1, ax1[1]/s1, ax1[2]/s1)
    d2 = _get_or_create_direction(ifc, ax2[0]/s2, ax2[1]/s2, ax2[2]/s2)
    d3 = _get_or_create_direction(ifc, ax3[0]/s3, ax3[1]/s3, ax3[2]/s3)

    # Translation, scaled and rounded to mm
    tx = round(float(t[3])  * ts, 3)
    ty = round(float(t[7])  * ts, 3)
    tz = round(float(t[11]) * ts, 3)
    origin = ifc.createIfcCartesianPoint([tx, ty, tz])

    # Round scales for cleaner output
    s1 = round(s1, 6)
    s2 = round(s2, 6)
    s3 = round(s3, 6)

    return ifc.createIfcCartesianTransformationOperator3DnonUniform(
        d1,      # Axis1
        d2,      # Axis2
        origin,  # LocalOrigin
        s1,      # Scale
        d3,      # Axis3 (explicit -- never derived)
        s2,      # Scale2
        s3,      # Scale3
    )


# --------------------------------------------------------------------------- #
# Main conversion -- IfcMappedItem approach
# --------------------------------------------------------------------------- #

def instance_to_ifc(ifc, body_context, obj: Base, definition_map: dict,
                    scale: float = 1.0, material_manager=None):
    """
    Convert a Speckle InstanceProxy -> (IfcShapeRepresentation, IfcLocalPlacement).

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

    # Translation scale: IFC format transform is in metres -> convert to mm
    # Revit format transform is already in mm (same as IFC file units)
    ts = 1000.0 if ifc_format else _resolve_instance_scale(obj, scale)

    # Identity placement (transform is encoded in the MappedItem) -- shared across all instances
    fid = id(ifc)
    if fid not in _identity_placement_cache:
        shared = _get_shared(ifc)
        a2p = ifc.createIfcAxis2Placement3D(shared["origin_0"], None, None)
        _identity_placement_cache[fid] = ifc.createIfcLocalPlacement(PlacementRelTo=None, RelativePlacement=a2p)
    placement = _identity_placement_cache[fid]

    # --- Get or build IfcRepresentationMap (cached per definition_id) ---
    if definition_id not in _rep_map_cache:
        if ifc_format:
            meshes, extra_app_ids = _get_ifc_meshes(definition_id, definition_map)
        else:
            meshes, extra_app_ids = _get_revit_meshes(definition_id, definition_map)

        # Build fallback app_id list: instance's own + definition chain IDs
        instance_app_id = _get(obj, "applicationId")
        fallback_ids = []
        if instance_app_id:
            fallback_ids.append(str(instance_app_id))
        fallback_ids.extend(extra_app_ids)

        rep_map_result = None
        if meshes:
            rep_map_result = _build_rep_map(
                ifc, body_context, meshes, ifc_format, material_manager,
                fallback_app_ids=fallback_ids,
                definition_id=definition_id,
            )

        # If no mesh geometry produced, try curve geometry from the definition object
        if rep_map_result is None:
            curve_obj = _get_definition_source_object(definition_id, definition_map)
            if curve_obj and is_curve(curve_obj):
                curve_scale = _resolve_instance_scale(curve_obj, 1.0)
                rep_map_result = build_curve_rep_map(
                    ifc, body_context, curve_obj, scale=curve_scale,
                    material_manager=material_manager,
                    fallback_app_ids=fallback_ids,
                    definition_id=definition_id,
                )

        _rep_map_cache[definition_id] = rep_map_result
        if rep_map_result is not None:
            _stats["found"] += 1
        else:
            _stats["not_found"] += 1
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
    return _get_definition_source_object(definition_id, definition_map)


def is_definition_source(obj, definition_map: dict) -> bool:
    """Return True if this object is a definition geometry source (should not be exported standalone)."""
    app_id = _get(obj, "applicationId")
    if not app_id:
        return False
    return str(app_id).lower() in definition_map.get("definition_sources", set())


def print_instance_stats():
    total = _stats["found"] + _stats["not_found"]
    print(f"  Instance resolution: {_stats['found']}/{total} definitions found")
    if _stats["not_found"] > 0:
        print(f"  Warning: {_stats['not_found']} instances had no definition geometry")
    unique_defs = len(_rep_map_cache)
    unique_geom = len([v for v in _geometry_hash_cache.values() if v is not None])
    if unique_defs > unique_geom:
        print(f"  Geometry dedup: {unique_defs} definitions -> {unique_geom} unique geometries")


def reset_caches():
    """Reset module-level caches (call at start of each export run)."""
    _mesh_data_cache.clear()
    _rep_map_cache.clear()
    _geometry_hash_cache.clear()
    _identity_placement_cache.clear()
    _direction_cache.clear()
    _stats["found"] = 0
    _stats["not_found"] = 0
