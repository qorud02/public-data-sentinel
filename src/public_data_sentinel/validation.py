"""Validation retains text identifiers and reports row-level failures."""

import datetime as dt
import json
import re
from decimal import Decimal, InvalidOperation


class ContractError(ValueError):
    """An unsupported or contradictory contract cannot be applied."""


def _decimal(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not a number")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Expected a decimal number") from None
    if not number.is_finite():
        raise ValueError("Number must be finite")
    return number


def check_contract(contract):
    if not isinstance(contract, dict):
        raise ContractError("Contract must be a JSON object")
    allowed = {"columns", "unique_by", "allow_extra_columns"}
    if set(contract) - allowed:
        raise ContractError("Unknown contract keys: " + ", ".join(sorted(set(contract) - allowed)))
    columns = contract.get("columns")
    if not isinstance(columns, dict) or not columns:
        raise ContractError("columns must be a nonempty object")
    if not isinstance(contract.get("allow_extra_columns", False), bool):
        raise ContractError("allow_extra_columns must be boolean")
    for name, rules in columns.items():
        if not isinstance(name, str) or not name or not isinstance(rules, dict):
            raise ContractError("Each column needs a name and an object of rules")
        extra = set(rules) - {"type", "required", "minimum", "maximum", "enum"}
        if extra:
            raise ContractError(f"{name}: unknown rules: {', '.join(sorted(extra))}")
        kind = rules.get("type")
        if not isinstance(kind, str) or kind not in {"string", "integer", "decimal", "date"}:
            raise ContractError(f"{name}: type must be string, integer, decimal or date")
        if not isinstance(rules.get("required", True), bool):
            raise ContractError(f"{name}: required must be boolean")
        if "enum" in rules and (not isinstance(rules["enum"], list) or not rules["enum"]):
            raise ContractError(f"{name}: enum must be a nonempty list")
        if "enum" in rules:
            try:
                for choice in rules["enum"]:
                    _coerce(choice, kind)
            except ValueError as exc:
                raise ContractError(f"{name}: enum contains an invalid value: {exc}") from exc
        if {"minimum", "maximum"} & rules.keys():
            if kind not in {"integer", "decimal"}:
                raise ContractError(f"{name}: numeric limits need integer or decimal type")
            try:
                limits = {key: _decimal(rules[key]) for key in ("minimum", "maximum") if key in rules}
            except ValueError as exc:
                raise ContractError(f"{name}: invalid numeric limit: {exc}") from exc
            if "minimum" in limits and "maximum" in limits and limits["minimum"] > limits["maximum"]:
                raise ContractError(f"{name}: minimum exceeds maximum")
    unique = contract.get("unique_by", [])
    if not isinstance(unique, list) or any(not isinstance(key, str) or key not in columns for key in unique):
        raise ContractError("unique_by must list known column names")
    if len(set(unique)) != len(unique):
        raise ContractError("unique_by repeats a column")
    return contract


def _coerce(value, kind):
    if kind == "string":
        if not isinstance(value, str):
            raise ValueError("Expected text; numeric identifiers must be supplied as strings")
        return value
    if kind == "date":
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("Expected a date in YYYY-MM-DD format")
        try:
            return dt.date.fromisoformat(value)
        except ValueError:
            raise ValueError("Date does not exist") from None
    number = _decimal(value)
    if kind == "integer" and number != number.to_integral_value():
        raise ValueError("Expected a whole number")
    return number


def validate(records, contract, *, headers=None):
    """Validate records. Record numbers are one-based, excluding a CSV header.

    This is an intentionally small contract language, not a JSON Schema implementation.
    The input records are never mutated. CSV numerics are parsed only for checking;
    fields declared as strings retain leading zeroes.
    """
    check_contract(contract)
    columns = contract["columns"]
    issues = []

    def issue(row, field, code, message):
        issues.append({"record": row, "field": field, "code": code, "message": message})

    if headers is not None:
        for name, rules in columns.items():
            if rules.get("required", True) and name not in headers:
                issue(None, name, "missing_column", "Required CSV column is missing")
        if not contract.get("allow_extra_columns", False):
            for name in headers:
                if name not in columns:
                    issue(None, name, "extra_column", "CSV column is not in the contract")
    count = 0
    seen = {}
    keys = contract.get("unique_by", [])
    for count, record in enumerate(records, 1):
        if not isinstance(record, dict):
            issue(count, None, "record_type", "Each record must be an object")
            continue
        valid_values = {}
        if headers is None and not contract.get("allow_extra_columns", False):
            for field in sorted(record.keys() - columns.keys(), key=str):
                issue(count, field, "extra_field", "Field is not in the contract")
        for field, rules in columns.items():
            value = record.get(field)
            missing = value is None or (isinstance(value, str) and not value.strip())
            if missing:
                if rules.get("required", True):
                    issue(count, field, "missing_value", "Required value is missing")
                continue
            try:
                parsed = _coerce(value, rules["type"])
            except ValueError as exc:
                issue(count, field, "type", str(exc))
                continue
            valid_values[field] = parsed
            for key, direction in (("minimum", -1), ("maximum", 1)):
                if key in rules:
                    bound = _decimal(rules[key])
                    if (direction == -1 and parsed < bound) or (direction == 1 and parsed > bound):
                        issue(count, field, key, f"Value is outside {key} {rules[key]}")
            if "enum" in rules:
                try:
                    choices = [_coerce(choice, rules["type"]) for choice in rules["enum"]]
                except ValueError as exc:
                    raise ContractError(f"{field}: enum contains an invalid value: {exc}") from exc
                if parsed not in choices:
                    issue(count, field, "enum", "Value is not in the allowed list")
        if keys and all(key in valid_values for key in keys):
            key = tuple(valid_values[field] for field in keys)
            if key in seen:
                issue(count, ", ".join(keys), "duplicate", f"Key duplicates record {seen[key]}")
            else:
                seen[key] = count
    if count == 0:
        issue(None, None, "empty_data", "No records found")
    return {"valid": not issues, "records_checked": count, "error_count": len(issues), "issues": issues}


def load_json(text):
    """Reject ambiguous keys, nonfinite numbers, and unsupported JSON inputs."""
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"Duplicate JSON key: {key}")
            result[key] = value
        return result

    def constant(value):
        raise ValueError(f"Nonstandard JSON constant: {value}")

    try:
        # Reuse strict finite conversion, including when Decimal traps are off.
        return json.loads(text, object_pairs_hook=pairs, parse_float=_decimal, parse_constant=constant)
    except RecursionError:
        raise ValueError("JSON nesting exceeds the supported parser depth") from None
