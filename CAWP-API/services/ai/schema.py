"""Strict JSON schemas for structured outputs, and a validator for what comes back.

`check_strict(schema)` enforces the structured-outputs strict-mode rules that bite most often
(every object has `additionalProperties: false` and lists every property in `required`), so a
Q1-Q3 schema mistake fails in our tests instead of as a provider 400.

`validate(value, schema)` checks a parsed answer against the subset we use: type (incl. a list of
types such as ["number", "null"]), properties/required/additionalProperties, items, enum,
const, anyOf, minItems/maxItems, minLength/maxLength, minimum/maximum. The provider guarantees
schema adherence in strict mode; we check anyway, because a result we cannot vouch for must
never be shown as if it were valid.
"""
from __future__ import annotations

_TYPES = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


class SchemaError(ValueError):
    pass


def check_strict(schema: dict, path: str = "$") -> None:
    """Raise SchemaError when `schema` breaks the strict structured-output rules."""
    if not isinstance(schema, dict):
        raise SchemaError(f"{path}: a schema must be an object")
    types = schema.get("type")
    types = types if isinstance(types, list) else [types] if types else []
    for t in types:
        if t not in _TYPES:
            raise SchemaError(f"{path}: unknown type {t!r}")
    if "object" in types:
        props = schema.get("properties")
        if not isinstance(props, dict):
            raise SchemaError(f"{path}: an object needs properties")
        if schema.get("additionalProperties") is not False:
            raise SchemaError(f"{path}: an object needs additionalProperties: false")
        if sorted(schema.get("required") or []) != sorted(props):
            raise SchemaError(f"{path}: every property must be listed in required")
        for name, sub in props.items():
            check_strict(sub, f"{path}.{name}")
    if "array" in types:
        if "items" not in schema:
            raise SchemaError(f"{path}: an array needs items")
        check_strict(schema["items"], f"{path}[]")
    for i, sub in enumerate(schema.get("anyOf") or []):
        check_strict(sub, f"{path}.anyOf[{i}]")
    if not types and "anyOf" not in schema and "enum" not in schema and "const" not in schema:
        raise SchemaError(f"{path}: a schema needs a type, anyOf, enum or const")


def validate(value, schema: dict, path: str = "$") -> None:
    """Raise SchemaError at the first mismatch."""
    if "anyOf" in schema:
        for sub in schema["anyOf"]:
            try:
                validate(value, sub, path)
                break
            except SchemaError:
                continue
        else:
            raise SchemaError(f"{path}: matches none of anyOf")
    types = schema.get("type")
    if types:
        types = types if isinstance(types, list) else [types]
        if not any(_TYPES[t](value) for t in types):
            raise SchemaError(f"{path}: expected {'/'.join(types)}")
    if "enum" in schema and value not in schema["enum"]:
        raise SchemaError(f"{path}: not an allowed value")
    if "const" in schema and value != schema["const"]:
        raise SchemaError(f"{path}: not the constant value")
    if isinstance(value, dict) and "properties" in schema:
        props = schema["properties"]
        for name in schema.get("required") or []:
            if name not in value:
                raise SchemaError(f"{path}.{name}: required")
        if schema.get("additionalProperties") is False:
            extra = set(value) - set(props)
            if extra:
                raise SchemaError(f"{path}: unexpected properties")
        for name, sub in props.items():
            if name in value:
                validate(value[name], sub, f"{path}.{name}")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise SchemaError(f"{path}: too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise SchemaError(f"{path}: too many items")
        if "items" in schema:
            for i, item in enumerate(value):
                validate(item, schema["items"], f"{path}[{i}]")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise SchemaError(f"{path}: too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise SchemaError(f"{path}: too long")
    if _TYPES["number"](value):
        if "minimum" in schema and value < schema["minimum"]:
            raise SchemaError(f"{path}: below the minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise SchemaError(f"{path}: above the maximum")
