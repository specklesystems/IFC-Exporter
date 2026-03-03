# =============================================================================
# properties.py
# Extracts Revit data from Speckle DataObjects and writes IFC property sets.
#
# Revit parameter structure from the Speckle connector:
#   obj.properties = {
#     "elementId": "704282",
#     "Parameters": {
#       "Type Parameters": {
#         "Dimensions": {
#           "Thickness": {"name": "Thickness", "value": 25.4, "units": "Millimeters", ...}
#         },
#         ...
#       },
#       "Instance Parameters": {
#         "Constraints": {
#           "Level": {"name": "Level", "value": "Level 1", ...}
#         },
#         ...
#       }
#     }
#   }
#
# We flatten this into two IFC property sets:
#   Pset_RevitTypeParameters     — from "Type Parameters"
#   Pset_RevitInstanceParameters — from "Instance Parameters"
# =============================================================================

import ifcopenshell.api
from specklepy.objects.base import Base


def _safe_val(value) -> str | None:
    """Convert a value to a clean IFC-safe string."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        # Trim excessive decimals
        return f"{value:.6g}"
    if isinstance(value, (int, str)):
        s = str(value).strip()
        return s if s else None
    return str(value).strip() or None


def _extract_param(entry) -> tuple[str, str] | None:
    """
    Given a Revit parameter entry dict like:
      {"name": "Thickness", "value": 25.4, "units": "Millimeters", ...}
    Returns (display_name, display_value) or None if unusable.
    """
    if not isinstance(entry, dict):
        return None
    name = entry.get("name")
    value = entry.get("value")
    if not name or value is None:
        return None
    units = entry.get("units", "")
    # Skip non-informative unit labels
    skip_units = {"", "None", "General", "Currency", "Integer"}
    val_str = _safe_val(value)
    if val_str is None:
        return None
    if units and units not in skip_units:
        display = f"{val_str} {units}"
    else:
        display = val_str
    return str(name), display


def _flatten_param_group(group: dict) -> dict:
    """
    Flatten one parameter group (e.g. "Dimensions", "Constraints") dict.
    Each value is a Revit parameter entry {"name":..., "value":..., "units":...}.
    Returns {display_name: display_value}.
    """
    result = {}
    if not isinstance(group, dict):
        return result
    for _internal_key, entry in group.items():
        pair = _extract_param(entry)
        if pair:
            name, val = pair
            result[name] = val
    return result


def _extract_parameter_block(params_block: dict) -> dict:
    """
    Flatten all groups in a parameter block (Type Parameters or Instance Parameters).
    Returns a merged {display_name: display_value} dict.
    """
    result = {}
    if not isinstance(params_block, dict):
        return result
    for _group_name, group in params_block.items():
        result.update(_flatten_param_group(group))
    return result


def _get_properties_dict(obj: Base) -> dict:
    """Extract the raw properties dict from a DataObject."""
    for key in ["properties", "@properties", "_properties"]:
        try:
            props = obj[key]
            if props is None:
                continue
            if hasattr(props, "get_dynamic_member_names"):
                names = props.get_dynamic_member_names()
                return {n: props[n] for n in names}
            if isinstance(props, dict):
                return props
        except Exception:
            continue
    return {}


def _write_pset(ifc, element, pset_name: str, props: dict):
    """Write a property set if there are any properties."""
    if not props:
        return
    try:
        pset = ifcopenshell.api.run("pset.add_pset", ifc, product=element, name=pset_name)
        ifcopenshell.api.run("pset.edit_pset", ifc, pset=pset, properties=props)
    except Exception as e:
        print(f"  ⚠️  {pset_name}: {e}")


def write_properties(ifc, element, obj: Base):
    """
    Write Revit parameters as IFC property sets.
    Creates separate psets for Type and Instance parameters.
    """
    props_dict = _get_properties_dict(obj)
    parameters = props_dict.get("Parameters") or {}

    # Type Parameters → Pset_RevitTypeParameters
    type_params = parameters.get("Type Parameters") or {}
    type_flat = _extract_parameter_block(type_params)
    _write_pset(ifc, element, "RVT_TypeParameters", type_flat)

    # Instance Parameters → Pset_RevitInstanceParameters
    inst_params = parameters.get("Instance Parameters") or {}
    inst_flat = _extract_parameter_block(inst_params)
    _write_pset(ifc, element, "RVT_InstanceParameters", inst_flat)

    # Top-level semantic fields → Pset_RevitIdentity
    identity = {}
    for field in ["type", "family", "category", "level"]:
        val = getattr(obj, field, None)
        if val and isinstance(val, str) and val.strip():
            identity[field.capitalize()] = val.strip()
    # Also include elementId if present
    elem_id = props_dict.get("elementId")
    if elem_id:
        identity["ElementId"] = str(elem_id)

    _write_pset(ifc, element, "RVT_Identity", identity)


def write_common_properties(ifc, element, obj: Base, category_name: str = ""):
    """
    Write Pset_SpeckleData for traceability back to the Speckle source object.
    """
    props = {}
    speckle_id   = getattr(obj, "id", None)
    app_id       = getattr(obj, "applicationId", None)
    speckle_type = getattr(obj, "speckle_type", None)

    if speckle_id:   props["SpeckleId"]      = str(speckle_id)
    if app_id:       props["ApplicationId"]  = str(app_id)
    if speckle_type: props["SpeckleType"]    = str(speckle_type)
    if category_name: props["RevitCategory"] = str(category_name)

    _write_pset(ifc, element, "RVT_SpeckleData", props)