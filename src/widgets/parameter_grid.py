import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import *

import theme
from values import format_value_for_table, parse_single_value, parse_values_input
from widgets.cells import clear_cell_widgets
from widgets.enter_key_button import EnterKeyButton


class ParameterGrid(QTableWidget):
    """Cartesian-product grid over a list of parameters.

    Standalone: it knows nothing about where its parameters come from.
    A parameter is a dict of {"name", "values", "value_type", "label"}; the grid renders
    every combination of those values as a row, merges repeated cells, and lets
    the user edit values, reorder columns by dragging, and remove rows.

    Signals:
        gridChanged           parameters, values, column order or hidden rows changed
        rowExportRequested    the action button on a row was clicked (arg: row dict)
    """

    gridChanged = Signal()
    rowExportRequested = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.parameters = []
        self.hidden_row_keys = set()
        self.current_grid_data = []
        self.column_patterns = []
        self._refreshing_table = False
        self._reordering_columns = False

        self.verticalHeader().setDefaultSectionSize(theme.ROW_HEIGHT)
        self.itemChanged.connect(self.handle_item_changed)

        # Right-click menu to remove a parameter column.
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_table_context_menu)
        header = self.horizontalHeader()
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self.show_header_context_menu)
        header.sectionDoubleClicked.connect(self.edit_parameter_values)

        # Drag a header to reorder parameters.
        header.setSectionsMovable(True)
        header.sectionMoved.connect(self.handle_section_moved)

        self.refresh_grid()

    # --- Public API ---------------------------------------------------------

    def set_state(self, parameters, hidden_row_keys=()):
        """Replace the grid contents. Programmatic, so no gridChanged is emitted."""
        self.parameters = [
            {
                "name": p["name"],
                "values": list(p["values"]),
                "value_type": p.get("value_type", "str"),
                "label": p.get("label", ""),
            }
            for p in parameters
        ]
        self.hidden_row_keys = {self.canonical_row_key(str(k)) for k in hidden_row_keys}
        self.refresh_grid()

    def has_parameter(self, name):
        return any(p["name"] == name for p in self.parameters)

    def add_parameter(self, name, values, value_type, label=""):
        self.parameters.append({"name": name, "values": list(values),
                                "value_type": value_type, "label": label})
        self.hidden_row_keys.clear()
        self.refresh_grid()
        self.gridChanged.emit()

    def rows(self):
        """The visible rows, each a {parameter name: value} dict."""
        return [dict(row_data) for row_data in self.current_grid_data]

    # --- Context menus ------------------------------------------------------

    def show_table_context_menu(self, pos):
        row = self.rowAt(pos.y())
        col = self.columnAt(pos.x())
        self.open_remove_column_menu(self.viewport().mapToGlobal(pos), col, row)

    def show_header_context_menu(self, pos):
        col = self.horizontalHeader().logicalIndexAt(pos)
        self.open_remove_column_menu(self.horizontalHeader().mapToGlobal(pos), col)

    def open_remove_column_menu(self, global_pos, col, row=None):
        if col < 0 or col >= len(self.parameters):
            return

        menu = QMenu(self)
        edit_action = menu.addAction("Edit values")
        remove_row_action = None
        if row is not None and 0 <= row < len(self.current_grid_data):
            remove_row_action = menu.addAction("Remove this row")

        param = self.parameters[col]
        remove_action = menu.addAction(f"Remove '{param.get('label') or param['name']}'")
        selected_action = menu.exec(global_pos)

        if selected_action == edit_action:
            self.edit_parameter_values(col)
        elif selected_action == remove_row_action:
            self.remove_row(row)
        elif selected_action == remove_action:
            self.remove_parameter_column(col)

    # --- Editing -----------------------------------------------------------

    def handle_section_moved(self, logical_index, old_visual, new_visual):
        if self._refreshing_table or self._reordering_columns:
            return

        header = self.horizontalHeader()
        self._reordering_columns = True
        try:
            # Put the header back and reorder self.parameters instead, so visual
            # and logical indices stay identical everywhere else in the class.
            header.moveSection(new_visual, old_visual)
        finally:
            self._reordering_columns = False

        # The trailing "Actions" column is not a parameter: it cannot be dragged,
        # and nothing may be dropped past it.
        last = len(self.parameters) - 1
        if old_visual > last or new_visual > last or old_visual == new_visual:
            return

        self.parameters.insert(new_visual, self.parameters.pop(old_visual))
        self.refresh_grid()
        self.gridChanged.emit()

    def get_value_index_for_cell(self, col, row):
        if row is None or row < 0 or col < 0 or col >= len(self.parameters):
            return None
        if col >= len(self.column_patterns):
            return None

        pattern = self.column_patterns[col]
        row_repeat = pattern["row_repeat"]
        current_span = pattern["current_span"]
        return (row % current_span) // row_repeat

    def remove_parameter_value(self, col, value_idx):
        if value_idx is None or col < 0 or col >= len(self.parameters):
            return

        values = self.parameters[col]["values"]
        if value_idx < 0 or value_idx >= len(values):
            return

        values.pop(value_idx)
        if not values:
            self.parameters.pop(col)

        self.refresh_grid()
        self.gridChanged.emit()

    def remove_parameter_column(self, col):
        if 0 <= col < len(self.parameters):
            self.parameters.pop(col)
            self.hidden_row_keys.clear()
            self.refresh_grid()
            self.gridChanged.emit()

    def edit_parameter_values(self, col):
        if col < 0 or col >= len(self.parameters):
            return

        param = self.parameters[col]
        value_type = param.get("value_type", "str")
        if value_type == "json":
            QMessageBox.information(self, "Edit Not Supported", "Editing complex JSON values here is not supported.")
            return

        current_values = ", ".join(format_value_for_table(v) for v in param["values"])
        title = f"Edit values: {param.get('label') or param['name']}"
        prompt = "Enter comma-separated values. Numeric ranges like 1:2:9 are supported for numeric types."
        text, ok = QInputDialog.getMultiLineText(self, title, prompt, current_values)
        if not ok:
            return

        parsed_values, error = parse_values_input(text.strip(), value_type)
        if error:
            QMessageBox.warning(self, "Invalid Values", error)
            return

        if not parsed_values:
            QMessageBox.warning(self, "No Values", "At least one value is required.")
            return

        param["values"] = parsed_values
        self.hidden_row_keys.clear()
        self.refresh_grid()
        self.gridChanged.emit()

    def handle_item_changed(self, item):
        if self._refreshing_table:
            return

        row = item.row()
        col = item.column()
        if row < 0 or row >= len(self.current_grid_data) or col < 0 or col >= len(self.parameters):
            return

        row_data = self.current_grid_data[row]
        param_name = self.parameters[col]["name"]
        row_value = row_data.get(param_name)
        try:
            val_idx = self.parameters[col]["values"].index(row_value)
        except ValueError:
            return

        value_type = self.parameters[col].get("value_type", "str")
        old_value = self.parameters[col]["values"][val_idx]
        new_value, ok = parse_single_value(item.text().strip(), value_type)
        if not ok:
            self._refreshing_table = True
            try:
                item.setText(format_value_for_table(old_value))
            finally:
                self._refreshing_table = False
            QMessageBox.warning(self, "Invalid Value", f"Please enter a valid {value_type} value.")
            return

        if old_value != new_value:
            self.parameters[col]["values"][val_idx] = new_value
            self.hidden_row_keys.clear()
            self.refresh_grid()
            self.gridChanged.emit()

    def remove_row(self, row_idx):
        if row_idx < 0 or row_idx >= len(self.current_grid_data):
            return

        row_key = self.build_row_key(self.current_grid_data[row_idx])
        self.hidden_row_keys.add(row_key)
        self.refresh_grid()
        self.gridChanged.emit()

    # --- Row keys ----------------------------------------------------------

    def build_row_key(self, row_data):
        # Sorted so the key survives a column reorder: a row hidden as
        # "a=1|b=2" stays hidden when b is dragged in front of a.
        key = self.build_group_key(row_data, [p["name"] for p in self.parameters])
        return self.canonical_row_key(key)

    def canonical_row_key(self, key):
        return "|".join(sorted(key.split("|"))) if key else key

    def build_group_key(self, row_data, names):
        parts = []
        for name in names:
            if name not in row_data:
                continue
            value = row_data[name]
            try:
                serialized = json.dumps(value, sort_keys=True)
            except TypeError:
                serialized = str(value)
            parts.append(f"{name}={serialized}")
        return "|".join(parts)

    # --- Rendering ---------------------------------------------------------

    def apply_row_spans(self, visible_grid_data):
        """Merge each run of repeated values into one tall cell."""
        for col_idx in range(len(self.parameters)):
            # A cell only merges with the one below it when every column to its
            # left matches too, so a group never spans a parent's boundary.
            names = [p["name"] for p in self.parameters[:col_idx + 1]]
            start = 0
            while start < len(visible_grid_data):
                key = self.build_group_key(visible_grid_data[start], names)
                end = start + 1
                while (end < len(visible_grid_data)
                       and self.build_group_key(visible_grid_data[end], names) == key):
                    end += 1
                if end - start > 1:
                    self.setSpan(start, col_idx, end - start, 1)
                start = end

    def refresh_grid(self):
        self._refreshing_table = True
        try:
            total_rows = 1
            for p in self.parameters:
                total_rows *= len(p["values"])

            clear_cell_widgets(self, self.columnCount() - 1)
            self.clearSpans()
            self.clear()
            self.setRowCount(total_rows)
            self.setColumnCount(len(self.parameters) + 1)
            headers = [p.get("label") or p["name"] for p in self.parameters]
            self.setHorizontalHeaderLabels(headers + ["Actions"])

            full_grid_data = [{} for _ in range(total_rows)]
            self.column_patterns = []

            current_span = total_rows
            for col_idx, param in enumerate(self.parameters):
                num_vals = len(param["values"])
                row_repeat = current_span // num_vals
                self.column_patterns.append({"current_span": current_span, "row_repeat": row_repeat})

                for section in range(total_rows // current_span):
                    for val_idx, val in enumerate(param["values"]):
                        start_row = (section * current_span) + (val_idx * row_repeat)

                        for r in range(start_row, start_row + row_repeat):
                            full_grid_data[r][param['name']] = val

                current_span = row_repeat

            visible_grid_data = [
                row_data
                for row_data in full_grid_data
                if self.build_row_key(row_data) not in self.hidden_row_keys
            ]

            self.setRowCount(len(visible_grid_data))

            # Rebuild table body with only visible rows.
            for r, row_data in enumerate(visible_grid_data):
                for col_idx, param in enumerate(self.parameters):
                    val = row_data[param["name"]]
                    item = QTableWidgetItem(format_value_for_table(val))
                    item.setTextAlignment(Qt.AlignCenter)
                    if param.get("value_type") == "json":
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    self.setItem(r, col_idx, item)

            self.apply_row_spans(visible_grid_data)

            for r in range(len(visible_grid_data)):
                btn = EnterKeyButton("Generate")
                btn.setProperty("class", "cell")
                # Its font comes from the stylesheet, which is only resolved on
                # polish -- measuring before that gives the wrong width.
                btn.ensurePolished()
                node_data = visible_grid_data[r]
                btn.clicked.connect(lambda chk=False, d=dict(node_data): self.rowExportRequested.emit(d))
                self.setCellWidget(r, len(self.parameters), btn)

            self.current_grid_data = [dict(row_data) for row_data in visible_grid_data]

            # Parameter columns share the width; the action column only needs
            # to fit its button. A styled button's sizeHint can fall a few px
            # short of what its padding actually needs, which clips the label,
            # so the column is set from the button plus a margin.
            action_column = len(self.parameters)
            header = self.horizontalHeader()
            header.setSectionResizeMode(QHeaderView.Stretch)
            header.setSectionResizeMode(action_column, QHeaderView.Fixed)

            button = self.cellWidget(0, action_column)
            if button is None:
                width = self.sizeHintForColumn(action_column)
            else:
                # A styled QPushButton reports a sizeHint that does not cover
                # its QSS padding, so the label gets clipped. Measure the text.
                button.ensurePolished()
                text_width = button.fontMetrics().horizontalAdvance(button.text())
                width = text_width + 5 * theme.SPACING_SM
            self.setColumnWidth(action_column, width)
        finally:
            self._refreshing_table = False
