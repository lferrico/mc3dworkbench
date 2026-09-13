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
        if expanded == [] and is_logspace_token(token):
            return [], (f"'{token}' is not valid: use logspace(start, end, count) "
                        "with a positive start and end and a whole count.")
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


LOGSPACE = "logspace("


def is_logspace_token(token):
    normalized = token.replace(" ", "")
    return normalized.startswith(LOGSPACE) and normalized.endswith(")")


def expand_logspace_token(token, value_type):
    """logspace(start, end, count) -> `count` values spaced geometrically.

    Returns None when the token is not a logspace call at all, and an empty
    list when it is one but cannot be read.
    """
    if value_type not in ("int", "float") or not is_logspace_token(token):
        return None

    normalized = token.replace(" ", "")
    parts = normalized[len(LOGSPACE):-1].split(",")
    if len(parts) != 3 or any(not part for part in parts):
        return []

    try:
        start = float(parts[0])
        end = float(parts[1])
        count_value = float(parts[2])
    except ValueError:
        return []

    # A geometric progression has no way through zero or a sign change.
    if start <= 0 or end <= 0:
        return []

    count = int(round(count_value))
    if abs(count_value - count) > 1e-12 or count <= 0:
        return []

    if count == 1:
        return [start] if value_type == "float" else [int(round(start))]

    log_start = math.log10(start)
    log_end = math.log10(end)
    values = []
    for index in range(count):
        fraction = index / (count - 1)
        value = 10 ** (log_start + fraction * (log_end - log_start))
        values.append(value if value_type == "float" else int(round(value)))

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
    # Whether a value is a whole number is a question about the value, not
    # about its distance from zero: an absolute tolerance here flattens every
    # small magnitude, turning a 1e-16 timestep into 0.
    if float(value).is_integer() and abs(value) < 1e16:
        return str(int(value))
    return f"{value:.12g}"


def format_value_for_table(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format_numeric_value(value)
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)
