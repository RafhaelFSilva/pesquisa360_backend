import pandas as pd
import json
from shapely.geometry import shape, MultiPolygon, Polygon
from shapely.ops import transform
from sqlalchemy.orm import Session
from pesquisa360.db import models
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from pyproj import Transformer
import math

transformer = Transformer.from_crs("epsg:31982", "epsg:4326", always_xy=True)

def safe_int(val, default=0):
    """Tenta converter um valor para inteiro de forma segura (ignora NaN)."""
    try:
        if pd.isna(val):
            return default
        return int(float(val))
    except (ValueError, TypeError):
        return default

def processar_csv_locais(db: Session, file_path: str, company_id: int):
    db.query(models.LocalVotacao).filter(models.LocalVotacao.company_id == company_id).delete()
    db.commit()

    df = pd.read_csv(file_path, sep=None, engine='python')
    
    col_x = next((c for c in df.columns if str(c).upper() in ['X', 'LONGITUDE', 'LONG', 'LON', 'COORD_X']), None)
    col_y = next((c for c in df.columns if str(c).upper() in ['Y', 'LATITUDE', 'LAT', 'COORD_Y']), None)

    for index, row in df.iterrows():
        ponto_geografico = None
        if col_x and col_y:
            try:
                val_x = str(row[col_x]).replace(',', '.').strip()
                val_y = str(row[col_y]).replace(',', '.').strip()
                
                if val_x not in ['nan', 'None', ''] and val_y not in ['nan', 'None', '']:
                    vx, vy = float(val_x), float(val_y)
                    
                    # 🚀 AUTO-CORREÇÃO DE UTM SEM FALSO NORTHING (HEMISFÉRIO SUL)
                    # Se o Y for um valor negativo pequeno (ex: -3000), significa que o 
                    # software que gerou o CSV exportou os metros a sul do Equador como negativos,
                    # esquecendo-se de somar os 10.000.000 exigidos pelo padrão UTM 22S.
                    if vy < 0 and vy > -100000: 
                        vy = 10000000 + vy  # Ex: 10000000 + (-3377) = 9996623 (Correção do False Northing)
                        
                    # Auto-Correção X e Y trocados
                    if vx > 1000000 and vy < 1000000:
                        vx, vy = vy, vx
                    
                    # Transformação
                    if -180 <= vx <= 180 and -90 <= vy <= 90:
                        if vx > vy: # Auto-correção para Lat/Long (X deve ser menor que Y no Amapá)
                            vx, vy = vy, vx
                        lon, lat = vx, vy
                    else: 
                        lon, lat = transformer.transform(vx, vy)
                    
                    # Se mesmo assim der fora do Brasil, tenta inverter para garantir
                    if not (-75 < lon < -30):
                        lon, lat = transformer.transform(vy, vx)

                    if math.isfinite(lon) and math.isfinite(lat):
                        ponto_geografico = from_shape(Point(lon, lat), srid=4326)
            except Exception as e:
                pass # Continua para a próxima linha

        # Montagem das seções
        lista_secoes = []
        for i in range(1, 12):
            col_s = f"Secao{i}" if i != 10 else "10Secao"
            col_v = f"Qtd_Votos{i}" if i < 3 else (f"Votos_sec{i}" if i != 10 else "votos_sec1")
            
            if col_s in row and pd.notna(row[col_s]):
                num_secao = safe_int(row[col_s])
                if num_secao > 0:
                    votos_secao = safe_int(row[col_v]) if col_v in row else 0
                    lista_secoes.append({"num": num_secao, "votos": votos_secao})

        novo_local = models.LocalVotacao(
            nome=str(row.get('Nome', 'Local Sem Nome')) if pd.notna(row.get('Nome')) else "Local Sem Nome",
            zona=safe_int(row.get('Zona', 0)),
            municipio=str(row.get('Municipio', '')) if pd.notna(row.get('Municipio')) else "",
            bairro=str(row.get('Bairro', '')) if pd.notna(row.get('Bairro')) else "",
            endereco=str(row.get('Endereco', '')) if pd.notna(row.get('Endereco')) else "",
            secoes=lista_secoes,
            localizacao=ponto_geografico,
            company_id=company_id
        )
        db.add(novo_local)
    
    db.commit()

def processar_geojson_bairros(db: Session, file_path: str, company_id: int):
    """Lê um GeoJSON, converte as coordenadas de UTM para WGS84 e salva os polígonos."""
    
    with open(file_path, 'r', encoding='utf-8') as f:
        geojson_data = json.load(f)
        
    for feature in geojson_data.get('features', []):
        props = feature.get('properties', {})
        geom_geojson = feature.get('geometry')
        
        if not geom_geojson:
            continue
            
        # 1. Converte a geometria do formato GeoJSON (dicionário) para objeto Shapely
        shapely_geom = shape(geom_geojson)
        
        # 2. Aplica a transformação matemática (UTM 22S -> WGS84) em todos os vértices do polígono
        geom_wgs84 = transform(transformer.transform, shapely_geom)
        
        # 3. O banco de dados exige estritamente MULTIPOLYGON (pois alguns bairros podem ter ilhas)
        # Se o Shapely gerou um Polygon simples, nós o "empacotamos" em um MultiPolygon
        if isinstance(geom_wgs84, Polygon):
            geom_wgs84 = MultiPolygon([geom_wgs84])
            
        # 4. Extrai as propriedades buscando pelas chaves exatas que vieram no seu arquivo
        nome_bairro = props.get('BAIRRO') or props.get('LAYER') or "Bairro Sem Nome"
        area_ha = props.get('AREA_HA')
        populacao = props.get('POPULAÇÃ') # Notei que a string está sem o 'O' final no seu arquivo
        eleitores = props.get('ELEITORES')
        
        # 5. Persiste no banco de dados isolando pelo Tenant (company_id)
        novo_bairro = models.Bairro(
            nome=str(nome_bairro),
            area_ha=float(area_ha) if area_ha is not None else None,
            populacao=int(populacao) if populacao is not None else None,
            eleitores=int(eleitores) if eleitores is not None else None,
            geometria=from_shape(geom_wgs84, srid=4326),
            company_id=company_id
        )
        db.add(novo_bairro)
        
    db.commit()