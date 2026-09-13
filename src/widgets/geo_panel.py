import os

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import *

import geo
import theme
from values import format_value_for_table
from widgets.cells import FittedTable, clear_cell_widgets, fit_columns, fit_height
from widgets.enter_key_button import EnterKeyButton


SELECT_TEXT = "Select .geo"


class GeoPanel(QWidget):
    """Picks a Gmsh .geo file: its constants, and a material per physical volume.

    "Volume" means a physical group of the model's own dimension -- the groups
    that carry material, as opposed to the lower-dimensional ones that are
    contacts. These become Encore's regions.

    Standalone: give it nothing and it manages its own file dialog, or call
    load(path) to open one directly.

    Signals:
        geometryLoaded       a .geo was read (arg: the dict from geo.read_geo)
        assignmentsChanged   a volume's material was picked (arg: {volume: material})
    """

    geometryLoaded = Signal(object)
    assignmentsChanged = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.geo_data = None
        self.material_names = []
        self.assignments = {}
        self._filling = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, theme.SPACING_XS)
        layout.setSpacing(theme.SPACING_SM)

        # The button doubles as the label for the loaded file.
        self.select_btn = EnterKeyButton(SELECT_TEXT)
        self.select_btn.clicked.connect(self.select_geo_file)

        self.table = FittedTable()
        self.table.setColumnCount(2)
        self.table.setHorizontalHeaderLabels(["Constant", "Value"])
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # Nothing acts on a selection here, so a clicked cell should not stay lit.
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.verticalHeader().setVisible(False)
        # No vertical header, so the first column header is the top-left corner.
        self.table.setProperty("cornerless", True)
        self.table.setProperty("textcells", True)
        self.table.verticalHeader().setDefaultSectionSize(theme.ROW_HEIGHT)

        self.volume_table = FittedTable()
        self.volume_table.setColumnCount(2)
        self.volume_table.setHorizontalHeaderLabels(["Volume", "Material"])
        self.volume_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.volume_table.setSelectionMode(QAbstractItemView.NoSelection)
        self.volume_table.verticalHeader().setVisible(False)
        # No vertical header, so the first column header is the top-left corner.
        self.volume_table.setProperty("cornerless", True)
        self.volume_table.verticalHeader().setDefaultSectionSize(theme.ROW_HEIGHT)
        # Columns are sized to their contents by fit_columns after filling.

        # A table with nothing in it is not shown at all; the placeholder
        # beside it explains what would go there.
        self.constants_label = QLabel()
        self.volumes_label = QLabel()
        constants_area = self.table_area(self.constants_label, self.table)
        volumes_area = self.table_area(self.volumes_label, self.volume_table)

        layout.addWidget(self.select_btn)
        layout.addWidget(constants_area)
        layout.addWidget(volumes_area)

        self.show_constants([])
        self.show_volumes()

    def table_area(self, label, table):
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignLeft | Qt.AlignTop)

        area = QWidget()
        layout = QVBoxLayout(area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.SPACING_XS)
        layout.addWidget(label)
        layout.addWidget(table)
        return area

    def select_geo_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select Geometry", "", "Gmsh Geometry (*.geo)")
        if not path:
            return

        self.load(path)

    def load(self, path, show_errors=True):
        try:
            geo_data = geo.read_geo(path)
        except geo.GeoError as exc:
            if show_errors:
                QMessageBox.warning(self, "Invalid Geometry", f"Could not read this .geo file:\n\n{exc}")
            return False

        self.geo_data = geo_data
        self.select_btn.setText(os.path.basename(path))
        self.select_btn.setToolTip(f"{path}\n\nClick to choose a different .geo")
        self.show_constants(geo_data["constants"])

        # A new geometry has its own volumes; old assignments do not apply.
        self.assignments = {}
        self.show_volumes()

        self.geometryLoaded.emit(geo_data)
        return True

    def show_constants(self, constants):
        self.table.setVisible(bool(constants))
        self.constants_label.setVisible(not constants)
        if not constants:
            self.table.setRowCount(0)
            if self.geo_data is None:
                self.constants_label.setText("Select a .geo file to list its constants.")
            else:
                self.constants_label.setText(
                    "No named constants. Only DefineConstant entries carrying a "
                    'Name attribute are visible, e.g. DefineConstant[ lc = '
                    '{0.005, Name "Parameters/lc"} ];'
                )
            return

        self.table.setRowCount(len(constants))
        for row, constant in enumerate(constants):
            self.table.setItem(row, 0, self.name_item(constant))
            self.table.setItem(row, 1, QTableWidgetItem(format_value_for_table(constant["value"])))

        fit_columns(self.table)
        fit_height(self.table)

    def name_item(self, constant):
        # "Parameters/" is the conventional gmsh prefix and adds nothing here.
        item = QTableWidgetItem(geo.short_name(constant["name"]))
        tooltip = [constant["name"]]
        if constant["read_only"]:
            tooltip.append("read-only")
        item.setToolTip("\n".join(tooltip))
        return item

    # --- Volumes ------------------------------------------------------------

    def volumes(self):
        """The physical groups that carry material: those of the model's dimension."""
        if self.geo_data is None:
            return []

        dimension = self.geo_data["dimension"]
        return [group for group in self.geo_data["physical_groups"]
                if group["dimension"] == dimension and group["name"]]

    def set_materials(self, names):
        """Offer these material names in every volume's dropdown."""
        self.material_names = list(names)

        # Drop assignments whose material is no longer available.
        self.assignments = {volume: material
                            for volume, material in self.assignments.items()
                            if material in self.material_names}
        self.show_volumes()

    def set_assignments(self, assignments, announce=False):
        self.assignments = {str(k): str(v) for k, v in (assignments or {}).items()
                            if v in self.material_names}
        self.show_volumes()
        if announce:
            self.assignmentsChanged.emit(dict(self.assignments))

    def show_volumes(self):
        volumes = self.volumes()
        self._filling = True
        try:
            clear_cell_widgets(self.volume_table, 1)
            self.volume_table.setRowCount(0)
            self.volume_table.setVisible(bool(volumes))
            self.volumes_label.setVisible(not volumes)
            if not volumes:
                self.volumes_label.setText(
                    "Select a .geo file to list its volumes."
                    if self.geo_data is None else
                    "This geometry declares no physical group of its own "
                    "dimension, so it has no volume to give a material to.")
                return

            self.volume_table.setRowCount(len(volumes))
            for row, volume in enumerate(volumes):
                name = volume["name"]
                item = QTableWidgetItem(name)
                item.setToolTip(f"physical group {volume['tag']}, dimension {volume['dimension']}")
                self.volume_table.setItem(row, 0, item)

                combo = QComboBox()
                combo.addItem("")
                combo.addItems(self.material_names)
                chosen = self.assignments.get(name, "")
                combo.setCurrentIndex(combo.findText(chosen) if chosen else 0)
                combo.currentTextChanged.connect(
                    lambda material, volume_name=name: self.on_material_picked(volume_name, material))
                self.volume_table.setCellWidget(row, 1, combo)
        finally:
            self._filling = False

        fit_columns(self.volume_table)
        fit_height(self.volume_table)

    def on_material_picked(self, volume, material):
        if self._filling:
            return

        if material:
            self.assignments[volume] = material
        else:
            self.assignments.pop(volume, None)
        self.assignmentsChanged.emit(dict(self.assignments))
