"""Encore's simulation tools -- the entries of a top-level input's `sim:` list.

Only MEMC so far. The fields below come from MEMC/src/memc_reader.cpp rather
than the user guide, whose "Standalone Monte Carlo input" section is linked from
its contents but never written.

`nthreads` is deliberately absent: ReadNumThreads is commented out in the
reader, so the key is no longer read even though Encore's own example inputs
still carry it.

A field's `key` is its path inside the tool's YAML node, dotted for nesting.
"""

MEMC_FIELDS = [
    # key, label, type, default, required, choices, unit
    ("material", "material", "str", "", True, (), ""),
    ("sim_time.tot", "sim_time.tot", "float", 1e-10, True, (), "s"),
    ("sim_time.dt", "sim_time.dt", "float", 1e-16, True, (), "s"),
    ("efield.x", "efield x", "float", 0.0, True, (), "V/m"),
    ("efield.y", "efield y", "float", 0.0, True, (), "V/m"),
    ("efield.z", "efield z", "float", 1e5, True, (), "V/m"),
    ("bfield.x", "bfield x", "float", 0.0, True, (), "T"),
    ("bfield.y", "bfield y", "float", 0.0, True, (), "T"),
    ("bfield.z", "bfield z", "float", 0.0, True, (), "T"),
    ("electrons", "electrons", "int", 10000, True, (), ""),
    ("holes", "holes", "int", 10000, True, (), ""),
    ("output_every", "output_every", "int", 1, False, (), "steps"),
    ("subhistory", "subhistory", "int", 0, False, (), "frames"),
    ("ionize_stream", "ionize_stream", "bool", False, False, (), ""),
    # ParseBandModel also accepts FULLBAND and FULL_BAND for FULL; the two
    # canonical spellings are enough to offer.
    ("band_model.conduction", "band_model.conduction", "str", "ANALYTIC", False,
     ("ANALYTIC", "FULL"), ""),
    ("band_model.valence", "band_model.valence", "str", "ANALYTIC", False,
     ("ANALYTIC", "FULL"), ""),
    ("rate_model", "rate_model", "str", "energy", False, ("energy", "k"), ""),
]

TOOL_TYPES = {
    "memc": {
        "label": "MEMC",
        "description": "Ensemble Monte Carlo transport",
        "fields": MEMC_FIELDS,
    },
}


def fields(tool_type):
    """The tool's fields as dicts, or an empty list for an unknown type."""
    definition = TOOL_TYPES.get(tool_type)
    if not definition:
        return []

    return [
        {"key": key, "label": label, "value_type": value_type, "default": default,
         "required": required, "choices": choices, "unit": unit}
        for key, label, value_type, default, required, choices, unit in definition["fields"]
    ]


def label(tool_type):
    return TOOL_TYPES.get(tool_type, {}).get("label", tool_type)


def new_tool(tool_type, existing_names=()):
    """A tool of `tool_type`, named so it does not clash with `existing_names`."""
    base = tool_type
    name = base
    suffix = 2
    while name in existing_names:
        name = f"{base}_{suffix}"
        suffix += 1

    return {"type": tool_type, "name": name}


def variable_name(tool, key):
    """How a tool's field is addressed in the sweep table."""
    return f"{tool['name']}.{key}"


# Fields that name a material from the input's own registry, rather than
# holding a value of their own.
MATERIAL_FIELDS = {"material"}


def is_material_field(key):
    return key in MATERIAL_FIELDS


# Fields whose x/y/z parts are written as a single [x, y, z] sequence.
VECTOR_FIELDS = {"efield", "bfield"}

# Groups that must be written whole or not at all. ReadBandModel reads both
# conduction and valence unconditionally once band_model is present, and calls
# exit(EXIT_FAILURE) on the missing one -- so omitting a member that happens to
# sit at its default would produce an input that kills the run.
ALL_OR_NOTHING = {"band_model"}


def build_node(tool, values):
    """The tool's `sim:` entry: one single-key node, e.g. {"memc": {...}}.

    `values` maps field keys to the values a sweep row supplies; anything
    missing falls back to the field's default. Optional fields still sitting at
    their default are left out, so the file only says what it means to say.
    """
    body = {"name": tool["name"]}
    vectors = {}

    tool_fields = fields(tool["type"])
    written_groups = {
        field["key"].split(".")[0]
        for field in tool_fields
        if field["key"].split(".")[0] in ALL_OR_NOTHING
        and values.get(field["key"], field["default"]) != field["default"]
    }

    for field in tool_fields:
        key = field["key"]
        value = values.get(key, field["default"])
        # An optional field still at its default says nothing Encore does not
        # already assume, so it is left out of the file -- unless its group is
        # being written, in which case it has to come along.
        if (not field["required"] and value == field["default"]
                and key.split(".")[0] not in written_groups):
            continue

        parts = key.split(".")
        if parts[0] in VECTOR_FIELDS:
            vectors.setdefault(parts[0], {})[parts[1]] = value
            continue

        target = body
        for part in parts[:-1]:
            target = target.setdefault(part, {})
        target[parts[-1]] = value

    for name, axes in vectors.items():
        body[name] = [axes.get(axis, 0.0) for axis in ("x", "y", "z")]

    return {tool["type"]: body}


def defaults(tool_type):
    return {field["key"]: field["default"] for field in fields(tool_type)}


def effective_values(tool):
    """The tool's settings: its defaults, with whatever the project overrides."""
    values = defaults(tool["type"])
    stored = tool.get("values")
    if isinstance(stored, dict):
        for field in fields(tool["type"]):
            if field["key"] in stored:
                values[field["key"]] = stored[field["key"]]
    return values


def is_default(tool, key):
    stored = tool.get("values")
    return not isinstance(stored, dict) or key not in stored


def tool_values(tool, row_data):
    """The field values for one sweep row: the tool's settings, then the row."""
    values = effective_values(tool)
    for field in fields(tool["type"]):
        name = variable_name(tool, field["key"])
        if name in row_data:
            values[field["key"]] = row_data[name]
    return values


def missing_required(tool, values):
    """Required fields the project has not filled in."""
    return [field["label"] for field in fields(tool["type"])
            if field["required"] and values.get(field["key"], field["default"]) in ("", None)]


def field_by_key(tool_type, key):
    for field in fields(tool_type):
        if field["key"] == key:
            return field
    return None
