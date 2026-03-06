# =============================================================================
# mapper.py
# Maps Speckle objects → IFC entity classes.
#
# Strategy (priority order):
#   1. builtInCategory (OST_ enum from properties.builtInCategory) — most reliable
#   2. speckle_type prefix match — for typed Speckle objects
#   3. category_name string (traversal context) — display name fallback
#   4. IfcBuildingElementProxy — last resort
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


# --- speckle_type → IFC class (secondary lookup) ---
SPECKLE_TYPE_MAP: dict[str, str] = {
    "Objects.BuiltElements.Wall":                        "IfcWall",
    "Objects.BuiltElements.Floor":                       "IfcSlab",
    "Objects.BuiltElements.Roof":                        "IfcRoof",
    "Objects.BuiltElements.Column":                      "IfcColumn",
    "Objects.BuiltElements.Beam":                        "IfcBeam",
    "Objects.BuiltElements.Brace":                       "IfcMember",
    "Objects.BuiltElements.Duct":                        "IfcDuctSegment",
    "Objects.BuiltElements.Pipe":                        "IfcPipeSegment",
    "Objects.BuiltElements.Wire":                        "IfcCableCarrierSegment",
    "Objects.BuiltElements.Opening":                     "IfcOpeningElement",
    "Objects.BuiltElements.Room":                        "IfcSpace",
    "Objects.BuiltElements.Ceiling":                     "IfcCovering",
    "Objects.BuiltElements.Stair":                       "IfcStair",
    "Objects.BuiltElements.Ramp":                        "IfcRamp",
    "Objects.BuiltElements.Foundation":                  "IfcFooting",
    "Objects.BuiltElements.Grid":                        "IfcGrid",
    "Objects.BuiltElements.Level":                       "IfcBuildingStorey",
    "Objects.BuiltElements.Revit.RevitWall":             "IfcWall",
    "Objects.BuiltElements.Revit.RevitFloor":            "IfcSlab",
    "Objects.BuiltElements.Revit.RevitRoof":             "IfcRoof",
    "Objects.BuiltElements.Revit.RevitColumn":           "IfcColumn",
    "Objects.BuiltElements.Revit.RevitBeam":             "IfcBeam",
    "Objects.BuiltElements.Revit.RevitBrace":            "IfcMember",
    "Objects.BuiltElements.Revit.RevitDuct":             "IfcDuctSegment",
    "Objects.BuiltElements.Revit.RevitPipe":             "IfcPipeSegment",
    "Objects.BuiltElements.Revit.RevitRoom":             "IfcSpace",
    "Objects.BuiltElements.Revit.RevitStair":            "IfcStair",
    "Objects.BuiltElements.Revit.RevitRailing":          "IfcRailing",
    "Objects.BuiltElements.Revit.RevitCeiling":          "IfcCovering",
    "Objects.BuiltElements.Revit.RevitTopography":       "IfcGeographicElement",
    "Objects.BuiltElements.Revit.RevitElementType":      "IfcBuildingElementProxy",
    "Objects.Geometry.Mesh":                             "IfcBuildingElementProxy",
    "Objects.Geometry.Brep":                             "IfcBuildingElementProxy",
}

# --- Display category name → IFC class (tertiary fallback) ---
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
    "Curtain Panels":               "IfcCurtainWall",
    "Curtain Wall Mullions":        "IfcMember",
    "Doors":                        "IfcDoor",
    "Windows":                      "IfcWindow",
    "Furniture":                    "IfcFurnishingElement",
    "Furniture Systems":            "IfcFurnishingElement",
    "Casework":                     "IfcFurnishingElement",
    "Plumbing Fixtures":            "IfcSanitaryTerminal",
    "Electrical Fixtures":          "IfcElectricAppliance",
    "Lighting Fixtures":            "IfcLightFixture",
    "Mechanical Equipment":         "IfcUnitaryEquipment",
    "Electrical Equipment":         "IfcElectricDistributionBoard",
    "Structural Foundations":       "IfcFooting",
    "Foundation Slabs":             "IfcSlab",
    "Topography":                   "IfcGeographicElement",
    "Site":                         "IfcSite",
    "Parking":                      "IfcSpace",
    "Generic Models":               "IfcBuildingElementProxy",
    "Mass":                         "IfcBuildingElementProxy",
    "Specialty Equipment":          "IfcFurnishingElement",
}


def _get_builtin_category(obj) -> str | None:
    """
    Read builtInCategory from obj.properties.builtInCategory.
    Returns the OST_ string or None.
    """
    try:
        props = obj["properties"] or getattr(obj, "properties", None)
        if props is None:
            return None
        if hasattr(props, "__getitem__"):
            val = props["builtInCategory"]
        else:
            val = getattr(props, "builtInCategory", None)
        if val and isinstance(val, str):
            return val.strip()
    except Exception:
        pass
    return None


def classify(obj, category_name: str = "") -> str:
    """
    Determine the IFC class for a Speckle object.

    Priority:
      1. properties.builtInCategory (OST_ enum) — definitive Revit classification
      2. speckle_type prefix match
      3. category_name from traversal context (display string)
      4. obj.category field
      5. IfcBuildingElementProxy fallback
    """
    # 1. builtInCategory — most reliable, direct Revit enum
    bic = _get_builtin_category(obj)
    if bic and bic in BUILTIN_CATEGORY_MAP:
        return BUILTIN_CATEGORY_MAP[bic]

    # 2. speckle_type
    speckle_type = getattr(obj, "speckle_type", "") or ""
    if speckle_type in SPECKLE_TYPE_MAP:
        return SPECKLE_TYPE_MAP[speckle_type]
    for key, ifc_class in SPECKLE_TYPE_MAP.items():
        if speckle_type.startswith(key):
            return ifc_class

    # 3. category_name from traversal context
    if category_name:
        if category_name in CATEGORY_MAP:
            return CATEGORY_MAP[category_name]
        for key, ifc_class in CATEGORY_MAP.items():
            if key.lower() in category_name.lower():
                return ifc_class

    # 4. obj.category field
    obj_category = getattr(obj, "category", None)
    if obj_category and isinstance(obj_category, str):
        if obj_category in CATEGORY_MAP:
            return CATEGORY_MAP[obj_category]
        for key, ifc_class in CATEGORY_MAP.items():
            if key.lower() in obj_category.lower():
                return ifc_class

    return "IfcBuildingElementProxy"