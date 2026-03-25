# =============================================================================
# helpers.py
# Shared utilities used across the exporter modules.
# =============================================================================


def _get(obj, key, default=None):
    """
    Safe access for specklepy Base objects, dicts, or any hybrid.
    Tries attribute access first, then bracket access.
    """
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
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


# Scale factors → MILLIMETRES (IFC file is declared as mm)
MM_SCALES = {
    "mm": 1.0,    "millimeter": 1.0,    "millimeters": 1.0,
    "cm": 10.0,   "centimeter": 10.0,   "centimeters": 10.0,
    "m":  1000.0, "meter": 1000.0,      "meters": 1000.0,
    "ft": 304.8,  "foot": 304.8,        "feet": 304.8,
    "in": 25.4,   "inch": 25.4,         "inches": 25.4,
}
