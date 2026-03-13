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
    │   ├── Write property sets
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
| `utils/properties.py` | Writes IFC property sets from Revit parameters |
| `utils/type_manager.py` | Creates and caches IfcTypeObjects (IfcWallType, etc.) |
| `utils/materials.py` | Maps Speckle render materials to IfcSurfaceStyle colours |
| `utils/writer.py` | Creates the IFC file scaffold and manages storey creation |
| `utils/config.py` | Project/site/building name configuration |

## Mapping Logic

Classification of Speckle objects to IFC entity types follows a priority chain with three lookup tables. The first match wins.

### Priority 1: `builtInCategory` (OST_ enum)

The most reliable source. Read from `obj.properties.builtInCategory`, which contains the Revit `BuiltInCategory` enum value. This is a direct Revit classification and maps unambiguously to IFC.

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

The full table covers ~70 Revit categories across Architectural, Structural, MEP (HVAC, Plumbing, Electrical), and Site/Civil disciplines.

### Priority 2: `speckle_type` prefix

For typed Speckle objects, the `speckle_type` string is matched. Exact match is tried first, then longest-prefix match.

Examples:
| speckle_type | IFC Class |
|---|---|
| `Objects.BuiltElements.Wall` | `IfcWall` |
| `Objects.BuiltElements.Floor` | `IfcSlab` |
| `Objects.BuiltElements.Revit.RevitWall` | `IfcWall` |
| `Objects.BuiltElements.Revit.RevitColumn` | `IfcColumn` |
| `Objects.Geometry.Mesh` | `IfcBuildingElementProxy` |

### Priority 3: Category name (display string)

The category name from the traversal context (the name of the parent Collection in the Speckle tree). Exact match first, then case-insensitive substring match.

Examples:
| Category Name | IFC Class |
|---|---|
| `Walls` | `IfcWall` |
| `Structural Columns` | `IfcColumn` |
| `Plumbing Fixtures` | `IfcSanitaryTerminal` |
| `Lighting Fixtures` | `IfcLightFixture` |

### Priority 4: `obj.category` field

Same lookup as Priority 3, but using the object's own `category` attribute.

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

Performance optimisation: geometry is built once as an `IfcRepresentationMap`, then each instance references it via `IfcMappedItem` + `IfcCartesianTransformationOperator3DnonUniform`. This avoids duplicating vertex data across hundreds of identical elements (e.g. chairs, light fixtures, curtain wall panels).

## Property Sets

The exporter writes property sets matching Revit's native IFC export structure:

| Property Set | Content |
|---|---|
| `Pset_<Entity>Common` | Standard IFC properties: Reference, IsExternal, LoadBearing, ThermalTransmittance |
| `RVT_TypeParameters` | All Revit type parameters (written on the IfcTypeObject) |
| `RVT_InstanceParameters` | All Revit instance parameters |
| `RVT_Identity` | Family, Type, ElementId, BuiltInCategory |
| `Qto_<MaterialName>` | Material quantities: area, volume, density |

## Getting Started

### Prerequisites

- Python 3.11+
- A Speckle account and project with a Revit model

### Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install --upgrade pip
pip install .[dev]
```

### Running Locally

Configure your Speckle Automate credentials, then:

```bash
python main.py
```

### Deploying to Speckle Automate

1. [Create](https://automate.speckle.dev/) a new Speckle Automation
2. Select your Speckle Project and Model
3. Select this function
4. Configure the inputs (file name, project/site/building names)
5. Click Create Automation

## Function Inputs

| Input | Description |
|---|---|
| `file_name` | Output IFC filename (timestamp is appended automatically) |
| `IFC_PROJECT_NAME` | Name for the IfcProject entity |
| `IFC_SITE_NAME` | Name for the IfcSite entity |
| `IFC_BUILDING_NAME` | Name for the IfcBuilding entity |

## Resources

- [Speckle Developer Docs](https://speckle.guide/dev/python.html)
- [ifcopenshell Documentation](https://ifcopenshell.org/)
- [IFC 4.3 Schema](https://standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/)
- [Revit BuiltInCategory Reference](https://www.revitapidocs.com/2019/ba1c5b30-242f-5fdc-8ea9-ec3b61e6e722.htm)
