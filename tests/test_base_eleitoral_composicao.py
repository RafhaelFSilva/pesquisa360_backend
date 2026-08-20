"""Composicao do valor operacional final (read model da Auditoria).

A explicacao de COMO o valor operacional foi formado e calculada a partir da
arvore, nunca por nome de territorio. Estes testes usam uma fixture generica
justamente para provar que a regra nao depende dos dados do DEV.

Nenhum teste aqui altera regra de negocio: a composicao e derivada, nao
persistida.
"""

import os
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-base-eleitoral-composicao-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import base_eleitoral as rotas
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from pesquisa360.services import base_eleitoral as service
from pesquisa360.services import base_eleitoral_import as core

from tests.test_base_eleitoral_import import run_alembic_upgrade


class ComposicaoValorFinalTests(unittest.TestCase):
    """Arvore de duas camadas divergentes.

    ESTADO  declara 1000, filhos somam 950
      MUNICIPIO A declara 500, bairros somam 480
      MUNICIPIO B declara 450, bairros somam 440

    Resolvidos os municipios (480 e 440), a soma dos filhos do estado passa a
    920. O estado adota 920.
    """

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="pesquisa360-composicao-"))
        cls.db_path = cls.temp_dir / "composicao.db"
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
        self.session.execute(text("INSERT INTO perfis (id, nome) VALUES (2, 'Gerente')"))
        self.session.execute(
            text("INSERT INTO companies (id, name, is_active) VALUES (10, 'Empresa A', 1)")
        )
        self.session.execute(
            text(
                "INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id)"
                " VALUES (1, 'a@a', 'Gerente A', 'x', 1, 2, 10)"
            )
        )
        self.session.commit()
        self.usuario = self.session.get(models.Usuario, 1)

        self.base = models.BaseEleitoral(
            nome="Base Composicao",
            ano=2026,
            uf="ZZ",
            fonte="FIXTURE",
            versao="comp-1",
            data_referencia=date(2026, 1, 1),
            criado_por_id=1,
            company_id=10,
        )
        self.session.add(self.base)
        self.session.commit()

        registros = [
            core.RegistroTerritorioImportacao(
                tipo="ESTADO", nome="Estado Alfa", chave="e1", eleitorado_apto=1000
            ),
            core.RegistroTerritorioImportacao(
                tipo="MUNICIPIO",
                nome="Municipio A",
                chave="m1",
                parent_chave="e1",
                eleitorado_apto=500,
            ),
            core.RegistroTerritorioImportacao(
                tipo="MUNICIPIO",
                nome="Municipio B",
                chave="m2",
                parent_chave="e1",
                eleitorado_apto=450,
            ),
        ]
        # Bairros de A somam 480; bairros de B somam 440.
        for chave_pai, valores in (("m1", (300, 180)), ("m2", (250, 190))):
            for indice, valor in enumerate(valores, start=1):
                registros.append(
                    core.RegistroTerritorioImportacao(
                        tipo="BAIRRO",
                        nome=f"Bairro {chave_pai}-{indice}",
                        chave=f"b{chave_pai}{indice}",
                        parent_chave=chave_pai,
                        municipio_chave=chave_pai,
                        eleitorado_apto=valor,
                    )
                )

        core.importar_registros(
            self.session,
            base_eleitoral=self.base,
            registros=registros,
            arquivo_origem="fixture.csv",
            executado_por_id=1,
            conteudo_arquivo=b"lote-composicao",
        )
        self.session.commit()

    def _territorio(self, nome):
        return (
            self.session.query(models.TerritorioEleitoral)
            .filter(
                models.TerritorioEleitoral.base_eleitoral_id == self.base.id,
                models.TerritorioEleitoral.nome == nome,
            )
            .one()
        )

    def _divergencias(self):
        return {
            item["territorio"]: item
            for item in service.listar_divergencias(self.session, self.base.id, self.usuario)
        }

    def _resolver(self, nome, valor_final):
        service.resolver_divergencia_territorio(
            self.session,
            territorio_id=self._territorio(nome).id,
            valor_final=valor_final,
            justificativa="Resolucao de fixture.",
            current_user=self.usuario,
        )
        self.session.commit()

    def _resolver_municipios(self):
        self._resolver("Municipio A", 480)
        self._resolver("Municipio B", 440)

    def _cliente(self):
        app = FastAPI()
        app.include_router(rotas.router)

        def _db():
            yield self.session

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = lambda: self.usuario
        return TestClient(app)

    # -- estrutura da conferencia ---------------------------------------------

    def test_conferencia_registra_as_tres_divergencias(self):
        divergencias = self._divergencias()
        self.assertEqual(
            {"Estado Alfa", "Municipio A", "Municipio B"}, set(divergencias)
        )
        estado = divergencias["Estado Alfa"]
        self.assertEqual(estado["valor_resumo"], 1000)
        self.assertEqual(estado["valor_detalhe"], 950)
        self.assertEqual(estado["diferenca"], 50)

    def test_divergencia_carrega_o_id_do_territorio(self):
        # Sem o id, a UI so poderia casar por nome -- que repete entre municipios.
        divergencias = self._divergencias()
        self.assertEqual(
            divergencias["Municipio A"]["territorio_id"], self._territorio("Municipio A").id
        )

    # -- caso simples: municipio ----------------------------------------------

    def test_municipio_sem_ajuste_interno_nao_lista_ajustes(self):
        self._resolver_municipios()
        composicao = self._divergencias()["Municipio A"]["composicao_valor_final"]
        self.assertEqual(composicao["soma_filhos_original"], 480)
        self.assertEqual(composicao["soma_filhos_atual"], 480)
        self.assertEqual(composicao["ajuste_total_filhos"], 0)
        self.assertEqual(composicao["valor_operacional_final"], 480)
        self.assertEqual(composicao["diferenca_final_fonte"], 20)
        # Bairros nao foram tocados: a secao de ajustes fica vazia.
        self.assertEqual(composicao["ajustes_filhos"], [])
        self.assertEqual(composicao["total_filhos"], 2)

    # -- caso composto: estado -------------------------------------------------

    def test_estado_soma_os_ajustes_dos_filhos_sobre_a_soma_original(self):
        self._resolver_municipios()
        self._resolver("Estado Alfa", 920)

        composicao = self._divergencias()["Estado Alfa"]["composicao_valor_final"]
        self.assertEqual(composicao["soma_filhos_original"], 950)
        self.assertEqual(composicao["soma_filhos_atual"], 920)
        self.assertEqual(composicao["ajuste_total_filhos"], -30)
        self.assertEqual(composicao["valor_operacional_final"], 920)
        self.assertEqual(composicao["diferenca_final_fonte"], 80)

        ajustes = composicao["ajustes_filhos"]
        self.assertEqual(
            [(a["territorio"], a["valor_anterior"], a["valor_final"], a["ajuste"]) for a in ajustes],
            [("Municipio A", 500, 480, -20), ("Municipio B", 450, 440, -10)],
        )

    def test_a_ancora_dos_ajustes_e_a_soma_dos_filhos_nao_o_valor_declarado(self):
        """Regressao metodologica.

        950 - 20 - 10 = 920 (correto).
        1000 - 20 - 10 = 970 seria semantica errada: os ajustes ocorreram sobre
        a soma dos filhos, nao sobre o valor declarado pela fonte.
        """
        self._resolver_municipios()
        self._resolver("Estado Alfa", 920)
        composicao = self._divergencias()["Estado Alfa"]["composicao_valor_final"]

        declarado = self._divergencias()["Estado Alfa"]["valor_resumo"]
        soma_original = composicao["soma_filhos_original"]
        ajustes = sum(a["ajuste"] for a in composicao["ajustes_filhos"])

        self.assertEqual(soma_original + ajustes, composicao["soma_filhos_atual"])
        self.assertNotEqual(declarado + ajustes, composicao["soma_filhos_atual"])

    def test_diferenca_original_e_diferenca_final_sao_distintas(self):
        self._resolver_municipios()
        self._resolver("Estado Alfa", 920)
        estado = self._divergencias()["Estado Alfa"]
        # Declarado - soma original dos filhos.
        self.assertEqual(estado["diferenca"], 50)
        # Declarado - valor operacional final.
        self.assertEqual(estado["composicao_valor_final"]["diferenca_final_fonte"], 80)

    def test_sinal_do_ajuste_e_preservado(self):
        self._resolver_municipios()
        composicao = self._divergencias()["Estado Alfa"]["composicao_valor_final"]
        self.assertTrue(all(a["ajuste"] < 0 for a in composicao["ajustes_filhos"]))
        self.assertEqual(composicao["ajuste_total_filhos"], -30)
        # A diferenca para a fonte e modulo; a linguagem fica com a UI.
        self.assertEqual(
            self._divergencias()["Municipio A"]["composicao_valor_final"][
                "diferenca_final_fonte"
            ],
            20,
        )

    def test_ajuste_positivo_tambem_e_representado(self):
        # Adotar valor maior que o declarado e legitimo; o sinal tem de aparecer.
        self._resolver("Municipio A", 520)
        composicao = self._divergencias()["Estado Alfa"]["composicao_valor_final"]
        self.assertEqual(composicao["ajustes_filhos"][0]["ajuste"], 20)
        self.assertEqual(composicao["ajuste_total_filhos"], 20)
        self.assertEqual(composicao["soma_filhos_atual"], 970)

    # -- estados parciais ------------------------------------------------------

    def test_divergencia_pendente_expoe_composicao_sem_valor_final(self):
        composicao = self._divergencias()["Estado Alfa"]["composicao_valor_final"]
        self.assertEqual(composicao["soma_filhos_original"], 950)
        self.assertEqual(composicao["soma_filhos_atual"], 950)
        self.assertEqual(composicao["ajuste_total_filhos"], 0)
        # Sem resolucao nao ha valor operacional: nulo, nunca zero.
        self.assertIsNone(composicao["valor_operacional_final"])
        self.assertIsNone(composicao["diferenca_final_fonte"])

    def test_composicao_do_estado_acompanha_resolucoes_parciais(self):
        self._resolver("Municipio A", 480)
        composicao = self._divergencias()["Estado Alfa"]["composicao_valor_final"]
        self.assertEqual(composicao["soma_filhos_atual"], 930)
        self.assertEqual(composicao["ajuste_total_filhos"], -20)
        self.assertEqual(len(composicao["ajustes_filhos"]), 1)

    # -- contrato HTTP ---------------------------------------------------------

    def test_rota_entrega_a_composicao(self):
        self._resolver_municipios()
        self._resolver("Estado Alfa", 920)
        resposta = self._cliente().get(f"/base-eleitoral/{self.base.id}/divergencias")
        self.assertEqual(resposta.status_code, 200)
        estado = next(i for i in resposta.json() if i["territorio"] == "Estado Alfa")
        composicao = estado["composicao_valor_final"]
        self.assertEqual(composicao["soma_filhos_original"], 950)
        self.assertEqual(composicao["soma_filhos_atual"], 920)
        self.assertEqual(composicao["diferenca_final_fonte"], 80)
        self.assertEqual(len(composicao["ajustes_filhos"]), 2)
        # O contrato do cliente continua sem company_id.
        self.assertNotIn("company_id", estado)

    def test_composicao_nao_persiste_nada(self):
        """Read model puro: chamar a listagem nao altera a arvore."""
        antes = {
            (t.id, t.eleitorado_apto, t.eleitorado_apto_origem)
            for t in self.session.query(models.TerritorioEleitoral).all()
        }
        self._divergencias()
        self._divergencias()
        self.session.expire_all()
        depois = {
            (t.id, t.eleitorado_apto, t.eleitorado_apto_origem)
            for t in self.session.query(models.TerritorioEleitoral).all()
        }
        self.assertEqual(antes, depois)

    def test_bairro_sem_filhos_nao_ganha_composicao(self):
        # So territorios com conferencia resumo/detalhe tem composicao.
        divergencias = self._divergencias()
        self.assertNotIn("Bairro m1-1", divergencias)


if __name__ == "__main__":
    unittest.main()
