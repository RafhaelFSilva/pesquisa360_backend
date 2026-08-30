"""Importacao administrativa de Setores (FASE E.4).

Cobre dois eixos que ate agora nao tinham teste EXECUTAVEL no runtime oficial:

1. o motor geoespacial (`setor_shapefile_import`), cuja suite existente depende
   de pytest e por isso nunca roda aqui;
2. as rotas de Admin -- validar sem persistir, importar reprocessando o
   arquivo, e a composicao territorial confirmada.

As funcoes espaciais do PostGIS sao substituidas por implementacoes reais em
Shapely dentro do SQLite. Nao sao stubs que devolvem constante: `ST_Covers` e
`ST_Intersects` calculam de verdade, entao os testes de territorio coberto x
parcial exercitam o criterio geometrico, e nao a fiacao.
"""

from __future__ import annotations

import io
import json
import os
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import shapefile
from pyproj import CRS
from shapely import wkt as shapely_wkt
from shapely.geometry import Polygon
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-setor-import-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from fastapi import FastAPI
from fastapi.testclient import TestClient

from pesquisa360.api.endpoints import setor_importacao
from pesquisa360.core.dependencies import get_db, require_manager_or_superadmin
from pesquisa360.services import setor_import_admin as import_service
from tests.test_base_eleitoral_import import run_alembic_upgrade

from pesquisa360.services.setor_shapefile_import import (
    MultipleFeaturesError,
    ShapefileImportError,
    load_sector_geometry,
    read_source_crs,
    validate_shapefile_components,
)

PROJETO = 100
PESQUISA = 1000
COMPANY = 10


# --- construcao de shapefiles ------------------------------------------------


def quadrado(min_x: float, min_y: float, tamanho: float = 1.0) -> list[list[float]]:
    """Anel no sentido horario, como o shapefile espera para o anel externo."""
    return [
        [min_x, min_y],
        [min_x, min_y + tamanho],
        [min_x + tamanho, min_y + tamanho],
        [min_x + tamanho, min_y],
        [min_x, min_y],
    ]


def escrever_shapefile(
    base: Path,
    feicoes: list[list[list[list[float]]]],
    *,
    crs: CRS = CRS.from_epsg(4326),
) -> Path:
    with shapefile.Writer(str(base), shapeType=shapefile.POLYGON) as writer:
        writer.field("id", "N")
        for indice, partes in enumerate(feicoes, start=1):
            writer.poly(partes)
            writer.record(indice)
    base.with_suffix(".prj").write_text(crs.to_wkt(), encoding="utf-8")
    return base.with_suffix(".shp")


def zipar(shp: Path) -> bytes:
    """Empacota o conjunto completo, como o Web enviaria."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as pacote:
        for extensao in (".shp", ".shx", ".dbf", ".prj"):
            componente = shp.with_suffix(extensao)
            if componente.is_file():
                pacote.write(componente, componente.name)
    return buffer.getvalue()


def pacote_quadrado(nome: str, min_x: float, min_y: float, tamanho: float = 1.0) -> bytes:
    with TemporaryDirectory() as tmp:
        shp = escrever_shapefile(Path(tmp) / nome, [[quadrado(min_x, min_y, tamanho)]])
        return zipar(shp)


# --- motor geoespacial (sem banco) -------------------------------------------


class MotorShapefileTests(unittest.TestCase):
    """Cobertura executavel do motor que hoje so tem suite pytest."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_conjunto_incompleto_e_recusado(self):
        shp = escrever_shapefile(self.tmp / "centro", [[quadrado(-51, -1)]])
        shp.with_suffix(".shx").unlink()

        with self.assertRaises(ShapefileImportError) as erro:
            validate_shapefile_components(shp)
        self.assertIn(".shx", str(erro.exception))

    def test_extensao_diferente_de_shp_e_recusada(self):
        with self.assertRaises(ShapefileImportError):
            validate_shapefile_components(self.tmp / "centro.dbf")

    def test_prj_vazio_e_recusado(self):
        shp = escrever_shapefile(self.tmp / "centro", [[quadrado(-51, -1)]])
        shp.with_suffix(".prj").write_text("", encoding="utf-8")

        with self.assertRaises(ShapefileImportError):
            read_source_crs(shp.with_suffix(".prj"))

    def test_prj_ilegivel_e_recusado(self):
        shp = escrever_shapefile(self.tmp / "centro", [[quadrado(-51, -1)]])
        shp.with_suffix(".prj").write_text("isto nao e WKT", encoding="utf-8")

        with self.assertRaises(ShapefileImportError):
            read_source_crs(shp.with_suffix(".prj"))

    def test_poligono_em_4326_e_lido(self):
        shp = escrever_shapefile(self.tmp / "centro", [[quadrado(-51, -1, 0.01)]])

        resultado = load_sector_geometry(shp)

        self.assertEqual(resultado.feature_count, 1)
        self.assertFalse(resultado.features_merged)
        self.assertEqual(resultado.polygon.geom_type, "Polygon")
        # coordinates_lat_lon e o formato que o CRUD de Setor consome.
        primeiro = resultado.coordinates_lat_lon[0]
        self.assertAlmostEqual(primeiro[0], -1, places=5)
        self.assertAlmostEqual(primeiro[1], -51, places=5)

    def test_crs_projetado_e_reprojetado_para_4326(self):
        # SIRGAS 2000 / UTM 22S, o CRS tipico de shapefile do Amapa.
        utm = CRS.from_epsg(31982)
        shp = escrever_shapefile(
            self.tmp / "utm", [[quadrado(500000, 9900000, 1000)]], crs=utm
        )

        resultado = load_sector_geometry(shp)

        self.assertIn("31982", resultado.source_crs.replace(" ", ""))
        min_x, min_y, max_x, max_y = resultado.polygon.bounds
        # Reprojetado: passa a estar em graus, perto do Amapa.
        self.assertTrue(-60 < min_x < -40, f"longitude inesperada: {min_x}")
        self.assertTrue(-5 < min_y < 5, f"latitude inesperada: {min_y}")
        self.assertLess(max_x - min_x, 1)
        self.assertLess(max_y - min_y, 1)

    def test_varias_feicoes_exigem_decisao_explicita(self):
        shp = escrever_shapefile(
            self.tmp / "duplo",
            [[quadrado(-51, -1)], [quadrado(-50, -1)]],
        )

        with self.assertRaises(MultipleFeaturesError) as erro:
            load_sector_geometry(shp)
        self.assertEqual(erro.exception.feature_count, 2)

    def test_feicoes_adjacentes_unidas_viram_um_poligono(self):
        shp = escrever_shapefile(
            self.tmp / "adjacente",
            [[quadrado(-51, -1)], [quadrado(-50, -1)]],
        )

        resultado = load_sector_geometry(shp, merge_features=True)

        self.assertEqual(resultado.polygon.geom_type, "Polygon")
        self.assertEqual(resultado.feature_count, 2)
        self.assertTrue(resultado.features_merged)

    def test_feicoes_separadas_nao_viram_setor(self):
        # Uniao de poligonos desconexos e MultiPolygon, que o Setor nao aceita.
        shp = escrever_shapefile(
            self.tmp / "separado",
            [[quadrado(-51, -1)], [quadrado(-30, -1)]],
        )

        with self.assertRaises(ShapefileImportError) as erro:
            load_sector_geometry(shp, merge_features=True)
        self.assertIn("MultiPolygon", str(erro.exception))

    def test_shapefile_sem_feicao_e_recusado(self):
        shp = escrever_shapefile(self.tmp / "vazio", [])

        with self.assertRaises(ShapefileImportError):
            load_sector_geometry(shp)


# --- seguranca do pacote -----------------------------------------------------


class PacoteSeguroTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _zip_com(self, entradas: dict[str, bytes]) -> Path:
        caminho = self.tmp / "malicioso.zip"
        with zipfile.ZipFile(caminho, "w") as pacote:
            for nome, conteudo in entradas.items():
                pacote.writestr(nome, conteudo)
        return caminho

    def test_path_traversal_nao_escapa_do_destino(self):
        origem = self._zip_com({"../../fora.shp": b"x", "bom.shp": b"y"})
        destino = self.tmp / "destino"
        destino.mkdir()

        import_service.extrair_pacote_zip(origem, destino)

        # A entrada com `../` foi regravada pelo nome-base, dentro do destino.
        self.assertTrue((destino / "fora.shp").is_file())
        self.assertFalse((self.tmp.parent / "fora.shp").exists())
        for produzido in destino.iterdir():
            self.assertEqual(produzido.parent, destino)

    def test_caminho_absoluto_e_reduzido_ao_nome(self):
        origem = self._zip_com({"/etc/passwd.shp": b"x"})
        destino = self.tmp / "destino"
        destino.mkdir()

        import_service.extrair_pacote_zip(origem, destino)

        self.assertTrue((destino / "passwd.shp").is_file())

    def test_extensao_nao_permitida_e_descartada(self):
        origem = self._zip_com({"malware.exe": b"x", "ok.shp": b"y"})
        destino = self.tmp / "destino"
        destino.mkdir()

        import_service.extrair_pacote_zip(origem, destino)

        self.assertFalse((destino / "malware.exe").exists())
        self.assertTrue((destino / "ok.shp").is_file())

    def test_zip_com_entradas_demais_e_recusado(self):
        entradas = {f"a{i}.shp": b"x" for i in range(import_service.MAX_ENTRADAS_ZIP + 1)}
        origem = self._zip_com(entradas)
        destino = self.tmp / "destino"
        destino.mkdir()

        with self.assertRaises(ShapefileImportError):
            import_service.extrair_pacote_zip(origem, destino)

    def test_zip_invalido_nao_vaza_stack(self):
        origem = self.tmp / "quebrado.zip"
        origem.write_bytes(b"nao sou um zip")
        destino = self.tmp / "destino"
        destino.mkdir()

        with self.assertRaises(ShapefileImportError):
            import_service.extrair_pacote_zip(origem, destino)

    def test_limites_do_lote(self):
        with self.assertRaises(Exception):
            import_service.validar_limites_do_lote([])
        with self.assertRaises(Exception):
            import_service.validar_limites_do_lote([1] * (import_service.MAX_ITENS_POR_LOTE + 1))
        with self.assertRaises(Exception):
            import_service.validar_limites_do_lote([import_service.MAX_TAMANHO_POR_ITEM + 1])
        # Dentro do limite nao levanta.
        import_service.validar_limites_do_lote([10, 20, 30])

    def test_raiz_temporaria_e_removida(self):
        with import_service.RaizTemporaria() as raiz:
            self.assertTrue(raiz.is_dir())
            (raiz / "arquivo.txt").write_text("x")
            guardado = raiz
        self.assertFalse(guardado.exists())


# --- API ---------------------------------------------------------------------


def usuario(company_id=COMPANY):
    return SimpleNamespace(
        id=1,
        email="gerente@a",
        company_id=company_id,
        ativo=True,
        perfil=SimpleNamespace(nome="Gerente"),
    )


class ImportacaoApiTests(unittest.TestCase):
    """Schema montado pelo Alembic real.

    DDL escrita a mao foi justamente a divida que a FASE E.2 teve de pagar;
    aqui o banco de teste sai da mesma cadeia de migrations da producao, entao
    coluna nova em `base_eleitoral` ou `territorio_eleitoral` nunca deixa este
    arquivo desatualizado em silencio.
    """

    @classmethod
    def setUpClass(cls):
        cls._dir = TemporaryDirectory()
        cls.db_path = Path(cls._dir.name) / "e4.sqlite"
        run_alembic_upgrade(f"sqlite:///{cls.db_path.as_posix()}")

    @classmethod
    def tearDownClass(cls):
        cls._dir.cleanup()

    def setUp(self):
        self.engine = create_engine(f"sqlite:///{self.db_path.as_posix()}")

        @event.listens_for(self.engine, "connect")
        def _funcoes_espaciais(conexao, _):
            # PostGIS de verdade, calculado por Shapely. Nao sao constantes:
            # os testes de coberto x parcial dependem do calculo real.
            def _geom(valor):
                return shapely_wkt.loads(valor) if valor else None

            # Aridade variavel: o servico chama com 1 argumento e o
            # `crud.create_setor` com 2 (wkt, srid).
            conexao.create_function("ST_GeomFromText", -1, lambda *args: args[0])
            conexao.create_function("ST_SetSRID", 2, lambda v, srid: v)
            conexao.create_function("GeomFromEWKT", 1, lambda v: v)
            conexao.create_function("ST_GeomFromEWKT", 1, lambda v: v)
            # O ORM le colunas Geometry como WKB hexadecimal; aqui o valor
            # gravado e WKT. Devolver None evita a decodificacao -- a geometria
            # persistida e conferida por SQL cru, nao pelo ORM.
            conexao.create_function("AsEWKB", 1, lambda v: None)
            conexao.create_function("ST_AsGeoJSON", 1, lambda v: v)
            conexao.create_function(
                "ST_Covers",
                2,
                lambda a, b: bool(_geom(a).covers(_geom(b))) if a and b else False,
            )
            conexao.create_function(
                "ST_Intersects",
                2,
                lambda a, b: bool(_geom(a).intersects(_geom(b))) if a and b else False,
            )

        self.Session = sessionmaker(bind=self.engine)
        self.addCleanup(self.engine.dispose)

        with self.engine.begin() as conexao:
            for tabela in (
                "setor_territorio_eleitoral",
                "setor_agentes",
                "coletas",
                "setores",
                "projeto_base_eleitoral",
                "territorio_eleitoral",
                "base_eleitoral",
                "pesquisas",
                "projetos",
                "usuarios",
                "perfis",
                "companies",
            ):
                conexao.execute(text(f"DELETE FROM {tabela}"))

            conexao.execute(text(
                f"INSERT INTO companies (id, name, is_active) VALUES ({COMPANY},'A',1),(20,'B',1)"
            ))
            conexao.execute(text("INSERT INTO perfis (id, nome) VALUES (1,'Gerente'),(2,'Agente')"))
            conexao.execute(text(
                "INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id)"
                " VALUES"
                f" (1,'g@a','Gerente A','x',1,1,{COMPANY}),"
                f" (2,'a1@a','Agente 1','x',1,2,{COMPANY}),"
                f" (3,'a2@a','Agente 2','x',1,2,{COMPANY}),"
                " (4,'a@b','Agente B','x',1,2,20)"
            ))
            conexao.execute(text(
                "INSERT INTO projetos (id, nome, status, coordenador_id, company_id)"
                f" VALUES ({PROJETO},'P','Ativo',1,{COMPANY})"
            ))
            conexao.execute(text(
                "INSERT INTO pesquisas (id, titulo, ativo, projeto_id)"
                f" VALUES ({PESQUISA},'Q',1,{PROJETO})"
            ))

        self.db = self.Session()
        self.addCleanup(self.db.close)

        self.app = FastAPI()
        self.app.include_router(setor_importacao.router)
        self.app.dependency_overrides[get_db] = lambda: self.db
        self.app.dependency_overrides[require_manager_or_superadmin] = lambda: usuario()
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)

    # --- fixtures territoriais ---------------------------------------------

    def criar_base(self, base_id=1, principal=True):
        # Colunas nomeadas: o schema vem do Alembic e pode crescer.
        self.db.execute(
            text(
                "INSERT INTO base_eleitoral"
                " (id, nome, ano, uf, fonte, versao, data_referencia, status,"
                "  company_id, criado_por_id)"
                " VALUES (:id, :nome, 2026, 'AP', 'TSE', '1', '2026-01-01',"
                "         'VALIDADA', :company, 1)"
            ),
            {"id": base_id, "nome": f"Base {base_id}", "company": COMPANY},
        )
        self.db.execute(
            text(
                "INSERT INTO projeto_base_eleitoral (projeto_id, base_eleitoral_id, principal)"
                " VALUES (:projeto, :base, :principal)"
            ),
            {"projeto": PROJETO, "base": base_id, "principal": 1 if principal else 0},
        )
        self.db.commit()
        return base_id

    def _criar_territorio(self, territorio_id, nome, tipo, base_id, parent_id, municipio_id, geometria):
        self.db.execute(
            text(
                "INSERT INTO territorio_eleitoral (id, base_eleitoral_id, parent_id, tipo, nome,"
                " nome_normalizado, municipio_id, status_validacao, geometria)"
                " VALUES (:id, :base, :parent, :tipo, :nome, :norm, :municipio, 'VALIDADA', :geom)"
            ),
            {
                "id": territorio_id,
                "base": base_id,
                "parent": parent_id,
                "tipo": tipo,
                "nome": nome,
                "norm": nome.lower(),
                "municipio": municipio_id,
                "geom": geometria,
            },
        )
        self.db.commit()
        return territorio_id

    def criar_municipio(self, territorio_id, nome, base_id=1):
        raiz = self._raiz_estado(base_id)
        return self._criar_territorio(
            territorio_id, nome, "MUNICIPIO", base_id, raiz, None, None
        )

    def criar_bairro(self, territorio_id, nome, municipio_id, geometria=None, base_id=1):
        return self._criar_territorio(
            territorio_id, nome, "BAIRRO", base_id, municipio_id, municipio_id, geometria
        )

    def _raiz_estado(self, base_id):
        """ESTADO e a unica raiz permitida (ck_territorio_raiz)."""
        existente = self.db.execute(
            text(
                "SELECT id FROM territorio_eleitoral"
                " WHERE base_eleitoral_id = :base AND tipo = 'ESTADO'"
            ),
            {"base": base_id},
        ).scalar()
        if existente:
            return existente
        return self._criar_territorio(
            base_id * 10_000, "Amapa", "ESTADO", base_id, None, None, None
        )

    # --- helpers de chamada -------------------------------------------------

    def validar(self, arquivos, unir=None):
        return self.client.post(
            f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/setores/importacao/validar",
            files=[("arquivos", (nome, conteudo, "application/zip")) for nome, conteudo in arquivos],
            data={"unir_features": json.dumps(unir or {})},
        )

    def importar(self, arquivos, itens, projeto_id=PROJETO, pesquisa_id=PESQUISA):
        return self.client.post(
            f"/projetos/{projeto_id}/pesquisas/{pesquisa_id}/setores/importacao",
            files=[("arquivos", (nome, conteudo, "application/zip")) for nome, conteudo in arquivos],
            data={"itens": json.dumps(itens)},
        )

    def contar(self, tabela):
        return self.db.execute(text(f"SELECT COUNT(*) FROM {tabela}")).scalar()

    def item(self, **overrides):
        base = {
            "client_id": "centro.zip",
            "arquivo_index": 0,
            "nome": "Centro",
            "meta": 200,
            "tolerancia_metros": 50,
            "finalidade": "OPERACAO",
            "agente_ids": [],
            "territorio_eleitoral_ids": [],
            "unir_features": False,
        }
        base.update(overrides)
        return base

    # --- validacao ----------------------------------------------------------

    def test_validar_um_arquivo_devolve_preview(self):
        resposta = self.validar([("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.05))])

        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(len(corpo["itens"]), 1)
        item = corpo["itens"][0]
        self.assertTrue(item["valido"])
        self.assertEqual(item["nome_sugerido"], "centro")
        self.assertEqual(item["quantidade_features"], 1)
        self.assertEqual(item["crs_destino"], "EPSG:4326")
        # GeoJSON pronto para o Leaflet.
        self.assertEqual(item["geometria"]["type"], "Polygon")
        self.assertTrue(item["geometria"]["coordinates"][0])
        self.assertIn("min_lat", item["bounds"])

    def test_validar_varios_arquivos(self):
        resposta = self.validar([
            ("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.05)),
            ("trem.zip", pacote_quadrado("trem", -51.0, -0.1, 0.05)),
            ("buritizal.zip", pacote_quadrado("buritizal", -50.9, -0.1, 0.05)),
        ])

        corpo = resposta.json()
        self.assertEqual(len(corpo["itens"]), 3)
        self.assertTrue(all(item["valido"] for item in corpo["itens"]))
        self.assertEqual(
            [item["nome_sugerido"] for item in corpo["itens"]],
            ["centro", "trem", "buritizal"],
        )

    def test_validar_NAO_persiste_nada(self):
        antes = (self.contar("setores"), self.contar("setor_agentes"), self.contar("setor_territorio_eleitoral"))

        self.validar([
            ("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.05)),
            ("trem.zip", pacote_quadrado("trem", -51.0, -0.1, 0.05)),
        ])

        depois = (self.contar("setores"), self.contar("setor_agentes"), self.contar("setor_territorio_eleitoral"))
        self.assertEqual(antes, depois)
        self.assertEqual(depois, (0, 0, 0))

    def test_validar_arquivo_invalido_traz_erro_legivel(self):
        resposta = self.validar([("quebrado.zip", b"isto nao e um zip")])

        item = resposta.json()["itens"][0]
        self.assertFalse(item["valido"])
        self.assertTrue(item["errors"])
        # Mensagem de operador, nunca traceback.
        self.assertNotIn("Traceback", " ".join(item["errors"]))

    def test_validar_pacote_incompleto_traz_erro(self):
        with TemporaryDirectory() as tmp:
            shp = escrever_shapefile(Path(tmp) / "centro", [[quadrado(-51, -1, 0.05)]])
            shp.with_suffix(".prj").unlink()
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as pacote:
                for extensao in (".shp", ".shx", ".dbf"):
                    pacote.write(shp.with_suffix(extensao), shp.with_suffix(extensao).name)
            conteudo = buffer.getvalue()

        item = self.validar([("centro.zip", conteudo)]).json()["itens"][0]
        self.assertFalse(item["valido"])
        self.assertTrue(any(".prj" in erro for erro in item["errors"]))

    def test_varias_feicoes_pedem_confirmacao_e_depois_unem(self):
        with TemporaryDirectory() as tmp:
            shp = escrever_shapefile(
                Path(tmp) / "duplo",
                [[quadrado(-51, -1, 0.05)], [quadrado(-50.95, -1, 0.05)]],
            )
            conteudo = zipar(shp)

        item = self.validar([("duplo.zip", conteudo)]).json()["itens"][0]
        self.assertFalse(item["valido"])
        self.assertTrue(item["exige_uniao"])
        self.assertEqual(item["quantidade_features"], 2)

        # Com a decisao explicita do operador, passa.
        unido = self.validar([("duplo.zip", conteudo)], unir={"duplo.zip": True}).json()["itens"][0]
        self.assertTrue(unido["valido"])
        self.assertTrue(unido["features_unidas"])
        self.assertTrue(any("unidas" in aviso for aviso in unido["warnings"]))

    # --- sugestao territorial -----------------------------------------------

    def test_territorio_totalmente_contido_e_sugerido_e_o_de_fora_nao(self):
        base = self.criar_base()
        macapa = self.criar_municipio(900, "Macapa", base)
        # Dentro do setor (-51.1..-51.0 / -0.1..0.0)
        self.criar_bairro(101, "Centro", macapa, "POLYGON((-51.09 -0.09, -51.09 -0.06, -51.06 -0.06, -51.06 -0.09, -51.09 -0.09))")
        # Longe do setor
        self.criar_bairro(102, "Fora", macapa, "POLYGON((-40 -10, -40 -9, -39 -9, -39 -10, -40 -10))")

        item = self.validar([("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.1))]).json()["itens"][0]

        self.assertEqual(item["territorio_status"], import_service.SUGESTAO_DISPONIVEL)
        sugeridos = [t["id"] for t in item["territorios_sugeridos"]]
        self.assertEqual(sugeridos, [101])
        self.assertNotIn(102, sugeridos)
        self.assertEqual(item["territorios_sugeridos"][0]["criterio"], import_service.CRITERIO_COBERTO)

    def test_territorio_parcialmente_intersectado_nao_vira_sugestao_confirmada(self):
        base = self.criar_base()
        macapa = self.criar_municipio(900, "Macapa", base)
        # Atravessa a borda direita do setor.
        self.criar_bairro(103, "Meio Dentro", macapa, "POLYGON((-51.02 -0.05, -51.02 -0.03, -50.95 -0.03, -50.95 -0.05, -51.02 -0.05))")

        item = self.validar([("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.1))]).json()["itens"][0]

        self.assertEqual([t["id"] for t in item["territorios_sugeridos"]], [])
        parciais = [t["id"] for t in item["territorios_parciais"]]
        self.assertEqual(parciais, [103])
        self.assertEqual(item["territorios_parciais"][0]["criterio"], import_service.CRITERIO_PARCIAL)
        self.assertTrue(any("parcial" in aviso.lower() for aviso in item["warnings"]))

    def test_varios_bairros_do_mesmo_municipio_resolvem_um_municipio(self):
        base = self.criar_base()
        macapa = self.criar_municipio(900, "Macapa", base)
        self.criar_bairro(101, "Centro", macapa, "POLYGON((-51.09 -0.09, -51.09 -0.07, -51.07 -0.07, -51.07 -0.09, -51.09 -0.09))")
        self.criar_bairro(102, "Laguinho", macapa, "POLYGON((-51.06 -0.09, -51.06 -0.07, -51.04 -0.07, -51.04 -0.09, -51.06 -0.09))")

        item = self.validar([("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.1))]).json()["itens"][0]

        self.assertEqual(len(item["territorios_sugeridos"]), 2)
        self.assertEqual(item["municipio_status"], import_service.MUNICIPIO_UNICO)
        self.assertEqual([m["nome"] for m in item["municipios_sugeridos"]], ["Macapa"])

    def test_bairros_de_municipios_diferentes_nao_escolhem_um(self):
        base = self.criar_base()
        macapa = self.criar_municipio(900, "Macapa", base)
        santana = self.criar_municipio(901, "Santana", base)
        self.criar_bairro(101, "Centro", macapa, "POLYGON((-51.09 -0.09, -51.09 -0.07, -51.07 -0.07, -51.07 -0.09, -51.09 -0.09))")
        self.criar_bairro(201, "Provedor", santana, "POLYGON((-51.06 -0.09, -51.06 -0.07, -51.04 -0.07, -51.04 -0.09, -51.06 -0.09))")

        item = self.validar([("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.1))]).json()["itens"][0]

        self.assertEqual(item["municipio_status"], import_service.MUNICIPIO_MULTIPLOS)
        self.assertEqual(len(item["municipios_sugeridos"]), 2)
        self.assertTrue(any("mais de um Municipio" in aviso for aviso in item["warnings"]))

    def test_sem_base_eleitoral_a_composicao_fica_indisponivel_sem_erro(self):
        item = self.validar([("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.05))]).json()["itens"][0]

        # Geometria continua valida: so a composicao e que nao pode ser sugerida.
        self.assertTrue(item["valido"])
        self.assertEqual(item["territorio_status"], import_service.SUGESTAO_SEM_BASE)
        self.assertTrue(any("Base Eleitoral" in aviso for aviso in item["warnings"]))

    def test_base_sem_geometria_territorial_e_sinalizada_explicitamente(self):
        base = self.criar_base()
        macapa = self.criar_municipio(900, "Macapa", base)
        # Exatamente o estado real do DEV hoje: bairros sem poligono.
        self.criar_bairro(101, "Centro", macapa, None)

        item = self.validar([("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.05))]).json()["itens"][0]

        self.assertTrue(item["valido"])
        self.assertEqual(item["territorio_status"], import_service.SUGESTAO_SEM_GEOMETRIA)
        self.assertTrue(any("geometria" in aviso.lower() for aviso in item["warnings"]))

    # --- importacao ---------------------------------------------------------

    def test_importar_um_setor_com_parametros_e_composicao(self):
        base = self.criar_base()
        macapa = self.criar_municipio(900, "Macapa", base)
        self.criar_bairro(101, "Centro", macapa, None)
        self.criar_bairro(102, "Laguinho", macapa, None)

        resposta = self.importar(
            [("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.05))],
            [self.item(agente_ids=[2, 3], territorio_eleitoral_ids=[101, 102])],
        )

        self.assertEqual(resposta.status_code, 200)
        corpo = resposta.json()
        self.assertEqual(corpo["total"], 1)
        self.assertEqual(corpo["importados"], 1)
        self.assertEqual(corpo["falhas"], 0)

        item = corpo["itens"][0]
        self.assertEqual(item["status"], "IMPORTADO")
        setor_id = item["setor_id"]

        setor = self.db.execute(text(
            "SELECT nome, meta, tolerancia, finalidade, geometria FROM setores"
            f" WHERE id={setor_id}"
        )).first()
        # A geometria veio do ARQUIVO reprocessado, nao do GeoJSON do browser.
        self.assertIn("POLYGON", (setor.geometria or "").upper())
        self.assertEqual(setor.nome, "Centro")
        self.assertEqual(setor.meta, 200)
        self.assertEqual(setor.tolerancia, 50)
        self.assertEqual(setor.finalidade, "OPERACAO")

        agentes = self.db.execute(text(
            f"SELECT agente_id FROM setor_agentes WHERE setor_id={setor_id} AND ativo=1 ORDER BY agente_id"
        )).scalars().all()
        self.assertEqual(agentes, [2, 3])

        territorios = self.db.execute(text(
            f"SELECT territorio_eleitoral_id FROM setor_territorio_eleitoral WHERE setor_id={setor_id}"
            " ORDER BY territorio_eleitoral_id"
        )).scalars().all()
        self.assertEqual(territorios, [101, 102])
        self.assertEqual(sorted(item["territorio_eleitoral_ids"]), [101, 102])

    def test_importar_tres_setores_com_parametros_independentes(self):
        base = self.criar_base()
        macapa = self.criar_municipio(900, "Macapa", base)
        self.criar_bairro(101, "Centro", macapa, None)
        self.criar_bairro(102, "Trem", macapa, None)

        resposta = self.importar(
            [
                ("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.05)),
                ("trem.zip", pacote_quadrado("trem", -51.0, -0.1, 0.05)),
                ("buritizal.zip", pacote_quadrado("buritizal", -50.9, -0.1, 0.05)),
            ],
            [
                self.item(client_id="centro", arquivo_index=0, nome="Centro", meta=200,
                          agente_ids=[2, 3], territorio_eleitoral_ids=[101]),
                self.item(client_id="trem", arquivo_index=1, nome="Trem", meta=100,
                          agente_ids=[6 - 4], territorio_eleitoral_ids=[102]),
                self.item(client_id="buritizal", arquivo_index=2, nome="Buritizal", meta=150,
                          agente_ids=[], territorio_eleitoral_ids=[]),
            ],
        )

        corpo = resposta.json()
        self.assertEqual((corpo["total"], corpo["importados"], corpo["falhas"]), (3, 3, 0))

        por_nome = {
            linha.nome: linha
            for linha in self.db.execute(text("SELECT id, nome, meta FROM setores")).all()
        }
        self.assertEqual(por_nome["Centro"].meta, 200)
        self.assertEqual(por_nome["Trem"].meta, 100)
        self.assertEqual(por_nome["Buritizal"].meta, 150)

        # Cada Setor manteve a sua propria composicao e os seus agentes.
        centro_id = por_nome["Centro"].id
        buritizal_id = por_nome["Buritizal"].id
        self.assertEqual(
            self.db.execute(text(
                f"SELECT COUNT(*) FROM setor_agentes WHERE setor_id={centro_id} AND ativo=1"
            )).scalar(),
            2,
        )
        self.assertEqual(
            self.db.execute(text(
                f"SELECT COUNT(*) FROM setor_agentes WHERE setor_id={buritizal_id} AND ativo=1"
            )).scalar(),
            0,
        )
        self.assertEqual(
            self.db.execute(text(
                f"SELECT COUNT(*) FROM setor_territorio_eleitoral WHERE setor_id={buritizal_id}"
            )).scalar(),
            0,
        )

    def test_importar_sem_agente_e_sem_territorio_e_permitido(self):
        resposta = self.importar(
            [("livre.zip", pacote_quadrado("livre", -51.1, -0.1, 0.05))],
            [self.item(client_id="livre", nome="Livre", agente_ids=[], territorio_eleitoral_ids=[])],
        )

        corpo = resposta.json()
        self.assertEqual(corpo["importados"], 1)
        self.assertEqual(self.contar("setores"), 1)
        self.assertEqual(self.contar("setor_territorio_eleitoral"), 0)

    def test_mesmo_agente_em_varios_setores_e_permitido(self):
        resposta = self.importar(
            [
                ("a.zip", pacote_quadrado("a", -51.1, -0.1, 0.05)),
                ("b.zip", pacote_quadrado("b", -51.0, -0.1, 0.05)),
            ],
            [
                self.item(client_id="a", arquivo_index=0, nome="A", agente_ids=[2]),
                self.item(client_id="b", arquivo_index=1, nome="B", agente_ids=[2]),
            ],
        )

        self.assertEqual(resposta.json()["importados"], 2)
        self.assertEqual(
            self.db.execute(text("SELECT COUNT(*) FROM setor_agentes WHERE agente_id=2 AND ativo=1")).scalar(),
            2,
        )

    def test_importacao_individual_usa_o_mesmo_endpoint(self):
        resposta = self.importar(
            [("unico.zip", pacote_quadrado("unico", -51.1, -0.1, 0.05))],
            [self.item(client_id="unico", nome="Unico")],
        )

        self.assertEqual(resposta.status_code, 200)
        self.assertEqual(resposta.json()["total"], 1)
        self.assertEqual(self.contar("setores"), 1)

    # --- seguranca ----------------------------------------------------------

    def test_agente_de_outro_tenant_derruba_o_lote_antes_de_criar(self):
        resposta = self.importar(
            [("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.05))],
            [self.item(agente_ids=[4])],  # agente da empresa 20
        )

        self.assertEqual(resposta.status_code, 404)
        # Validacao previa: nenhum Setor chegou a existir.
        self.assertEqual(self.contar("setores"), 0)

    def test_territorio_de_outra_base_e_rejeitado(self):
        self.criar_base(base_id=1)
        macapa = self.criar_municipio(900, "Macapa", 1)
        self.criar_bairro(101, "Centro", macapa, None, base_id=1)
        # Bairro de uma base que nao e a principal do projeto.
        self.db.execute(text(
            "INSERT INTO base_eleitoral"
            " (id, nome, ano, uf, fonte, versao, data_referencia, status,"
            "  company_id, criado_por_id)"
            f" VALUES (2,'Outra Base',2026,'AP','TSE','2','2026-01-01',"
            f"         'VALIDADA',{COMPANY},1)"
        ))
        # Bairro precisa de pai (ck_territorio_raiz): municipio da propria base 2.
        intruso_municipio = self.criar_municipio(950, "Outro Municipio", base_id=2)
        self.criar_bairro(555, "Intruso", intruso_municipio, None, base_id=2)

        resposta = self.importar(
            [("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.05))],
            [self.item(territorio_eleitoral_ids=[555])],
        )

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(self.contar("setores"), 0)

    def test_territorio_de_tipo_invalido_e_rejeitado(self):
        base = self.criar_base()
        macapa = self.criar_municipio(900, "Macapa", base)

        # MUNICIPIO nao compoe Setor; so BAIRRO.
        resposta = self.importar(
            [("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.05))],
            [self.item(territorio_eleitoral_ids=[macapa])],
        )

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(self.contar("setores"), 0)

    def test_territorio_sem_base_principal_e_rejeitado(self):
        resposta = self.importar(
            [("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.05))],
            [self.item(territorio_eleitoral_ids=[101])],
        )

        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(self.contar("setores"), 0)

    def test_pesquisa_de_outro_projeto_e_404(self):
        resposta = self.importar(
            [("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.05))],
            [self.item()],
            pesquisa_id=9999,
        )

        self.assertEqual(resposta.status_code, 404)

    def test_projeto_de_outro_tenant_e_404(self):
        self.db.execute(text(
            "INSERT INTO projetos (id, nome, status, coordenador_id, company_id)"
            " VALUES (777,'P B','Ativo',1,20)"
        ))
        self.db.execute(text(
            "INSERT INTO pesquisas (id, titulo, ativo, projeto_id) VALUES (7777,'Q B',1,777)"
        ))
        self.db.commit()

        resposta = self.importar(
            [("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.05))],
            [self.item()],
            projeto_id=777,
            pesquisa_id=7777,
        )

        self.assertEqual(resposta.status_code, 404)
        self.assertEqual(self.contar("setores"), 0)

    # --- payload ------------------------------------------------------------

    def test_meta_invalida_e_recusada(self):
        resposta = self.importar(
            [("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.05))],
            [self.item(meta=0)],
        )
        self.assertEqual(resposta.status_code, 422)
        self.assertEqual(self.contar("setores"), 0)

    def test_nome_vazio_e_recusado(self):
        resposta = self.importar(
            [("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.05))],
            [self.item(nome="   ")],
        )
        self.assertEqual(resposta.status_code, 422)

    def test_item_apontando_para_arquivo_inexistente_e_recusado(self):
        resposta = self.importar(
            [("centro.zip", pacote_quadrado("centro", -51.1, -0.1, 0.05))],
            [self.item(arquivo_index=5)],
        )
        self.assertEqual(resposta.status_code, 400)

    def test_arquivo_invalido_vira_ERRO_do_item_sem_derrubar_o_lote(self):
        resposta = self.importar(
            [
                ("bom.zip", pacote_quadrado("bom", -51.1, -0.1, 0.05)),
                ("ruim.zip", b"nao sou zip"),
            ],
            [
                self.item(client_id="bom", arquivo_index=0, nome="Bom"),
                self.item(client_id="ruim", arquivo_index=1, nome="Ruim"),
            ],
        )

        corpo = resposta.json()
        # Nao e 500: erro previsto de um item nao derruba a requisicao.
        self.assertEqual(resposta.status_code, 200)
        self.assertEqual((corpo["total"], corpo["importados"], corpo["falhas"]), (2, 1, 1))

        por_id = {item["client_id"]: item for item in corpo["itens"]}
        self.assertEqual(por_id["bom"]["status"], "IMPORTADO")
        self.assertEqual(por_id["ruim"]["status"], "ERRO")
        self.assertTrue(por_id["ruim"]["detail"])

        # Partial success: o item que deu certo NAO e desfeito.
        self.assertEqual(self.contar("setores"), 1)

    # --- territorios selecionaveis -----------------------------------------

    def test_listagem_de_territorios_para_selecao_manual(self):
        base = self.criar_base()
        macapa = self.criar_municipio(900, "Macapa", base)
        self.criar_bairro(101, "Centro", macapa, None)
        self.criar_bairro(102, "Laguinho", macapa, None)

        resposta = self.client.get(
            f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/setores/importacao/territorios"
        )

        corpo = resposta.json()
        self.assertEqual(corpo["base_eleitoral"]["id"], base)
        self.assertEqual([t["nome"] for t in corpo["territorios"]], ["Centro", "Laguinho"])
        self.assertTrue(all(t["municipio_nome"] == "Macapa" for t in corpo["territorios"]))

    def test_listagem_de_territorios_filtra_por_busca(self):
        base = self.criar_base()
        macapa = self.criar_municipio(900, "Macapa", base)
        self.criar_bairro(101, "Centro", macapa, None)
        self.criar_bairro(102, "Laguinho", macapa, None)

        corpo = self.client.get(
            f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/setores/importacao/territorios",
            params={"busca": "lagu"},
        ).json()

        self.assertEqual([t["nome"] for t in corpo["territorios"]], ["Laguinho"])

    def test_rota_de_territorios_nao_e_sombreada_pelo_router_de_projetos(self):
        """Regressao de ordem de rotas.

        `projetos.py` publica `/setores/{setor_id}/territorios`, e o literal
        "importacao" casa com `{setor_id}`. Montando apenas este router o
        conflito nao aparece -- so no app real, com os dois registrados. O QA em
        DEV pegou isso como 422 (`int_parsing` em setor_id), entao o teste monta
        a mesma ordem do `main.py`.
        """
        from fastapi import FastAPI as _FastAPI

        from pesquisa360.api.endpoints import projetos as rotas_projetos

        app = _FastAPI()
        app.include_router(setor_importacao.router)
        app.include_router(rotas_projetos.router)
        app.dependency_overrides[get_db] = lambda: self.db
        app.dependency_overrides[require_manager_or_superadmin] = lambda: usuario()
        from pesquisa360.core.dependencies import get_current_user

        app.dependency_overrides[get_current_user] = lambda: usuario()

        with TestClient(app) as cliente:
            resposta = cliente.get(
                f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/setores/importacao/territorios"
            )

        self.assertNotEqual(resposta.status_code, 422, resposta.text)
        self.assertEqual(resposta.status_code, 200)
        self.assertIn("territorios", resposta.json())

    def test_listagem_sem_base_devolve_vazio_sem_erro(self):
        corpo = self.client.get(
            f"/projetos/{PROJETO}/pesquisas/{PESQUISA}/setores/importacao/territorios"
        ).json()

        self.assertIsNone(corpo["base_eleitoral"])
        self.assertEqual(corpo["territorios"], [])


if __name__ == "__main__":
    unittest.main()
