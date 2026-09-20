"""Resolve explicit array units without discarding conflicting declarations."""

from ..exceptions import DataValidationError


def declared_unit(array, metadata, name):
    metadata_units = metadata.get("units", {})
    if not isinstance(metadata_units, dict):
        raise DataValidationError(
            f"Invalid unit declarations for {name}: metadata.units must be an object"
        )
    declarations = {
        "unit": array.attrs.get("unit"),
        "units": array.attrs.get("units"),
        "metadata.units": metadata_units.get(name),
    }
    values = [value for value in declarations.values() if value is not None]
    if any(not isinstance(value, str) or not value for value in values):
        raise DataValidationError(f"Invalid unit declaration for {name}")
    if len(set(values)) > 1:
        raise DataValidationError(f"Conflicting unit declarations for {name}: {declarations}")
    return values[0] if values else None
