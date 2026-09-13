"""Reading Encore material files.

Encore loads a material from a single YAML file (Material::Material). That file
is valid in either of two shapes -- the reader unwraps an optional top-level
`material:` key and then requires `parameters:`:

    material:                     parameters:
      parameters: "Params.yaml"     name: Diamond
      analytic_band:                egap: 5.51
        ebands: "ebands.yaml"       ...

so both `Silicon/Silicon.yaml` (wrapper, with band structure) and
`Diamond/Params.yaml` (parameters only) are loadable materials. `parameters:`
may be an inline node or a path relative to the file's own directory, which is
followed here to read the material's details.

The `name:` inside the parameters is not used as the material's name: Encore's
own AlN/Params.yaml says "Al2O3". The file tells us more -- Silicon.yaml is
Silicon -- falling back to the containing directory when the filename is a
generic one like Params.yaml.
"""

import os

MAX_FILE_BYTES = 1024 * 1024

# Filenames that say nothing about which material this is.
GENERIC_STEMS = {"params", "parameters", "material"}


def read_material(path):
    """Describe the material in `path`, or None if it is not a material file."""
    import yaml

    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None

    if not isinstance(data, dict):
        return None

    wrapped = isinstance(data.get("material"), dict)
    root = data["material"] if wrapped else data
    if "parameters" not in root:
        return None

    parameters = _resolve(root["parameters"], os.path.dirname(path), "parameters")
    if not isinstance(parameters, dict):
        return None

    return {
        "name": material_name(path),
        "path": path,
        "wrapped": wrapped,
        "declared_name": str(parameters.get("name", "")),
        "crystal_structure": str(parameters.get("crystal_structure", "")),
        "egap": parameters.get("egap"),
        "has_analytic_band": "analytic_band" in root,
        "has_epm": "epm" in root,
    }


def material_name(path):
    """Name a material after its file, or its folder when the file is generic."""
    absolute = os.path.abspath(path)
    stem = os.path.splitext(os.path.basename(absolute))[0]
    if stem.lower() in GENERIC_STEMS:
        return os.path.basename(os.path.dirname(absolute))
    return stem


def _resolve(node, directory, key):
    """A section may be an inline node or a path relative to its own file."""
    import yaml

    if not isinstance(node, str):
        return node

    path = node if os.path.isabs(node) else os.path.join(directory, node)
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None

    if isinstance(data, dict) and key in data:
        return data[key]
    return data
