# =============================================================================
# materials.py
# Reads renderMaterialProxies from the Speckle root object and applies
# IfcSurfaceStyle colours to IFC geometry.
#
# Structure of renderMaterialProxies:
#   root.renderMaterialProxies = [
#     {
#       id: "636259b3..."
#       value: RenderMaterial {
#         name:    "Glass"
#         diffuse: -16744256   ← ARGB packed int (A=255, R=0, G=128, B=192)
#         opacity: 0.1         ← 0=transparent, 1=opaque
#       }
#       objects: ["a1a6b0c2-...", "d5dd3127-...", ...]  ← mesh applicationIds
#     },
#     ...
#   ]
#
# Usage:
#   mgr = MaterialManager(ifc, root)
#   mgr.apply_to_item(brep_item, mesh_app_id)
# =============================================================================

import ifcopenshell
import ifcopenshell.api
from specklepy.objects.base import Base


def _argb_to_rgb(argb_int: int) -> tuple[float, float, float]:
    """Unpack a signed ARGB int to normalised (R, G, B) floats 0..1."""
    unsigned = argb_int & 0xFFFFFFFF
    r = ((unsigned >> 16) & 0xFF) / 255.0
    g = ((unsigned >> 8)  & 0xFF) / 255.0
    b = (unsigned         & 0xFF) / 255.0
    return r, g, b


def _get(obj, key, default=None):
    try:
        val = getattr(obj, key, None)
        if val is not None:
            return val
    except Exception:
        pass
    try:
        val = obj[key]
        if val is not None:
            return val
    except Exception:
        pass
    return default


class MaterialManager:
    """
    Builds a lookup from mesh applicationId → IfcSurfaceStyle,
    then applies styles to IFC geometry items.
    """

    def __init__(self, ifc: ifcopenshell.file, root: Base):
        self._ifc = ifc
        # mesh applicationId (lowercase) → IfcSurfaceStyle (populated lazily)
        self._style_map: dict[str, object] = {}
        # name → IfcSurfaceStyle (cache to avoid duplicates)
        self._style_cache: dict[str, object] = {}
        self._build(root)

    def _build(self, root: Base):
        """
        Parse renderMaterialProxies and store raw material data keyed by mesh applicationId.
        IFC styles are created lazily (only when actually assigned to geometry) to avoid
        orphaned IfcSurfaceStyle instances that would fail IFC105 validation.
        """
        proxies = _get(root, "renderMaterialProxies") or []
        if not isinstance(proxies, list):
            proxies = list(proxies) if proxies else []

        # mesh applicationId (lowercase) → (name, diffuse_argb, transparency)
        self._material_data: dict[str, tuple] = {}

        for proxy in proxies:
            material = _get(proxy, "value")
            if material is None:
                continue
            name    = _get(material, "name") or "Unnamed"
            diffuse = _get(material, "diffuse")
            opacity = _get(material, "opacity")
            if diffuse is None:
                continue
            opacity_val  = float(opacity) if opacity is not None else 1.0
            transparency = max(0.0, min(1.0, 1.0 - opacity_val))

            objects = _get(proxy, "objects") or []
            for app_id in (objects if isinstance(objects, list) else []):
                if app_id:
                    self._material_data[str(app_id).lower()] = (name, int(diffuse), transparency)

        print(f"   Materials: {len(self._material_data)} mesh mappings (styles created on demand)")

    def _get_or_create_style(self, name: str, diffuse_argb: int, transparency: float):
        """Return cached style or create a new IfcSurfaceStyle."""
        cache_key = f"{name}|{diffuse_argb}|{transparency:.4f}"
        if cache_key in self._style_cache:
            return self._style_cache[cache_key]

        r, g, b = _argb_to_rgb(diffuse_argb)
        style = ifcopenshell.api.run("style.add_style", self._ifc, name=name)
        ifcopenshell.api.run(
            "style.add_surface_style",
            self._ifc,
            style=style,
            ifc_class="IfcSurfaceStyleRendering",
            attributes={
                "SurfaceColour": {"Name": None, "Red": r, "Green": g, "Blue": b},
                "Transparency": transparency,
                "ReflectanceMethod": "NOTDEFINED",
            },
        )
        self._style_cache[cache_key] = style
        return style

    def get_style(self, mesh_app_id: str):
        """Return the IfcSurfaceStyle for a mesh applicationId (created on demand), or None."""
        key = str(mesh_app_id).lower()
        # Return already-created style if cached
        if key in self._style_map:
            return self._style_map[key]
        # Create style now only if this mesh has material data
        data = self._material_data.get(key)
        if data is None:
            return None
        name, diffuse, transparency = data
        style = self._get_or_create_style(name, diffuse, transparency)
        self._style_map[key] = style
        return style

    def apply_to_item(self, item, mesh_app_id: str):
        """Assign the material style to a single IFC geometry item (e.g. IfcPolygonalFaceSet)."""
        style = self.get_style(mesh_app_id)
        if style is None:
            return
        try:
            ifcopenshell.api.run(
                "style.assign_item_style",
                self._ifc,
                item=item,
                style=style,
            )
        except Exception as e:
            pass  # Non-fatal — geometry still exports without colour