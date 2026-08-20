"""Contrato de leitura do Workspace da Base Eleitoral (Fase 3D-A).

Cobre a rota Projeto -> Base, os agregados do detalhe e a paginacao com total.
Nenhum destes testes escreve no banco alem da propria fixture.
"""

import os
import shutil
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-base-eleitoral-read-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import base_eleitoral as rotas
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from pesquisa360.services import base_eleitoral as service
from pesquisa360.services import base_eleitoral_import as core

from tests.test_base_eleitoral_import import run_alembic_upgrade

# Valores do DEV real usados apenas como fixture; nenhum deles vive no codigo
# de producao.
OPERACIONAL = 577894
DECLARADO = 578157
HASH_FIXTURE = "7a363d763d9aaec1e791a606a32278e3bcff2499add98c784ff56bef233439fa"


class _LeituraFixture(unittest.TestCase):
    """Dois tenants; base privada A com arvore ESTADO/MUNICIPIO/BAIRRO."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="pesquisa360-read-"))
        cls.db_path = cls.temp_dir / "read.db"
        run_alembic_upgrade(f"sqlite:///{cls.db_path.as_posix()}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self):
        self.engine = create_engine(f"sqlite:///{self.db_path.as_posix()}")

        @event.listens_for(self.engine, "connect")
        def _preparar(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
            dbapi_connection.create_function("GeomFromEWKT", 1, lambda valor: valor)
            dbapi_connection.create_function("ST_GeomFromEWKT", 1, lambda valor: valor)
            dbapi_connection.create_function("AsEWKB", 1, lambda valor: valor)

        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        self._limpar()
        self._semear()

    def _limpar(self):
        for tabela in (
            "importacao_base_eleitoral",
            "projeto_base_eleitoral",
            "territorio_eleitoral",
            "base_eleitoral",
            "projetos",
            "usuarios",
            "companies",
            "perfis",
        ):
            self.session.execute(text(f"DELETE FROM {tabela}"))
        self.session.commit()

    def _semear(self):
        self.session.execute(
            text("INSERT INTO perfis (id, nome) VALUES (1, 'Superadmin'), (2, 'Gerente')")
        )
        self.session.execute(
            text(
                "INSERT INTO companies (id, name, is_active) VALUES"
                " (10, 'Empresa A', 1), (20, 'Empresa B', 1)"
            )
        )
        self.session.execute(
            text(
                "INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id)"
                " VALUES (1, 'a@a', 'Gerente A', 'x', 1, 2, 10),"
                " (2, 'b@b', 'Gerente B', 'x', 1, 2, 20)"
            )
        )
        self.session.execute(
            text(
                "INSERT INTO projetos (id, nome, status, data_inicio, coordenador_id, company_id)"
                " VALUES (100, 'Campanha A', 'Ativo', '2026-01-01', 1, 10),"
                " (101, 'Sem base', 'Ativo', '2026-01-01', 1, 10),"
                " (200, 'Campanha B', 'Ativo', '2026-01-01', 2, 20)"
            )
        )
        self.session.commit()

        self.usuario_a = self.session.get(models.Usuario, 1)
        self.usuario_b = self.session.get(models.Usuario, 2)

        self.privada_a = self._criar_base(company_id=10, nome="Privada A", versao="pa-1")
        self.privada_b = self._criar_base(company_id=20, nome="Privada B", versao="pb-1")
        self.importacao = self._importar(self.privada_a)
        self._importar(self.privada_b, conteudo=b"lote-b", prefixo="B")

        service.vincular_base_eleitoral_ao_projeto(
            self.session, 100, self.privada_a.id, self.usuario_a, principal=True
        )
        service.vincular_base_eleitoral_ao_projeto(
            self.session, 200, self.privada_b.id, self.usuario_b, principal=True
        )

    def _criar_base(self, **overrides):
        dados = dict(
            nome="Base",
            ano=2026,
            uf="AP",
            fonte="GSPC",
            versao="v1",
            data_referencia=date(2026, 1, 1),
            criado_por_id=1,
        )
        dados.update(overrides)
        base = models.BaseEleitoral(**dados)
        self.session.add(base)
        self.session.commit()
        return base

    def _importar(self, base, conteudo=b"lote-a", prefixo="A"):
        """ESTADO + 2 municipios + 100 bairros, para exercitar a paginacao."""
        auditoria = {
            "data_referencia_origem": "CONVENCAO",
            "data_referencia_convencional": True,
            "data_fonte_declarada": None,
            "motivo": "A fonte informa apenas o ano eleitoral e nao declara data de corte.",
            "pdf_creation_date": "2026-05-20T15:49:55",
        }
        registros = [
            core.RegistroTerritorioImportacao(
                tipo="ESTADO",
                nome="Amapá",
                chave="e1",
                eleitorado_apto=DECLARADO,
                metadados={"data_referencia_auditoria": auditoria},
            ),
            core.RegistroTerritorioImportacao(
                tipo="MUNICIPIO", nome="Macapá", chave="m1", parent_chave="e1",
                eleitorado_apto=OPERACIONAL - 1000,
            ),
            core.RegistroTerritorioImportacao(
                tipo="MUNICIPIO", nome="Santana", chave="m2", parent_chave="e1",
                eleitorado_apto=1000,
            ),
        ]
        # 60 bairros em Macapa e 40 em Santana = 100 no total.
        for indice in range(1, 101):
            municipio = "m1" if indice <= 60 else "m2"
            registros.append(
                core.RegistroTerritorioImportacao(
                    tipo="BAIRRO",
                    nome="{} Bairro {:03d}".format(prefixo, indice),
                    chave="b{}".format(indice),
                    parent_chave=municipio,
                    municipio_chave=municipio,
                    eleitorado_apto=indice,
                )
            )
        importacao = core.importar_registros(
            self.session,
            base_eleitoral=base,
            registros=registros,
            arquivo_origem="Eleitorado Apto (Amapá 2026).pdf",
            executado_por_id=1,
            hash_arquivo=HASH_FIXTURE if conteudo == b"lote-a" else None,
            conteudo_arquivo=None if conteudo == b"lote-a" else conteudo,
        )
        # O ESTADO fica divergente (declarado != soma): ajusta para espelhar o
        # DEV, onde a resolucao humana ja ocorreu.
        raiz = service.obter_raiz_estado(self.session, base.id)
        raiz.eleitorado_apto = OPERACIONAL
        raiz.eleitorado_apto_divergente = False
        raiz.status_validacao = "IMPORTADA"
        self.session.add(raiz)
        self.session.commit()
        return importacao

    def _municipio(self, base, nome):
        return (
            self.session.query(models.TerritorioEleitoral)
            .filter(
                models.TerritorioEleitoral.base_eleitoral_id == base.id,
                models.TerritorioEleitoral.tipo == "MUNICIPIO",
                models.TerritorioEleitoral.nome == nome,
            )
            .one()
        )

    def _cliente(self, usuario):
        app = FastAPI()
        app.include_router(rotas.router)

        def _db():
            yield self.session

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = lambda: usuario
        return TestClient(app)


class ProjetoBaseEleitoralTests(_LeituraFixture):
    def test_projeto_com_base_principal(self):
        resposta = self._cliente(self.usuario_a).get("/projetos/100/base-eleitoral")
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo["projeto_id"], 100)
        self.assertTrue(corpo["principal"])
        self.assertEqual(corpo["base"]["id"], self.privada_a.id)
        self.assertEqual(corpo["base"]["versao"], "pa-1")

    def test_projeto_sem_vinculo_responde_200_com_base_nula(self):
        # Ausencia de vinculo e estado funcional normal, nao 404.
        resposta = self._cliente(self.usuario_a).get("/projetos/101/base-eleitoral")
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo["projeto_id"], 101)
        self.assertFalse(corpo["principal"])
        self.assertIsNone(corpo["base"])

    def test_projeto_inexistente_retorna_404(self):
        resposta = self._cliente(self.usuario_a).get("/projetos/999999/base-eleitoral")
        self.assertEqual(resposta.status_code, 404)

    def test_projeto_de_outro_tenant_retorna_404(self):
        resposta = self._cliente(self.usuario_a).get("/projetos/200/base-eleitoral")
        self.assertEqual(resposta.status_code, 404)
        self.assertNotEqual(resposta.status_code, 403)

    def test_nao_vaza_base_privada_do_outro_tenant(self):
        corpo = self._cliente(self.usuario_b).get("/projetos/200/base-eleitoral").json()
        self.assertEqual(corpo["base"]["id"], self.privada_b.id)
        self.assertNotEqual(corpo["base"]["id"], self.privada_a.id)
        self.assertNotIn("company_id", corpo["base"])

    def test_rota_ignora_company_id_no_request(self):
        resposta = self._cliente(self.usuario_a).get(
            "/projetos/100/base-eleitoral", params={"company_id": 20}
        )
        self.assertEqual(resposta.status_code, 200)
        # O tenant vem do JWT: o parametro nao muda nada.
        self.assertEqual(resposta.json()["base"]["id"], self.privada_a.id)

    def test_helper_de_calculo_permanece_estrito(self):
        # Base ainda nao validada: a porta de calculo continua recusando.
        with self.assertRaises(service.BaseEleitoralNaoValidadaError):
            service.obter_base_principal_projeto_para_calculo(
                self.session, 100, self.usuario_a
            )
        # E o helper opcional nao inventa base para projeto sem vinculo.
        self.assertIsNone(
            service.obter_base_principal_projeto_opcional(self.session, 101, self.usuario_a)
        )


class DetalheBaseTests(_LeituraFixture):
    def _detalhe(self, usuario=None):
        cliente = self._cliente(usuario or self.usuario_a)
        return cliente.get(f"/base-eleitoral/{self.privada_a.id}").json()

    def test_totais_por_tipo(self):
        totais = self._detalhe()["totais_por_tipo"]
        self.assertEqual(totais["ESTADO"], 1)
        self.assertEqual(totais["MUNICIPIO"], 2)
        self.assertEqual(totais["BAIRRO"], 100)
        self.assertEqual(totais["LOCALIDADE"], 0)
        self.assertEqual(totais["LOCAL_VOTACAO"], 0)
        self.assertEqual(totais["SECAO"], 0)

    def test_eleitorado_operacional_e_declarado(self):
        corpo = self._detalhe()
        self.assertEqual(corpo["eleitorado_operacional"], OPERACIONAL)
        self.assertEqual(corpo["eleitorado_declarado"], DECLARADO)
        self.assertEqual(corpo["diferenca_eleitorado"], DECLARADO - OPERACIONAL)

    def test_importacao_origem_com_hash_completo(self):
        corpo = self._detalhe()
        self.assertEqual(corpo["total_importacoes"], 1)
        origem = corpo["importacao_origem"]
        self.assertEqual(origem["arquivo_origem"], "Eleitorado Apto (Amapá 2026).pdf")
        # Backend devolve o hash inteiro; abreviar e papel da UI.
        self.assertEqual(origem["hash_arquivo"], HASH_FIXTURE)
        self.assertEqual(len(origem["hash_arquivo"]), 64)

    def test_auditoria_da_data_de_referencia(self):
        auditoria = self._detalhe()["auditoria_data_referencia"]
        self.assertEqual(auditoria["origem"], "CONVENCAO")
        self.assertTrue(auditoria["convencional"])
        self.assertIsNone(auditoria["data_fonte_declarada"])
        self.assertIn("nao declara data de corte", auditoria["motivo"])
        self.assertEqual(auditoria["pdf_creation_date"], "2026-05-20T15:49:55")

    def test_auditoria_nao_e_deduzida_da_data(self):
        # Base sem bloco persistido nao ganha auditoria so por ser 01/01.
        outra = self._criar_base(company_id=10, nome="Sem auditoria", versao="sem-aud")
        corpo = self._cliente(self.usuario_a).get(f"/base-eleitoral/{outra.id}").json()
        self.assertEqual(corpo["data_referencia"], "2026-01-01")
        self.assertIsNone(corpo["auditoria_data_referencia"])

    def test_detalhe_nao_expoe_company_id_nem_metadados_crus(self):
        corpo = self._detalhe()
        self.assertNotIn("company_id", corpo)
        self.assertNotIn("metadados", corpo)
        self.assertIn("eh_oficial", corpo)
        self.assertFalse(corpo["eh_oficial"])

    def test_detalhe_de_outro_tenant_retorna_404(self):
        resposta = self._cliente(self.usuario_a).get(f"/base-eleitoral/{self.privada_b.id}")
        self.assertEqual(resposta.status_code, 404)

    def test_importacoes_listadas_em_rota_propria(self):
        resposta = self._cliente(self.usuario_a).get(
            f"/base-eleitoral/{self.privada_a.id}/importacoes"
        )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(len(corpo), 1)
        self.assertEqual(corpo[0]["hash_arquivo"], HASH_FIXTURE)
        self.assertEqual(corpo[0]["total_importadas"], 103)

    def test_importacoes_de_outro_tenant_retorna_404(self):
        resposta = self._cliente(self.usuario_a).get(
            f"/base-eleitoral/{self.privada_b.id}/importacoes"
        )
        self.assertEqual(resposta.status_code, 404)

    def test_varias_importacoes_nao_elegem_origem_arbitraria(self):
        # Sem unicidade garantida, o detalhe deixa de escolher uma origem.
        self.session.add(
            models.ImportacaoBaseEleitoral(
                base_eleitoral_id=self.privada_a.id,
                arquivo_origem="segundo-lote.csv",
                hash_arquivo=None,
                total_linhas=1,
                total_importadas=1,
                total_divergencias=0,
                executado_por_id=1,
                executado_em=datetime.now(timezone.utc),
            )
        )
        self.session.commit()
        corpo = self._detalhe()
        self.assertEqual(corpo["total_importacoes"], 2)
        self.assertIsNone(corpo["importacao_origem"])
        lista = self._cliente(self.usuario_a).get(
            f"/base-eleitoral/{self.privada_a.id}/importacoes"
        ).json()
        self.assertEqual(len(lista), 2)


class PaginacaoTerritoriosTests(_LeituraFixture):
    def _pagina(self, **params):
        return self._cliente(self.usuario_a).get(
            f"/base-eleitoral/{self.privada_a.id}/territorios", params=params
        ).json()

    def test_envelope_com_total_limit_offset(self):
        corpo = self._pagina(tipo="BAIRRO", limit=25, offset=0)
        self.assertEqual(len(corpo["items"]), 25)
        self.assertEqual(corpo["total"], 100)
        self.assertEqual(corpo["limit"], 25)
        self.assertEqual(corpo["offset"], 0)

    def test_ultima_pagina(self):
        corpo = self._pagina(tipo="BAIRRO", limit=25, offset=75)
        self.assertEqual(len(corpo["items"]), 25)
        self.assertEqual(corpo["total"], 100)
        self.assertEqual(corpo["offset"], 75)

    def test_offset_alem_do_total_devolve_lista_vazia_com_total(self):
        corpo = self._pagina(tipo="BAIRRO", limit=25, offset=200)
        self.assertEqual(corpo["items"], [])
        self.assertEqual(corpo["total"], 100)

    def test_paginas_nao_se_sobrepoem(self):
        primeira = self._pagina(tipo="BAIRRO", limit=25, offset=0)["items"]
        segunda = self._pagina(tipo="BAIRRO", limit=25, offset=25)["items"]
        ids_primeira = {item["id"] for item in primeira}
        ids_segunda = {item["id"] for item in segunda}
        self.assertEqual(ids_primeira & ids_segunda, set())

    def test_total_respeita_filtro_de_municipio(self):
        macapa = self._municipio(self.privada_a, "Macapá")
        corpo = self._pagina(tipo="BAIRRO", municipio_id=macapa.id, limit=10)
        self.assertEqual(corpo["total"], 60)
        self.assertEqual(len(corpo["items"]), 10)

        santana = self._municipio(self.privada_a, "Santana")
        corpo = self._pagina(tipo="BAIRRO", municipio_id=santana.id, limit=10)
        self.assertEqual(corpo["total"], 40)

    def test_total_respeita_busca_textual(self):
        # "A Bairro 01x" -> 010..019 mais 001..009 nao casam; usa prefixo exato.
        corpo = self._pagina(tipo="BAIRRO", q="A Bairro 01", limit=100)
        nomes = [item["nome"] for item in corpo["items"]]
        self.assertEqual(corpo["total"], len(nomes))
        self.assertTrue(all("bairro 01" in n.lower() for n in nomes))
        self.assertGreater(corpo["total"], 0)
        self.assertLess(corpo["total"], 100)

    def test_total_combina_filtros(self):
        macapa = self._municipio(self.privada_a, "Macapá")
        corpo = self._pagina(tipo="BAIRRO", municipio_id=macapa.id, q="A Bairro 05", limit=100)
        self.assertEqual(corpo["total"], len(corpo["items"]))
        for item in corpo["items"]:
            self.assertEqual(item["municipio_id"], macapa.id)

    def test_total_por_tipo_nao_e_o_total_global(self):
        bairros = self._pagina(tipo="BAIRRO", limit=1)
        municipios = self._pagina(tipo="MUNICIPIO", limit=1)
        estado = self._pagina(tipo="ESTADO", limit=1)
        self.assertEqual(bairros["total"], 100)
        self.assertEqual(municipios["total"], 2)
        self.assertEqual(estado["total"], 1)
        sem_filtro = self._pagina(limit=1)
        self.assertEqual(sem_filtro["total"], 103)

    def test_itens_nao_expoem_geometria_nem_company_id(self):
        corpo = self._pagina(tipo="BAIRRO", limit=5)
        for item in corpo["items"]:
            with self.subTest(nome=item["nome"]):
                self.assertNotIn("geometria", item)
                self.assertNotIn("company_id", item)
                self.assertNotIn("metadados", item)
                self.assertIn("possui_geometria", item)

    def test_territorios_de_outro_tenant_retorna_404(self):
        resposta = self._cliente(self.usuario_a).get(
            f"/base-eleitoral/{self.privada_b.id}/territorios"
        )
        self.assertEqual(resposta.status_code, 404)

    def test_contagem_por_tipo_vem_do_banco(self):
        totais = service.contar_territorios_por_tipo(self.session, self.privada_a.id)
        self.assertEqual(totais["BAIRRO"], 100)
        self.assertEqual(totais["MUNICIPIO"], 2)
        self.assertEqual(totais["SECAO"], 0)


if __name__ == "__main__":
    unittest.main()
