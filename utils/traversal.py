# =============================================================================
# traversal.py
# Walks the nested Speckle Collection tree from a Revit export.
#
# Expected structure (from your screenshot):
#   root
#   └── elements[]
#       └── Collection (project)
#           └── elements[]
#               └── Collection (Level 18, Level 19, ...)   ← storeys
#                   └── elements[]
#                       └── Collection (Floors, Walls, ...)  ← categories
#                           └── elements[]
#                               └── Base object             ← real BIM element
# =============================================================================

from typing import Generator, Tuple
from specklepy.objects.base import Base


# --------------------------------------------------------------------------- #
# Low-level helpers
# --------------------------------------------------------------------------- #

def is_collection(obj) -> bool:
    """Returns True if this object is a Speckle Collection node (not a leaf element)."""
    speckle_type = getattr(obj, "speckle_type", "") or ""
    return "Collection" in speckle_type


def get_children(obj) -> list:
    """
    Safely get the 'elements' list from a Base/Collection object.
    Handles both 'elements' and '@elements' (detached) variants.
    """
    for key in ["elements", "@elements"]:
        try:
            val = obj[key]
            if val is not None:
                return list(val)
        except Exception:
            continue
    return []


def get_prop(obj, key: str, default=None):
    """Safe property access for Speckle Base objects — avoids AttributeError."""
    try:
        val = getattr(obj, key, None)
        if val is None:
            val = obj[key]
        return val
    except Exception:
        return default


# speckle_type fragments that mark a non-exportable / spatial-structure object
_SKIP_TYPE_FRAGMENTS = {
    "Collection", "Level", "Grid", "View", "RenderMaterial",
    "Site", "Building", "Storey",
}


def _is_valid_element(obj) -> bool:
    """
    Returns True only for leaf objects that should become IFC elements.
    Filters out Collections, spatial structure types, and other non-geometry nodes.
    """
    if obj is None:
        return False

    speckle_type = getattr(obj, "speckle_type", "") or ""

    for fragment in _SKIP_TYPE_FRAGMENTS:
        if fragment in speckle_type:
            return False

    return True


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _element_level(obj) -> str:
    """
    Try to read the level/storey name directly from an element's properties.
    Handles both flat and deeply nested Revit property structures.
    """
    # Top-level field (Revit connector puts it here for parent elements)
    level = get_prop(obj, "level") or get_prop(obj, "Level")
    if level and isinstance(level, str) and level.strip():
        return level.strip()

    props = get_prop(obj, "properties")
    if isinstance(props, dict):
        # Flat key
        for key in ["Level", "level", "Building Storey"]:
            val = props.get(key)
            if val and isinstance(val, str) and val.strip():
                return val.strip()

        # Nested: properties.Instance Parameters.Constraints.Level.value
        # (used by curtain wall children / panels / mullions)
        instance_params = props.get("Instance Parameters") or {}
        constraints = instance_params.get("Constraints") or {}
        level_entry = constraints.get("Level") or {}
        if isinstance(level_entry, dict):
            val = level_entry.get("value")
            if val and isinstance(val, str) and val.strip():
                return val.strip()

        # Also check Identity Data
        identity = props.get("Identity Data") or {}
        for key in ["Level", "level"]:
            val = identity.get(key)
            if val and isinstance(val, str) and val.strip():
                return val.strip()

    return ""


def _yield_element_and_children(obj, level_name: str, category_name: str):
    """
    Yield a leaf element, then recursively yield any DataObject children
    from its elements[] list (e.g. curtain wall panels and mullions).
    Children have their own level and displayValue geometry.
    """
    yield obj, level_name, category_name

    children = get_children(obj)
    for child in children:
        if child is None or is_collection(child):
            continue
        if not _is_valid_element(child):
            continue
        # Get child's own level, fall back to parent's level
        child_level = _element_level(child) or level_name
        if child_level and child_level != "Unknown Level":
            child_category = getattr(child, "category", None) or category_name
            yield from _yield_element_and_children(child, child_level, child_category)


# --------------------------------------------------------------------------- #
# Main traversal
# --------------------------------------------------------------------------- #

def traverse(
    root: Base,
) -> Generator[Tuple[Base, str, str], None, None]:
    """
    Walk the full Speckle object tree from the root Base object.

    Yields:
        (element, level_name, category_name) for every leaf BIM element found.
        level_name   — e.g. "Level 18"
        category_name — e.g. "Floors", "Walls", "Structural Columns"
    """
    root_children = get_children(root)

    if not root_children:
        if _is_valid_element(root):
            yield root, "Unknown Level", "Unknown Category"
        return

    for child in root_children:
        if is_collection(child):
            yield from _walk_level(child)
        else:
            if _is_valid_element(child):
                level = _element_level(child)
                if level:
                    yield child, level, "Unknown Category"


def _walk_level(project_collection: Base):
    """Walk the project collection → level collections."""
    for level_obj in get_children(project_collection):
        level_name = getattr(level_obj, "name", None) or ""

        if is_collection(level_obj):
            # Only walk into this level if it has a real name
            if level_name and level_name != "Unknown Level":
                yield from _walk_category(level_obj, level_name)
        else:
            if _is_valid_element(level_obj):
                level = _element_level(level_obj) or level_name
                if level and level != "Unknown Level":
                    yield from _yield_element_and_children(level_obj, level, "Unknown Category")


def _walk_category(level_obj: Base, level_name: str):
    """Walk level collection → category collections → leaf elements."""
    for category_obj in get_children(level_obj):
        category_name = getattr(category_obj, "name", "Unknown Category") or "Unknown Category"

        if is_collection(category_obj):
            for element in get_children(category_obj):
                if is_collection(element):
                    # One extra nesting level (e.g. sub-families)
                    for sub_element in get_children(element):
                        if _is_valid_element(sub_element):
                            level = _element_level(sub_element) or level_name
                            if level and level != "Unknown Level":
                                yield from _yield_element_and_children(sub_element, level, category_name)
                else:
                    if _is_valid_element(element):
                        level = _element_level(element) or level_name
                        if level and level != "Unknown Level":
                            yield from _yield_element_and_children(element, level, category_name)
        else:
            if _is_valid_element(category_obj):
                level = _element_level(category_obj) or level_name
                if level and level != "Unknown Level":
                    yield from _yield_element_and_children(category_obj, level, "Unknown Category")


# --------------------------------------------------------------------------- #
# Debug helper
# --------------------------------------------------------------------------- #

def print_tree(obj: Base, indent: int = 0, max_depth: int = 5):
    """
    Print the object tree structure for debugging.
    Call this on the root object to understand your data before exporting.

    Usage:
        from traversal import print_tree
        print_tree(base)
    """
    if indent > max_depth:
        return

    prefix = "  " * indent
    name = getattr(obj, "name", None) or ""
    speckle_type = getattr(obj, "speckle_type", "") or ""
    children = get_children(obj)
    child_count = f"  ({len(children)} children)" if children else ""

    print(f"{prefix}├─ [{speckle_type}]  name={name!r}{child_count}")

    for child in children[:5]:  # limit to first 5 per level to avoid spam
        print_tree(child, indent + 1, max_depth)

    if len(children) > 5:
        print(f"{prefix}   ... and {len(children) - 5} more")