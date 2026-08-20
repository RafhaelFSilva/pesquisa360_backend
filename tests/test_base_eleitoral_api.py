"""Testes das rotas da Base Eleitoral (Fase 3A).

Foco no contrato HTTP: 404 para base de outro tenant em TODAS as rotas, ausencia
de company_id nos requests e nenhuma geometria PostGIS crua na resposta.
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

os.environ.setdefault("SECRET_KEY", "test-only-base-eleitoral-api-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import base_eleitoral as rotas
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from pesquisa360.services import base_eleitoral_import as core

from tests.test_base_eleitoral_import import run_alembic_upgrade


class BaseEleitoralApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="pesquisa360-api-"))
        cls.db_path = cls.temp_dir / "api.db"
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
                " (200, 'Campanha B', 'Ativo', '2026-01-01', 2, 20)"
            )
        )
        self.session.commit()

        self.usuario_a = self.session.get(models.Usuario, 1)
        self.usuario_b = self.session.get(models.Usuario, 2)
        self.oficial = self._criar_base(company_id=None, nome="Oficial", versao="of-1")
        self.privada_a = self._criar_base(company_id=10, nome="Privada A", versao="pa-1")
        self.privada_b = self._criar_base(company_id=20, nome="Privada B", versao="pb-1")
        self._importar(self.privada_b, detalhes=(400, 350, 200), conteudo=b"lote-b")
        self._importar(self.privada_a, detalhes=(400, 350, 200), conteudo=b"lote-a")
        self.municipio_a = self._municipio(self.privada_a)
        self.municipio_b = self._municipio(self.privada_b)

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

    def _importar(self, base, detalhes, conteudo):
        registros = [
            core.RegistroTerritorioImportacao(tipo="ESTADO", nome="Amapá", chave="e1"),
            core.RegistroTerritorioImportacao(
                tipo="MUNICIPIO",
                nome="Macapá",
                chave="m1",
                parent_chave="e1",
                codigo="6050",
                eleitorado_apto=1000,
            ),
        ]
        for indice, valor in enumerate(detalhes, start=1):
            registros.append(
                core.RegistroTerritorioImportacao(
                    tipo="BAIRRO",
                    nome="Bairro {}".format(indice),
                    chave="b{}".format(indice),
                    parent_chave="m1",
                    municipio_chave="m1",
                    eleitorado_apto=valor,
                )
            )
        return core.importar_registros(
            self.session,
            base_eleitoral=base,
            registros=registros,
            arquivo_origem="fixture.csv",
            executado_por_id=1,
            conteudo_arquivo=conteudo,
        )

    def _municipio(self, base):
        return (
            self.session.query(models.TerritorioEleitoral)
            .filter(
                models.TerritorioEleitoral.base_eleitoral_id == base.id,
                models.TerritorioEleitoral.tipo == "MUNICIPIO",
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

    # -- leitura ---------------------------------------------------------------

    def test_listagem_mostra_oficial_e_privada_do_proprio_tenant(self):
        resposta = self._cliente(self.usuario_a).get("/base-eleitoral/")
        self.assertEqual(resposta.status_code, 200)
        ids = {item["id"] for item in resposta.json()}
        self.assertIn(self.oficial.id, ids)
        self.assertIn(self.privada_a.id, ids)
        self.assertNotIn(self.privada_b.id, ids)

    def test_listagem_marca_base_oficial(self):
        resposta = self._cliente(self.usuario_a).get("/base-eleitoral/")
        por_id = {item["id"]: item for item in resposta.json()}
        self.assertTrue(por_id[self.oficial.id]["eh_oficial"])
        self.assertFalse(por_id[self.privada_a.id]["eh_oficial"])
        # company_id nunca e exposto no contrato do cliente.
        self.assertNotIn("company_id", por_id[self.privada_a.id])

    def test_detalhe_traz_contadores_de_conferencia(self):
        resposta = self._cliente(self.usuario_a).get(f"/base-eleitoral/{self.privada_a.id}")
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo["status"], "EM_CONFERENCIA")
        self.assertEqual(corpo["total_territorios"], 5)
        self.assertEqual(corpo["total_em_conferencia"], 1)

    def test_territorios_nao_expoem_geometria_crua(self):
        resposta = self._cliente(self.usuario_a).get(
            f"/base-eleitoral/{self.privada_a.id}/territorios"
        )
        self.assertEqual(resposta.status_code, 200)
        for item in resposta.json():
            with self.subTest(territorio=item["nome"]):
                self.assertNotIn("geometria", item)
                self.assertIn("possui_geometria", item)

    def test_filtro_por_tipo_e_busca_textual(self):
        cliente = self._cliente(self.usuario_a)
        base_id = self.privada_a.id
        resposta = cliente.get(f"/base-eleitoral/{base_id}/territorios", params={"tipo": "BAIRRO"})
        self.assertEqual({item["tipo"] for item in resposta.json()}, {"BAIRRO"})

        resposta = cliente.get(
            f"/base-eleitoral/{base_id}/territorios", params={"status_validacao": "EM_CONFERENCIA"}
        )
        self.assertEqual(len(resposta.json()), 1)

        # Busca por acento tem de casar com a chave normalizada.
        resposta = cliente.get(f"/base-eleitoral/{base_id}/territorios", params={"q": "macapa"})
        self.assertEqual([item["nome"] for item in resposta.json()], ["Macapá"])

    def test_divergencias_expostas_com_origem_do_lote(self):
        resposta = self._cliente(self.usuario_a).get(
            f"/base-eleitoral/{self.privada_a.id}/divergencias"
        )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(len(corpo), 1)
        self.assertEqual(corpo[0]["valor_resumo"], 1000)
        self.assertEqual(corpo[0]["valor_detalhe"], 950)
        self.assertFalse(corpo[0]["resolvida"])
        self.assertEqual(corpo[0]["arquivo_origem"], "fixture.csv")

    # -- 404 cross-tenant -------------------------------------------------------

    def test_todas_as_rotas_retornam_404_para_base_de_outro_tenant(self):
        cliente = self._cliente(self.usuario_a)
        base_id = self.privada_b.id
        chamadas = {
            "detalhe": lambda: cliente.get(f"/base-eleitoral/{base_id}"),
            "territorios": lambda: cliente.get(f"/base-eleitoral/{base_id}/territorios"),
            "divergencias": lambda: cliente.get(f"/base-eleitoral/{base_id}/divergencias"),
            "validar": lambda: cliente.post(f"/base-eleitoral/{base_id}/validar"),
            "resolver": lambda: cliente.post(
                f"/base-eleitoral/territorios/{self.municipio_b.id}/resolver-divergencia",
                json={"valor_final": 10, "justificativa": "tentativa"},
            ),
            "vincular": lambda: cliente.post(f"/projetos/100/base-eleitoral/{base_id}/vincular"),
        }
        for nome, chamada in chamadas.items():
            with self.subTest(rota=nome):
                resposta = chamada()
                self.assertEqual(resposta.status_code, 404)
                # 404 e nao 403: nao revelar que o recurso existe em outro tenant.
                self.assertNotEqual(resposta.status_code, 403)

    def test_tentativa_cross_tenant_nao_altera_dados(self):
        cliente = self._cliente(self.usuario_a)
        cliente.post(
            f"/base-eleitoral/territorios/{self.municipio_b.id}/resolver-divergencia",
            json={"valor_final": 10, "justificativa": "tentativa"},
        )
        self.session.expire_all()
        municipio = self.session.get(models.TerritorioEleitoral, self.municipio_b.id)
        self.assertEqual(municipio.eleitorado_apto, 1000)
        self.assertTrue(municipio.eleitorado_apto_divergente)

    def test_oficial_acessivel_pelos_dois_tenants(self):
        for usuario in (self.usuario_a, self.usuario_b):
            with self.subTest(usuario=usuario.email):
                resposta = self._cliente(usuario).get(f"/base-eleitoral/{self.oficial.id}")
                self.assertEqual(resposta.status_code, 200)
                self.assertTrue(resposta.json()["eh_oficial"])

    # -- acoes -----------------------------------------------------------------

    def test_vincular_projeto_a_base_visivel(self):
        resposta = self._cliente(self.usuario_a).post(
            f"/projetos/100/base-eleitoral/{self.oficial.id}/vincular"
        )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo["projeto_id"], 100)
        self.assertTrue(corpo["principal"])

    def test_vincular_projeto_de_outro_tenant_retorna_404(self):
        resposta = self._cliente(self.usuario_a).post(
            f"/projetos/200/base-eleitoral/{self.oficial.id}/vincular"
        )
        self.assertEqual(resposta.status_code, 404)

    def test_request_de_vinculo_rejeita_company_id(self):
        resposta = self._cliente(self.usuario_a).post(
            f"/projetos/100/base-eleitoral/{self.oficial.id}/vincular",
            json={"principal": True, "company_id": 20},
        )
        self.assertEqual(resposta.status_code, 422)

    def test_validar_rejeita_base_com_divergencia_pendente(self):
        resposta = self._cliente(self.usuario_a).post(
            f"/base-eleitoral/{self.privada_a.id}/validar"
        )
        self.assertEqual(resposta.status_code, 422)

    def test_fluxo_resolver_e_depois_validar(self):
        cliente = self._cliente(self.usuario_a)
        resposta = cliente.post(
            f"/base-eleitoral/territorios/{self.municipio_a.id}/resolver-divergencia",
            json={"valor_final": 980, "justificativa": "Conferencia manual da pagina 47"},
        )
        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo["eleitorado_apto"], 980)
        self.assertEqual(corpo["eleitorado_apto_origem"], 1000)
        self.assertFalse(corpo["eleitorado_apto_divergente"])

        resposta = cliente.post(f"/base-eleitoral/{self.privada_a.id}/validar")
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["status"], "VALIDADA")
        self.assertEqual(resposta.json()["total_em_conferencia"], 0)

    def test_resolver_exige_justificativa_e_valor_nao_negativo(self):
        cliente = self._cliente(self.usuario_a)
        url = f"/base-eleitoral/territorios/{self.municipio_a.id}/resolver-divergencia"
        for payload in (
            {"valor_final": 980, "justificativa": "   "},
            {"valor_final": -1, "justificativa": "ok"},
            {"valor_final": 980},
            {"justificativa": "ok"},
        ):
            with self.subTest(payload=payload):
                self.assertEqual(cliente.post(url, json=payload).status_code, 422)

    def test_resolver_rejeita_campo_extra(self):
        resposta = self._cliente(self.usuario_a).post(
            f"/base-eleitoral/territorios/{self.municipio_a.id}/resolver-divergencia",
            json={"valor_final": 980, "justificativa": "ok", "company_id": 20},
        )
        self.assertEqual(resposta.status_code, 422)

    def test_nao_existe_rota_de_importacao_generica(self):
        # Sem a fonte real, expor upload/JSON livre criaria um adapter falso.
        caminhos = {rota.path for rota in rotas.router.routes}
        for caminho in caminhos:
            with self.subTest(caminho=caminho):
                self.assertNotIn("importar", caminho)
                self.assertNotIn("upload", caminho)


if __name__ == "__main__":
    unittest.main()
