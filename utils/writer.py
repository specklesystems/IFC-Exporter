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


def create_ifc_scaffold(
    project_name: str = "Default Project",
    site_name: str = "Default Site",
    building_name: str = "Default Building",
) -> tuple:
    """
    Create the IFC file with the required project/site/building hierarchy.

    Returns:
        (ifc_file, site, building, body_context)
        - ifc_file:     The ifcopenshell file object
        - site:         The IfcSite entity
        - building:     The IfcBuilding entity (storeys are assigned under this)
        - body_context: The Body geometry subcontext for shape representations
    """
    ifc = ifcopenshell.file(schema="IFC4X3")

    # Project
    project = ifcopenshell.api.run(
        "root.create_entity", ifc,
        ifc_class="IfcProject",
        name=project_name,
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
        name=site_name,
    )
    building = ifcopenshell.api.run(
        "root.create_entity", ifc,
        ifc_class="IfcBuilding",
        name=building_name,
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

    return ifc, site, building, body_ctx


class StoreyManager:
    """
    Lazily creates IfcBuildingStorey entities as new level names are encountered.
    Keeps storeys in insertion order so the IFC file is logically ordered.

    Spatial containment is batched — call flush() after all elements are created
    to write all IfcRelContainedInSpatialStructure / aggregate relationships at once.
    """

    def __init__(self, ifc: ifcopenshell.file, building):
        self.ifc = ifc
        self.building = building
        self._storeys: dict[str, object] = {}  # level_name → IfcBuildingStorey
        # Batched containment: storey_id → [element, ...]
        self._contained: dict[int, list] = {}
        # Batched aggregation (IfcSite etc.): storey_id → [element, ...]
        self._aggregated: dict[int, list] = {}

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

    def queue_contain(self, storey, element):
        """Queue an element for spatial containment (batched flush)."""
        sid = storey.id()
        if sid not in self._contained:
            self._contained[sid] = []
        self._contained[sid].append(element)

    def queue_aggregate(self, storey, element):
        """Queue an element for aggregation under storey (e.g. IfcSite)."""
        sid = storey.id()
        if sid not in self._aggregated:
            self._aggregated[sid] = []
        self._aggregated[sid].append(element)

    def flush(self):
        """Write all batched spatial containment and aggregation relationships."""
        ifc = self.ifc
        for sid, elements in self._contained.items():
            storey = ifc.by_id(sid)
            ifcopenshell.api.run(
                "spatial.assign_container", ifc,
                relating_structure=storey,
                products=elements,
            )
        for sid, elements in self._aggregated.items():
            storey = ifc.by_id(sid)
            ifcopenshell.api.run(
                "aggregate.assign_object", ifc,
                relating_object=storey,
                products=elements,
            )
        self._contained.clear()
        self._aggregated.clear()

    @property
    def count(self) -> int:
        return len(self._storeys)

    @property
    def names(self) -> list[str]:
        return list(self._storeys.keys())