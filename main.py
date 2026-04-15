import sys
import json
import os
import copy
import math
from PySide6.QtWidgets import *
from PySide6.QtCore import Qt, QTimer


class EnterKeyButton(QPushButton):
    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.click()
            event.accept()
            return
        super().keyPressEvent(event)

class WorkbenchGrid(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Encore")
        self.parameters = []
        self.source_json = None
        self.source_path = ""
        self.field_paths = {}
        self.current_grid_data = []
        self.hidden_row_keys = set()
        self.project_path = ""
        self._loading_project = False
        self._refreshing_table = False
        self.column_patterns = []

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        layout = QVBoxLayout(self.central_widget)

        # Input Area
        input_layout = QHBoxLayout()
        self.file_label = QLabel("No JSON selected")
        self.file_label.setMinimumWidth(220)
        file_btn = EnterKeyButton("Select JSON")
        file_btn.clicked.connect(self.select_json_file)
        self.parameter_input = QComboBox()
        self.parameter_input.setEnabled(False)
        self.parameter_input.setMinimumWidth(180)
        self.parameter_input.setPlaceholderText("Parameter")
        self.parameter_input.currentTextChanged.connect(self.on_parameter_selection_changed)
        self.values_input = QLineEdit(placeholderText="Values (300, 400 or 1:2:10)")
        self.values_input.setMinimumWidth(240)
        add_btn = EnterKeyButton("Add Parameter")
        add_btn.clicked.connect(self.add_parameter)
        print_all_btn = EnterKeyButton("Print all")
        print_all_btn.clicked.connect(self.export_all_rows)
        input_layout.addWidget(file_btn)
        input_layout.addWidget(self.file_label, 1)
        input_layout.addWidget(self.parameter_input)
        input_layout.addWidget(self.values_input)
        input_layout.addWidget(add_btn)
        input_layout.addWidget(print_all_btn)
        layout.addLayout(input_layout)

        # Table
        self.table = QTableWidget()
        layout.addWidget(self.table)
        self.table.itemChanged.connect(self.handle_item_changed)

        # Right-click menu to remove a parameter column.
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_table_context_menu)
        header = self.table.horizontalHeader()
        header.setContextMenuPolicy(Qt.CustomContextMenu)
        header.customContextMenuRequested.connect(self.show_header_context_menu)
        header.sectionDoubleClicked.connect(self.edit_parameter_values)

        # Force project selection at startup.
        QTimer.singleShot(0, self.ensure_project_selected)

    def ensure_project_selected(self):
        if self.project_path:
            return

        while not self.project_path:
            choice = QMessageBox(self)
            choice.setWindowTitle("Project")
            choice.setText("Create a new project or open an existing project to continue.")
            create_btn = choice.addButton("Create New Project", QMessageBox.AcceptRole)
            open_btn = choice.addButton("Open Project", QMessageBox.ActionRole)
            cancel_btn = choice.addButton("Cancel", QMessageBox.RejectRole)
            choice.exec()

            clicked = choice.clickedButton()
            if clicked == create_btn:
                if self.create_new_project():
                    break
            elif clicked == open_btn:
                if self.open_project():
                    break
            elif clicked == cancel_btn:
                self.close()
                return

    def create_new_project(self):
        path, _ = QFileDialog.getSaveFileName(self, "Create Project", "", "Encore Project (*.encore.json)")
        if not path:
            return False

        if not path.endswith(".encore.json"):
            path = f"{path}.encore.json"

        self.project_path = path
        self.source_json = None
        self.source_path = ""
        self.parameters = []
        self.hidden_row_keys.clear()
        self.file_label.setText("No JSON selected")
        self.parameter_input.clear()
        self.parameter_input.setEnabled(False)
        self.refresh_grid()
        self.save_project_state()
        return True

    def open_project(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open Project", "", "Encore Project (*.encore.json)")
        if not path:
            return False

        return self.load_project(path)

    def load_project(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            QMessageBox.warning(self, "Open Failed", f"Could not open project:\n{exc}")
            return False

        if not isinstance(data, dict):
            QMessageBox.warning(self, "Invalid Project", "Project file must contain a JSON object.")
            return False

        self._loading_project = True
        try:
            self.project_path = path
            template_path = data.get("template_path", "")

            self.source_json = None
            self.source_path = ""
            self.file_label.setText("No JSON selected")
            self.parameter_input.clear()
            self.parameter_input.setEnabled(False)

            if template_path:
                self.load_template_from_path(template_path, reset_parameters=False, show_errors=True)

            loaded_parameters = data.get("parameters", [])
            self.parameters = []
            if isinstance(loaded_parameters, list):
                for param in loaded_parameters:
                    if not isinstance(param, dict):
                        continue
                    name = str(param.get("name", "")).strip()
                    values = param.get("values", [])
                    value_type = param.get("value_type", "str")
                    if name and isinstance(values, list) and values:
                        self.parameters.append({"name": name, "values": values, "value_type": value_type})

            hidden = data.get("hidden_row_keys", [])
            if isinstance(hidden, list):
                self.hidden_row_keys = {str(x) for x in hidden}
            else:
                self.hidden_row_keys.clear()

            self.refresh_grid()
        finally:
            self._loading_project = False

        return True

    def save_project_state(self):
        if not self.project_path or self._loading_project:
            return

        payload = {
            "template_path": self.source_path,
            "parameters": self.parameters,
            "hidden_row_keys": sorted(self.hidden_row_keys),
        }

        try:
            with open(self.project_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except Exception as exc:
            QMessageBox.warning(self, "Save Failed", f"Could not save project:\n{exc}")

    def select_json_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select JSON File", "", "JSON Files (*.json)")
        if not path:
            return

        self.load_template_from_path(path, reset_parameters=True, show_errors=True)

    def load_template_from_path(self, path, reset_parameters=True, show_errors=True):
        if not path:
            return False

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            if show_errors:
                QMessageBox.warning(self, "Invalid JSON", f"Could not read JSON file:\n{exc}")
            return False

        fields = self.get_json_fields(data)
        if not fields:
            if show_errors:
                QMessageBox.warning(self, "No Fields", "Could not find any usable fields in this JSON file.")
            return False

        self.source_json = data
        self.source_path = path
        self.parameter_input.clear()
        self.parameter_input.addItems(fields)
        self.parameter_input.setEnabled(True)
        self.file_label.setText(os.path.basename(path))
        self.values_input.clear()
        self.on_parameter_selection_changed(self.parameter_input.currentText())

        if reset_parameters:
            self.parameters = []
            self.hidden_row_keys.clear()
        self.refresh_grid()
        self.save_project_state()
        return True

    def on_parameter_selection_changed(self, field_name):
        value_type = self.infer_field_type(field_name) if field_name else "str"
        placeholders = {
            "bool": "Values (true, false)",
            "int": "Values (1, 5, 1:2:9 or logspace(1,4,1000))",
            "float": "Values (1.5, 2.0, 1:0.5:3 or logspace(1e-3,5,1))",
            "str": "Values (alpha, beta)",
            "json": "Complex JSON values: uses values from selected JSON",
        }
        self.values_input.setPlaceholderText(placeholders.get(value_type, "Values"))
        self.values_input.setEnabled(value_type != "json")

    def split_value_tokens(self, raw_text):
        tokens = []
        current = []
        depth = 0

        for ch in raw_text:
            if ch == "(":
                depth += 1
                current.append(ch)
            elif ch == ")":
                depth = max(0, depth - 1)
                current.append(ch)
            elif ch == "," and depth == 0:
                token = "".join(current).strip()
                if token:
                    tokens.append(token)
                current = []
            else:
                current.append(ch)

        token = "".join(current).strip()
        if token:
            tokens.append(token)

        return tokens

    def parse_values_input(self, raw_text, value_type):
        values = []
        tokens = self.split_value_tokens(raw_text)

        for token in tokens:
            expanded = self.expand_logspace_token(token, value_type)
            if expanded is None:
                expanded = self.expand_range_token(token, value_type)
            if expanded is None:
                parsed_single, ok = self.parse_single_value(token, value_type)
                if not ok:
                    return [], f"'{token}' is not valid for type {value_type}."
                values.append(parsed_single)
            else:
                values.extend(expanded)

        return values, ""

    def expand_logspace_token(self, token, value_type):
        if value_type not in ("int", "float"):
            return None

        normalized = token.replace(" ", "")
        if not normalized.startswith("logspace(") or not normalized.endswith(")"):
            return None

        args_text = normalized[len("logspace("):-1]
        parts = args_text.split(",")
        if len(parts) != 3 or any(not part for part in parts):
            return []

        try:
            start = float(parts[0])
            points_float = float(parts[1])
            end = float(parts[2])
        except ValueError:
            return []

        if start <= 0 or end <= 0:
            return []

        points = int(round(points_float))
        if abs(points_float - points) > 1e-12 or points <= 0:
            return []

        if points == 1:
            values = [start]
            return values if value_type == "float" else [int(round(start))]

        log_start = math.log10(start)
        log_end = math.log10(end)
        values = []
        for idx in range(points):
            t = idx / (points - 1)
            exp = log_start + t * (log_end - log_start)
            value = 10 ** exp
            if value_type == "float":
                values.append(value)
            else:
                values.append(int(round(value)))

        return values

    def expand_range_token(self, token, value_type):
        if value_type not in ("int", "float"):
            return None

        parts = [part.strip() for part in token.split(":")]
        if len(parts) != 3 or any(not part for part in parts):
            return None

        try:
            if value_type == "int":
                start = int(parts[0])
                step = int(parts[1])
                end = int(parts[2])
            else:
                start = float(parts[0])
                step = float(parts[1])
                end = float(parts[2])
        except ValueError:
            return None

        if step == 0:
            return None

        if (end - start) * step < 0:
            return []

        values = []
        current = start
        epsilon = abs(step) * 1e-9 + 1e-12
        max_points = 10000

        while len(values) < max_points:
            if step > 0 and current > end + epsilon:
                break
            if step < 0 and current < end - epsilon:
                break

            values.append(current if value_type == "float" else int(current))
            current += step

        return values

    def parse_single_value(self, token, value_type):
        if value_type == "bool":
            lowered = token.strip().lower()
            if lowered in ("true", "1", "yes", "y", "on"):
                return True, True
            if lowered in ("false", "0", "no", "n", "off"):
                return False, True
            return None, False

        if value_type == "int":
            try:
                return int(token), True
            except ValueError:
                return None, False

        if value_type == "float":
            try:
                return float(token), True
            except ValueError:
                return None, False

        # String fallback.
        return token, True

    def infer_field_type(self, field_name):
        values = self.get_values_for_field(field_name)
        return self.infer_type_from_values(values)

    def infer_type_from_values(self, values):
        scalar_types = []
        for value in values:
            if isinstance(value, bool):
                scalar_types.append("bool")
            elif isinstance(value, int):
                scalar_types.append("int")
            elif isinstance(value, float):
                scalar_types.append("float")
            elif isinstance(value, str):
                scalar_types.append("str")
            else:
                return "json"

        if not scalar_types:
            return "str"
        if all(t == "bool" for t in scalar_types):
            return "bool"
        if all(t in ("int", "bool") for t in scalar_types):
            return "int"
        if all(t in ("int", "float", "bool") for t in scalar_types):
            return "float"
        if all(t == "str" for t in scalar_types):
            return "str"
        return "json"

    def format_numeric_value(self, value):
        rounded_int = round(value)
        if abs(value - rounded_int) < 1e-12:
            return str(rounded_int)
        return f"{value:.12g}"

    def format_value_for_table(self, value):
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float):
            return self.format_numeric_value(value)
        if isinstance(value, (dict, list)):
            return json.dumps(value)
        return str(value)

    def get_json_fields(self, data):
        self.field_paths = {}

        if isinstance(data, dict):
            self.collect_leaf_paths(data, [])
            return sorted(self.field_paths.keys())

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    self.collect_leaf_paths(item, [])
            return sorted(self.field_paths.keys())

        return []

    def collect_leaf_paths(self, value, prefix):
        if isinstance(value, dict):
            if not value:
                self.register_field_path(prefix)
                return
            for key, child in value.items():
                self.collect_leaf_paths(child, prefix + [str(key)])
            return

        # Keep lists as leaf values so users can still select them.
        self.register_field_path(prefix)

    def register_field_path(self, segments):
        if not segments:
            return

        display = "-".join(segments)
        unique_display = display
        suffix = 2
        while unique_display in self.field_paths and self.field_paths[unique_display] != segments:
            unique_display = f"{display} ({suffix})"
            suffix += 1

        self.field_paths[unique_display] = list(segments)

    def get_path_segments(self, field_name):
        if field_name in self.field_paths:
            return self.field_paths[field_name]
        return [field_name]

    def try_get_nested_value(self, source, segments):
        current = source
        for segment in segments:
            if not isinstance(current, dict) or segment not in current:
                return None, False
            current = current[segment]
        return current, True

    def get_values_for_field(self, field_name):
        segments = self.get_path_segments(field_name)

        if isinstance(self.source_json, dict):
            raw, ok = self.try_get_nested_value(self.source_json, segments)
            if not ok:
                return []
            if isinstance(raw, list):
                return list(raw)
            return [raw]

        if isinstance(self.source_json, list):
            values = []
            for item in self.source_json:
                if not isinstance(item, dict):
                    continue
                raw, ok = self.try_get_nested_value(item, segments)
                if ok:
                    values.append(raw)
            # Keep order while removing duplicates.
            deduped = []
            seen = set()
            for value in values:
                try:
                    key = json.dumps(value, sort_keys=True)
                except TypeError:
                    key = str(value)
                if key not in seen:
                    seen.add(key)
                    deduped.append(value)
            return deduped

        return []

    def show_table_context_menu(self, pos):
        row = self.table.rowAt(pos.y())
        col = self.table.columnAt(pos.x())
        self.open_remove_column_menu(self.table.viewport().mapToGlobal(pos), col, row)

    def show_header_context_menu(self, pos):
        col = self.table.horizontalHeader().logicalIndexAt(pos)
        self.open_remove_column_menu(self.table.horizontalHeader().mapToGlobal(pos), col)

    def open_remove_column_menu(self, global_pos, col, row=None):
        if col < 0 or col >= len(self.parameters):
            return

        menu = QMenu(self)
        edit_action = menu.addAction("Edit values")
        remove_row_action = None
        if row is not None and 0 <= row < len(self.current_grid_data):
            remove_row_action = menu.addAction("Remove this row")

        remove_action = menu.addAction(f"Remove '{self.parameters[col]['name']}'")
        selected_action = menu.exec(global_pos)

        if selected_action == edit_action:
            self.edit_parameter_values(col)
        elif selected_action == remove_row_action:
            self.remove_row(row)
        elif selected_action == remove_action:
            self.remove_parameter_column(col)

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

    def remove_parameter_column(self, col):
        if 0 <= col < len(self.parameters):
            self.parameters.pop(col)
            self.hidden_row_keys.clear()
            self.refresh_grid()
            self.save_project_state()

    def edit_parameter_values(self, col):
        if col < 0 or col >= len(self.parameters):
            return

        param = self.parameters[col]
        value_type = param.get("value_type", "str")
        if value_type == "json":
            QMessageBox.information(self, "Edit Not Supported", "Editing complex JSON values here is not supported.")
            return

        current_values = ", ".join(self.format_value_for_table(v) for v in param["values"])
        title = f"Edit values: {param['name']}"
        prompt = "Enter comma-separated values. Numeric ranges like 1:2:9 are supported for numeric types."
        text, ok = QInputDialog.getMultiLineText(self, title, prompt, current_values)
        if not ok:
            return

        parsed_values, error = self.parse_values_input(text.strip(), value_type)
        if error:
            QMessageBox.warning(self, "Invalid Values", error)
            return

        if not parsed_values:
            QMessageBox.warning(self, "No Values", "At least one value is required.")
            return

        param["values"] = parsed_values
        self.hidden_row_keys.clear()
        self.refresh_grid()
        self.save_project_state()

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
        new_value, ok = self.parse_single_value(item.text().strip(), value_type)
        if not ok:
            self._refreshing_table = True
            try:
                item.setText(self.format_value_for_table(old_value))
            finally:
                self._refreshing_table = False
            QMessageBox.warning(self, "Invalid Value", f"Please enter a valid {value_type} value.")
            return

        if old_value != new_value:
            self.parameters[col]["values"][val_idx] = new_value
            self.hidden_row_keys.clear()
            self.refresh_grid()
            self.save_project_state()

    def add_parameter(self):
        if self.source_json is None:
            QMessageBox.information(self, "Select JSON", "Please select a JSON file first.")
            return

        name = self.parameter_input.currentText().strip()
        if not name:
            return

        if any(p["name"] == name for p in self.parameters):
            QMessageBox.information(self, "Already Added", f"Parameter '{name}' is already in the table.")
            return

        value_type = self.infer_field_type(name)
        typed_text = self.values_input.text().strip()
        typed_values, error = self.parse_values_input(typed_text, value_type) if typed_text else ([], "")
        if error:
            QMessageBox.warning(self, "Invalid Values", error)
            return

        vals = typed_values if typed_values else self.get_values_for_field(name)
        if not vals:
            QMessageBox.warning(self, "No Values", f"Field '{name}' has no values to add.")
            return

        self.parameters.append({"name": name, "values": vals, "value_type": value_type})
        self.values_input.clear()
        self.hidden_row_keys.clear()
        self.refresh_grid()
        self.save_project_state()

    def remove_row(self, row_idx):
        if row_idx < 0 or row_idx >= len(self.current_grid_data):
            return

        row_key = self.build_row_key(self.current_grid_data[row_idx])
        self.hidden_row_keys.add(row_key)
        self.refresh_grid()
        self.save_project_state()

    def export_row_to_json(self, row_data):
        base_name = self.build_row_name(row_data)
        default_name = f"{base_name}.json"

        path, _ = QFileDialog.getSaveFileName(self, "Save Row JSON", default_name, "JSON Files (*.json)")
        if not path:
            return

        if not path.lower().endswith(".json"):
            path = f"{path}.json"

        output_data = self.build_output_payload(row_data, base_name)

        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(output_data, f, indent=2)
        except Exception as exc:
            QMessageBox.warning(self, "Save Failed", f"Could not save file:\n{exc}")

    def export_all_rows(self):
        if not self.current_grid_data:
            QMessageBox.information(self, "No Rows", "There are no rows to export.")
            return

        directory = QFileDialog.getExistingDirectory(self, "Select Output Folder")
        if not directory:
            return

        written = 0
        for row_data in self.current_grid_data:
            base_name = self.build_row_name(row_data)
            output_data = self.build_output_payload(row_data, base_name)
            file_path = self.build_unique_output_path(directory, base_name)

            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(output_data, f, indent=2)
                written += 1
            except Exception as exc:
                QMessageBox.warning(self, "Save Failed", f"Could not save file:\n{exc}")
                return

        QMessageBox.information(self, "Export Complete", f"Exported {written} JSON files.")

    def build_unique_output_path(self, directory, base_name):
        candidate = os.path.join(directory, f"{base_name}.json")
        if not os.path.exists(candidate):
            return candidate

        idx = 2
        while True:
            candidate = os.path.join(directory, f"{base_name}_{idx}.json")
            if not os.path.exists(candidate):
                return candidate
            idx += 1

    def build_row_name(self, row_data):
        source_base = "row"
        if self.source_path:
            source_base = os.path.splitext(os.path.basename(self.source_path))[0]

        parts = [source_base]
        for param in self.parameters:
            name = param["name"]
            if name in row_data:
                value_text = self.format_value_for_table(row_data[name])
                parts.append(f"{name}{value_text}")

        return "_".join(self.sanitize_name_token(part) for part in parts if part)

    def build_row_key(self, row_data):
        parts = []
        for param in self.parameters:
            name = param["name"]
            if name not in row_data:
                continue
            value = row_data[name]
            try:
                serialized = json.dumps(value, sort_keys=True)
            except TypeError:
                serialized = str(value)
            parts.append(f"{name}={serialized}")
        return "|".join(parts)

    def sanitize_name_token(self, token):
        allowed = "-_."
        cleaned = []
        for ch in str(token):
            if ch.isalnum() or ch in allowed:
                cleaned.append(ch)
            elif ch.isspace():
                cleaned.append("-")
            else:
                cleaned.append("-")

        sanitized = "".join(cleaned).strip("-_")
        return sanitized or "value"

    def build_output_payload(self, row_data, simulation_name):
        if isinstance(self.source_json, dict):
            payload = copy.deepcopy(self.source_json)
            for key, value in row_data.items():
                self.set_nested_value(payload, self.get_path_segments(key), value)
            payload["simulation"] = simulation_name
            return payload

        payload = dict(row_data)
        payload["simulation"] = simulation_name
        return payload

    def set_nested_value(self, target, segments, value):
        if not segments:
            return

        current = target
        for segment in segments[:-1]:
            if segment not in current or not isinstance(current[segment], dict):
                current[segment] = {}
            current = current[segment]
        current[segments[-1]] = value

    def refresh_grid(self):
        self._refreshing_table = True
        try:
            total_rows = 1
            for p in self.parameters:
                total_rows *= len(p["values"])

            self.table.clearSpans()
            self.table.clear()
            self.table.setRowCount(total_rows)
            self.table.setColumnCount(len(self.parameters) + 1)
            self.table.setHorizontalHeaderLabels([p["name"] for p in self.parameters] + ["Actions"])

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

                        # Create the item
                        item = QTableWidgetItem(self.format_value_for_table(val))
                        item.setTextAlignment(Qt.AlignCenter)
                        if param.get("value_type") == "json":
                            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                        self.table.setItem(start_row, col_idx, item)

                        if row_repeat > 1:
                            self.table.setSpan(start_row, col_idx, row_repeat, 1)

                        for r in range(start_row, start_row + row_repeat):
                            full_grid_data[r][param['name']] = val

                current_span = row_repeat

            visible_grid_data = [
                row_data
                for row_data in full_grid_data
                if self.build_row_key(row_data) not in self.hidden_row_keys
            ]

            self.table.setRowCount(len(visible_grid_data))
            self.table.clearSpans()

            # Rebuild table body with only visible rows.
            for r, row_data in enumerate(visible_grid_data):
                for col_idx, param in enumerate(self.parameters):
                    val = row_data[param["name"]]
                    item = QTableWidgetItem(self.format_value_for_table(val))
                    item.setTextAlignment(Qt.AlignCenter)
                    if param.get("value_type") == "json":
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                    self.table.setItem(r, col_idx, item)

            for r in range(len(visible_grid_data)):
                btn = EnterKeyButton("Print")
                node_data = visible_grid_data[r]
                btn.clicked.connect(lambda chk=False, d=dict(node_data): self.export_row_to_json(d))
                self.table.setCellWidget(r, len(self.parameters), btn)

            self.current_grid_data = [dict(row_data) for row_data in visible_grid_data]
            self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        finally:
            self._refreshing_table = False

        self.save_project_state()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = WorkbenchGrid()
    window.show()
    sys.exit(app.exec())
