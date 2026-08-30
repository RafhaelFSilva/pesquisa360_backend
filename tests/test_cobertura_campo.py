"""PROMPT 06 -- Cobertura territorial de campo (CB-B01..CB-B13).

Atividade CONHECIDA (snapshot), orientativa, sem dados pessoais. A regra
critica: entrevista concluida (Coleta + TentativaCampo CONCLUIDA vinculada)
aparece UMA vez.
"""
import os
import re
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely import wkb, wkt
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-cobertura-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import cobertura_campo, coletas, tentativas_campo
from pesquisa360.core.dependencies import get_current_user, get_db, require_manager_or_superadmin
from pesquisa360.db import models
from pesquisa360.services import cobertura_campo as svc
from tests.acl_fixture import criar_tabelas_acl


class CoberturaCampoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

        @event.listens_for(cls.engine, "connect")
        def register(connection, _):
            def coord(value, pos):
                if value is None:
                    return None
                if isinstance(value, bytes):
                    value = value.decode()
                m = re.search(r"POINT\s*\(([-+0-9.eE]+)\s+([-+0-9.eE]+)\)", str(value))
                return float(m.group(pos + 1)) if m else None

            def as_ewkb(value):
                if value is None:
                    return None
                if isinstance(value, bytes):
                    value = value.decode()
                return wkb.dumps(wkt.loads(str(value).split(";", 1)[-1]), hex=True, srid=4326)

            connection.create_function("AsEWKB", 1, as_ewkb)
            connection.create_function("ST_AsEWKB", 1, as_ewkb)
            connection.create_function("GeomFromEWKT", 1, lambda v: v)
            connection.create_function("ST_Y", 1, lambda v: coord(v, 1))
            connection.create_function("ST_X", 1, lambda v: coord(v, 0))
            connection.create_function("ST_AsGeoJSON", 1, lambda v: None)
            connection.create_function("AsGeoJSON", 1, lambda v: None)

        criar_tabelas_acl(cls.engine)

        criar_tabelas_acl(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)
        with cls.engine.begin() as c:
            c.execute(text("CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT)"))
            c.execute(text("CREATE TABLE usuarios (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, nome TEXT, senha_hash TEXT NOT NULL, ativo BOOLEAN NOT NULL, perfil_id INTEGER NOT NULL, company_id INTEGER NOT NULL)"))
            c.execute(text("CREATE TABLE perfis (id INTEGER PRIMARY KEY, nome TEXT, descricao TEXT)"))
            # RBAC (ADR-037) le o perfil do usuario: a fixture precisa te-lo.
            c.execute(text("INSERT INTO perfis (id,nome) VALUES (1,'Gerente'),(99,'Gerente')"))
            c.execute(text("CREATE TABLE projetos (id INTEGER PRIMARY KEY, nome TEXT NOT NULL, descricao TEXT, status TEXT NOT NULL, data_inicio DATE, data_fim DATE, coordenador_id INTEGER NOT NULL, company_id INTEGER NOT NULL)"))
            c.execute(text("CREATE TABLE pesquisas (id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, tipo_pesquisa TEXT, ativo BOOLEAN NOT NULL, projeto_id INTEGER NOT NULL, cerca_eletronica BLOB, tolerancia_metros INTEGER)"))
            c.execute(text("CREATE TABLE perguntas (id INTEGER PRIMARY KEY, texto_pergunta TEXT NOT NULL, tipo_pergunta TEXT NOT NULL, ordem INTEGER NOT NULL, eh_obrigatoria BOOLEAN NOT NULL, eh_resposta_espontanea BOOLEAN NOT NULL DEFAULT 0, papel_analitico VARCHAR(50), metadados_analiticos JSON NOT NULL DEFAULT '{}', ativo BOOLEAN NOT NULL, pesquisa_id INTEGER NOT NULL, aplicabilidade VARCHAR(20) NOT NULL DEFAULT 'GLOBAL')"))
            c.execute(text("CREATE TABLE setores (id INTEGER PRIMARY KEY, nome TEXT NOT NULL, meta INTEGER NOT NULL, tolerancia INTEGER NOT NULL DEFAULT 50, finalidade TEXT NOT NULL DEFAULT 'OPERACAO', geometria BLOB, pesquisa_id INTEGER NOT NULL, agente_id INTEGER, municipio_territorio_id INTEGER)"))
            c.execute(text("CREATE TABLE setor_agentes (id INTEGER PRIMARY KEY AUTOINCREMENT, setor_id INTEGER NOT NULL, agente_id INTEGER NOT NULL, ativo BOOLEAN NOT NULL DEFAULT 1, UNIQUE (setor_id, agente_id))"))
            c.execute(text("CREATE TABLE coletas (id INTEGER PRIMARY KEY AUTOINCREMENT, pesquisa_id INTEGER NOT NULL, agente_id INTEGER NOT NULL, company_id INTEGER NOT NULL, client_uuid TEXT NOT NULL, setor_id INTEGER, foi_offline BOOLEAN, endereco_estimado TEXT, status_sincronizacao TEXT, data_inicio_coleta DATETIME NOT NULL, data_fim_coleta DATETIME, localizacao_inicio BLOB, localizacao_fim BLOB, inconformidade_localizacao BOOLEAN NOT NULL DEFAULT 0, CONSTRAINT uq_coletas_company_client_uuid UNIQUE (company_id, client_uuid))"))
            c.execute(text("CREATE TABLE respostas (id INTEGER PRIMARY KEY AUTOINCREMENT, pergunta_id INTEGER NOT NULL, coleta_id INTEGER NOT NULL, valor_resposta TEXT NOT NULL)"))
        models.TentativaCampo.__table__.create(cls.engine)
        models.ConfiguracaoCampoPesquisa.__table__.create(cls.engine)

        cls.current_user_id = 1
        app = FastAPI()
        app.include_router(coletas.router)
        app.include_router(tentativas_campo.router)
        app.include_router(cobertura_campo.router)

        def override_db():
            db = cls.Session()
            try:
                yield db
            finally:
                db.close()

        def override_user():
            db = cls.Session()
            try:
                yield db.get(models.Usuario, cls.current_user_id)
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user
        app.dependency_overrides[require_manager_or_superadmin] = override_user
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.engine.dispose()

    def setUp(self):
        p = patch("pesquisa360.crud.geocoding.obter_endereco_por_coords", return_value="Rua X, 1 - Nome Pessoa")
        p.start()
        self.addCleanup(p.stop)
        type(self).current_user_id = 1
        with self.engine.begin() as c:
            for t in ("configuracoes_campo_pesquisa", "tentativas_campo", "respostas", "coletas", "setor_agentes", "setores", "perguntas", "pesquisas", "projetos", "usuarios", "companies"):
                c.execute(text(f"DELETE FROM {t}"))
            c.execute(text("INSERT INTO companies VALUES (10,'A'),(20,'B')"))
            c.execute(text("INSERT INTO usuarios VALUES (1,'a1@a','A1','h',1,99,10),(2,'a2@a','A2','h',1,99,10),(3,'b@b','B','h',1,99,20),(9,'g@a','Gerente','h',1,1,10)"))
            c.execute(text("INSERT INTO projetos (id,nome,status,coordenador_id,company_id) VALUES (10,'P','Ativo',1,10),(20,'PB','Ativo',3,20)"))
            c.execute(text("INSERT INTO pesquisas (id,titulo,ativo,projeto_id) VALUES (100,'Q',1,10),(200,'QB',1,20)"))
            c.execute(text("INSERT INTO perguntas (id,texto_pergunta,tipo_pergunta,ordem,eh_obrigatoria,ativo,pesquisa_id) VALUES (1000,'Nome','TEXTO',1,1,1,100),(2000,'P','TEXTO',1,1,1,200)"))
            c.execute(text("INSERT INTO setores (id,nome,meta,pesquisa_id) VALUES (500,'Centro',10,100),(501,'Norte',10,100),(502,'Sul',10,100),(600,'B',10,200)"))
            # agente 1: setores 500 e 501; agente 2: so 502; agente 3 (tenant B): 600
            c.execute(text("INSERT INTO setor_agentes (setor_id,agente_id,ativo) VALUES (500,1,1),(501,1,1),(502,2,1),(600,3,1),(502,1,0)"))

    # ------------------------------------------------------------ helpers
    def coleta(self, setor_id, lat=0.0349, lon=-51.0694, pesquisa_id=100, sem_gps=False, agente=None):
        anterior = type(self).current_user_id
        if agente:
            type(self).current_user_id = agente
        try:
            uuid = str(uuid4())
            body = {
                "client_uuid": uuid, "setor_id": setor_id,
                "data_inicio_coleta": "2026-08-27T12:00:00Z", "data_fim_coleta": "2026-08-27T12:05:00Z",
                "respostas": [{"pergunta_id": 1000 if pesquisa_id == 100 else 2000, "valor_resposta": "Fulano da Silva CPF 123"}],
            }
            if not sem_gps:
                body["localizacao_inicio"] = {"lat": lat, "lon": lon}
            r = self.client.post(f"/pesquisas/{pesquisa_id}/coletas/", json=body)
            self.assertEqual(r.status_code, 201, r.text)
            return r.json()["id"], uuid
        finally:
            type(self).current_user_id = anterior

    def tentativa(self, setor_id, resultado, lat=0.0354, lng=-51.07, coleta_uuid=None, pesquisa_id=100, agente=None):
        anterior = type(self).current_user_id
        if agente:
            type(self).current_user_id = agente
        try:
            r = self.client.post(f"/pesquisas/{pesquisa_id}/tentativas-campo/", json={
                "client_uuid": str(uuid4()), "setor_id": setor_id,
                "iniciada_em": "2026-08-27T12:03:00Z", "encerrada_em": "2026-08-27T12:04:00Z",
                "localizacao": {"lat": lat, "lng": lng, "accuracy": 10.2, "capturada_em": "2026-08-27T12:03:00Z"},
                "resultado": resultado, "motivo": None if resultado == "CONCLUIDA" else "OUTRO",
                "coleta_client_uuid": coleta_uuid,
            })
            self.assertEqual(r.status_code, 201, r.text)
            return r.json()["id"]
        finally:
            type(self).current_user_id = anterior

    def cobertura(self, pesquisa_id=100):
        return self.client.get(f"/agente/pesquisas/{pesquisa_id}/cobertura-campo/")

    def eventos(self, pesquisa_id=100):
        r = self.cobertura(pesquisa_id)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()["eventos"]

    # ------------------------------------------------------------ testes
    def test_CB_B01_coleta_valida_aparece_como_COLETA(self):
        cid, _ = self.coleta(500)
        ev = self.eventos()
        self.assertEqual(len(ev), 1)
        e = ev[0]
        self.assertEqual(e["tipo"], "COLETA")
        self.assertEqual(e["server_id"], cid)
        self.assertEqual(e["setor_id"], 500)
        self.assertAlmostEqual(e["lat"], 0.0349)
        self.assertAlmostEqual(e["lng"], -51.0694)
        self.assertIsNone(e["resultado"])
        self.assertTrue(e["ocorrido_em"].startswith("2026-08-27"))

    def test_CB_B02_B03_B04_tentativas_aparecem_como_TENTATIVA(self):
        ids = {r: self.tentativa(500, r) for r in ("RECUSA", "NAO_ELEGIVEL", "DESISTENCIA")}
        ev = self.eventos()
        self.assertEqual(sorted(e["resultado"] for e in ev), ["DESISTENCIA", "NAO_ELEGIVEL", "RECUSA"])
        for e in ev:
            self.assertEqual(e["tipo"], "TENTATIVA")
            self.assertEqual(e["server_id"], ids[e["resultado"]])
            self.assertAlmostEqual(e["accuracy"], 10.2)

    def test_CB_B05_concluida_vinculada_a_coleta_e_um_unico_evento(self):
        cid, uuid = self.coleta(500)
        tid = self.tentativa(500, "CONCLUIDA", coleta_uuid=uuid)
        ev = self.eventos()
        self.assertEqual(len(ev), 1, ev)
        self.assertEqual((ev[0]["tipo"], ev[0]["server_id"]), ("COLETA", cid))
        # CONCLUIDA SEM vinculo (nao deveria ocorrer, mas nao some): 1 TENTATIVA.
        with self.engine.begin() as c:
            c.execute(text("UPDATE tentativas_campo SET coleta_id = NULL WHERE id = :t"), {"t": tid})
        ev = self.eventos()
        self.assertEqual(sorted(e["tipo"] for e in ev), ["COLETA", "TENTATIVA"])

    def test_CB_B06_em_andamento_nao_aparece(self):
        with self.engine.begin() as c:
            c.execute(text(
                "INSERT INTO tentativas_campo (client_uuid, pesquisa_id, setor_id, agente_id, company_id, iniciada_em, latitude, longitude, resultado)"
                " VALUES ('u1', 100, 500, 1, 10, '2026-08-27 12:00:00', 0.03, -51.06, 'EM_ANDAMENTO')"
            ))
        self.assertEqual(self.eventos(), [])

    def test_CB_B07_gps_nulo_ou_invalido_nao_gera_evento(self):
        self.coleta(500, sem_gps=True)
        with self.engine.begin() as c:
            c.execute(text(
                "INSERT INTO tentativas_campo (client_uuid, pesquisa_id, setor_id, agente_id, company_id, iniciada_em, encerrada_em, latitude, longitude, resultado)"
                " VALUES ('u2', 100, 500, 1, 10, '2026-08-27 12:00:00', '2026-08-27 12:01:00', 95.0, -51.06, 'RECUSA')"
            ))
        self.assertEqual(self.eventos(), [])

    def test_CB_B08_outro_tenant_404_e_nunca_aparece(self):
        self.coleta(600, pesquisa_id=200, agente=3)
        r = self.cobertura(200)  # agente 1 (tenant A) pedindo pesquisa de B
        self.assertEqual(r.status_code, 404)
        self.coleta(500)
        self.assertEqual({e["setor_id"] for e in self.eventos()}, {500})

    def test_CB_B09_agente_nao_atribuido_nao_recebe_setor(self):
        self.coleta(502, agente=2)          # setor do agente 2
        self.tentativa(502, "RECUSA", agente=2)
        self.coleta(500)
        ev = self.eventos()                 # agente 1: vinculo 502 inativo
        self.assertEqual({e["setor_id"] for e in ev}, {500})
        self.assertEqual(self.cobertura().json()["setor_ids"], [500, 501])
        type(self).current_user_id = 2
        self.assertEqual({e["setor_id"] for e in self.eventos()}, {502})

    def test_CB_B10_dois_setores_permitidos_separados(self):
        self.coleta(500, lat=0.03, lon=-51.06)
        self.coleta(501, lat=0.04, lon=-51.07)
        self.tentativa(501, "RECUSA")
        ev = self.eventos()
        por_setor = {}
        for e in ev:
            por_setor.setdefault(e["setor_id"], []).append(e["tipo"])
        self.assertEqual(por_setor, {500: ["COLETA"], 501: ["COLETA", "TENTATIVA"]})

    def test_CB_B11_sem_dados_pessoais(self):
        self.coleta(500)
        self.tentativa(500, "RECUSA")
        r = self.cobertura()
        corpo = r.text
        for proibido in ("Fulano", "CPF", "respostas", "valor_resposta", "endereco", "agente_id", "nome", "email", "client_uuid"):
            self.assertNotIn(proibido, corpo, proibido)
        chaves = set().union(*(e.keys() for e in r.json()["eventos"]))
        self.assertEqual(chaves, {"tipo", "server_id", "setor_id", "lat", "lng", "accuracy", "ocorrido_em", "resultado"})

    def test_CB_B12_problema_tecnico_e_outro_aparecem_como_TENTATIVA(self):
        # Decisao: comprovam presenca fisica -> atividade de campo, sinalizada
        # pelo `resultado` para a UI diferenciar; nunca contam cota/perfil.
        self.tentativa(500, "PROBLEMA_TECNICO")
        self.tentativa(500, "OUTRO")
        self.tentativa(500, "INCOMPLETA")
        ev = self.eventos()
        self.assertEqual(sorted(e["resultado"] for e in ev), ["INCOMPLETA", "OUTRO", "PROBLEMA_TECNICO"])
        self.assertTrue(all(e["tipo"] == "TENTATIVA" for e in ev))

    def test_CB_B13_distancia_recomendada_default_e_configuracao(self):
        body = self.cobertura().json()
        self.assertEqual(body["distancia_recomendada_entre_abordagens_metros"], svc.DISTANCIA_RECOMENDADA_PADRAO_METROS)
        self.assertEqual(body["distancia_recomendada_entre_abordagens_metros"], 100)
        self.assertFalse(body["distancia_configurada"])
        self.assertRegex(body["snapshot_em"], r"^\d{4}-")

        type(self).current_user_id = 9  # gerente
        rota = "/projetos/10/pesquisas/100/configuracao-campo"
        self.assertEqual(self.client.put(rota, json={"distancia_recomendada_entre_abordagens_metros": 0}).status_code, 422)
        self.assertEqual(self.client.put(rota, json={"distancia_recomendada_entre_abordagens_metros": -5}).status_code, 422)
        r = self.client.put(rota, json={"distancia_recomendada_entre_abordagens_metros": 150})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["distancia_recomendada_entre_abordagens_metros"], 150)
        self.assertTrue(r.json()["distancia_configurada"])
        self.assertEqual(self.client.get(rota).json()["distancia_recomendada_entre_abordagens_metros"], 150)
        # Outro tenant: 404.
        self.assertEqual(self.client.put("/projetos/20/pesquisas/200/configuracao-campo", json={"distancia_recomendada_entre_abordagens_metros": 50}).status_code, 404)
        # Limpar volta ao default.
        r = self.client.put(rota, json={"distancia_recomendada_entre_abordagens_metros": None})
        self.assertEqual(r.json()["distancia_recomendada_entre_abordagens_metros"], 100)
        self.assertFalse(r.json()["distancia_configurada"])

        type(self).current_user_id = 1
        self.assertEqual(self.cobertura().json()["distancia_recomendada_entre_abordagens_metros"], 100)

    def test_pesquisa_sem_setores_retorna_vazio_sem_erro(self):
        with self.engine.begin() as c:
            c.execute(text("DELETE FROM setor_agentes"))
        body = self.cobertura().json()
        self.assertEqual(body["setor_ids"], [])
        self.assertEqual(body["eventos"], [])


if __name__ == "__main__":
    unittest.main()
