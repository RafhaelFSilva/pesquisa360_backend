"""Mapa de Respostas Georreferenciadas (Fase 2A).

Cada coleta vira um ponto categorizado pela resposta a uma pergunta principal.
Estes testes cobrem a semantica dos filtros (OR interno, AND entre dimensoes),
o ponto canonico, multitenancy e os limites declarados do MVP.

Cobrem tambem o modo CRUZADO (`pergunta_secundaria_id`): o par nasce sempre do
mesmo `coleta_id`, e a ausencia do parametro mantem a resposta identica.
"""

import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely import wkt as shapely_wkt
from shapely.geometry import mapping
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-mapa-respostas-geo-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import relatorios
from pesquisa360.core.dependencies import get_current_user, get_db

ROTA = "/relatorios/pesquisas/{}/mapas/respostas-georreferenciadas/"

# Perguntas da fixture.
VOTO, SEXO, FAIXA, AVALIACAO, MULTIPLA, TEXTO_LIVRE = 100, 101, 102, 103, 104, 105
# Respondida por apenas 3 coletas: expoe o caso "sem par" do cruzamento.
SEGUNDO_TURNO = 106
CANDIDATOS = ["Candidato A", "Candidato B", "Candidato C", "Candidato D"]


def user(user_id: int = 1, company_id: int = 10):
    return SimpleNamespace(
        id=user_id,
        company_id=company_id,
        ativo=True,
        perfil=SimpleNamespace(nome="Gerente"),
    )


@pytest.fixture
def database():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    @event.listens_for(engine, "connect")
    def register_spatial_functions(connection, _):
        def geom(value):
            if value is None:
                return None
            if isinstance(value, bytes):
                value = value.decode()
            return shapely_wkt.loads(str(value).split(";", 1)[-1])

        def as_geojson(value):
            return json.dumps(mapping(geom(value))) if value else None

        connection.create_function("AsEWKB", 1, lambda value: value)
        connection.create_function("ST_GeomFromText", 2, lambda value, srid: value)
        connection.create_function("ST_AsGeoJSON", 1, as_geojson)
        connection.create_function("AsGeoJSON", 1, as_geojson)
        connection.create_function(
            "ST_Covers",
            2,
            lambda polygon, point: int(geom(polygon).covers(geom(point)))
            if polygon and point
            else 0,
        )
        connection.create_function("ST_X", 1, lambda value: geom(value).x if value else None)
        connection.create_function("ST_Y", 1, lambda value: geom(value).y if value else None)

    Session = sessionmaker(bind=engine)
    with engine.begin() as connection:
        connection.execute(text("""
            CREATE TABLE companies (
                id INTEGER PRIMARY KEY, name TEXT, cnpj TEXT, logo_url TEXT,
                is_active BOOLEAN, created_at DATETIME
            )
        """))
        connection.execute(text(
            "CREATE TABLE perfis (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT)"
        ))
        connection.execute(text("""
            CREATE TABLE usuarios (
                id INTEGER PRIMARY KEY, email TEXT, nome TEXT, senha_hash TEXT,
                ativo BOOLEAN, perfil_id INTEGER, company_id INTEGER
            )
        """))
        connection.execute(text("""
            CREATE TABLE projetos (
                id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT, status TEXT,
                data_inicio DATE, data_fim DATE, coordenador_id INTEGER, company_id INTEGER
            )
        """))
        connection.execute(text("""
            CREATE TABLE pesquisas (
                id INTEGER PRIMARY KEY, titulo TEXT, tipo_pesquisa TEXT, ativo BOOLEAN,
                projeto_id INTEGER, cerca_eletronica TEXT, tolerancia_metros INTEGER
            )
        """))
        connection.execute(text("""
            CREATE TABLE perguntas (
                id INTEGER PRIMARY KEY, texto_pergunta TEXT, tipo_pergunta TEXT,
                ordem INTEGER, eh_obrigatoria BOOLEAN, eh_resposta_espontanea BOOLEAN,
                papel_analitico VARCHAR(50), metadados_analiticos JSON NOT NULL DEFAULT '{}',
                ativo BOOLEAN, pesquisa_id INTEGER
            )
        """))
        connection.execute(text("""
            CREATE TABLE coletas (
                id INTEGER PRIMARY KEY, pesquisa_id INTEGER, agente_id INTEGER,
                company_id INTEGER, client_uuid TEXT, foi_offline BOOLEAN,
                endereco_estimado TEXT, status_sincronizacao TEXT,
                data_inicio_coleta DATETIME, data_fim_coleta DATETIME,
                localizacao_inicio TEXT, localizacao_fim TEXT,
                inconformidade_localizacao BOOLEAN
            )
        """))
        connection.execute(text("""
            CREATE TABLE respostas (
                id INTEGER PRIMARY KEY, pergunta_id INTEGER, coleta_id INTEGER,
                valor_resposta TEXT
            )
        """))
        connection.execute(text("""
            CREATE TABLE categorias_resposta_espontanea (
                id INTEGER PRIMARY KEY, pesquisa_id INTEGER, nome TEXT,
                nome_normalizado TEXT, ativo BOOLEAN, criado_por_id INTEGER,
                atualizado_por_id INTEGER, criado_em DATETIME, atualizado_em DATETIME
            )
        """))
        connection.execute(text("""
            CREATE TABLE mapeamentos_resposta_espontanea (
                id INTEGER PRIMARY KEY, pesquisa_id INTEGER, categoria_id INTEGER,
                chave_normalizada TEXT, texto_referencia TEXT, ativo BOOLEAN,
                criado_por_id INTEGER, atualizado_por_id INTEGER,
                criado_em DATETIME, atualizado_em DATETIME
            )
        """))
        connection.execute(text("""
            CREATE TABLE setores (
                id INTEGER PRIMARY KEY, nome TEXT, meta INTEGER, tolerancia INTEGER,
                finalidade TEXT DEFAULT 'OPERACAO' NOT NULL,
                geometria TEXT, pesquisa_id INTEGER, agente_id INTEGER
            )
        """))

        connection.execute(text(
            "INSERT INTO companies VALUES (10,'A',NULL,NULL,1,NULL),(20,'B',NULL,NULL,1,NULL)"
        ))
        connection.execute(text("INSERT INTO perfis VALUES (1,'Gerente',NULL),(2,'Agente',NULL)"))
        connection.execute(text("""
            INSERT INTO usuarios VALUES
              (1,'manager@a','Manager A','x',1,1,10),
              (2,'agent1@a','Agente 1','x',1,2,10),
              (3,'agent2@a','Agente 2','x',1,2,10)
        """))
        connection.execute(text("""
            INSERT INTO projetos VALUES
              (100,'Projeto A',NULL,'Ativo',NULL,NULL,1,10),
              (200,'Projeto B',NULL,'Ativo',NULL,NULL,1,20)
        """))
        connection.execute(text("""
            INSERT INTO pesquisas VALUES
              (1000,'Pesquisa A',NULL,1,100,NULL,NULL),
              (2000,'Pesquisa B',NULL,1,200,NULL,NULL)
        """))
        connection.execute(text("""
            INSERT INTO perguntas VALUES
              (100,'Intencao de voto','ESCOLHA_SIMPLES',1,1,0,NULL,'{}',1,1000),
              (101,'Sexo','ESCOLHA_SIMPLES',2,1,0,NULL,'{}',1,1000),
              (102,'Faixa etaria','ESCOLHA_SIMPLES',3,1,0,NULL,'{}',1,1000),
              (103,'Avaliacao do governo','ESCOLHA_SIMPLES',4,1,0,NULL,'{}',1,1000),
              (104,'Temas de interesse','MULTIPLA_ESCOLHA',5,0,0,NULL,'{}',1,1000),
              (105,'Comentario livre','TEXTO',6,0,0,NULL,'{}',1,1000),
              (106,'Segundo turno','ESCOLHA_SIMPLES',7,0,0,NULL,'{}',1,1000),
              (900,'Pergunta de outra pesquisa','ESCOLHA_SIMPLES',1,1,0,NULL,'{}',1,2000)
        """))
        # Dois setores analiticos disjuntos.
        connection.execute(text("""
            INSERT INTO setores VALUES
              (11,'Setor Norte',0,0,'RELATORIO','POLYGON ((0 0, 1 0, 1 1, 0 1, 0 0))',1000,NULL),
              (12,'Setor Sul',0,0,'AMBOS','POLYGON ((2 0, 3 0, 3 1, 2 1, 2 0))',1000,NULL)
        """))

        started = datetime(2026, 1, 10, tzinfo=timezone.utc).isoformat()
        # (id, agente, ponto_inicio, ponto_fim, voto, sexo, faixa, avaliacao)
        coletas = [
            (1, 2, "POINT (0.2 0.2)", None, "Candidato A", "Feminino", "25-34", "Ruim"),
            (2, 2, "POINT (0.3 0.3)", None, "Candidato A", "Feminino", "35-44", "Pessimo"),
            (3, 2, "POINT (0.4 0.4)", None, "Candidato B", "Feminino", "25-34", "Ruim"),
            (4, 3, "POINT (2.2 0.2)", None, "Candidato B", "Masculino", "25-34", "Ruim"),
            (5, 3, "POINT (2.3 0.3)", None, "Candidato C", "Feminino", "45-59", "Otimo"),
            (6, 3, "POINT (2.4 0.4)", None, "Candidato D", "Masculino", "35-44", "Pessimo"),
            # Sem localizacao_inicio: o ponto canonico cai no fim.
            (7, 2, None, "POINT (0.5 0.5)", "Candidato A", "Feminino", "25-34", "Ruim"),
            # Sem coordenada alguma: contabilizada, nunca descartada.
            (8, 2, None, None, "Candidato A", "Feminino", "25-34", "Ruim"),
            # Coordenada fora da faixa valida.
            (9, 2, "POINT (-999 91)", None, "Candidato B", "Feminino", "25-34", "Ruim"),
            # Fora de qualquer setor analitico.
            (10, 3, "POINT (9 9)", None, "Candidato C", "Masculino", "60+", "Bom"),
        ]
        resposta_id = 1
        for coleta_id, agente, inicio, fim, voto, sexo, faixa, avaliacao in coletas:
            connection.execute(
                text("""
                    INSERT INTO coletas VALUES
                    (:id,1000,:agente,10,:uuid,0,NULL,'ok',:started,:started,:inicio,:fim,0)
                """),
                {
                    "id": coleta_id, "agente": agente, "uuid": f"uuid-{coleta_id}",
                    "started": started, "inicio": inicio, "fim": fim,
                },
            )
            for pergunta_id, valor in (
                (VOTO, voto), (SEXO, sexo), (FAIXA, faixa), (AVALIACAO, avaliacao)
            ):
                connection.execute(
                    text("INSERT INTO respostas VALUES (:id,:p,:c,:v)"),
                    {"id": resposta_id, "p": pergunta_id, "c": coleta_id, "v": valor},
                )
                resposta_id += 1

        # Somente 1, 2 e 3 respondem ao segundo turno: as demais nao produzem
        # par no cruzamento e precisam ser CONTADAS, nunca mascaradas.
        for offset, (coleta_id, valor) in enumerate(
            ((1, "Candidato A"), (2, "Candidato B"), (3, "Candidato A"))
        ):
            connection.execute(
                text("INSERT INTO respostas VALUES (:id,:p,:c,:v)"),
                {"id": 900 + offset, "p": SEGUNDO_TURNO, "c": coleta_id, "v": valor},
            )

    yield engine, Session
    engine.dispose()


@pytest.fixture
def sessionmaker_fixture(database):
    _engine, Session = database
    return Session


def make_client(Session, current=None):
    app = FastAPI()
    app.include_router(relatorios.router)

    def override_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: current or user()
    return TestClient(app)


@pytest.fixture
def client(sessionmaker_fixture):
    return make_client(sessionmaker_fixture)


def pedir(client, pesquisa_id=1000, **payload):
    corpo = {"pergunta_id": VOTO, "valores": CANDIDATOS}
    corpo.update(payload)
    return client.post(ROTA.format(pesquisa_id), json=corpo)


def valores_por_coleta(payload):
    return {ponto["coleta_id"]: ponto["valor"] for ponto in payload["pontos"]}


# --- basico -------------------------------------------------------------------


def test_pergunta_principal_sem_filtros(client):
    resposta = pedir(client)
    assert resposta.status_code == 200
    payload = resposta.json()

    assert payload["pesquisa_id"] == 1000
    assert payload["pergunta_id"] == VOTO
    assert payload["resumo"]["total_universo"] == 10
    assert payload["resumo"]["total_filtrado"] == 10
    # 8 exibiveis: a 8 nao tem ponto e a 9 tem coordenada invalida.
    assert payload["resumo"]["total_com_coordenada"] == 8
    assert payload["resumo"]["total_sem_coordenada"] == 2
    assert len(payload["pontos"]) == 8


def test_cada_ponto_carrega_a_categoria_da_pergunta_principal(client):
    payload = pedir(client).json()
    mapa = valores_por_coleta(payload)
    assert mapa[1] == "Candidato A"
    assert mapa[3] == "Candidato B"
    assert mapa[5] == "Candidato C"
    assert mapa[6] == "Candidato D"


def test_categorias_trazem_o_total_de_cada_resposta(client):
    payload = pedir(client).json()
    totais = {item["valor"]: item["total"] for item in payload["categorias"]}
    # Conta o universo filtrado, inclusive quem nao tem ponto exibivel.
    assert totais == {
        "Candidato A": 4,
        "Candidato B": 3,
        "Candidato C": 2,
        "Candidato D": 1,
    }
    assert sum(totais.values()) == payload["resumo"]["total_filtrado"]


# --- selecao de categorias -------------------------------------------------------


def test_selecao_a_b_exclui_c_e_d(client):
    payload = pedir(client, valores=["Candidato A", "Candidato B"]).json()
    valores = {ponto["valor"] for ponto in payload["pontos"]}
    assert valores == {"Candidato A", "Candidato B"}
    assert "Candidato C" not in valores
    assert "Candidato D" not in valores
    assert payload["resumo"]["total_filtrado"] == 7


def test_valor_inexistente_nao_quebra_nem_inventa_ponto(client):
    payload = pedir(client, valores=["Candidato Inexistente"]).json()
    assert payload["pontos"] == []
    assert payload["categorias"] == []
    assert payload["resumo"]["total_filtrado"] == 0
    # O universo continua sendo reportado.
    assert payload["resumo"]["total_universo"] == 10


# --- dimensoes de filtro ----------------------------------------------------------


def test_uma_dimensao(client):
    payload = pedir(
        client, filtros_respostas=[{"pergunta_id": SEXO, "valores": ["Feminino"]}]
    ).json()
    # Coletas 1,2,3,5,7,8,9 sao Feminino.
    assert payload["resumo"]["total_filtrado"] == 7
    assert set(valores_por_coleta(payload)) == {1, 2, 3, 5, 7}


def test_or_dentro_da_mesma_dimensao(client):
    payload = pedir(
        client, filtros_respostas=[{"pergunta_id": FAIXA, "valores": ["25-34", "35-44"]}]
    ).json()
    # 25-34: 1,3,4,7,8,9 | 35-44: 2,6
    assert payload["resumo"]["total_filtrado"] == 8
    somente_25 = pedir(
        client, filtros_respostas=[{"pergunta_id": FAIXA, "valores": ["25-34"]}]
    ).json()
    # O OR amplia o universo, nunca reduz.
    assert somente_25["resumo"]["total_filtrado"] < payload["resumo"]["total_filtrado"]


def test_and_entre_dimensoes(client):
    payload = pedir(
        client,
        filtros_respostas=[
            {"pergunta_id": SEXO, "valores": ["Feminino"]},
            {"pergunta_id": FAIXA, "valores": ["25-34", "35-44"]},
        ],
    ).json()
    # Feminino {1,2,3,5,7,8,9} ∩ faixa {1,2,3,4,6,7,8,9} = {1,2,3,7,8,9}
    assert payload["resumo"]["total_filtrado"] == 6
    assert set(valores_por_coleta(payload)) == {1, 2, 3, 7}


def test_tres_dimensoes_simultaneas(client):
    payload = pedir(
        client,
        filtros_respostas=[
            {"pergunta_id": SEXO, "valores": ["Feminino"]},
            {"pergunta_id": FAIXA, "valores": ["25-34", "35-44"]},
            {"pergunta_id": AVALIACAO, "valores": ["Ruim", "Pessimo"]},
        ],
    ).json()
    # Mulheres de 25 a 44 que avaliam mal: 1,2,3,7,8,9.
    assert payload["resumo"]["total_filtrado"] == 6
    assert set(valores_por_coleta(payload)) == {1, 2, 3, 7}
    # A = {1,2,7,8}, B = {3,9}. A 8 (sem GPS) e a 9 (GPS invalido) contam na
    # categoria mesmo sem virar ponto.
    totais = {item["valor"]: item["total"] for item in payload["categorias"]}
    assert totais == {"Candidato A": 4, "Candidato B": 2}
    assert sum(totais.values()) == payload["resumo"]["total_filtrado"]


def test_filtro_e_restricao_de_universo_nao_cruzamento(client):
    """Adicionar dimensao so pode reduzir ou manter o universo."""
    base = pedir(client).json()["resumo"]["total_filtrado"]
    um = pedir(
        client, filtros_respostas=[{"pergunta_id": SEXO, "valores": ["Feminino"]}]
    ).json()["resumo"]["total_filtrado"]
    dois = pedir(
        client,
        filtros_respostas=[
            {"pergunta_id": SEXO, "valores": ["Feminino"]},
            {"pergunta_id": AVALIACAO, "valores": ["Ruim"]},
        ],
    ).json()["resumo"]["total_filtrado"]
    assert base >= um >= dois


def test_filtro_sem_correspondencia_devolve_vazio_sem_erro(client):
    resposta = pedir(
        client,
        filtros_respostas=[
            {"pergunta_id": SEXO, "valores": ["Feminino"]},
            {"pergunta_id": FAIXA, "valores": ["60+"]},
        ],
    )
    assert resposta.status_code == 200
    payload = resposta.json()
    assert payload["pontos"] == []
    assert payload["categorias"] == []
    assert payload["resumo"]["total_filtrado"] == 0


# --- filtros territoriais ------------------------------------------------------------


def test_filtro_por_setor(client):
    payload = pedir(client, setor_ids=[11]).json()
    # Setor Norte cobre 1,2,3,7; a 9 tem coordenada invalida e nao classifica.
    assert set(valores_por_coleta(payload)) == {1, 2, 3, 7}
    assert all(ponto["setor_id"] == 11 for ponto in payload["pontos"])


def test_filtro_por_setor_usa_a_regra_espacial_oficial(client):
    """Coleta 10 esta fora de qualquer setor: nao entra em nenhum recorte."""
    norte = set(valores_por_coleta(pedir(client, setor_ids=[11]).json()))
    sul = set(valores_por_coleta(pedir(client, setor_ids=[12]).json()))
    assert 10 not in norte and 10 not in sul
    assert sul == {4, 5, 6}
    assert norte.isdisjoint(sul)


def test_setor_id_aparece_no_ponto_quando_classificado(client):
    payload = pedir(client).json()
    por_coleta = {ponto["coleta_id"]: ponto["setor_id"] for ponto in payload["pontos"]}
    assert por_coleta[1] == 11
    assert por_coleta[4] == 12
    # Fora de setor nao inventa pertencimento.
    assert por_coleta[10] is None


def test_setor_de_outra_pesquisa_responde_404(client):
    assert pedir(client, setor_ids=[9999]).status_code == 404


def test_filtro_por_agente(client):
    payload = pedir(client, agente_ids=[3]).json()
    assert set(valores_por_coleta(payload)) == {4, 5, 6, 10}
    assert payload["resumo"]["total_filtrado"] == 4
    # O universo total nao muda com o recorte.
    assert payload["resumo"]["total_universo"] == 10


def test_agente_e_setor_combinam(client):
    payload = pedir(client, agente_ids=[3], setor_ids=[12]).json()
    assert set(valores_por_coleta(payload)) == {4, 5, 6}


# --- coordenadas -----------------------------------------------------------------------


def test_ponto_canonico_usa_inicio_e_cai_no_fim(client):
    payload = pedir(client).json()
    pontos = {ponto["coleta_id"]: (ponto["lat"], ponto["lng"]) for ponto in payload["pontos"]}
    # Coleta 1 tem inicio.
    assert pontos[1] == pytest.approx((0.2, 0.2))
    # Coleta 7 so tem fim: coalesce(inicio, fim), a mesma regra dos outros mapas.
    assert pontos[7] == pytest.approx((0.5, 0.5))


def test_coleta_sem_coordenada_e_contabilizada_nao_descartada(client):
    payload = pedir(client).json()
    exibidas = set(valores_por_coleta(payload))
    assert 8 not in exibidas
    assert payload["resumo"]["total_sem_coordenada"] >= 1
    # Continua contando na categoria: o total nao depende de haver GPS.
    totais = {item["valor"]: item["total"] for item in payload["categorias"]}
    assert totais["Candidato A"] == 4


def test_coordenada_invalida_nao_derruba_a_requisicao(client):
    resposta = pedir(client)
    assert resposta.status_code == 200
    payload = resposta.json()
    assert 9 not in valores_por_coleta(payload)
    for ponto in payload["pontos"]:
        assert -90 <= ponto["lat"] <= 90
        assert -180 <= ponto["lng"] <= 180
    # Nunca (0,0) inventado.
    assert all((ponto["lat"], ponto["lng"]) != (0.0, 0.0) for ponto in payload["pontos"])


def test_soma_de_exibiveis_e_nao_exibiveis_fecha_com_o_filtrado(client):
    resumo = pedir(client).json()["resumo"]
    assert resumo["total_com_coordenada"] + resumo["total_sem_coordenada"] == resumo["total_filtrado"]


# --- multitenancy ------------------------------------------------------------------------


def test_tenant_proprio_responde_200(client):
    assert pedir(client).status_code == 200


def test_pesquisa_de_outro_tenant_responde_404(sessionmaker_fixture):
    outro = make_client(sessionmaker_fixture, current=user(user_id=9, company_id=20))
    resposta = outro.post(ROTA.format(1000), json={"pergunta_id": VOTO, "valores": CANDIDATOS})
    # 404, nunca 403: nao revela a existencia do recurso.
    assert resposta.status_code == 404


def test_pesquisa_inexistente_responde_404(client):
    assert pedir(client, pesquisa_id=999999).status_code == 404


def test_payload_recusa_company_id(client):
    resposta = client.post(
        ROTA.format(1000),
        json={"pergunta_id": VOTO, "valores": CANDIDATOS, "company_id": 20},
    )
    assert resposta.status_code == 422


def test_payload_recusa_configuracao_visual(client):
    for extra in ({"cores": {"Candidato A": "#0072B2"}}, {"zoom": 12}, {"marker_size": 8}):
        corpo = {"pergunta_id": VOTO, "valores": CANDIDATOS, **extra}
        assert client.post(ROTA.format(1000), json=corpo).status_code == 422


# --- validacao de pergunta ------------------------------------------------------------------


def test_pergunta_de_outra_pesquisa_responde_404(client):
    assert pedir(client, pergunta_id=900).status_code == 404


def test_pergunta_inexistente_responde_404(client):
    assert pedir(client, pergunta_id=99999).status_code == 404


def test_filtro_com_pergunta_de_outra_pesquisa_responde_404(client):
    resposta = pedir(client, filtros_respostas=[{"pergunta_id": 900, "valores": ["x"]}])
    assert resposta.status_code == 404


def test_pergunta_principal_nao_pode_ser_filtro(client):
    resposta = pedir(
        client, filtros_respostas=[{"pergunta_id": VOTO, "valores": ["Candidato A"]}]
    )
    assert resposta.status_code == 422


def test_filtros_nao_podem_repetir_pergunta(client):
    resposta = pedir(
        client,
        filtros_respostas=[
            {"pergunta_id": SEXO, "valores": ["Feminino"]},
            {"pergunta_id": SEXO, "valores": ["Masculino"]},
        ],
    )
    assert resposta.status_code == 422


def test_valores_vazio_e_recusado(client):
    assert pedir(client, valores=[]).status_code == 422


# --- limites declarados do MVP ------------------------------------------------------------


def test_multipla_escolha_e_recusada_com_mensagem_clara(client):
    resposta = pedir(client, pergunta_id=MULTIPLA)
    assert resposta.status_code == 422
    detalhe = resposta.json()["detail"]
    # Recusa explicita em vez de escolher uma das respostas em silencio.
    assert "multipla escolha" in detalhe.lower()
    assert "categoria" in detalhe.lower()


def test_pergunta_nao_categorica_e_recusada(client):
    resposta = pedir(client, pergunta_id=TEXTO_LIVRE)
    assert resposta.status_code == 422
    assert "resposta unica" in resposta.json()["detail"].lower()


# --- contrato -----------------------------------------------------------------------------


def test_ponto_nao_expoe_dado_pessoal(client):
    payload = pedir(client).json()
    # Os dois booleanos de agrupamento sao estado de apresentacao da categoria,
    # nao dado do entrevistado. O conjunto continua fechado: nada de nome,
    # telefone, endereco ou identificador de pessoa.
    permitido = {
        "coleta_id",
        "lat",
        "lng",
        "valor",
        "valor_secundario",
        "setor_id",
        "agrupado",
        "agrupado_secundario",
    }
    for ponto in payload["pontos"]:
        assert set(ponto) == permitido
        # Sem cruzamento o campo existe mas nao carrega categoria alguma.
        assert ponto["valor_secundario"] is None


def test_resposta_nao_traz_cor(client):
    corpo = pedir(client).text.lower()
    # A decisao valor -> cor pertence ao cliente.
    assert "cor" not in corpo
    assert "#" not in corpo


def test_resposta_nao_expoe_geometria_crua(client):
    payload = pedir(client).json()
    for ponto in payload["pontos"]:
        assert isinstance(ponto["lat"], float)
        assert isinstance(ponto["lng"], float)
    corpo = pedir(client).text
    for proibido in ("POINT (", "WKB", "0101000020"):
        assert proibido not in corpo


# --- ordenacao da legenda -----------------------------------------------------


def test_categorias_vem_ordenadas_da_maior_para_a_menor(client):
    payload = pedir(client).json()
    totais = [item["total"] for item in payload["categorias"]]
    assert totais == sorted(totais, reverse=True)
    assert [item["valor"] for item in payload["categorias"]] == [
        "Candidato A",
        "Candidato B",
        "Candidato C",
        "Candidato D",
    ]


def test_ordenacao_nao_depende_da_ordem_em_que_o_cliente_enviou(client):
    """A legenda le por quantidade, nao pela ordem de clique do usuario."""
    invertido = pedir(client, valores=list(reversed(CANDIDATOS))).json()
    assert [item["valor"] for item in invertido["categorias"]] == [
        "Candidato A",
        "Candidato B",
        "Candidato C",
        "Candidato D",
    ]


def test_empate_desempata_pela_ordem_original_das_opcoes(client):
    """C e D empatam em 1 sob este recorte; C vem antes por ser opcao anterior."""
    payload = pedir(client, agente_ids=[3], valores=["Candidato D", "Candidato C"]).json()
    totais = {item["valor"]: item["total"] for item in payload["categorias"]}
    assert totais == {"Candidato C": 2, "Candidato D": 1}
    # Agora um recorte com empate real: setor Sul tem C=1 e D=1.
    empate = pedir(client, setor_ids=[12], valores=["Candidato D", "Candidato C"]).json()
    assert [item["total"] for item in empate["categorias"]] == [1, 1]
    # `valores` chegou com D primeiro: o desempate segue essa ordem, nao o alfabeto.
    assert [item["valor"] for item in empate["categorias"]] == ["Candidato D", "Candidato C"]


# --- modo cruzado: retrocompatibilidade ---------------------------------------


def test_sem_pergunta_secundaria_a_resposta_e_a_de_sempre(client):
    payload = pedir(client).json()
    assert payload["pergunta_secundaria_id"] is None
    assert payload["combinacoes"] == []
    assert payload["resumo"]["total_sem_par"] == 0
    assert all(ponto["valor_secundario"] is None for ponto in payload["pontos"])


def test_pergunta_secundaria_nula_equivale_a_ausente(client):
    com_nulo = pedir(client, pergunta_secundaria_id=None).json()
    sem_campo = pedir(client).json()
    assert com_nulo == sem_campo


# --- modo cruzado: semantica --------------------------------------------------


def test_cruzamento_pareia_pela_mesma_coleta(client):
    payload = pedir(client, pergunta_secundaria_id=SEXO).json()
    assert payload["pergunta_secundaria_id"] == SEXO
    pares = {
        ponto["coleta_id"]: (ponto["valor"], ponto["valor_secundario"])
        for ponto in payload["pontos"]
    }
    # Cada par vem da MESMA entrevista.
    assert pares[1] == ("Candidato A", "Feminino")
    assert pares[4] == ("Candidato B", "Masculino")
    assert pares[6] == ("Candidato D", "Masculino")
    assert pares[10] == ("Candidato C", "Masculino")


def test_combinacoes_contam_pares_observados(client):
    payload = pedir(client, pergunta_secundaria_id=SEXO).json()
    contagem = {
        (item["valor"], item["valor_secundario"]): item["total"]
        for item in payload["combinacoes"]
    }
    assert contagem == {
        ("Candidato A", "Feminino"): 4,
        ("Candidato B", "Feminino"): 2,
        ("Candidato B", "Masculino"): 1,
        ("Candidato C", "Feminino"): 1,
        ("Candidato C", "Masculino"): 1,
        ("Candidato D", "Masculino"): 1,
    }
    # Fecha com o universo filtrado: nenhuma coleta contada duas vezes.
    assert sum(contagem.values()) == payload["resumo"]["total_filtrado"] == 10


def test_cruzamento_nao_e_produto_de_totais_independentes(client):
    """Marginais A=4 e Masculino=3 nunca produziriam zero para (A, Masculino)."""
    payload = pedir(client, pergunta_secundaria_id=SEXO).json()
    pares = {(item["valor"], item["valor_secundario"]) for item in payload["combinacoes"]}
    marginais_a = {item["valor"] for item in payload["categorias"]}
    marginais_b = {item["valor_secundario"] for item in payload["combinacoes"]}
    # O produto cartesiano teria 4x2 = 8 celulas; so 6 existem de fato.
    assert len(marginais_a) * len(marginais_b) == 8
    assert len(pares) == 6
    assert ("Candidato A", "Masculino") not in pares
    assert ("Candidato D", "Feminino") not in pares


def test_combinacoes_ordenadas_da_maior_para_a_menor(client):
    payload = pedir(client, pergunta_secundaria_id=SEXO).json()
    totais = [item["total"] for item in payload["combinacoes"]]
    assert totais == sorted(totais, reverse=True)
    assert (payload["combinacoes"][0]["valor"], payload["combinacoes"][0]["valor_secundario"]) == (
        "Candidato A",
        "Feminino",
    )


def test_cruzamento_respeita_a_selecao_de_categorias_da_principal(client):
    payload = pedir(
        client, pergunta_secundaria_id=SEXO, valores=["Candidato A", "Candidato B"]
    ).json()
    assert {item["valor"] for item in payload["combinacoes"]} == {
        "Candidato A",
        "Candidato B",
    }
    assert payload["resumo"]["total_filtrado"] == 7


def test_cruzamento_respeita_filtros_de_setor_e_agente(client):
    payload = pedir(client, pergunta_secundaria_id=SEXO, setor_ids=[11]).json()
    contagem = {
        (item["valor"], item["valor_secundario"]): item["total"]
        for item in payload["combinacoes"]
    }
    # Setor Norte: coletas 1,2,3,7 -> A/F x3 e B/F x1.
    assert contagem == {("Candidato A", "Feminino"): 3, ("Candidato B", "Feminino"): 1}
    assert sum(contagem.values()) == payload["resumo"]["total_filtrado"]


def test_cruzamento_respeita_dimensoes_adicionais(client):
    payload = pedir(
        client,
        pergunta_secundaria_id=SEXO,
        filtros_respostas=[{"pergunta_id": FAIXA, "valores": ["25-34"]}],
    ).json()
    contagem = {
        (item["valor"], item["valor_secundario"]): item["total"]
        for item in payload["combinacoes"]
    }
    # 25-34: coletas 1,3,4,7,8,9.
    assert contagem == {
        ("Candidato A", "Feminino"): 3,
        ("Candidato B", "Feminino"): 2,
        ("Candidato B", "Masculino"): 1,
    }
    assert sum(contagem.values()) == payload["resumo"]["total_filtrado"] == 6


def test_filtro_recalcula_legenda_e_combinacoes_no_mesmo_universo(client):
    """Legenda e mapa nao podem descrever universos diferentes."""
    payload = pedir(client, pergunta_secundaria_id=SEXO, agente_ids=[2]).json()
    soma_categorias = sum(item["total"] for item in payload["categorias"])
    soma_combinacoes = sum(item["total"] for item in payload["combinacoes"])
    assert soma_categorias == soma_combinacoes == payload["resumo"]["total_filtrado"]


# --- modo cruzado: resposta ausente na segunda pergunta -----------------------


def test_coleta_sem_resposta_na_secundaria_sai_do_cruzamento_e_e_declarada(client):
    payload = pedir(client, pergunta_secundaria_id=SEGUNDO_TURNO).json()
    # So 1, 2 e 3 responderam a segunda pergunta.
    assert payload["resumo"]["total_filtrado"] == 3
    assert payload["resumo"]["total_sem_par"] == 7
    assert set(valores_por_coleta(payload)) == {1, 2, 3}
    contagem = {
        (item["valor"], item["valor_secundario"]): item["total"]
        for item in payload["combinacoes"]
    }
    assert contagem == {
        ("Candidato A", "Candidato A"): 1,
        ("Candidato A", "Candidato B"): 1,
        ("Candidato B", "Candidato A"): 1,
    }


def test_sem_par_nao_contamina_o_total_sem_coordenada(client):
    resumo = pedir(client, pergunta_secundaria_id=SEGUNDO_TURNO).json()["resumo"]
    assert resumo["total_com_coordenada"] + resumo["total_sem_coordenada"] == resumo["total_filtrado"]
    assert resumo["total_universo"] == 10


# --- modo cruzado: validacao e multitenancy -----------------------------------


def test_cruzar_a_pergunta_com_ela_mesma_e_recusado(client):
    resposta = pedir(client, pergunta_secundaria_id=VOTO)
    assert resposta.status_code == 422


def test_pergunta_secundaria_de_outra_pesquisa_responde_404(client):
    # 900 pertence a Pesquisa B (outro tenant): indistinguivel de inexistente.
    assert pedir(client, pergunta_secundaria_id=900).status_code == 404


def test_pergunta_secundaria_inexistente_responde_404(client):
    assert pedir(client, pergunta_secundaria_id=99999).status_code == 404


def test_pergunta_secundaria_de_multipla_escolha_e_recusada(client):
    resposta = pedir(client, pergunta_secundaria_id=MULTIPLA)
    assert resposta.status_code == 422
    assert "multipla escolha" in resposta.json()["detail"].lower()


def test_pergunta_secundaria_nao_categorica_e_recusada(client):
    resposta = pedir(client, pergunta_secundaria_id=TEXTO_LIVRE)
    assert resposta.status_code == 422


def test_pergunta_secundaria_nao_pode_ser_dimensao_de_filtro(client):
    resposta = pedir(
        client,
        pergunta_secundaria_id=SEXO,
        filtros_respostas=[{"pergunta_id": SEXO, "valores": ["Feminino"]}],
    )
    assert resposta.status_code == 422


def test_cruzamento_em_pesquisa_de_outro_tenant_responde_404(sessionmaker_fixture):
    outro = make_client(sessionmaker_fixture, current=user(user_id=9, company_id=20))
    resposta = outro.post(
        ROTA.format(1000),
        json={
            "pergunta_id": VOTO,
            "pergunta_secundaria_id": SEXO,
            "valores": CANDIDATOS,
        },
    )
    assert resposta.status_code == 404


def test_cruzamento_continua_recusando_company_id(client):
    resposta = client.post(
        ROTA.format(1000),
        json={
            "pergunta_id": VOTO,
            "pergunta_secundaria_id": SEXO,
            "valores": CANDIDATOS,
            "company_id": 20,
        },
    )
    assert resposta.status_code == 422


# --- recorte da segunda pergunta (valores_secundarios) ------------------------
#
# Seed efetivo de VOTO x SEXO na fixture:
#   A/Feminino  1,2,7,8   B/Feminino  3,9   B/Masculino 4
#   C/Feminino  5         C/Masculino 10    D/Masculino 6


def test_valores_secundarios_recorta_o_par(client):
    payload = pedir(
        client, pergunta_secundaria_id=SEXO, valores_secundarios=["Feminino"]
    ).json()
    contagem = {
        (item["valor"], item["valor_secundario"]): item["total"]
        for item in payload["combinacoes"]
    }
    assert contagem == {
        ("Candidato A", "Feminino"): 4,
        ("Candidato B", "Feminino"): 2,
        ("Candidato C", "Feminino"): 1,
    }
    # Masculino desaparece por completo: nenhum par, nenhum ponto.
    assert all(item["valor_secundario"] == "Feminino" for item in payload["combinacoes"])
    assert all(ponto["valor_secundario"] == "Feminino" for ponto in payload["pontos"])


def test_o_recorte_de_b_recalcula_quantidade_e_universo(client):
    """Legenda e mapa no MESMO universo, depois do filtro secundario."""
    payload = pedir(
        client,
        pergunta_secundaria_id=SEXO,
        valores=["Candidato A", "Candidato B"],
        valores_secundarios=["Feminino"],
    ).json()
    # A/F = 4 e B/F = 2 -> 6 no total, e nao os 7 do recorte sem filtro de B.
    assert payload["resumo"]["total_filtrado"] == 6
    soma_categorias = sum(item["total"] for item in payload["categorias"])
    soma_combinacoes = sum(item["total"] for item in payload["combinacoes"])
    assert soma_categorias == soma_combinacoes == 6
    # As marginais de A tambem encolhem: o denominador do percentual e este.
    assert {item["valor"]: item["total"] for item in payload["categorias"]} == {
        "Candidato A": 4,
        "Candidato B": 2,
    }


def test_recorte_de_b_com_50_50(client):
    """Cenario da Fase 18: dois pares empatados sob o recorte de B."""
    payload = pedir(
        client,
        pergunta_secundaria_id=SEXO,
        valores=["Candidato B", "Candidato C"],
        valores_secundarios=["Masculino"],
    ).json()
    contagem = {
        (item["valor"], item["valor_secundario"]): item["total"]
        for item in payload["combinacoes"]
    }
    # B/Masculino = coleta 4; C/Masculino = coleta 10.
    assert contagem == {
        ("Candidato B", "Masculino"): 1,
        ("Candidato C", "Masculino"): 1,
    }
    total = payload["resumo"]["total_filtrado"]
    assert total == 2
    assert [round(item["total"] / total * 100, 1) for item in payload["combinacoes"]] == [
        50.0,
        50.0,
    ]


def test_valores_secundarios_ausente_significa_todas_as_categorias(client):
    """Retrocompatibilidade: o contrato anterior nao muda de comportamento."""
    sem_campo = pedir(client, pergunta_secundaria_id=SEXO).json()
    com_todas = pedir(
        client,
        pergunta_secundaria_id=SEXO,
        valores_secundarios=["Feminino", "Masculino"],
    ).json()
    assert sem_campo == com_todas


def test_valor_secundario_inexistente_devolve_vazio_sem_erro(client):
    resposta = pedir(
        client, pergunta_secundaria_id=SEXO, valores_secundarios=["Nao binario"]
    )
    assert resposta.status_code == 200
    payload = resposta.json()
    assert payload["combinacoes"] == []
    assert payload["pontos"] == []
    assert payload["resumo"]["total_filtrado"] == 0
    # O universo continua sendo reportado.
    assert payload["resumo"]["total_universo"] == 10


def test_recorte_de_b_nao_e_confundido_com_ausencia_de_resposta(client):
    """`total_sem_par` conta buraco de dado, nunca recorte pedido pelo usuario."""
    todas = pedir(client, pergunta_secundaria_id=SEXO).json()
    recortado = pedir(
        client, pergunta_secundaria_id=SEXO, valores_secundarios=["Feminino"]
    ).json()
    assert todas["resumo"]["total_sem_par"] == 0
    assert recortado["resumo"]["total_sem_par"] == 0
    assert recortado["resumo"]["total_filtrado"] < todas["resumo"]["total_filtrado"]

    # Ja com pergunta de fato nao respondida por todos, o contador sobe.
    parcial = pedir(
        client, pergunta_secundaria_id=SEGUNDO_TURNO, valores_secundarios=["Candidato A"]
    ).json()
    assert parcial["resumo"]["total_sem_par"] == 7
    assert parcial["resumo"]["total_filtrado"] == 2


def test_recorte_de_b_combina_com_setor_agente_e_dimensoes(client):
    payload = pedir(
        client,
        pergunta_secundaria_id=SEXO,
        valores_secundarios=["Feminino"],
        setor_ids=[11],
        filtros_respostas=[{"pergunta_id": FAIXA, "valores": ["25-34"]}],
    ).json()
    # Setor Norte {1,2,3,7} ∩ 25-34 {1,3,7} e todas Feminino.
    assert set(valores_por_coleta(payload)) == {1, 3, 7}
    contagem = {
        (item["valor"], item["valor_secundario"]): item["total"]
        for item in payload["combinacoes"]
    }
    assert contagem == {
        ("Candidato A", "Feminino"): 2,
        ("Candidato B", "Feminino"): 1,
    }


# --- validacao do recorte de B --------------------------------------------------


def test_valores_secundarios_sem_pergunta_secundaria_e_recusado(client):
    resposta = pedir(client, valores_secundarios=["Feminino"])
    assert resposta.status_code == 422


def test_valores_secundarios_vazio_e_recusado(client):
    resposta = pedir(client, pergunta_secundaria_id=SEXO, valores_secundarios=[])
    assert resposta.status_code == 422


def test_valores_secundarios_duplicados_sao_recusados(client):
    resposta = pedir(
        client,
        pergunta_secundaria_id=SEXO,
        valores_secundarios=["Feminino", "Feminino"],
    )
    assert resposta.status_code == 422


def test_valores_secundarios_com_item_vazio_e_recusado(client):
    resposta = pedir(
        client, pergunta_secundaria_id=SEXO, valores_secundarios=["Feminino", "  "]
    )
    assert resposta.status_code == 422


def test_recorte_de_b_em_pesquisa_de_outro_tenant_responde_404(sessionmaker_fixture):
    outro = make_client(sessionmaker_fixture, current=user(user_id=9, company_id=20))
    resposta = outro.post(
        ROTA.format(1000),
        json={
            "pergunta_id": VOTO,
            "pergunta_secundaria_id": SEXO,
            "valores": CANDIDATOS,
            "valores_secundarios": ["Feminino"],
        },
    )
    assert resposta.status_code == 404


# --- tipos elegiveis: a regra que o QA testou ------------------------------------


def test_escolha_simples_e_aceita_como_principal_e_como_cruzamento(client):
    """Tipos reais da Pesquisa 10; nenhum deles pode ser recusado."""
    assert pedir(client, pergunta_id=VOTO).status_code == 200
    assert pedir(client, pergunta_id=SEXO, valores=["Feminino", "Masculino"]).status_code == 200
    assert pedir(client, pergunta_secundaria_id=SEXO).status_code == 200
    assert pedir(client, pergunta_secundaria_id=FAIXA).status_code == 200


def test_recusa_de_tipo_explica_o_motivo_real(client):
    """A mensagem precisa dizer O QUE ha de errado, nao um generico."""
    multipla = pedir(client, pergunta_secundaria_id=MULTIPLA)
    assert multipla.status_code == 422
    assert "multipla escolha" in multipla.json()["detail"].lower()

    texto = pedir(client, pergunta_secundaria_id=TEXTO_LIVRE)
    assert texto.status_code == 422
    assert "resposta unica" in texto.json()["detail"].lower()


# --- universo analitico -------------------------------------------------------
#
# O denominador do "% do universo" e o universo APOS os filtros estruturais
# (setor, agente, dimensoes) e ANTES da selecao de categorias. Marcar ou
# desmarcar uma resposta destaca ou agrupa -- nunca mexe no denominador.


def test_universo_analitico_sem_filtros_e_a_pesquisa_inteira(client):
    resumo = pedir(client).json()["resumo"]
    assert resumo["total_universo"] == 10
    assert resumo["total_universo_analitico"] == 10


def test_dimensao_estrutural_reduz_o_universo_analitico(client):
    # 7 das 10 coletas sao Feminino.
    resumo = pedir(
        client,
        filtros_respostas=[{"pergunta_id": SEXO, "valores": ["Feminino"]}],
    ).json()["resumo"]
    assert resumo["total_universo"] == 10
    assert resumo["total_universo_analitico"] == 7


def test_selecao_de_resposta_nao_altera_o_universo_analitico(client):
    """A regra central da Fase 2: selecionar destaca, nao redefine o denominador."""
    base = pedir(
        client,
        filtros_respostas=[{"pergunta_id": SEXO, "valores": ["Feminino"]}],
    ).json()["resumo"]
    recorte = pedir(
        client,
        valores=["Candidato A"],
        filtros_respostas=[{"pergunta_id": SEXO, "valores": ["Feminino"]}],
    ).json()["resumo"]

    # O universo analitico e o MESMO nas duas leituras...
    assert base["total_universo_analitico"] == recorte["total_universo_analitico"] == 7
    # ...enquanto o recorte destacado muda: 4 das 7 mulheres votam em A.
    assert base["total_filtrado"] == 7
    assert recorte["total_filtrado"] == 4


def test_setor_e_agente_tambem_recortam_o_universo_analitico(client):
    resumo = pedir(client, setor_ids=[11]).json()["resumo"]
    # Setor Norte cobre 1, 2, 3 e 7; a 8 e a 9 nao tem ponto classificavel.
    assert resumo["total_universo_analitico"] == 4


def test_sem_categoria_explica_a_diferenca_para_o_universo_analitico(client):
    """Universo analitico = representados + sem categoria. Sem subtracao cega."""
    resumo = pedir(client, valores=["Candidato A"]).json()["resumo"]
    assert resumo["total_universo_analitico"] == 10
    assert resumo["total_filtrado"] == 4
    assert resumo["total_sem_categoria"] == 6
    assert (
        resumo["total_filtrado"] + resumo["total_sem_categoria"]
        == resumo["total_universo_analitico"]
    )


# --- agrupamento em "Outros" --------------------------------------------------


def test_sem_agrupamento_o_comportamento_e_identico_ao_legado(client):
    """Caso A do plano: a chave ausente e `False` produzem a MESMA resposta."""
    legado = pedir(client, valores=["Candidato A"]).json()
    explicito = pedir(
        client, valores=["Candidato A"], agrupar_nao_selecionadas=False
    ).json()
    assert legado == explicito
    # E as nao selecionadas continuam fora do mapa.
    assert [c["valor"] for c in legado["categorias"]] == ["Candidato A"]
    assert legado["resumo"]["total_filtrado"] == 4


def test_agrupamento_preserva_as_nao_selecionadas_como_outros(client):
    payload = pedir(
        client, valores=["Candidato A"], agrupar_nao_selecionadas=True
    ).json()
    categorias = {(c["valor"], c["agrupado"]): c["total"] for c in payload["categorias"]}
    assert categorias == {("Candidato A", False): 4, ("Outros", True): 6}
    # Nada se perdeu: o mapa passa a representar o universo analitico inteiro.
    assert payload["resumo"]["total_filtrado"] == 10
    assert payload["resumo"]["total_universo_analitico"] == 10
    assert payload["resumo"]["total_sem_categoria"] == 0


def test_pontos_agrupados_carregam_a_flag_e_nao_o_texto(client):
    payload = pedir(
        client, valores=["Candidato A"], agrupar_nao_selecionadas=True
    ).json()
    agrupados = [p for p in payload["pontos"] if p["agrupado"]]
    destacados = [p for p in payload["pontos"] if not p["agrupado"]]
    assert {p["valor"] for p in agrupados} == {"Outros"}
    assert {p["valor"] for p in destacados} == {"Candidato A"}


def test_valores_preservados_nao_entram_no_balde(client):
    """Caso G: categoria especial mantem identidade propria mesmo desmarcada."""
    payload = pedir(
        client,
        valores=["Candidato A"],
        agrupar_nao_selecionadas=True,
        valores_preservados=["Candidato D"],
    ).json()
    categorias = {(c["valor"], c["agrupado"]): c["total"] for c in payload["categorias"]}
    assert categorias == {
        ("Candidato A", False): 4,
        ("Outros", True): 5,
        ("Candidato D", False): 1,
    }
    assert payload["resumo"]["total_filtrado"] == 10


def test_preservada_nao_e_confundida_com_selecionada(client):
    """A preservada aparece com agrupado=False, como qualquer categoria real."""
    payload = pedir(
        client,
        valores=["Candidato A"],
        agrupar_nao_selecionadas=True,
        valores_preservados=["Candidato D"],
    ).json()
    d = next(c for c in payload["categorias"] if c["valor"] == "Candidato D")
    assert d["agrupado"] is False


def test_preservados_sem_agrupamento_e_recusado(client):
    resposta = pedir(client, valores=["Candidato A"], valores_preservados=["Candidato D"])
    # Preservar de um balde que nao existe seria uma instrucao sem efeito.
    assert resposta.status_code == 422


def test_agrupamento_nao_afrouxa_a_regra_de_coordenada(client):
    payload = pedir(
        client, valores=["Candidato A"], agrupar_nao_selecionadas=True
    ).json()
    # A 8 nao tem ponto e a 9 tem coordenada invalida: continuam declaradas.
    assert payload["resumo"]["total_sem_coordenada"] == 2
    assert payload["resumo"]["total_com_coordenada"] == 8


def test_agrupamento_com_todas_marcadas_nao_cria_balde_vazio(client):
    payload = pedir(client, agrupar_nao_selecionadas=True).json()
    assert all(not c["agrupado"] for c in payload["categorias"])
    assert payload["resumo"]["total_filtrado"] == 10


# --- cruzamento agrupado ------------------------------------------------------


def _responder_segundo_turno(Session, coleta_id, valor, resposta_id):
    """Amplia a fixture dentro do teste, sem mexer nas expectativas das demais."""
    db = Session()
    db.execute(
        text("INSERT INTO respostas VALUES (:id,:p,:c,:v)"),
        {"id": resposta_id, "p": SEGUNDO_TURNO, "c": coleta_id, "v": valor},
    )
    db.commit()
    db.close()


def test_cruzamento_agrupado_produz_as_quatro_combinacoes(sessionmaker_fixture):
    """Caso D do plano: Sel x Sel, Sel x Outros, Outros x Sel, Outros x Outros.

    A fixture responde ao segundo turno em 1, 2 e 3; a 4 completa o quadrante
    que faltava (voto destacado, segundo turno agrupado).
    """
    Session = sessionmaker_fixture
    _responder_segundo_turno(Session, 4, "Candidato B", 950)
    client = make_client(Session)

    payload = pedir(
        client,
        valores=["Candidato B"],
        agrupar_nao_selecionadas=True,
        pergunta_secundaria_id=SEGUNDO_TURNO,
        valores_secundarios=["Candidato A"],
        agrupar_nao_selecionadas_secundaria=True,
    ).json()

    observadas = [
        (c["valor"], c["agrupado"], c["valor_secundario"], c["agrupado_secundario"], c["total"])
        for c in payload["combinacoes"]
    ]
    assert observadas == [
        ("Candidato B", False, "Candidato A", False, 1),   # coleta 3
        ("Candidato B", False, "Outros", True, 1),         # coleta 4
        ("Outros", True, "Candidato A", False, 1),         # coleta 1
        ("Outros", True, "Outros", True, 1),               # coleta 2
    ]


def test_cruzamento_agrupado_nao_engole_quem_nao_respondeu(sessionmaker_fixture):
    """Outros reune quem RESPONDEU outra coisa, nunca quem nao respondeu."""
    Session = sessionmaker_fixture
    _responder_segundo_turno(Session, 4, "Candidato B", 950)
    client = make_client(Session)

    payload = pedir(
        client,
        valores=["Candidato B"],
        agrupar_nao_selecionadas=True,
        pergunta_secundaria_id=SEGUNDO_TURNO,
        valores_secundarios=["Candidato A"],
        agrupar_nao_selecionadas_secundaria=True,
    ).json()
    resumo = payload["resumo"]

    # 6 coletas nao responderam ao segundo turno: continuam declaradas.
    assert resumo["total_sem_par"] == 6
    assert resumo["total_filtrado"] == 4
    assert resumo["total_universo_analitico"] == 10
    assert sum(c["total"] for c in payload["combinacoes"]) == resumo["total_filtrado"]


def test_agrupar_so_a_principal(client):
    """Caso E: B mantem o recorte legado, A preserva as nao selecionadas."""
    payload = pedir(
        client,
        valores=["Candidato B"],
        agrupar_nao_selecionadas=True,
        pergunta_secundaria_id=SEGUNDO_TURNO,
        valores_secundarios=["Candidato A"],
    ).json()
    observadas = {
        (c["valor"], c["agrupado"], c["valor_secundario"], c["agrupado_secundario"])
        for c in payload["combinacoes"]
    }
    # Coleta 1 (voto A -> Outros, 2o turno A) e coleta 3 (voto B, 2o turno A).
    # A coleta 2 responde B no segundo turno e cai fora: sem agrupamento em B,
    # o recorte continua excluindo.
    assert observadas == {
        ("Candidato B", False, "Candidato A", False),
        ("Outros", True, "Candidato A", False),
    }
    assert all(not c["agrupado_secundario"] for c in payload["combinacoes"])


def test_agrupar_so_a_secundaria(client):
    """Caso F: A mantem o filtro legado, B preserva as nao selecionadas."""
    payload = pedir(
        client,
        valores=["Candidato A"],
        pergunta_secundaria_id=SEGUNDO_TURNO,
        valores_secundarios=["Candidato A"],
        agrupar_nao_selecionadas_secundaria=True,
    ).json()
    observadas = {
        (c["valor"], c["agrupado"], c["valor_secundario"], c["agrupado_secundario"])
        for c in payload["combinacoes"]
    }
    # Coletas 1 (2o turno A) e 2 (2o turno B -> Outros); ambas votam em A.
    assert observadas == {
        ("Candidato A", False, "Candidato A", False),
        ("Candidato A", False, "Outros", True),
    }
    assert all(not c["agrupado"] for c in payload["combinacoes"])


def test_agrupar_secundaria_sem_cruzamento_e_recusado(client):
    resposta = pedir(client, agrupar_nao_selecionadas_secundaria=True)
    assert resposta.status_code == 422


def test_preservada_na_secundaria_mantem_identidade(client):
    payload = pedir(
        client,
        valores=["Candidato A"],
        pergunta_secundaria_id=SEGUNDO_TURNO,
        valores_secundarios=["Candidato A"],
        agrupar_nao_selecionadas_secundaria=True,
        valores_preservados_secundarios=["Candidato B"],
    ).json()
    observadas = {
        (c["valor_secundario"], c["agrupado_secundario"]) for c in payload["combinacoes"]
    }
    # "Candidato B" continua com nome proprio no lado direito do par.
    assert observadas == {("Candidato A", False), ("Candidato B", False)}


# --- identidade da categoria --------------------------------------------------


def test_opcao_real_chamada_outros_nao_se_funde_ao_balde(sessionmaker_fixture):
    """A identidade e o PAR (valor, agrupado), nunca o texto sozinho."""
    Session = sessionmaker_fixture
    db = Session()
    # A coleta 6 passa a responder literalmente "Outros" a pergunta de voto.
    db.execute(
        text(
            "UPDATE respostas SET valor_resposta='Outros' "
            "WHERE pergunta_id=:p AND coleta_id=6"
        ),
        {"p": VOTO},
    )
    db.commit()
    db.close()
    client = make_client(Session)

    payload = pedir(
        client,
        valores=["Candidato A", "Outros"],
        agrupar_nao_selecionadas=True,
    ).json()
    categorias = {(c["valor"], c["agrupado"]): c["total"] for c in payload["categorias"]}

    # Duas linhas chamadas "Outros" convivem: a resposta real (1) e o balde (5).
    assert categorias == {
        ("Candidato A", False): 4,
        ("Outros", True): 5,
        ("Outros", False): 1,
    }
    assert payload["resumo"]["total_filtrado"] == 10
