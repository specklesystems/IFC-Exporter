from datetime import datetime

import ifcopenshell.api

import utils.config as config

from utils.materials import MaterialManager
from utils.traversal import traverse, print_tree
from utils.mapper import classify
from utils.geometry import mesh_to_ifc, get_display_instances, _make_placement
from utils.instances import is_instance, instance_to_ifc, build_definition_map, print_instance_stats
from utils.properties import write_properties, write_common_properties
from utils.writer import create_ifc_scaffold, StoreyManager


SPATIAL_STRUCTURE_TYPES = {
    "IfcSite", "IfcBuilding", "IfcBuildingStorey",
    "IfcSpace", "IfcExternalSpatialElement", "IfcSpatialZone",
    "IfcGrid", "IfcAnnotation",
}

from pydantic import Field, SecretStr
from speckle_automate import (
    AutomateBase,
    AutomationContext,
    execute_automate_function,
)

class FunctionInputs(AutomateBase):
    """These are function author-defined values.

    Automate will make sure to supply them matching the types specified here.
    Please use the pydantic model schema to define your inputs:
    https://docs.pydantic.dev/latest/usage/models/
    """
    file_name: str = Field(
        title="File Name",
        description="The name of the IFC file.",
    )
    IFC_PROJECT_NAME : str = Field(
						title="IFC Project Name",
						description="The name of the IFC project.",
		)
    IFC_SITE_NAME : str = Field(
						title="IFC Site Name",
						description="The name of the IFC site.",
		)
    IFC_BUILDING_NAME : str = Field(
						title="IFC Building Name",
						description="The name of the IFC building.",
		)


def automate_function(
    automate_context: AutomationContext,
    function_inputs: FunctionInputs,
) -> None:
    print("=" * 60)
    print("  Speckle -> IFC4.3 Exporter")
    print("=" * 60)

    #version_root_object = automate_context.receive_version()

    # ------------------------------------------------------------------ #
    # 1. Receive
    # ------------------------------------------------------------------ #
    base = automate_context.receive_version()
    scale = 1.0

    # Uncomment to debug object tree:
    # print_tree(base)

    # ------------------------------------------------------------------ #
    # 2. Build definition map (for instance resolution)
    # ------------------------------------------------------------------ #
    print("\n🔍 Building definition map...")
    definition_map = build_definition_map(base)

    # ------------------------------------------------------------------ #
    # 3. Set up IFC
    # ------------------------------------------------------------------ #
    ifc, building, body_context = create_ifc_scaffold()
    storey_manager = StoreyManager(ifc, building)

    # ------------------------------------------------------------------ #
    # 3b. Build material map from renderMaterialProxies
    # ------------------------------------------------------------------ #
    print("\n🎨 Building material map...")
    material_manager = MaterialManager(ifc, base)

    # ------------------------------------------------------------------ #
    # 4. Traverse & export
    # ------------------------------------------------------------------ #
    total           = 0
    no_geometry     = 0
    skipped_spatial = 0
    instance_count  = 0

    print(f"\n📐 Processing elements (scale={scale})...\n")

    for obj, level_name, category_name in traverse(base):

        ifc_class = classify(obj, category_name)

        if ifc_class in SPATIAL_STRUCTURE_TYPES:
            skipped_spatial += 1
            continue

        name   = getattr(obj, "name", None) or getattr(obj, "applicationId", None) or getattr(obj, "id", "unnamed")
        storey = storey_manager.get_or_create(level_name)

        # ------------------------------------------------------------------ #
        # Path A: Instance object (has transform + definitionId, no displayValue)
        # ------------------------------------------------------------------ #
        if is_instance(obj):
            rep, placement = instance_to_ifc(ifc, body_context, obj, definition_map, scale=scale, material_manager=material_manager)
            element = _create_element(ifc, ifc_class, name, rep, placement, storey)
            write_common_properties(ifc, element, obj, category_name)
            write_properties(ifc, element, obj)
            instance_count += 1
            total += 1
            if not rep:
                no_geometry += 1

        else:
            # ------------------------------------------------------------------ #
            # Path B: Normal DataObject — may have:
            #   B1. Direct mesh geometry in displayValue
            #   B2. Instance objects in displayValue (the hidden case!)
            # ------------------------------------------------------------------ #

            # B1: Mesh geometry on the parent object
            rep, placement = mesh_to_ifc(ifc, body_context, obj, scale=scale, material_manager=material_manager)
            element = _create_element(ifc, ifc_class, name, rep, placement, storey)
            write_common_properties(ifc, element, obj, category_name)
            write_properties(ifc, element, obj)
            total += 1
            if not rep:
                no_geometry += 1

            # B2: Instance objects nested inside displayValue
            # Each becomes its own IFC element (same class as parent)
            # Use the parent object's name — the InstanceProxy has no meaningful name
            nested_instances = get_display_instances(obj)
            for inst in nested_instances:
                inst_rep, inst_placement = instance_to_ifc(
                    ifc, body_context, inst, definition_map, scale=scale, material_manager=material_manager
                )
                inst_element = _create_element(
                    ifc, ifc_class, name, inst_rep, inst_placement, storey
                )
                write_common_properties(ifc, inst_element, obj, category_name)
                write_properties(ifc, inst_element, obj)
                instance_count += 1
                total += 1
                if not inst_rep:
                    no_geometry += 1

        if total % 100 == 0:
            print(f"  ... processed {total} elements")

    # ------------------------------------------------------------------ #
    # 5. Write output
    # ------------------------------------------------------------------ #
    file_name = function_inputs.file_name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    ifc_filename = f"{file_name}_{timestamp}.ifc"

    ifc.write(ifc_filename)
    automate_context.store_file_result(f"./{ifc_filename}")

    print(f"\n{'=' * 60}")
    print(f"  Export complete!")
    print(f"  Total exported     : {total}")
    print(f"  Instances          : {instance_count}")
    print(f"  Without geometry   : {no_geometry}")
    print(f"  Skipped (spatial)  : {skipped_spatial}")
    print(f"  Storeys created    : {storey_manager.count}")
    print(f"  Levels             : {', '.join(storey_manager.names)}")
    print_instance_stats()
    print(f"{'=' * 60}\n")

def _create_element(ifc, ifc_class, name, rep, placement, storey):
    """Helper: create an IFC element, assign geometry + placement + container."""
    element = ifcopenshell.api.run(
        "root.create_entity", ifc,
        ifc_class=ifc_class,
        name=str(name),
    )
    if rep and placement:
        element.Representation = ifc.createIfcProductDefinitionShape(
            Representations=(rep,)
        )
        element.ObjectPlacement = placement
    elif placement:
        element.ObjectPlacement = placement
    else:
        element.ObjectPlacement = _make_placement(ifc, 0.0, 0.0, 0.0)

    ifcopenshell.api.run(
        "spatial.assign_container", ifc,
        relating_structure=storey,
        products=[element],
    )
    return element

# make sure to call the function with the executor
if __name__ == "__main__":
    # NOTE: always pass in the automate function by its reference; do not invoke it!

    # Pass in the function reference with the inputs schema to the executor.
    execute_automate_function(automate_function, FunctionInputs)

    # If the function has no arguments, the executor can handle it like so
    # execute_automate_function(automate_function_without_inputs)
