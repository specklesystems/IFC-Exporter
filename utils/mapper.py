# =============================================================================
# mapper.py
# Maps Speckle objects → IFC entity classes.
#
# Strategy (priority order):
#   1. builtInCategory (OST_ enum from properties.builtInCategory) — most reliable
#   2. category_name string (traversal context) — display name fallback
#   3. IfcBuildingElementProxy — last resort
#
# builtInCategory values: https://www.revitapidocs.com/2019/ba1c5b30-242f-5fdc-8ea9-ec3b61e6e722.htm
# =============================================================================


# --- OST_ BuiltInCategory → IFC class (primary lookup) ---
BUILTIN_CATEGORY_MAP: dict[str, str] = {
    # Architectural - Walls
    "OST_Walls":                            "IfcWall",
    "OST_CurtainWallPanels":                "IfcCurtainWall",
    "OST_CurtainWallMullions":              "IfcMember",
    "OST_Fascia":                           "IfcCovering",
    "OST_Gutters":                          "IfcPipeSegment",

    # Architectural - Floors / Roofs / Ceilings
    "OST_Floors":                           "IfcSlab",
    "OST_Roofs":                            "IfcRoof",
    "OST_Ceilings":                         "IfcCovering",
    "OST_RoofSoffit":                       "IfcCovering",

    # Architectural - Doors / Windows / Openings
    "OST_Doors":                            "IfcDoor",
    "OST_Windows":                          "IfcWindow",
    "OST_CurtainWallFamilies":              "IfcCurtainWall",
    "OST_Skylights":                        "IfcWindow",

    # Architectural - Stairs / Ramps / Railings
    "OST_Stairs":                           "IfcStair",
    "OST_StairsRailing":                    "IfcRailing",
    "OST_RailingTopRail":                   "IfcRailing",
    "OST_Ramps":                            "IfcRamp",
    "OST_StairsLandings":                   "IfcStairFlight",
    "OST_StairsRuns":                       "IfcStairFlight",
    "OST_StairsSupports":                   "IfcMember",

    # Architectural - Rooms / Spaces
    "OST_Rooms":                            "IfcSpace",
    "OST_Parking":                          "IfcSpace",
    "OST_Areas":                            "IfcSpace",

    # Architectural - Furniture / Casework
    "OST_Furniture":                        "IfcFurnishingElement",
    "OST_FurnitureSystems":                 "IfcFurnishingElement",
    "OST_Casework":                         "IfcFurnishingElement",
    "OST_SpecialtyEquipment":               "IfcFurnishingElement",
    "OST_Entourage":                        "IfcFurnishingElement",

    # Structural
    "OST_StructuralColumns":                "IfcColumn",
    "OST_Columns":                          "IfcColumn",
    "OST_StructuralFraming":                "IfcBeam",
    "OST_StructuralFoundation":             "IfcFooting",
    "OST_FoundationSlab":                   "IfcSlab",
    "OST_StructuralStiffener":              "IfcMember",
    "OST_StructuralTruss":                  "IfcMember",
    "OST_StructuralConnectionModel":        "IfcMechanicalFastener",
    "OST_StructConnections":                "IfcMechanicalFastener",
    "OST_Rebar":                            "IfcReinforcingBar",
    "OST_FabricAreas":                      "IfcReinforcingMesh",
    "OST_FabricReinforcement":              "IfcReinforcingMesh",

    # MEP - HVAC
    "OST_DuctCurves":                       "IfcDuctSegment",
    "OST_DuctFitting":                      "IfcDuctFitting",
    "OST_DuctAccessory":                    "IfcDuctSegment",
    "OST_DuctTerminal":                     "IfcAirTerminal",
    "OST_FlexDuctCurves":                   "IfcDuctSegment",
    "OST_MechanicalEquipment":              "IfcUnitaryEquipment",
    "OST_AirTerminal":                      "IfcAirTerminal",

    # MEP - Plumbing
    "OST_PipeCurves":                       "IfcPipeSegment",
    "OST_PipeFitting":                      "IfcPipeFitting",
    "OST_PipeAccessory":                    "IfcPipeSegment",
    "OST_FlexPipeCurves":                   "IfcPipeSegment",
    "OST_PlumbingFixtures":                 "IfcSanitaryTerminal",
    "OST_PlumbingEquipment":                "IfcSanitaryTerminal",
    "OST_Sprinklers":                       "IfcFireSuppressionTerminal",

    # MEP - Electrical
    "OST_ElectricalEquipment":              "IfcElectricDistributionBoard",
    "OST_ElectricalFixtures":               "IfcElectricAppliance",
    "OST_LightingFixtures":                 "IfcLightFixture",
    "OST_LightingDevices":                  "IfcLightFixture",
    "OST_CableTray":                        "IfcCableCarrierSegment",
    "OST_CableTrayFitting":                 "IfcCableCarrierFitting",
    "OST_Conduit":                          "IfcCableCarrierSegment",
    "OST_ConduitFitting":                   "IfcCableCarrierFitting",
    "OST_CommunicationDevices":             "IfcElectricAppliance",
    "OST_DataDevices":                      "IfcElectricAppliance",
    "OST_FireAlarmDevices":                 "IfcAlarm",
    "OST_SecurityDevices":                  "IfcAlarm",
    "OST_NurseCallDevices":                 "IfcElectricAppliance",

    # Site / Civil
    "OST_Site":                             "IfcSite",
    "OST_Topography":                       "IfcGeographicElement",
    "OST_Toposolid":                        "IfcGeographicElement",
    "OST_Roads":                            "IfcRoad",
    "OST_Hardscape":                        "IfcPavement",
    "OST_Planting":                         "IfcGeographicElement",
    "OST_SiteSurface":                      "IfcGeographicElement",

    # Generic / Annotation (skip or proxy)
    "OST_GenericModel":                     "IfcBuildingElementProxy",
    "OST_Mass":                             "IfcBuildingElementProxy",
    "OST_DetailComponents":                 "IfcAnnotation",
    "OST_Lines":                            "IfcAnnotation",
    "OST_Grids":                            "IfcGrid",
    "OST_Levels":                           "IfcBuildingStorey",
    "OST_Views":                            "IfcAnnotation",
}


# --- OST categories to skip entirely (analytical / energy / separation lines) ---
SKIP_CATEGORIES: set[str] = {
    "OST_MEPLoadAreaSeparationLines",
    "OST_EnergyAnalysisZones",
    "OST_EnergyAnalysisSurface",
    "OST_SolarShading",
    "OST_MEPAnalyticalPipeSegments",
    "OST_MEPAnalyticalDuctSegments",
    "OST_MEPAnalyticalSpaces",
    "OST_ElectricalConduitAnalyticalLines",
    "OST_MEPLoadBoundaryLines",
    "OST_FlowTerminalSeparationLines",
}


# --- Display category name → IFC class (secondary fallback) ---
CATEGORY_MAP: dict[str, str] = {
    "Walls":                        "IfcWall",
    "Floors":                       "IfcSlab",
    "Roofs":                        "IfcRoof",
    "Structural Columns":           "IfcColumn",
    "Columns":                      "IfcColumn",
    "Structural Framing":           "IfcBeam",
    "Beams":                        "IfcBeam",
    "Ducts":                        "IfcDuctSegment",
    "Pipes":                        "IfcPipeSegment",
    "Conduits":                     "IfcCableCarrierSegment",
    "Cable Trays":                  "IfcCableCarrierSegment",
    "Rooms":                        "IfcSpace",
    "Spaces":                       "IfcSpace",
    "Ceilings":                     "IfcCovering",
    "Stairs":                       "IfcStair",
    "Ramps":                        "IfcRamp",
    "Railings":                     "IfcRailing",
    "Top Rails":                    "IfcRailing",
    "Curtain Panels":               "IfcCurtainWall",
    "Curtain Wall Mullions":        "IfcMember",
    "Doors":                        "IfcDoor",
    "Windows":                      "IfcWindow",
    "Furniture":                    "IfcFurnishingElement",
    "Furniture Systems":            "IfcFurnishingElement",
    "Casework":                     "IfcFurnishingElement",
    "Plumbing Fixtures":            "IfcSanitaryTerminal",
    "Plumbing Equipment":           "IfcSanitaryTerminal",
    "Electrical Fixtures":          "IfcElectricAppliance",
    "Lighting Fixtures":            "IfcLightFixture",
    "Mechanical Equipment":         "IfcUnitaryEquipment",
    "Electrical Equipment":         "IfcElectricDistributionBoard",
    "Structural Rebar":             "IfcReinforcingBar",
    "Structural Connections":       "IfcMechanicalFastener",
    "Structural Foundations":       "IfcFooting",
    "Foundation Slabs":             "IfcSlab",
    "Topography":                   "IfcGeographicElement",
    "Toposolid":                    "IfcGeographicElement",
    "Planting":                     "IfcGeographicElement",
    "Site":                         "IfcSite",
    "Parking":                      "IfcSpace",
    "Generic Models":               "IfcBuildingElementProxy",
    "Mass":                         "IfcBuildingElementProxy",
    "Specialty Equipment":          "IfcFurnishingElement",
}


_bic_cache: dict[int, str | None] = {}  # id(obj) → builtInCategory


def _get_builtin_category(obj) -> str | None:
    """
    Read builtInCategory from obj.properties.builtInCategory.
    Returns the OST_ string or None. Cached per object.
    """
    oid = id(obj)
    if oid in _bic_cache:
        return _bic_cache[oid]
    result = None
    try:
        props = getattr(obj, "properties", None)
        if props is None:
            try:
                props = obj["properties"]
            except Exception:
                pass
        if props is not None:
            val = getattr(props, "builtInCategory", None)
            if val is None:
                try:
                    val = props["builtInCategory"]
                except Exception:
                    pass
            if val and isinstance(val, str):
                result = val.strip()
    except Exception:
        pass
    _bic_cache[oid] = result
    return result


# Pre-computed lowercase category map for substring matching
_CATEGORY_MAP_LOWER: list[tuple[str, str]] = [
    (k.lower(), v) for k, v in CATEGORY_MAP.items()
]

# Classification cache: (obj_id, category_name) → ifc_class
_classify_cache: dict[tuple, str] = {}


def classify(obj, category_name: str = "") -> str | None:
    """
    Determine the IFC class for a Speckle object.

    Priority:
      1. properties.builtInCategory (OST_ enum) — definitive Revit classification
      2. category_name from traversal context (display string)
      3. obj.category field
      4. IfcBuildingElementProxy fallback
    """
    cache_key = (id(obj), category_name)
    if cache_key in _classify_cache:
        return _classify_cache[cache_key]

    result = _classify_impl(obj, category_name)
    _classify_cache[cache_key] = result
    return result


def _classify_impl(obj, category_name: str) -> str | None:
    # 0. Skip analytical / energy / separation-line categories
    bic = _get_builtin_category(obj)
    if bic and bic in SKIP_CATEGORIES:
        return None

    # 1. builtInCategory — most reliable, direct Revit enum
    if bic and bic in BUILTIN_CATEGORY_MAP:
        return BUILTIN_CATEGORY_MAP[bic]

    # 2. category_name from traversal context — exact match first
    if category_name:
        if category_name in CATEGORY_MAP:
            return CATEGORY_MAP[category_name]
        cat_lower = category_name.lower()
        for key_lower, ifc_class in _CATEGORY_MAP_LOWER:
            if key_lower in cat_lower:
                return ifc_class

    # 3. obj.category field
    obj_category = getattr(obj, "category", None)
    if obj_category and isinstance(obj_category, str):
        if obj_category in CATEGORY_MAP:
            return CATEGORY_MAP[obj_category]
        obj_cat_lower = obj_category.lower()
        for key_lower, ifc_class in _CATEGORY_MAP_LOWER:
            if key_lower in obj_cat_lower:
                return ifc_class

    return "IfcBuildingElementProxy"


def reset_caches():
    """Clear module-level caches (call at start of each export run)."""
    _bic_cache.clear()
    _classify_cache.clear()
