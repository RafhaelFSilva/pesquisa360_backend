"""PROMPT 04 -- Cota territorial por setor: ABERTO / ATENCAO / ENCERRADO.

CT-B01..CT-B10 sobre o motor oficial (`crud.obter_progressos_setores`,
`crud.calcular_cota_territorial`) e a missao do agente; CT-B11 sobre o endpoint
real de coletas: cota cheia NUNCA rejeita sincronizacao.
"""
import json
import os
import re
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely import wkb, wkt
from shapely import wkt as shapely_wkt
from shapely.geometry import mapping
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-cota-territorial-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import crud
from pesquisa360.api.endpoints import coletas
from pesquisa360.api.endpoints.agente import get_missao_agente
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from tests.acl_fixture import criar_tabelas_acl


def usuario(user_id=1, company_id=10, perfil="Agente"):
    return SimpleNamespace(
        id=user_id, company_id=company_id, ativo=True, perfil=SimpleNamespace(nome=perfil)
    )


class CotaTerritorialRegraTests(unittest.TestCase):
    """Regra pura, sem banco."""

    def status(self, meta, realizado):
        return crud.calcular_cota_territorial(meta, realizado)

    def test_CT_B01_meta_100_realizado_0_aberto(self):
        r = self.status(100, 0)
        self.assertEqual(r["status_cota"], "ABERTO")
        self.assertEqual(r["limite_atencao_realizado"], 90)
        self.assertEqual(r["restante"], 100)

    def test_CT_B02_meta_100_realizado_89_aberto(self):
        self.assertEqual(self.status(100, 89)["status_cota"], "ABERTO")

    def test_CT_B03_meta_100_realizado_90_atencao(self):
        self.assertEqual(self.status(100, 90)["status_cota"], "ATENCAO")

    def test_CT_B04_meta_100_realizado_99_atencao(self):
        r = self.status(100, 99)
        self.assertEqual(r["status_cota"], "ATENCAO")
        self.assertEqual(r["restante"], 1)
        self.assertFalse(r["cota_atingida"])

    def test_CT_B05_meta_100_realizado_100_encerrado(self):
        r = self.status(100, 100)
        self.assertEqual(r["status_cota"], "ENCERRADO")
        self.assertTrue(r["cota_atingida"])

    def test_CT_B06_meta_100_realizado_103_encerrado_excedente_3(self):
        r = self.status(100, 103)
        self.assertEqual(r["status_cota"], "ENCERRADO")
        self.assertEqual(r["restante"], 0)
        self.assertEqual(r["excedente"], 3)
        self.assertEqual(r["percentual_atingimento"], 103.0)

    def test_CT_B10_sem_meta_nunca_encerra(self):
        for meta in (None, 0, -5):
            r = self.status(meta, 50)
            self.assertEqual(r["status_cota"], "SEM_COTA", meta)
            self.assertIsNone(r["limite_atencao_realizado"])
            self.assertIsNone(r["cota_atingida"])

    def test_limiar_metas_pequenas(self):
        # ceil(meta * 0.9): meta 1 -> 1 (ENCERRADO vence), meta 3 -> 3,
        # meta 5 -> 5, meta 10 -> 9, meta 11 -> 10.
        self.assertEqual(crud.limite_atencao_realizado(1), 1)
        self.assertEqual(crud.limite_atencao_realizado(3), 3)
        self.assertEqual(crud.limite_atencao_realizado(10), 9)
        self.assertEqual(crud.limite_atencao_realizado(11), 10)
        self.assertEqual(self.status(10, 8)["status_cota"], "ABERTO")
        self.assertEqual(self.status(10, 9)["status_cota"], "ATENCAO")
        self.assertEqual(self.status(3, 2)["status_cota"], "ABERTO")
        self.assertEqual(self.status(3, 3)["status_cota"], "ENCERRADO")
        # Progresso legado permanece identico (contrato do Web).
        legado = crud.calcular_progresso_setor(200, 203)
        self.assertEqual(set(legado), {"meta", "realizado", "restante", "excedente", "percentual_atingimento", "cota_atingida"})


class CotaTerritorialMissaoTests(unittest.TestCase):
    """Snapshot por setor na missao do agente (fixture de test_setor_agentes)."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        raw = self.engine.raw_connection()
        raw.create_function("ST_GeomFromText", 2, lambda value, srid: value)
        raw.create_function(
            "ST_AsGeoJSON", 1,
            lambda value: json.dumps(mapping(shapely_wkt.loads(value))) if value else None,
        )
        raw.create_function(
            "AsGeoJSON", 1,
            lambda value: json.dumps(mapping(shapely_wkt.loads(value))) if value else None,
        )
        raw.create_function("AsEWKB", 1, lambda value: value)
        raw.close()
        criar_tabelas_acl(self.engine)
        self.Session = sessionmaker(bind=self.engine)
        with self.engine.begin() as connection:
            for statement in (
                "CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT, cnpj TEXT, logo_url TEXT, is_active BOOLEAN, created_at DATETIME)",
                "CREATE TABLE perfis (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT)",
                "CREATE TABLE usuarios (id INTEGER PRIMARY KEY, email TEXT, nome TEXT, senha_hash TEXT, ativo BOOLEAN, perfil_id INTEGER, company_id INTEGER)",
                "CREATE TABLE projetos (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT, status TEXT, data_inicio DATE, data_fim DATE, coordenador_id INTEGER, company_id INTEGER)",
                "CREATE TABLE pesquisas (id INTEGER PRIMARY KEY, titulo TEXT, tipo_pesquisa TEXT, ativo BOOLEAN, projeto_id INTEGER, cerca_eletronica TEXT, tolerancia_metros INTEGER)",
                "CREATE TABLE setores (id INTEGER PRIMARY KEY, nome TEXT, meta INTEGER, tolerancia INTEGER, finalidade TEXT, geometria TEXT, pesquisa_id INTEGER, agente_id INTEGER, municipio_territorio_id INTEGER)",
                "CREATE TABLE setor_agentes (id INTEGER PRIMARY KEY AUTOINCREMENT, setor_id INTEGER NOT NULL, agente_id INTEGER NOT NULL, ativo BOOLEAN NOT NULL DEFAULT 1, CONSTRAINT uq_setor_agentes_setor_agente UNIQUE (setor_id, agente_id))",
                "CREATE TABLE coletas (id INTEGER PRIMARY KEY, pesquisa_id INTEGER, agente_id INTEGER, setor_id INTEGER)",
                "CREATE TABLE territorio_eleitoral (id INTEGER PRIMARY KEY, base_eleitoral_id INTEGER, parent_id INTEGER, tipo TEXT, nome TEXT, nome_normalizado TEXT, municipio_id INTEGER)",
                "CREATE TABLE setor_territorio_eleitoral (id INTEGER PRIMARY KEY AUTOINCREMENT, setor_id INTEGER NOT NULL, territorio_eleitoral_id INTEGER NOT NULL, criado_em DATETIME)",
                "CREATE TABLE perguntas (id INTEGER PRIMARY KEY, texto_pergunta TEXT, tipo_pergunta TEXT, ordem INTEGER, eh_obrigatoria BOOLEAN, eh_resposta_espontanea BOOLEAN DEFAULT 0, papel_analitico TEXT, metadados_analiticos TEXT DEFAULT '{}', ativo BOOLEAN DEFAULT 1, pesquisa_id INTEGER, aplicabilidade TEXT NOT NULL DEFAULT 'GLOBAL')",
                "CREATE TABLE pergunta_territorio_eleitoral (id INTEGER PRIMARY KEY AUTOINCREMENT, pergunta_id INTEGER NOT NULL, territorio_eleitoral_id INTEGER NOT NULL)",
                # PROMPT 05: a missao consulta o plano de cotas de perfil.
                "CREATE TABLE planos_cota_perfil (id INTEGER PRIMARY KEY, pesquisa_id INTEGER NOT NULL, company_id INTEGER NOT NULL, ativo BOOLEAN NOT NULL DEFAULT 1, pergunta_sexo_id INTEGER NOT NULL, pergunta_idade_id INTEGER NOT NULL, modo_idade TEXT NOT NULL DEFAULT 'NUMERICA', sexo_valores TEXT NOT NULL, criado_em DATETIME, atualizado_em DATETIME)",
                "CREATE TABLE cotas_perfil (id INTEGER PRIMARY KEY, plano_id INTEGER NOT NULL, territorio_eleitoral_id INTEGER NOT NULL, sexo TEXT NOT NULL, faixa_rotulo TEXT NOT NULL, idade_min INTEGER, idade_max INTEGER, idade_valores TEXT, meta INTEGER NOT NULL DEFAULT 0, ordem INTEGER NOT NULL DEFAULT 0)",
            ):
                connection.execute(text(statement))
            connection.execute(text("INSERT INTO companies VALUES (10,'A',NULL,NULL,1,NULL),(20,'B',NULL,NULL,1,NULL)"))
            connection.execute(text("INSERT INTO perfis VALUES (1,'Gerente',NULL),(2,'Agente',NULL)"))
            connection.execute(text("""INSERT INTO usuarios VALUES
                (2,'a1@a','Agente 1','x',1,2,10),(3,'a2@a','Agente 2','x',1,2,10),
                (4,'a@b','Agente B','x',1,2,20),(6,'a3@a','Agente 3','x',1,2,10)"""))
            connection.execute(text("INSERT INTO projetos VALUES (100,'P',NULL,'Ativo',NULL,NULL,2,10),(200,'PB',NULL,'Ativo',NULL,NULL,4,20)"))
            connection.execute(text("INSERT INTO pesquisas VALUES (1000,'Q',NULL,1,100,NULL,NULL),(2000,'QB',NULL,1,200,NULL,NULL)"))
            connection.execute(text("""INSERT INTO setores (id, nome, meta, tolerancia, finalidade, geometria, pesquisa_id, agente_id) VALUES
                (500,'Centro',100,50,'OPERACAO',NULL,1000,NULL),
                (501,'Trem',0,50,'OPERACAO',NULL,1000,NULL),
                (502,'Legado',10,50,'OPERACAO',NULL,1000,6),
                (600,'B',100,50,'OPERACAO',NULL,2000,NULL)"""))
            connection.execute(text("""INSERT INTO setor_agentes (setor_id,agente_id,ativo) VALUES
                (500,2,1),(500,3,1),(500,6,0),(501,2,1),(502,2,1),(502,6,1),(600,4,1)"""))

    def tearDown(self):
        self.engine.dispose()

    def coletas(self, db, *, inicio, quantidade, setor_id, agente_id=2, pesquisa_id=1000):
        db.execute(
            text("INSERT INTO coletas (id, pesquisa_id, agente_id, setor_id) VALUES (:id, :p, :a, :s)"),
            [{"id": inicio + i, "p": pesquisa_id, "a": agente_id, "s": setor_id} for i in range(quantidade)],
        )
        db.commit()

    def missao(self, db, user_id=2, company_id=10, pesquisa_id=1000):
        with patch("pesquisa360.api.endpoints.agente.crud.get_pesquisa") as gp:
            gp.side_effect = lambda db, pesquisa_id, current_user: (
                object() if (pesquisa_id, current_user.company_id) in ((1000, 10), (2000, 20)) else None
            )
            return get_missao_agente(db=db, pesquisa_id=pesquisa_id, current_user=usuario(user_id, company_id))

    def test_CT_B07_snapshot_count_e_max_id_coerentes_em_uma_query(self):
        statements = []

        @event.listens_for(self.engine, "before_cursor_execute")
        def registrar(_c, _cur, statement, _p, _ctx, _m):
            if "FROM coletas" in statement:
                statements.append(statement)

        try:
            with self.Session() as db:
                self.coletas(db, inicio=1, quantidade=92, setor_id=500)
                self.coletas(db, inicio=5000, quantidade=3, setor_id=501)  # ids altos em outro setor
                self.coletas(db, inicio=9000, quantidade=1, setor_id=None)
                statements.clear()
                setores = crud.get_setores_by_pesquisa(db, 1000)
                progressos = crud.obter_progressos_setores(db, setores, pesquisa_id=1000)
        finally:
            event.remove(self.engine, "before_cursor_execute", registrar)

        self.assertEqual(len(statements), 1)
        self.assertIn("count(", statements[0].lower())
        self.assertIn("max(", statements[0].lower())
        p500 = progressos[500]
        self.assertEqual(p500["realizado"], 92)
        self.assertEqual(p500["snapshot_ate_coleta_id"], 92)  # nao 5002 nem 9000
        self.assertEqual(p500["status_cota"], "ATENCAO")
        self.assertEqual(p500["limite_atencao_realizado"], 90)
        self.assertIsNotNone(p500["snapshot_em"])
        self.assertEqual(progressos[501]["snapshot_ate_coleta_id"], 5002)
        self.assertEqual(progressos[501]["status_cota"], "SEM_COTA")
        self.assertIsNone(progressos[502]["snapshot_ate_coleta_id"])
        self.assertEqual(progressos[502]["status_cota"], "ABERTO")

    def test_missao_expoe_campos_da_cota_por_setor(self):
        with self.Session() as db:
            self.coletas(db, inicio=1, quantidade=100, setor_id=500)
            missao = self.missao(db)
        por_id = {s["id"]: s for s in missao["setores"]}
        centro = por_id[500]
        self.assertEqual(centro["status_cota"], "ENCERRADO")
        self.assertEqual(centro["realizado"], 100)
        self.assertEqual(centro["restante"], 0)
        self.assertEqual(centro["snapshot_ate_coleta_id"], 100)
        self.assertRegex(centro["snapshot_em"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertEqual(centro["agentes_atribuidos_total"], 2)
        self.assertEqual(missao["status_cota"], "ENCERRADO")
        # Campos legados intactos.
        self.assertTrue(centro["cota_atingida"])
        self.assertEqual(centro["percentual_atingimento"], 100.0)

    def test_CT_B08_setor_de_outro_tenant_nunca_aparece(self):
        from fastapi import HTTPException

        with self.Session() as db:
            missao_a = self.missao(db, user_id=2, company_id=10)
            missao_b = self.missao(db, user_id=4, company_id=20, pesquisa_id=2000)
            # Empresa B pedindo a pesquisa da empresa A: 404, sem vazar setores.
            with self.assertRaises(HTTPException) as ctx:
                self.missao(db, user_id=4, company_id=20, pesquisa_id=1000)
            self.assertEqual(ctx.exception.status_code, 404)
            # Agente da empresa A sem vinculo nao ve setores de ninguem.
            sem_vinculo = self.missao(db, user_id=3, company_id=10)
        self.assertEqual(sorted(s["id"] for s in missao_a["setores"]), [500, 501, 502])
        self.assertEqual([s["id"] for s in missao_b["setores"]], [600])
        self.assertEqual([s["id"] for s in sem_vinculo["setores"]], [500])

    def test_CT_B09_agentes_atribuidos_conta_so_vinculos_validos(self):
        with self.Session() as db:
            setores = crud.get_setores_by_pesquisa(db, 1000)
            progressos = crud.obter_progressos_setores(db, setores, pesquisa_id=1000)
        # 500: agentes 2 e 3 ativos; 6 inativo nao conta.
        self.assertEqual(progressos[500]["agentes_atribuidos_total"], 2)
        # 501: apenas 2.
        self.assertEqual(progressos[501]["agentes_atribuidos_total"], 1)
        # 502: N:N {2, 6} U legado agente_id=6 -> 2 distintos, sem duplicar.
        self.assertEqual(progressos[502]["agentes_atribuidos_total"], 2)


class CotaNaoRejeitaSincronizacaoTests(unittest.TestCase):
    """CT-B11: endpoint real de coletas com setor 100/100."""

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )

        @event.listens_for(cls.engine, "connect")
        def register(connection, _):
            def as_ewkb(value):
                if value is None:
                    return None
                if isinstance(value, bytes):
                    value = value.decode()
                return wkb.dumps(wkt.loads(str(value).split(";", 1)[-1]), hex=True, srid=4326)

            def coord(value, pos):
                if value is None:
                    return None
                m = re.search(r"POINT\s*\(([-+0-9.eE]+)\s+([-+0-9.eE]+)\)", str(value))
                return float(m.group(pos + 1)) if m else None

            connection.create_function("AsEWKB", 1, as_ewkb)
            connection.create_function("ST_AsEWKB", 1, as_ewkb)
            connection.create_function("GeomFromEWKT", 1, lambda v: v)
            connection.create_function("ST_Y", 1, lambda v: coord(v, 1))
            connection.create_function("ST_X", 1, lambda v: coord(v, 0))
            connection.create_function("ST_AsGeoJSON", 1, lambda v: None)
            connection.create_function("AsGeoJSON", 1, lambda v: None)

        criar_tabelas_acl(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)
        with cls.engine.begin() as c:
            # RBAC (ADR-037) resolve o papel pelo NOME do perfil.
            c.execute(text("CREATE TABLE perfis (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT)"))
            c.execute(text("INSERT INTO perfis VALUES (1,'Gerente',NULL),(2,'Agente',NULL),(99,'Agente',NULL)"))
            c.execute(text("CREATE TABLE usuarios (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, nome TEXT, senha_hash TEXT NOT NULL, ativo BOOLEAN NOT NULL, perfil_id INTEGER NOT NULL, company_id INTEGER NOT NULL)"))
            c.execute(text("CREATE TABLE projetos (id INTEGER PRIMARY KEY, nome TEXT NOT NULL, descricao TEXT, status TEXT NOT NULL, data_inicio DATE, data_fim DATE, coordenador_id INTEGER NOT NULL, company_id INTEGER NOT NULL)"))
            c.execute(text("CREATE TABLE pesquisas (id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, tipo_pesquisa TEXT, ativo BOOLEAN NOT NULL, projeto_id INTEGER NOT NULL, cerca_eletronica BLOB, tolerancia_metros INTEGER)"))
            c.execute(text("CREATE TABLE perguntas (id INTEGER PRIMARY KEY, texto_pergunta TEXT NOT NULL, tipo_pergunta TEXT NOT NULL, ordem INTEGER NOT NULL, eh_obrigatoria BOOLEAN NOT NULL, eh_resposta_espontanea BOOLEAN NOT NULL DEFAULT 0, papel_analitico VARCHAR(50), metadados_analiticos JSON NOT NULL DEFAULT '{}', ativo BOOLEAN NOT NULL, pesquisa_id INTEGER NOT NULL, aplicabilidade VARCHAR(20) NOT NULL DEFAULT 'GLOBAL')"))
            c.execute(text("CREATE TABLE setores (id INTEGER PRIMARY KEY, nome TEXT NOT NULL, meta INTEGER NOT NULL, tolerancia INTEGER NOT NULL DEFAULT 50, finalidade TEXT NOT NULL DEFAULT 'OPERACAO', geometria BLOB, pesquisa_id INTEGER NOT NULL, agente_id INTEGER, municipio_territorio_id INTEGER)"))
            c.execute(text("CREATE TABLE setor_agentes (id INTEGER PRIMARY KEY AUTOINCREMENT, setor_id INTEGER NOT NULL, agente_id INTEGER NOT NULL, ativo BOOLEAN NOT NULL DEFAULT 1, UNIQUE (setor_id, agente_id))"))
            c.execute(text("CREATE TABLE coletas (id INTEGER PRIMARY KEY AUTOINCREMENT, pesquisa_id INTEGER NOT NULL, agente_id INTEGER NOT NULL, company_id INTEGER NOT NULL, client_uuid TEXT NOT NULL, setor_id INTEGER, foi_offline BOOLEAN, endereco_estimado TEXT, status_sincronizacao TEXT, data_inicio_coleta DATETIME NOT NULL, data_fim_coleta DATETIME, localizacao_inicio BLOB, localizacao_fim BLOB, inconformidade_localizacao BOOLEAN NOT NULL DEFAULT 0, CONSTRAINT uq_coletas_company_client_uuid UNIQUE (company_id, client_uuid))"))
            c.execute(text("CREATE TABLE respostas (id INTEGER PRIMARY KEY AUTOINCREMENT, pergunta_id INTEGER NOT NULL, coleta_id INTEGER NOT NULL, valor_resposta TEXT NOT NULL)"))
            c.execute(text("INSERT INTO usuarios VALUES (1,'a1@a','A1','h',1,99,10)"))
            c.execute(text("INSERT INTO projetos (id,nome,status,coordenador_id,company_id) VALUES (10,'P','Ativo',1,10)"))
            c.execute(text("INSERT INTO pesquisas (id,titulo,ativo,projeto_id) VALUES (100,'Q',1,10)"))
            c.execute(text("INSERT INTO perguntas (id,texto_pergunta,tipo_pergunta,ordem,eh_obrigatoria,ativo,pesquisa_id) VALUES (1000,'P1','TEXTO',1,1,1,100)"))
            c.execute(text("INSERT INTO setores (id,nome,meta,pesquisa_id) VALUES (500,'Centro',3,100)"))
            c.execute(text("INSERT INTO setor_agentes (setor_id,agente_id,ativo) VALUES (500,1,1)"))

        app = FastAPI()
        app.include_router(coletas.router)

        def override_db():
            db = cls.Session()
            try:
                yield db
            finally:
                db.close()

        def override_user():
            db = cls.Session()
            try:
                yield db.get(models.Usuario, 1)
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.engine.dispose()

    def post_coleta(self):
        return self.client.post(
            "/pesquisas/100/coletas/",
            json={
                "client_uuid": str(uuid4()),
                "setor_id": 500,
                "data_inicio_coleta": "2026-08-27T10:00:00-03:00",
                "data_fim_coleta": "2026-08-27T10:05:00-03:00",
                "foi_offline": True,
                "respostas": [{"pergunta_id": 1000, "valor_resposta": "ok"}],
            },
        )

    def test_CT_B11_cota_cheia_nao_rejeita_coleta_offline_legitima(self):
        with patch("pesquisa360.crud.geocoding.obter_endereco_por_coords", return_value="x"):
            for _ in range(3):
                self.assertEqual(self.post_coleta().status_code, 201)
            with self.Session() as db:
                setores = crud.get_setores_by_pesquisa(db, 100)
                cheio = crud.obter_progressos_setores(db, setores, pesquisa_id=100)[500]
            self.assertEqual(cheio["status_cota"], "ENCERRADO")
            self.assertEqual(cheio["realizado"], 3)

            # Dispositivo offline que nao conhecia o encerramento: ACEITA.
            tardia = self.post_coleta()
            self.assertEqual(tardia.status_code, 201, tardia.text)

            with self.Session() as db:
                setores = crud.get_setores_by_pesquisa(db, 100)
                depois = crud.obter_progressos_setores(db, setores, pesquisa_id=100)[500]
            self.assertEqual(depois["realizado"], 4)
            self.assertEqual(depois["excedente"], 1)
            self.assertEqual(depois["status_cota"], "ENCERRADO")
            self.assertEqual(depois["snapshot_ate_coleta_id"], 4)


if __name__ == "__main__":
    unittest.main()
