"""The workbench project: a folder, described by a YAML file inside it.

The project file records what the workbench needs to reopen a device: where the
geometry lives and where to look for materials. Everything Encore itself reads
is YAML, so this is too.
"""

import os

SUFFIX = ".encore.yaml"


class ProjectError(Exception):
    """A project could not be opened or saved."""


class Project:
    def __init__(self, path, state=None):
        self.path = path
        self.state = state or {}

    @property
    def directory(self):
        return os.path.dirname(os.path.abspath(self.path))

    @property
    def name(self):
        return os.path.basename(self.path)[: -len(SUFFIX)] or "project"

    @classmethod
    def create(cls, path):
        if not path.endswith(SUFFIX):
            path = f"{path}{SUFFIX}"

        project = cls(path, {})
        project.save()
        return project

    @classmethod
    def open(cls, path):
        import yaml

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
            raise ProjectError(f"Could not read {os.path.basename(path)}:\n{exc}")

        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ProjectError(f"{os.path.basename(path)} is not a project file.")

        return cls(path, data)

    def save(self):
        import yaml

        try:
            with open(self.path, "w", encoding="utf-8") as f:
                yaml.safe_dump(self.state, f, sort_keys=False, default_flow_style=False)
        except (OSError, yaml.YAMLError) as exc:
            raise ProjectError(f"Could not save {os.path.basename(self.path)}:\n{exc}")

    def resolve(self, path):
        """A stored path as an absolute one, relative paths being relative to
        the project file rather than to wherever the app was started."""
        if not path:
            return ""
        if os.path.isabs(path):
            return path
        return os.path.normpath(os.path.join(self.directory, path))

    def relative(self, path):
        """Store a path relative to the project when it lives alongside it, so
        the project stays portable; anything outside keeps its absolute path."""
        if not path:
            return ""

        absolute = os.path.abspath(path)
        directory = self.directory
        if absolute == directory or absolute.startswith(directory + os.sep):
            return os.path.relpath(absolute, directory)
        return absolute

    def get(self, key, default=None):
        value = self.state.get(key, default)
        return default if value is None else value

    def set(self, key, value):
        if value in ("", None):
            self.state.pop(key, None)
        else:
            self.state[key] = value
