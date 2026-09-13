"""Building Encore's top-level input document.

A run is described by one YAML file with three sections (docs/USERGUIDE.md):

    materials:  a registry, each loaded once and referenced by name
    devices:    mesh + regions + contacts, optional for a memc-only input
    sim:        an ordered list of solver runs

Paths inside it resolve relative to the file's own directory, so an input is
written next to -- or a known distance from -- the mesh it names.
"""

import os

import yaml


class InputError(Exception):
    """The project does not describe a runnable input."""


def build_document(materials, sims, devices=()):
    """The input as a plain dict, ready to write.

    `materials` is a list of {"name", "path"}, `devices` a list of dicts from
    build_device, and `sims` the ordered `sim:` entries. Encore builds the
    materials and devices once and shares them across every entry in `sims`,
    so a whole sweep belongs in one document.
    """
    if not sims:
        raise InputError("No tools to run. Add a tool before generating.")

    document = {}
    if materials:
        document["materials"] = [{"name": m["name"], "file": m["path"]} for m in materials]
    devices = [device for device in devices if device]
    if devices:
        document["devices"] = devices
    document["sim"] = sims
    return document


def build_device(name, mesh, volumes, contacts, materials):
    """A device entry, or None when the geometry has no materials bound to it.

    `volumes` maps a physical group name to a material name; `contacts` is the
    list of lower-dimensional physical group names. Both must match the mesh's
    own group names, which is what Encore looks them up by.
    """
    regions = [{"name": volume, "material": material}
               for volume, material in sorted(volumes.items()) if material]
    if not regions:
        return None

    used = [m for m in materials if m["name"] in {r["material"] for r in regions}]
    return {
        "name": name,
        "mesh": mesh,
        "materials": [m["name"] for m in used],
        "regions": regions,
        "contacts": [{"name": contact, "type": "ohmic"} for contact in contacts],
    }


def write_document(path, document):
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)

    try:
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(document, f, sort_keys=False, default_flow_style=None)
    except (OSError, yaml.YAMLError) as exc:
        raise InputError(f"Could not write {os.path.basename(path)}:\n{exc}")
