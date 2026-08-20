"""Parametros de projecao eleitoral da Base Eleitoral (Fase 6A.1).

comparecimento_estimado e percentual_votos_validos sao decisao metodologica
humana. Estes testes garantem que o sistema NUNCA os preenche sozinho e que o
PATCH nao toca em mais nada da base.
"""

import os
import shutil
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-parametros-projecao-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import base_eleitoral as rotas
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from pesquisa360.services import base_eleitoral_import as core

from tests.test_base_eleitoral_import import run_alembic_upgrade

ROTA = "/base-eleitoral/{}/parametros-projecao"


class ParametrosProjecaoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="pesquisa360-parametros-"))
        cls.db_path = cls.temp_dir / "parametros.db"
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
            text(
                "INSERT INTO perfis (id, nome) VALUES"
                " (1, 'Superadmin'), (2, 'Gerente'), (3, 'Agente')"
            )
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
                " VALUES (1, 'g@a', 'Gerente A', 'x', 1, 2, 10),"
                " (2, 'g@b', 'Gerente B', 'x', 1, 2, 20),"
                " (3, 'ag@a', 'Agente A', 'x', 1, 3, 10),"
                " (4, 'su@x', 'Superadmin', 'x', 1, 1, 10)"
            )
        )
        self.session.commit()

        self.gerente_a = self.session.get(models.Usuario, 1)
        self.gerente_b = self.session.get(models.Usuario, 2)
        self.agente_a = self.session.get(models.Usuario, 3)
        self.superadmin = self.session.get(models.Usuario, 4)

        self.base = self._criar_base(company_id=10, nome="Privada A", versao="pa-1")
        self.base_b = self._criar_base(company_id=20, nome="Privada B", versao="pb-1")
        self._importar(self.base)

    def _criar_base(self, **overrides):
        dados = dict(
            nome="Base",
            ano=2026,
            uf="AP",
            fonte="FIXTURE",
            versao="v1",
            data_referencia=date(2026, 1, 1),
            criado_por_id=1,
        )
        dados.update(overrides)
        base = models.BaseEleitoral(**dados)
        self.session.add(base)
        self.session.commit()
        return base

    def _importar(self, base):
        registros = [
            core.RegistroTerritorioImportacao(tipo="ESTADO", nome="Estado", chave="e1"),
            core.RegistroTerritorioImportacao(
                tipo="MUNICIPIO",
                nome="Municipio",
                chave="m1",
                parent_chave="e1",
                eleitorado_apto=1000,
            ),
            core.RegistroTerritorioImportacao(
                tipo="BAIRRO",
                nome="Bairro 1",
                chave="b1",
                parent_chave="m1",
                municipio_chave="m1",
                eleitorado_apto=1000,
            ),
        ]
        core.importar_registros(
            self.session,
            base_eleitoral=base,
            registros=registros,
            arquivo_origem="fixture.csv",
            executado_por_id=1,
            conteudo_arquivo=b"lote",
        )
        self.session.commit()

    def _cliente(self, usuario):
        app = FastAPI()
        # Mesmo handler da aplicacao: sem ele, NaN/Infinity viram 500 no lugar
        # do 422 correto (o corpo do erro ecoa o valor recebido).
        from fastapi.exceptions import RequestValidationError

        from pesquisa360.main import erro_de_validacao

        app.add_exception_handler(RequestValidationError, erro_de_validacao)
        app.include_router(rotas.router)

        def _db():
            yield self.session

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = lambda: usuario
        return TestClient(app)

    def _fotografia(self):
        """Estado que o PATCH nao pode alterar."""
        self.session.expire_all()
        base = self.session.get(models.BaseEleitoral, self.base.id)
        territorios = sorted(
            (t.id, t.nome, t.eleitorado_apto, t.eleitorado_apto_origem, t.status_validacao)
            for t in self.session.query(models.TerritorioEleitoral).all()
        )
        importacoes = sorted(
            (i.id, i.hash_arquivo, str(i.divergencias))
            for i in self.session.query(models.ImportacaoBaseEleitoral).all()
        )
        return {
            "status": base.status,
            "data_referencia": base.data_referencia,
            "versao": base.versao,
            "fonte": base.fonte,
            "company_id": base.company_id,
            "territorios": territorios,
            "importacoes": importacoes,
        }

    # -- leitura ---------------------------------------------------------------

    def test_detalhe_traz_os_parametros_atuais(self):
        resposta = self._cliente(self.gerente_a).get(f"/base-eleitoral/{self.base.id}")
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        # Base recem importada nasce sem parametros: nulo, nunca 1 ou 0.
        self.assertIsNone(corpo["comparecimento_estimado"])
        self.assertIsNone(corpo["percentual_votos_validos"])

    def test_importacao_nao_preenche_parametros_por_convencao(self):
        base = self.session.get(models.BaseEleitoral, self.base.id)
        self.assertIsNone(base.comparecimento_estimado)
        self.assertIsNone(base.percentual_votos_validos)

    # -- escrita valida ---------------------------------------------------------

    def test_patch_grava_os_dois_parametros(self):
        resposta = self._cliente(self.gerente_a).patch(
            ROTA.format(self.base.id),
            json={"comparecimento_estimado": 0.8, "percentual_votos_validos": 0.9},
        )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo["comparecimento_estimado"], 0.8)
        self.assertEqual(corpo["percentual_votos_validos"], 0.9)

    def test_valor_e_persistido_na_forma_canonica(self):
        self._cliente(self.gerente_a).patch(
            ROTA.format(self.base.id), json={"comparecimento_estimado": 0.8}
        )
        self.session.expire_all()
        base = self.session.get(models.BaseEleitoral, self.base.id)
        # 0.8000 no banco, nunca 80.
        self.assertEqual(Decimal(str(base.comparecimento_estimado)), Decimal("0.8000"))
        self.assertLessEqual(Decimal(str(base.comparecimento_estimado)), Decimal("1"))

    def test_patch_parcial_mantem_o_outro_parametro(self):
        cliente = self._cliente(self.gerente_a)
        cliente.patch(
            ROTA.format(self.base.id),
            json={"comparecimento_estimado": 0.8, "percentual_votos_validos": 0.9},
        )
        resposta = cliente.patch(
            ROTA.format(self.base.id), json={"percentual_votos_validos": 0.95}
        )
        corpo = resposta.json()
        self.assertEqual(corpo["comparecimento_estimado"], 0.8)
        self.assertEqual(corpo["percentual_votos_validos"], 0.95)

    def test_null_explicito_limpa_a_configuracao(self):
        cliente = self._cliente(self.gerente_a)
        cliente.patch(
            ROTA.format(self.base.id),
            json={"comparecimento_estimado": 0.8, "percentual_votos_validos": 0.9},
        )
        resposta = cliente.patch(
            ROTA.format(self.base.id), json={"comparecimento_estimado": None}
        )
        corpo = resposta.json()
        self.assertIsNone(corpo["comparecimento_estimado"])
        # Campo ausente segue intacto: null e "limpar", nao "zerar tudo".
        self.assertEqual(corpo["percentual_votos_validos"], 0.9)

    def test_zero_e_um_sao_valores_legitimos(self):
        resposta = self._cliente(self.gerente_a).patch(
            ROTA.format(self.base.id),
            json={"comparecimento_estimado": 0, "percentual_votos_validos": 1},
        )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo["comparecimento_estimado"], 0.0)
        self.assertEqual(corpo["percentual_votos_validos"], 1.0)

    def test_base_validada_aceita_alteracao_sem_mudar_status(self):
        base = self.session.get(models.BaseEleitoral, self.base.id)
        base.status = "VALIDADA"
        self.session.commit()

        resposta = self._cliente(self.gerente_a).patch(
            ROTA.format(self.base.id), json={"comparecimento_estimado": 0.75}
        )
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["status"], "VALIDADA")

    def test_base_substituida_recusa_alteracao(self):
        base = self.session.get(models.BaseEleitoral, self.base.id)
        base.status = "SUBSTITUIDA"
        self.session.commit()

        resposta = self._cliente(self.gerente_a).patch(
            ROTA.format(self.base.id), json={"comparecimento_estimado": 0.75}
        )
        self.assertEqual(resposta.status_code, 422)

    # -- validacao --------------------------------------------------------------

    def test_valores_fora_da_faixa_sao_recusados(self):
        cliente = self._cliente(self.gerente_a)
        for payload in (
            {"comparecimento_estimado": -0.01},
            {"comparecimento_estimado": 1.01},
            {"percentual_votos_validos": -1},
            {"percentual_votos_validos": 2},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(
                    cliente.patch(ROTA.format(self.base.id), json=payload).status_code, 422
                )

    def test_nan_infinito_e_texto_sao_recusados(self):
        cliente = self._cliente(self.gerente_a)
        # NaN/Infinity nao sao JSON valido, mas Python/JS os emitem; texto tampouco.
        corpos = [
            '{"comparecimento_estimado": NaN}',
            '{"comparecimento_estimado": Infinity}',
            '{"comparecimento_estimado": -Infinity}',
            '{"comparecimento_estimado": "oitenta por cento"}',
            '{"comparecimento_estimado": "80%"}',
        ]
        for corpo in corpos:
            with self.subTest(corpo=corpo):
                resposta = cliente.patch(
                    ROTA.format(self.base.id),
                    content=corpo,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(resposta.status_code, 422)
        self.session.expire_all()
        base = self.session.get(models.BaseEleitoral, self.base.id)
        self.assertIsNone(base.comparecimento_estimado)

    def test_percentual_inteiro_nao_e_aceito(self):
        # 80 significaria 8000%; o backend e canonico em 0..1.
        resposta = self._cliente(self.gerente_a).patch(
            ROTA.format(self.base.id), json={"comparecimento_estimado": 80}
        )
        self.assertEqual(resposta.status_code, 422)

    def test_campos_fora_do_contrato_sao_recusados(self):
        cliente = self._cliente(self.gerente_a)
        for extra in ("company_id", "status", "versao", "fonte", "eleitorado_operacional"):
            with self.subTest(campo=extra):
                resposta = cliente.patch(
                    ROTA.format(self.base.id),
                    json={"comparecimento_estimado": 0.8, extra: 1},
                )
                self.assertEqual(resposta.status_code, 422)

    # -- permissoes -------------------------------------------------------------

    def test_agente_nao_altera_parametros(self):
        resposta = self._cliente(self.agente_a).patch(
            ROTA.format(self.base.id), json={"comparecimento_estimado": 0.8}
        )
        self.assertEqual(resposta.status_code, 403)
        self.session.expire_all()
        self.assertIsNone(
            self.session.get(models.BaseEleitoral, self.base.id).comparecimento_estimado
        )

    def test_base_de_outro_tenant_responde_404(self):
        resposta = self._cliente(self.gerente_a).patch(
            ROTA.format(self.base_b.id), json={"comparecimento_estimado": 0.8}
        )
        self.assertEqual(resposta.status_code, 404)

    def test_base_inexistente_responde_404(self):
        resposta = self._cliente(self.gerente_a).patch(
            ROTA.format(999999), json={"comparecimento_estimado": 0.8}
        )
        self.assertEqual(resposta.status_code, 404)

    def test_base_oficial_exige_superadmin(self):
        oficial = self._criar_base(company_id=None, nome="Oficial", versao="of-1")
        self.assertEqual(
            self._cliente(self.gerente_a)
            .patch(ROTA.format(oficial.id), json={"comparecimento_estimado": 0.8})
            .status_code,
            403,
        )
        self.assertEqual(
            self._cliente(self.superadmin)
            .patch(ROTA.format(oficial.id), json={"comparecimento_estimado": 0.8})
            .status_code,
            200,
        )

    # -- regressao: nao mexer em mais nada ---------------------------------------

    def test_patch_nao_altera_territorios_nem_importacao(self):
        antes = self._fotografia()
        self._cliente(self.gerente_a).patch(
            ROTA.format(self.base.id),
            json={"comparecimento_estimado": 0.8, "percentual_votos_validos": 0.9},
        )
        self.assertEqual(antes, self._fotografia())

    def test_patch_nao_altera_eleitorado_da_base(self):
        cliente = self._cliente(self.gerente_a)
        antes = cliente.get(f"/base-eleitoral/{self.base.id}").json()
        cliente.patch(
            ROTA.format(self.base.id),
            json={"comparecimento_estimado": 0.8, "percentual_votos_validos": 0.9},
        )
        depois = cliente.get(f"/base-eleitoral/{self.base.id}").json()
        for campo in (
            "eleitorado_operacional",
            "eleitorado_declarado",
            "diferenca_eleitorado",
            "total_territorios",
            "total_em_conferencia",
            "status",
            "data_referencia",
            "versao",
            "fonte",
            "total_importacoes",
        ):
            with self.subTest(campo=campo):
                self.assertEqual(antes[campo], depois[campo])

    def test_resposta_nao_expoe_company_id(self):
        resposta = self._cliente(self.gerente_a).patch(
            ROTA.format(self.base.id), json={"comparecimento_estimado": 0.8}
        )
        self.assertNotIn("company_id", resposta.json())


if __name__ == "__main__":
    unittest.main()
