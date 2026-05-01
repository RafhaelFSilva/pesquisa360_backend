# pesquisa360/utils/geocoding.py
from geopy.geocoders import Nominatim
from functools import lru_cache

@lru_cache(maxsize=128)
def obter_endereco_por_coords(lat: float, lon: float) -> str:
    try:
        geolocator = Nominatim(user_agent="pesquisa360_system")
        location = geolocator.reverse(f"{lat}, {lon}", timeout=5, language='pt-br')
        if location:
            return location.address
    except Exception as e:
        print(f"Erro geocoding: {e}")
    return "Endereço não identificado"