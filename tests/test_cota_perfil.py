"""PROMPT 05 -- Cotas de Perfil amostral (CP-B01..CP-B20).

Schema real via Alembic (inclui a8b9c0d1e2f3). Cota de perfil e ORIENTATIVA:
os testes provam classificacao, motor de prioridade, recorte por territorio,
multitenancy, transacionalidade e que nada bloqueia coleta.
"""
from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-cota-perfil-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import schemas
from pesquisa360.api.endpoints import agente as rotas_agente
from pesquisa360.api.endpoints import coletas as rotas_coletas
from pesquisa360.api.endpoints import cotas_perfil as rotas_cotas
from pesquisa360.api.endpoints import projetos as rotas_projetos
from pesquisa360.core.dependencies import get_current_user, get_db, require_manager_or_superadmin
from pesquisa360.db import models
from pesquisa360.services import cota_perfil as svc
from tests.test_base_eleitoral_import import run_alembic_upgrade

COMPANY = 10
PROJETO = 100
PESQUISA = 1000
MACAPA = 900
SANTANA = 901


def usuario(user_id=1, company_id=COMPANY, perfil="Gerente"):
    return SimpleNamespace(
        id=user_id, email=f"u{user_id}@a", company_id=company_id, ativo=True,
        perfil=SimpleNamespace(nome=perfil),
    )


class CotaPerfilRegraTests(unittest.TestCase):
    def test_CP_B11_a_B14_classificacao_por_desvio(self):
        self.assertEqual(svc.classificar_prioridade(-22.86), "ALTO")   # CP-B11
        self.assertEqual(svc.classificar_prioridade(-15), "MEDIO")     # CP-B12
        self.assertEqual(svc.classificar_prioridade(-8), "BAIXO")      # CP-B13
        self.assertEqual(svc.classificar_prioridade(-3), "EQUILIBRADO")  # CP-B14
        # Fronteiras: -5 e -10 e -20 pertencem ao nivel mais leve.
        self.assertEqual(svc.classificar_prioridade(-5), "EQUILIBRADO")
        self.assertEqual(svc.classificar_prioridade(-5.01), "BAIXO")
        self.assertEqual(svc.classificar_prioridade(-10), "BAIXO")
        self.assertEqual(svc.classificar_prioridade(-20), "MEDIO")
        self.assertEqual(svc.classificar_prioridade(0), "EQUILIBRADO")
        self.assertEqual(svc.classificar_prioridade(12), "EQUILIBRADO")
        self.assertEqual(svc.LIMITE_DEFICIT_BAIXO, -5)
        self.assertEqual(svc.LIMITE_DEFICIT_MEDIO, -10)
        self.assertEqual(svc.LIMITE_DEFICIT_ALTO, -20)
        self.assertEqual(svc.FRACAO_MINIMA_PARA_PRIORIDADE, 0.10)


class CotaPerfilTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._dir = TemporaryDirectory()
        cls.db_path = Path(cls._dir.name) / "cp.sqlite"
        run_alembic_upgrade(f"sqlite:///{cls.db_path.as_posix()}")

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def setUp(self):
        self.engine = create_engine(f"sqlite:///{self.db_path.as_posix()}")

        @event.listens_for(self.engine, "connect")
        def _funcoes(conexao, _):
            conexao.create_function("ST_GeomFromText", -1, lambda *a: a[0])
            conexao.create_function("ST_AsGeoJSON", 1, lambda v: None)
            conexao.create_function("AsGeoJSON", 1, lambda v: None)
            conexao.create_function("AsEWKB", 1, lambda v: None)
            conexao.create_function("GeomFromEWKT", 1, lambda v: v)
            conexao.create_function("ST_GeomFromEWKT", 1, lambda v: v)

        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as c:
            for tabela in (
                "cotas_perfil", "planos_cota_perfil", "tentativas_campo", "respostas", "coletas",
                "pergunta_territorio_eleitoral", "opcoes", "perguntas",
                "setor_territorio_eleitoral", "setor_agentes", "setores",
                "projeto_base_eleitoral", "territorio_eleitoral", "base_eleitoral",
                "pesquisas", "projetos", "usuarios", "perfis", "companies",
            ):
                c.execute(text(f"DELETE FROM {tabela}"))
            c.execute(text(f"INSERT INTO companies (id, name, is_active) VALUES ({COMPANY},'A',1),(20,'B',1)"))
            c.execute(text("INSERT INTO perfis (id, nome) VALUES (1,'Gerente'),(2,'Agente')"))
            c.execute(text(
                "INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id)"
                f" VALUES (1,'g@a','Gerente A','x',1,1,{COMPANY}), (2,'a1@a','Agente 1','x',1,2,{COMPANY}),"
                f" (4,'a2@a','Agente 2','x',1,2,{COMPANY}), (3,'g@b','Gerente B','x',1,1,20)"
            ))
            c.execute(text(
                "INSERT INTO projetos (id, nome, status, coordenador_id, company_id)"
                f" VALUES ({PROJETO},'P','Ativo',1,{COMPANY}), (200,'P B','Ativo',3,20)"
            ))
            c.execute(text(
                "INSERT INTO pesquisas (id, titulo, ativo, projeto_id)"
                f" VALUES ({PESQUISA},'Q',1,{PROJETO}), (1001,'Q2',1,{PROJETO}), (2000,'Q B',1,200)"
            ))
        self.db = self.Session()
        self.addCleanup(self.db.close)

        self.app = FastAPI()
        self.app.include_router(rotas_projetos.router)
        self.app.include_router(rotas_coletas.router)
        self.app.include_router(rotas_cotas.router)
        self.app.include_router(rotas_agente.router, prefix="/agente")
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.usuario_atual = usuario()
        self.app.dependency_overrides[get_current_user] = lambda: self.usuario_atual
        self.app.dependency_overrides[require_manager_or_superadmin] = lambda: self.usuario_atual
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

        self.geocoding = patch("pesquisa360.crud.geocoding.obter_endereco_por_coords", return_value="x")
        self.geocoding.start()
        self.addCleanup(self.geocoding.stop)

    # --- fixtures -----------------------------------------------------------
    def criar_base(self, base_id=1, projeto_id=PROJETO, company_id=COMPANY):
        self.db.execute(text(
            "INSERT INTO base_eleitoral (id, nome, ano, uf, fonte, versao, data_referencia, status, company_id, criado_por_id)"
            " VALUES (:id, :nome, 2026, 'AP', 'TSE', '1', '2026-01-01', 'VALIDADA', :company, 1)"
        ), {"id": base_id, "nome": f"Base {base_id}", "company": company_id})
        self.db.execute(text(
            "INSERT INTO projeto_base_eleitoral (projeto_id, base_eleitoral_id, principal) VALUES (:p, :b, 1)"
        ), {"p": projeto_id, "b": base_id})
        self.db.execute(text(
            "INSERT INTO territorio_eleitoral (id, base_eleitoral_id, parent_id, tipo, nome, nome_normalizado, municipio_id, status_validacao)"
            " VALUES (:id, :b, NULL, 'ESTADO', 'Amapa', 'amapa', NULL, 'VALIDADA')"
        ), {"id": base_id * 10_000, "b": base_id})
        self.db.commit()

    def criar_municipio(self, tid, nome, base_id=1):
        self.db.execute(text(
            "INSERT INTO territorio_eleitoral (id, base_eleitoral_id, parent_id, tipo, nome, nome_normalizado, municipio_id, status_validacao)"
            " VALUES (:id, :b, :estado, 'MUNICIPIO', :nome, :norm, NULL, 'VALIDADA')"
        ), {"id": tid, "b": base_id, "estado": base_id * 10_000, "nome": nome, "norm": nome.lower()})
        self.db.commit()

    def criar_bairro(self, tid, nome, municipio_id, base_id=1):
        self.db.execute(text(
            "INSERT INTO territorio_eleitoral (id, base_eleitoral_id, parent_id, tipo, nome, nome_normalizado, municipio_id, status_validacao)"
            " VALUES (:id, :b, :parent, 'BAIRRO', :nome, :norm, :mun, 'VALIDADA')"
        ), {"id": tid, "b": base_id, "parent": municipio_id, "nome": nome, "norm": nome.lower(), "mun": municipio_id})
        self.db.commit()

    def criar_setor(self, sid, nome, bairros=(), agente_id=2, pesquisa_id=PESQUISA, meta=100):
        self.db.execute(text(
            "INSERT INTO setores (id, nome, meta, tolerancia, finalidade, pesquisa_id, agente_id)"
            " VALUES (:id, :nome, :meta, 50, 'OPERACAO', :p, :a)"
        ), {"id": sid, "nome": nome, "meta": meta, "p": pesquisa_id, "a": agente_id})
        self.db.execute(text("INSERT INTO setor_agentes (setor_id, agente_id, ativo) VALUES (:s, :a, 1)"), {"s": sid, "a": agente_id})
        for bairro in bairros:
            self.db.execute(text(
                "INSERT INTO setor_territorio_eleitoral (setor_id, territorio_eleitoral_id) VALUES (:s, :t)"
            ), {"s": sid, "t": bairro})
        self.db.commit()

    def pergunta(self, pid, texto, tipo, pesquisa_id=PESQUISA):
        self.db.execute(text(
            "INSERT INTO perguntas (id, texto_pergunta, tipo_pergunta, ordem, eh_obrigatoria, ativo, pesquisa_id)"
            " VALUES (:id, :t, :tipo, :id, 1, 1, :p)"
        ), {"id": pid, "t": texto, "tipo": tipo, "p": pesquisa_id})
        self.db.commit()

    def cenario(self):
        """Base + Macapa (Centro 500, agente 2) e Santana (Provedor 501, agente 4)."""
        self.criar_base()
        self.criar_municipio(MACAPA, "Macapa")
        self.criar_municipio(SANTANA, "Santana")
        self.criar_bairro(101, "Centro", MACAPA)
        self.criar_bairro(201, "Provedor", SANTANA)
        self.criar_setor(500, "Centro", [101], agente_id=2)
        self.criar_setor(501, "Provedor", [201], agente_id=4)
        self.pergunta(11, "Sexo", "ESCOLHA_SIMPLES")
        self.pergunta(12, "Idade", "NUMERO")
        self.pergunta(13, "Voto", "ESCOLHA_SIMPLES")
        self.pergunta(91, "Sexo outra pesquisa", "ESCOLHA_SIMPLES", pesquisa_id=1001)

    @staticmethod
    def faixas(metas):
        """metas: {(sexo, rotulo): meta} -> lista de cotas numericas."""
        limites = {"16-24": (16, 24), "25-34": (25, 34), "35-44": (35, 44), "45-59": (45, 59), "60+": (60, None)}
        return [
            {"sexo": sexo, "faixa_etaria": rotulo, "idade_min": limites[rotulo][0], "idade_max": limites[rotulo][1], "meta": meta}
            for (sexo, rotulo), meta in metas.items()
        ]

    def plano_macapa(self, **overrides):
        """Macapa: 775 entrevistas, Sexo x Faixa (matriz do enunciado)."""
        metas = {
            ("FEMININO", "16-24"): 73, ("MASCULINO", "16-24"): 67,
            ("FEMININO", "25-34"): 101, ("MASCULINO", "25-34"): 93,
            ("FEMININO", "35-44"): 93, ("MASCULINO", "35-44"): 85,
            ("FEMININO", "45-59"): 85, ("MASCULINO", "45-59"): 78,
            ("FEMININO", "60+"): 51, ("MASCULINO", "60+"): 49,
        }
        payload = {
            "pergunta_sexo_id": 11,
            "pergunta_idade_id": 12,
            "modo_idade": "NUMERICA",
            "sexo_valores": {"MASCULINO": ["Masculino"], "FEMININO": ["Feminino"]},
            "territorios": [{"territorio_id": MACAPA, "cotas": self.faixas(metas)}],
        }
        payload.update(overrides)
        return payload

    def put(self, payload, projeto_id=PROJETO, pesquisa_id=PESQUISA):
        return self.client.put(f"/projetos/{projeto_id}/pesquisas/{pesquisa_id}/cotas-perfil", json=payload)

    def coleta(self, setor_id, sexo=None, idade=None, agente=2, pesquisa_id=PESQUISA):
        anterior = self.usuario_atual
        self.usuario_atual = usuario(agente, perfil="Agente")
        try:
            respostas = [{"pergunta_id": 13, "valor_resposta": "A"}]
            if sexo is not None:
                respostas.append({"pergunta_id": 11, "valor_resposta": sexo})
            if idade is not None:
                respostas.append({"pergunta_id": 12, "valor_resposta": str(idade)})
            r = self.client.post(f"/pesquisas/{pesquisa_id}/coletas/", json={
                "client_uuid": str(uuid4()), "setor_id": setor_id,
                "data_inicio_coleta": datetime.now(timezone.utc).isoformat(),
                "respostas": respostas,
            })
            assert r.status_code == 201, r.text
            return r.json()["id"]
        finally:
            self.usuario_atual = anterior

    def progresso(self):
        return self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil/progresso").json()

    def celula(self, sexo, faixa, tid=MACAPA):
        for t in self.progresso()["territorios"]:
            if t["territorio_id"] == tid:
                for c in t["celulas"]:
                    if c["sexo"] == sexo and c["faixa_etaria"] == faixa:
                        return c
        return None

    def missao(self, agente_id=2):
        anterior = self.usuario_atual
        self.usuario_atual = usuario(agente_id, perfil="Agente")
        try:
            r = self.client.get(f"/agente/missao/{PESQUISA}")
            assert r.status_code == 200, r.text
            return r.json()
        finally:
            self.usuario_atual = anterior

    # --- testes -------------------------------------------------------------
    def test_CP_B01_criar_plano_valido(self):
        self.cenario()
        r = self.put(self.plano_macapa())
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["ativo"])
        self.assertEqual(len(body["cotas"]), 10)
        self.assertEqual(body["sexo_valores"]["MASCULINO"], ["Masculino"])
        # CP-B05: Macapa Homem 60+ meta 49 persistida.
        h60 = [c for c in body["cotas"] if c["sexo"] == "MASCULINO" and c["faixa_etaria"] == "60+"][0]
        self.assertEqual(h60["meta"], 49)
        self.assertEqual(h60["idade_min"], 60)
        self.assertIsNone(h60["idade_max"])
        self.assertEqual(h60["territorio_nome"], "Macapa")
        # Diagnostico de total (sem meta municipal formal -> so informativo).
        self.assertEqual(body["diagnostico"][0]["total_cotas_perfil"], 775)
        # ADR-035: meta territorial consolidada = setores operacionais do municipio (Centro=100).
        self.assertEqual(body["diagnostico"][0]["meta_territorial"], 100)
        self.assertEqual(body["diagnostico"][0]["diferenca"], 775 - 100)
        self.assertEqual([s["nome"] for s in body["diagnostico"][0]["setores_operacionais"]], ["Centro"])
        self.assertEqual(self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil").status_code, 200)

    def test_CP_B02_pergunta_sexo_de_outra_pesquisa_rejeita(self):
        self.cenario()
        r = self.put(self.plano_macapa(pergunta_sexo_id=91))
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(self.db.query(models.PlanoCotaPerfil).count(), 0)

    def test_CP_B03_pergunta_idade_de_outra_pesquisa_rejeita(self):
        self.cenario()
        self.pergunta(92, "Idade outra", "NUMERO", pesquisa_id=1001)
        r = self.put(self.plano_macapa(pergunta_idade_id=92))
        self.assertEqual(r.status_code, 422, r.text)

    def test_CP_B04_territorio_de_outro_tenant_404(self):
        self.cenario()
        self.criar_base(base_id=2, projeto_id=200, company_id=20)
        self.criar_municipio(950, "Oiapoque", base_id=2)
        payload = self.plano_macapa()
        payload["territorios"][0]["territorio_id"] = 950
        r = self.put(payload)
        self.assertEqual(r.status_code, 404, r.text)
        self.assertEqual(self.db.query(models.PlanoCotaPerfil).count(), 0)

    def test_validacoes_da_matriz(self):
        self.cenario()
        base = self.plano_macapa()
        sobreposta = dict(base)
        sobreposta["territorios"] = [{"territorio_id": MACAPA, "cotas": [
            {"sexo": "MASCULINO", "faixa_etaria": "16-34", "idade_min": 16, "idade_max": 34, "meta": 10},
            {"sexo": "MASCULINO", "faixa_etaria": "30-44", "idade_min": 30, "idade_max": 44, "meta": 10},
        ]}]
        self.assertEqual(self.put(sobreposta).status_code, 422)
        invertida = dict(base)
        invertida["territorios"] = [{"territorio_id": MACAPA, "cotas": [
            {"sexo": "MASCULINO", "faixa_etaria": "x", "idade_min": 40, "idade_max": 30, "meta": 10},
        ]}]
        self.assertEqual(self.put(invertida).status_code, 422)
        negativa = dict(base)
        negativa["territorios"] = [{"territorio_id": MACAPA, "cotas": [
            {"sexo": "MASCULINO", "faixa_etaria": "60+", "idade_min": 60, "meta": -1},
        ]}]
        self.assertEqual(self.put(negativa).status_code, 422)
        self.assertEqual(self.put(self.plano_macapa(pergunta_idade_id=11)).status_code, 422)

    def test_CP_B06_B07_classificacao_numerica(self):
        self.cenario()
        self.put(self.plano_macapa())
        self.coleta(500, "Masculino", 64)   # Homem 60+
        self.coleta(500, "masculino ", 59)  # Homem 45-59 (normalizacao)
        self.coleta(500, "Feminino", 16)    # Mulher 16-24
        self.assertEqual(self.celula("MASCULINO", "60+")["realizado"], 1)      # CP-B06
        self.assertEqual(self.celula("MASCULINO", "45-59")["realizado"], 1)    # CP-B07
        self.assertEqual(self.celula("FEMININO", "16-24")["realizado"], 1)
        self.assertEqual(self.progresso()["nao_classificadas"], 0)

    def test_CP_B08_resposta_ausente_nao_classificada(self):
        self.cenario()
        self.put(self.plano_macapa())
        self.coleta(500, None, 64)            # sem sexo
        self.coleta(500, "Masculino", None)   # sem idade
        self.coleta(500, "Outro", 40)         # sexo fora do mapa
        self.coleta(500, "Masculino", 15)     # fora de qualquer faixa
        self.coleta(500, "Masculino", "abc")  # idade invalida
        p = self.progresso()
        self.assertEqual(p["nao_classificadas"], 5)
        self.assertEqual(p["motivos_nao_classificadas"], {
            "SEM_SEXO": 1, "SEM_IDADE": 1, "SEXO_DESCONHECIDO": 1, "SEM_CELULA": 1, "IDADE_INVALIDA": 1,
        })
        self.assertEqual(sum(c["realizado"] for t in p["territorios"] for c in t["celulas"]), 0)

    def test_CP_B09_tentativas_sem_coleta_nao_contam(self):
        self.cenario()
        self.put(self.plano_macapa())
        anterior = self.usuario_atual
        self.usuario_atual = usuario(2, perfil="Agente")
        from pesquisa360.api.endpoints import tentativas_campo
        self.app.include_router(tentativas_campo.router)
        try:
            for resultado in ("RECUSA", "NAO_ELEGIVEL", "DESISTENCIA"):
                r = self.client.post(f"/pesquisas/{PESQUISA}/tentativas-campo/", json={
                    "client_uuid": str(uuid4()), "setor_id": 500,
                    "iniciada_em": "2026-08-27T10:00:00-03:00", "encerrada_em": "2026-08-27T10:01:00-03:00",
                    "localizacao": {"lat": 0.03, "lng": -51.06}, "resultado": resultado, "motivo": "OUTRO",
                })
                self.assertEqual(r.status_code, 201, r.text)
        finally:
            self.usuario_atual = anterior
        p = self.progresso()
        self.assertEqual(sum(c["realizado"] for t in p["territorios"] for c in t["celulas"]), 0)
        self.assertEqual(p["nao_classificadas"], 0)

    def test_CP_B10_B11_percentual_e_desvio_do_enunciado(self):
        """Macapa 620/775 (80%); Homem 60+ 28/49 (57,14%) -> -22,86 ALTO."""
        self.cenario()
        self.put(self.plano_macapa())
        distrib = {
            ("Feminino", 20): 73, ("Masculino", 20): 67, ("Feminino", 30): 101, ("Masculino", 30): 93,
            ("Feminino", 40): 93, ("Masculino", 40): 85, ("Feminino", 50): 85, ("Masculino", 50): 0,
            ("Feminino", 65): 0, ("Masculino", 65): 28,
        }
        # 73+67+101+93+93+85+85+0+0+28 = 625 -> ajusta Feminino 45-59 para 80: total 620
        distrib[("Feminino", 50)] = 80
        for (sexo, idade), n in distrib.items():
            for _ in range(n):
                self.db.execute(text(
                    "INSERT INTO coletas (pesquisa_id, agente_id, company_id, client_uuid, setor_id, foi_offline,"
                    " inconformidade_localizacao, status_sincronizacao, data_inicio_coleta)"
                    " VALUES (:p, 2, :c, :u, 500, 0, 0, 'sincronizado', '2026-08-27 10:00:00')"
                ), {"p": PESQUISA, "c": COMPANY, "u": str(uuid4())})
                cid = self.db.execute(text("SELECT MAX(id) FROM coletas")).scalar()
                self.db.execute(text("INSERT INTO respostas (pergunta_id, coleta_id, valor_resposta) VALUES (11, :c, :s), (12, :c, :i)"),
                                {"c": cid, "s": sexo, "i": str(idade)})
        self.db.commit()
        p = self.progresso()
        t = p["territorios"][0]
        self.assertEqual(t["realizado_total"], 620)
        self.assertEqual(t["meta_total"], 775)
        self.assertAlmostEqual(t["percentual_territorio"], 80.0, places=1)
        h60 = self.celula("MASCULINO", "60+")
        self.assertEqual(h60["realizado"], 28)                       # CP-B10
        self.assertEqual(h60["restante"], 21)
        self.assertAlmostEqual(h60["percentual_atingimento"], 57.14, places=1)
        self.assertAlmostEqual(h60["desvio_pp"], -22.86, places=1)   # CP-B11
        self.assertEqual(h60["prioridade"], "ALTO")
        # Homem 45-59 com 0/78 -> -80 ALTO (mais deficitario); Mulher 60+ 0/51 idem.
        self.assertEqual(self.celula("MASCULINO", "45-59")["prioridade"], "ALTO")

    def test_CP_B15_territorio_abaixo_de_10_por_cento_sem_prioridade(self):
        self.cenario()
        self.put(self.plano_macapa())
        for _ in range(3):
            self.coleta(500, "Feminino", 30)  # 3/775 < 10%
        missao = self.missao(2)
        self.assertTrue(missao["plano_cota_perfil_ativo"])
        self.assertEqual(missao["prioridades_perfil"], [])
        self.assertEqual(missao["perfil_status_territorios"], {str(MACAPA): "FASE_INICIAL"})
        self.assertTrue(self.progresso()["territorios"][0]["fase_inicial"])

    def test_CP_B16_B17_celula_completa_nao_aparece_e_ordenacao(self):
        self.cenario()
        payload = self.plano_macapa()
        payload["territorios"] = [{"territorio_id": MACAPA, "cotas": self.faixas({
            ("MASCULINO", "60+"): 2, ("MASCULINO", "45-59"): 4, ("FEMININO", "60+"): 4, ("FEMININO", "45-59"): 10,
        })}]
        self.put(payload)
        # Meta total 20. Homem 60+ 2/2 (completa), Homem 45-59 0/4, Mulher 60+ 1/4, Mulher 45-59 5/10.
        for _ in range(2):
            self.coleta(500, "Masculino", 70)
        self.coleta(500, "Feminino", 70)
        for _ in range(5):
            self.coleta(500, "Feminino", 50)
        # territorio 8/20 = 40%: H45-59 0% (-40 ALTO), M60+ 25% (-15 MEDIO), M45-59 50% (+10 nao aparece)
        prioridades = self.missao(2)["prioridades_perfil"]
        chaves = [(p["sexo"], p["faixa_etaria"], p["prioridade"]) for p in prioridades]
        self.assertEqual(chaves, [("MASCULINO", "45-59", "ALTO"), ("FEMININO", "60+", "MEDIO")])
        self.assertNotIn(("MASCULINO", "60+"), [(p["sexo"], p["faixa_etaria"]) for p in prioridades])  # CP-B16
        self.assertLess(prioridades[0]["desvio_pp"], prioridades[1]["desvio_pp"])                    # CP-B17
        self.assertEqual(prioridades[0]["sexo_rotulo"], "Homem")

    def test_CP_B18_missao_recorta_por_territorio_do_agente(self):
        self.cenario()
        payload = self.plano_macapa()
        payload["territorios"].append({"territorio_id": SANTANA, "cotas": self.faixas({
            ("MASCULINO", "60+"): 4, ("FEMININO", "60+"): 4,
        })})
        self.put(payload)
        # Santana 2/8 = 25%: Homem 60+ 0/4 -> ALTO; Macapa segue em fase inicial.
        self.coleta(501, "Feminino", 70, agente=4)
        self.coleta(501, "Feminino", 70, agente=4)
        m_macapa = self.missao(2)
        m_santana = self.missao(4)
        self.assertEqual(m_macapa["prioridades_perfil"], [])
        self.assertEqual(list(m_macapa["perfil_status_territorios"]), [str(MACAPA)])
        self.assertEqual([p["territorio_nome"] for p in m_santana["prioridades_perfil"]], ["Santana"])
        self.assertEqual(m_santana["perfil_status_territorios"], {str(SANTANA): "PRIORIDADES"})
        self.assertRegex(m_santana["prioridades_perfil_snapshot_em"], r"^\d{4}-")
        # Setor com municipio: o payload traz o id que liga ao card do Mobile.
        self.assertEqual(m_santana["setores"][0]["municipio"], {"id": SANTANA, "nome": "Santana"})

    def test_CP_B19_outro_tenant_nao_acessa_plano(self):
        self.cenario()
        self.put(self.plano_macapa())
        self.usuario_atual = usuario(3, company_id=20)
        self.assertEqual(self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil").status_code, 404)
        self.assertEqual(self.put(self.plano_macapa()).status_code, 404)
        self.assertEqual(self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil/progresso").status_code, 404)
        self.usuario_atual = usuario()
        self.assertEqual(self.db.query(models.PlanoCotaPerfil).count(), 1)

    def test_CP_B21_superadmin_de_outra_empresa_configura_e_le_o_plano(self):
        """ADR-034: o tenant do plano e o do Projeto, nao a empresa de quem configura."""
        self.cenario()
        self.usuario_atual = usuario(9, company_id=30, perfil="Superadmin")
        self.assertEqual(self.put(self.plano_macapa()).status_code, 200)
        plano = self.db.query(models.PlanoCotaPerfil).one()
        self.assertEqual(plano.company_id, COMPANY, "gravado no tenant do Projeto, nao no do Superadmin")
        for quem in (usuario(9, company_id=30, perfil="Superadmin"), usuario()):
            self.usuario_atual = quem
            self.assertEqual(self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil").status_code, 200)
            self.assertEqual(self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil/progresso").status_code, 200)

    def test_CP_B22_gerente_principal_A_com_acl_no_projeto_B_configura_pesquisa_B(self):
        """Usuario principal A + ACL Projeto B -> perguntas B, base/municipios B, plano B."""
        self.cenario()
        # Projeto B (200) com base propria e municipio proprio; pesquisa 2000.
        self.criar_base(base_id=2, projeto_id=200, company_id=20)
        self.criar_municipio(2001, "Laranjal", base_id=2)
        self.pergunta(21, "Sexo B", "ESCOLHA_SIMPLES", pesquisa_id=2000)
        self.pergunta(22, "Idade B", "NUMERO", pesquisa_id=2000)
        self.db.execute(text(
            "INSERT INTO usuario_empresa_acessos (usuario_id, company_id, acesso_todos_projetos, ativo, principal)"
            f" VALUES (5, {COMPANY}, 1, 1, 1), (5, 20, 0, 1, 0)"))
        self.db.execute(text("INSERT INTO usuario_projeto_acessos (usuario_id, projeto_id, ativo) VALUES (5, 200, 1)"))
        self.db.commit()
        self.usuario_atual = usuario(5, company_id=COMPANY)
        payload = self.plano_macapa(pergunta_sexo_id=21, pergunta_idade_id=22,
                                    territorios=[{"territorio_id": 2001, "cotas": self.faixas({("FEMININO", "16-24"): 5})}])
        r = self.put(payload, projeto_id=200, pesquisa_id=2000)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["cotas"][0]["territorio_nome"], "Laranjal")
        plano = self.db.query(models.PlanoCotaPerfil).filter_by(pesquisa_id=2000).one()
        self.assertEqual(plano.company_id, 20, "tenant do Projeto B, nao a empresa principal A")
        # Municipio da base A (empresa principal do usuario) NAO serve ao Projeto B.
        payload["territorios"] = [{"territorio_id": MACAPA, "cotas": self.faixas({("FEMININO", "16-24"): 5})}]
        self.assertEqual(self.put(payload, projeto_id=200, pesquisa_id=2000).status_code, 404)
        # E o Gerente B (dono) le exatamente o mesmo plano.
        self.usuario_atual = usuario(3, company_id=20)
        self.assertEqual(self.client.get("/projetos/200/pesquisas/2000/cotas-perfil").json()["id"], plano.id)

    def test_CP_B20_atualizacao_transacional(self):
        self.cenario()
        self.assertEqual(self.put(self.plano_macapa()).status_code, 200)
        self.assertEqual(self.db.query(models.CotaPerfil).count(), 10)
        # PUT invalido (faixas sobrepostas) nao deixa metade: plano anterior intacto.
        ruim = self.plano_macapa()
        ruim["territorios"][0]["cotas"].append(
            {"sexo": "MASCULINO", "faixa_etaria": "dup", "idade_min": 60, "idade_max": 70, "meta": 1}
        )
        self.assertEqual(self.put(ruim).status_code, 422)
        self.db.expire_all()
        self.assertEqual(self.db.query(models.CotaPerfil).count(), 10)
        self.assertEqual(self.db.query(models.PlanoCotaPerfil).count(), 1)
        # PUT valido menor substitui atomicamente (mesmo plano, cotas trocadas).
        menor = self.plano_macapa()
        menor["territorios"][0]["cotas"] = self.faixas({("MASCULINO", "60+"): 49})
        r = self.put(menor)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.db.query(models.CotaPerfil).count(), 1)
        self.assertEqual(self.db.query(models.PlanoCotaPerfil).count(), 1)

    def test_perfil_nunca_bloqueia_coleta_nem_sync(self):
        """Celula cheia + territorio cheio: POST de coleta continua 201."""
        self.cenario()
        payload = self.plano_macapa()
        payload["territorios"] = [{"territorio_id": MACAPA, "cotas": self.faixas({("FEMININO", "25-34"): 1})}]
        self.put(payload)
        self.coleta(500, "Feminino", 30)
        self.assertEqual(self.celula("FEMININO", "25-34")["realizado"], 1)
        cid = self.coleta(500, "Feminino", 31)  # meta atingida: aceita mesmo assim
        self.assertGreater(cid, 0)
        self.assertEqual(self.celula("FEMININO", "25-34")["realizado"], 2)
        self.assertEqual(self.missao(2)["prioridades_perfil"], [])

    def test_missao_sem_plano_e_modo_categorico(self):
        self.cenario()
        m = self.missao(2)
        self.assertFalse(m["plano_cota_perfil_ativo"])
        self.assertEqual(m["prioridades_perfil"], [])
        # Modo categorico: faixas pelos textos reais.
        self.pergunta(14, "Faixa etaria", "ESCOLHA_SIMPLES")
        payload = self.plano_macapa(pergunta_idade_id=14, modo_idade="CATEGORICA")
        payload["territorios"] = [{"territorio_id": MACAPA, "cotas": [
            {"sexo": "MASCULINO", "faixa_etaria": "60+", "idade_valores": ["60 anos ou mais"], "meta": 4},
            {"sexo": "MASCULINO", "faixa_etaria": "45-59", "idade_valores": ["45 a 59 anos"], "meta": 4},
        ]}]
        self.assertEqual(self.put(payload).status_code, 200)
        anterior = self.usuario_atual
        self.usuario_atual = usuario(2, perfil="Agente")
        r = self.client.post(f"/pesquisas/{PESQUISA}/coletas/", json={
            "client_uuid": str(uuid4()), "setor_id": 500,
            "data_inicio_coleta": datetime.now(timezone.utc).isoformat(),
            "respostas": [{"pergunta_id": 11, "valor_resposta": "Masculino"}, {"pergunta_id": 14, "valor_resposta": "60 anos ou mais"}],
        })
        self.usuario_atual = anterior
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(self.celula("MASCULINO", "60+")["realizado"], 1)


if __name__ == "__main__":
    unittest.main()
