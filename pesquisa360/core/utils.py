# pesquisa360/core/utils.py
from shapely.wkb import loads
from typing import Optional, Dict


def web_point(latitude: float, longitude: float) -> Dict[str, float]:
    return {"lat": float(latitude), "lng": float(longitude)}


def geojson_point(longitude: float, latitude: float) -> Dict[str, object]:
    return {
        "type": "Point",
        "coordinates": [float(longitude), float(latitude)],
    }

def wkb_to_geojson_point(valor) -> Optional[Dict[str, object]]:
    """Geometria POINT persistida -> Point GeoJSON (ADR-004).

    Nunca devolve o objeto PostGIS cru. `None` continua `None`: ausencia de
    ponto e estado valido e nao deve virar coordenada inventada.
    """
    if valor is None:
        return None
    if isinstance(valor, dict):
        return valor
    dado = getattr(valor, "data", valor)
    # PostGIS entrega memoryview; o SQLite dos testes entrega hex. shapely nao
    # aceita memoryview, entao normalizamos antes de parsear.
    if isinstance(dado, memoryview):
        dado = dado.tobytes()
    elif isinstance(dado, bytearray):
        dado = bytes(dado)
    ponto = loads(dado)
    return geojson_point(longitude=ponto.x, latitude=ponto.y)


def convert_wkb_to_geojson(wkb_element) -> Optional[Dict[str, float]]:
    """Converte um objeto WKBElement do PostGIS para um dicionário GeoJSON-like."""
    if wkb_element is None:
        return None
    if hasattr(wkb_element, 'geom_type'):
        point = loads(wkb_element.data)
        return web_point(latitude=point.y, longitude=point.x)
    return wkb_element
