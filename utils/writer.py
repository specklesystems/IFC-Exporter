# =============================================================================
# writer.py
# Creates and manages the IFC file structure:
#   IfcProject → IfcSite → IfcBuilding → IfcBuildingStorey (one per level)
#
# Also provides StoreyManager which lazily creates storeys on demand
# as the traversal encounters new level names.
# =============================================================================

import ifcopenshell
import ifcopenshell.api
import utils.config as config


def create_ifc_scaffold() -> tuple:
    """
    Create the IFC file with the required project/site/building hierarchy.

    Returns:
        (ifc_file, building, body_context)
        - ifc_file:     The ifcopenshell file object
        - building:     The IfcBuilding entity (storeys are assigned under this)
        - body_context: The Body geometry subcontext for shape representations
    """
    ifc = ifcopenshell.file(schema="IFC4X3")

    # Project
    project = ifcopenshell.api.run(
        "root.create_entity", ifc,
        ifc_class="IfcProject",
        name=config.IFC_PROJECT_NAME,
    )

    # Units — millimetres (matching Revit/Speckle source data)
    # This avoids any mm→m conversion errors and keeps coordinates at full precision
    ifcopenshell.api.run(
        "unit.assign_unit", ifc,
        length={"is_metric": True, "raw": "MILLIMETRES"},
    )

    # Geometry contexts
    model_ctx = ifcopenshell.api.run(
        "context.add_context", ifc,
        context_type="Model",
    )
    body_ctx = ifcopenshell.api.run(
        "context.add_context", ifc,
        context_type="Model",
        context_identifier="Body",
        target_view="MODEL_VIEW",
        parent=model_ctx,
    )

    # Spatial hierarchy
    site = ifcopenshell.api.run(
        "root.create_entity", ifc,
        ifc_class="IfcSite",
        name=config.IFC_SITE_NAME,
    )
    building = ifcopenshell.api.run(
        "root.create_entity", ifc,
        ifc_class="IfcBuilding",
        name=config.IFC_BUILDING_NAME,
    )

    ifcopenshell.api.run(
        "aggregate.assign_object", ifc,
        relating_object=project,
        products=[site],
    )
    ifcopenshell.api.run(
        "aggregate.assign_object", ifc,
        relating_object=site,
        products=[building],
    )

    return ifc, building, body_ctx


class StoreyManager:
    """
    Lazily creates IfcBuildingStorey entities as new level names are encountered.
    Keeps storeys in insertion order so the IFC file is logically ordered.
    """

    def __init__(self, ifc: ifcopenshell.file, building):
        self.ifc = ifc
        self.building = building
        self._storeys: dict[str, object] = {}  # level_name → IfcBuildingStorey

    def get_or_create(self, level_name: str):
        """Return existing storey or create a new one for this level name."""
        if level_name not in self._storeys:
            storey = ifcopenshell.api.run(
                "root.create_entity", self.ifc,
                ifc_class="IfcBuildingStorey",
                name=level_name,
            )
            ifcopenshell.api.run(
                "aggregate.assign_object", self.ifc,
                relating_object=self.building,
                products=[storey],
            )
            self._storeys[level_name] = storey
            print(f"  🏢 Created storey: {level_name}")

        return self._storeys[level_name]

    @property
    def count(self) -> int:
        return len(self._storeys)

    @property
    def names(self) -> list[str]:
        return list(self._storeys.keys())