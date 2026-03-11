# =============================================================================
# type_manager.py
# Creates and caches IfcTypeObjects (IfcWallType, IfcRoofType, etc.) and
# links element instances to them via IfcRelDefinesByType.
#
# Revit native IFC export pattern:
#   IfcWallType
#     Name = "Family:TypeName"            (no ElementId)
#     Tag  = Type's Revit ElementId       (from Instance Parameters > Other > Type Id)
#     GlobalId = from Type IfcGUID param  (from Type Parameters > IFC Parameters > Type IfcGUID)
#     HasPropertySets:
#       Pset_WallCommon:  IsExternal, ThermalTransmittance  (type-level)
#       Pset_EnvironmentalImpactIndicators: Reference = TypeName
#       RVT_TypeParameters: all remaining type params
#
# Type objects are SHARED — multiple instances of the same Revit type
# map to one IfcTypeObject, keyed by (ifc_class, family, type_name).
# =============================================================================

import ifcopenshell
import ifcopenshell.api
from specklepy.objects.base import Base
from utils.properties import (
    _get_props_dict, _get_nested, _param_value, _make_prop, _write_pset,
    _safe_get, _to_dict,
    COMMON_PSET, EXTERNAL_CATEGORIES, _flatten_params
)


# IFC element class → IFC type class
TYPE_CLASS_MAP: dict[str, str] = {
    "IfcWall":                  "IfcWallType",
    "IfcWallStandardCase":      "IfcWallType",
    "IfcSlab":                  "IfcSlabType",
    "IfcRoof":                  "IfcRoofType",
    "IfcColumn":                "IfcColumnType",
    "IfcBeam":                  "IfcBeamType",
    "IfcMember":                "IfcMemberType",
    "IfcDoor":                  "IfcDoorType",
    "IfcWindow":                "IfcWindowType",
    "IfcStair":                 "IfcStairType",
    "IfcStairFlight":           "IfcStairFlightType",
    "IfcRamp":                  "IfcRampType",
    "IfcRailing":               "IfcRailingType",
    "IfcCovering":              "IfcCoveringType",
    "IfcCurtainWall":           "IfcCurtainWallType",
    "IfcFooting":               "IfcFootingType",
    "IfcBuildingElementProxy":  "IfcBuildingElementProxyType",
    "IfcFurnishingElement":     "IfcFurnitureType",
    "IfcLightFixture":          "IfcLightFixtureType",
    "IfcElectricAppliance":     "IfcElectricApplianceType",
    "IfcElectricDistributionBoard": "IfcElectricDistributionBoardType",
    "IfcSanitaryTerminal":      "IfcSanitaryTerminalType",
    "IfcUnitaryEquipment":      "IfcUnitaryEquipmentType",
    "IfcDuctSegment":           "IfcDuctSegmentType",
    "IfcPipeSegment":           "IfcPipeSegmentType",
    "IfcCableCarrierSegment":   "IfcCableCarrierSegmentType",
    "IfcPlate":                 "IfcPlateType",
}


class TypeManager:
    """
    Creates IfcTypeObjects on demand and caches them by (ifc_class, family, type_name).
    Call assign(element, obj, ifc_class) for each exported element.
    """

    def __init__(self, ifc: ifcopenshell.file):
        self._ifc = ifc
        # key: (ifc_class, family, type_name) → IfcTypeObject
        self._cache: dict[tuple, object] = {}
        # type_object → [element, ...]  (for batched IfcRelDefinesByType)
        self._pending: dict[int, list] = {}

    def assign(self, element, obj: Base, ifc_class: str):
        """Create (or retrieve cached) type object and queue the assignment."""
        type_class = TYPE_CLASS_MAP.get(ifc_class)
        if not type_class:
            return

        family    = getattr(obj, "family", None) or ""
        type_name = getattr(obj, "type",   None) or ""
        if not type_name:
            return

        cache_key = (ifc_class, family, type_name)

        if cache_key not in self._cache:
            type_obj = self._create_type(type_class, family, type_name, obj, ifc_class)
            self._cache[cache_key] = type_obj

        type_obj = self._cache[cache_key]
        type_id  = type_obj.id()

        if type_id not in self._pending:
            self._pending[type_id] = []
        self._pending[type_id].append(element)

    def flush(self):
        """Write all IfcRelDefinesByType relationships."""
        for type_id, elements in self._pending.items():
            type_obj = self._ifc.by_id(type_id)
            ifcopenshell.api.run(
                "type.assign_type", self._ifc,
                related_objects=elements,
                relating_type=type_obj,
            )
        self._pending.clear()
        print(f"   Type objects created: {len(self._cache)}")

    # -----------------------------------------------------------------------
    def _create_type(self, type_class: str, family: str, type_name: str,
                     obj: Base, ifc_class: str):
        """Instantiate the IfcTypeObject with name, tag, GlobalId, and psets."""
        props       = _get_props_dict(obj)
        params      = _safe_get(props, "Parameters", {})
        type_params = _safe_get(params, "Type Parameters", {})
        inst_params = _safe_get(params, "Instance Parameters", {})

        # Name: "Family:TypeName" (no ElementId)
        name_parts = [p for p in [family, type_name] if p]
        name = ":".join(name_parts)

        # Tag: Type's Revit ElementId
        type_id_entry = _get_nested(inst_params, "Other", "Type Id")
        type_id_d = _to_dict(type_id_entry)
        tag = str(type_id_d.get("value")) if type_id_d.get("value") else None

        # GlobalId: from Type IfcGUID parameter
        type_guid_entry = _get_nested(type_params, "IFC Parameters", "Type IfcGUID")
        type_guid_d = _to_dict(type_guid_entry)
        guid = type_guid_d.get("value") if type_guid_d else None

        # Create type entity
        type_obj = ifcopenshell.api.run(
            "root.create_entity", self._ifc,
            ifc_class=type_class,
            name=name,
        )
        if tag:
            try:
                type_obj.Tag = str(tag)
            except Exception:
                pass
        if guid:
            try:
                type_obj.GlobalId = str(guid)
            except Exception:
                pass

        # Write type-level property sets
        self._write_type_psets(type_obj, obj, ifc_class, type_name, props,
                               type_params, inst_params)
        return type_obj

    def _write_type_psets(self, type_obj, obj, ifc_class, type_name,
                          props, type_params, inst_params):
        """Write psets on the type object (type-level parameters only)."""
        ifc = self._ifc
        pset_name = COMMON_PSET.get(ifc_class)

        # ── Standard Common pset on the type ──────────────────────────────
        if pset_name:
            type_ifc_props = []

            # IsExternal (type-level)
            bic = _safe_get(props, "builtInCategory", "")
            is_external = bic in EXTERNAL_CATEGORIES
            if ifc_class not in {"IfcSpace", "IfcSite", "IfcBuildingStorey",
                                  "IfcBuilding", "IfcFurnishingElement", "IfcOpeningElement"}:
                p = _make_prop(ifc, "IsExternal", "IfcBoolean", is_external)
                if p:
                    type_ifc_props.append(p)

            # ThermalTransmittance (from type parameters)
            if ifc_class in {"IfcWall", "IfcWallStandardCase", "IfcRoof",
                              "IfcSlab", "IfcDoor", "IfcWindow"}:
                u_val = _param_value(type_params,
                                     "ANALYTICAL_HEAT_TRANSFER_COEFFICIENT")
                if u_val is not None:
                    try:
                        p = _make_prop(ifc, "ThermalTransmittance",
                                       "IfcThermalTransmittanceMeasure", float(u_val))
                        if p:
                            type_ifc_props.append(p)
                    except Exception:
                        pass

            # LoadBearing (from type parameters)
            if ifc_class in {"IfcWall", "IfcWallStandardCase", "IfcColumn",
                              "IfcBeam", "IfcSlab"}:
                lb_val = _param_value(type_params, "WALL_STRUCTURAL_SIGNIFICANT")
                if lb_val is not None:
                    p = _make_prop(ifc, "LoadBearing", "IfcBoolean", bool(lb_val))
                    if p:
                        type_ifc_props.append(p)

            if type_ifc_props:
                _write_pset(ifc, type_obj, pset_name, type_ifc_props)

        # ── RVT_TypeParameters — all type-level Revit params ──────────────
        type_flat = _flatten_params(type_params)
        if type_flat:
            type_str_props = []
            for name_p, val in type_flat.items():
                try:
                    nominal = ifc.create_entity("IfcLabel", wrappedValue=val)
                    prop = ifc.create_entity("IfcPropertySingleValue",
                                             Name=name_p, NominalValue=nominal)
                    type_str_props.append(prop)
                except Exception:
                    pass
            if type_str_props:
                _write_pset(ifc, type_obj, "RVT_TypeParameters", type_str_props)