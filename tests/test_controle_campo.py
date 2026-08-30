"""PROMPT 07 -- Painel Web consolidado de Controle de Campo (PW-B01..PW-B20).

Fixture Alembic real (mesma de test_cota_perfil): municipio resolvido pela
cadeia setor -> bairro -> municipio, plano de cotas de perfil, tentativas e
coletas reais via endpoints. O painel NAO recalcula nada: os testes conferem
que ele repete o motor oficial (cota territorial / perfil / cobertura) e que
os filtros seguem a tabela da §48 (agente/periodo nao tocam cota nem perfil).
"""
import os
import re
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely import wkb, wkt
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-controle-campo-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import cobertura_campo as rotas_cobertura
from pesquisa360.api.endpoints import coletas as rotas_coletas
from pesquisa360.api.endpoints import controle_campo as rotas_painel
from pesquisa360.api.endpoints import cotas_perfil as rotas_cotas
from pesquisa360.api.endpoints import tentativas_campo as rotas_tentativas
from pesquisa360.core.dependencies import get_current_user, get_db, require_manager_or_superadmin
from pesquisa360 import crud
from pesquisa360.services import controle_campo as svc
from tests.test_base_eleitoral_import import run_alembic_upgrade

COMPANY = 10
PROJETO = 100
PESQUISA = 1000
MACAPA = 900
SANTANA = 901
ROTA = f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/controle-campo"


def usuario(user_id=1, company_id=COMPANY, perfil="Gerente"):
    return SimpleNamespace(
        id=user_id, email=f"u{user_id}@a", company_id=company_id, ativo=True,
        perfil=SimpleNamespace(nome=perfil),
    )


class ControleCampoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._dir = TemporaryDirectory()
        cls.db_path = Path(cls._dir.name) / "cc.sqlite"
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
            conexao.create_function("ST_AsGeoJSON", 1, lambda v: None)
            conexao.create_function("AsGeoJSON", 1, lambda v: None)
            conexao.create_function("AsEWKB", 1, as_ewkb)
            conexao.create_function("ST_AsEWKB", 1, as_ewkb)
            conexao.create_function("GeomFromEWKT", 1, lambda v: v)
            conexao.create_function("ST_GeomFromEWKT", 1, lambda v: v)
            conexao.create_function("ST_Y", 1, lambda v: coord(v, 1))
            conexao.create_function("ST_X", 1, lambda v: coord(v, 0))

        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)
        with self.engine.begin() as c:
            for tabela in (
                "configuracoes_campo_pesquisa", "cotas_perfil", "planos_cota_perfil", "tentativas_campo",
                "respostas", "coletas", "pergunta_territorio_eleitoral", "opcoes", "perguntas",
                "setor_territorio_eleitoral", "setor_agentes", "setores",
                "projeto_base_eleitoral", "territorio_eleitoral", "base_eleitoral",
                "pesquisas", "projetos", "usuarios", "perfis", "companies",
            ):
                c.execute(text(f"DELETE FROM {tabela}"))
            c.execute(text(f"INSERT INTO companies (id, name, is_active) VALUES ({COMPANY},'A',1),(20,'B',1)"))
            c.execute(text("INSERT INTO perfis (id, nome) VALUES (1,'Gerente'),(2,'Agente')"))
            c.execute(text(
                "INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id)"
                f" VALUES (1,'g@a','Gerente A','x',1,1,{COMPANY}), (2,'a1@a','Agente Um','x',1,2,{COMPANY}),"
                f" (4,'a2@a','Agente Dois','x',1,2,{COMPANY}), (3,'g@b','Gerente B','x',1,1,20), (5,'a@b','Agente B','x',1,2,20)"
            ))
            c.execute(text(
                "INSERT INTO projetos (id, nome, status, coordenador_id, company_id)"
                f" VALUES ({PROJETO},'P','Ativo',1,{COMPANY}), (101,'P2','Ativo',1,{COMPANY}), (200,'P B','Ativo',3,20)"
            ))
            c.execute(text(
                "INSERT INTO pesquisas (id, titulo, ativo, projeto_id)"
                f" VALUES ({PESQUISA},'Q',1,{PROJETO}), (1001,'Q2',1,101), (2000,'Q B',1,200)"
            ))
        self.db = self.Session()
        self.addCleanup(self.db.close)

        self.app = FastAPI()
        self.app.include_router(rotas_coletas.router)
        self.app.include_router(rotas_cotas.router)
        self.app.include_router(rotas_tentativas.router)
        self.app.include_router(rotas_cobertura.router)
        self.app.include_router(rotas_painel.router)
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.usuario_atual = usuario()
        self.app.dependency_overrides[get_current_user] = lambda: self.usuario_atual
        self.app.dependency_overrides[require_manager_or_superadmin] = lambda: self.usuario_atual
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

        self.geocoding = patch("pesquisa360.crud.geocoding.obter_endereco_por_coords", return_value="Rua X - Fulano")
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

    def criar_setor(self, sid, nome, bairros=(), agente_id=2, pesquisa_id=PESQUISA, meta=10):
        self.db.execute(text(
            "INSERT INTO setores (id, nome, meta, tolerancia, finalidade, pesquisa_id, agente_id)"
            " VALUES (:id, :nome, :meta, 50, 'OPERACAO', :p, :a)"
        ), {"id": sid, "nome": nome, "meta": meta, "p": pesquisa_id, "a": agente_id})
        if agente_id is not None:
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
        """Base + Macapa (Centro 500 / agente 2) e Santana (Provedor 501 / agente 4)."""
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

    @staticmethod
    def faixas(metas):
        limites = {"16-24": (16, 24), "25-34": (25, 34), "35-44": (35, 44), "45-59": (45, 59), "60+": (60, None)}
        return [
            {"sexo": sexo, "faixa_etaria": rotulo, "idade_min": limites[rotulo][0], "idade_max": limites[rotulo][1], "meta": meta}
            for (sexo, rotulo), meta in metas.items()
        ]

    def plano_macapa(self):
        metas = {("FEMININO", "16-24"): 5, ("MASCULINO", "16-24"): 5, ("FEMININO", "60+"): 5, ("MASCULINO", "60+"): 5}
        return {
            "pergunta_sexo_id": 11, "pergunta_idade_id": 12, "modo_idade": "NUMERICA",
            "sexo_valores": {"MASCULINO": ["Masculino"], "FEMININO": ["Feminino"]},
            "territorios": [{"territorio_id": MACAPA, "cotas": self.faixas(metas)}],
        }

    def coleta(self, setor_id, sexo=None, idade=None, agente=2, gps=(0.0349, -51.0694), quando="2026-08-27T12:00:00Z"):
        anterior = self.usuario_atual
        self.usuario_atual = usuario(agente, perfil="Agente")
        try:
            respostas = [{"pergunta_id": 13, "valor_resposta": "Fulano da Silva CPF 123"}]
            if sexo is not None:
                respostas.append({"pergunta_id": 11, "valor_resposta": sexo})
            if idade is not None:
                respostas.append({"pergunta_id": 12, "valor_resposta": str(idade)})
            uuid = str(uuid4())
            body = {"client_uuid": uuid, "setor_id": setor_id, "data_inicio_coleta": quando, "respostas": respostas}
            if gps is not None:
                body["localizacao_inicio"] = {"lat": gps[0], "lon": gps[1]}
            r = self.client.post(f"/pesquisas/{PESQUISA}/coletas/", json=body)
            self.assertEqual(r.status_code, 201, r.text)
            return r.json()["id"], uuid
        finally:
            self.usuario_atual = anterior

    def tentativa(self, setor_id, resultado, agente=2, coleta_uuid=None, quando="2026-08-27T12:03:00Z", gps=(0.0354, -51.07)):
        anterior = self.usuario_atual
        self.usuario_atual = usuario(agente, perfil="Agente")
        try:
            r = self.client.post(f"/pesquisas/{PESQUISA}/tentativas-campo/", json={
                "client_uuid": str(uuid4()), "setor_id": setor_id,
                "iniciada_em": quando, "encerrada_em": quando,
                "localizacao": {"lat": gps[0], "lng": gps[1], "accuracy": 10.2, "capturada_em": quando},
                "resultado": resultado, "motivo": None if resultado == "CONCLUIDA" else "OUTRO",
                "coleta_client_uuid": coleta_uuid,
            })
            self.assertEqual(r.status_code, 201, r.text)
            return r.json()["id"]
        finally:
            self.usuario_atual = anterior

    def painel(self, query="", esperado=200, rota=ROTA):
        r = self.client.get(rota + (f"?{query}" if query else ""))
        self.assertEqual(r.status_code, esperado, r.text)
        return r.json()

    def setor(self, body, sid):
        return next(s for s in body["setores"] if s["setor_id"] == sid)

    # --- testes -------------------------------------------------------------
    def test_PW_B01_tenant_e_projeto_isolados_404(self):
        self.cenario()
        self.painel(esperado=200)
        self.usuario_atual = usuario(3, company_id=20)
        self.painel(esperado=404)                       # pesquisa de outro tenant
        self.usuario_atual = usuario()
        self.painel(esperado=404, rota=f"/projetos/101/pesquisas/{PESQUISA}/controle-campo")  # projeto errado
        self.painel(esperado=404, rota="/projetos/200/pesquisas/2000/controle-campo")

    def test_PW_B02_contrato_completo_e_snapshot(self):
        self.cenario()
        body = self.painel()
        self.assertEqual(set(body), {
            "pesquisa_id", "snapshot_em", "filtros", "opcoes", "resumo", "resumo_territorial", "alertas",
            "setores", "cotas_perfil", "tentativas_por_resultado", "atividade_campo",
        })
        self.assertEqual(body["pesquisa_id"], PESQUISA)
        self.assertRegex(body["snapshot_em"], r"^\d{4}-\d{2}-\d{2}T")
        self.assertIn("nao respondem a agente/periodo", body["filtros"]["nota"])
        self.assertEqual(body["resumo"]["taxa_conclusao_tentativas"], None)
        self.assertIn("CONCLUIDA / tentativas encerradas", body["resumo"]["nota_taxa"])

    def test_PW_B03_pesquisa_legada_sem_tentativa_plano_ou_config(self):
        self.cenario()
        self.coleta(500)
        self.coleta(500, gps=None)
        body = self.painel()
        r = body["resumo"]
        self.assertEqual((r["entrevistas_concluidas"], r["coletas_com_tentativa"], r["coletas_sem_tentativa"]), (2, 0, 2))
        self.assertEqual(r["tentativas_encerradas"], 0)
        self.assertIsNone(r["taxa_conclusao_tentativas"])
        self.assertEqual(body["tentativas_por_resultado"], [{"resultado": x, "total": 0} for x in svc.RESULTADOS_ENCERRADOS])
        self.assertEqual(sorted(svc.RESULTADOS_ENCERRADOS), sorted(("CONCLUIDA", "RECUSA", "NAO_ELEGIVEL", "DESISTENCIA", "INCOMPLETA", "PROBLEMA_TECNICO", "OUTRO")))
        self.assertEqual(body["cotas_perfil"], {"plano_ativo": False, "territorios": [], "nao_classificadas": 0,
                                                "motivos_nao_classificadas": {}, "snapshot_em": None})
        self.assertEqual(body["atividade_campo"]["distancia_recomendada_entre_abordagens_metros"], 100)
        self.assertFalse(body["atividade_campo"]["distancia_configurada"])
        self.assertEqual([e["tipo"] for e in body["atividade_campo"]["eventos"]], ["COLETA"])  # sem GPS nao vira evento
        self.assertEqual(self.setor(body, 500)["realizado"], 2)

    def test_PW_B04_cota_territorial_repete_motor_oficial(self):
        self.cenario()
        for _ in range(9):
            self.coleta(500, gps=None)
        self.coleta(501, gps=None, agente=4)
        body = self.painel()
        oficial = crud.obter_progressos_setores(self.db, self.db.query(crud.models.Setor).filter_by(pesquisa_id=PESQUISA).all(), pesquisa_id=PESQUISA)
        for sid in (500, 501):
            s = self.setor(body, sid)
            for chave in ("meta", "realizado", "restante", "excedente", "percentual_atingimento", "status_cota",
                          "limite_atencao_realizado", "agentes_atribuidos_total", "snapshot_ate_coleta_id"):
                self.assertEqual(s[chave], oficial[sid][chave], (sid, chave))
        s500 = self.setor(body, 500)
        self.assertEqual((s500["status_cota"], s500["limite_atencao_realizado"], s500["realizado"]), ("ATENCAO", 9, 9))
        self.assertEqual(self.setor(body, 501)["status_cota"], "ABERTO")
        self.assertEqual(s500["municipio_nome"], "Macapa")
        self.assertEqual(self.setor(body, 501)["municipio_id"], SANTANA)

    def test_PW_B05_ordenacao_atencao_aberto_encerrado_sem_cota(self):
        self.cenario()
        self.criar_setor(502, "Encerrado", agente_id=2, meta=1)
        self.criar_setor(503, "SemCota", agente_id=2, meta=0)
        self.criar_setor(504, "Aberto2", agente_id=2, meta=10)
        for _ in range(9):
            self.coleta(500, gps=None)
        self.coleta(502, gps=None)
        self.coleta(502, gps=None)   # excedente
        self.coleta(504, gps=None)
        body = self.painel()
        self.assertEqual([s["setor_id"] for s in body["setores"]], [500, 504, 501, 502, 503])
        self.assertEqual([s["status_cota"] for s in body["setores"]], ["ATENCAO", "ABERTO", "ABERTO", "ENCERRADO", "SEM_COTA"])
        self.assertEqual(body["resumo_territorial"], {"abertos": 2, "atencao": 1, "encerrados": 1, "sem_cota": 1, "com_excedente": 1})
        self.assertEqual(self.setor(body, 502)["excedente"], 1)
        self.assertEqual(body["resumo"]["meta_territorial"], 31)
        self.assertEqual(body["resumo"]["realizado_territorial"], 12)

    def test_PW_B06_tentativas_group_by_resultado_e_em_andamento_separado(self):
        self.cenario()
        for r in ("RECUSA", "RECUSA", "NAO_ELEGIVEL", "DESISTENCIA", "INCOMPLETA", "PROBLEMA_TECNICO", "OUTRO"):
            self.tentativa(500, r)
        self.db.execute(text(
            "INSERT INTO tentativas_campo (client_uuid, pesquisa_id, setor_id, agente_id, company_id, iniciada_em, latitude, longitude, resultado)"
            " VALUES ('u-ea', :p, 500, 2, :c, '2026-08-27 12:00:00', 0.03, -51.06, 'EM_ANDAMENTO')"
        ), {"p": PESQUISA, "c": COMPANY})
        self.db.commit()
        body = self.painel()
        r = body["resumo"]
        self.assertEqual((r["recusas"], r["nao_elegiveis"], r["desistencias"], r["incompletas"], r["problemas_tecnicos"], r["outros"]), (2, 1, 1, 1, 1, 1))
        self.assertEqual(r["tentativas_encerradas"], 7)
        self.assertEqual(r["tentativas_em_andamento"], 1)
        self.assertEqual(r["tentativas_concluidas"], 0)
        self.assertEqual(r["taxa_conclusao_tentativas"], 0.0)
        totais = {t["resultado"]: t["total"] for t in body["tentativas_por_resultado"]}
        self.assertEqual(totais["RECUSA"], 2)
        self.assertNotIn("EM_ANDAMENTO", totais)
        # EM_ANDAMENTO nunca vira evento no mapa (regra da cobertura).
        self.assertTrue(all(e["resultado"] != "EM_ANDAMENTO" for e in body["atividade_campo"]["eventos"]))

    def test_PW_B07_taxa_conclusao_e_coletas_sem_tentativa(self):
        self.cenario()
        _, u1 = self.coleta(500)
        _, u2 = self.coleta(500)
        self.tentativa(500, "CONCLUIDA", coleta_uuid=u1)
        self.tentativa(500, "CONCLUIDA", coleta_uuid=u2)
        self.tentativa(500, "RECUSA")
        self.tentativa(500, "RECUSA")
        self.coleta(500, gps=None)   # legada: sem tentativa
        r = self.painel()["resumo"]
        self.assertEqual(r["entrevistas_concluidas"], 3)
        self.assertEqual(r["coletas_com_tentativa"], 2)
        self.assertEqual(r["coletas_sem_tentativa"], 1)
        self.assertEqual(r["tentativas_concluidas"], 2)
        self.assertEqual(r["tentativas_encerradas"], 4)
        self.assertEqual(r["taxa_conclusao_tentativas"], 50.0)  # 2/4, nao 3/4

    def test_PW_B08_dedup_coleta_tentativa_concluida_e_identificacao_do_agente(self):
        self.cenario()
        cid, u1 = self.coleta(500)
        self.tentativa(500, "CONCLUIDA", coleta_uuid=u1)
        tid = self.tentativa(501, "RECUSA", agente=4)
        r = self.client.get(ROTA)
        body = r.json()
        ev = body["atividade_campo"]["eventos"]
        self.assertEqual([(e["tipo"], e["server_id"]) for e in ev], [("COLETA", cid), ("TENTATIVA", tid)])
        self.assertEqual(ev[0]["agente_id"], 2)
        self.assertEqual(ev[0]["agente_nome"], "Agente Um")
        self.assertEqual(ev[1]["agente_nome"], "Agente Dois")
        for proibido in ("Fulano", "CPF", "valor_resposta", "respostas\"", "endereco", "email", "client_uuid", "Rua X"):
            self.assertNotIn(proibido, r.text, proibido)
        self.assertEqual(set(ev[0]), {"tipo", "server_id", "setor_id", "lat", "lng", "accuracy", "ocorrido_em", "resultado", "agente_id", "agente_nome"})

    def test_PW_B09_endpoint_do_agente_continua_sem_agente(self):
        self.cenario()
        self.coleta(500)
        self.usuario_atual = usuario(2, perfil="Agente")
        r = self.client.get(f"/agente/pesquisas/{PESQUISA}/cobertura-campo/")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertNotIn("agente_id", r.text)
        self.assertNotIn("nome", r.text)
        self.assertEqual(r.json()["setor_ids"], [500])   # recorte do agente preservado

    def test_PW_B10_filtro_setor_restringe_tudo_e_setor_invalido_404(self):
        self.cenario()
        self.coleta(500)
        self.coleta(501, agente=4)
        self.tentativa(501, "RECUSA", agente=4)
        body = self.painel("setor_ids=501")
        self.assertEqual([s["setor_id"] for s in body["setores"]], [501])
        self.assertEqual(body["resumo"]["entrevistas_concluidas"], 1)
        self.assertEqual(body["resumo"]["recusas"], 1)
        self.assertEqual({e["setor_id"] for e in body["atividade_campo"]["eventos"]}, {501})
        self.assertEqual(body["filtros"]["setor_ids"], [501])
        self.assertEqual(len(body["opcoes"]["setores"]), 2)   # opcoes sempre completas
        self.painel("setor_ids=999", esperado=404)
        self.painel("setor_ids=abc", esperado=422)

    def test_PW_B11_filtro_municipio_recorta_setores_e_perfil(self):
        self.cenario()
        self.assertEqual(self.client.put(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil", json=self.plano_macapa()).status_code, 200)
        self.coleta(500, sexo="Feminino", idade=20)
        self.coleta(501, agente=4)
        body = self.painel(f"municipio_id={SANTANA}")
        self.assertEqual([s["setor_id"] for s in body["setores"]], [501])
        self.assertEqual(body["resumo"]["entrevistas_concluidas"], 1)
        self.assertTrue(body["cotas_perfil"]["plano_ativo"])
        self.assertEqual(body["cotas_perfil"]["territorios"], [])   # plano so tem Macapa
        body = self.painel(f"municipio_id={MACAPA}")
        self.assertEqual([t["territorio_id"] for t in body["cotas_perfil"]["territorios"]], [MACAPA])
        self.assertEqual(self.painel("municipio_id=777")["setores"], [])

    def test_PW_B12_filtro_agente_nao_altera_cota_nem_perfil(self):
        self.cenario()
        self.criar_setor(502, "Compartilhado", [101], agente_id=2)
        self.db.execute(text("INSERT INTO setor_agentes (setor_id, agente_id, ativo) VALUES (502, 4, 1)"))
        self.db.commit()
        self.coleta(502, agente=2)
        self.coleta(502, agente=4)
        self.tentativa(502, "RECUSA", agente=2)
        self.tentativa(502, "RECUSA", agente=4)
        tudo = self.painel()
        so4 = self.painel("agente_ids=4")
        self.assertEqual(self.setor(tudo, 502)["realizado"], 2)
        self.assertEqual(self.setor(so4, 502)["realizado"], 2)          # cota oficial intocada
        self.assertEqual(so4["resumo"]["realizado_territorial"], tudo["resumo"]["realizado_territorial"])
        self.assertEqual(so4["resumo"]["entrevistas_concluidas"], 1)
        self.assertEqual(so4["resumo"]["recusas"], 1)
        self.assertEqual({e["agente_id"] for e in so4["atividade_campo"]["eventos"]}, {4})
        self.assertEqual(len(tudo["atividade_campo"]["eventos"]), 4)

    def test_PW_B13_filtro_periodo_nao_altera_cota(self):
        self.cenario()
        self.coleta(500, quando="2026-08-01T10:00:00Z")
        self.coleta(500, quando="2026-08-20T10:00:00Z")
        self.tentativa(500, "RECUSA", quando="2026-08-01T11:00:00Z")
        self.tentativa(500, "RECUSA", quando="2026-08-20T11:00:00Z")
        body = self.painel("data_inicio=2026-08-15T00:00:00Z&data_fim=2026-08-31T00:00:00Z")
        self.assertEqual(self.setor(body, 500)["realizado"], 2)
        self.assertEqual(body["resumo"]["entrevistas_concluidas"], 1)
        self.assertEqual(body["resumo"]["recusas"], 1)
        self.assertEqual(len(body["atividade_campo"]["eventos"]), 2)
        self.assertTrue(all(e["ocorrido_em"].startswith("2026-08-20") for e in body["atividade_campo"]["eventos"]))
        self.painel("data_inicio=2026-08-31T00:00:00Z&data_fim=2026-08-01T00:00:00Z", esperado=422)

    def test_PW_B14_agente_de_outro_tenant_404_e_validacoes_422(self):
        self.cenario()
        self.painel("agente_ids=5", esperado=404)
        self.painel("agente_ids=999", esperado=404)
        self.painel("agente_ids=2,x", esperado=422)
        self.painel("resultado=BANANA", esperado=422)
        self.painel("municipio_id=0", esperado=422)
        self.painel("agente_ids=2,4")

    def test_PW_B15_filtro_resultado_so_no_mapa(self):
        self.cenario()
        self.coleta(500)
        self.tentativa(500, "RECUSA")
        self.tentativa(500, "DESISTENCIA")
        body = self.painel("resultado=RECUSA")
        ev = body["atividade_campo"]["eventos"]
        self.assertEqual([(e["tipo"], e["resultado"]) for e in ev], [("TENTATIVA", "RECUSA")])
        self.assertEqual(body["resumo"]["desistencias"], 1)      # resumo continua completo
        self.assertEqual(body["resumo"]["entrevistas_concluidas"], 1)

    def test_PW_B16_cotas_perfil_numeros_fase_inicial_e_nao_classificadas(self):
        self.cenario()
        self.assertEqual(self.client.put(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil", json=self.plano_macapa()).status_code, 200)
        self.coleta(500, sexo="Feminino", idade=20)
        self.coleta(500, sexo="Feminino", idade=None)   # idade ausente -> nao classificada
        self.coleta(500)                                  # sem sexo/idade
        body = self.painel()
        cp = body["cotas_perfil"]
        self.assertTrue(cp["plano_ativo"])
        self.assertEqual(cp["nao_classificadas"], 2)
        self.assertEqual(sum(cp["motivos_nao_classificadas"].values()), 2)
        self.assertRegex(cp["snapshot_em"], r"^\d{4}-")
        t = cp["territorios"][0]
        self.assertEqual((t["territorio_id"], t["territorio_nome"], t["meta_total"], t["realizado_total"]), (MACAPA, "Macapa", 20, 1))
        self.assertEqual(t["status"], "FASE_INICIAL")   # 1 < 10% de 20
        self.assertTrue(t["fase_inicial"])
        self.assertNotIn("prioridades", t)
        self.assertEqual(len(t["celulas"]), 4)
        f16 = next(c for c in t["celulas"] if c["sexo"] == "FEMININO" and c["faixa_etaria"] == "16-24")
        self.assertEqual((f16["meta"], f16["realizado"], f16["restante"]), (5, 1, 4))
        # Numeros completos (o Mobile nao os recebe; o painel sim).
        for chave in ("meta", "realizado", "restante", "percentual_atingimento", "desvio_pp", "prioridade"):
            self.assertIn(chave, f16)
        # Passa da fase inicial (5/20 = 25%): demais celulas a -25 pp -> ALTO primeiro.
        for _ in range(4):
            self.coleta(500, sexo="Feminino", idade=20)
        t = self.painel()["cotas_perfil"]["territorios"][0]
        self.assertEqual(t["status"], "PRIORIDADES")
        self.assertEqual([c["prioridade"] for c in t["celulas"]], sorted((c["prioridade"] for c in t["celulas"]), key=lambda p: {"ALTO": 0, "MEDIO": 1, "BAIXO": 2, "EQUILIBRADO": 3}[p]))
        self.assertEqual(t["celulas"][0]["prioridade"], "ALTO")

    def test_PW_B17_alertas_derivados(self):
        self.cenario()
        self.assertEqual(self.painel()["alertas"], [])
        self.criar_setor(502, "Curto", agente_id=2, meta=1)
        for _ in range(9):
            self.coleta(500, gps=None)
        self.coleta(502, gps=None)
        self.coleta(502, gps=None)
        self.assertEqual(self.client.put(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil", json=self.plano_macapa()).status_code, 200)
        alertas = {a["tipo"]: a for a in self.painel()["alertas"]}
        self.assertEqual(alertas["SETORES_ATENCAO"]["total"], 1)
        self.assertEqual(alertas["SETORES_EXCEDENTE"]["total"], 1)
        self.assertEqual(alertas["PERFIL_NAO_CLASSIFICADAS"]["total"], 11)
        self.assertTrue(all(a["mensagem"] for a in alertas.values()))

    def test_PW_B18_opcoes_de_filtro_e_eco_dos_filtros(self):
        self.cenario()
        self.criar_setor(502, "SemMunicipio", agente_id=None)
        body = self.painel("agente_ids=2,4&setor_ids=500,501&resultado=RECUSA")
        self.assertEqual(body["opcoes"]["municipios"], [{"id": MACAPA, "nome": "Macapa"}, {"id": SANTANA, "nome": "Santana"}])
        self.assertEqual([s["id"] for s in body["opcoes"]["setores"]], [500, 501, 502])
        self.assertEqual([a["nome"] for a in body["opcoes"]["agentes"]], ["Agente Dois", "Agente Um"])
        self.assertEqual(body["filtros"]["agente_ids"], [2, 4])
        self.assertEqual(body["filtros"]["setor_ids"], [500, 501])
        self.assertEqual(body["filtros"]["resultado"], "RECUSA")
        self.assertIsNone(body["filtros"]["municipio_id"])
        self.assertIsNone(self.setor(self.painel(), 502)["municipio_id"])

    def test_PW_B19_cobertura_gerencial_e_distancia_configurada(self):
        self.cenario()
        self.coleta(500)
        self.assertEqual(self.client.put(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/configuracao-campo",
                                         json={"distancia_recomendada_entre_abordagens_metros": 150}).status_code, 200)
        r = self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cobertura-campo")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(set(body), {"pesquisa_id", "snapshot_em", "setores", "distancia_recomendada_entre_abordagens_metros", "distancia_configurada", "eventos"})
        self.assertEqual(body["distancia_recomendada_entre_abordagens_metros"], 150)
        self.assertTrue(body["distancia_configurada"])
        self.assertEqual(body["eventos"][0]["agente_nome"], "Agente Um")
        self.assertEqual(self.painel()["atividade_campo"]["distancia_recomendada_entre_abordagens_metros"], 150)
        self.usuario_atual = usuario(3, company_id=20)
        self.assertEqual(self.client.get(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cobertura-campo").status_code, 404)

    def test_PW_B20_numero_de_consultas_nao_cresce_com_setores_e_coletas(self):
        self.cenario()
        self.assertEqual(self.client.put(f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/cotas-perfil", json=self.plano_macapa()).status_code, 200)
        self.coleta(500, sexo="Feminino", idade=20)
        self.tentativa(500, "RECUSA")

        def contar():
            n = [0]

            def _conta(*_):
                n[0] += 1

            event.listen(self.engine, "before_cursor_execute", _conta)
            try:
                self.painel()
            finally:
                event.remove(self.engine, "before_cursor_execute", _conta)
            return n[0]

        pequeno = contar()
        for sid in range(510, 518):
            self.criar_setor(sid, f"S{sid}", [101], agente_id=2)
            self.coleta(sid, sexo="Masculino", idade=70)
            self.coleta(sid)
            self.tentativa(sid, "RECUSA")
            self.tentativa(sid, "DESISTENCIA")
        grande = contar()
        self.assertEqual(pequeno, grande, (pequeno, grande))
        self.assertLessEqual(grande, 25)


if __name__ == "__main__":
    unittest.main()
