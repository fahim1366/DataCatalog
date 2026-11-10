from __future__ import annotations

from typing import Dict, List, Tuple

MONTH_NAMES: Dict[str, int] = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}

DOW_NAMES: Dict[str, int] = {
    "SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6,
}

FIELD_BOUNDS: List[Tuple[str, int, int]] = [
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day of month", 1, 31),
    ("month", 1, 12),
    ("day of week", 0, 7),
]


def _parse_token(token: str, name_map: Dict[str, int]) -> int:
    token_upper = token.upper()
    if token_upper in name_map:
        return name_map[token_upper]
    if not token.isdigit():
        raise ValueError(f"Invalid integer value: '{token}'")
    return int(token)


def _validate_cron_field(field_str: str, field_name: str, min_val: int, max_val: int, name_map: Dict[str, int]) -> None:
    if not field_str:
        raise ValueError(f"Field '{field_name}' cannot be empty")

    items = field_str.split(",")
    for item in items:
        if not item:
            raise ValueError(f"Empty element in field '{field_name}'")

        # Step expression: either */step or range/step
        if "/" in item:
            parts = item.split("/")
            if len(parts) != 2:
                raise ValueError(f"Invalid step expression '{item}' in field '{field_name}'")
            base, step_str = parts[0], parts[1]
            if not step_str.isdigit():
                raise ValueError(f"Step '{step_str}' must be a positive integer in field '{field_name}'")
            step = int(step_str)
            if step < 1:
                raise ValueError(f"Step '{step}' must be >= 1 in field '{field_name}'")

            if base == "*":
                continue
            item = base

        if item == "*":
            continue

        if "-" in item:
            range_parts = item.split("-")
            if len(range_parts) != 2:
                raise ValueError(f"Invalid range expression '{item}' in field '{field_name}'")
            start = _parse_token(range_parts[0], name_map)
            end = _parse_token(range_parts[1], name_map)
            if not (min_val <= start <= max_val) or not (min_val <= end <= max_val):
                raise ValueError(
                    f"Range values '{item}' must be between {min_val} and {max_val} in field '{field_name}'"
                )
            if start > end:
                raise ValueError(f"Range start '{start}' must be <= end '{end}' in field '{field_name}'")
        else:
            val = _parse_token(item, name_map)
            if not (min_val <= val <= max_val):
                raise ValueError(
                    f"Value '{val}' out of range [{min_val}, {max_val}] in field '{field_name}'"
                )


def validate_cron_expression(expression: str) -> None:
    """
    Rigorously validates standard 5-part cron syntax:
    `<minute> <hour> <day of month> <month> <day of week>`.

    Raises:
        ValueError: If the cron expression does not contain exactly 5 valid fields.
    """
    if not isinstance(expression, str) or not expression.strip():
        raise ValueError("Cron expression must be a non-empty string")

    fields = expression.strip().split()
    if len(fields) != 5:
        raise ValueError(
            f"Invalid cron expression: '{expression}'. Expected exactly 5 whitespace-separated fields, got {len(fields)}."
        )

    for i, (field_name, min_v, max_v) in enumerate(FIELD_BOUNDS):
        name_map = {}
        if field_name == "month":
            name_map = MONTH_NAMES
        elif field_name == "day of week":
            name_map = DOW_NAMES

        try:
            _validate_cron_field(fields[i], field_name, min_v, max_v, name_map)
        except ValueError as exc:
            raise ValueError(f"Invalid cron field '{fields[i]}' for {field_name}: {str(exc)}") from exc

