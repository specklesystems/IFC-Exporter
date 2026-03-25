# Speckle → IFC 4.3 Exporter (Revit)

## 🚧 Project Status: WIP
Hey there! This project is still under active development, so expect changes, bugs, and incomplete features.
If you have any questions or suggestions, don’t hesitate to reach out at: **nikos@speckle.systems**

A [Speckle Automate](https://docs.speckle.systems/developers/automate/introduction) function that converts Speckle models from Revit into IFC 4X3 files using [ifcopenshell](https://ifcopenshell.org/). This exporter is specifically designed for models sent to Speckle from Autodesk Revit and relies on Revit-specific object structures, categories, and parameters.

> ⚠️ **Note on Model Uploads**
>
> Large models (greater than 200MB) may fail to upload due to current file size limitations. The team is actively working on resolving this issue.

## What It Does

The exporter receives a Speckle model version, walks its object tree, and produces a standards-compliant IFC 4.3 file. Each Speckle object becomes an IFC element with:

- Correct IFC entity classification (IfcWall, IfcSlab, IfcColumn, etc.)
- Tessellated geometry (IfcPolygonalFaceSet)
- Curve geometry for Lines, Arcs, and Polycurves (IfcIndexedPolyCurve with IfcLineIndex/IfcArcIndex)
- Material colours from Speckle render materials
- Revit property sets (Common psets, instance/type parameters, material quantities)
- IFC type objects (IfcWallType, IfcSlabType, etc.) shared across instances
- Spatial structure (IfcProject > IfcSite > IfcBuilding > IfcBuildingStorey)
- IfcSpace elements aggregated under storeys with Room properties
- Automatic skipping of analytical/energy categories (e.g. Energy Analysis, MEP Analytical, Solar Shading)

## Pipeline Overview

```
Speckle Model
    │
    ▼
1. Receive version (specklepy)
    │
    ▼
2. Build definition map (for instance geometry reuse)
    │
    ▼
3. Create IFC scaffold (Project → Site → Building)
    │
    ▼
4. Traverse object tree
    │   For each leaf element:
    │   ├── Classify → IFC entity class (skip analytical categories)
    │   ├── Convert geometry → IfcPolygonalFaceSet or IfcIndexedPolyCurve
    │   ├── Create IFC element + placement
    │   ├── Write property sets & quantities
    │   └── Assign IFC type object
    │
    ▼
5. Flush spatial containment & type relationships
    │
    ▼
6. Write .ifc file
```

## Module Structure

| File | Purpose |
|------|---------|
| `main.py` | Entry point, orchestrates the full pipeline |
| `utils/traversal.py` | Walks the Speckle Collection tree (Project > Level > Category > Element) |
| `utils/mapper.py` | Classifies Speckle objects into IFC entity types |
| `utils/helpers.py` | Shared utilities (`_get` safe accessor, `MM_SCALES` unit conversion) |
| `utils/geometry.py` | Converts Speckle meshes to IfcPolygonalFaceSet geometry (handles nested BrepX) |
| `utils/curves.py` | Converts Lines, Arcs, and Polycurves to IfcIndexedPolyCurve geometry |
| `utils/instances.py` | Handles InstanceProxy objects with shared geometry (IfcMappedItem), content-based deduplication |
| `utils/properties.py` | Writes IFC property sets and quantities from Revit parameters |
| `utils/type_manager.py` | Creates and caches IfcTypeObjects (IfcWallType, etc.) |
| `utils/materials.py` | Maps Speckle render materials to IfcSurfaceStyle colours |
| `utils/writer.py` | Creates the IFC file scaffold and manages storey creation |
| `utils/receiver.py` | Connects to Speckle server and receives model data (uses `.env`) |

## Mapping Logic

Classification of Speckle objects to IFC entity types follows a priority chain. The first match wins.

### Priority 1: `builtInCategory` (OST_ enum)

The most reliable source. Read from `obj.properties.builtInCategory`, which contains the Revit `BuiltInCategory` enum value.

Examples:
| builtInCategory | IFC Class |
|---|---|
| `OST_Walls` | `IfcWall` |
| `OST_Floors` | `IfcSlab` |
| `OST_StructuralColumns` | `IfcColumn` |
| `OST_StructuralFraming` | `IfcBeam` |
| `OST_Doors` | `IfcDoor` |
| `OST_Windows` | `IfcWindow` |
| `OST_Roofs` | `IfcRoof` |
| `OST_CurtainWallPanels` | `IfcCurtainWall` |
| `OST_DuctCurves` | `IfcDuctSegment` |
| `OST_PipeCurves` | `IfcPipeSegment` |
| `OST_PipeFitting` | `IfcPipeFitting` |
| `OST_PlumbingEquipment` | `IfcSanitaryTerminal` |
| `OST_Rebar` | `IfcReinforcingBar` |
| `OST_StructConnections` | `IfcMechanicalFastener` |
| `OST_LightingFixtures` | `IfcLightFixture` |
| `OST_Furniture` | `IfcFurnishingElement` |
| `OST_Rooms` | `IfcSpace` |

The full table covers ~70 Revit categories across Architectural, Structural, MEP (HVAC, Plumbing, Electrical), and Site/Civil disciplines.

### Skipped Categories

The following analytical/energy OST categories are automatically skipped (not exported to IFC):

`OST_MEPLoadAreaSeparationLines`, `OST_EnergyAnalysisZones`, `OST_EnergyAnalysisSurface`, `OST_SolarShading`, `OST_MEPAnalyticalPipeSegments`, `OST_MEPAnalyticalDuctSegments`, `OST_MEPAnalyticalSpaces`, `OST_ElectricalConduitAnalyticalLines`, `OST_MEPLoadBoundaryLines`, `OST_FlowTerminalSeparationLines`

### Priority 2: Category name (display string)

The category name from the traversal context (the name of the parent Collection in the Speckle tree). Exact match first, then case-insensitive substring match.

Examples:
| Category Name | IFC Class |
|---|---|
| `Walls` | `IfcWall` |
| `Structural Columns` | `IfcColumn` |
| `Plumbing Fixtures` | `IfcSanitaryTerminal` |
| `Structural Rebar` | `IfcReinforcingBar` |
| `Structural Connections` | `IfcMechanicalFastener` |
| `Lighting Fixtures` | `IfcLightFixture` |

### Priority 3: `obj.category` field

Same lookup as Priority 2, but using the object's own `category` attribute.

### Fallback

If none of the above match, the object is classified as `IfcBuildingElementProxy`.

## Geometry Handling

### Direct Meshes (Path B1)

Objects with `displayValue` containing Mesh objects are converted directly:

1. Extract vertices and faces from each mesh in `displayValue` (recursively handles nested BrepX/Brep objects)
2. Scale vertices to millimetres based on the mesh's unit declaration
3. Deduplicate vertices via snap grid (0.01mm tolerance) to avoid IFC GEM111 errors
4. Round vertex coordinates to 0.001mm precision for smaller IFC file output
5. Build `IfcPolygonalFaceSet` with `IfcCartesianPointList3D` + `IfcIndexedPolygonalFace`
6. Compute bounding box origin incrementally for `IfcLocalPlacement`, offset vertices relative to it

### Instance Objects (Path A / B2)

Speckle `InstanceProxy` objects reference shared definition geometry via `definitionId`. The exporter supports two formats:

- **Revit format**: `definitionId` is a 64-char hex hash; geometry is found by walking the object tree
- **IFC format**: `definitionId` starts with `DEFINITION:`; geometry is in `definitionGeometry` collection

Performance optimisation: geometry is built once as an `IfcRepresentationMap`, then each instance references it via `IfcMappedItem` + `IfcCartesianTransformationOperator3DnonUniform`. This avoids duplicating vertex data across hundreds of identical elements. Content-based hashing further deduplicates definitions that share identical geometry.

### Curve Geometry (Path B3)

Objects whose `displayValue` contains `Objects.Geometry.Line`, `Objects.Geometry.Arc`, or `Objects.Geometry.Polycurve` items (and no meshes or instances) are exported as curve geometry using native IFC curve types:

- **Lines** → `IfcLineIndex` segments (start/end points)
- **Arcs** → `IfcArcIndex` segments (start/mid/end points)
- **Polycurves** → Mixed `IfcLineIndex` and `IfcArcIndex` segments from the polycurve's segment list (supports Line, Arc, and Polyline sub-segments)

All curves use `IfcIndexedPolyCurve` with `IfcCartesianPointList3D` for compact, deduplicated point storage. The representation uses `RepresentationType="Curve3D"`.

### Composite Objects (Path B2 — merged instances)

Objects like Windows and Doors may have multiple `InstanceProxy` items in their `displayValue` (e.g. frame, glass, sill). These are **not** separate IFC elements — all instance geometries are merged into a single `IfcShapeRepresentation` with combined `IfcMappedItem` entries, producing one IFC element per Speckle object.

## Property Sets

The exporter writes property sets matching Revit's native IFC export structure:

| Property Set | Content |
|---|---|
| `Pset_<Entity>Common` | Standard IFC properties: Reference, IsExternal, LoadBearing, ThermalTransmittance |
| `Pset_SpaceCommon` | Room-specific: Reference, RoomNumber, RoomName, Category (Occupant) |
| `RVT_InstanceParameters` | All Revit instance parameters |
| `RVT_Identity` | Family, Type, ElementId, BuiltInCategory |

## Quantities

Quantities follow the IFC standard naming convention: `Qto_<EntityType>BaseQuantities` and `Qto_<MaterialName>BaseQuantities`.

| Quantity Set | Content |
|---|---|
| `Qto_<EntityType>BaseQuantities` | Element-level quantities from Revit computed parameters (area, volume, length, width, height, perimeter) |
| `Qto_SpaceBaseQuantities` | Room quantities: NetFloorArea, NetVolume |
| `Qto_<MaterialName>BaseQuantities` | Per-material quantities: GrossArea, GrossVolume, Density |

### Element Quantity Mapping

| IFC Quantity | Revit Parameter(s) |
|---|---|
| GrossArea | `HOST_AREA_COMPUTED` |
| GrossVolume | `HOST_VOLUME_COMPUTED` |
| Length | `CURVE_ELEM_LENGTH`, `INSTANCE_LENGTH_PARAM` |
| Height | `WALL_USER_HEIGHT_PARAM`, `FAMILY_HEIGHT_PARAM`, `INSTANCE_HEAD_HEIGHT_PARAM` |
| Width | `INSTANCE_WIDTH_PARAM`, `FURNITURE_WIDTH`, `FLOOR_ATTR_THICKNESS_PARAM` |
| Perimeter | `HOST_PERIMETER_COMPUTED` |

### Supported Entity Qto Sets

`Qto_WallBaseQuantities`, `Qto_SlabBaseQuantities`, `Qto_ColumnBaseQuantities`, `Qto_BeamBaseQuantities`, `Qto_DoorBaseQuantities`, `Qto_WindowBaseQuantities`, `Qto_RoofBaseQuantities`, `Qto_CoveringBaseQuantities`, `Qto_RailingBaseQuantities`, `Qto_StairBaseQuantities`, `Qto_RampBaseQuantities`, `Qto_MemberBaseQuantities`, `Qto_FootingBaseQuantities`, `Qto_CurtainWallBaseQuantities`, `Qto_BuildingElementProxyBaseQuantities`, `Qto_PipeFittingBaseQuantities`, `Qto_SanitaryTerminalBaseQuantities`, `Qto_ReinforcingElementBaseQuantities`, `Qto_MechanicalFastenerBaseQuantities`

## IfcSpace (Rooms)

Revit Rooms (`OST_Rooms`) are exported as `IfcSpace` elements with special handling:

- **Spatial relationship**: Aggregated under `IfcBuildingStorey` via `IfcRelAggregates` (not contained)
- **Naming**: Uses the Speckle object `name` attribute (not Family:Type which is "none:none" for rooms)
- **IfcSpace.Name**: Set to `ROOM_NUMBER`
- **IfcSpace.LongName**: Set to `ROOM_NAME`
- **Geometry**: Converted from `displayValue` meshes like any other element

## Function Inputs

| Input | Description |
|---|---|
| `file_name` | Output IFC filename (timestamp is appended automatically) |
| `IFC_PROJECT_NAME` | Name for the IfcProject entity |
| `IFC_SITE_NAME` | Name for the IfcSite entity |
| `IFC_BUILDING_NAME` | Name for the IfcBuilding entity |

## Environment Variables

For local testing via `receiver.py`, configure a `.env` file:

| Variable | Description |
|---|---|
| `SPECKLE_SERVER_URL` | Speckle server URL (default: `https://app.speckle.systems`) |
| `SPECKLE_TOKEN` | Personal access token for authentication |
| `SPECKLE_PROJECT_ID` | Project (stream) ID |

## Testing

| Model Name                      | Revit Size | IFC Size | Conversion Time |
|----------------------------------|------------|----------|-----------------|
| Huge confidential model         | 450 MB     | 391 MB   | 2h 30m          |
| Snowdon Towers (Architecture)   | 93.2 MB    | 118 MB   | 8m 37s          |
| Speckle Tower                   | 51 MB      | 45 MB    | 3m              |
| Rac Basic Sample Model          | 18.8 MB    | 12 MB    | 12s             |

## Resources

- [Speckle Developer Docs](https://speckle.guide/dev/python.html)
- [ifcopenshell Documentation](https://ifcopenshell.org/)
- [IFC 4.3 Schema](https://standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/)
- [Revit BuiltInCategory Reference](https://www.revitapidocs.com/2019/ba1c5b30-242f-5fdc-8ea9-ec3b61e6e722.htm)
