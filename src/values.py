"""Parsing and formatting of parameter values.

Shared by the grid widget and the window, owned by neither.
"""

import json
import math


def split_value_tokens(raw_text):
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


def parse_values_input(raw_text, value_type):
    values = []
    tokens = split_value_tokens(raw_text)

    for token in tokens:
        expanded = expand_logspace_token(token, value_type)
        if expanded is None:
            expanded = expand_range_token(token, value_type)
        if expanded is None:
            parsed_single, ok = parse_single_value(token, value_type)
            if not ok:
                return [], f"'{token}' is not valid for type {value_type}."
            values.append(parsed_single)
        else:
            values.extend(expanded)

    return values, ""


def expand_logspace_token(token, value_type):
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


def expand_range_token(token, value_type):
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


def parse_single_value(token, value_type):
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


def format_numeric_value(value):
    rounded_int = round(value)
    if abs(value - rounded_int) < 1e-12:
        return str(rounded_int)
    return f"{value:.12g}"


def format_value_for_table(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format_numeric_value(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)
