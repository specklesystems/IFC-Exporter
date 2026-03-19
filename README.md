# Speckle to IFC 4.3 Exporter

A [Speckle Automate](https://automate.speckle.dev/) function that converts Speckle BIM models (primarily from Revit) into IFC 4.3 files using [ifcopenshell](https://ifcopenshell.org/).

## What It Does

The exporter receives a Speckle model version, walks its object tree, and produces a standards-compliant IFC 4.3 file. Each Speckle object becomes an IFC element with:

- Correct IFC entity classification (IfcWall, IfcSlab, IfcColumn, etc.)
- Tessellated geometry (IfcPolygonalFaceSet)
- Material colours from Speckle render materials
- Revit property sets (Common psets, instance/type parameters, material quantities)
- IFC type objects (IfcWallType, IfcSlabType, etc.) shared across instances
- Spatial structure (IfcProject > IfcSite > IfcBuilding > IfcBuildingStorey)
- IfcSpace elements aggregated under storeys with Room properties

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
    │   ├── Classify → IFC entity class
    │   ├── Convert geometry → IfcPolygonalFaceSet
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
| `utils/geometry.py` | Converts Speckle meshes to IfcPolygonalFaceSet geometry |
| `utils/instances.py` | Handles InstanceProxy objects with shared geometry (IfcMappedItem) |
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
| `OST_LightingFixtures` | `IfcLightFixture` |
| `OST_Furniture` | `IfcFurnishingElement` |
| `OST_Rooms` | `IfcSpace` |

The full table covers ~70 Revit categories across Architectural, Structural, MEP (HVAC, Plumbing, Electrical), and Site/Civil disciplines.

### Priority 2: Category name (display string)

The category name from the traversal context (the name of the parent Collection in the Speckle tree). Exact match first, then case-insensitive substring match.

Examples:
| Category Name | IFC Class |
|---|---|
| `Walls` | `IfcWall` |
| `Structural Columns` | `IfcColumn` |
| `Plumbing Fixtures` | `IfcSanitaryTerminal` |
| `Lighting Fixtures` | `IfcLightFixture` |

### Priority 3: `obj.category` field

Same lookup as Priority 2, but using the object's own `category` attribute.

### Fallback

If none of the above match, the object is classified as `IfcBuildingElementProxy`.

## Geometry Handling

### Direct Meshes (Path B1)

Objects with `displayValue` containing Mesh objects are converted directly:

1. Extract vertices and faces from each mesh in `displayValue`
2. Scale vertices to millimetres based on the mesh's unit declaration
3. Deduplicate vertices via snap grid (0.01mm tolerance) to avoid IFC GEM111 errors
4. Build `IfcPolygonalFaceSet` with `IfcCartesianPointList3D` + `IfcIndexedPolygonalFace`
5. Compute bounding box origin for `IfcLocalPlacement`, offset vertices relative to it

### Instance Objects (Path A / B2)

Speckle `InstanceProxy` objects reference shared definition geometry via `definitionId`. The exporter supports two formats:

- **Revit format**: `definitionId` is a 64-char hex hash; geometry is found by walking the object tree
- **IFC format**: `definitionId` starts with `DEFINITION:`; geometry is in `definitionGeometry` collection

Performance optimisation: geometry is built once as an `IfcRepresentationMap`, then each instance references it via `IfcMappedItem` + `IfcCartesianTransformationOperator3DnonUniform`. This avoids duplicating vertex data across hundreds of identical elements.

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

`Qto_WallBaseQuantities`, `Qto_SlabBaseQuantities`, `Qto_ColumnBaseQuantities`, `Qto_BeamBaseQuantities`, `Qto_DoorBaseQuantities`, `Qto_WindowBaseQuantities`, `Qto_RoofBaseQuantities`, `Qto_CoveringBaseQuantities`, `Qto_RailingBaseQuantities`, `Qto_StairBaseQuantities`, `Qto_RampBaseQuantities`, `Qto_MemberBaseQuantities`, `Qto_FootingBaseQuantities`, `Qto_CurtainWallBaseQuantities`, `Qto_BuildingElementProxyBaseQuantities`

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
