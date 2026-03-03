# =============================================================================
# mapper.py
# Maps Speckle speckle_type strings and Revit category names → IFC entity classes.
#
# Strategy:
#   1. Try to match speckle_type exactly or by prefix
#   2. Fall back to Revit category name (e.g. "Floors" → IfcSlab)
#   3. Fall back to IfcBuildingElementProxy if nothing matches
# =============================================================================


# --- speckle_type → IFC class ---
# Covers Objects.BuiltElements.* from the Speckle Objects kit
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

# --- Revit category name → IFC class (fallback) ---
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
    "Specialty Equipment":          "IfcBuildingElementProxy",
}


def classify(obj, category_name: str = "") -> str:
    """
    Determine the IFC class for a Speckle object.

    With the new Objects.Data.DataObject:Objects.Data.RevitObject speckle_type,
    category name is now the primary classification signal.

    Args:
        obj: A specklepy Base object (leaf element).
        category_name: The Revit category string from the traversal context
                       e.g. "Floors", "Walls", "Structural Columns"

    Returns:
        An IFC class name string e.g. "IfcWall"
    """
    speckle_type = getattr(obj, "speckle_type", "") or ""

    # 1. Category name — PRIMARY lookup for RevitObject types
    if category_name:
        # Exact match
        if category_name in CATEGORY_MAP:
            return CATEGORY_MAP[category_name]
        # Partial match handles Revit appending IDs e.g. "Structural Framing [12345]"
        for key, ifc_class in CATEGORY_MAP.items():
            if key.lower() in category_name.lower():
                return ifc_class

    # 2. Read 'category' directly off the object itself
    # Per docs: category is a TOP-LEVEL field on RevitObject, not inside properties
    obj_category = getattr(obj, "category", None)
    if obj_category and isinstance(obj_category, str):
        if obj_category in CATEGORY_MAP:
            return CATEGORY_MAP[obj_category]
        for key, ifc_class in CATEGORY_MAP.items():
            if key.lower() in obj_category.lower():
                return ifc_class

    # 3. speckle_type — fallback for non-RevitObject types (geometry, structural, etc.)
    if speckle_type in SPECKLE_TYPE_MAP:
        return SPECKLE_TYPE_MAP[speckle_type]
    for key, ifc_class in SPECKLE_TYPE_MAP.items():
        if speckle_type.startswith(key):
            return ifc_class

    # 4. Last resort
    return "IfcBuildingElementProxy"