"""Reading Gmsh .geo files and meshing them with overridden constants.

A .geo exposes a constant to the outside world through DefineConstant with an
explicit Name attribute:

    DefineConstant[ lc = {0.005, Name "Parameters/lc"} ];   # readable here
    DefineConstant[ lc = 0.005 ];                           # NOT readable
    DefineConstant[ lc = {0.005, Min 0, Max 1} ];           # gmsh rejects this

Only the first form reaches the ONELAB database this module reads. The name is
exactly what the .geo writes: gmsh prepends no "Parameters/" of its own, that
prefix is only a convention, so names are discovered here rather than guessed.

Overriding works because DefineConstant keeps a value that is already in the
database: set the constants first, then open the .geo, and the geometry is
built with the new values.

Every call runs in a throwaway subprocess. A syntax error in any .geo leaves
the gmsh parser broken for the rest of the process -- every later read, of any
file, then fails with a bogus "line 0: syntax error" -- and finalize() does not
reset it. A fresh interpreter is the only recovery, so each call gets one.
Subprocesses are spawned, so the caller must run from a real __main__ file
(a script or a frozen app, not `python -c` or a REPL).
"""

import importlib.util
import multiprocessing
import os
import sys

READ_TIMEOUT_SECONDS = 30
MESH_TIMEOUT_SECONDS = 600

# gmsh keeps its own bookkeeping in the same database as the .geo's constants.
INTERNAL_PREFIX = "Gmsh/"
INTERNAL_NAMES = {"ONELAB/Button"}

class GeoError(Exception):
    """A .geo file could not be read or meshed."""


def read_geo(path):
    """Read a .geo file.

    Returns {"path", "dimension", "constants", "physical_groups"}, where each
    constant is {"name", "value", "value_type", "read_only"} and each physical
    group is {"dimension", "tag", "name"}. Raises GeoError if the file cannot
    be read.
    """
    return _run(_read_worker, (path,), READ_TIMEOUT_SECONDS, path)


def read_constants(path):
    """The constants declared by a .geo file. See read_geo."""
    return read_geo(path)["constants"]


def generate_mesh(path, values, output_path):
    """Mesh a .geo with `values` overriding its constants, and write it out.

    `values` maps ONELAB constant names to numbers or strings; names the .geo
    does not declare are ignored by gmsh. Returns {"path", "nodes",
    "elements"}. Raises GeoError if the geometry cannot be read or meshed.
    """
    return _run(_mesh_worker, (path, dict(values), output_path), MESH_TIMEOUT_SECONDS, path)


def _run(worker, args, timeout, path):
    if not os.path.isfile(path):
        raise GeoError(f"No such file: {path}")

    if importlib.util.find_spec("gmsh") is None:
        raise GeoError(_no_gmsh_message())

    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=worker, args=args + (sender,), daemon=True)
    process.start()
    sender.close()

    try:
        if not receiver.poll(timeout):
            raise GeoError(f"Timed out after {timeout}s on {os.path.basename(path)}.")
        try:
            reply = receiver.recv()
        except EOFError:
            raise GeoError(f"gmsh stopped without finishing {os.path.basename(path)}.")
    finally:
        receiver.close()
        if process.is_alive():
            process.terminate()
        process.join()

    if not reply["ok"]:
        raise GeoError(reply["error"])
    return reply["data"]


def _read_worker(path, sender):
    _reply(sender, lambda: read_geo_here(path))


def _mesh_worker(path, values, output_path, sender):
    _reply(sender, lambda: generate_mesh_here(path, values, output_path))


def _reply(sender, work):
    try:
        sender.send({"ok": True, "data": work()})
    except Exception as exc:
        sender.send({"ok": False, "error": str(exc) or type(exc).__name__})
    finally:
        sender.close()


def read_geo_here(path):
    """Read a .geo in this process.

    Call this at most once per process: a failure here poisons gmsh's parser
    for every later call. read_geo() is the safe entry point.
    """
    import json

    with _opened(path) as gmsh:
        parameters = json.loads(gmsh.onelab.get())["onelab"]["parameters"]
        constants = [
            _as_constant(parameter)
            for parameter in parameters
            if not parameter["name"].startswith(INTERNAL_PREFIX)
            and parameter["name"] not in INTERNAL_NAMES
        ]

        physical_groups = [
            {"dimension": dim, "tag": tag, "name": gmsh.model.getPhysicalName(dim, tag)}
            for dim, tag in gmsh.model.getPhysicalGroups()
        ]

        return {
            "path": path,
            "dimension": gmsh.model.getDimension(),
            "constants": constants,
            "physical_groups": physical_groups,
        }


def generate_mesh_here(path, values, output_path):
    """Mesh a .geo in this process. Call at most once per process -- see read_geo_here."""
    with _opened(path, values) as gmsh:
        dimension = gmsh.model.getDimension()
        if dimension < 1:
            raise GeoError(f"{os.path.basename(path)} defines no geometry to mesh.")

        gmsh.model.mesh.generate(dimension)

        directory = os.path.dirname(output_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        gmsh.write(output_path)

        node_tags, _, _ = gmsh.model.mesh.getNodes()
        _, element_tags, _ = gmsh.model.mesh.getElements(dimension)
        return {
            "path": output_path,
            "nodes": len(node_tags),
            "elements": sum(len(tags) for tags in element_tags),
        }


class _opened:
    """Context manager: a gmsh session with `path` parsed and constants applied."""

    def __init__(self, path, values=None):
        self.path = path
        self.values = values or {}
        self.gmsh = None

    def __enter__(self):
        try:
            import gmsh
        except ImportError:
            raise GeoError(_no_gmsh_message())

        self.gmsh = gmsh
        gmsh.initialize()
        try:
            gmsh.option.setNumber("General.Terminal", 0)

            # DefineConstant keeps a value already in the database, so these
            # must be set before the .geo is parsed.
            for name, value in self.values.items():
                if isinstance(value, str):
                    gmsh.onelab.setString(name, [value])
                else:
                    gmsh.onelab.setNumber(name, [float(value)])

            gmsh.logger.start()
            try:
                gmsh.open(self.path)
            except Exception as exc:
                raise GeoError(_readable(str(exc), self.path))
            finally:
                log = gmsh.logger.get()
                gmsh.logger.stop()

            # A .geo can fail to parse without gmsh.open() raising, so check the log.
            for line in log:
                if line.startswith("Error:"):
                    raise GeoError(_readable(line[len("Error:"):].strip(), self.path))
        except Exception:
            gmsh.finalize()
            raise

        return gmsh

    def __exit__(self, *exc_info):
        self.gmsh.finalize()
        return False


def _as_constant(parameter):
    is_number = parameter.get("type") == "number"
    values = parameter.get("values") or [0.0 if is_number else ""]

    return {
        "name": parameter["name"],
        "value": values[0],
        "value_type": "float" if is_number else "str",
        "read_only": bool(parameter.get("readOnly", False)),
    }


def short_name(name):
    """The tail of a ONELAB name: "Parameters/lc" -> "lc"."""
    return name.split("/")[-1]


def _no_gmsh_message():
    return (
        f"gmsh is not installed for this interpreter:\n  {sys.executable}\n\n"
        f"Install it with:\n  {sys.executable} -m pip install gmsh\n\n"
        "Or run the app from the project's virtual environment, which already has it."
    )


def _readable(message, path):
    """gmsh quotes the absolute path in every message; the basename is enough."""
    return message.replace(f"'{path}'", f"'{os.path.basename(path)}'").strip()
