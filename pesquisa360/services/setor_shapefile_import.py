"""Leitura e normalizacao de Shapefile para setores.

Este modulo nao acessa banco, terminal ou configuracao da aplicacao.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import shapefile
from pyproj import CRS, Transformer
from shapely.geometry import Polygon, shape
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform, unary_union
from shapely.validation import make_valid


REQUIRED_EXTENSIONS = (".shp", ".shx", ".dbf", ".prj")


class ShapefileImportError(ValueError):
    """Erro de entrada geoespacial seguro para exibicao ao operador."""


class MultipleFeaturesError(ShapefileImportError):
    def __init__(self, feature_count: int):
        self.feature_count = feature_count
        super().__init__(
            f"O arquivo possui {feature_count} feicoes. Escolha unir ou cancelar."
        )


@dataclass(frozen=True)
class ImportedSectorGeometry:
    polygon: Polygon
    source_crs: str
    feature_count: int
    features_merged: bool

    @property
    def coordinates_lat_lon(self) -> list[list[float]]:
        return [[float(latitude), float(longitude)] for longitude, latitude in self.polygon.exterior.coords]


def validate_shapefile_components(shp_path: str | Path) -> dict[str, Path]:
    path = Path(shp_path).expanduser()
    if path.suffix.casefold() != ".shp":
        raise ShapefileImportError("Informe um arquivo com extensao .shp.")

    components = {extension: path.with_suffix(extension) for extension in REQUIRED_EXTENSIONS}
    missing = [component.name for component in components.values() if not component.is_file()]
    if missing:
        raise ShapefileImportError(
            "Conjunto Shapefile incompleto. Arquivos ausentes: " + ", ".join(missing)
        )
    return components


def read_source_crs(prj_path: str | Path) -> CRS:
    path = Path(prj_path)
    try:
        definition = path.read_text(encoding="utf-8-sig").strip()
    except UnicodeDecodeError:
        definition = path.read_text(encoding="latin-1").strip()
    if not definition:
        raise ShapefileImportError(f"O arquivo de projecao {path.name} esta vazio.")
    try:
        return CRS.from_wkt(definition)
    except Exception as exc:
        raise ShapefileImportError(
            f"Nao foi possivel interpretar o CRS de {path.name}."
        ) from exc


def _validated_polygon(geometry: BaseGeometry, context: str) -> Polygon:
    if geometry.is_empty:
        raise ShapefileImportError(f"{context} resultou em geometria vazia.")
    if not geometry.is_valid:
        geometry = make_valid(geometry)
    if geometry.geom_type != "Polygon":
        raise ShapefileImportError(
            f"{context} resultou em {geometry.geom_type}. O banco aceita somente Polygon; "
            "MultiPolygon e outros tipos nao podem ser importados nesta versao."
        )
    polygon = geometry
    if not polygon.is_valid or polygon.is_empty:
        raise ShapefileImportError(f"{context} nao produziu um Polygon valido.")
    if polygon.interiors:
        raise ShapefileImportError(
            f"{context} possui aneis internos. O contrato atual de criacao de setor "
            "aceita somente um anel externo."
        )
    return polygon


def load_sector_geometry(
    shp_path: str | Path,
    *,
    merge_features: bool = False,
) -> ImportedSectorGeometry:
    components = validate_shapefile_components(shp_path)
    source_crs = read_source_crs(components[".prj"])
    try:
        transformer = Transformer.from_crs(source_crs, CRS.from_epsg(4326), always_xy=True)
    except Exception as exc:
        raise ShapefileImportError("Nao foi possivel preparar a transformacao para EPSG:4326.") from exc

    try:
        with shapefile.Reader(str(components[".shp"])) as reader:
            source_shapes = reader.shapes()
    except Exception as exc:
        raise ShapefileImportError("Nao foi possivel ler o conjunto Shapefile.") from exc

    feature_count = len(source_shapes)
    if feature_count == 0:
        raise ShapefileImportError("O Shapefile nao possui feicoes.")
    if feature_count > 1 and not merge_features:
        raise MultipleFeaturesError(feature_count)

    geometries: list[BaseGeometry] = []
    for index, source_shape in enumerate(source_shapes, start=1):
        try:
            geometry = shape(source_shape.__geo_interface__)
            geometry = transform(transformer.transform, geometry)
        except Exception as exc:
            raise ShapefileImportError(f"Falha ao converter a feicao {index} para EPSG:4326.") from exc
        geometries.append(_validated_polygon(geometry, f"A feicao {index}"))

    final_geometry = geometries[0] if feature_count == 1 else unary_union(geometries)
    final_polygon = _validated_polygon(final_geometry, "A uniao das feicoes")
    return ImportedSectorGeometry(
        polygon=final_polygon,
        source_crs=source_crs.to_string(),
        feature_count=feature_count,
        features_merged=feature_count > 1,
    )
