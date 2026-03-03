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
# =============================================================================

from specklepy.objects.base import Base
from utils.geometry import _get, unwrap_chunks, decode_faces, _UNIT_SCALES, build_ifc_breps


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

    # Diagnostic: dump first 3 instanceDefinitionProxies to understand structure
    print("\n   [PROXY DIAG] First 3 instanceDefinitionProxies from root:")
    proxies_raw2 = _get(root, "instanceDefinitionProxies")
    if proxies_raw2:
        sample = proxies_raw2 if isinstance(proxies_raw2, list) else [proxies_raw2]
        for i, proxy in enumerate(sample[:3]):
            app_id  = _get(proxy, "applicationId") or "?"
            name    = _get(proxy, "name") or "?"
            objects = _get(proxy, "objects") or []
            obj_ids = list(objects)[:3] if objects else []
            print(f"   [{i}] appId={app_id}")
            print(f"        name={name}")
            print(f"        objects={obj_ids} (len={len(list(objects)) if objects else 0})")
            # Check if first object is found in our maps
            if obj_ids:
                oid = str(obj_ids[0])
                in_by_id     = oid.lower()[:32] in by_id
                in_by_app_id = oid.lower() in by_app_id
                print(f"        objects[0]='{oid}' → in by_id: {in_by_id}, in by_app_id: {in_by_app_id}")
    else:
        print("   [PROXY DIAG] No instanceDefinitionProxies found on root!")
        # Check where they might be
        for key in ["@instanceDefinitionProxies", "instancedefinitionproxies"]:
            val = _get(root, key)
            if val:
                print(f"   Found under key '{key}': {type(val)}")

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


def _parse_transform(t: list, scale: float) -> tuple:
    """
    Row-major 4x4 matrix.
    Translation at t[3], t[7], t[11] — scaled to metres.
    Local X axis = row 0, Local Z axis = row 2.
    """
    tx = float(t[3])  * scale
    ty = float(t[7])  * scale
    tz = float(t[11]) * scale
    x_axis = (float(t[0]), float(t[1]), float(t[2]))
    z_axis = (float(t[8]), float(t[9]), float(t[10]))
    return (tx, ty, tz), x_axis, z_axis


def _make_ifc_placement(ifc, tx, ty, tz, x_axis, z_axis):
    origin = ifc.createIfcCartesianPoint([tx, ty, tz])
    x_dir  = ifc.createIfcDirection(list(x_axis))
    z_dir  = ifc.createIfcDirection(list(z_axis))
    a2p    = ifc.createIfcAxis2Placement3D(origin, z_dir, x_dir)
    return ifc.createIfcLocalPlacement(PlacementRelTo=None, RelativePlacement=a2p)


# Stats
_stats   = {"found": 0, "not_found": 0}
_dbg_cnt = [0]


_MM_SCALES = {
    "mm": 1.0, "millimeter": 1.0, "millimeters": 1.0,
    "cm": 10.0, "centimeter": 10.0,
    "m": 1000.0, "meter": 1000.0, "meters": 1000.0,
    "ft": 304.8, "in": 25.4,
}


def _apply_transform(t: list, vx: float, vy: float, vz: float, ts: float) -> tuple:
    """
    Apply a row-major 4x4 transform to a single vertex.
    ts = scale factor applied to the translation components only (not rotation).
    For Revit mm data with IFC in mm: ts=1.0 (no conversion).
    For IFC-format transforms (metres): ts=1000.0 (m→mm).
    Rotation components are dimensionless and never scaled.
    """
    x = t[0]*vx + t[1]*vy + t[2]*vz  + t[3]  * ts
    y = t[4]*vx + t[5]*vy + t[6]*vz  + t[7]  * ts
    z = t[8]*vx + t[9]*vy + t[10]*vz + t[11] * ts
    return x, y, z


def instance_to_ifc(ifc, body_context, obj: Base, definition_map: dict,
                    scale: float = 1.0, material_manager=None):
    """
    Convert a Speckle InstanceProxy → (IfcShapeRepresentation, IfcLocalPlacement).

    Strategy: BAKE the full 4x4 transform into every vertex (world coordinates).
    Creates one IfcFacetedBrep per definition mesh so each can carry its own
    material style via renderMaterialProxies.
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

    if _dbg_cnt[0] < 6:
        _dbg_cnt[0] += 1
        fmt = "IFC" if ifc_format else "Revit"
        x_axis = (round(t[0],2), round(t[1],2), round(t[2],2))
        z_axis = (round(t[8],2), round(t[9],2), round(t[10],2))
        print(f"  [INST {_dbg_cnt[0]} {fmt}] {definition_id[:40]}")
        print(f"    t[3]={t[3]:.1f} t[7]={t[7]:.1f} t[11]={t[11]:.1f}  x={x_axis}  z={z_axis}")

    # World-origin placement (geometry is baked to world coords)
    origin    = ifc.createIfcCartesianPoint([0.0, 0.0, 0.0])
    a2p       = ifc.createIfcAxis2Placement3D(origin, None, None)
    placement = ifc.createIfcLocalPlacement(PlacementRelTo=None, RelativePlacement=a2p)

    # Get definition meshes
    if ifc_format:
        meshes = _get_ifc_meshes(definition_id, definition_map)
    else:
        meshes = _get_revit_meshes(definition_id, definition_map)

    if not meshes:
        _stats["not_found"] += 1
        return None, placement

    _stats["found"] += 1

    # One brep per mesh so each can have its own material style
    brep_items = []
    for mesh in meshes:
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

        # Pre-compute world coords for all vertices in this mesh
        verts_world = []
        for vi in range(0, len(verts) - 2, 3):
            lx = float(verts[vi])   * ms
            ly = float(verts[vi+1]) * ms
            lz = float(verts[vi+2]) * ms
            wx, wy, wz = _apply_transform(t, lx, ly, lz, ts)
            verts_world.append(wx)
            verts_world.append(wy)
            verts_world.append(wz)

        mesh_breps = build_ifc_breps(ifc, verts_world, face_groups)

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
        return None, placement

    rep = ifc.createIfcShapeRepresentation(
        ContextOfItems=body_context,
        RepresentationIdentifier="Body",
        RepresentationType="Brep",
        Items=brep_items,
    )
    return rep, placement


def print_instance_stats():
    total = _stats["found"] + _stats["not_found"]
    print(f"  Instance resolution: {_stats['found']}/{total} definitions found")
    if _stats["not_found"] > 0:
        print(f"  ⚠️  {_stats['not_found']} instances had no definition geometry")
