"""Persistencia, historico, idempotencia e ingestao do dominio TSE.

Schema criado pelo Alembic sobre SQLite (mesmo padrao das demais suites). A
"rede" e um `httpx.MockTransport` que serve as fixtures SIMULADO_* com ETag e
responde 304 a GET condicional -- nenhuma requisicao real e feita.
"""

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-tse-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.db import models, models_tse
from pesquisa360.db.models_tse import (
    TseAbrangencia, TseCandidato, TseEleicao, TseResultadoCandidato, TseSecao, TseSnapshot,
    TseTotalizacao,
)
from pesquisa360.services.tse import reconciliation as rec
from pesquisa360.services.tse.client import TseClient
from pesquisa360.services.tse.config import TseSettings
from pesquisa360.services.tse.ingestion import IngestScope, TseIngestion, TseOrigemError
from pesquisa360.services.tse.normalization import OFICIAL, SIMULADO
from pesquisa360.services.tse.repository import TseRepository, abrangencia_key

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures_tse"
REVISAO_TSE = "cd95ebf79450"
ANTERIOR = "be8036a45b28"

BASE = "https://resultados-sim.tse.jus.br/simulado"
SETTINGS = TseSettings(base_url=BASE, ambiente="simulado2026", requests_per_second=10)
DADOS = f"{BASE}/simulado2026/ele2026/21272/dados"
URNA = f"{BASE}/simulado2026/ele2026/arquivo-urna/17801"
URL_EA14 = f"{DADOS}/br/br-e021272-ab.json"
URL_EA15 = f"{DADOS}/ap/ap-e021272-ab.json"
URL_EA20_UF = f"{DADOS}/ap/ap-c0006-e021272-u.json"
URL_EA20_MUN = f"{DADOS}/ap/ap06050-c0006-e021272-u.json"
CANDIDATO = "41609530"
ESCOPO = IngestScope(pleito="17801", uf="ap", cargos=("0006",), municipios=("06050",),
                     zonas_de=("06050",), origem=SIMULADO)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def arquivos_simulados() -> dict[str, dict]:
    arquivos = {
        f"{BASE}/simulado2026/comum/config/ele-c.json": fixture("SIMULADO_ea11.json"),
        f"{BASE}/simulado2026/ele2026/21272/config/mun-e021272-cm.json":
            fixture("SIMULADO_ea12_ap.json"),
        URL_EA14: fixture("SIMULADO_ea14.json"),
        URL_EA15: fixture("SIMULADO_ea15_ap.json"),
        URL_EA20_UF: fixture("SIMULADO_ea20_ap_c0006.json"),
        URL_EA20_MUN: fixture("SIMULADO_ea20_ap06050_c0006.json"),
        f"{URNA}/config/ap/ap-p017801-cs.json": fixture("SIMULADO_ea16_ap.json"),
    }
    for zona in ("0002", "0010", "0014"):
        arquivos[f"{DADOS}/ap/ap06050-z{zona}-c0006-e021272-u.json"] = fixture(
            f"SIMULADO_ea20_ap06050_z{zona}_c0006.json")
    return arquivos


class FakeTse:
    """Serve `arquivos` (url -> dict) com ETag; conteudo pode mudar entre execucoes."""

    def __init__(self, arquivos):
        self.arquivos = arquivos
        self.chamadas: list[tuple[str, int]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url not in self.arquivos:
            self.chamadas.append((url, 404))
            return httpx.Response(404)
        body = json.dumps(self.arquivos[url], ensure_ascii=False).encode("utf-8")
        etag = f'"{hashlib.md5(body).hexdigest()}"'
        if request.headers.get("If-None-Match") == etag:
            self.chamadas.append((url, 304))
            return httpx.Response(304, headers={"ETag": etag})
        self.chamadas.append((url, 200))
        return httpx.Response(200, content=body, headers={"ETag": etag})


def _alembic(database_url: str, *args: str) -> str:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    completed = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=PROJECT_ROOT,
                               env=env, capture_output=True, text=True)
    if completed.returncode != 0:
        raise AssertionError(f"alembic {args} falhou:\n{completed.stdout}\n{completed.stderr}")
    return completed.stdout + completed.stderr


class _TseFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="pesquisa360-tse-"))
        cls.db_path = cls.temp_dir / "tse.db"
        cls.url = f"sqlite:///{cls.db_path.as_posix()}"
        _alembic(cls.url, "upgrade", "head")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self):
        self.engine = create_engine(self.url)

        @event.listens_for(self.engine, "connect")
        def _fk(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")

        self.session = sessionmaker(bind=self.engine)()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        for tabela in reversed(models.Base.metadata.sorted_tables):
            if tabela.name.startswith("tse_"):
                self.session.execute(tabela.delete())
        self.session.commit()
        self.repo = TseRepository(self.session)
        self.tse = FakeTse(arquivos_simulados())

    def ingerir(self, scope=ESCOPO, settings=SETTINGS):
        self.tse.chamadas.clear()
        client = TseClient(settings=settings, transport=httpx.MockTransport(self.tse),
                           sleep=lambda _s: None)
        with client:
            return TseIngestion(self.session, client, settings).run(scope)

    def contar(self, modelo) -> int:
        return self.session.scalar(select(func.count()).select_from(modelo))

    def contagens(self) -> dict[str, int]:
        return {t.name: self.session.scalar(select(func.count()).select_from(t))
                for t in models.Base.metadata.sorted_tables if t.name.startswith("tse_")}

    def eleicao(self) -> TseEleicao:
        return self.repo.get_eleicao(SIMULADO, "17801", "21272")

    def abrangencia(self, municipio=None, zona=None) -> TseAbrangencia:
        return self.repo.get_abrangencia(self.eleicao().id, abrangencia_key("ap", municipio, zona))

    def candidato(self, sqcand=CANDIDATO) -> TseCandidato:
        return self.repo.find_candidato(self.eleicao().id, sqcand=sqcand)

    def votos(self, abrangencia) -> int:
        return next(v["votos"] for v in self.repo.candidate_votes(self.candidato().id)
                    if v["abrangencia_id"] == abrangencia.id)


class MigrationTest(unittest.TestCase):
    def test_upgrade_downgrade_upgrade_so_toca_tabelas_tse(self):
        temp = Path(tempfile.mkdtemp(prefix="pesquisa360-tse-mig-"))
        self.addCleanup(shutil.rmtree, temp, ignore_errors=True)
        url = f"sqlite:///{(temp / 'm.db').as_posix()}"

        def tabelas():
            engine = create_engine(url)
            try:
                with engine.connect() as conn:
                    return {r[0] for r in conn.exec_driver_sql(
                        "SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                engine.dispose()

        _alembic(url, "upgrade", ANTERIOR)
        antes = tabelas()
        self.assertFalse({t for t in antes if t.startswith("tse_")})
        _alembic(url, "upgrade", REVISAO_TSE)
        self.assertIn(REVISAO_TSE, _alembic(url, "current"))
        novas = tabelas() - antes
        self.assertEqual(novas, {t.name for t in models.Base.metadata.sorted_tables
                                 if t.name.startswith("tse_")})
        self.assertNotIn("tse_resultados_secao", novas)   # depende do BU oficial
        _alembic(url, "downgrade", ANTERIOR)
        self.assertEqual(tabelas(), antes)
        _alembic(url, "upgrade", REVISAO_TSE)
        self.assertEqual(tabelas() - antes, novas)
        _alembic(url, "upgrade", "head")
        self.assertEqual(_alembic(url, "heads").count("(head)"), 1)


class IsolamentoGlobalTest(unittest.TestCase):
    def test_t18_dados_tse_nao_dependem_de_company_id(self):
        tabelas = [t for t in models.Base.metadata.sorted_tables if t.name.startswith("tse_")]
        self.assertEqual(len(tabelas), 12)
        for tabela in tabelas:
            self.assertNotIn("company_id", tabela.c, tabela.name)
            for fk in tabela.foreign_keys:
                self.assertTrue(fk.column.table.name.startswith("tse_"),
                                f"{tabela.name} referencia {fk.column.table.name}")
        # E nenhuma tabela de tenant aponta para o dominio TSE.
        for tabela in models.Base.metadata.sorted_tables:
            if not tabela.name.startswith("tse_"):
                self.assertFalse([fk for fk in tabela.foreign_keys
                                  if fk.column.table.name.startswith("tse_")], tabela.name)

    def test_tabela_de_resultado_por_secao_nao_existe(self):
        self.assertNotIn("tse_resultados_secao", models.Base.metadata.tables)
        self.assertFalse(hasattr(models_tse, "TseResultadoSecao"))


class IngestaoTest(_TseFixture):
    def test_ingestao_persiste_catalogo_e_resultados(self):
        report = self.ingerir()
        self.assertEqual(report.totalizacoes_novas, 5)          # UF + Macapa + 3 zonas
        self.assertEqual(report.snapshots_novos, 10)
        self.assertEqual(report.nao_encontrados, [])

        eleicao = self.eleicao()
        self.assertEqual((eleicao.origem, eleicao.ciclo, eleicao.turno),
                         (SIMULADO, "ele2026", 1))
        self.assertEqual(self.contar(TseEleicao), 3)
        tipos = dict(self.session.execute(
            select(TseAbrangencia.tipo, func.count()).group_by(TseAbrangencia.tipo)).all())
        self.assertEqual(tipos, {"BR": 1, "UF": 1, "MUNICIPIO": 16, "ZONA": 3})

    def test_t08_persistencia_de_candidato(self):
        self.ingerir()
        cand = self.candidato()
        self.assertEqual((cand.numero, cand.uf, cand.cargo.codigo, cand.partido.sigla),
                         ("6903", "ap", "0006", "P 9985"))
        self.assertEqual(cand.partido.federacao.numero, "100")
        self.assertEqual(self.contar(TseCandidato), 176)
        por_numero = self.repo.find_candidato(self.eleicao().id, numero="6903", cargo="0006",
                                              uf="ap")
        self.assertEqual(por_numero.id, cand.id)
        with self.assertRaises(ValueError):          # numero sozinho nao identifica
            self.repo.find_candidato(self.eleicao().id, numero="6903")
        with self.assertRaises(IntegrityError):      # sqcand unico por eleicao
            self.session.add(TseCandidato(
                eleicao_id=cand.eleicao_id, cargo_id=cand.cargo_id, uf="ap", sqcand=cand.sqcand,
                numero="1", nome="x", nome_urna="x", partido_id=cand.partido_id))
            self.session.flush()
        self.session.rollback()

    def test_t09_t10_t11_uf_municipio_zona(self):
        self.ingerir()
        self.assertEqual(self.votos(self.abrangencia()), 3115)                      # T09
        macapa = self.abrangencia("06050")
        self.assertEqual((macapa.municipio_nome, macapa.parent_id),
                         ("MACAPÁ", self.abrangencia().id))
        self.assertEqual(self.votos(macapa), 1612)                                  # T10
        zonas = self.repo.candidate_votes(self.candidato().id, parent_id=macapa.id)
        self.assertEqual({z["zona"]: z["votos"] for z in zonas},                    # T11
                         {"0002": 676, "0010": 427, "0014": 509})
        self.assertTrue(all(z["municipio_codigo"] == "06050" for z in zonas))
        total = self.repo.latest_totalizacao(self.abrangencia().id, self.candidato().cargo_id)
        self.assertEqual((total.secoes_totalizadas, total.votos_nominais,
                          total.votos_anulados_sub_judice, total.quociente_eleitoral),
                         (2177, 378662, 84351, 55655))

    def test_candidato_anulado_sub_judice_preserva_destinacao(self):
        self.ingerir()
        linha = next(v for v in self.repo.candidate_votes(self.candidato("41609564").id)
                     if v["tipo"] == "UF")
        self.assertEqual((linha["votos"], linha["destinacao_voto"]), (2588, "Anulado sub judice"))

    def test_ranking_e_partidos(self):
        self.ingerir()
        cargo_id = self.candidato().cargo_id
        ranking = self.repo.ranking(self.abrangencia().id, cargo_id, limit=3)
        self.assertEqual(ranking[0]["sqcand"], CANDIDATO)
        self.assertEqual([r["votos"] for r in ranking], sorted((r["votos"] for r in ranking),
                                                               reverse=True))
        partidos = self.repo.party_totals(self.abrangencia().id, cargo_id)
        self.assertEqual(len(partidos), 25)
        self.assertEqual(sum(p["votos_legenda"] for p in partidos), 66579)
        self.assertEqual({p["federacao"] for p in partidos if p["federacao"]},
                         {"F 9995", "F 9996"})

    def test_t12_t13_secoes_principais_e_agregadas(self):
        self.ingerir()
        secoes = self.repo.secoes(SIMULADO, "17801", "ap", municipio="06009")
        principal = next(s for s in secoes if s.secao == "0027")
        self.assertTrue(principal.eh_principal)
        self.assertIsNotNone(principal.auxiliar_em)
        agregadas = [s for s in secoes if not s.eh_principal]
        self.assertEqual({s.secao for s in agregadas}, {"0090", "0091", "0092", "0093"})
        self.assertEqual({s.secao_principal for s in agregadas}, {"0027"})
        # Urnas = secoes principais; agregada nunca entra na contagem.
        urnas = self.repo.secoes(SIMULADO, "17801", "ap", municipio="06009", principais=True)
        self.assertEqual(len(urnas), len(secoes) - 4)
        with self.assertRaises(IntegrityError):      # principal nao pode apontar para outra
            self.session.add(TseSecao(origem=SIMULADO, pleito="17801", uf="ap",
                                      municipio_codigo="06009", zona="0001", secao="9999",
                                      eh_principal=True, secao_principal="0027"))
            self.session.flush()
        self.session.rollback()

    def test_t14_ea18_sem_arquivos_nao_gera_arquivo_de_secao(self):
        self.tse.arquivos[
            f"{URNA}/dados/ap/06050/0002/0055/p017801-ap-m06050-z0002-s0055-aux.json"
        ] = fixture("SIMULADO_ea18_sem_arquivos.json")
        report = self.ingerir(IngestScope(**{**ESCOPO.__dict__, "secoes_ea18": 1}))
        self.assertEqual(report.arquivos_secao_novos, 0)
        self.assertEqual(self.contar(models_tse.TseArquivoSecao), 0)
        self.assertEqual(self.session.scalar(
            select(func.count()).select_from(TseSnapshot).where(TseSnapshot.tipo == "EA18")), 1)

    def test_ea18_com_arquivos_registra_metadados(self):
        self.ingerir()
        secao = self.repo.secoes(SIMULADO, "17801", "ap", municipio="06050", principais=True)[0]
        snapshot = self.session.scalars(select(TseSnapshot)).first()
        from pesquisa360.services.tse import sections
        auxiliar = sections.parse_ea18(fixture("ea18_2024.json"))
        url_for = lambda hash_, nome: f"{URNA}/x/{hash_}/{nome}"   # noqa: E731
        self.assertEqual(self.repo.record_ea18(secao, snapshot, auxiliar, url_for), 4)
        self.assertEqual(self.repo.record_ea18(secao, snapshot, auxiliar, url_for), 0)
        tipos = set(self.session.scalars(select(models_tse.TseArquivoSecao.tipo_arquivo)))
        self.assertEqual(tipos, {"bu", "rdv", "log", "vota"})


class IdempotenciaEHistoricoTest(_TseFixture):
    def test_t06_t07_reingestao_nao_duplica(self):
        self.ingerir()
        antes = self.contagens()
        segundo = self.ingerir()
        self.assertEqual(self.contagens(), antes)
        self.assertEqual((segundo.snapshots_novos, segundo.totalizacoes_novas), (0, 0))
        self.assertEqual(segundo.ea20_ignorados_sem_mudanca, 5)
        # Sem mudanca no EA14 nenhum EA15/EA20 e sequer requisitado.
        self.assertFalse([u for u, _s in self.tse.chamadas if "-u.json" in u or u == URL_EA15])
        self.assertEqual({s for _u, s in self.tse.chamadas}, {304})

        forcado = self.ingerir(IngestScope(**{**ESCOPO.__dict__, "force": True}))
        self.assertEqual(self.contagens(), antes)
        self.assertEqual((forcado.totalizacoes_novas, forcado.totalizacoes_inalteradas), (0, 5))

    def test_t06_snapshot_idempotente_por_url_e_sha(self):
        self.ingerir()
        snapshot = self.repo.latest_snapshot(URL_EA20_UF)
        self.assertEqual(snapshot.payload_json["idg"], snapshot.idg)
        with self.assertRaises(IntegrityError):
            self.session.add(TseSnapshot(origem=SIMULADO, tipo="EA20", url=snapshot.url,
                                         http_status=200, sha256=snapshot.sha256,
                                         tamanho_bytes=1))
            self.session.flush()
        self.session.rollback()

    def test_arquivo_regerado_sem_mudanca_material_nao_cria_totalizacao(self):
        self.ingerir()
        self.tse.arquivos[URL_EA20_UF].update({"idg": "999999999", "hg": "23:59:59"})
        report = self.ingerir(IngestScope(**{**ESCOPO.__dict__, "force": True}))
        self.assertEqual((report.snapshots_novos, report.totalizacoes_novas), (1, 0))
        self.assertEqual(self.contar(TseTotalizacao), 5)

    def test_historico_append_only_guiado_pelo_ea14(self):
        # Estado 1: apuracao parcial.
        parcial = copy.deepcopy(self.tse.arquivos)
        ap14 = next(a for a in parcial[URL_EA14]["abr"] if a["cdabr"] == "ap")
        ap14.update({"and": "p", "ht": "15:00:00"})
        ap14["s"]["st"] = "1000"
        uf = parcial[URL_EA20_UF]
        uf.update({"and": "p", "tf": "n", "ht": "15:00:00", "idg": "111"})
        uf["s"]["st"] = "1000"
        alvo = next(c for agr in uf["carg"][0]["agr"] for par in agr["par"]
                    for c in par["cand"] if c["sqcand"] == CANDIDATO)
        alvo["vap"] = "1500"
        finais, self.tse.arquivos = self.tse.arquivos, parcial
        self.ingerir()
        abr_uf = self.abrangencia()
        self.assertEqual(self.votos(abr_uf), 1500)
        primeira = self.repo.latest_totalizacao(abr_uf.id, self.candidato().cargo_id)

        # Estado 2: o EA14 muda a UF; o EA15 nao muda nenhum municipio.
        self.tse.arquivos = finais
        report = self.ingerir()
        self.assertEqual(report.abrangencias_alteradas, ["uf:ap"])
        self.assertEqual(report.totalizacoes_novas, 1)
        self.assertEqual(report.ea20_ignorados_sem_mudanca, 4)
        self.assertEqual(self.votos(abr_uf), 3115)

        historico = self.repo.candidate_history(self.candidato().id, abr_uf.id)
        self.assertEqual([(h["votos"], h["secoes_totalizadas"], h["andamento"]) for h in historico],
                         [(1500, 1000, "p"), (3115, 2177, "f")])
        # A primeira totalizacao continua intacta: nada foi atualizado.
        self.session.expire_all()
        antiga = self.session.get(TseTotalizacao, primeira.id)
        self.assertEqual((antiga.idg, antiga.secoes_totalizadas), ("111", 1000))
        self.assertEqual(self.session.scalar(
            select(TseResultadoCandidato.votos).where(
                TseResultadoCandidato.totalizacao_id == primeira.id,
                TseResultadoCandidato.candidato_id == self.candidato().id)), 1500)
        self.assertEqual(self.contar(TseCandidato), 176)        # candidatos nao duplicam

    def test_municipio_alterado_no_ea15_refaz_municipio_e_zonas(self):
        self.ingerir()
        self.tse.arquivos = copy.deepcopy(self.tse.arquivos)
        for url in (URL_EA14, URL_EA15):
            for linha in self.tse.arquivos[url]["abr"]:
                if linha["cdabr"] in ("ap", "06050"):
                    linha["e"]["c"] = str(int(linha["e"]["c"]) + 1)
        self.ingerir()
        pedidos = [u for u, _s in self.tse.chamadas if "-u.json" in u]
        self.assertEqual(len(pedidos), 5)       # UF + Macapa + 3 zonas, todos condicionais
        self.assertEqual(self.contar(TseTotalizacao), 5)        # conteudo igual: sem versao nova


class SeparacaoOficialSimuladoTest(_TseFixture):
    def test_escopo_oficial_recusa_arquivo_simulado(self):
        with self.assertRaises(TseOrigemError):
            self.ingerir(IngestScope(**{**ESCOPO.__dict__, "origem": OFICIAL}))
        self.session.rollback()
        self.assertEqual(sum(self.contagens().values()), 0)

    def test_mesmo_codigo_em_origens_diferentes_nao_colide(self):
        self.ingerir()
        simulada = self.eleicao()
        self.session.add(TseEleicao(origem=OFICIAL, ambiente="oficial", ciclo="ele2026",
                                    pleito=simulada.pleito,
                                    codigo_eleicao=simulada.codigo_eleicao, nome="x", turno=1))
        self.session.flush()
        self.assertIsNone(self.repo.get_eleicao(OFICIAL, "3220", "21272"))
        self.assertEqual(self.repo.get_eleicao(SIMULADO, "17801", "21272").id, simulada.id)
        with self.assertRaises(IntegrityError):
            self.session.add(TseEleicao(origem="TESTE", ambiente="x", ciclo="x", pleito="1",
                                        codigo_eleicao="1", nome="x", turno=1))
            self.session.flush()
        self.session.rollback()

    def test_snapshots_e_secoes_carregam_a_origem(self):
        self.ingerir()
        self.assertEqual(set(self.session.scalars(select(TseSnapshot.origem))), {SIMULADO})
        self.assertEqual(set(self.session.scalars(select(TseSecao.origem))), {SIMULADO})
        self.assertEqual(self.repo.secoes(OFICIAL, "17801", "ap"), [])


class ReconciliacaoPersistidaTest(_TseFixture):
    def test_t16_zonas_x_municipio_consistente(self):
        self.ingerir()
        r = rec.reconcile_abrangencia(self.repo, self.candidato().id, self.abrangencia("06050"))
        self.assertEqual((r["status"], r["official"]["votes"], r["derived"]["votes"]),
                         (rec.CONSISTENT, 1612, 1612))
        self.assertEqual((r["derived"]["parts"], r["derived"]["expected_parts"]), (3, 3))

    def test_uf_com_municipios_faltando_e_dado_insuficiente(self):
        self.ingerir()      # so Macapa foi ingerido; o EA12 cadastrou os 16 municipios
        r = rec.reconcile_abrangencia(self.repo, self.candidato().id, self.abrangencia())
        self.assertEqual(r["status"], rec.INSUFFICIENT_DATA)
        self.assertEqual((r["derived"]["parts"], r["derived"]["expected_parts"]), (1, 16))
        self.assertIsNone(r["derived"]["votes"])

    def test_t17_municipio_a_frente_das_zonas_e_defasagem(self):
        self.ingerir()
        self.tse.arquivos = copy.deepcopy(self.tse.arquivos)
        zona = self.tse.arquivos[f"{DADOS}/ap/ap06050-z0014-c0006-e021272-u.json"]
        zona.update({"and": "p", "ht": "16:00:00"})
        zona["s"]["st"] = "400"
        alvo = next(c for agr in zona["carg"][0]["agr"] for par in agr["par"]
                    for c in par["cand"] if c["sqcand"] == CANDIDATO)
        alvo["vap"] = "480"
        self.ingerir(IngestScope(**{**ESCOPO.__dict__, "force": True}))
        r = rec.reconcile_abrangencia(self.repo, self.candidato().id, self.abrangencia("06050"))
        self.assertEqual((r["status"], r["difference"], r["same_window"]),
                         (rec.TEMPORAL_LAG, 29, False))


if __name__ == "__main__":
    unittest.main()
