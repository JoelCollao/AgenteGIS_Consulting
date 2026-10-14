"""
Skill Params - Modelos Pydantic para validación de parámetros de Skills
========================================================================
Cada skill tiene un modelo que valida los parámetros de entrada de forma
estricta antes de ejecutar la operación.
"""

import re
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Validador reutilizable de identificador SQL
# ---------------------------------------------------------------------------
_SQL_ID_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")


def _validate_sql_identifier(value: str, field_name: str) -> str:
    """Valida que el valor sea un identificador SQL seguro."""
    value = value.strip()
    if not _SQL_ID_PATTERN.match(value):
        raise ValueError(
            f"'{field_name}' no es un identificador válido: '{value}'. "
            "Solo se permiten letras, dígitos y guiones bajos."
        )
    return value


# ═══════════════════════════════════════════════════════════════════════════
# Spatial Skills
# ═══════════════════════════════════════════════════════════════════════════


class BufferParams(BaseModel):
    """Parámetros para buffer_analysis.

    Soporta dos modos:
    - PostGIS: requiere 'table' (nombre de tabla en la BD)
    - In-memory: requiere 'features' (lista de GeoJSON features del frontend)
    Al menos uno de los dos debe estar presente.
    """

    table: Optional[str] = Field(default=None, min_length=1)
    features: Optional[List[dict]] = Field(default=None)
    distance: float = Field(..., gt=0, le=100000)
    unit: str = Field(default="meters")
    geom_column: str = Field(default="geom")
    output_srid: int = Field(default=4326, ge=1, le=900914)
    id_column: Optional[str] = None
    id_value: Optional[Any] = None
    limit: int = Field(default=500, gt=0, le=2000)
    dissolve: bool = Field(default=False)

    @field_validator("table")
    @classmethod
    def validate_table(cls, v):
        if v is not None:
            return _validate_sql_identifier(v, "table")
        return v

    @field_validator("geom_column")
    @classmethod
    def validate_geom_column(cls, v):
        return _validate_sql_identifier(v, "geom_column")

    @field_validator("unit")
    @classmethod
    def validate_unit(cls, v):
        v = v.lower().strip()
        if v not in {"meters", "km", "miles"}:
            raise ValueError(f"Unidad no válida: '{v}'. Válidas: meters, km, miles")
        return v

    @field_validator("id_column")
    @classmethod
    def validate_id_column(cls, v):
        if v is not None:
            return _validate_sql_identifier(v, "id_column")
        return v

    def model_post_init(self, __context):
        """Valida que al menos table o features esté presente."""
        if not self.table and not self.features:
            raise ValueError(
                "Se requiere 'table' (capa PostGIS) o 'features' (GeoJSON del frontend). "
                "Al menos uno debe estar presente."
            )


class IntersectParams(BaseModel):
    """Parámetros para intersect."""

    table1: str = Field(..., min_length=1)
    table2: str = Field(..., min_length=1)
    geom_column: str = Field(default="geom")
    limit: int = Field(default=100, gt=0, le=500)

    @field_validator("table1", "table2")
    @classmethod
    def validate_tables(cls, v):
        return _validate_sql_identifier(v, "table")

    @field_validator("geom_column")
    @classmethod
    def validate_geom_column(cls, v):
        return _validate_sql_identifier(v, "geom_column")


class DistanceParams(BaseModel):
    """Parámetros para distance (proximidad)."""

    table: str = Field(..., min_length=1)
    longitude: float = Field(..., ge=-180, le=180)
    latitude: float = Field(..., ge=-90, le=90)
    max_distance: float = Field(default=500, gt=0, le=1000000)
    geom_column: str = Field(default="geom")
    limit: int = Field(default=50, gt=0, le=200)

    @field_validator("table")
    @classmethod
    def validate_table(cls, v):
        return _validate_sql_identifier(v, "table")

    @field_validator("geom_column")
    @classmethod
    def validate_geom_column(cls, v):
        return _validate_sql_identifier(v, "geom_column")


# ═══════════════════════════════════════════════════════════════════════════
# Data Skills
# ═══════════════════════════════════════════════════════════════════════════

_ALLOWED_OPERATORS = {"=", "!=", ">", "<", ">=", "<=", "LIKE", "ILIKE"}


class CountFeaturesParams(BaseModel):
    """Parámetros para count_features."""

    layer: str = Field(..., min_length=1)
    filter_column: Optional[str] = None
    filter_operator: Optional[str] = None
    filter_value: Optional[Any] = None

    @field_validator("layer")
    @classmethod
    def validate_layer(cls, v):
        return _validate_sql_identifier(v, "layer")

    @field_validator("filter_column")
    @classmethod
    def validate_filter_column(cls, v):
        if v is not None:
            return _validate_sql_identifier(v, "filter_column")
        return v

    @field_validator("filter_operator")
    @classmethod
    def validate_filter_operator(cls, v):
        if v is not None:
            v = v.strip().upper()
            if v not in _ALLOWED_OPERATORS:
                raise ValueError(
                    f"Operador no válido: '{v}'. "
                    f"Permitidos: {sorted(_ALLOWED_OPERATORS)}"
                )
            return v
        return v


class FilterFeaturesParams(BaseModel):
    """Parámetros para filter_features."""

    layer: str = Field(..., min_length=1)
    column: str = Field(..., min_length=1)
    operator: str = Field(default="=")
    value: Any = Field(...)
    limit: int = Field(default=100, gt=0, le=500)
    geom_column: str = Field(default="geom")

    @field_validator("layer")
    @classmethod
    def validate_layer(cls, v):
        return _validate_sql_identifier(v, "layer")

    @field_validator("column")
    @classmethod
    def validate_column(cls, v):
        v = v.strip()
        if v == "__search__":
            return v
        return _validate_sql_identifier(v, "column")

    @field_validator("operator")
    @classmethod
    def validate_operator(cls, v):
        v = v.strip().upper()
        if v not in _ALLOWED_OPERATORS:
            raise ValueError(
                f"Operador no válido: '{v}'. "
                f"Permitidos: {sorted(_ALLOWED_OPERATORS)}"
            )
        return v

    @field_validator("geom_column")
    @classmethod
    def validate_geom_column(cls, v):
        return _validate_sql_identifier(v, "geom_column")


class StatisticsParams(BaseModel):
    """Parámetros para statistics."""

    layer: str = Field(..., min_length=1)
    column: str = Field(default="id", min_length=1)
    group_by: Optional[str] = None

    @field_validator("layer")
    @classmethod
    def validate_layer(cls, v):
        return _validate_sql_identifier(v, "layer")

    @field_validator("column")
    @classmethod
    def validate_column(cls, v):
        return _validate_sql_identifier(v, "column")

    @field_validator("group_by")
    @classmethod
    def validate_group_by(cls, v):
        if v is not None:
            return _validate_sql_identifier(v, "group_by")
        return v


# ═══════════════════════════════════════════════════════════════════════════
# Map Skills
# ═══════════════════════════════════════════════════════════════════════════


class HighlightFeaturesParams(BaseModel):
    """Parámetros para highlight_features."""

    layer: str = Field(..., min_length=1)
    feature_ids: List[int] = Field(default_factory=list)
    style: Optional[dict] = None


class ZoomLayerParams(BaseModel):
    """Parámetros para zoom_layer."""

    layer: str = Field(..., min_length=1)
    extent: Optional[List[float]] = None
    zoom: Optional[int] = Field(default=None, ge=0, le=20)

    @field_validator("extent")
    @classmethod
    def validate_extent(cls, v):
        if v is not None:
            if len(v) != 4:
                raise ValueError("extent debe tener 4 valores: [minx, miny, maxx, maxy]")
            if not (-180 <= v[0] <= 180 and -180 <= v[2] <= 180):
                raise ValueError("Longitudes fuera de rango [-180, 180]")
            if not (-90 <= v[1] <= 90 and -90 <= v[3] <= 90):
                raise ValueError("Latitudes fuera de rango [-90, 90]")
        return v
