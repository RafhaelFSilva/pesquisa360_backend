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

def convert_wkb_to_geojson(wkb_element) -> Optional[Dict[str, float]]:
    """Converte um objeto WKBElement do PostGIS para um dicionário GeoJSON-like."""
    if wkb_element is None:
        return None
    if hasattr(wkb_element, 'geom_type'):
        point = loads(wkb_element.data)
        return web_point(latitude=point.y, longitude=point.x)
    return wkb_element
