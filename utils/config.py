# =============================================================================
# config.py
# All user-facing settings. Edit this file before running main.py.
# =============================================================================

# --- Speckle Connection ---
SPECKLE_HOST = "app.speckle.systems"   # or your self-hosted server URL
SPECKLE_TOKEN = "40e3222fe7d82ed1796aa4ccd353f38ad098cc84dd"  # from app.speckle.systems/profile

# --- Speckle Project ---
PROJECT_ID = "d7d987146d"         # the stream/project ID from the URL
VERSION_ID = "d59178f01e"         # the specific version/commit to export

# --- IFC Output ---
OUTPUT_PATH = "output3.ifc"             # where to write the IFC file
IFC_SCHEMA = "IFC4X3"                  # IFC4X3 = IFC4.3

# --- Project Metadata (written into the IFC file) ---
IFC_PROJECT_NAME = "Speckle Export"
IFC_SITE_NAME = "Site"
IFC_BUILDING_NAME = "Building"

# --- Units ---
# Speckle unit → metres scale factor
# The exporter reads units from the root object automatically,
# but this is the fallback if units are not set on the stream.
DEFAULT_UNITS = "mm"
UNIT_SCALE = {
    "mm": 0.001,
    "cm": 0.01,
    "m":  1.0,
    "ft": 0.3048,
    "in": 0.0254,
}