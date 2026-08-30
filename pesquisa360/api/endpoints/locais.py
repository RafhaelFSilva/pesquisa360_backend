from fastapi import APIRouter, Depends, UploadFile, File, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import text
from sqlalchemy import func
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from pesquisa360.core.dependencies import get_db, get_current_user
from pesquisa360.core.utils import geojson_point
from pesquisa360.db import models
from pesquisa360.utils.importadores import processar_csv_locais, processar_geojson_bairros
from pesquisa360.core.rbac import Permissao, require_permissao
import shutil
import os

router = APIRouter()

@router.post("/upload-csv/", dependencies=[Depends(require_permissao(Permissao.TERRITORIO_GERENCIAR))])
async def upload_locais_votacao(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    # Regra de Multitenancia: Usa a company do usuário logado
    company_id = current_user.company_id 
    
    # Salva arquivo temporário
    temp_path = f"static/uploads/temp_{file.filename}"
    with open(temp_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    try:
        processar_csv_locais(db, temp_path, company_id)
        return {"message": "Locais e comportamento de urnas importados com sucesso!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao processar CSV: {str(e)}")
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

@router.post("/bairros/upload-geojson/", dependencies=[Depends(require_permissao(Permissao.TERRITORIO_GERENCIAR))])
async def upload_bairros_geojson(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    # Regra de Multitenancia: Usa a company do usuário logado
    company_id = current_user.company_id 
    
    # Salva o GeoJSON temporariamente
    temp_path = f"static/uploads/temp_{file.filename}"
    with open(temp_path, "wb") as buffer:
        import shutil # Garanta que shutil está importado no topo do arquivo
        shutil.copyfileobj(file.file, buffer)
    
    try:
        processar_geojson_bairros(db, temp_path, company_id)
        return {"message": "Polígonos dos bairros importados e convertidos com sucesso!"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao processar GeoJSON: {str(e)}")
    finally:
        import os
        if os.path.exists(temp_path):
            os.remove(temp_path)

# Adicione no topo se faltar: from sqlalchemy import text

@router.get("/geojson/pontos", summary="Retorna os Locais de Votação (Pinos)", dependencies=[Depends(require_permissao(Permissao.RELATORIO_VER))])
def get_locais_geojson(
    db: Session = Depends(get_db), 
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Usa o PostGIS para gerar um GeoJSON ultrarrápido com todos os locais de votação.
    """
    query = """
        SELECT jsonb_build_object(
            'type',     'FeatureCollection',
            'features', coalesce(jsonb_agg(features.feature), '[]'::jsonb)
        )
        FROM (
            SELECT jsonb_build_object(
                'type',       'Feature',
                'id',         id,
                'geometry',   ST_AsGeoJSON(localizacao)::jsonb,
                'properties', to_jsonb(inputs) - 'id' - 'localizacao' - 'company_id'
            ) AS feature
            FROM (
                SELECT id, nome, zona, secoes, municipio, bairro, endereco, localizacao, company_id
                FROM locais_votacao
                WHERE company_id = :company_id AND localizacao IS NOT NULL
            ) inputs
        ) features;
    """
    resultado = db.execute(text(query), {"company_id": current_user.company_id}).scalar()
    return resultado

@router.get("/geojson/bairros", summary="Retorna os Bairros (Polígonos)", dependencies=[Depends(require_permissao(Permissao.RELATORIO_VER))])
def get_bairros_geojson(
    db: Session = Depends(get_db), 
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Usa o PostGIS para gerar um GeoJSON ultrarrápido com as camadas dos bairros.
    """
    query = """
        SELECT jsonb_build_object(
            'type',     'FeatureCollection',
            'features', coalesce(jsonb_agg(features.feature), '[]'::jsonb)
        )
        FROM (
            SELECT jsonb_build_object(
                'type',       'Feature',
                'id',         id,
                'geometry',   ST_AsGeoJSON(geometria)::jsonb,
                'properties', to_jsonb(inputs) - 'id' - 'geometria' - 'company_id'
            ) AS feature
            FROM (
                SELECT id, nome, area_ha, populacao, eleitores, geometria, company_id
                FROM bairros
                WHERE company_id = :company_id
            ) inputs
        ) features;
    """
    resultado = db.execute(text(query), {"company_id": current_user.company_id}).scalar()
    return resultado

@router.get("/bairros/peso-eleitoral", summary="Ranking de Peso Eleitoral por Bairro", dependencies=[Depends(require_permissao(Permissao.RELATORIO_VER))])
def get_peso_eleitoral(
    db: Session = Depends(get_db), 
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Retorna o total de eleitores do município e o ranking percentual de cada bairro.
    """
    company_id = current_user.company_id
    
    # 1. Calcula o total absoluto de eleitores daquele cliente (Tenant)
    total_eleitores = db.query(func.sum(models.Bairro.eleitores))\
                        .filter(models.Bairro.company_id == company_id).scalar()
                        
    # Proteção contra divisão por zero caso o banco esteja vazio
    total_eleitores = total_eleitores or 1 

    # 2. Busca todos os bairros
    bairros = db.query(models.Bairro).filter(models.Bairro.company_id == company_id).all()

    # 3. Monta o ranking calculando o percentual
    ranking = []
    for b in bairros:
        # Se o bairro não tiver eleitores cadastrados, assume 0
        qtd = b.eleitores or 0 
        peso_percentual = (qtd / total_eleitores) * 100
        
        ranking.append({
            "bairro_id": b.id,
            "nome": b.nome,
            "eleitores": qtd,
            "peso_percentual": round(peso_percentual, 2)
        })

    # 4. Ordena do bairro mais pesado para o mais leve
    ranking.sort(key=lambda x: x["peso_percentual"], reverse=True)

    return {
        "total_eleitores_municipio": total_eleitores if total_eleitores > 1 else 0,
        "ranking": ranking
    }

@router.post("/dev/seed-coletas", summary="[DEV] Gerar Coletas Falsas no Mapa", dependencies=[Depends(require_permissao(Permissao.TERRITORIO_GERENCIAR))])
def seed_coletas_mock(
    qtd: int = 500,
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    """
    Gera coletas fictícias espalhadas por Santana-AP com intenções de voto viciadas
    para simularmos o Mapa de Calor no Front-end.
    """
    import random
    from datetime import datetime, timedelta
    from geoalchemy2.shape import from_shape
    from shapely.geometry import Point
    
    # Limites geográficos aproximados de Santana - AP
    MIN_LON, MAX_LON = -51.190, -51.150
    MIN_LAT, MAX_LAT = -0.060, -0.020

    candidatos = ["Candidato A", "Candidato B", "Candidato C", "Branco/Nulo"]
    pesos = [0.40, 0.35, 0.15, 0.10] 

    # 1. Navegação na hierarquia do Banco de Dados
    projeto = db.query(models.Projeto).filter(models.Projeto.company_id == current_user.company_id).first()
    if not projeto:
        raise HTTPException(status_code=400, detail="Crie um Projeto primeiro para o seu usuário.")

    pesquisa = db.query(models.Pesquisa).filter(models.Pesquisa.projeto_id == projeto.id).first()
    if not pesquisa:
        raise HTTPException(status_code=400, detail="Crie uma Pesquisa dentro do projeto.")

    pergunta = db.query(models.Pergunta).filter(models.Pergunta.pesquisa_id == pesquisa.id).first()
    if not pergunta:
        raise HTTPException(status_code=400, detail="Crie uma Pergunta dentro da pesquisa.")

    coletas_criadas = 0
    for _ in range(qtd):
        # Gera coordenada aleatória dentro do município
        lon = random.uniform(MIN_LON, MAX_LON)
        lat = random.uniform(MIN_LAT, MAX_LAT)
        ponto_gps = from_shape(Point(lon, lat), srid=4326)

        # Simula a data_inicio nos últimos 7 dias
        data_inicio = datetime.now() - timedelta(days=random.randint(0, 7), hours=random.randint(0, 23))
        # Simula que a entrevista durou entre 5 e 15 minutos
        data_fim = data_inicio + timedelta(minutes=random.randint(5, 15))

        # 2. Cria a Coleta com mapeamento EXATO do models.py
        nova_coleta = models.Coleta(
            data_inicio_coleta=data_inicio,      # CORRIGIDO
            data_fim_coleta=data_fim,            # CORRIGIDO
            localizacao_inicio=ponto_gps,        # CORRIGIDO
            localizacao_fim=ponto_gps,           # CORRIGIDO (Considerando que ele não andou)
            status_sincronizacao="sincronizado",
            inconformidade_localizacao=False,
            foi_offline=False,                   # ADICIONADO
            pesquisa_id=pesquisa.id,
            agente_id=current_user.id
        )
        db.add(nova_coleta)
        db.flush() # Salva temporariamente para pegar o ID

        # 3. Cria a Resposta (Intenção de Voto)
        nova_resposta = models.Resposta(
            valor_resposta=random.choices(candidatos, weights=pesos)[0],
            pergunta_id=pergunta.id,
            coleta_id=nova_coleta.id
        )
        db.add(nova_resposta)
        coletas_criadas += 1

    db.commit()
    return {"message": f"✅ {coletas_criadas} coletas falsas geradas e espalhadas pelo mapa com sucesso!"}

@router.get("/coletas/geojson", summary="Camada de Coletas de Campo (Mapa)", dependencies=[Depends(require_permissao(Permissao.RELATORIO_VER))])
def get_coletas_geojson(
    db: Session = Depends(get_db),
    current_user: models.Usuario = Depends(get_current_user)
):
    from sqlalchemy import func
    
    # Busca a pesquisa ativa do Tenant
    projeto = db.query(models.Projeto).filter(models.Projeto.company_id == current_user.company_id).first()
    if not projeto: return {"type": "FeatureCollection", "features": []}
    
    pesquisa = db.query(models.Pesquisa).filter(models.Pesquisa.projeto_id == projeto.id).first()
    if not pesquisa: return {"type": "FeatureCollection", "features": []}

    # A Mágica do PostGIS: Extraindo Longitude (ST_X) e Latitude (ST_Y) diretamente e unindo com o Voto
    resultados = db.query(
        models.Coleta.id,
        func.ST_X(models.Coleta.localizacao_inicio).label('lon'),
        func.ST_Y(models.Coleta.localizacao_inicio).label('lat'),
        models.Resposta.valor_resposta
    ).join(
        models.Resposta, models.Resposta.coleta_id == models.Coleta.id
    ).filter(
        models.Coleta.pesquisa_id == pesquisa.id,
        models.Coleta.localizacao_inicio != None
    ).all()

    features = []
    for r in resultados:
        features.append({
            "type": "Feature",
            "geometry": geojson_point(longitude=r.lon, latitude=r.lat),
            "properties": {
                "id": r.id,
                "voto": r.valor_resposta
            }
        })

    return {"type": "FeatureCollection", "features": features}
