"""Aplicabilidade territorial das Perguntas (FASE F).

Schema montado pelo Alembic real: alem de evitar fixture manual defasada (a
divida da E.2), isso prova que a migration F aplica em SQLite e que as
perguntas nascem GLOBAL.

Os cenarios seguem o enunciado da fase: GLOBAL em todo setor, municipal so no
municipio certo, N:N, varios bairros do mesmo municipio, AMBIGUO,
SEM_MUNICIPIO, composicao cujos bairros nao resolvem municipio, tenant, tipo
invalido, payload incoerente, resposta territorial invalida e cliente legado.
"""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-pergunta-territorial-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import crud, schemas
from pesquisa360.api.endpoints import agente as rotas_agente
from pesquisa360.api.endpoints import coletas as rotas_coletas
from pesquisa360.api.endpoints import projetos as rotas_projetos
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from pesquisa360.services import pergunta_territorio as svc
from tests.test_base_eleitoral_import import run_alembic_upgrade

COMPANY = 10
PROJETO = 100
PESQUISA = 1000
MACAPA = 900
SANTANA = 901


def usuario(user_id=1, company_id=COMPANY, perfil="Gerente"):
    return SimpleNamespace(
        id=user_id,
        email=f"u{user_id}@a",
        company_id=company_id,
        ativo=True,
        perfil=SimpleNamespace(nome=perfil),
    )


class PerguntaTerritorialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._dir = TemporaryDirectory()
        cls.db_path = Path(cls._dir.name) / "f.sqlite"
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
            conexao.create_function("AsEWKB", 1, lambda v: None)
            conexao.create_function("GeomFromEWKT", 1, lambda v: v)
            conexao.create_function("ST_GeomFromEWKT", 1, lambda v: v)

        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)

        with self.engine.begin() as c:
            for tabela in (
                "respostas", "coletas", "pergunta_territorio_eleitoral", "opcoes",
                "perguntas", "setor_territorio_eleitoral", "setor_agentes", "setores",
                "projeto_base_eleitoral", "territorio_eleitoral", "base_eleitoral",
                "pesquisas", "projetos", "usuarios", "perfis", "companies",
            ):
                c.execute(text(f"DELETE FROM {tabela}"))
            c.execute(text(
                f"INSERT INTO companies (id, name, is_active) VALUES ({COMPANY},'A',1),(20,'B',1)"
            ))
            c.execute(text("INSERT INTO perfis (id, nome) VALUES (1,'Gerente'),(2,'Agente')"))
            c.execute(text(
                "INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id)"
                f" VALUES (1,'g@a','Gerente A','x',1,1,{COMPANY}),"
                f" (2,'a1@a','Agente 1','x',1,2,{COMPANY}),"
                " (3,'g@b','Gerente B','x',1,1,20)"
            ))
            c.execute(text(
                "INSERT INTO projetos (id, nome, status, coordenador_id, company_id)"
                f" VALUES ({PROJETO},'P','Ativo',1,{COMPANY}), (200,'P B','Ativo',3,20)"
            ))
            c.execute(text(
                "INSERT INTO pesquisas (id, titulo, ativo, projeto_id)"
                f" VALUES ({PESQUISA},'Q',1,{PROJETO}), (2000,'Q B',1,200)"
            ))

        self.db = self.Session()
        self.addCleanup(self.db.close)

        self.app = FastAPI()
        self.app.include_router(rotas_projetos.router)
        self.app.include_router(rotas_coletas.router)
        self.app.include_router(rotas_agente.router, prefix="/agente")
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.usuario_atual = usuario()
        self.app.dependency_overrides[get_current_user] = lambda: self.usuario_atual
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    # --- fixtures territoriais ---------------------------------------------

    def criar_base(self, base_id=1, projeto_id=PROJETO, company_id=COMPANY, versao="1"):
        self.db.execute(text(
            "INSERT INTO base_eleitoral (id, nome, ano, uf, fonte, versao, data_referencia,"
            " status, company_id, criado_por_id) VALUES (:id, :nome, 2026, 'AP', 'TSE',"
            " :versao, '2026-01-01', 'VALIDADA', :company, 1)"
        ), {"id": base_id, "nome": f"Base {base_id}", "versao": versao, "company": company_id})
        self.db.execute(text(
            "INSERT INTO projeto_base_eleitoral (projeto_id, base_eleitoral_id, principal)"
            " VALUES (:p, :b, 1)"
        ), {"p": projeto_id, "b": base_id})
        estado_id = base_id * 10_000
        self.db.execute(text(
            "INSERT INTO territorio_eleitoral (id, base_eleitoral_id, parent_id, tipo, nome,"
            " nome_normalizado, municipio_id, status_validacao) VALUES"
            " (:id, :b, NULL, 'ESTADO', 'Amapa', 'amapa', NULL, 'VALIDADA')"
        ), {"id": estado_id, "b": base_id})
        self.db.commit()
        return base_id

    def criar_municipio(self, tid, nome, base_id=1):
        self.db.execute(text(
            "INSERT INTO territorio_eleitoral (id, base_eleitoral_id, parent_id, tipo, nome,"
            " nome_normalizado, municipio_id, status_validacao) VALUES"
            " (:id, :b, :estado, 'MUNICIPIO', :nome, :norm, NULL, 'VALIDADA')"
        ), {"id": tid, "b": base_id, "estado": base_id * 10_000, "nome": nome, "norm": nome.lower()})
        self.db.commit()
        return tid

    def criar_bairro(self, tid, nome, municipio_id, base_id=1, *, municipio_resolvivel=True):
        # `municipio_resolvivel=False` reproduz o achado da E.4: bairro com
        # composicao mas sem relacao municipal utilizavel.
        self.db.execute(text(
            "INSERT INTO territorio_eleitoral (id, base_eleitoral_id, parent_id, tipo, nome,"
            " nome_normalizado, municipio_id, status_validacao) VALUES"
            " (:id, :b, :parent, 'BAIRRO', :nome, :norm, :mun, 'VALIDADA')"
        ), {
            "id": tid, "b": base_id, "parent": municipio_id, "nome": nome,
            "norm": nome.lower(), "mun": municipio_id if municipio_resolvivel else None,
        })
        self.db.commit()
        return tid

    def criar_setor(self, sid, nome, bairros=(), agente_id=2, pesquisa_id=PESQUISA):
        self.db.execute(text(
            "INSERT INTO setores (id, nome, meta, tolerancia, finalidade, pesquisa_id, agente_id)"
            " VALUES (:id, :nome, 100, 50, 'OPERACAO', :p, :a)"
        ), {"id": sid, "nome": nome, "p": pesquisa_id, "a": agente_id})
        self.db.execute(text(
            "INSERT INTO setor_agentes (setor_id, agente_id, ativo) VALUES (:s, :a, 1)"
        ), {"s": sid, "a": agente_id})
        for bairro in bairros:
            self.db.execute(text(
                "INSERT INTO setor_territorio_eleitoral (setor_id, territorio_eleitoral_id)"
                " VALUES (:s, :t)"
            ), {"s": sid, "t": bairro})
        self.db.commit()
        return sid

    def cenario_dois_municipios(self):
        """Base + Macapa/Santana + bairros + setores Centro (Macapa) e Provedor (Santana)."""
        self.criar_base()
        self.criar_municipio(MACAPA, "Macapa")
        self.criar_municipio(SANTANA, "Santana")
        self.criar_bairro(101, "Centro", MACAPA)
        self.criar_bairro(102, "Laguinho", MACAPA)
        self.criar_bairro(201, "Provedor", SANTANA)
        self.criar_setor(500, "Centro", [101, 102])
        self.criar_setor(501, "Provedor", [201])

    # --- perguntas via API -------------------------------------------------

    def pergunta(self, texto, ordem, aplicabilidade="GLOBAL", municipio_ids=None, pesquisa_id=PESQUISA):
        payload = {
            "texto_pergunta": texto, "tipo_pergunta": "TEXTO", "ordem": ordem,
            "eh_obrigatoria": True, "aplicabilidade": aplicabilidade,
        }
        if municipio_ids is not None:
            payload["municipio_ids"] = municipio_ids
        return self.client.post(f"/pesquisas/{pesquisa_id}/perguntas/", json=payload)

    def patch(self, pergunta_id, payload):
        return self.client.patch(f"/pesquisas/{PESQUISA}/perguntas/{pergunta_id}", json=payload)

    def questionario_padrao(self):
        """P01 GLOBAL, P10 Macapa, P11 Santana, P20 GLOBAL -- ordens 1,3,4,5."""
        ids = {}
        ids["P01"] = self.pergunta("Governador", 1).json()["id"]
        ids["P10"] = self.pergunta("Prefeito Macapa", 3, "TERRITORIAL", [MACAPA]).json()["id"]
        ids["P11"] = self.pergunta("Prefeito Santana", 4, "TERRITORIAL", [SANTANA]).json()["id"]
        ids["P20"] = self.pergunta("Senador", 5).json()["id"]
        return ids

    def missao(self, agente_id=2):
        self.usuario_atual = usuario(agente_id, perfil="Agente")
        try:
            return self.client.get(f"/agente/missao/{PESQUISA}")
        finally:
            self.usuario_atual = usuario()

    def coleta(self, setor_id, pergunta_ids, *, territorial):
        self.usuario_atual = usuario(2, perfil="Agente")
        try:
            payload = {
                "client_uuid": str(uuid4()),
                "setor_id": setor_id,
                "data_inicio_coleta": datetime.now(timezone.utc).isoformat(),
                "respostas": [{"pergunta_id": pid, "valor_resposta": "x"} for pid in pergunta_ids],
            }
            if territorial:
                payload["questionario_territorial"] = True
            return self.client.post(f"/pesquisas/{PESQUISA}/coletas/", json=payload)
        finally:
            self.usuario_atual = usuario()

    # --- Gate F.0 / migration / backfill -----------------------------------

    def test_perguntas_nascem_GLOBAL_e_o_contrato_traz_aplicabilidade(self):
        resposta = self.pergunta("Legada", 1)
        self.assertEqual(resposta.status_code, 200, resposta.text)
        corpo = resposta.json()
        self.assertEqual(corpo["aplicabilidade"], "GLOBAL")
        self.assertEqual(corpo["municipio_ids"], [])
        self.assertEqual(corpo["municipios"], [])

    def test_pergunta_sem_o_campo_continua_GLOBAL(self):
        # Cliente anterior a F nao envia aplicabilidade.
        resposta = self.client.post(
            f"/pesquisas/{PESQUISA}/perguntas/",
            json={"texto_pergunta": "Antiga", "tipo_pergunta": "TEXTO", "ordem": 1},
        )
        self.assertEqual(resposta.json()["aplicabilidade"], "GLOBAL")
        linha = self.db.execute(text("SELECT aplicabilidade FROM perguntas")).scalar()
        self.assertEqual(linha, "GLOBAL")

    def test_criar_TERRITORIAL_com_municipio(self):
        self.cenario_dois_municipios()
        resposta = self.pergunta("Prefeito", 1, "TERRITORIAL", [MACAPA])
        self.assertEqual(resposta.status_code, 200, resposta.text)
        corpo = resposta.json()
        self.assertEqual(corpo["aplicabilidade"], "TERRITORIAL")
        self.assertEqual(corpo["municipio_ids"], [MACAPA])
        self.assertEqual(corpo["municipios"], [{"id": MACAPA, "nome": "Macapa"}])
        # Listagem tambem traz, e em uma consulta so.
        lista = self.client.get(f"/pesquisas/{PESQUISA}/perguntas/").json()
        self.assertEqual(lista[0]["municipio_ids"], [MACAPA])

    # --- invariantes -------------------------------------------------------

    def test_GLOBAL_com_municipio_e_recusado(self):
        self.cenario_dois_municipios()
        resposta = self.pergunta("X", 1, "GLOBAL", [MACAPA])
        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(self.db.execute(text("SELECT COUNT(*) FROM perguntas")).scalar(), 0)

    def test_TERRITORIAL_vazio_e_recusado(self):
        self.cenario_dois_municipios()
        self.assertEqual(self.pergunta("X", 1, "TERRITORIAL", []).status_code, 422)
        self.assertEqual(self.pergunta("X", 1, "TERRITORIAL", None).status_code, 422)
        self.assertEqual(self.db.execute(text("SELECT COUNT(*) FROM perguntas")).scalar(), 0)

    def test_BAIRRO_nao_serve_como_municipio(self):
        self.cenario_dois_municipios()
        resposta = self.pergunta("X", 1, "TERRITORIAL", [101])
        self.assertEqual(resposta.status_code, 404)

    def test_municipio_de_outra_base_e_rejeitado(self):
        self.cenario_dois_municipios()
        self.criar_base(base_id=2, projeto_id=200, company_id=20, versao="2")
        outro = self.criar_municipio(950, "Outro", base_id=2)
        self.assertEqual(self.pergunta("X", 1, "TERRITORIAL", [outro]).status_code, 404)

    def test_tenant_cruzado_nao_associa_nem_revela(self):
        self.cenario_dois_municipios()
        # Gerente da empresa B tenta usar Macapa (base da empresa A) numa pergunta sua.
        self.usuario_atual = usuario(3, company_id=20)
        self.criar_base(base_id=2, projeto_id=200, company_id=20, versao="2")
        resposta = self.pergunta("X", 1, "TERRITORIAL", [MACAPA], pesquisa_id=2000)
        self.assertEqual(resposta.status_code, 404)
        # E nao enxerga a pesquisa A de jeito nenhum.
        self.assertEqual(self.pergunta("X", 1, pesquisa_id=PESQUISA).status_code, 404)

    def test_sem_base_principal_nao_permite_TERRITORIAL(self):
        resposta = self.pergunta("X", 1, "TERRITORIAL", [MACAPA])
        self.assertEqual(resposta.status_code, 422)

    # --- PATCH -------------------------------------------------------------

    def test_patch_sem_campo_territorial_nao_mexe_na_associacao(self):
        self.cenario_dois_municipios()
        pid = self.pergunta("P", 1, "TERRITORIAL", [MACAPA]).json()["id"]
        resposta = self.patch(pid, {"texto_pergunta": "Renomeada"})
        self.assertEqual(resposta.json()["aplicabilidade"], "TERRITORIAL")
        self.assertEqual(resposta.json()["municipio_ids"], [MACAPA])

    def test_patch_TERRITORIAL_para_GLOBAL_remove_associacoes(self):
        self.cenario_dois_municipios()
        pid = self.pergunta("P", 1, "TERRITORIAL", [MACAPA, SANTANA]).json()["id"]
        resposta = self.patch(pid, {"aplicabilidade": "GLOBAL"})
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["municipio_ids"], [])
        self.assertEqual(
            self.db.execute(text("SELECT COUNT(*) FROM pergunta_territorio_eleitoral")).scalar(), 0,
            "nenhuma associacao pode sobrar escondida atras de uma GLOBAL",
        )

    def test_patch_GLOBAL_para_TERRITORIAL_exige_municipio(self):
        self.cenario_dois_municipios()
        pid = self.pergunta("P", 1).json()["id"]
        self.assertEqual(self.patch(pid, {"aplicabilidade": "TERRITORIAL"}).status_code, 422)
        ok = self.patch(pid, {"aplicabilidade": "TERRITORIAL", "municipio_ids": [SANTANA]})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["municipio_ids"], [SANTANA])

    def test_patch_lista_vazia_explicita_em_TERRITORIAL_e_recusada(self):
        self.cenario_dois_municipios()
        pid = self.pergunta("P", 1, "TERRITORIAL", [MACAPA]).json()["id"]
        self.assertEqual(self.patch(pid, {"municipio_ids": []}).status_code, 422)
        # Nada mudou.
        self.assertEqual(
            self.db.execute(text("SELECT COUNT(*) FROM pergunta_territorio_eleitoral")).scalar(), 1
        )

    # --- resolucao municipal do setor -------------------------------------

    def test_setor_com_varios_bairros_do_mesmo_municipio_resolve(self):
        self.cenario_dois_municipios()
        r = svc.resolver_municipio_setor(self.db, 500)
        self.assertEqual(r.status, svc.STATUS_RESOLVIDO)
        self.assertEqual(r.municipio.nome, "Macapa")

    def test_setor_sem_composicao_e_SEM_MUNICIPIO(self):
        self.cenario_dois_municipios()
        self.criar_setor(502, "Livre", [])
        self.assertEqual(svc.resolver_municipio_setor(self.db, 502).status, svc.STATUS_SEM_MUNICIPIO)

    def test_composicao_cujos_bairros_nao_resolvem_municipio_e_SEM_MUNICIPIO(self):
        """Achado da E.4: bairros confirmados, relacao municipal ausente."""
        self.cenario_dois_municipios()
        self.criar_bairro(103, "Orfao", MACAPA, municipio_resolvivel=False)
        self.criar_setor(503, "Orfao", [103])
        r = svc.resolver_municipio_setor(self.db, 503)
        self.assertEqual(r.status, svc.STATUS_SEM_MUNICIPIO)
        self.assertIsNone(r.municipio)

    def test_setor_multi_municipio_e_AMBIGUO_sem_escolha(self):
        self.cenario_dois_municipios()
        self.criar_setor(504, "Divisa", [101, 201])
        r = svc.resolver_municipio_setor(self.db, 504)
        self.assertEqual(r.status, svc.STATUS_AMBIGUO)
        self.assertIsNone(r.municipio)
        self.assertEqual(sorted(m.nome for m in r.municipios_encontrados), ["Macapa", "Santana"])

    # --- perguntas aplicaveis ---------------------------------------------

    def test_GLOBAL_aparece_em_todos_os_setores(self):
        self.cenario_dois_municipios()
        self.criar_setor(502, "Livre", [])
        p1 = self.pergunta("G", 1).json()["id"]
        aplicaveis = svc.perguntas_aplicaveis_por_setor(self.db, PESQUISA, [500, 501, 502])
        self.assertEqual(aplicaveis, {500: [p1], 501: [p1], 502: [p1]})

    def test_municipal_so_no_municipio_certo_e_na_ordem(self):
        self.cenario_dois_municipios()
        ids = self.questionario_padrao()
        aplicaveis = svc.perguntas_aplicaveis_por_setor(self.db, PESQUISA, [500, 501])
        self.assertEqual(aplicaveis[500], [ids["P01"], ids["P10"], ids["P20"]])
        self.assertEqual(aplicaveis[501], [ids["P01"], ids["P11"], ids["P20"]])

    def test_pergunta_em_dois_municipios_aparece_nos_dois(self):
        self.cenario_dois_municipios()
        p20 = self.pergunta("Ambos", 1, "TERRITORIAL", [MACAPA, SANTANA]).json()["id"]
        aplicaveis = svc.perguntas_aplicaveis_por_setor(self.db, PESQUISA, [500, 501])
        self.assertEqual(aplicaveis[500], [p20])
        self.assertEqual(aplicaveis[501], [p20])

    def test_varios_bairros_nao_duplicam_a_pergunta(self):
        self.cenario_dois_municipios()
        p10 = self.pergunta("Macapa", 1, "TERRITORIAL", [MACAPA]).json()["id"]
        self.assertEqual(svc.obter_perguntas_aplicaveis_setor(self.db, PESQUISA, 500), [p10])

    def test_AMBIGUO_e_SEM_MUNICIPIO_recebem_somente_GLOBAL(self):
        self.cenario_dois_municipios()
        self.criar_setor(502, "Livre", [])
        self.criar_setor(504, "Divisa", [101, 201])
        ids = self.questionario_padrao()
        aplicaveis = svc.perguntas_aplicaveis_por_setor(self.db, PESQUISA, [502, 504])
        self.assertEqual(aplicaveis[502], [ids["P01"], ids["P20"]])
        self.assertEqual(aplicaveis[504], [ids["P01"], ids["P20"]])

    # --- missao -------------------------------------------------------------

    def test_missao_entrega_status_municipio_e_ids_por_setor(self):
        self.cenario_dois_municipios()
        self.criar_setor(502, "Livre", [])
        self.criar_setor(504, "Divisa", [101, 201])
        ids = self.questionario_padrao()

        corpo = self.missao().json()
        self.assertTrue(corpo["possui_perguntas_territoriais"])
        por_id = {s["id"]: s for s in corpo["setores"]}

        self.assertEqual(por_id[500]["territorio_status"], "RESOLVIDO")
        self.assertEqual(por_id[500]["municipio"], {"id": MACAPA, "nome": "Macapa"})
        self.assertEqual(por_id[500]["pergunta_ids_aplicaveis"], [ids["P01"], ids["P10"], ids["P20"]])

        self.assertEqual(por_id[501]["municipio"]["nome"], "Santana")
        self.assertEqual(por_id[501]["pergunta_ids_aplicaveis"], [ids["P01"], ids["P11"], ids["P20"]])

        self.assertEqual(por_id[502]["territorio_status"], "SEM_MUNICIPIO")
        self.assertIsNone(por_id[502]["municipio"])
        self.assertEqual(por_id[502]["pergunta_ids_aplicaveis"], [ids["P01"], ids["P20"]])

        self.assertEqual(por_id[504]["territorio_status"], "AMBIGUO")
        self.assertIsNone(por_id[504]["municipio"])
        # Definicao completa da pergunta NAO viaja por setor.
        self.assertNotIn("perguntas", por_id[500])

    def test_missao_sem_perguntas_territoriais_sinaliza_false(self):
        self.cenario_dois_municipios()
        self.pergunta("G", 1)
        self.assertFalse(self.missao().json()["possui_perguntas_territoriais"])

    # --- validacao das respostas -------------------------------------------

    def test_cliente_F_nao_responde_pergunta_de_outro_municipio(self):
        self.cenario_dois_municipios()
        ids = self.questionario_padrao()
        resposta = self.coleta(500, [ids["P01"], ids["P11"]], territorial=True)
        self.assertEqual(resposta.status_code, 422, resposta.text)
        self.assertIn(str(ids["P11"]), resposta.json()["detail"])
        # Transacional: nada persistiu.
        self.assertEqual(self.db.execute(text("SELECT COUNT(*) FROM coletas")).scalar(), 0)
        self.assertEqual(self.db.execute(text("SELECT COUNT(*) FROM respostas")).scalar(), 0)

    def test_cliente_F_com_respostas_aplicaveis_e_aceito(self):
        self.cenario_dois_municipios()
        ids = self.questionario_padrao()
        resposta = self.coleta(500, [ids["P01"], ids["P10"], ids["P20"]], territorial=True)
        self.assertEqual(resposta.status_code, 201, resposta.text)
        self.assertEqual(self.db.execute(text("SELECT COUNT(*) FROM respostas")).scalar(), 3)

    def test_cliente_F_sem_setor_so_responde_GLOBAL(self):
        self.cenario_dois_municipios()
        ids = self.questionario_padrao()
        self.assertEqual(self.coleta(None, [ids["P10"]], territorial=True).status_code, 422)
        self.assertEqual(self.coleta(None, [ids["P01"]], territorial=True).status_code, 201)

    def test_cliente_legado_continua_aceito_na_janela_de_transicao(self):
        """Sem o marcador, a coleta segue a politica anterior -- documentado."""
        self.cenario_dois_municipios()
        ids = self.questionario_padrao()
        resposta = self.coleta(500, [ids["P01"], ids["P11"]], territorial=False)
        self.assertEqual(resposta.status_code, 201, resposta.text)

    # --- helper de configuracao ---------------------------------------------

    def test_validar_configuracao_dedup_e_invariantes(self):
        self.assertEqual(svc.validar_configuracao("TERRITORIAL", [MACAPA, MACAPA]), [MACAPA])
        self.assertEqual(svc.validar_configuracao("GLOBAL", None), [])
        with self.assertRaises(Exception):
            svc.validar_configuracao("OUTRA", [])


if __name__ == "__main__":
    unittest.main()
