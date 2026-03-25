import zipfile
from datetime import datetime

import ifcopenshell.api

from utils.materials import MaterialManager
from utils.traversal import traverse, print_tree
from utils.mapper import classify, reset_caches as reset_mapper_caches
from utils.geometry import mesh_to_ifc, get_display_instances, _make_placement
from utils.curves import curve_to_ifc
from utils.instances import is_instance, instance_to_ifc, build_definition_map, print_instance_stats, get_definition_object, reset_caches as reset_instance_caches
from utils.properties import write_properties, write_common_properties, build_element_name, get_element_tag, get_ifc_guid, reset_caches as reset_props_caches
from utils.writer import create_ifc_scaffold, StoreyManager
from utils.type_manager import TypeManager


SPATIAL_STRUCTURE_TYPES = {
    "IfcBuilding", "IfcBuildingStorey",
    "IfcExternalSpatialElement", "IfcSpatialZone",
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

    # Reset caches from any previous run
    reset_props_caches()
    reset_mapper_caches()
    reset_instance_caches()

    # ------------------------------------------------------------------ #
    # 1. Receive
    # ------------------------------------------------------------------ #
    base = automate_context.receive_version()
    scale = 1.0

    # Uncomment to debug object tree:
    # print_tree(base)

    # ------------------------------------------------------------------ #
    # 2. Build definition map (for instance resolution)
    # ----------------------------------------------
    definition_map = build_definition_map(base)

    # ------------------------------------------------------------------ #
    # 3. Set up IFC
    # ------------------------------------------------------------------ #
    ifc, _site, building, body_context = create_ifc_scaffold(
        project_name=function_inputs.IFC_PROJECT_NAME,
        site_name=function_inputs.IFC_SITE_NAME,
        building_name=function_inputs.IFC_BUILDING_NAME,
    )
    storey_manager = StoreyManager(ifc, building)

    # ------------------------------------------------------------------ #
    # 3b. Build material map from renderMaterialProxies
    # ------------------------------------------------------------------ #
    material_manager = MaterialManager(ifc, base)
    type_manager = TypeManager(ifc)

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

        if ifc_class is None:
            continue

        if ifc_class in SPATIAL_STRUCTURE_TYPES:
            skipped_spatial += 1
            continue

        # IfcSpace uses the Speckle object name (e.g. "Rooms - Live/Work Unit 507")
        # instead of Family:Type (which is "none:none" for Revit rooms)
        if ifc_class == "IfcSpace":
            name = getattr(obj, "name", None) or build_element_name(obj)
        else:
            name = build_element_name(obj)
        storey = storey_manager.get_or_create(level_name)

        # ------------------------------------------------------------------ #
        # Path A: Instance object (has transform + definitionId, no displayValue)
        # ------------------------------------------------------------------ #
        if is_instance(obj):
            # Instances may lack category info — inherit from definition object
            if ifc_class == "IfcBuildingElementProxy":
                def_obj = get_definition_object(obj, definition_map)
                if def_obj:
                    ifc_class = classify(def_obj, category_name)

            rep, placement = instance_to_ifc(ifc, body_context, obj, definition_map, scale=scale, material_manager=material_manager)
            if not rep:
                no_geometry += 1
                continue
            element = _create_element(ifc, ifc_class, name, rep, placement, storey,
                                         storey_manager=storey_manager,
                                         tag=get_element_tag(obj), guid=get_ifc_guid(obj),
                                         object_type=getattr(obj, "type", None),
)
            write_properties(ifc, element, obj, ifc_class=ifc_class, category_name=category_name)
            type_manager.assign(element, obj, ifc_class)
            instance_count += 1
            total += 1

        else:
            # ------------------------------------------------------------------ #
            # Path B: Normal DataObject — may have:
            #   B1. Direct mesh geometry in displayValue
            #   B2. Instance objects in displayValue (the hidden case!)
            # ------------------------------------------------------------------ #

            # B1: Mesh geometry on the parent object
            rep, placement = mesh_to_ifc(ifc, body_context, obj, scale=scale, material_manager=material_manager)
            if rep:
                element = _create_element(ifc, ifc_class, name, rep, placement, storey,
                                             storey_manager=storey_manager,
                                             tag=get_element_tag(obj), guid=get_ifc_guid(obj),
                                             object_type=getattr(obj, "type", None),
    )
                write_properties(ifc, element, obj, ifc_class=ifc_class, category_name=category_name)
                type_manager.assign(element, obj, ifc_class)
                total += 1

            # B2: Instance objects nested inside displayValue
            # All instances are parts of the SAME element (e.g. window frame + glass + sill)
            # Merge all into a single IFC element with combined geometry
            nested_instances = get_display_instances(obj)
            if nested_instances:
                mapped_items = []
                inst_placement = None
                for inst in nested_instances:
                    inst_rep, inst_pl = instance_to_ifc(
                        ifc, body_context, inst, definition_map, scale=scale, material_manager=material_manager
                    )
                    if inst_rep:
                        mapped_items.extend(inst_rep.Items)
                        if inst_placement is None:
                            inst_placement = inst_pl
                if mapped_items:
                    combined_rep = ifc.createIfcShapeRepresentation(
                        ContextOfItems=body_context,
                        RepresentationIdentifier="Body",
                        RepresentationType="MappedRepresentation",
                        Items=mapped_items,
                    )
                    element = _create_element(
                        ifc, ifc_class, name, combined_rep, inst_placement, storey,
                        storey_manager=storey_manager,
                        tag=get_element_tag(obj), guid=get_ifc_guid(obj),
                        object_type=getattr(obj, "type", None),
                    )
                    write_properties(ifc, element, obj, ifc_class=ifc_class, category_name=category_name)
                    type_manager.assign(element, obj, ifc_class)
                    instance_count += 1
                    total += 1

            # B3: Curve geometry (Lines, Arcs in displayValue)
            if not rep and not nested_instances:
                curve_rep, curve_placement = curve_to_ifc(ifc, body_context, obj, scale=scale, material_manager=material_manager)
                if curve_rep:
                    element = _create_element(ifc, ifc_class, name, curve_rep, curve_placement, storey,
                                                 storey_manager=storey_manager,
                                                 tag=get_element_tag(obj), guid=get_ifc_guid(obj),
                                                 object_type=getattr(obj, "type", None),
                    )
                    write_properties(ifc, element, obj, ifc_class=ifc_class, category_name=category_name)
                    type_manager.assign(element, obj, ifc_class)
                    total += 1
                else:
                    no_geometry += 1

        if total % 100 == 0:
            print(f"  ... processed {total} elements")

    # ------------------------------------------------------------------ #
    # 5. Write output
    # ------------------------------------------------------------------ #
    print("\n🔗 Flushing spatial containment...")
    storey_manager.flush()
    print("🔗 Flushing type relationships...")
    type_manager.flush()

    file_name = function_inputs.file_name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    ifc_filename = f"{file_name}_{timestamp}.ifc"

    ifc.write(ifc_filename)
    print(f"\n💾 IFC file written: {ifc_filename}")

    zip_filename = f"{file_name}_{timestamp}.zip"
    with zipfile.ZipFile(zip_filename, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(ifc_filename)
    print(f"Zipped: {zip_filename}")

    try:
        automate_context.mark_run_success("Success! You can download the IFC file below.")
        automate_context.store_file_result(f"./{zip_filename}")
    except Exception as e:
        print(f"  Could not upload file result (network issue?): {e}")
        automate_context.mark_run_failed(f"Something went wrong when storing file result. Exception detail: {e}") 

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

def _create_element(ifc, ifc_class, name, rep, placement, storey,
                    storey_manager=None,
                    tag=None, guid=None, object_type=None):
    """Helper: create an IFC element, assign geometry + placement, queue containment."""
    element = ifcopenshell.api.run("root.create_entity", ifc,
                                   ifc_class=ifc_class, name=str(name))
    if tag:
        try:
            element.Tag = str(tag)
        except AttributeError:
            pass
    if object_type:
        try:
            element.ObjectType = str(object_type)
        except AttributeError:
            pass
    if guid:
        try:
            element.GlobalId = guid
        except Exception:
            pass
    if rep and placement:
        element.Representation = ifc.createIfcProductDefinitionShape(
            Representations=(rep,)
        )
        element.ObjectPlacement = placement
    elif placement:
        element.ObjectPlacement = placement
    else:
        element.ObjectPlacement = _make_placement(ifc, 0.0, 0.0, 0.0)

    # Queue spatial assignment (batched flush at end for performance)
    # IfcSpace is a spatial structure element — must be decomposed (aggregated)
    # under its IfcBuildingStorey, not spatially contained.
    if storey_manager:
        if ifc_class in ("IfcSite", "IfcSpace"):
            storey_manager.queue_aggregate(storey, element)
        else:
            storey_manager.queue_contain(storey, element)
    return element

# make sure to call the function with the executor
if __name__ == "__main__":
    # NOTE: always pass in the automate function by its reference; do not invoke it!

    # Pass in the function reference with the inputs schema to the executor.
    execute_automate_function(automate_function, FunctionInputs)

    # If the function has no arguments, the executor can handle it like so
    # execute_automate_function(automate_function_without_inputs)
