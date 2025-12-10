# pesquisa360/core/utils.py
from shapely.wkb import loads
from typing import Optional, Dict

def convert_wkb_to_geojson(wkb_element) -> Optional[Dict[str, float]]:
    """Converte um objeto WKBElement do PostGIS para um dicionário GeoJSON-like."""
    if wkb_element is None:
        return None
    if hasattr(wkb_element, 'geom_type'):
        point = loads(wkb_element.data)
        return {"lat": point.y, "lon": point.x}
    return wkb_element