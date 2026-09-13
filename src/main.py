import multiprocessing
import os
import sys

from PySide6.QtCore import QSettings, Qt, QTimer
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import *

import encore_input
import geo
import theme
import tools
from project import Project, ProjectError, SUFFIX as PROJECT_SUFFIX
from values import format_value_for_table, parse_values_input
from widgets import (CollapsibleSection, EnterKeyButton, GeoPanel, MaterialsPanel,
                     ParameterGrid, ToolPanel)


class Workbench(QMainWindow):
    """Builds an Encore device: geometry, materials, and mesh sweeps over them."""

    def __init__(self):
        super().__init__()
        self.geo_data = None
        self.variables = {}
        self.tools = []
        self.project = None
        self._loading_project = False

        # Menus are held on the window: the menu bar does not keep the Python
        # side alive, and a collected menu takes its actions with it.
        self.project_menu = self.menuBar().addMenu("Project")
        self.project_menu.addAction("New Project...", self.new_project)
        self.project_menu.addAction("Open Project...", self.open_project)

        # Between Project and View; build_theme_menu adds View after this.
        self.tool_menu = self.menuBar().addMenu("Tool")
        self.add_menu = self.tool_menu.addMenu("Add")
        for tool_type, definition in tools.TOOL_TYPES.items():
            action = self.add_menu.addAction(definition["label"])
            action.setStatusTip(definition["description"])
            action.triggered.connect(lambda checked=False, t=tool_type: self.add_tool(t))

        # Filled in by rebuild_tool_sections, since it lists the tools by name.
        self.remove_menu = self.tool_menu.addMenu("Remove")
        self.rebuild_remove_menu()

        self.settings = QSettings("Encore", "Workbench")
        self.theme_name = self.settings.value("theme", theme.SYSTEM_THEME)
        self.build_theme_menu()
        self.apply_theme()

        # While following the desktop, track its light/dark changes live.
        QApplication.instance().styleHints().colorSchemeChanged.connect(self.apply_theme)
        self.update_title()

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        layout = QVBoxLayout(self.central_widget)
        layout.setContentsMargins(theme.SPACING_MD, theme.SPACING_MD,
                                  theme.SPACING_MD, theme.SPACING_MD)
        layout.setSpacing(theme.SPACING_MD)

        # Sweep bar: pick a constant, give it values, add it as a column.
        input_layout = QHBoxLayout()
        input_layout.setSpacing(theme.SPACING_SM)
        self.variable_input = QComboBox()
        self.variable_input.setEnabled(False)
        self.variable_input.setMinimumWidth(200)
        self.variable_input.setPlaceholderText("Variable")
        self.variable_input.currentIndexChanged.connect(self.on_variable_selection_changed)
        self.values_input = QLineEdit(
            placeholderText="Values (0.01, 0.02 or 0.01:0.01:0.05 or logspace(1e3,1e5,10))")
        self.values_input.setMinimumWidth(260)
        self.values_input.setEnabled(False)
        self.add_btn = EnterKeyButton("Add Variable")
        self.add_btn.clicked.connect(self.add_variable)
        self.add_btn.setEnabled(False)
        self.generate_btn = EnterKeyButton("Generate all")
        self.generate_btn.clicked.connect(self.generate_all_meshes)
        self.generate_btn.setEnabled(False)
        # The one action that produces output, so the only filled button.
        self.generate_btn.setProperty("class", "primary")
        input_layout.addWidget(QLabel("Sweep"))
        input_layout.addWidget(self.variable_input)
        input_layout.addWidget(self.values_input, 1)
        input_layout.addWidget(self.add_btn)
        input_layout.addWidget(self.generate_btn)

        # Project column: materials above geometry.
        self.materials_panel = MaterialsPanel()
        self.materials_panel.materialsChanged.connect(self.on_materials_changed)
        self.geo_panel = GeoPanel()
        self.geo_panel.geometryLoaded.connect(self.on_geometry_loaded)
        self.geo_panel.assignmentsChanged.connect(self.on_assignments_changed)

        self.materials_section = CollapsibleSection("Materials", self.materials_panel)
        self.geo_section = CollapsibleSection("Geometry", self.geo_panel)
        self.tool_sections = []

        # Each section takes exactly the height its content needs; the trailing
        # stretch absorbs the rest, so folding one leaves the others in place
        # and nothing floats in the middle of the column.
        self.column_layout = QVBoxLayout()
        self.column_layout.setContentsMargins(0, 0, 0, 0)
        self.column_layout.setSpacing(theme.SPACING_MD)
        self.column_layout.addWidget(self.materials_section)
        self.column_layout.addWidget(self.geo_section)
        self.column_layout.addStretch(1)
        self.sections = [self.materials_section, self.geo_section]

        column = QWidget()
        column.setLayout(self.column_layout)

        # The column is as tall as its content, so it scrolls rather than
        # squeezing the tables when there is more than the window can show.
        self.project_column = QScrollArea()
        self.project_column.setWidget(column)
        self.project_column.setWidgetResizable(True)
        self.project_column.setFrameShape(QFrame.NoFrame)
        self.project_column.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        project_column = self.project_column

        self.grid = ParameterGrid()
        self.grid.rowExportRequested.connect(self.generate_row_mesh)
        self.grid.gridChanged.connect(self.on_grid_changed)

        sweep_column = QWidget()
        sweep_layout = QVBoxLayout(sweep_column)
        sweep_layout.setContentsMargins(0, 0, 0, 0)
        sweep_layout.setSpacing(theme.SPACING_SM)
        sweep_layout.addLayout(input_layout)
        sweep_layout.addWidget(self.grid)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(project_column)
        splitter.addWidget(sweep_column)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        # The tool panels carry long setting names, so the column starts wider.
        splitter.setSizes([380, 620])
        layout.addWidget(splitter)

        self.resize(1100, 620)

    # --- Project -----------------------------------------------------------

    def new_project(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Create Project", f"project{PROJECT_SUFFIX}",
            f"Encore Project (*{PROJECT_SUFFIX})")
        if not path:
            return

        try:
            self.project = Project.create(path)
        except ProjectError as exc:
            QMessageBox.warning(self, "Create Failed", str(exc))
            return

        self.update_title()
        # A new project keeps whatever is already loaded, so record it.
        self.save_project()

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Project", "", f"Encore Project (*{PROJECT_SUFFIX})")
        if not path:
            return

        self.open_project_file(path)

    def open_project_file(self, path):
        try:
            project = Project.open(path)
        except ProjectError as exc:
            QMessageBox.warning(self, "Open Failed", str(exc))
            return False

        self.project = project
        self.update_title()

        # Restoring triggers the same signals as editing, so hold off saving
        # until everything is back -- otherwise loading the geometry would
        # write its empty assignments over the ones still to be restored.
        self._loading_project = True
        try:
            materials = [project.resolve(path) for path in project.get("materials", [])]
            self.materials_panel.set_paths(materials, show_errors=False)
            self.set_tools(project.get("tools", []))
            self.geo_panel.set_materials(self.material_names())

            geometry = project.resolve(project.get("geometry", ""))
            if geometry and self.geo_panel.load(geometry, show_errors=False):
                # Loading clears assignments and the sweep, so both are put
                # back afterwards.
                self.geo_panel.set_assignments(project.get("volumes", {}))
            self.refresh_variables()
            self.restore_sweep(project)
        finally:
            self._loading_project = False

        return True

    def save_project(self):
        if self.project is None or self._loading_project:
            return

        self.project.set("geometry",
                         self.project.relative(self.geo_data["path"]) if self.geo_data else "")
        self.project.set("materials",
                         [self.project.relative(path) for path in self.materials_panel.paths()])
        self.project.set("volumes", dict(self.geo_panel.assignments))
        self.project.set("tools", self.tools)
        self.project.set("sweep", self.grid.parameters)
        self.project.set("hidden_rows", sorted(self.grid.hidden_row_keys))
        try:
            self.project.save()
        except ProjectError as exc:
            QMessageBox.warning(self, "Save Failed", str(exc))

    def set_tools(self, saved):
        """Replace the tool list from a project. No signal: this is the load."""
        self.tools = [
            {"type": tool["type"],
             "name": str(tool.get("name") or tool["type"]),
             "values": dict(tool.get("values") or {})}
            for tool in saved
            if isinstance(tool, dict) and tool.get("type") in tools.TOOL_TYPES
        ]
        self.rebuild_tool_sections()

    def restore_sweep(self, project):
        """Put back the saved sweep columns, minus any the geometry has lost."""
        saved = project.get("sweep", [])
        if not isinstance(saved, list):
            return

        known = set(self.variables)
        kept, dropped = [], []
        for param in saved:
            if not isinstance(param, dict) or not param.get("name") or not param.get("values"):
                continue
            (kept if param["name"] in known else dropped).append(param)

        self.grid.set_state(kept, project.get("hidden_rows", []))

        if dropped:
            # Keeping them would be worse than dropping them: gmsh ignores a
            # constant the .geo does not declare, so every row of that sweep
            # would quietly mesh identically.
            names = ", ".join(param.get("label") or param["name"] for param in dropped)
            QMessageBox.information(
                self, "Sweep Variables Dropped",
                "These are no longer offered by the geometry or the project's "
                f"tools, so they were removed from the sweep:\n\n{names}")

    def build_theme_menu(self):
        themes = theme.available_themes()
        self.view_menu = self.menuBar().addMenu("View")
        self.theme_menu = self.view_menu.addMenu("Theme")
        theme_menu = self.theme_menu

        group = QActionGroup(self)
        group.setExclusive(True)

        # System first, then the two built-ins, then everything Omarchy offers.
        names = [theme.SYSTEM_THEME, "Light", "Dark"]
        names += [name for name in sorted(themes) if name not in names]

        for index, name in enumerate(names):
            if index == 3:
                theme_menu.addSeparator()
            action = theme_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(name == self.theme_name)
            action.setData(name)
            group.addAction(action)
            action.triggered.connect(lambda checked=False, chosen=name: self.choose_theme(chosen))

    def choose_theme(self, name):
        self.theme_name = name
        self.settings.setValue("theme", name)
        self.apply_theme()

    def apply_theme(self, *_):
        theme.apply(QApplication.instance(), self.theme_name)

    def update_title(self):
        name = self.project.name if self.project else "no project"
        self.setWindowTitle(f"Encore Workbench - {name}")

    def on_grid_changed(self):
        self.mark_swept_fields()
        self.save_project()

    def material_names(self):
        return [material["name"] for material in self.materials_panel.materials]

    def on_materials_changed(self, paths):
        self.geo_panel.set_materials(self.material_names())
        for section in self.tool_sections:
            section.content.set_materials(self.material_names())
        self.schedule_column_fit()
        self.save_project()

    def on_assignments_changed(self, assignments):
        self.save_project()

    # --- Geometry ----------------------------------------------------------

    def on_geometry_loaded(self, geo_data):
        self.geo_data = geo_data
        self.geo_panel.set_materials(self.material_names())

        # A new geometry has its own constants, so previous sweep columns are
        # meaningless: start the table over.
        self.grid.set_state([])

        self.refresh_variables()
        self.schedule_column_fit()
        self.save_project()

    def refresh_variables(self):
        """Rebuild what the sweep table can vary: geometry constants and tool settings."""
        self.variables = {}

        if self.geo_data:
            for constant in self.geo_data["constants"]:
                if constant["read_only"]:
                    continue
                self.variables[constant["name"]] = {
                    "label": geo.short_name(constant["name"]),
                    "value": constant["value"],
                    "value_type": constant["value_type"],
                    "geometry": True,
                }

        for tool in self.tools:
            for field in tools.fields(tool["type"]):
                self.variables[tools.variable_name(tool, field["key"])] = {
                    "label": f"{tool['name']}.{field['key']}",
                    "value": field["default"],
                    "value_type": field["value_type"],
                    "geometry": False,
                }

        current = self.variable_input.currentData()
        self.variable_input.blockSignals(True)
        self.variable_input.clear()
        for name, variable in self.variables.items():
            self.variable_input.addItem(variable["label"], name)
        if current in self.variables:
            self.variable_input.setCurrentIndex(self.variable_input.findData(current))
        self.variable_input.blockSignals(False)

        enabled = bool(self.variables)
        self.variable_input.setEnabled(enabled)
        self.values_input.setEnabled(enabled)
        self.add_btn.setEnabled(enabled)
        # A geometry is optional -- a memc run needs no device -- but there is
        # nothing to write without a tool.
        self.generate_btn.setEnabled(bool(self.tools))
        self.on_variable_selection_changed()

    def geometry_names(self):
        return {name for name, variable in self.variables.items() if variable["geometry"]}

    def schedule_column_fit(self):
        """Refit once the tables have worked out their own widths."""
        QTimer.singleShot(0, self.fit_column_width)

    def fit_column_width(self):
        """Ask the splitter for the width the panels need.

        A QScrollArea's own hint ignores its contents, so without this the
        column collapses to something narrower than the tables inside it and
        clips them -- there is no horizontal scrollbar to fall back on.
        """
        inner = self.project_column.widget()
        width = inner.minimumSizeHint().width() + 2
        if self.project_column.verticalScrollBar().isVisible():
            width += self.project_column.verticalScrollBar().sizeHint().width()
        self.project_column.setMinimumWidth(min(width, 480))

    def add_tool(self, tool_type):
        self.tools.append(tools.new_tool(tool_type, [t["name"] for t in self.tools]))
        self.on_tools_changed()

    def remove_tool(self, tool):
        if tool in self.tools:
            self.tools.remove(tool)
            self.on_tools_changed()

    def on_tools_changed(self):
        self.rebuild_tool_sections()
        self.refresh_variables()
        self.prune_sweep()
        self.save_project()

    def rename_tool(self, tool, new_name):
        """Rename a tool, carrying its sweep columns with it.

        The name is the prefix of every variable the tool offers, so a rename
        that did not migrate them would leave the columns pointing at a tool
        that no longer exists, and prune_sweep would quietly drop them.
        """
        old_name = tool["name"]
        new_name = self.sanitize_name_token(new_name) if new_name else ""

        if not new_name or new_name == old_name:
            self.rebuild_tool_sections()
            return

        if new_name in [other["name"] for other in self.tools if other is not tool]:
            QMessageBox.warning(self, "Name Taken",
                                f"Another tool is already called '{new_name}'.")
            self.rebuild_tool_sections()
            return

        tool["name"] = new_name
        old_prefix, new_prefix = f"{old_name}.", f"{new_name}."

        parameters = []
        for param in self.grid.parameters:
            if param["name"].startswith(old_prefix):
                key = param["name"][len(old_prefix):]
                param = dict(param, name=new_prefix + key, label=new_prefix + key)
            parameters.append(param)
        hidden = {key.replace(old_prefix, new_prefix) for key in self.grid.hidden_row_keys}
        self.grid.set_state(parameters, hidden)

        self.rebuild_tool_sections()
        self.refresh_variables()
        self.save_project()

    def on_tool_values_changed(self, tool):
        self.refresh_variables()
        self.save_project()

    def rebuild_tool_sections(self):
        """One collapsible section per tool, after the fixed two."""
        for section in self.tool_sections:
            self.column_layout.removeWidget(section)
            section.setParent(None)
            section.deleteLater()
        self.tool_sections = []

        for tool in self.tools:
            panel = ToolPanel(tool)
            panel.set_materials(self.material_names())
            panel.valuesChanged.connect(self.on_tool_values_changed)
            panel.renameRequested.connect(self.rename_tool)
            panel.removeRequested.connect(self.remove_tool)
            section = CollapsibleSection(f"{tools.label(tool['type'])}: {tool['name']}", panel)
            # Before the trailing stretch, which keeps the column packed up top.
            self.column_layout.insertWidget(self.column_layout.count() - 1, section)
            self.tool_sections.append(section)

        self.sections = [self.materials_section, self.geo_section] + self.tool_sections
        self.rebuild_remove_menu()
        self.mark_swept_fields()
        self.schedule_column_fit()

    def rebuild_remove_menu(self):
        self.remove_menu.clear()
        self.remove_menu.setEnabled(bool(self.tools))
        for tool in self.tools:
            action = self.remove_menu.addAction(tool["name"])
            action.triggered.connect(lambda checked=False, t=tool: self.remove_tool(t))

    def mark_swept_fields(self):
        """Tell each tool panel which of its fields the sweep table varies."""
        swept = {param["name"] for param in self.grid.parameters}
        for tool, section in zip(self.tools, self.tool_sections):
            keys = [field["key"] for field in tools.fields(tool["type"])
                    if tools.variable_name(tool, field["key"]) in swept]
            section.content.set_swept(keys)

    def prune_sweep(self):
        """Drop sweep columns whose variable no longer exists.

        Removing a tool takes its settings with it; leaving the column would
        sweep a name nothing answers to.
        """
        kept = [param for param in self.grid.parameters if param["name"] in self.variables]
        if len(kept) != len(self.grid.parameters):
            self.grid.set_state(kept, self.grid.hidden_row_keys)

    def constant(self, name):
        return self.variables.get(name)

    def on_variable_selection_changed(self, index=None):
        constant = self.variables.get(self.variable_input.currentData())
        if constant is None:
            self.values_input.setPlaceholderText("Values")
            return

        current = format_value_for_table(constant["value"])
        if constant["value_type"] in ("int", "float"):
            self.values_input.setPlaceholderText(
                f"Values (default {current}; a, b or start:step:end or logspace(start,end,count))")
        else:
            self.values_input.setPlaceholderText(f"Values (default {current})")

    # --- Sweep -------------------------------------------------------------

    def add_variable(self):
        name = self.variable_input.currentData()
        constant = self.variables.get(name)
        if constant is None:
            return

        if self.grid.has_parameter(name):
            QMessageBox.information(self, "Already Added",
                                    f"'{constant['label']}' is already in the table.")
            return

        value_type = constant["value_type"]
        typed_text = self.values_input.text().strip()
        if typed_text:
            values, error = parse_values_input(typed_text, value_type)
            if error:
                QMessageBox.warning(self, "Invalid Values", error)
                return
            if not values:
                QMessageBox.warning(self, "No Values", "At least one value is required.")
                return
        else:
            # No input means sweep the value the .geo already declares.
            values = [constant["value"]]

        self.values_input.clear()
        self.grid.add_parameter(name, values, value_type, label=constant["label"])

    # --- Meshing -----------------------------------------------------------

    def geometry_values(self, row_data):
        """Only the geometry's own constants: a tool's settings mean nothing to gmsh."""
        names = self.geometry_names()
        return {name: value for name, value in row_data.items() if name in names}

    def contact_names(self):
        """Physical groups below the model's dimension: the device's contacts."""
        if self.geo_data is None:
            return []

        dimension = self.geo_data["dimension"]
        return [group["name"] for group in self.geo_data["physical_groups"]
                if group["dimension"] < dimension and group["name"]]

    def device_name(self):
        return os.path.splitext(os.path.basename(self.geo_data["path"]))[0] if self.geo_data else "device"

    def varying_parameters(self):
        """Sweep columns with more than one value.

        A column pinned to a single value is the same in every row, so naming
        anything after it only adds noise.
        """
        return [param for param in self.grid.parameters if len(param["values"]) > 1]

    def row_label(self, row_data):
        """What distinguishes this row from the others, for naming its output."""
        parts = []
        for param in self.varying_parameters():
            name = param["name"]
            if name in row_data:
                parts.append(f"{param.get('label') or name}"
                             f"{format_value_for_table(row_data[name])}")
        return "_".join(self.sanitize_name_token(part) for part in parts if part)

    def sim_nodes(self, row_data, suffix=""):
        """The `sim:` entries for one row, or raise if a tool is underspecified."""
        nodes = []
        for tool in self.tools:
            values = tools.tool_values(tool, row_data)
            missing = tools.missing_required(tool, values)
            if missing:
                raise encore_input.InputError(
                    f"{tool['name']} has no value for: {', '.join(missing)}.\n\n"
                    "Add each as a sweep variable to give it one.")

            node = tools.build_node(tool, values)
            if suffix:
                # Encore names each run's output after this, so every entry in
                # the sweep needs its own; it nests output the way Encore's own
                # bias and efield sweeps do.
                body = node[tool["type"]]
                body["name"] = f"{tool['name']}/{suffix}"
            nodes.append(node)
        return nodes

    def build_input(self, row_data, mesh_path, input_path):
        """The input document for one row, naming the mesh relative to itself."""
        materials = [{"name": m["name"], "path": m["path"]}
                     for m in self.materials_panel.materials]

        device = None
        if mesh_path:
            device = encore_input.build_device(
                self.device_name(),
                os.path.relpath(mesh_path, os.path.dirname(input_path)),
                self.geo_panel.assignments, self.contact_names(), materials)

        return encore_input.build_document(materials, self.sim_nodes(row_data), [device])

    def builds_device(self):
        """Whether this project describes a device at all.

        A memc run works off a material and fields; it needs no mesh, so a
        project with no geometry, or none bound to a material, still generates.
        """
        return bool(self.geo_data and self.geo_panel.assignments)

    def write_run(self, row_data, directory, mesh_path):
        """Write one row's input, and its mesh if there is a device."""
        result = None
        if self.builds_device():
            result = geo.generate_mesh(self.geo_data["path"],
                                       self.geometry_values(row_data), mesh_path)
        else:
            mesh_path = None

        input_path = os.path.join(directory, "encore.yaml")
        encore_input.write_document(input_path, self.build_input(row_data, mesh_path, input_path))
        return result

    def generate_row_mesh(self, row_data):
        if not self.tools:
            QMessageBox.information(self, "No Tools", "Add a tool before generating.")
            return

        directory = QFileDialog.getExistingDirectory(self, "Select Run Folder")
        if not directory:
            return

        try:
            result = self.write_run(row_data, directory, os.path.join(directory, "mesh.msh"))
        except (geo.GeoError, encore_input.InputError) as exc:
            QMessageBox.warning(self, "Generate Failed", str(exc))
            return

        written = "encore.yaml and mesh.msh" if result else "encore.yaml"
        detail = f"\n{result['nodes']} nodes, {result['elements']} elements" if result else ""
        QMessageBox.information(self, "Run Written", f"{directory}\n\n{written}{detail}")

    def generate_all_meshes(self):
        rows = self.grid.rows()
        if not rows:
            QMessageBox.information(self, "No Rows", "There are no sweep points to run.")
            return

        if not self.tools:
            QMessageBox.information(self, "No Tools", "Add a tool before generating.")
            return

        directory = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if not directory:
            return

        progress = QProgressDialog(f"Meshing {len(rows)} sweep points...", "Cancel",
                                   0, len(rows), self)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)

        materials = [{"name": m["name"], "path": m["path"]}
                     for m in self.materials_panel.materials]

        # One document for the whole sweep: Encore builds the materials and
        # devices once and runs every `sim:` entry against them. Rows differing
        # only in a tool's settings share a mesh, and so share a device.
        # With no geometry, or none bound to a material, there is no device to
        # build and nothing would reference a mesh, so none is written.
        with_device = self.builds_device()

        devices = {}
        sims = []
        try:
            for index, row_data in enumerate(rows):
                progress.setValue(index)
                progress.setLabelText(f"Sweep point {index + 1} of {len(rows)}...")
                QApplication.processEvents()
                if progress.wasCanceled():
                    return

                mesh_name = self.mesh_name(row_data)
                if with_device and mesh_name not in devices:
                    mesh_path = os.path.join(directory, "meshes", f"{mesh_name}.msh")
                    geo.generate_mesh(self.geo_data["path"],
                                      self.geometry_values(row_data), mesh_path)
                    devices[mesh_name] = encore_input.build_device(
                        self.device_name() if len(rows) == 1 else mesh_name,
                        os.path.relpath(mesh_path, directory),
                        self.geo_panel.assignments, self.contact_names(), materials)

                sims.extend(self.sim_nodes(row_data, suffix=self.row_label(row_data)))

            document = encore_input.build_document(materials, sims, devices.values())
            encore_input.write_document(os.path.join(directory, "encore.yaml"), document)
        except (geo.GeoError, encore_input.InputError) as exc:
            progress.close()
            QMessageBox.warning(self, "Generate Failed", str(exc))
            return
        finally:
            progress.setValue(len(rows))

        meshes = len(devices)
        QMessageBox.information(
            self, "Input Written",
            f"encore.yaml with {len(sims)} sim entr{'y' if len(sims) == 1 else 'ies'}"
            f"{f' and {meshes} mesh(es)' if meshes else ''} under\n{directory}")

    def unique_mesh_path(self, directory, base_name):
        candidate = os.path.join(directory, f"{base_name}.msh")
        if not os.path.exists(candidate):
            return candidate

        idx = 2
        while True:
            candidate = os.path.join(directory, f"{base_name}_{idx}.msh")
            if not os.path.exists(candidate):
                return candidate
            idx += 1

    def run_name(self, row_data):
        parts = [self.device_name()]
        for param in self.grid.parameters:
            name = param["name"]
            if name in row_data:
                parts.append(f"{param.get('label') or name}{format_value_for_table(row_data[name])}")

        return "_".join(self.sanitize_name_token(part) for part in parts if part)

    def mesh_name(self, row_data):
        if self.geo_data is None:
            return ""

        parts = [os.path.splitext(os.path.basename(self.geo_data["path"]))[0]]
        names = self.geometry_names()
        for param in self.varying_parameters():
            name = param["name"]
            if name in row_data and name in names:
                value_text = format_value_for_table(row_data[name])
                parts.append(f"{geo.short_name(name)}{value_text}")

        return "_".join(self.sanitize_name_token(part) for part in parts if part)

    def sanitize_name_token(self, token):
        allowed = "-_."
        cleaned = []
        for ch in str(token):
            if ch.isalnum() or ch in allowed:
                cleaned.append(ch)
            else:
                cleaned.append("-")

        sanitized = "".join(cleaned).strip("-_")
        return sanitized or "value"


def main(argv):
    # geo calls spawn a child process; PyInstaller builds need this first.
    multiprocessing.freeze_support()

    app = QApplication(argv)
    theme.load_fonts()
    window = Workbench()
    window.show()

    # An optional project to open: encore [project.encore.yaml]
    arguments = [a for a in argv[1:] if not a.startswith("-")]
    if arguments:
        window.open_project_file(os.path.abspath(arguments[0]))

    return app.exec()


if __name__ == "__main__":
    sys.exit(main(sys.argv))
