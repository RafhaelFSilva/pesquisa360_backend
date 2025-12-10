# pesquisa360/crud.py

import json
import pandas as pd
import numpy as np
from typing import List

from sqlalchemy.orm import Session, joinedload 
from sqlalchemy import func, Text
from sqlalchemy.sql import text, bindparam # <-- ADICIONE ESTA LINHA
from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from .db import models
from . import schemas
from .core import security

# --- Funções CRUD para Usuário ---
def get_user_by_email(db: Session, email: str):
    return db.query(models.Usuario).filter(models.Usuario.email == email).first()

def create_user(db: Session, user: schemas.UsuarioCreate):
    hashed_password = security.get_password_hash(user.senha)
    db_user = models.Usuario(
        email=user.email, nome=user.nome,
        senha_hash=hashed_password, perfil_id=user.perfil_id
    )
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user

def get_users(db: Session, skip: int = 0, limit: int = 100):
    """Retorna uma lista de todos os usuários."""
    return db.query(models.Usuario).offset(skip).limit(limit).all()

# --- Funções CRUD para Perfil ---
def get_perfil(db: Session, perfil_id: int):
    return db.query(models.Perfil).filter(models.Perfil.id == perfil_id).first()

def get_perfil_by_name(db: Session, nome: str):
    return db.query(models.Perfil).filter(models.Perfil.nome == nome).first()

def create_perfil(db: Session, perfil: schemas.PerfilCreate):
    db_perfil = models.Perfil(nome=perfil.nome, descricao=perfil.descricao)
    db.add(db_perfil)
    db.commit()
    db.refresh(db_perfil)
    return db_perfil

# --- Funções CRUD para Projeto ---
def get_projeto(db: Session, projeto_id: int):
    return db.query(models.Projeto).filter(models.Projeto.id == projeto_id).first()

def get_projetos_by_coordenador(db: Session, coordenador_id: int, skip: int = 0, limit: int = 100):
    """Busca todos os projetos de um coordenador, exceto os excluídos."""
    return db.query(models.Projeto).filter(
        models.Projeto.coordenador_id == coordenador_id,
        models.Projeto.status != "Excluído" # <-- FILTRO ADICIONADO
    ).offset(skip).limit(limit).all()

def create_projeto(db: Session, projeto: schemas.ProjetoCreate, coordenador_id: int):
    db_projeto = models.Projeto(**projeto.model_dump(), coordenador_id=coordenador_id)
    db.add(db_projeto)
    db.commit()
    db.refresh(db_projeto)
    return db_projeto

def get_projetos_em_campo(db: Session, skip: int = 0, limit: int = 100):
    """Busca todos os projetos com status 'Em Campo'."""
    return db.query(models.Projeto).filter(models.Projeto.status == "Em Campo").offset(skip).limit(limit).all()

def delete_projeto(db: Session, *, db_obj: models.Projeto) -> models.Projeto:
    """Marca um projeto como 'Excluído' (soft delete)."""
    db_obj.status = "Excluído"
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

# --- Funções CRUD para Pesquisa ---
def get_pesquisa(db: Session, pesquisa_id: int):
    """Busca uma pesquisa pelo seu ID."""
    return db.query(models.Pesquisa).filter(models.Pesquisa.id == pesquisa_id).first()

def get_pesquisas_by_projeto(db: Session, projeto_id: int, skip: int = 0, limit: int = 100):
    """Busca todas as pesquisas ativas de um projeto específico."""
    return db.query(models.Pesquisa).filter(
        models.Pesquisa.projeto_id == projeto_id,
        models.Pesquisa.ativo == True  # <-- FILTRO ADICIONADO
    ).offset(skip).limit(limit).all()

def create_pesquisa_for_projeto(db: Session, pesquisa: schemas.PesquisaCreate, projeto_id: int):
    db_pesquisa = models.Pesquisa(**pesquisa.model_dump(), projeto_id=projeto_id)
    db.add(db_pesquisa)
    db.commit()
    db.refresh(db_pesquisa)
    return db_pesquisa

def update_pesquisa(db: Session, *, db_obj: models.Pesquisa, obj_in: schemas.PesquisaUpdate) -> models.Pesquisa:
    """Atualiza uma pesquisa."""
    update_data = obj_in.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(db_obj, field, value)
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def delete_pesquisa(db: Session, *, db_obj: models.Pesquisa) -> models.Pesquisa:
    """Marca uma pesquisa como inativa (soft delete)."""
    db_obj.ativo = False
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

# --- Funções CRUD para Geofence ---
def update_geofence_pesquisa(db: Session, *, db_obj: models.Pesquisa, obj_in: schemas.GeofenceUpdate) -> models.Pesquisa:
    """Atualiza a cerca eletrônica e a tolerância de uma pesquisa."""

    # Constrói a string do polígono no formato WKT (Well-Known Text)
    # Ex: POLYGON((lon1 lat1, lon2 lat2, lon3 lat3, lon1 lat1))
    coordenadas_str = ", ".join([f"{c.lng} {c.lat}" for c in obj_in.cerca_eletronica])

    # Garante que o polígono seja fechado, repetindo a primeira coordenada no final
    primeira_coordenada = f"{obj_in.cerca_eletronica[0].lng} {obj_in.cerca_eletronica[0].lat}"

    poligono_wkt = f"SRID=4326;POLYGON(({coordenadas_str}, {primeira_coordenada}))"

    db_obj.cerca_eletronica = poligono_wkt
    db_obj.tolerancia_metros = obj_in.tolerancia_metros

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

# --- Funções CRUD para Projeto ---

def update_projeto(db: Session, *, db_obj: models.Projeto, obj_in: schemas.ProjetoUpdate) -> models.Projeto:
    """Atualiza um projeto no banco de dados."""
    # Converte o schema Pydantic para um dicionário
    update_data = obj_in.model_dump(exclude_unset=True)

    # Itera sobre os dados recebidos e atualiza o objeto do banco
    for field, value in update_data.items():
        setattr(db_obj, field, value)

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

# --- Funções CRUD para Pergunta (CORRIGIDAS) ---

def create_pergunta_for_pesquisa(db: Session, pergunta: schemas.PerguntaCreate, pesquisa_id: int):
    # 1. Separa a lista de opções do objeto principal (pois 'opcoes' não é uma coluna simples de Pergunta)
    pergunta_data = pergunta.model_dump()
    opcoes_data = pergunta_data.pop("opcoes", None)

    # 2. Cria a Pergunta principal
    db_pergunta = models.Pergunta(**pergunta_data, pesquisa_id=pesquisa_id)
    db.add(db_pergunta)
    db.commit()
    db.refresh(db_pergunta)

    # 3. Se houver opções (Múltipla Escolha), salva elas na tabela 'opcoes'
    if opcoes_data:
        for opcao in opcoes_data:
            # Remove 'id' se vier no dict, para garantir criação de novo registro
            if isinstance(opcao, dict):
                opcao.pop("id", None) 
            # Cria a opção vinculada à pergunta recém-criada
            # O campo 'proxima_pergunta_id' será salvo automaticamente se estiver no dict 'opcao'
            db_opcao = models.Opcao(**opcao, pergunta_id=db_pergunta.id)
            db.add(db_opcao)
        
        db.commit()
        db.refresh(db_pergunta) # Atualiza para trazer as opções no relacionamento
    
    return db_pergunta

def update_pergunta(db: Session, *, db_obj: models.Pergunta, obj_in: schemas.PerguntaUpdate) -> models.Pergunta:
    """Atualiza uma pergunta e suas opções (incluindo Lógica de Pulo)."""
    
    # 1. Separa os dados de atualização
    update_data = obj_in.model_dump(exclude_unset=True)
    opcoes_data = update_data.pop("opcoes", None)

    # 2. Atualiza os campos simples da pergunta
    for field, value in update_data.items():
        setattr(db_obj, field, value)

    # 3. Atualiza as Opções (Estratégia: Full Replace)
    if opcoes_data is not None:
        # A. Remove opções antigas
        db.query(models.Opcao).filter(models.Opcao.pergunta_id == db_obj.id).delete()
        
        # B. Cria novas opções
        for opcao in opcoes_data:
            if isinstance(opcao, dict):
                opcao.pop("id", None) # Remove ID antigo
                opcao.pop("pergunta_id", None) # <--- CORREÇÃO CRÍTICA: Remove duplicidade
                
            # Cria a opção vinculada
            db_opcao = models.Opcao(**opcao, pergunta_id=db_obj.id)
            db.add(db_opcao)

    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

def get_perguntas_by_pesquisa(db: Session, pesquisa_id: int):
    """Retorna todas as perguntas ativas de uma pesquisa."""
    return (
        db.query(models.Pergunta)
        .filter(
            models.Pergunta.pesquisa_id == pesquisa_id,
            models.Pergunta.ativo == True
        )
        .order_by(models.Pergunta.ordem.asc())
        .all()
    )

# CRUD DELETE, SOFT DELETE
def delete_pergunta(db: Session, *, db_obj: models.Pergunta) -> models.Pergunta:
    """Marca uma pergunta como inativa (soft delete)."""
    db_obj.ativo = False
    db.add(db_obj)
    db.commit()
    db.refresh(db_obj)
    return db_obj

# ADICIONE TAMBÉM UMA FUNÇÃO PARA BUSCAR UMA PERGUNTA ESPECÍFICA
def get_pergunta(db: Session, pergunta_id: int):
    """Busca uma pergunta pelo seu ID."""
    return db.query(models.Pergunta).filter(models.Pergunta.id == pergunta_id).first()

# --- Funções CRUD para Coleta e Respostas ---
def get_coletas_for_monitoring_by_pesquisa(db: Session, pesquisa_id: int) -> List[schemas.ColetaMonitoramento]:
    """
    Busca todas as coletas de uma pesquisa específica, formatando-as para o
    schema de monitoramento usando as funções do PostGIS para converter a geometria.
    """
    coletas_db = (
        db.query(models.Coleta)
        .options(joinedload(models.Coleta.agente))
        .filter(models.Coleta.pesquisa_id == pesquisa_id)
        .order_by(models.Coleta.data_fim_coleta.asc())
        .all()
    )

    resultado_final = []
    for coleta in coletas_db:
        # Converte a geometria usando a função ST_AsGeoJSON do PostGIS
        loc_inicio_json = db.query(func.ST_AsGeoJSON(coleta.localizacao_inicio)).scalar() if coleta.localizacao_inicio is not None else None
        loc_fim_json = db.query(func.ST_AsGeoJSON(coleta.localizacao_fim)).scalar() if coleta.localizacao_fim is not None else None

        # O GeoJSON de um ponto é: {"type":"Point","coordinates":[-51.0694,0.0349]}
        # Precisamos extrair as coordenadas [lon, lat]
        coords_inicio = json.loads(loc_inicio_json)['coordinates'] if loc_inicio_json else [None, None]
        coords_fim = json.loads(loc_fim_json)['coordinates'] if loc_fim_json else [None, None]

        # Cria o objeto Pydantic final
        resultado_final.append(
            schemas.ColetaMonitoramento(
                id=coleta.id,
                agente_id=coleta.agente_id,
                agente_nome=coleta.agente.nome,
                data_inicio_coleta=coleta.data_inicio_coleta,
                data_fim_coleta=coleta.data_fim_coleta,
                localizacao_inicio=schemas.Point(lon=coords_inicio[0], lat=coords_inicio[1]) if coords_inicio[0] is not None else None,
                localizacao_fim=schemas.Point(lon=coords_fim[0], lat=coords_fim[1]) if coords_fim[0] is not None else None,
                inconformidade_localizacao=coleta.inconformidade_localizacao,
            )
        )
    return resultado_final

# --- FUNÇÃO DE COLETA ATUALIZADA COM VALIDAÇÃO DE GEOFENCE ---
def create_coleta(
    db: Session, coleta: schemas.ColetaCreate, pesquisa_id: int, agente_id: int
):
    """Cria uma nova coleta e suas respostas, validando contra a cerca eletrônica."""
    pesquisa = get_pesquisa(db=db, pesquisa_id=pesquisa_id)
    if not pesquisa:
        # Esta verificação é redundante se o endpoint já verifica, mas é uma boa prática.
        raise ValueError("Pesquisa não encontrada")

    inconformidade = False

    # 1. Converte a localização inicial para o formato do banco
    localizacao_inicio_wkt = (
        f"SRID=4326;POINT({coleta.localizacao_inicio.lon} {coleta.localizacao_inicio.lat})"
        if coleta.localizacao_inicio else None
    )

    # 2. Lógica de Validação do Geofence
    if pesquisa.cerca_eletronica is not None and localizacao_inicio_wkt is not None:
        # Usa a função ST_DWithin do PostGIS.
        # Ela verifica se a distância entre a geometria da coleta e a cerca é menor ou igual à tolerância.
        # Se a consulta retornar 'false', significa que o ponto está FORA da área permitida.
        esta_dentro = db.scalar(
            text("""
                SELECT ST_DWithin(
                    ST_GeogFromText(:coleta),
                    ST_GeogFromText(:cerca),
                    :tolerancia
                )
            """).params(
                coleta=localizacao_inicio_wkt,
                cerca=f"SRID=4326;{cerca_wkt}",
                tolerancia=pesquisa.tolerancia_metros
            )
        )
        if not esta_dentro:
            inconformidade = True

    localizacao_fim_wkt = (
        f"SRID=4326;POINT({coleta.localizacao_fim.lon} {coleta.localizacao_fim.lat})"
        if coleta.localizacao_fim else None
    )

    # 3. Cria o objeto principal da Coleta, já com o status de inconformidade
    db_coleta = models.Coleta(
        pesquisa_id=pesquisa_id,
        agente_id=agente_id,
        localizacao_inicio=localizacao_inicio_wkt,
        localizacao_fim=localizacao_fim_wkt,
        inconformidade_localizacao=inconformidade
    )
    db.add(db_coleta)
    db.flush()

    for resposta in coleta.respostas:
        db_resposta = models.Resposta(**resposta.model_dump(), coleta_id=db_coleta.id)
        db.add(db_resposta)

    db.commit()
    db.refresh(db_coleta)
    return db_coleta                                                        

    """Cria uma nova coleta e todas as suas respostas associadas."""

    # Sua função de conversão, que está correta
    def make_geom(point):
        """Converte Point(lat, lon) para Geometry SRID=4326."""
        if point and point.lat is not None and point.lon is not None:
            return from_shape(Point(point.lon, point.lat), srid=4326)
        return None

    localizacao_inicio_geom = make_geom(coleta.localizacao_inicio)
    localizacao_fim_geom = make_geom(coleta.localizacao_fim)

    # Cria o objeto principal da Coleta
    db_coleta = models.Coleta(
        pesquisa_id=pesquisa_id,
        agente_id=agente_id,
        localizacao_inicio=localizacao_inicio_geom,
        localizacao_fim=localizacao_fim_geom,
    )
    db.add(db_coleta)
    db.flush()

    # Itera sobre as respostas recebidas
    for resposta in coleta.respostas:
        db_resposta = models.Resposta(
            **resposta.model_dump(),
            coleta_id=db_coleta.id
        )
        db.add(db_resposta)

    db.commit()
    db.refresh(db_coleta)
    return db_coleta

# --- FUNÇÃO DE ANÁLISE (Fase 5) ---

def get_relatorio_pesquisa(db: Session, pesquisa_id: int):
    pesquisa = get_pesquisa(db=db, pesquisa_id=pesquisa_id)
    if not pesquisa:
        return None
    total_coletas = db.query(models.Coleta).filter(models.Coleta.pesquisa_id == pesquisa_id).count()
    resultados_por_pergunta = []
    for pergunta in pesquisa.perguntas:
        respostas_db = db.query(models.Resposta.valor_resposta).filter(models.Resposta.pergunta_id == pergunta.id).all()
        lista_respostas = [r[0] for r in respostas_db]
        df_contagem = pd.DataFrame(lista_respostas, columns=['resposta'])
        contagens = df_contagem['resposta'].value_counts().reset_index()
        contagens.columns = ['opcao', 'contagem']
        resultados_formatados = [schemas.ResultadoOpcao(opcao=row['opcao'], contagem=row['contagem']) for index, row in contagens.iterrows()]
        
        stats = {}
        if pergunta.tipo_pergunta in ["Numero", "Escala"] and lista_respostas:
            try:
                # CORREÇÃO AQUI: Garante que estamos trabalhando com uma Series do Pandas
                dados_numericos_brutos = pd.Series(pd.to_numeric(lista_respostas, errors='coerce'))
                dados_numericos = dados_numericos_brutos.dropna() # Agora o .dropna() vai funcionar

                if not dados_numericos.empty:
                    stats['media'] = round(dados_numericos.mean(), 2)
                    stats['mediana'] = round(dados_numericos.median(), 2)
                    moda_result = dados_numericos.mode()
                    stats['moda'] = moda_result.iloc[0] if not moda_result.empty else None
                    stats['desvio_padrao'] = round(dados_numericos.std(), 2)
            except Exception as e:
                print(f"Erro ao calcular estatísticas para a pergunta {pergunta.id}: {e}")

        # Pydantic V2 prefere que passemos um dicionário para construir o objeto
        resultados_por_pergunta.append({
            "pergunta_id": pergunta.id,
            "texto_pergunta": pergunta.texto_pergunta,
            "tipo_pergunta": pergunta.tipo_pergunta,
            "resultados": resultados_formatados,
            **stats
        })

    relatorio_final = schemas.RelatorioPesquisa(
        pesquisa_id=pesquisa.id,
        titulo_pesquisa=pesquisa.titulo,
        total_coletas=total_coletas,
        resultados_por_pergunta=resultados_por_pergunta,
    )
    return relatorio_final

def get_relatorio_crosstab(db: Session, *, pesquisa_id: int, pergunta_linha_id: int, pergunta_coluna_id: int):
    """
    Gera um relatório de cruzamento de dados (crosstab) entre duas perguntas.
    """
    # 1. Busca todas as respostas da pesquisa
    respostas = db.query(models.Resposta).join(models.Coleta).filter(models.Coleta.pesquisa_id == pesquisa_id).all()
    if not respostas:
        return None

    # 2. Converte os dados para um DataFrame do Pandas
    dados_lista = [
        {"coleta_id": r.coleta_id, "pergunta_id": r.pergunta_id, "resposta": r.valor_resposta}
        for r in respostas
    ]
    df_respostas = pd.DataFrame(dados_lista)

    # 3. "Pivota" o DataFrame para que cada linha seja uma coleta e cada coluna uma pergunta
    df_pivot = df_respostas.pivot_table(index='coleta_id', columns='pergunta_id', values='resposta', aggfunc='first').reset_index()

    # 4. Busca os textos das perguntas para usar nos cabeçalhos
    pergunta_linha = db.query(models.Pergunta).filter(models.Pergunta.id == pergunta_linha_id).first()
    pergunta_coluna = db.query(models.Pergunta).filter(models.Pergunta.id == pergunta_coluna_id).first()

    if not pergunta_linha or not pergunta_coluna:
        raise HTTPException(status_code=404, detail="Uma das perguntas não foi encontrada")

    # 5. Executa a mágica: o cruzamento de dados com o Pandas
    #    Isso cria uma tabela de contagem entre as respostas das duas perguntas
    tabela_cruzada = pd.crosstab(df_pivot[pergunta_linha_id], df_pivot[pergunta_coluna_id])

    # 6. Formata o resultado para o nosso schema de resposta da API
    dados_formatados = []
    for index, row in tabela_cruzada.iterrows():
        celulas = []
        for col_name, contagem in row.items():
            percentual = (contagem / row.sum()) * 100 if row.sum() > 0 else 0
            celulas.append(
                schemas.CrosstabCell(
                    valor_coluna=str(col_name),
                    contagem=int(contagem),
                    percentual=round(percentual, 2)
                )
            )
        dados_formatados.append(
            schemas.CrosstabRow(valor_linha=str(index), celulas=celulas)
        )

    return schemas.CrosstabResponse(
        pergunta_linha=pergunta_linha.texto_pergunta,
        pergunta_coluna=pergunta_coluna.texto_pergunta,
        dados=dados_formatados
    )

def create_setor(db: Session, setor_in: schemas.SetorCreate, pesquisa_id: int):
    # 1. Monta o WKT (Well-Known Text) do Polígono
    # Formato: POLYGON((x y, x y, x y...))
    coords = setor_in.geometria_coords
    # Fecha o polígono repetindo o primeiro ponto se necessário
    if coords[0] != coords[-1]:
        coords.append(coords[0])
        
    coords_str = ", ".join([f"{p[1]} {p[0]}" for p in coords]) # PostGIS usa Lon Lat
    wkt = f"POLYGON(({coords_str}))"

    db_setor = models.Setor(
        nome=setor_in.nome,
        meta=setor_in.meta,
        pesquisa_id=pesquisa_id,
        agente_id=setor_in.agente_id,
        tolerancia=setor_in.tolerancia,
        geometria=func.ST_GeomFromText(wkt, 4326)
    )
    db.add(db_setor)
    db.commit()
    db.refresh(db_setor)
    return db_setor

def get_setores_by_pesquisa(db: Session, pesquisa_id: int):
    # Retorna os setores convertendo a geometria para GeoJSON
    return db.query(
        models.Setor.id,
        models.Setor.nome,
        models.Setor.meta,
        models.Setor.agente_id,
        models.Setor.pesquisa_id,
        func.ST_AsGeoJSON(models.Setor.geometria).label("geometria_geojson")
    ).filter(models.Setor.pesquisa_id == pesquisa_id).all()

def delete_setor(db: Session, setor_id: int):
    setor = db.query(models.Setor).filter(models.Setor.id == setor_id).first()
    if setor:
        db.delete(setor)
        db.commit()
    return setor