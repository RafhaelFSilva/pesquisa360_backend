"""PROMPT 08 -- QA integrado de campo (Backend).

Prova, em um unico fluxo, que GPS -> Setor -> Cota Territorial -> TentativaCampo
-> Coleta -> Cota de Perfil -> Cobertura -> Sincronizacao -> Painel Web contam o
MESMO evento de forma compativel. Duas familias:

* MigrationGatesQA: cadeia Alembic real (banco vazio, upgrade sobre base legada
  com dados, downgrade/re-upgrade controlado).
* FluxoCampoIntegradoQA: cenario A/B/C (ABERTO/ATENCAO/ENCERRADO), plano de
  perfil Homem 60+ ALTO e Homem 45-59 MEDIO, abordagens, entrevistas,
  idempotencia, concorrencia 9/10 + A + B, multitenancy, dados pessoais,
  filtros do painel, pesquisa legada e custo em consultas.

Os payloads reais sao gravados em tests/fixtures_qa/*.json para os testes de
contrato do Web e do Mobile (PROMPT 08 §47/§52).
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely import wkb, wkt
from shapely.geometry import mapping
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-qa-campo-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import agente as rotas_agente
from pesquisa360.api.endpoints import cobertura_campo as rotas_cobertura
from pesquisa360.api.endpoints import coletas as rotas_coletas
from pesquisa360.api.endpoints import controle_campo as rotas_painel
from pesquisa360.api.endpoints import cotas_perfil as rotas_cotas
from pesquisa360.api.endpoints import tentativas_campo as rotas_tentativas
from pesquisa360.core.dependencies import get_current_user, get_db, require_manager_or_superadmin
from pesquisa360.services import cota_perfil as svc_perfil
from tests.test_base_eleitoral_import import run_alembic_upgrade

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_QA = Path(__file__).resolve().parent / "fixtures_qa"

HEAD = "c6d7e8f9a0b1"
ANTES_DAS_NOVAS = "e6f7a8b9c0d1"   # estado imediatamente anterior a f7a8b9c0d1e2
NOVAS = ["f7a8b9c0d1e2", "a8b9c0d1e2f3", "b9c0d1e2f3a4", "c0d1e2f3a4b5", HEAD]
TABELAS_NOVAS = ["tentativas_campo", "planos_cota_perfil", "cotas_perfil", "configuracoes_campo_pesquisa"]

COMPANY, COMPANY_B = 10, 20
PROJETO, PROJETO_LEGADO, PROJETO_B = 100, 101, 200
PESQUISA, PESQUISA_LEGADA, PESQUISA_B = 1000, 1001, 2000
MACAPA, SANTANA = 900, 901
A, B, C, D_SANTANA, LEGADO, SETOR_B = 500, 501, 502, 510, 520, 600
AG1, AG2, AG_B, GERENTE, GERENTE_B = 2, 4, 5, 1, 3
GPS = (0.0349, -51.0694)


def sem_snapshot(bloco: dict) -> dict:
    return {k: v for k, v in bloco.items() if k != "snapshot_em"}


def usuario(user_id=GERENTE, company_id=COMPANY, perfil="Gerente"):
    return SimpleNamespace(
        id=user_id, email=f"u{user_id}@a", company_id=company_id, ativo=True,
        perfil=SimpleNamespace(nome=perfil),
    )


# =============================================================================
# GATES 1-3: MIGRATIONS
# =============================================================================
class MigrationGatesQA(unittest.TestCase):
    def alembic(self, *args, url):
        env = os.environ.copy()
        env["DATABASE_URL"] = url
        done = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=PROJECT_ROOT, env=env, capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, f"alembic {' '.join(args)}\n{done.stdout}\n{done.stderr}")
        return done.stdout + done.stderr

    def tabelas(self, path):
        con = sqlite3.connect(path)
        try:
            return {r[0]: r[1] or "" for r in con.execute("SELECT name, sql FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()

    def seed_legado(self, path):
        con = sqlite3.connect(path)
        try:
            company = con.execute("SELECT id FROM companies ORDER BY id LIMIT 1").fetchone()[0]
            perfil = con.execute("SELECT id FROM perfis ORDER BY id LIMIT 1").fetchone()[0]
            con.execute("INSERT INTO usuarios (id,email,nome,senha_hash,ativo,perfil_id,company_id) VALUES (900,'legado@a','Legado','h',1,?,?)", (perfil, company))
            con.execute("INSERT INTO projetos (id,nome,status,data_inicio,coordenador_id,company_id) VALUES (901,'P legado','Ativo','2026-01-01',900,?)", (company,))
            con.execute("INSERT INTO pesquisas (id,titulo,ativo,projeto_id) VALUES (902,'Q legada',1,901)")
            con.execute("INSERT INTO setores (id,nome,meta,geometria,pesquisa_id,agente_id,tolerancia,finalidade) VALUES (990,'Setor legado',10,NULL,902,900,50,'OPERACAO')")
            con.execute("INSERT INTO perguntas (id,texto_pergunta,tipo_pergunta,ordem,eh_obrigatoria,ativo,pesquisa_id) VALUES (950,'Idade','NUMERO',1,1,1,902)")
            for i in range(3):
                con.execute(
                    "INSERT INTO coletas (id,pesquisa_id,agente_id,company_id,client_uuid,setor_id,data_inicio_coleta,foi_offline,status_sincronizacao,inconformidade_localizacao)"
                    " VALUES (?,902,900,?,?,990,'2026-01-01 10:00:00',0,'sincronizado',0)", (910 + i, company, f"legado-{i}"))
                con.execute("INSERT INTO respostas (id,valor_resposta,pergunta_id,coleta_id) VALUES (?, ?, 950, ?)", (960 + i, str(30 + i), 910 + i))
            con.commit()
            return company
        finally:
            con.close()

    def snapshot_legado(self, path):
        con = sqlite3.connect(path)
        try:
            return {
                "coletas": con.execute("SELECT id, client_uuid, setor_id, pesquisa_id, agente_id FROM coletas ORDER BY id").fetchall(),
                "respostas": con.execute("SELECT id, valor_resposta, pergunta_id, coleta_id FROM respostas ORDER BY id").fetchall(),
                "setores": con.execute("SELECT id, nome, meta, pesquisa_id FROM setores ORDER BY id").fetchall(),
                "pesquisas": con.execute("SELECT id, titulo, projeto_id FROM pesquisas ORDER BY id").fetchall(),
                "usuarios": con.execute("SELECT id, email FROM usuarios WHERE id = 900").fetchall(),
            }
        finally:
            con.close()

    def test_gate1_banco_vazio_ate_head_com_tabelas_e_constraints(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "g1.sqlite"
            url = f"sqlite:///{path.as_posix()}"
            self.assertIn(HEAD, self.alembic("heads", url=url))
            self.alembic("upgrade", "head", url=url)
            self.assertIn(HEAD, self.alembic("current", url=url))
            tabelas = self.tabelas(path)
            for t in TABELAS_NOVAS:
                self.assertIn(t, tabelas, t)
            self.assertIn("uq_tentativas_campo_company_client_uuid", tabelas["tentativas_campo"])
            self.assertIn("uq_planos_cota_perfil_pesquisa", tabelas["planos_cota_perfil"])
            self.assertIn("uq_configuracoes_campo_pesquisa", tabelas["configuracoes_campo_pesquisa"])
            self.assertIn("ck_cotas_perfil_meta", tabelas["cotas_perfil"])
            for coluna in ("client_uuid", "resultado", "coleta_id", "latitude", "longitude", "capturada_em"):
                self.assertIn(coluna, tabelas["tentativas_campo"], coluna)

    def test_gate2_upgrade_sobre_base_legada_preserva_dados_e_ids(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "g2.sqlite"
            url = f"sqlite:///{path.as_posix()}"
            self.alembic("upgrade", ANTES_DAS_NOVAS, url=url)
            self.assertIn(ANTES_DAS_NOVAS, self.alembic("current", url=url))
            for t in TABELAS_NOVAS:
                self.assertNotIn(t, self.tabelas(path))
            self.seed_legado(path)
            antes = self.snapshot_legado(path)
            self.alembic("upgrade", "head", url=url)
            self.assertIn(HEAD, self.alembic("current", url=url))
            self.assertEqual(self.snapshot_legado(path), antes)          # nenhum id/valor alterado
            self.assertEqual(len(antes["coletas"]), 3)
            self.assertEqual(len(antes["respostas"]), 3)
            tabelas = self.tabelas(path)
            for t in TABELAS_NOVAS:
                self.assertIn(t, tabelas, t)
            con = sqlite3.connect(path)
            try:
                con.execute(
                    "INSERT INTO tentativas_campo (client_uuid,pesquisa_id,setor_id,agente_id,company_id,iniciada_em,latitude,longitude,resultado,coleta_id)"
                    " VALUES ('t1',902,990,900,10,'2026-08-27 12:00:00',0.03,-51.06,'CONCLUIDA',910)")
                con.execute("INSERT INTO configuracoes_campo_pesquisa (pesquisa_id, company_id, distancia_recomendada_entre_abordagens_metros) VALUES (902, 10, 150)")
                con.commit()
                self.assertEqual(con.execute("SELECT coleta_id FROM tentativas_campo").fetchone()[0], 910)
            finally:
                con.close()

    def test_gate3_downgrade_controlado_e_reupgrade(self):
        with TemporaryDirectory() as d:
            path = Path(d) / "g3.sqlite"
            url = f"sqlite:///{path.as_posix()}"
            self.alembic("upgrade", ANTES_DAS_NOVAS, url=url)
            self.seed_legado(path)
            antes = self.snapshot_legado(path)
            self.alembic("upgrade", "head", url=url)
            # Reverte SO a cadeia nova (nivel ja coberto pela suite: revisao-a-revisao).
            for alvo in ["b9c0d1e2f3a4", "a8b9c0d1e2f3", "f7a8b9c0d1e2", ANTES_DAS_NOVAS]:
                self.alembic("downgrade", alvo, url=url)
                self.assertIn(alvo, self.alembic("current", url=url))
            for t in TABELAS_NOVAS:
                self.assertNotIn(t, self.tabelas(path), t)
            self.assertEqual(self.snapshot_legado(path), antes)
            self.alembic("upgrade", "head", url=url)
            self.assertIn(HEAD, self.alembic("current", url=url))
            for t in TABELAS_NOVAS:
                self.assertIn(t, self.tabelas(path), t)
            self.assertEqual(self.snapshot_legado(path), antes)
            self.assertEqual(self.alembic("heads", url=url).count("(head)"), 1)


# =============================================================================
# FLUXO INTEGRADO
# =============================================================================
class FluxoCampoIntegradoQA(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._dir = TemporaryDirectory()
        cls.db_path = Path(cls._dir.name) / "qa.sqlite"
        run_alembic_upgrade(f"sqlite:///{cls.db_path.as_posix()}")

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def setUp(self):
        self.engine = create_engine(f"sqlite:///{self.db_path.as_posix()}")

        @event.listens_for(self.engine, "connect")
        def _funcoes(conexao, _):
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

            conexao.create_function("ST_GeomFromText", -1, lambda *a: a[0])
            def as_geojson(value):
                if value is None:
                    return None
                if isinstance(value, bytes):
                    value = value.decode()
                return json.dumps(mapping(wkt.loads(str(value).split(";", 1)[-1])))

            def from_ewkb(value):
                if value is None:
                    return None
                geom = wkb.loads(value, hex=True) if isinstance(value, str) else wkb.loads(bytes(value))
                return f"SRID=4326;{geom.wkt}"

            conexao.create_function("GeomFromEWKB", 1, from_ewkb)
            conexao.create_function("ST_GeomFromEWKB", 1, from_ewkb)
            conexao.create_function("ST_AsGeoJSON", 1, as_geojson)
            conexao.create_function("AsGeoJSON", 1, as_geojson)
            conexao.create_function("AsEWKB", 1, as_ewkb)
            conexao.create_function("ST_AsEWKB", 1, as_ewkb)
            conexao.create_function("GeomFromEWKT", 1, lambda v: v)
            conexao.create_function("ST_GeomFromEWKT", 1, lambda v: v)
            conexao.create_function("ST_Y", 1, lambda v: coord(v, 1))
            conexao.create_function("ST_X", 1, lambda v: coord(v, 0))

        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as c:
            for t in (
                "configuracoes_campo_pesquisa", "cotas_perfil", "planos_cota_perfil", "tentativas_campo",
                "respostas", "coletas", "pergunta_territorio_eleitoral", "opcoes", "perguntas",
                "setor_territorio_eleitoral", "setor_agentes", "setores",
                "projeto_base_eleitoral", "territorio_eleitoral", "base_eleitoral",
                "pesquisas", "projetos", "usuarios", "perfis", "companies",
            ):
                c.execute(text(f"DELETE FROM {t}"))
            c.execute(text(f"INSERT INTO companies (id, name, is_active) VALUES ({COMPANY},'Empresa A',1),({COMPANY_B},'Empresa B',1)"))
            c.execute(text("INSERT INTO perfis (id, nome) VALUES (1,'Gerente'),(2,'Agente')"))
            c.execute(text(
                "INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id) VALUES "
                f"({GERENTE},'gerente@a','Gerente A','x',1,1,{COMPANY}), ({AG1},'a1@a','Agente Um','x',1,2,{COMPANY}), "
                f"({AG2},'a2@a','Agente Dois','x',1,2,{COMPANY}), ({GERENTE_B},'g@b','Gerente B','x',1,1,{COMPANY_B}), "
                f"({AG_B},'a@b','Agente B','x',1,2,{COMPANY_B})"
            ))
            c.execute(text(
                "INSERT INTO projetos (id, nome, status, coordenador_id, company_id) VALUES "
                f"({PROJETO},'Projeto A','Ativo',{GERENTE},{COMPANY}), ({PROJETO_LEGADO},'Projeto legado','Ativo',{GERENTE},{COMPANY}), "
                f"({PROJETO_B},'Projeto B','Ativo',{GERENTE_B},{COMPANY_B})"
            ))
            c.execute(text(
                "INSERT INTO pesquisas (id, titulo, ativo, projeto_id) VALUES "
                f"({PESQUISA},'Pesquisa A',1,{PROJETO}), ({PESQUISA_LEGADA},'Pesquisa legada',1,{PROJETO_LEGADO}), ({PESQUISA_B},'Pesquisa B',1,{PROJETO_B})"
            ))
        self.db = self.Session()
        self.addCleanup(self.db.close)

        self.app = FastAPI()
        self.app.include_router(rotas_coletas.router)
        self.app.include_router(rotas_cotas.router)
        self.app.include_router(rotas_tentativas.router)
        self.app.include_router(rotas_cobertura.router)
        self.app.include_router(rotas_painel.router)
        self.app.include_router(rotas_agente.router, prefix="/agente")
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.usuario_atual = usuario()
        self.app.dependency_overrides[get_current_user] = lambda: self.usuario_atual
        self.app.dependency_overrides[require_manager_or_superadmin] = lambda: self.usuario_atual
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        p = patch("pesquisa360.crud.geocoding.obter_endereco_por_coords", return_value="Rua X, 1 - Fulano")
        p.start()
        self.addCleanup(p.stop)

    # ------------------------------------------------------------ fixtures
    def sql(self, stmt, **params):
        self.db.execute(text(stmt), params)
        self.db.commit()

    def territorio(self):
        self.sql("INSERT INTO base_eleitoral (id, nome, ano, uf, fonte, versao, data_referencia, status, company_id, criado_por_id)"
                 " VALUES (1, 'Base', 2026, 'AP', 'TSE', '1', '2026-01-01', 'VALIDADA', :c, 1)", c=COMPANY)
        self.sql("INSERT INTO projeto_base_eleitoral (projeto_id, base_eleitoral_id, principal) VALUES (:p, 1, 1)", p=PROJETO)
        self.sql("INSERT INTO territorio_eleitoral (id, base_eleitoral_id, parent_id, tipo, nome, nome_normalizado, municipio_id, status_validacao)"
                 " VALUES (10000, 1, NULL, 'ESTADO', 'Amapa', 'amapa', NULL, 'VALIDADA')")
        for tid, nome in ((MACAPA, "Macapa"), (SANTANA, "Santana")):
            self.sql("INSERT INTO territorio_eleitoral (id, base_eleitoral_id, parent_id, tipo, nome, nome_normalizado, municipio_id, status_validacao)"
                     " VALUES (:id, 1, 10000, 'MUNICIPIO', :n, :norm, NULL, 'VALIDADA')", id=tid, n=nome, norm=nome.lower())
        for tid, nome, mun in ((101, "Centro", MACAPA), (102, "Norte", MACAPA), (103, "Sul", MACAPA), (201, "Provedor", SANTANA)):
            self.sql("INSERT INTO territorio_eleitoral (id, base_eleitoral_id, parent_id, tipo, nome, nome_normalizado, municipio_id, status_validacao)"
                     " VALUES (:id, 1, :mun, 'BAIRRO', :n, :norm, :mun, 'VALIDADA')", id=tid, n=nome, norm=nome.lower(), mun=mun)

    def setor(self, sid, nome, bairro=None, agentes=(AG1,), pesquisa_id=PESQUISA, meta=10):
        # Poligono real (o Mobile ignora setor sem geometria): quadrado ~1 km deslocado por setor.
        dx = (sid % 100) * 0.01
        poligono = f"SRID=4326;POLYGON(({-51.08 + dx} 0.02, {-51.06 + dx} 0.02, {-51.06 + dx} 0.05, {-51.08 + dx} 0.05, {-51.08 + dx} 0.02))"
        self.sql("INSERT INTO setores (id, nome, meta, tolerancia, finalidade, pesquisa_id, agente_id, geometria) VALUES (:id, :n, :m, 50, 'OPERACAO', :p, :a, :g)",
                 id=sid, n=nome, m=meta, p=pesquisa_id, a=agentes[0] if agentes else None, g=poligono)
        for a in agentes:
            self.sql("INSERT INTO setor_agentes (setor_id, agente_id, ativo) VALUES (:s, :a, 1)", s=sid, a=a)
        if bairro:
            self.sql("INSERT INTO setor_territorio_eleitoral (setor_id, territorio_eleitoral_id) VALUES (:s, :t)", s=sid, t=bairro)

    def pergunta(self, pid, texto, tipo, pesquisa_id=PESQUISA):
        self.sql("INSERT INTO perguntas (id, texto_pergunta, tipo_pergunta, ordem, eh_obrigatoria, ativo, pesquisa_id) VALUES (:id, :t, :tipo, :id, 1, 1, :p)",
                 id=pid, t=texto, tipo=tipo, p=pesquisa_id)

    def coleta_seed(self, setor_id, agente, sexo=None, idade=None, quando="2026-08-01 10:00:00", pesquisa_id=PESQUISA, company=COMPANY, gps=True):
        """Coleta legada (ja sincronizada) inserida direto no banco."""
        uuid = str(uuid4())
        self.sql("INSERT INTO coletas (pesquisa_id, agente_id, company_id, client_uuid, setor_id, foi_offline, status_sincronizacao, data_inicio_coleta, inconformidade_localizacao, localizacao_inicio)"
                 " VALUES (:p, :a, :c, :u, :s, 0, 'sincronizado', :q, 0, :g)",
                 p=pesquisa_id, a=agente, c=company, u=uuid, s=setor_id, q=quando, g=f"SRID=4326;POINT({GPS[1]} {GPS[0]})" if gps else None)
        cid = self.db.execute(text("SELECT id FROM coletas WHERE client_uuid = :u"), {"u": uuid}).scalar()
        if sexo is not None:
            self.sql("INSERT INTO respostas (valor_resposta, pergunta_id, coleta_id) VALUES (:v, 11, :c)", v=sexo, c=cid)
        if idade is not None:
            self.sql("INSERT INTO respostas (valor_resposta, pergunta_id, coleta_id) VALUES (:v, 12, :c)", v=str(idade), c=cid)
        return cid

    def como(self, user_id, company_id=COMPANY, perfil="Agente"):
        self.usuario_atual = usuario(user_id, company_id=company_id, perfil=perfil)

    def coleta_api(self, setor_id, agente=AG1, sexo=None, idade=None, gps=GPS, quando="2026-08-27T12:00:00Z", pesquisa_id=PESQUISA, company_id=COMPANY, uuid=None, esperado=201):
        anterior = self.usuario_atual
        self.como(agente, company_id=company_id)
        try:
            respostas = [{"pergunta_id": 13 if pesquisa_id == PESQUISA else 23, "valor_resposta": "Fulano da Silva CPF 123 fulano@mail.com (96) 99999-0000"}]
            if sexo is not None:
                respostas.append({"pergunta_id": 11, "valor_resposta": sexo})
            if idade is not None:
                respostas.append({"pergunta_id": 12, "valor_resposta": str(idade)})
            uuid = uuid or str(uuid4())
            body = {"client_uuid": uuid, "setor_id": setor_id, "data_inicio_coleta": quando, "respostas": respostas}
            if gps is not None:
                body["localizacao_inicio"] = {"lat": gps[0], "lon": gps[1]}
            r = self.client.post(f"/pesquisas/{pesquisa_id}/coletas/", json=body)
            self.assertEqual(r.status_code, esperado, r.text)
            return (r.json().get("id") if r.status_code in (200, 201) else None), uuid
        finally:
            self.usuario_atual = anterior

    def tentativa_api(self, setor_id, resultado, agente=AG1, coleta_uuid=None, gps=GPS, quando="2026-08-27T12:03:00Z", pesquisa_id=PESQUISA, company_id=COMPANY, uuid=None, esperado=201):
        anterior = self.usuario_atual
        self.como(agente, company_id=company_id)
        try:
            uuid = uuid or str(uuid4())
            r = self.client.post(f"/pesquisas/{pesquisa_id}/tentativas-campo/", json={
                "client_uuid": uuid, "setor_id": setor_id, "iniciada_em": quando, "encerrada_em": quando,
                "localizacao": {"lat": gps[0], "lng": gps[1], "accuracy": 8.0, "capturada_em": quando},
                "resultado": resultado, "motivo": None if resultado == "CONCLUIDA" else "OUTRO",
                "coleta_client_uuid": coleta_uuid,
            })
            self.assertEqual(r.status_code, esperado, r.text)
            return (r.json().get("id") if r.status_code in (200, 201) else None), uuid
        finally:
            self.usuario_atual = anterior

    def missao(self, agente=AG1, pesquisa_id=PESQUISA, company_id=COMPANY, esperado=200):
        anterior = self.usuario_atual
        self.como(agente, company_id=company_id)
        try:
            r = self.client.get(f"/agente/missao/{pesquisa_id}")
            self.assertEqual(r.status_code, esperado, r.text)
            return r.json() if esperado == 200 else r
        finally:
            self.usuario_atual = anterior

    def cobertura(self, agente=AG1, pesquisa_id=PESQUISA, company_id=COMPANY, esperado=200):
        anterior = self.usuario_atual
        self.como(agente, company_id=company_id)
        try:
            r = self.client.get(f"/agente/pesquisas/{pesquisa_id}/cobertura-campo/")
            self.assertEqual(r.status_code, esperado, r.text)
            return r
        finally:
            self.usuario_atual = anterior

    def painel(self, query="", projeto_id=PROJETO, pesquisa_id=PESQUISA, esperado=200):
        self.como(GERENTE, perfil="Gerente")
        r = self.client.get(f"/projetos/{projeto_id}/pesquisas/{pesquisa_id}/controle-campo" + (f"?{query}" if query else ""))
        self.assertEqual(r.status_code, esperado, r.text)
        return r.json() if esperado == 200 else r

    def setor_do(self, payload, sid, chave="setores", id_chave="setor_id"):
        return next(s for s in payload[chave] if s[id_chave] == sid)

    def contar(self, stmt, **params):
        return int(self.db.execute(text(stmt), params).scalar() or 0)

    def cenario(self):
        """§8: 1 tenant, 1 projeto, 1 pesquisa, Macapa, setores A 5/10, B 9/10 (2 agentes), C 10/10; plano de perfil."""
        self.territorio()
        self.setor(A, "Setor A", 101, agentes=(AG1,))
        self.setor(B, "Setor B", 102, agentes=(AG1, AG2))
        self.setor(C, "Setor C", 103, agentes=(AG1,))
        self.pergunta(11, "Sexo", "ESCOLHA_SIMPLES")
        self.pergunta(12, "Idade", "NUMERO")
        self.pergunta(13, "Voto", "ESCOLHA_SIMPLES")
        # Perfil Macapa: F16-24 10/10 (completa), M16-24 8/10, M45-59 4/10 (-20 -> MEDIO), M60+ 2/10 (-40 -> ALTO). Total 24/40 = 60%.
        perfis = [("Feminino", 20)] * 10 + [("Masculino", 20)] * 8 + [("Masculino", 50)] * 4 + [("Masculino", 65)] * 2
        distribuicao = [A] * 5 + [B] * 9 + [C] * 10
        for (sexo, idade), setor in zip(perfis, distribuicao):
            self.coleta_seed(setor, AG1, sexo=sexo, idade=idade)
        self.como(GERENTE, perfil="Gerente")
        faixas = {"16-24": (16, 24), "45-59": (45, 59), "60+": (60, None)}
        cotas = [
            {"sexo": "FEMININO", "faixa_etaria": "16-24", "idade_min": 16, "idade_max": 24, "meta": 10},
            {"sexo": "MASCULINO", "faixa_etaria": "16-24", "idade_min": 16, "idade_max": 24, "meta": 10},
            {"sexo": "MASCULINO", "faixa_etaria": "45-59", "idade_min": 45, "idade_max": 59, "meta": 10},
            {"sexo": "MASCULINO", "faixa_etaria": "60+", "idade_min": 60, "idade_max": None, "meta": 10},
        ]
        del faixas
        r = self.client.put(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil", json={
            "pergunta_sexo_id": 11, "pergunta_idade_id": 12, "modo_idade": "NUMERICA",
            "sexo_valores": {"MASCULINO": ["Masculino"], "FEMININO": ["Feminino"]},
            "territorios": [{"territorio_id": MACAPA, "cotas": cotas}],
        })
        self.assertEqual(r.status_code, 200, r.text)
        r = self.client.put(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/configuracao-campo", json={"distancia_recomendada_entre_abordagens_metros": 120})
        self.assertEqual(r.status_code, 200, r.text)

    # ------------------------------------------------------------ §9-§12 missao / cota territorial
    def test_QA_01_missao_do_agente_reflete_cota_territorial_oficial(self):
        self.cenario()
        m = self.missao(AG1)
        self.assertEqual([s["id"] for s in m["setores"]], [A, B, C])
        esperado = {A: (5, 5, "ABERTO"), B: (9, 1, "ATENCAO"), C: (10, 0, "ENCERRADO")}
        for sid, (realizado, restante, status) in esperado.items():
            s = self.setor_do(m, sid, id_chave="id")
            self.assertEqual((s["meta"], s["realizado"], s["restante"], s["excedente"], s["status_cota"]), (10, realizado, restante, 0, status), sid)
            self.assertEqual(s["limite_atencao_realizado"], 9)
            self.assertEqual(s["municipio"], {"id": MACAPA, "nome": "Macapa"})
            self.assertIsNotNone(s["snapshot_em"])
            self.assertEqual(s["snapshot_ate_coleta_id"], self.contar("SELECT MAX(id) FROM coletas WHERE setor_id = :s", s=sid))
        self.assertEqual(self.setor_do(m, B, id_chave="id")["agentes_atribuidos_total"], 2)
        self.assertEqual(self.setor_do(m, A, id_chave="id")["agentes_atribuidos_total"], 1)
        # Perfil na missao: so prioridade, ALTO antes de MEDIO.
        self.assertTrue(m["plano_cota_perfil_ativo"])
        self.assertIsNotNone(m["prioridades_perfil_snapshot_em"])
        pri = [(p["sexo"], p["faixa_etaria"], p["prioridade"]) for p in m["prioridades_perfil"]]
        self.assertEqual(pri, [("MASCULINO", "60+", "ALTO"), ("MASCULINO", "45-59", "MEDIO")])
        self.assertEqual(m["perfil_status_territorios"][str(MACAPA)], "PRIORIDADES")
        # Agente 2: so o setor B (nada vaza).
        m2 = self.missao(AG2)
        self.assertEqual([s["id"] for s in m2["setores"]], [B])
        self.assertEqual(m2["setores"][0]["status_cota"], "ATENCAO")
        FIXTURES_QA.mkdir(exist_ok=True)
        (FIXTURES_QA / "missao_agente.json").write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")

    def test_QA_02_setor_encerrado_no_servidor_preserva_entrevista_com_excedente(self):
        """§12/§25: o bloqueio de ENCERRADO e do Mobile antes da abordagem; o servidor
        nunca rejeita uma entrevista legitima por cota."""
        self.cenario()
        self.coleta_api(C, AG1, sexo="Masculino", idade=70)
        s = self.setor_do(self.missao(AG1), C, id_chave="id")
        self.assertEqual((s["realizado"], s["excedente"], s["restante"], s["status_cota"]), (11, 1, 0, "ENCERRADO"))

    # ------------------------------------------------------------ §14-§18 abordagens, entrevistas, GPS unico
    def test_QA_03_tentativas_nao_consomem_cota_e_concluida_vincula_mesmo_gps(self):
        self.cenario()
        ids = {}
        for r in ("RECUSA", "NAO_ELEGIVEL", "DESISTENCIA", "INCOMPLETA"):
            ids[r], _ = self.tentativa_api(A, r, gps=(0.0351, -51.0701))
        m = self.setor_do(self.missao(AG1), A, id_chave="id")
        self.assertEqual((m["realizado"], m["status_cota"]), (5, "ABERTO"))       # §15/§16: cota territorial intacta
        perfil_antes = self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil/progresso").json()
        # §17/§18: entrevista concluida com o MESMO ponto da abordagem.
        ponto = (0.0360, -51.0710)
        cid, uuid = self.coleta_api(A, AG1, sexo="Masculino", idade=65, gps=ponto)
        tid, _ = self.tentativa_api(A, "CONCLUIDA", coleta_uuid=uuid, gps=ponto)
        row = self.db.execute(text(
            "SELECT t.coleta_id, t.latitude, t.longitude, ST_Y(c.localizacao_inicio), ST_X(c.localizacao_inicio)"
            " FROM tentativas_campo t JOIN coletas c ON c.id = t.coleta_id WHERE t.id = :t"), {"t": tid}).fetchone()
        self.assertEqual(row[0], cid)
        self.assertAlmostEqual(row[1], row[3]); self.assertAlmostEqual(row[2], row[4])
        m = self.setor_do(self.missao(AG1), A, id_chave="id")
        self.assertEqual((m["realizado"], m["status_cota"]), (6, "ABERTO"))
        perfil_depois = self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil/progresso").json()
        cel = lambda p: next(c for c in p["territorios"][0]["celulas"] if c["sexo"] == "MASCULINO" and c["faixa_etaria"] == "60+")
        self.assertEqual(cel(perfil_antes)["realizado"], 2)       # tentativas nao mexeram no perfil
        self.assertEqual(cel(perfil_depois)["realizado"], 3)      # a coleta sim
        # §14 perfil nao bloqueante: celula F16-24 ja completa (10/10) continua aceitando entrevista.
        self.coleta_api(A, AG1, sexo="Feminino", idade=18, esperado=201)
        # CONCLUIDA antes da coleta sincronizada -> 422 (Mobile mantem pendente e reenvia).
        self.tentativa_api(A, "CONCLUIDA", coleta_uuid=str(uuid4()), esperado=422)

    # ------------------------------------------------------------ §19-§22, §42 idempotencia
    def test_QA_04_idempotencia_de_coleta_e_tentativa_nao_duplica_nada(self):
        self.cenario()
        cid, cu = self.coleta_api(A, AG1, sexo="Masculino", idade=65)
        tid, tu = self.tentativa_api(A, "CONCLUIDA", coleta_uuid=cu)
        rid, ru = self.tentativa_api(A, "RECUSA", gps=(0.0352, -51.0702))
        antes = self.painel()
        for _ in range(3):
            cid2, _ = self.coleta_api(A, AG1, sexo="Masculino", idade=65, uuid=cu)
            tid2, _ = self.tentativa_api(A, "CONCLUIDA", coleta_uuid=cu, uuid=tu)
            rid2, _ = self.tentativa_api(A, "RECUSA", uuid=ru, gps=(0.0352, -51.0702))
            self.assertEqual((cid2, tid2, rid2), (cid, tid, rid))
        self.assertEqual(self.contar("SELECT COUNT(*) FROM coletas WHERE client_uuid = :u", u=cu), 1)
        self.assertEqual(self.contar("SELECT COUNT(*) FROM tentativas_campo WHERE client_uuid IN (:a, :b)", a=tu, b=ru), 2)
        depois = self.painel()
        for chave in ("resumo", "setores", "tentativas_por_resultado", "resumo_territorial"):
            self.assertEqual(antes[chave], depois[chave], chave)
        self.assertEqual(len(antes["atividade_campo"]["eventos"]), len(depois["atividade_campo"]["eventos"]))
        self.assertEqual(antes["cotas_perfil"]["territorios"], depois["cotas_perfil"]["territorios"])
        self.assertEqual(self.setor_do(depois, A)["realizado"], 6)

    # ------------------------------------------------------------ §25 concorrencia offline
    def test_QA_05_concorrencia_9_10_dois_agentes_gera_11_10_preservando_ambas(self):
        self.cenario()
        m1 = self.setor_do(self.missao(AG1), B, id_chave="id")
        m2 = self.setor_do(self.missao(AG2), B, id_chave="id")
        self.assertEqual((m1["realizado"], m2["realizado"], m1["status_cota"]), (9, 9, "ATENCAO"))
        # Ambos offline com snapshot 9/10; cada um faz 1 entrevista e sincroniza depois.
        self.coleta_api(B, AG1, sexo="Masculino", idade=66, esperado=201)
        self.coleta_api(B, AG2, sexo="Masculino", idade=50, esperado=201)
        for ag in (AG1, AG2):
            s = self.setor_do(self.missao(ag), B, id_chave="id")
            self.assertEqual((s["realizado"], s["excedente"], s["restante"], s["status_cota"]), (11, 1, 0, "ENCERRADO"))
        self.assertEqual(self.contar("SELECT COUNT(*) FROM coletas WHERE setor_id = :s", s=B), 11)
        painel = self.painel()
        self.assertEqual(self.setor_do(painel, B)["excedente"], 1)
        self.assertEqual(painel["resumo_territorial"]["com_excedente"], 1)
        self.assertEqual([a["tipo"] for a in painel["alertas"] if a["tipo"] == "SETORES_EXCEDENTE"], ["SETORES_EXCEDENTE"])

    # ------------------------------------------------------------ §30-§34 painel: numeros, territorio, perfil, mapa
    def test_QA_06_painel_web_coincide_com_banco_missao_e_calculo_manual(self):
        self.cenario()
        _, cu = self.coleta_api(A, AG1, sexo="Masculino", idade=65)          # entrevista com abordagem
        self.tentativa_api(A, "CONCLUIDA", coleta_uuid=cu)
        self.tentativa_api(A, "RECUSA", gps=(0.0352, -51.0702))
        self.tentativa_api(A, "NAO_ELEGIVEL", agente=AG1, gps=(0.0353, -51.0703))
        self.tentativa_api(B, "DESISTENCIA", agente=AG2, gps=(0.0354, -51.0704))
        self.tentativa_api(B, "INCOMPLETA", agente=AG2, gps=(0.0355, -51.0705))
        self.coleta_api(B, AG2, sexo="Feminino", idade=None)                  # §33 nao classificavel (sem idade)
        self.sql("INSERT INTO tentativas_campo (client_uuid, pesquisa_id, setor_id, agente_id, company_id, iniciada_em, latitude, longitude, resultado)"
                 " VALUES ('em-and', :p, :s, :a, :c, '2026-08-27 12:00:00', 0.03, -51.06, 'EM_ANDAMENTO')", p=PESQUISA, s=A, a=AG1, c=COMPANY)
        painel = self.painel()
        r = painel["resumo"]

        # §30 numeros = banco
        total_coletas = self.contar("SELECT COUNT(*) FROM coletas WHERE pesquisa_id = :p", p=PESQUISA)
        por_res = dict(self.db.execute(text("SELECT resultado, COUNT(*) FROM tentativas_campo WHERE pesquisa_id = :p GROUP BY resultado"), {"p": PESQUISA}).fetchall())
        encerradas = sum(v for k, v in por_res.items() if k != "EM_ANDAMENTO")
        vinculadas = self.contar("SELECT COUNT(*) FROM tentativas_campo WHERE pesquisa_id = :p AND coleta_id IS NOT NULL", p=PESQUISA)
        self.assertEqual(total_coletas, 26)
        self.assertEqual(r["entrevistas_concluidas"], total_coletas)
        self.assertEqual(r["coletas_com_tentativa"], vinculadas)
        self.assertEqual(r["coletas_sem_tentativa"], total_coletas - vinculadas)
        self.assertEqual((r["recusas"], r["nao_elegiveis"], r["desistencias"], r["incompletas"], r["tentativas_concluidas"]), (1, 1, 1, 1, 1))
        self.assertEqual(r["tentativas_encerradas"], encerradas)
        self.assertEqual(r["tentativas_em_andamento"], por_res["EM_ANDAMENTO"])
        self.assertEqual(r["taxa_conclusao_tentativas"], round(100.0 * por_res["CONCLUIDA"] / encerradas, 2))
        self.assertEqual(r["taxa_conclusao_tentativas"], 20.0)
        self.assertEqual({t["resultado"]: t["total"] for t in painel["tentativas_por_resultado"]}, {
            "CONCLUIDA": 1, "RECUSA": 1, "NAO_ELEGIVEL": 1, "DESISTENCIA": 1, "INCOMPLETA": 1, "PROBLEMA_TECNICO": 0, "OUTRO": 0})

        # §31 territorio: painel == missao == banco
        m = self.missao(AG1)
        for sid in (A, B, C):
            p = self.setor_do(painel, sid)
            s = self.setor_do(m, sid, id_chave="id")
            banco = self.contar("SELECT COUNT(*) FROM coletas WHERE setor_id = :s", s=sid)
            self.assertEqual(p["realizado"], banco)
            for chave in ("meta", "realizado", "restante", "excedente", "percentual_atingimento", "status_cota", "snapshot_ate_coleta_id", "agentes_atribuidos_total"):
                self.assertEqual(p[chave], s[chave], (sid, chave))
        self.assertEqual({s["setor_id"]: s["status_cota"] for s in painel["setores"]}, {A: "ABERTO", B: "ENCERRADO", C: "ENCERRADO"})
        self.assertEqual(r["meta_territorial"], 30)
        self.assertEqual(r["realizado_territorial"], 26)

        # §32 perfil: calculo manual. Territorio: 25 classificadas de 40 (F16-24 10, M16-24 8, M45-59 4, M60+ 3).
        cp = painel["cotas_perfil"]
        self.assertTrue(cp["plano_ativo"])
        t = cp["territorios"][0]
        self.assertEqual((t["territorio_id"], t["meta_total"], t["realizado_total"]), (MACAPA, 40, 25))
        pct_territorio = round(25 * 100.0 / 40, 2)
        self.assertEqual(t["percentual_territorio"], pct_territorio)
        self.assertEqual(t["status"], "PRIORIDADES")
        cel = {(c["sexo"], c["faixa_etaria"]): c for c in t["celulas"]}
        m60 = cel[("MASCULINO", "60+")]
        self.assertEqual((m60["meta"], m60["realizado"], m60["restante"]), (10, 3, 7))
        self.assertEqual(m60["percentual_atingimento"], 30.0)
        self.assertEqual(m60["desvio_pp"], round(30.0 - pct_territorio, 2))
        self.assertEqual(m60["prioridade"], svc_perfil.classificar_prioridade(30.0 - pct_territorio))
        self.assertEqual(m60["prioridade"], "ALTO")
        # M45-59: 4/10 = 40%; territorio 62.5% -> -22.5 pp -> ALTO (era MEDIO a 60%, ver QA-01).
        m45 = cel[("MASCULINO", "45-59")]
        self.assertEqual((m45["percentual_atingimento"], m45["desvio_pp"]), (40.0, round(40.0 - pct_territorio, 2)))
        self.assertEqual(m45["desvio_pp"], -22.5)
        self.assertEqual(m45["prioridade"], svc_perfil.classificar_prioridade(-22.5))
        self.assertEqual(m45["prioridade"], "ALTO")
        self.assertEqual(cel[("FEMININO", "16-24")]["prioridade"], "EQUILIBRADO")
        ordem = {"ALTO": 0, "MEDIO": 1, "BAIXO": 2, "EQUILIBRADO": 3}
        pri = [c["prioridade"] for c in t["celulas"]]
        self.assertEqual(pri, sorted(pri, key=ordem.get))
        self.assertEqual(pri[:2], ["ALTO", "ALTO"])
        # §33 nao classificadas: 1 (SEM_IDADE); segue contada em entrevistas.
        self.assertEqual(cp["nao_classificadas"], 1)
        self.assertEqual(cp["motivos_nao_classificadas"], {svc_perfil.NAO_CLASSIFICADA_SEM_IDADE: 1})
        self.assertEqual(self.contar("SELECT COUNT(*) FROM coletas WHERE inconformidade_localizacao = 1"), 0)
        self.assertIn("PERFIL_NAO_CLASSIFICADAS", {a["tipo"] for a in painel["alertas"]})

        # §34 mapa: Coleta + CONCLUIDA vinculada = 1 evento; EM_ANDAMENTO nao aparece.
        eventos = painel["atividade_campo"]["eventos"]
        com_gps = self.contar("SELECT COUNT(*) FROM coletas WHERE pesquisa_id = :p AND localizacao_inicio IS NOT NULL", p=PESQUISA)
        self.assertEqual(len(eventos), com_gps + (encerradas - vinculadas))
        self.assertEqual(sum(1 for e in eventos if e["tipo"] == "TENTATIVA" and e["resultado"] == "CONCLUIDA"), 0)
        self.assertEqual(sum(1 for e in eventos if e["tipo"] == "TENTATIVA"), 4)
        self.assertTrue(all(e["resultado"] != "EM_ANDAMENTO" for e in eventos))
        # Mesmo evento nos 3 mapas: cobertura do agente (recorte) x painel.
        cob = self.cobertura(AG2).json()
        self.assertEqual(cob["setor_ids"], [B])
        chaves_cob = {(e["tipo"], e["server_id"]) for e in cob["eventos"]}
        chaves_painel_b = {(e["tipo"], e["server_id"]) for e in eventos if e["setor_id"] == B}
        self.assertEqual(chaves_cob, chaves_painel_b)
        self.assertEqual(painel["atividade_campo"]["distancia_recomendada_entre_abordagens_metros"], 120)
        self.assertTrue(painel["atividade_campo"]["distancia_configurada"])
        self.assertEqual(cob["distancia_recomendada_entre_abordagens_metros"], 120)

        FIXTURES_QA.mkdir(exist_ok=True)
        (FIXTURES_QA / "controle_campo.json").write_text(json.dumps(painel, indent=2, ensure_ascii=False), encoding="utf-8")
        (FIXTURES_QA / "cobertura_agente.json").write_text(json.dumps(cob, indent=2, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------ §35-§38 filtros
    def test_QA_07_filtros_do_painel_nao_tocam_cota_nem_perfil(self):
        self.cenario()
        self.setor(D_SANTANA, "Setor D (Santana)", 201, agentes=(AG1,))
        self.coleta_api(A, AG1, sexo="Masculino", idade=65, quando="2026-08-27T12:00:00Z")
        self.coleta_api(B, AG2, sexo="Masculino", idade=50, quando="2026-08-27T13:00:00Z")
        self.coleta_api(D_SANTANA, AG1, sexo="Masculino", idade=30, quando="2026-08-27T14:00:00Z")
        self.tentativa_api(A, "RECUSA", agente=AG1, quando="2026-08-27T12:10:00Z", gps=(0.0352, -51.0702))
        self.tentativa_api(B, "RECUSA", agente=AG2, quando="2026-08-27T13:10:00Z", gps=(0.0353, -51.0703))
        self.tentativa_api(D_SANTANA, "DESISTENCIA", agente=AG1, quando="2026-08-27T14:10:00Z", gps=(0.0354, -51.0704))
        tudo = self.painel()
        self.assertEqual(tudo["resumo"]["entrevistas_concluidas"], 27)

        # §35 agente
        so2 = self.painel(f"agente_ids={AG2}")
        self.assertEqual(so2["resumo"]["entrevistas_concluidas"], 1)
        self.assertEqual(so2["resumo"]["recusas"], 1)
        self.assertEqual({e["agente_id"] for e in so2["atividade_campo"]["eventos"]}, {AG2})
        self.assertEqual(so2["setores"], tudo["setores"])
        self.assertEqual(so2["resumo"]["meta_territorial"], tudo["resumo"]["meta_territorial"])
        self.assertEqual(so2["resumo"]["realizado_territorial"], tudo["resumo"]["realizado_territorial"])
        self.assertEqual(sem_snapshot(so2["cotas_perfil"]), sem_snapshot(tudo["cotas_perfil"]))

        # §36 periodo (exclui as 24 coletas de 01/08 e o que aconteceu antes das 13h de 27/08)
        per = self.painel("data_inicio=2026-08-27T12:30:00Z&data_fim=2026-08-27T23:59:59Z")
        self.assertEqual(per["resumo"]["entrevistas_concluidas"], 2)
        self.assertEqual((per["resumo"]["recusas"], per["resumo"]["desistencias"]), (1, 1))
        self.assertEqual(len(per["atividade_campo"]["eventos"]), 4)
        self.assertEqual(per["setores"], tudo["setores"])
        self.assertEqual(sem_snapshot(per["cotas_perfil"]), sem_snapshot(tudo["cotas_perfil"]))

        # §37 municipio
        mac = self.painel(f"municipio_id={MACAPA}")
        self.assertEqual({s["setor_id"] for s in mac["setores"]}, {A, B, C})
        self.assertEqual({e["setor_id"] for e in mac["atividade_campo"]["eventos"]} & {D_SANTANA}, set())
        self.assertEqual([t["territorio_id"] for t in mac["cotas_perfil"]["territorios"]], [MACAPA])
        self.assertEqual(mac["resumo"]["entrevistas_concluidas"], 26)
        san = self.painel(f"municipio_id={SANTANA}")
        self.assertEqual([s["setor_id"] for s in san["setores"]], [D_SANTANA])
        self.assertEqual(san["cotas_perfil"]["territorios"], [])
        self.assertEqual(san["resumo"]["entrevistas_concluidas"], 1)
        self.assertEqual({e["setor_id"] for e in san["atividade_campo"]["eventos"]}, {D_SANTANA})

        # §38 setor
        so_a = self.painel(f"setor_ids={A}")
        self.assertEqual([s["setor_id"] for s in so_a["setores"]], [A])
        self.assertEqual({e["setor_id"] for e in so_a["atividade_campo"]["eventos"]}, {A})
        self.assertEqual(so_a["resumo"]["entrevistas_concluidas"], 6)
        self.assertEqual(so_a["resumo"]["recusas"], 1)

    # ------------------------------------------------------------ §39 pesquisa legada
    def test_QA_08_pesquisa_legada_sem_tentativa_plano_ou_configuracao(self):
        self.setor(LEGADO, "Setor legado", agentes=(AG1,), pesquisa_id=PESQUISA_LEGADA)
        self.pergunta(23, "Voto", "ESCOLHA_SIMPLES", pesquisa_id=PESQUISA_LEGADA)
        self.coleta_seed(LEGADO, AG1, pesquisa_id=PESQUISA_LEGADA)
        self.coleta_seed(LEGADO, AG1, pesquisa_id=PESQUISA_LEGADA, gps=False)
        self.coleta_api(LEGADO, AG1, pesquisa_id=PESQUISA_LEGADA)
        p = self.painel(projeto_id=PROJETO_LEGADO, pesquisa_id=PESQUISA_LEGADA)
        self.assertEqual(p["resumo"]["entrevistas_concluidas"], 3)
        self.assertEqual(p["resumo"]["coletas_sem_tentativa"], 3)
        self.assertIsNone(p["resumo"]["taxa_conclusao_tentativas"])
        self.assertEqual(p["cotas_perfil"]["plano_ativo"], False)
        self.assertEqual(p["cotas_perfil"]["territorios"], [])
        self.assertEqual([e["tipo"] for e in p["atividade_campo"]["eventos"]], ["COLETA", "COLETA"])
        self.assertEqual(p["atividade_campo"]["distancia_recomendada_entre_abordagens_metros"], 100)
        self.assertFalse(p["atividade_campo"]["distancia_configurada"])
        self.assertIsNone(self.setor_do(p, LEGADO)["municipio_id"])
        self.assertEqual(self.setor_do(p, LEGADO)["status_cota"], "ABERTO")
        m = self.missao(AG1, pesquisa_id=PESQUISA_LEGADA)
        self.assertFalse(m["plano_cota_perfil_ativo"])
        self.assertEqual(m["prioridades_perfil"], [])
        self.assertEqual(self.cobertura(AG1, pesquisa_id=PESQUISA_LEGADA).json()["distancia_recomendada_entre_abordagens_metros"], 100)

    # ------------------------------------------------------------ §40-§41 multitenancy / atribuicao
    def test_QA_09_multitenancy_e_agente_sem_atribuicao(self):
        self.cenario()
        self.setor(SETOR_B, "Setor tenant B", agentes=(AG_B,), pesquisa_id=PESQUISA_B)
        self.pergunta(23, "Voto B", "ESCOLHA_SIMPLES", pesquisa_id=PESQUISA_B)
        self.coleta_api(SETOR_B, AG_B, pesquisa_id=PESQUISA_B, company_id=COMPANY_B)
        self.tentativa_api(SETOR_B, "RECUSA", agente=AG_B, pesquisa_id=PESQUISA_B, company_id=COMPANY_B)
        # Tenant A tentando B: 404 em tudo.
        self.missao(AG1, pesquisa_id=PESQUISA_B, esperado=404)
        self.tentativa_api(SETOR_B, "RECUSA", agente=AG1, pesquisa_id=PESQUISA_B, esperado=404)
        self.coleta_api(SETOR_B, AG1, pesquisa_id=PESQUISA_B, esperado=404)
        self.cobertura(AG1, pesquisa_id=PESQUISA_B, esperado=404)
        self.como(GERENTE, perfil="Gerente")
        self.assertEqual(self.client.get(f"/projetos/{PROJETO_B}/pesquisas/{PESQUISA_B}/cotas-perfil").status_code, 404)
        self.assertEqual(self.client.get(f"/projetos/{PROJETO_B}/pesquisas/{PESQUISA_B}/cotas-perfil/progresso").status_code, 404)
        self.painel(projeto_id=PROJETO_B, pesquisa_id=PESQUISA_B, esperado=404)
        # Tenant B tentando A: 404 em tudo.
        self.missao(AG_B, pesquisa_id=PESQUISA, company_id=COMPANY_B, esperado=404)
        self.tentativa_api(A, "RECUSA", agente=AG_B, company_id=COMPANY_B, esperado=404)
        self.coleta_api(A, AG_B, company_id=COMPANY_B, esperado=404)
        self.cobertura(AG_B, company_id=COMPANY_B, esperado=404)
        self.como(GERENTE_B, company_id=COMPANY_B, perfil="Gerente")
        self.assertEqual(self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/controle-campo").status_code, 404)
        self.assertEqual(self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil").status_code, 404)
        # Nada de B no painel de A (e vice-versa).
        pa = self.painel()
        self.assertNotIn(SETOR_B, {s["setor_id"] for s in pa["setores"]})
        self.assertNotIn(SETOR_B, {e["setor_id"] for e in pa["atividade_campo"]["eventos"]})
        self.assertNotIn(AG_B, {a["id"] for a in pa["opcoes"]["agentes"]})
        self.como(GERENTE_B, company_id=COMPANY_B, perfil="Gerente")
        pb = self.client.get(f"/projetos/{PROJETO_B}/pesquisas/{PESQUISA_B}/controle-campo").json()
        self.assertEqual([s["setor_id"] for s in pb["setores"]], [SETOR_B])
        self.assertEqual({e["setor_id"] for e in pb["atividade_campo"]["eventos"]}, {SETOR_B})
        self.assertEqual(pb["resumo"]["entrevistas_concluidas"], 1)
        # §41 agente sem atribuicao (AG2 nao tem o setor A).
        self.assertNotIn(A, [s["id"] for s in self.missao(AG2)["setores"]])
        self.assertEqual(self.cobertura(AG2).json()["setor_ids"], [B])
        self.coleta_api(A, AG2, esperado=403)
        self.tentativa_api(A, "RECUSA", agente=AG2, esperado=403)
        self.assertEqual(self.contar("SELECT COUNT(*) FROM coletas WHERE setor_id = :s AND agente_id = :a", s=A, a=AG2), 0)

    # ------------------------------------------------------------ §43 dados pessoais
    def test_QA_10_dados_pessoais_nunca_saem_pelos_endpoints_novos(self):
        self.cenario()
        _, cu = self.coleta_api(A, AG1, sexo="Masculino", idade=65)
        self.tentativa_api(A, "CONCLUIDA", coleta_uuid=cu)
        self.tentativa_api(A, "RECUSA", gps=(0.0352, -51.0702))
        proibidos = ("Fulano", "CPF", "fulano@mail.com", "99999-0000", "valor_resposta", "\"respostas\"", "endereco", "Rua X", "client_uuid", "senha", "@a\"", "@b\"")
        cob = self.cobertura(AG1)
        for p in proibidos + ("agente_id", "agente_nome", "\"nome\"", "email"):
            self.assertNotIn(p, cob.text, f"cobertura agente expos {p}")
        self.assertEqual(set().union(*(e.keys() for e in cob.json()["eventos"])),
                         {"tipo", "server_id", "setor_id", "lat", "lng", "accuracy", "ocorrido_em", "resultado"})
        self.como(GERENTE, perfil="Gerente")
        painel = self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/controle-campo")
        for p in proibidos + ("email",):
            self.assertNotIn(p, painel.text, f"painel expos {p}")
        chaves_evento = set().union(*(e.keys() for e in painel.json()["atividade_campo"]["eventos"]))
        self.assertEqual(chaves_evento, {"tipo", "server_id", "setor_id", "lat", "lng", "accuracy", "ocorrido_em", "resultado", "agente_id", "agente_nome"})
        self.assertEqual(set(painel.json()["opcoes"]["agentes"][0].keys()), {"id", "nome"})
        missao = self.client.get(f"/agente/missao/{PESQUISA}")
        self.como(AG1)
        missao = self.client.get(f"/agente/missao/{PESQUISA}")
        for p in proibidos:
            self.assertNotIn(p, missao.text, f"missao expos {p}")
        tent = self.client.get(f"/pesquisas/{PESQUISA}/tentativas-campo/") if False else None
        del tent

    # ------------------------------------------------------------ §44 sem falso tempo real
    def test_QA_11_contratos_novos_nao_prometem_tempo_real(self):
        self.cenario()
        textos = [self.painel(), self.missao(AG1), self.cobertura(AG1).json()]
        corpo = json.dumps(textos, ensure_ascii=False).lower()
        for termo in ("ao vivo", "tempo real", "online agora", "agente ativo agora", "coletando agora", "heartbeat"):
            self.assertNotIn(termo, corpo, termo)
        self.assertIn("snapshot_em", corpo)

    # ------------------------------------------------------------ §45 performance
    def test_QA_12_controle_campo_sem_n_mais_1_com_10_setores_100_coletas_100_tentativas(self):
        self.territorio()
        self.pergunta(11, "Sexo", "ESCOLHA_SIMPLES"); self.pergunta(12, "Idade", "NUMERO"); self.pergunta(13, "Voto", "ESCOLHA_SIMPLES")

        def contar_queries():
            n = [0]

            def _c(*_):
                n[0] += 1

            event.listen(self.engine, "before_cursor_execute", _c)
            try:
                t0 = time.perf_counter()
                self.painel()
                dt = time.perf_counter() - t0
            finally:
                event.remove(self.engine, "before_cursor_execute", _c)
            return n[0], dt

        self.setor(A, "S1", 101, agentes=(AG1, AG2))
        self.setor(B, "S2", 102, agentes=(AG1,))
        self.coleta_seed(A, AG1, sexo="Masculino", idade=65)
        self.sql("INSERT INTO tentativas_campo (client_uuid, pesquisa_id, setor_id, agente_id, company_id, iniciada_em, latitude, longitude, resultado)"
                 " VALUES ('q0', :p, :s, :a, :c, '2026-08-27 12:00:00', 0.03, -51.06, 'RECUSA')", p=PESQUISA, s=A, a=AG1, c=COMPANY)
        pequeno, t_pequeno = contar_queries()

        for i in range(8):
            self.setor(600 + i, f"S{i + 3}", 101 + (i % 3), agentes=(AG1, AG2))
        setores = [A, B] + [600 + i for i in range(8)]
        for i in range(99):
            self.coleta_seed(setores[i % 10], AG1 if i % 2 else AG2, sexo="Masculino" if i % 3 else "Feminino", idade=20 + (i % 50))
        for i in range(99):
            self.sql("INSERT INTO tentativas_campo (client_uuid, pesquisa_id, setor_id, agente_id, company_id, iniciada_em, latitude, longitude, resultado)"
                     " VALUES (:u, :p, :s, :a, :c, '2026-08-27 12:00:00', 0.03, -51.06, :r)",
                     u=f"q{i + 1}", p=PESQUISA, s=setores[i % 10], a=AG1 if i % 2 else AG2, c=COMPANY,
                     r=["RECUSA", "NAO_ELEGIVEL", "DESISTENCIA", "INCOMPLETA"][i % 4])
        self.assertEqual(self.contar("SELECT COUNT(*) FROM coletas"), 100)
        self.assertEqual(self.contar("SELECT COUNT(*) FROM tentativas_campo"), 100)
        grande, t_grande = contar_queries()
        self.assertEqual(pequeno, grande, f"N+1: {pequeno} -> {grande}")
        self.assertLessEqual(grande, 25)
        print(f"\n[QA-12] controle-campo queries: pequeno={pequeno} grande={grande}; tempo pequeno={t_pequeno * 1000:.0f}ms grande={t_grande * 1000:.0f}ms")
        painel = self.painel()
        self.assertEqual(painel["resumo"]["entrevistas_concluidas"], 100)
        self.assertEqual(painel["resumo"]["tentativas_encerradas"], 100)
        self.assertEqual(len(painel["setores"]), 10)
        self.assertEqual(len(painel["atividade_campo"]["eventos"]), 200)


if __name__ == "__main__":
    unittest.main()
