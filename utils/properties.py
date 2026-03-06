# =============================================================================
# properties.py
# Writes IFC property sets matching the structure of Revit's native IFC export.
#
# Revit native IFC export produces:
#   - Element Name:    "Family:TypeName:ElementId"  e.g. "Basic Roof:SG Metal Panels roof:243274"
#   - Element Tag:     ElementId string              e.g. "243274"
#   - Element GlobalId: from IFC Parameters.IfcGUID
#   - Pset_<EntityType>Common with typed properties (IfcBoolean, IfcIdentifier, etc.)
#   - Pset_EnvironmentalImpactIndicators with Reference = TypeName
#
# Our Speckle source fields:
#   obj.family      → Family name
#   obj.type        → Type name (= Reference in all Common psets)
#   properties.elementId → Revit ElementId → Tag
#   properties.Parameters.Instance Parameters.IFC Parameters.IfcGUID.value → GlobalId
#   properties.Parameters.Type Parameters.*  → typed IFC properties
#   properties.Parameters.Instance Parameters.* → typed IFC properties
# =============================================================================

import ifcopenshell.api
from specklepy.objects.base import Base


# ---------------------------------------------------------------------------
# IFC entity → standard Common pset name
# ---------------------------------------------------------------------------
COMMON_PSET: dict[str, str] = {
    "IfcWall":                      "Pset_WallCommon",
    "IfcWallStandardCase":          "Pset_WallCommon",
    "IfcSlab":                      "Pset_SlabCommon",
    "IfcRoof":                      "Pset_RoofCommon",
    "IfcColumn":                    "Pset_ColumnCommon",
    "IfcBeam":                      "Pset_BeamCommon",
    "IfcMember":                    "Pset_MemberCommon",
    "IfcDoor":                      "Pset_DoorCommon",
    "IfcWindow":                    "Pset_WindowCommon",
    "IfcStair":                     "Pset_StairCommon",
    "IfcStairFlight":               "Pset_StairFlightCommon",
    "IfcRamp":                      "Pset_RampCommon",
    "IfcRailing":                   "Pset_RailingCommon",
    "IfcCovering":                  "Pset_CoveringCommon",
    "IfcCurtainWall":               "Pset_CurtainWallCommon",
    "IfcFooting":                   "Pset_FootingCommon",
    "IfcPile":                      "Pset_PileCommon",
    "IfcSpace":                     "Pset_SpaceCommon",
    "IfcSite":                      "Pset_SiteCommon",
    "IfcBuildingStorey":            "Pset_BuildingStoreyCommon",
    "IfcBuilding":                  "Pset_BuildingCommon",
    "IfcBuildingElementProxy":      "Pset_BuildingElementProxyCommon",
    "IfcFurnishingElement":         "Pset_FurnitureTypeCommon",
    "IfcLightFixture":              "Pset_LightFixtureTypeCommon",
    "IfcOpeningElement":            "Pset_OpeningElementCommon",
    "IfcPlate":                     "Pset_PlateCommon",
}

# ---------------------------------------------------------------------------
# Revit parameter internal names → (IFC pset property name, IFC value factory)
# These are harvested from the Common psets Revit native export produces.
# ---------------------------------------------------------------------------
def _bool(v):
    return ("IfcBoolean", bool(v))

def _identifier(v):
    return ("IfcIdentifier", str(v))

def _label(v):
    return ("IfcLabel", str(v))

def _real(v):
    return ("IfcReal", float(v))

def _thermal(v):
    return ("IfcThermalTransmittanceMeasure", float(v))

def _length(v):
    return ("IfcPositiveLengthMeasure", float(v))

def _count(v):
    return ("IfcCountMeasure", int(v))

def _angle(v):
    return ("IfcPlaneAngleMeasure", float(v))


# Map: Revit internalDefinitionName → (IFC property name, value factory fn)
REVIT_PARAM_TO_IFC: dict[str, tuple] = {
    # Wall
    "WALL_ATTR_ROOM_BOUNDING":              ("IsExternal",          _bool),
    "WALL_STRUCTURAL_SIGNIFICANT":          ("LoadBearing",         _bool),
    "WALL_STRUCTURAL_USAGE_PARAM":          ("LoadBearing",         _bool),
    "ANALYTICAL_THERMAL_RESISTANCE":        ("ThermalTransmittance", _thermal),
    "ANALYTICAL_HEAT_TRANSFER_COEFFICIENT": ("ThermalTransmittance", _thermal),

    # Slab / Roof / Floor
    "HOST_AREA_COMPUTED":                   ("NetArea",             _real),
    "HOST_VOLUME_COMPUTED":                 ("NetVolume",           _real),
    "ROOF_SLOPE":                           ("PitchAngle",          _angle),

    # Stair
    "STAIR_RISER_HEIGHT":                   ("RiserHeight",         _length),
    "STAIR_TREAD_DEPTH":                    ("TreadLength",         _length),
    "STAIR_NUMBER_OF_RISERS":               ("NumberOfRiser",       _count),
    "STAIR_NUMBER_OF_TREADS":               ("NumberOfTreads",      _count),
    "STAIR_NOSING_LENGTH":                  ("NosingLength",        _length),

    # Railing
    "RAILING_HEIGHT":                       ("Height",              _length),

    # Door / Window
    "DOOR_FIRE_RATING":                     ("FireExit",            _bool),

    # General identity
    "ALL_MODEL_FAMILY_NAME":                ("Reference",           _identifier),
    "ALL_MODEL_TYPE_NAME":                  ("Reference",           _identifier),
    "ASSEMBLY_CODE":                        ("Reference",           _identifier),
}

# External category OST_ codes (used to infer IsExternal)
EXTERNAL_CATEGORIES = {
    "OST_Walls", "OST_Roofs", "OST_Windows", "OST_Doors",
    "OST_CurtainWallPanels", "OST_CurtainWallMullions",
    "OST_StructuralColumns", "OST_StructuralFraming",
    "OST_Stairs", "OST_StairsRailing", "OST_Ramps",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_props_dict(obj: Base) -> dict:
    for key in ["properties", "@properties"]:
        try:
            p = obj[key]
            if p is None:
                continue
            if hasattr(p, "get_dynamic_member_names"):
                return {n: p[n] for n in p.get_dynamic_member_names()}
            if isinstance(p, dict):
                return p
        except Exception:
            pass
    return {}


def _get_nested(d: dict, *keys):
    """Safely walk nested dicts/objects."""
    cur = d
    for k in keys:
        if cur is None:
            return None
        if isinstance(cur, dict):
            cur = cur.get(k)
        else:
            try:
                cur = cur[k]
            except Exception:
                return None
    return cur


def _param_value(params_block: dict, internal_name: str):
    """
    Search all groups in a parameter block for a param with the given
    internalDefinitionName. Returns the raw value or None.
    """
    if not isinstance(params_block, dict):
        return None
    for group in params_block.values():
        if not isinstance(group, dict):
            continue
        for entry in group.values():
            if isinstance(entry, dict) and entry.get("internalDefinitionName") == internal_name:
                return entry.get("value")
    return None


def _make_prop(ifc, name: str, ifc_type: str, value) -> object | None:
    """Create an IfcPropertySingleValue with the correct IFC measure type."""
    try:
        nominal = ifc.create_entity(ifc_type, wrappedValue=value)
        return ifc.create_entity(
            "IfcPropertySingleValue",
            Name=name,
            NominalValue=nominal,
        )
    except Exception as e:
        return None


def _write_pset(ifc, element, pset_name: str, props: list):
    """Write an IfcPropertySet with the given list of IfcProperty objects."""
    if not props:
        return
    try:
        pset = ifcopenshell.api.run("pset.add_pset", ifc, product=element, name=pset_name)
        # Directly attach the pre-built property objects
        pset.HasProperties = props
    except Exception as e:
        print(f"  ⚠️  {pset_name}: {e}")


# ---------------------------------------------------------------------------
# Element name + tag (matching Revit native IFC format)
# ---------------------------------------------------------------------------

def build_element_name(obj: Base) -> str:
    """
    Build element name in Revit native IFC format: "Family:TypeName:ElementId"
    Falls back gracefully if any part is missing.
    """
    props = _get_props_dict(obj)
    family   = getattr(obj, "family", None) or ""
    typ      = getattr(obj, "type", None)   or ""
    elem_id  = props.get("elementId", "") or getattr(obj, "applicationId", "") or ""

    # Treat literal "none" (case-insensitive) the same as empty — Revit exports
    # placeholder objects with family/type set to the string "none".
    if family.strip().lower() == "none":
        family = ""
    if typ.strip().lower() == "none":
        typ = ""

    parts = [p for p in [family, typ] if p]
    if elem_id:
        parts.append(str(elem_id))
    return ":".join(parts) if parts else (getattr(obj, "id", None) or "unnamed")


def get_element_tag(obj: Base) -> str | None:
    """Return Revit ElementId as the IFC Tag."""
    props = _get_props_dict(obj)
    elem_id = props.get("elementId")
    return str(elem_id) if elem_id else None


def get_ifc_guid(obj: Base) -> str | None:
    """
    Read IfcGUID from the Revit IFC Parameters.
    Falls back to None (ifcopenshell will auto-generate a GUID).
    """
    props = _get_props_dict(obj)
    params = props.get("Parameters") or {}
    inst   = params.get("Instance Parameters") or {}
    ifc_p  = inst.get("IFC Parameters") or {}
    entry  = ifc_p.get("IfcGUID") or {}
    val    = entry.get("value") if isinstance(entry, dict) else None
    return str(val) if val else None


# ---------------------------------------------------------------------------
# Standard Common pset (Pset_WallCommon etc.)
# ---------------------------------------------------------------------------

def write_common_pset(ifc, element, obj: Base, ifc_class: str, category_name: str = ""):
    """
    Write the standard Pset_<Entity>Common property set, matching Revit native export.
    Properties: Reference (TypeName), IsExternal, LoadBearing, ThermalTransmittance, etc.
    """
    pset_name = COMMON_PSET.get(ifc_class)
    if not pset_name:
        return

    props = _get_props_dict(obj)
    params = props.get("Parameters") or {}
    type_params = params.get("Type Parameters") or {}
    inst_params = params.get("Instance Parameters") or {}

    ifc_props = []

    # Reference = TypeName (always present in Revit IFC)
    type_name = getattr(obj, "type", None) or ""
    if type_name:
        p = _make_prop(ifc, "Reference", "IfcIdentifier", type_name)
        if p:
            ifc_props.append(p)

    # IsExternal — derive from builtInCategory or "Constraints" parameters
    bic = props.get("builtInCategory", "")
    is_external = bic in EXTERNAL_CATEGORIES
    if not is_external:
        # Some elements expose it directly as a parameter
        ext_val = _param_value(inst_params, "WALL_ATTR_ROOM_BOUNDING")
        if ext_val is not None:
            is_external = bool(ext_val)
    if ifc_class not in {"IfcSpace", "IfcSite", "IfcBuildingStorey", "IfcBuilding",
                          "IfcFurnishingElement", "IfcOpeningElement"}:
        p = _make_prop(ifc, "IsExternal", "IfcBoolean", is_external)
        if p:
            ifc_props.append(p)

    # LoadBearing — walls, columns, beams, slabs
    if ifc_class in {"IfcWall", "IfcWallStandardCase", "IfcSlab", "IfcColumn", "IfcBeam"}:
        lb_val = (_param_value(inst_params, "WALL_STRUCTURAL_SIGNIFICANT") or
                  _param_value(inst_params, "WALL_STRUCTURAL_USAGE_PARAM") or
                  _param_value(type_params, "WALL_STRUCTURAL_SIGNIFICANT"))
        lb = bool(lb_val) if lb_val is not None else False
        p = _make_prop(ifc, "LoadBearing", "IfcBoolean", lb)
        if p:
            ifc_props.append(p)

    # ThermalTransmittance — walls, roofs, slabs, doors, windows
    if ifc_class in {"IfcWall", "IfcWallStandardCase", "IfcRoof", "IfcSlab",
                     "IfcDoor", "IfcWindow"}:
        u_val = (_param_value(type_params, "ANALYTICAL_HEAT_TRANSFER_COEFFICIENT") or
                 _param_value(inst_params, "ANALYTICAL_HEAT_TRANSFER_COEFFICIENT"))
        if u_val is not None:
            try:
                p = _make_prop(ifc, "ThermalTransmittance", "IfcThermalTransmittanceMeasure", float(u_val))
                if p:
                    ifc_props.append(p)
            except Exception:
                pass

    # PitchAngle — roofs/slabs
    if ifc_class in {"IfcRoof", "IfcSlab"}:
        slope = _param_value(inst_params, "ROOF_SLOPE")
        if slope is not None:
            try:
                p = _make_prop(ifc, "PitchAngle", "IfcPlaneAngleMeasure", float(slope))
                if p:
                    ifc_props.append(p)
            except Exception:
                pass

    # Stair-specific
    if ifc_class in {"IfcStair", "IfcStairFlight"}:
        for internal, prop_name, factory in [
            ("STAIR_RISER_HEIGHT",    "RiserHeight",   "IfcPositiveLengthMeasure"),
            ("STAIR_TREAD_DEPTH",     "TreadLength",   "IfcPositiveLengthMeasure"),
            ("STAIR_NUMBER_OF_RISERS","NumberOfRiser",  "IfcCountMeasure"),
            ("STAIR_NUMBER_OF_TREADS","NumberOfTreads", "IfcCountMeasure"),
        ]:
            v = _param_value(inst_params, internal) or _param_value(type_params, internal)
            if v is not None:
                try:
                    p = _make_prop(ifc, prop_name, factory, float(v) if "Measure" in factory else int(v))
                    if p:
                        ifc_props.append(p)
                except Exception:
                    pass

    # Railing height
    if ifc_class == "IfcRailing":
        h = _param_value(inst_params, "RAILING_HEIGHT") or _param_value(type_params, "RAILING_HEIGHT")
        if h is not None:
            try:
                p = _make_prop(ifc, "Height", "IfcPositiveLengthMeasure", float(h))
                if p:
                    ifc_props.append(p)
            except Exception:
                pass

    _write_pset(ifc, element, pset_name, ifc_props)


# ---------------------------------------------------------------------------
# Pset_EnvironmentalImpactIndicators (always written, Reference = TypeName)
# ---------------------------------------------------------------------------

def write_environmental_pset(ifc, element, obj: Base):
    """Write Pset_EnvironmentalImpactIndicators with Reference = TypeName."""
    type_name = getattr(obj, "type", None) or ""
    if not type_name:
        return
    p = _make_prop(ifc, "Reference", "IfcIdentifier", type_name)
    if p:
        _write_pset(ifc, element, "Pset_EnvironmentalImpactIndicators", [p])


# ---------------------------------------------------------------------------
# Custom Revit parameters pset (all remaining instance + type params)
# ---------------------------------------------------------------------------

def _safe_str(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:.6g}"
    s = str(value).strip()
    return s or None


def _flatten_params(params_block: dict) -> dict:
    """Flatten Type or Instance parameter block into {name: display_value}."""
    result = {}
    skip_units = {"", "None", "General", "Currency", "Integer"}
    for group in params_block.values():
        if not isinstance(group, dict):
            continue
        for entry in group.values():
            if not isinstance(entry, dict):
                continue
            name  = entry.get("name")
            value = entry.get("value")
            units = entry.get("units", "") or ""
            if not name or value is None:
                continue
            val_str = _safe_str(value)
            if val_str is None:
                continue
            display = f"{val_str} {units}".strip() if units not in skip_units else val_str
            result[name] = display
    return result


def write_revit_params(ifc, element, obj: Base):
    """
    Write remaining Revit parameters as two custom property sets
    using the vendor prefix 'RVT_' (not 'Pset_' which is reserved):
      RVT_TypeParameters     — from Type Parameters
      RVT_InstanceParameters — from Instance Parameters
    """
    props = _get_props_dict(obj)
    params = props.get("Parameters") or {}

    type_flat = _flatten_params(params.get("Type Parameters") or {})
    inst_flat = _flatten_params(params.get("Instance Parameters") or {})

    def build_str_props(flat: dict) -> list:
        out = []
        for name, val in flat.items():
            try:
                nominal = ifc.create_entity("IfcLabel", wrappedValue=val)
                p = ifc.create_entity("IfcPropertySingleValue", Name=name, NominalValue=nominal)
                out.append(p)
            except Exception:
                pass
        return out

    type_props = build_str_props(type_flat)
    inst_props = build_str_props(inst_flat)

    if type_props:
        _write_pset(ifc, element, "RVT_TypeParameters", type_props)
    if inst_props:
        _write_pset(ifc, element, "RVT_InstanceParameters", inst_props)

    # Identity: family, type, elementId, builtInCategory
    identity = {}
    for field in ["family", "type", "category"]:
        val = getattr(obj, field, None)
        if val and isinstance(val, str) and val.strip():
            identity[field.capitalize()] = val.strip()
    elem_id = props.get("elementId")
    if elem_id:
        identity["ElementId"] = str(elem_id)
    bic = props.get("builtInCategory")
    if bic:
        identity["BuiltInCategory"] = str(bic)

    id_props = []
    for name, val in identity.items():
        try:
            nominal = ifc.create_entity("IfcLabel", wrappedValue=val)
            p = ifc.create_entity("IfcPropertySingleValue", Name=name, NominalValue=nominal)
            id_props.append(p)
        except Exception:
            pass
    if id_props:
        _write_pset(ifc, element, "RVT_Identity", id_props)


# ---------------------------------------------------------------------------
# Public API — called from main.py
# ---------------------------------------------------------------------------

def write_properties(ifc, element, obj: Base, ifc_class: str = "", category_name: str = ""):
    """
    Write all property sets for an IFC element, matching Revit native IFC export structure:
      1. Pset_<Entity>Common      — standard typed properties (Reference, IsExternal, etc.)
      2. Pset_EnvironmentalImpactIndicators — Reference = TypeName
      3. RVT_TypeParameters       — all remaining Revit type parameters
      4. RVT_InstanceParameters   — all remaining Revit instance parameters
      5. RVT_Identity             — family, type, elementId, builtInCategory
    """
    write_common_pset(ifc, element, obj, ifc_class, category_name)
    write_environmental_pset(ifc, element, obj)
    write_revit_params(ifc, element, obj)


def write_common_properties(ifc, element, obj: Base, category_name: str = ""):
    """Legacy shim — kept for compatibility with main.py call sites."""
    pass  # All handled by write_properties now