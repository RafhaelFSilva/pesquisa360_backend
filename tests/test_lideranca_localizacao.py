"""Localizacao da lideranca no mapa (Fase 6B).

O ponto e uma referencia espacial da lideranca. NAO define o setor, nao e
inferido de bairro nem de geofence, e ausencia continua sendo estado valido.
"""

import os
import shutil
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from shapely import wkt as shapely_wkt
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-lideranca-localizacao-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.api.endpoints import liderancas as rotas
from pesquisa360.core.dependencies import get_current_user, get_db
from pesquisa360.db import models
from pesquisa360.services import base_eleitoral_import as core

from tests.test_base_eleitoral_import import run_alembic_upgrade

# Coordenadas de fixture; nenhuma delas vive no codigo de producao.
PONTO = {"type": "Point", "coordinates": [-51.0694, 0.0349]}
OUTRO_PONTO = {"type": "Point", "coordinates": [-51.1, 0.05]}


class LiderancaLocalizacaoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="pesquisa360-localizacao-"))
        cls.db_path = cls.temp_dir / "localizacao.db"
        run_alembic_upgrade(f"sqlite:///{cls.db_path.as_posix()}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self):
        self.engine = create_engine(f"sqlite:///{self.db_path.as_posix()}")

        # O SQLite nao tem PostGIS. Os stubs dos outros testes apenas devolvem o
        # valor, o que basta para gravar; aqui a geometria precisa VOLTAR, entao
        # convertemos EWKT -> WKB hex, que e o formato que o PostGIS entrega.
        def _ewkt_para_wkb_hex(valor):
            if valor is None:
                return None
            if isinstance(valor, (bytes, bytearray)):
                return valor
            texto = valor.split(";", 1)[-1] if ";" in valor else valor
            return shapely_wkt.loads(texto).wkb_hex

        @event.listens_for(self.engine, "connect")
        def _preparar(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
            dbapi_connection.create_function("GeomFromEWKT", 1, _ewkt_para_wkb_hex)
            dbapi_connection.create_function("ST_GeomFromEWKT", 1, _ewkt_para_wkb_hex)
            dbapi_connection.create_function("AsEWKB", 1, lambda valor: valor)

        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        self._limpar()
        self._semear()

    def _limpar(self):
        for tabela in (
            "lideranca_territorio_eleitoral",
            "lideranca_pesquisa_config",
            "liderancas_politicas",
            "importacao_base_eleitoral",
            "projeto_base_eleitoral",
            "territorio_eleitoral",
            "base_eleitoral",
            "setores",
            "pesquisas",
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
                " (3, 'ag@a', 'Agente A', 'x', 1, 3, 10)"
            )
        )
        self.session.execute(
            text(
                "INSERT INTO projetos (id, nome, status, data_inicio, coordenador_id, company_id)"
                " VALUES (100, 'Campanha A', 'Ativo', '2026-01-01', 1, 10),"
                " (200, 'Campanha B', 'Ativo', '2026-01-01', 2, 20)"
            )
        )
        self.session.execute(
            text(
                "INSERT INTO pesquisas (id, titulo, projeto_id, ativo)"
                " VALUES (300, 'Onda 1', 100, 1)"
            )
        )
        self.session.execute(
            text(
                "INSERT INTO setores (id, nome, meta, pesquisa_id, tolerancia, finalidade)"
                " VALUES (400, 'Setor A', 100, 300, 50, 'AMBOS')"
            )
        )
        self.session.commit()

        self.gerente = self.session.get(models.Usuario, 1)
        self.gerente_b = self.session.get(models.Usuario, 2)
        self.agente = self.session.get(models.Usuario, 3)

        base = models.BaseEleitoral(
            nome="Base",
            ano=2026,
            uf="AP",
            fonte="FIXTURE",
            versao="v1",
            data_referencia=date(2026, 1, 1),
            criado_por_id=1,
            company_id=10,
            status="VALIDADA",
        )
        self.session.add(base)
        self.session.commit()
        core.importar_registros(
            self.session,
            base_eleitoral=base,
            registros=[
                core.RegistroTerritorioImportacao(tipo="ESTADO", nome="Estado", chave="e1"),
                core.RegistroTerritorioImportacao(
                    tipo="MUNICIPIO", nome="Municipio", chave="m1", parent_chave="e1"
                ),
                core.RegistroTerritorioImportacao(
                    tipo="BAIRRO",
                    nome="Bairro 1",
                    chave="b1",
                    parent_chave="m1",
                    municipio_chave="m1",
                    eleitorado_apto=1000,
                ),
            ],
            arquivo_origem="fixture.csv",
            executado_por_id=1,
            conteudo_arquivo=b"lote",
        )
        self.session.add(
            models.ProjetoBaseEleitoral(
                projeto_id=100, base_eleitoral_id=base.id, principal=True,
                vinculado_em=datetime.now(timezone.utc),
            )
        )
        self.session.commit()
        self.base = base
        self.bairro = (
            self.session.query(models.TerritorioEleitoral)
            .filter(models.TerritorioEleitoral.tipo == "BAIRRO")
            .one()
        )

        self.lideranca = models.LiderancaPolitica(projeto_id=100, nome="Lideranca QA")
        self.session.add(self.lideranca)
        self.session.commit()

    def _cliente(self, usuario):
        app = FastAPI()
        from fastapi.exceptions import RequestValidationError

        from pesquisa360.main import erro_de_validacao

        app.add_exception_handler(RequestValidationError, erro_de_validacao)
        app.include_router(rotas.router)

        def _db():
            yield self.session

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = lambda: usuario
        return TestClient(app)

    def _patch(self, corpo, usuario=None, lideranca_id=None, projeto_id=100):
        cliente = self._cliente(usuario or self.gerente)
        alvo = lideranca_id or self.lideranca.id
        return cliente.patch(f"/projetos/{projeto_id}/liderancas/{alvo}", json=corpo)

    def _configurar(self):
        """Setor, cota e bairro: o que o PATCH de posicao nao pode tocar."""
        cliente = self._cliente(self.gerente)
        cliente.put(
            f"/projetos/100/liderancas/{self.lideranca.id}/pesquisas/300/config",
            json={"setor_id": 400, "cota_votos_validos": 500},
        )
        cliente.put(
            f"/projetos/100/liderancas/{self.lideranca.id}/territorios",
            json={"territorio_ids": [self.bairro.id]},
        )

    # -- serializacao ----------------------------------------------------------

    def test_helper_aceita_todas_as_formas_que_o_driver_entrega(self):
        """Regressao: o PostGIS entrega memoryview, o SQLite entrega hex.

        O harness SQLite nao produz memoryview, entao a forma real de producao
        precisa ser exercitada diretamente aqui.
        """
        from geoalchemy2.shape import from_shape
        from shapely.geometry import Point

        from pesquisa360.core.utils import wkb_to_geojson_point

        wkb = from_shape(Point(-51.05, 0.04), srid=4326)
        bruto = bytes.fromhex(str(wkb.data)) if isinstance(wkb.data, str) else bytes(wkb.data)

        class _Elemento:
            def __init__(self, data):
                self.data = data

        for rotulo, dado in (
            ("memoryview", memoryview(bruto)),
            ("bytes", bruto),
            ("bytearray", bytearray(bruto)),
            ("hex", bruto.hex()),
        ):
            with self.subTest(forma=rotulo):
                resultado = wkb_to_geojson_point(_Elemento(dado))
                self.assertEqual(resultado["type"], "Point")
                self.assertAlmostEqual(resultado["coordinates"][0], -51.05, places=6)
                self.assertAlmostEqual(resultado["coordinates"][1], 0.04, places=6)

        self.assertIsNone(wkb_to_geojson_point(None))

    def test_lideranca_sem_ponto_serializa_null(self):
        resposta = self._cliente(self.gerente).get("/projetos/100/liderancas")
        self.assertEqual(resposta.status_code, 200)
        item = resposta.json()[0]
        # Ausencia de ponto e estado valido; nunca (0,0).
        self.assertIsNone(item["localizacao"])

    def test_ponto_volta_como_geojson_e_nunca_como_geometria_crua(self):
        self.assertEqual(self._patch({"localizacao": PONTO}).status_code, 200)
        item = self._cliente(self.gerente).get("/projetos/100/liderancas").json()[0]
        self.assertEqual(item["localizacao"]["type"], "Point")
        longitude, latitude = item["localizacao"]["coordinates"]
        self.assertAlmostEqual(longitude, -51.0694, places=6)
        self.assertAlmostEqual(latitude, 0.0349, places=6)
        # Ordem GeoJSON: longitude primeiro.
        self.assertLess(longitude, latitude)

    # -- escrita ---------------------------------------------------------------

    def test_patch_grava_o_ponto(self):
        resposta = self._patch({"localizacao": PONTO})
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["localizacao"]["coordinates"][0], -51.0694)

    def test_patch_substitui_o_ponto(self):
        self._patch({"localizacao": PONTO})
        resposta = self._patch({"localizacao": OUTRO_PONTO})
        self.assertEqual(resposta.json()["localizacao"]["coordinates"][0], -51.1)

    def test_null_explicito_remove_o_ponto_sem_apagar_a_lideranca(self):
        self._patch({"localizacao": PONTO})
        resposta = self._patch({"localizacao": None})
        self.assertEqual(resposta.status_code, 200)
        self.assertIsNone(resposta.json()["localizacao"])
        self.assertTrue(resposta.json()["ativo"])

    def test_campo_ausente_mantem_o_ponto(self):
        self._patch({"localizacao": PONTO})
        resposta = self._patch({"nome": "Lideranca QA renomeada"})
        self.assertEqual(resposta.json()["nome"], "Lideranca QA renomeada")
        # Renomear nao pode apagar a posicao.
        self.assertIsNotNone(resposta.json()["localizacao"])

    # -- validacao -------------------------------------------------------------

    def test_coordenadas_fora_da_faixa_sao_recusadas(self):
        for coordenadas in ([-180.1, 0], [180.1, 0], [0, -90.1], [0, 90.1]):
            with self.subTest(coordenadas=coordenadas):
                resposta = self._patch(
                    {"localizacao": {"type": "Point", "coordinates": coordenadas}}
                )
                self.assertEqual(resposta.status_code, 422)

    def test_limites_da_faixa_sao_aceitos(self):
        for coordenadas in ([-180, -90], [180, 90], [0, 0]):
            with self.subTest(coordenadas=coordenadas):
                resposta = self._patch(
                    {"localizacao": {"type": "Point", "coordinates": coordenadas}}
                )
                self.assertEqual(resposta.status_code, 200)

    def test_nan_e_infinito_sao_recusados(self):
        cliente = self._cliente(self.gerente)
        for corpo in (
            '{"localizacao": {"type": "Point", "coordinates": [NaN, 0]}}',
            '{"localizacao": {"type": "Point", "coordinates": [0, Infinity]}}',
            '{"localizacao": {"type": "Point", "coordinates": [0, -Infinity]}}',
        ):
            with self.subTest(corpo=corpo):
                resposta = cliente.patch(
                    f"/projetos/100/liderancas/{self.lideranca.id}",
                    content=corpo,
                    headers={"Content-Type": "application/json"},
                )
                self.assertEqual(resposta.status_code, 422)
        self.session.expire_all()
        self.assertIsNone(self.session.get(models.LiderancaPolitica, self.lideranca.id).localizacao)

    def test_outras_geometrias_sao_recusadas(self):
        for geometria in (
            {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
            {"type": "LineString", "coordinates": [[0, 0], [1, 1]]},
            {"type": "MultiPoint", "coordinates": [[0, 0], [1, 1]]},
        ):
            with self.subTest(tipo=geometria["type"]):
                self.assertEqual(self._patch({"localizacao": geometria}).status_code, 422)

    def test_coordenadas_com_aridade_errada_sao_recusadas(self):
        for coordenadas in ([0], [0, 0, 0], []):
            with self.subTest(coordenadas=coordenadas):
                resposta = self._patch(
                    {"localizacao": {"type": "Point", "coordinates": coordenadas}}
                )
                self.assertEqual(resposta.status_code, 422)

    def test_company_id_nao_entra_pelo_patch(self):
        self.assertEqual(
            self._patch({"localizacao": PONTO, "company_id": 20}).status_code, 422
        )

    def test_resposta_nao_expoe_company_id(self):
        self.assertNotIn("company_id", self._patch({"localizacao": PONTO}).json())

    # -- permissoes ------------------------------------------------------------

    def test_agente_nao_altera_localizacao(self):
        resposta = self._patch({"localizacao": PONTO}, usuario=self.agente)
        self.assertEqual(resposta.status_code, 403)
        self.session.expire_all()
        self.assertIsNone(self.session.get(models.LiderancaPolitica, self.lideranca.id).localizacao)

    def test_lideranca_de_outro_tenant_responde_404(self):
        resposta = self._patch({"localizacao": PONTO}, usuario=self.gerente_b)
        self.assertEqual(resposta.status_code, 404)

    def test_projeto_de_outro_tenant_responde_404(self):
        resposta = self._patch({"localizacao": PONTO}, projeto_id=200)
        self.assertEqual(resposta.status_code, 404)

    # -- regressao: posicao nao mexe em mais nada --------------------------------

    def test_posicionar_nao_altera_setor_cota_nem_bairros(self):
        self._configurar()
        cliente = self._cliente(self.gerente)
        antes = cliente.get("/projetos/100/liderancas").json()[0]

        self._patch({"localizacao": PONTO})
        depois = cliente.get("/projetos/100/liderancas").json()[0]

        self.assertEqual(antes["configs"], depois["configs"])
        self.assertEqual(antes["territorios"], depois["territorios"])
        self.assertEqual(antes["nome"], depois["nome"])
        self.assertEqual(antes["ativo"], depois["ativo"])

    def test_remover_posicao_nao_altera_setor_cota_nem_bairros(self):
        self._configurar()
        self._patch({"localizacao": PONTO})
        cliente = self._cliente(self.gerente)
        antes = cliente.get("/projetos/100/liderancas").json()[0]

        self._patch({"localizacao": None})
        depois = cliente.get("/projetos/100/liderancas").json()[0]

        self.assertIsNone(depois["localizacao"])
        self.assertEqual(antes["configs"], depois["configs"])
        self.assertEqual(antes["territorios"], depois["territorios"])

    def test_ponto_nao_define_nem_altera_o_setor(self):
        """O setor continua vindo de LiderancaPesquisaConfig, nunca do mapa."""
        self._configurar()
        self._patch({"localizacao": PONTO})
        config = (
            self.session.query(models.LiderancaPesquisaConfig)
            .filter(models.LiderancaPesquisaConfig.lideranca_id == self.lideranca.id)
            .one()
        )
        self.assertEqual(config.setor_id, 400)

    def test_criar_lideranca_nao_gera_ponto_automatico(self):
        resposta = self._cliente(self.gerente).post(
            "/projetos/100/liderancas", json={"nome": "Sem ponto"}
        )
        self.assertEqual(resposta.status_code, 201)
        # Nem centroide de setor, nem geofence, nem (0,0).
        self.assertIsNone(resposta.json()["localizacao"])

    def test_create_nao_aceita_localizacao(self):
        # Posicionamento e uma acao explicita do mapa, nao parte do cadastro.
        resposta = self._cliente(self.gerente).post(
            "/projetos/100/liderancas", json={"nome": "X", "localizacao": PONTO}
        )
        self.assertEqual(resposta.status_code, 422)


if __name__ == "__main__":
    unittest.main()
