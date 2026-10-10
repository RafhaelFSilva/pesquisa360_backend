"""Boletim de Urna (BU): decoder, persistencia, ingestao, API por secao,
conferencia BU x EA20 e worker (ADR-094).

Duas bases, sem rede:

* boletins OFICIAIS reais (`tests/fixtures_tse/bu/`, pleito 3220, AP) provam o
  contrato com a especificacao ASN.1 de 2026;
* boletins SINTETICOS, gerados pelo MESMO schema (`tests/tse_bu_builder.py`)
  sobre as fixtures SIMULADO_*, dao os cenarios controlados (A -> B -> A,
  100 + 200 + 300 = 600).

Zona 0002 de Macapa nas fixtures: tres urnas -- 0055 (com as agregadas
0058, 0069, 0070, 0077..0080), 0056 e 0057.
"""

import copy
import hashlib
import json
import logging
import os
import tempfile
import unittest
from pathlib import Path

import httpx
from sqlalchemy import event, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-tse-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.db import models
from pesquisa360.db.models_tse import (
    TseArquivoSecao, TseBoletimUrna, TseBuCargo, TseBuControle, TseBuVoto, TseSecao, TseSnapshot,
    TseTotalizacao,
)
from pesquisa360.services.tse import bu_decoder, bu_reconciliation as conferencia, discovery
from pesquisa360.services.tse.bu_decoder import BuInvalido, decode_bu
from pesquisa360.services.tse.bu_ingestion import BuIngestion, BuScope
from pesquisa360.services.tse.bu_store import BuIncompativel, BuStore
from pesquisa360.services.tse.client import TseClient
from pesquisa360.services.tse.config import (
    TseIngestionSettings, TseSettings, load_ingestion_settings,
)
from pesquisa360.services.tse.ingestion import IngestScope
from pesquisa360.services.tse.normalization import OFICIAL, SIMULADO
from pesquisa360.services.tse.worker import TseIngestorWorker

from tests.test_apuracao_api import _ApiFixture
from tests.test_tse_persistencia import (
    BASE, DADOS, ESCOPO, FIXTURES, SETTINGS, URNA, _TseFixture, fixture,
)
from tests.tse_bu_builder import (
    BRANCO, LEGENDA, MAJORITARIO, NOMINAL, NULO, OFICIAL_FASE, TREINAMENTO_FASE, cargo, corpo,
    ea18, hash_ea18, montar_bu,
)

BU_FIXTURES = FIXTURES / "bu"
BU_0069 = "o03220ap0605000020069-bu.dat"
BU_0014 = "o03220ap0600900010014-bu.dat"
SHA_0069 = "7b88007c975d155ffffacc6aa3ac603542f921ca58ad931b4317b786de0a35ce"
SHA_0014 = "d2b3034951713bf33573e7fbbdd4a70039dcf103562e807cbeeb9a3e37d9105a"
URL_EA16 = f"{URNA}/config/ap/ap-p017801-cs.json"
URL_ZONA_2 = f"{DADOS}/ap/ap06050-z0002-c0006-e021272-u.json"
ELEICAO = 21272
URNAS = ("0055", "0056", "0057")
AGREGADAS_DA_0055 = ("0058", "0069", "0070", "0077", "0078", "0079", "0080")
# Candidatos das fixtures SIMULADO_*: (numero, partido). 8606 e "Anulado sub judice".
C_5801, C_5808, C_8606 = (5801, 58), (5808, 58), (8606, 86)
SQ_5801 = "41609374"


def real(nome: str) -> bytes:
    return (BU_FIXTURES / nome).read_bytes()


def votos_padrao(n5801=100, n5808=40, legenda58=7, legenda69=3, brancos=3, nulos=2,
                 fora=0, sub_judice=0) -> list[tuple]:
    votos = [(NOMINAL, n5801, C_5801[1], C_5801[0]), (NOMINAL, n5808, C_5808[1], C_5808[0]),
             (LEGENDA, legenda58, 58), (LEGENDA, legenda69, 69), (BRANCO, brancos), (NULO, nulos)]
    if fora:
        votos.append((NOMINAL, fora, 99, 9999))        # votavel fora da lista de candidatos
    if sub_judice:
        votos.append((NOMINAL, sub_judice, C_8606[1], C_8606[0]))
    return votos


def bu_da_secao(secao: str, votos=None, **extra) -> bytes:
    votos = votos_padrao() if votos is None else votos
    return montar_bu(secao=int(secao), eleicoes={ELEICAO: [cargo(6, votos)]}, **extra)


class FakeTseBin:
    """Como o FakeTse, mas tambem serve binarios (BU) e status forcados (int)."""

    def __init__(self, arquivos):
        self.arquivos = arquivos
        self.chamadas: list[tuple[str, int]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        doc = self.arquivos.get(url)
        if doc is None or isinstance(doc, int):
            status = 404 if doc is None else doc
            self.chamadas.append((url, status))
            return httpx.Response(status)
        body = corpo(doc)
        etag = f'"{hashlib.md5(body).hexdigest()}"'
        if request.headers.get("If-None-Match") == etag:
            self.chamadas.append((url, 304))
            return httpx.Response(304, headers={"ETag": etag})
        self.chamadas.append((url, 200))
        return httpx.Response(200, content=body, headers={"ETag": etag})


class BuMixin:
    """Prepara tres urnas na zona 0002 e publica EA18/BU no TSE falso."""

    def preparar_urnas(self):
        self.tse = FakeTseBin(self.tse.arquivos)
        ea16 = self.tse.arquivos[URL_EA16]
        for abr in ea16["abr"]:
            for mu in abr["mu"]:
                for zon in mu["zon"]:
                    for sec in zon["sec"]:
                        sec.pop("da", None), sec.pop("ha", None)
                        if (mu["cd"], zon["cd"]) != ("06050", "0002"):
                            continue
                        if sec["ns"] == "0055":
                            sec["nsa"] = list(AGREGADAS_DA_0055)
                        elif sec["ns"] in AGREGADAS_DA_0055:
                            sec["nsp"] = "0055"
        for secao in URNAS:
            self.carimbar(secao, "15:10:00", ingerir=False)
        self.ingerir()
        self.config = discovery.parse_ea11(
            fixture("SIMULADO_ea11.json"), base_url=SETTINGS.base_url,
            ambiente=SETTINGS.ambiente, pleito="17801")

    def _secao_ea16(self, secao: str) -> dict:
        abr = next(a for a in self.tse.arquivos[URL_EA16]["abr"] if a["cd"] == "ap")
        mu = next(m for m in abr["mu"] if m["cd"] == "06050")
        zon = next(z for z in mu["zon"] if z["cd"] == "0002")
        return next(s for s in zon["sec"] if s["ns"] == secao)

    def carimbar(self, secao: str, hora: str, ingerir: bool = True):
        """Muda a data do arquivo auxiliar no EA16: e o gatilho de nova verificacao."""
        self._secao_ea16(secao).update({"da": "29/09/2026", "ha": hora})
        if ingerir:
            self.ingerir()

    def url_ea18(self, secao: str) -> str:
        return discovery.ea18_url(self.config, "ap", "06050", "0002", secao)

    def url_bu(self, secao: str, hash_: str, nome: str) -> str:
        return discovery.urna_file_url(self.config, "ap", "06050", "0002", secao, hash_, nome)

    def publicar(self, secao: str, conteudo: bytes, *, hash_: str | None = None,
                 situacao: str = "Totalizado", nome: str | None = None, fase: str = "s") -> str:
        nome = nome or f"s17801ap060500002{secao}-bu.dat"
        hash_ = hash_ or hash_ea18(conteudo)
        self.tse.arquivos[self.url_ea18(secao)] = ea18(
            conteudo, nome, hash_=hash_, situacao_hash=situacao, fase=fase,
            idg=hash_[:8])
        self.tse.arquivos[self.url_bu(secao, hash_, nome)] = conteudo
        return hash_

    def lote(self, batch_size: int = 50, municipios=("06050",), **extra):
        self.tse.chamadas.clear()
        with TseClient(settings=SETTINGS, transport=httpx.MockTransport(self.tse),
                       sleep=lambda _s: None) as client:
            return BuIngestion(self.session, client, SETTINGS, **extra).run(BuScope(
                origem=SIMULADO, pleito="17801", uf="ap", batch_size=batch_size,
                municipios=municipios))

    def secao(self, numero: str) -> TseSecao:
        return self.session.scalars(select(TseSecao).where(
            TseSecao.origem == SIMULADO, TseSecao.municipio_codigo == "06050",
            TseSecao.zona == "0002", TseSecao.secao == numero)).one()

    def controle(self, numero: str) -> TseBuControle | None:
        self.session.expire_all()
        return BuStore(self.session).controle(self.secao(numero).id)

    def corrente(self, numero: str) -> TseBoletimUrna | None:
        controle = self.controle(numero)
        return self.session.get(TseBoletimUrna, controle.boletim_id) if controle else None

    def baixados(self) -> list[str]:
        return [u for u, s in self.tse.chamadas if u.endswith("-bu.dat") and s == 200]

    def publicar_tres(self, a=100, b=200, c=300, **extra):
        for secao, n in zip(URNAS, (a, b, c)):
            self.publicar(secao, bu_da_secao(secao, votos_padrao(n5801=n, **extra)))
        return self.lote()


class _BuFixture(BuMixin, _TseFixture):
    def setUp(self):
        super().setUp()
        self.preparar_urnas()


# ------------------------------------------------------------------ decoder
class EspecificacaoTest(unittest.TestCase):
    def test_schema_e_o_oficial_de_2026_e_e_compilado_uma_vez(self):
        self.assertEqual(hashlib.sha256(bu_decoder.SPEC_PATH.read_bytes()).hexdigest(),
                         bu_decoder.SPEC_SHA256)
        self.assertIs(bu_decoder._spec(), bu_decoder._spec())
        self.assertEqual(bu_decoder._spec.cache_info().misses, 1)

    def test_schema_adulterado_e_recusado(self):
        original = bu_decoder.SPEC_SHA256
        bu_decoder._spec.cache_clear()
        bu_decoder.SPEC_SHA256 = "0" * 64
        try:
            with self.assertRaises(RuntimeError):
                decode_bu(real(BU_0069))
        finally:
            bu_decoder.SPEC_SHA256 = original
            bu_decoder._spec.cache_clear()

    def test_fixtures_oficiais_preservam_o_sha256_documentado(self):
        leiame = (BU_FIXTURES / "README.md").read_text(encoding="utf-8")
        for nome, sha in ((BU_0069, SHA_0069), (BU_0014, SHA_0014)):
            self.assertEqual(hashlib.sha256(real(nome)).hexdigest(), sha)
            self.assertIn(sha, leiame)
        self.assertIn(bu_decoder.SPEC_SHA256, leiame)


class DecoderOficialTest(unittest.TestCase):
    """BU real da secao 0069 de Macapa (zona 0002), 1o turno de 2026."""

    @classmethod
    def setUpClass(cls):
        cls.bu = decode_bu(real(BU_0069))

    def cargo(self, eleicao: str, codigo: str):
        return next(c for c in self.bu.eleicao(eleicao).cargos if c.codigo == codigo)

    def test_identificacao_da_secao_pleito_e_fase(self):
        bu = self.bu
        self.assertEqual((bu.origem, bu.pleito, bu.tipo), (OFICIAL, "3220", "SECAO"))
        self.assertEqual((bu.municipio, bu.zona, bu.secao, bu.local), ("06050", "0002", "0069", 2313))
        self.assertEqual([e.codigo for e in bu.eleicoes], ["6257", "6259"])

    def test_cargo_majoritario(self):
        presidente = self.cargo("6257", "0001")
        self.assertEqual((presidente.tipo, presidente.constitucional), ("MAJORITARIO", True))
        self.assertEqual((presidente.nominais, presidente.legenda, presidente.brancos,
                          presidente.nulos), (130, 0, 1, 2))
        senador = self.cargo("6259", "0005")
        # Duas vagas: dois votos por eleitor (253 + 6 + 7 = 2 x 133).
        self.assertEqual(senador.nominais + senador.brancos + senador.nulos,
                         2 * senador.comparecimento)
        votos = {v.numero: (v.votos, v.partido) for v in senador.votos if v.tipo == "NOMINAL"}
        self.assertEqual((votos["200"], votos["130"], votos["151"]),
                         ((78, "20"), (41, "13"), (39, "15")))

    def test_cargo_proporcional_com_votos_de_legenda(self):
        federal = self.cargo("6259", "0006")
        self.assertEqual(federal.tipo, "PROPORCIONAL")
        self.assertEqual((federal.nominais, federal.legenda, federal.brancos, federal.nulos),
                         (124, 6, 2, 1))
        legendas = [v for v in federal.votos if v.tipo == "LEGENDA"]
        self.assertEqual(sum(v.votos for v in legendas), 6)
        self.assertTrue(all(v.numero == v.partido for v in legendas))    # legenda = partido
        estadual = self.cargo("6259", "0007")
        self.assertEqual((estadual.nominais, estadual.legenda, estadual.brancos, estadual.nulos),
                         (120, 9, 4, 0))

    def test_comparecimento_e_eleitores_aptos(self):
        self.assertEqual(self.bu.comparecimento, 133)
        for eleicao in self.bu.eleicoes:
            self.assertEqual((eleicao.aptos, eleicao.aptos_secao, eleicao.aptos_tte), (159, 158, 1))
            for c in eleicao.cargos:
                self.assertEqual(c.comparecimento, 133)
                if c.codigo != "0005":
                    self.assertEqual(c.nominais + c.legenda + c.brancos + c.nulos, 133)

    def test_datas_sao_a_hora_local_da_urna_sem_fuso(self):
        bu = self.bu
        self.assertEqual(bu.emitido_em.isoformat(), "2026-10-04T17:10:35")
        self.assertEqual((bu.abertura_em.isoformat(), bu.encerramento_em.isoformat()),
                         ("2026-10-04T08:00:01", "2026-10-04T17:08:41"))
        self.assertIsNone(bu.emitido_em.tzinfo)
        self.assertEqual(bu.urna.carga_em.isoformat(), "2026-09-21T14:20:00")

    def test_metadados_da_urna_e_strings(self):
        urna = self.bu.urna
        self.assertEqual(urna.versao_votacao, "10.23.0.0 - Praia da Barra do Cahy")
        self.assertEqual((urna.tipo_urna, urna.tipo_arquivo, urna.numero_interno), (1, 1, 2353102))
        self.assertEqual(urna.codigo_carga, "627184030813556146690377")
        self.assertEqual(urna.historico_codigos_carga, (urna.codigo_carga,))

    def test_bu_de_urna_com_secao_agregada_identifica_so_a_principal(self):
        """O schema nao lista agregadas: o BU da 0014 (que agrega a 0088) fala so da 0014.

        Os eleitores das duas secoes estao no mesmo boletim; a relacao vem do EA16.
        """
        bu = decode_bu(real(BU_0014))
        self.assertEqual((bu.municipio, bu.zona, bu.secao), ("06009", "0001", "0014"))
        self.assertEqual((bu.comparecimento, bu.eleicoes[0].aptos), (280, 327))
        self.assertFalse(hasattr(bu, "secoes_agregadas"))


class DecoderEntradaInvalidaTest(unittest.TestCase):
    def test_arquivo_truncado(self):
        raw = real(BU_0069)
        for corte in (1, 7, len(raw) // 2, len(raw) - 1):
            with self.assertRaises(BuInvalido, msg=corte):
                decode_bu(raw[:corte])

    def test_bytes_invalidos_vazios_ou_excedentes(self):
        raw = real(BU_0069)
        for caso in (b"", b"\x00" * 64, b"nao e asn1", os.urandom(256), raw + b"\x00",
                     b'{"json": true}'):
            with self.assertRaises(BuInvalido):
                decode_bu(caso)
        with self.assertRaises(BuInvalido):
            decode_bu("texto")

    def test_asn1_valido_mas_de_outra_estrutura(self):
        # SEQUENCE { INTEGER 5 }: BER bem formado, incompativel com o envelope.
        with self.assertRaises(BuInvalido):
            decode_bu(bytes([0x30, 0x03, 0x02, 0x01, 0x05]))
        raw = bytearray(real(BU_0069))
        raw[40:42] = b"\xff\xff"            # corrompe o miolo mantendo o tamanho
        with self.assertRaises(BuInvalido):
            decode_bu(bytes(raw))

    def test_tamanho_maximo(self):
        with self.assertRaises(BuInvalido) as erro:
            decode_bu(b"\x30" + b"\x00" * (bu_decoder.MAX_BU_BYTES + 1))
        self.assertIn("limite", str(erro.exception))

    def test_envelope_que_nao_e_de_bu_ou_esta_cifrado(self):
        base = {"secao": 55, "eleicoes": {ELEICAO: [cargo(6, votos_padrao())]}}
        with self.assertRaises(BuInvalido):
            decode_bu(montar_bu(**base, tipo_envelope=2))                # RDV
        with self.assertRaises(BuInvalido):
            decode_bu(montar_bu(**base, seguranca={
                "idTipoArquivo": 0, "idCriptografia": 1, "idArquivoCD": 0,
                "idArquivoChave": b"\x00"}))
        with self.assertRaises(BuInvalido):
            decode_bu(montar_bu(**base, secao_do_envelope=56))           # envelope x boletim

    def test_boletim_sintetico_e_decodificado_pelo_mesmo_schema(self):
        bu = decode_bu(montar_bu(
            secao=55, versao="10.23.0.0 - Seção de Ação", comparecimento=155,
            eleicoes={ELEICAO: [cargo(6, votos_padrao()), cargo(
                3, [(NOMINAL, 150, 58, 58), (BRANCO, 5)], tipo=MAJORITARIO, ordem=2)]}))
        self.assertEqual((bu.origem, bu.pleito, bu.secao), (SIMULADO, "17801", "0055"))
        self.assertEqual(bu.urna.versao_votacao, "10.23.0.0 - Seção de Ação")
        federal, governador = bu.eleicoes[0].cargos
        self.assertEqual((federal.nominais, federal.legenda, federal.brancos, federal.nulos),
                         (140, 10, 3, 2))
        self.assertEqual((governador.codigo, governador.tipo, governador.nominais),
                         ("0003", "MAJORITARIO", 150))
        self.assertEqual(decode_bu(montar_bu(secao=55, fase=TREINAMENTO_FASE)).origem, "TREINAMENTO")


# -------------------------------------------------------------- persistencia
class PersistenciaTest(_BuFixture):
    def gravar(self, secao: str, conteudo: bytes, hash_: str = "aa" * 8):
        store = BuStore(self.session)
        resposta = type("R", (), {"url": f"https://x/{hash_}/{secao}-bu.dat", "status": 200,
                                  "content": conteudo, "etag": None, "last_modified": None})()
        snapshot, _ = self.repo.save_snapshot(origem=SIMULADO, tipo="BU", response=resposta)
        boletim, criado = store.record_bu(
            self.secao(secao), snapshot, decode_bu(conteudo),
            sha256=hashlib.sha256(conteudo).hexdigest(), tamanho_bytes=len(conteudo),
            hash_ea18=hash_)
        if criado:
            store.marcar(self.secao(secao), "PROCESSADO", boletim=boletim)
        self.session.commit()
        return boletim, criado

    def test_grava_totais_e_votos_ligados_ao_cadastro(self):
        boletim, criado = self.gravar("0055", bu_da_secao("0055", votos_padrao(fora=4)))
        self.assertTrue(criado)
        linha = self.session.scalars(select(TseBuCargo).where(
            TseBuCargo.boletim_id == boletim.id)).one()
        self.assertEqual((linha.votos_nominais, linha.votos_legenda, linha.votos_brancos,
                          linha.votos_nulos, linha.eleitores_aptos, linha.comparecimento),
                         (144, 10, 3, 2, 300, 250))
        votos = {(v.tipo, v.numero): v for v in self.session.scalars(
            select(TseBuVoto).where(TseBuVoto.bu_cargo_id == linha.id))}
        self.assertEqual(votos[("NOMINAL", "5801")].votos, 100)
        self.assertEqual(votos[("NOMINAL", "5801")].candidato_id,
                         self.repo.find_candidato(self.eleicao().id, sqcand=SQ_5801).id)
        self.assertIsNotNone(votos[("LEGENDA", "58")].partido_id)
        # Votavel fora da lista de candidatos: gravado, sem vinculo inventado.
        self.assertIsNone(votos[("NOMINAL", "9999")].candidato_id)
        self.assertIsNone(votos[("NOMINAL", "9999")].partido_id)
        # Brancos e nulos ficam nos totais do cargo, nao como "votaveis".
        self.assertEqual({tipo for tipo, _n in votos}, {"NOMINAL", "LEGENDA"})
        self.assertEqual((boletim.origem, boletim.pleito, boletim.tipo_bu), (SIMULADO, "17801", "SECAO"))

    def test_a_b_a_b_e_idempotente_e_o_corrente_nao_regride(self):
        a = bu_da_secao("0055", votos_padrao(n5801=100))
        b = bu_da_secao("0055", votos_padrao(n5801=101), emissao="20260929T180000")
        boletim_a, criado_a = self.gravar("0055", a, "aa" * 8)
        self.assertTrue(criado_a)
        self.assertEqual(self.gravar("0055", a, "aa" * 8), (boletim_a, False))     # A -> A
        boletim_b, criado_b = self.gravar("0055", b, "bb" * 8)                    # A -> B
        self.assertTrue(criado_b)
        self.assertEqual(self.corrente("0055").id, boletim_b.id)
        antes = self.contagens()
        for conteudo, hash_, esperado in ((a, "aa" * 8, boletim_a), (b, "bb" * 8, boletim_b)):
            boletim, criado = self.gravar("0055", conteudo, hash_)                # -> A -> B
            self.assertEqual((boletim.id, criado), (esperado.id, False))
            self.assertEqual(self.corrente("0055").id, boletim_b.id)              # nao regride
        self.assertEqual(self.contagens(), antes)                                 # nada duplicou
        historico = list(self.session.scalars(select(TseBoletimUrna).where(
            TseBoletimUrna.secao_id == self.secao("0055").id).order_by(TseBoletimUrna.id)))
        self.assertEqual([h.id for h in historico], [boletim_a.id, boletim_b.id])  # historico
        nominal = lambda bol: self.session.scalar(
            select(TseBuCargo.votos_nominais).where(TseBuCargo.boletim_id == bol.id))
        self.assertEqual((nominal(boletim_a), nominal(boletim_b)), (140, 141))

    def test_unique_constraints(self):
        boletim, _ = self.gravar("0055", bu_da_secao("0055"))
        duplicado = TseBoletimUrna(
            origem=SIMULADO, pleito="17801", secao_id=boletim.secao_id,
            snapshot_id=boletim.snapshot_id, sha256=boletim.sha256, tamanho_bytes=1,
            hash_ea18="x", tipo_bu="SECAO", comparecimento=1)
        self.session.add(duplicado)
        with self.assertRaises(IntegrityError):
            self.session.flush()
        self.session.rollback()
        self.session.add(TseBuControle(secao_id=boletim.secao_id, status="PROCESSADO"))
        with self.assertRaises(IntegrityError):
            self.session.flush()
        self.session.rollback()
        self.session.add(TseBuControle(secao_id=self.secao("0056").id, status="TALVEZ"))
        with self.assertRaises(IntegrityError):
            self.session.flush()
        self.session.rollback()

    def test_bu_de_outra_secao_origem_ou_pleito_e_recusado_sem_gravar(self):
        antes = self.contagens()
        casos = {
            "outra secao": bu_da_secao("0056"),
            "fase oficial em secao simulada": bu_da_secao("0055", fase=OFICIAL_FASE),
            "outro pleito": bu_da_secao("0055", pleito=3220),
        }
        for caso, conteudo in casos.items():
            with self.assertRaises(BuIncompativel, msg=caso):
                self.gravar("0055", conteudo)
            self.session.rollback()
        with self.assertRaises(BuIncompativel):       # agregada nao tem BU proprio
            self.gravar("0058", bu_da_secao("0058"))
        self.session.rollback()
        self.assertEqual(self.contagens(), antes)

    def test_votos_sao_religados_quando_o_cadastro_chega_depois(self):
        boletim, _ = self.gravar("0055", bu_da_secao("0055"))
        self.session.execute(TseBuVoto.__table__.update().values(candidato_id=None, partido_id=None))
        self.session.commit()
        self.assertGreater(BuStore(self.session).religar_votos(SIMULADO, "17801", "ap"), 0)
        self.session.commit()
        sem_vinculo = self.session.scalar(select(func.count(TseBuVoto.id)).where(
            TseBuVoto.partido_id.is_(None)))
        self.assertEqual(sem_vinculo, 0)
        self.assertEqual(self.session.scalar(select(func.sum(TseBuVoto.votos))), 150)   # votos intactos


# ------------------------------------------------------------------ ingestao
class IngestaoTest(_BuFixture):
    def test_primeira_ingestao_ea18_download_decoder_e_estado(self):
        self.publicar("0055", bu_da_secao("0055"))
        r = self.lote()
        self.assertEqual(r.contadores(), {
            "bu_discovered": 3, "bu_downloaded": 1, "bu_already_known": 0, "bu_decoded": 1,
            "bu_persisted": 1, "bu_failed": 0, "bu_pending": 2, "restantes": 0})
        self.assertEqual(r.zonas, {("06050", "0002")})
        self.assertEqual(len(self.baixados()), 1)
        self.assertEqual((self.controle("0055").status, self.controle("0056").status),
                         ("PROCESSADO", "AGUARDANDO"))
        self.assertIsNone(self.controle("0056").boletim_id)
        boletim = self.corrente("0055")
        snapshot = self.session.get(TseSnapshot, boletim.snapshot_id)
        self.assertEqual((snapshot.tipo, snapshot.sha256, snapshot.payload_json),
                         ("BU", boletim.sha256, None))
        arquivo = self.session.scalars(select(TseArquivoSecao).where(
            TseArquivoSecao.secao_id == boletim.secao_id,
            TseArquivoSecao.tipo_arquivo == "bu")).one()
        self.assertEqual(arquivo.sha256, boletim.sha256)
        self.assertEqual(boletim.situacao_ea18, "Totalizado")
        self.assertIsNotNone(boletim.recebido_em)

    def test_repeticao_identica_nao_consulta_nem_duplica(self):
        self.publicar_tres()
        antes = self.contagens()
        r = self.lote()
        self.assertEqual((r.bu_discovered, r.bu_downloaded, r.bu_persisted), (0, 0, 0))
        self.assertEqual(self.tse.chamadas, [])            # nenhuma requisicao: incremental
        self.assertEqual(self.contagens(), antes)

    def test_carimbo_novo_com_o_mesmo_arquivo_nao_baixa_de_novo(self):
        self.publicar("0055", bu_da_secao("0055"))
        self.lote()
        antes = self.contar(TseBoletimUrna)
        self.carimbar("0055", "16:00:00")
        r = self.lote()
        self.assertEqual((r.bu_discovered, r.bu_already_known, r.bu_downloaded), (1, 1, 0))
        self.assertEqual(self.baixados(), [])
        self.assertEqual(self.contar(TseBoletimUrna), antes)

    def test_a_b_a_b_pelo_fluxo_do_tse(self):
        a = bu_da_secao("0055", votos_padrao(n5801=100))
        b = bu_da_secao("0055", votos_padrao(n5801=150), emissao="20260929T183000")
        hash_a = self.publicar("0055", a)
        self.lote()
        boletim_a = self.corrente("0055")

        hash_b = self.publicar("0055", b)                  # A -> B: nova versao oficial
        self.carimbar("0055", "16:00:00")
        r = self.lote()
        self.assertEqual((r.bu_downloaded, r.bu_persisted), (1, 1))
        boletim_b = self.corrente("0055")
        self.assertNotEqual(boletim_b.id, boletim_a.id)

        self.publicar("0055", a, hash_=hash_a)             # B -> A: arquivo antigo de novo
        self.carimbar("0055", "16:30:00")
        r = self.lote()
        self.assertEqual((r.bu_downloaded, r.bu_persisted, r.bu_already_known, r.bu_failed),
                         (0, 0, 1, 0))
        self.assertEqual(self.corrente("0055").id, boletim_b.id)        # nao regride

        self.publicar("0055", b, hash_=hash_b)             # A -> B de novo
        self.carimbar("0055", "17:00:00")
        r = self.lote()
        self.assertEqual((r.bu_downloaded, r.bu_persisted, r.bu_already_known), (0, 0, 1))
        self.assertEqual(self.corrente("0055").id, boletim_b.id)
        self.assertEqual(self.contar(TseBoletimUrna), 2)                # historico preservado
        self.assertEqual(self.contar(TseBuControle), 3)                 # 0056 e 0057 aguardando

    def test_falha_de_um_bu_nao_derruba_o_lote(self):
        self.publicar("0055", bu_da_secao("0055"))
        self.publicar("0056", bu_da_secao("0056")[:-9])            # truncado
        self.publicar("0057", bu_da_secao("0057"))
        with self.assertLogs("pesquisa360.tse.bu", level=logging.WARNING) as logs:
            r = self.lote()
        self.assertEqual((r.bu_persisted, r.bu_failed, r.bu_downloaded), (2, 1, 3))
        self.assertEqual([e["secao"] for e in r.erros], ["06050/0002/0056"])
        ruim = self.controle("0056")
        self.assertEqual((ruim.status, ruim.tentativas, ruim.boletim_id), ("ERRO", 1, None))
        self.assertIn("BuInvalido", ruim.erro)
        self.assertEqual((self.controle("0055").status, self.controle("0057").status),
                         ("PROCESSADO", "PROCESSADO"))
        # O log identifica a secao e o motivo; nunca carrega o binario.
        self.assertIn("06050/0002/0056", logs.output[0])
        self.assertLess(len(logs.output[0]), 600)

    def test_erro_tem_novas_tentativas_limitadas_e_se_recupera(self):
        self.publicar("0055", bu_da_secao("0055")[:-9])
        for tentativa in (1, 2, 3):
            r = self.lote()
            self.assertEqual((r.bu_failed, self.controle("0055").tentativas), (1, tentativa))
        r = self.lote()                                            # esgotou: sai da fila
        self.assertEqual((r.bu_discovered, self.tse.chamadas), (0, []))
        self.publicar("0055", bu_da_secao("0055"))                 # TSE publica arquivo bom
        self.carimbar("0055", "16:00:00")
        r = self.lote()
        self.assertEqual((r.bu_persisted, r.bu_failed), (1, 0))
        ok = self.controle("0055")
        self.assertEqual((ok.status, ok.tentativas, ok.erro), ("PROCESSADO", 0, None))

    def test_erro_no_meio_faz_rollback_da_secao(self):
        self.publicar("0055", bu_da_secao("0055", pleito=3220))    # decodifica, mas e de outro pleito
        antes = {t: n for t, n in self.contagens().items() if t.startswith("tse_b")}
        r = self.lote()
        self.assertEqual((r.bu_decoded, r.bu_persisted, r.bu_failed), (1, 0, 1))
        depois = self.contagens()
        self.assertEqual((depois["tse_boletins_urna"], depois["tse_bu_cargos"],
                          depois["tse_bu_votos"]),
                         (antes["tse_boletins_urna"], antes["tse_bu_cargos"], antes["tse_bu_votos"]))
        self.assertIn("BuIncompativel", self.controle("0055").erro)
        self.assertEqual(self.contar(TseSnapshot) - self.contar(TseSnapshot), 0)

    def test_recebido_sem_totalizar_404_e_sem_arquivo_ficam_aguardando(self):
        self.publicar("0055", bu_da_secao("0055"), situacao="Recebido")
        self.tse.arquivos[self.url_ea18("0056")] = ea18(None, "x-bu.dat")       # hashes: [{arq: []}]
        r = self.lote()                                                        # 0057: EA18 404
        self.assertEqual((r.bu_pending, r.bu_downloaded, r.bu_failed), (3, 0, 0))
        for secao in URNAS:
            controle = self.controle(secao)
            self.assertEqual((controle.status, controle.boletim_id), ("AGUARDANDO", None))
        self.assertEqual(self.contar(TseBoletimUrna), 0)
        # 'Recebido' vira 'Totalizado': o EA16 muda e a secao volta a fila.
        self.publicar("0055", bu_da_secao("0055"))
        self.carimbar("0055", "16:00:00")
        self.assertEqual(self.lote().bu_persisted, 1)

    def test_secao_aguardando_e_reconsultada_depois_do_intervalo(self):
        self.tse.arquivos[self.url_ea18("0055")] = ea18(bu_da_secao("0055"), "x-bu.dat",
                                                        situacao_hash="Recebido")
        self.assertEqual(self.lote().bu_pending, 3)
        self.assertEqual(self.lote().bu_discovered, 0)             # dentro do intervalo
        self.publicar("0055", bu_da_secao("0055"))                 # totalizou; EA16 nao mudou
        r = self.lote(reverificar_segundos=0)
        self.assertEqual((r.bu_discovered, r.bu_persisted, r.bu_pending), (3, 1, 2))

    def test_nome_e_hash_remotos_nao_viram_caminho(self):
        conteudo = bu_da_secao("0055")
        for nome, hash_ in (("../../../etc/passwd", None), ("a/b-bu.dat", None),
                            ("ok-bu.dat", "../x"), ("ok-bu.dat", "zz;rm")):
            self.publicar("0055", conteudo, nome=nome, hash_=hash_)
            self.carimbar("0055", f"16:{len(nome):02d}:{len(hash_ or ''):02d}")
            r = self.lote()
            self.assertEqual((r.bu_failed, r.bu_downloaded), (1, 0), (nome, hash_))
            self.assertEqual(self.baixados(), [])
        self.assertEqual(self.contar(TseBoletimUrna), 0)

    def test_ea18_de_outro_ambiente_e_erro(self):
        self.publicar("0055", bu_da_secao("0055"), fase="o")
        r = self.lote()
        self.assertEqual((r.bu_failed, r.bu_downloaded), (1, 0))
        self.assertIn("ambiente", self.controle("0055").erro)

    def test_falha_de_rede_no_bu_e_isolada(self):
        hash_ = self.publicar("0055", bu_da_secao("0055"))
        self.tse.arquivos[self.url_bu("0055", hash_, "s17801ap0605000020055-bu.dat")] = 500
        self.publicar("0056", bu_da_secao("0056"))
        r = self.lote()
        self.assertEqual((r.bu_failed, r.bu_persisted), (1, 1))
        self.assertIn("TseTransientError", self.controle("0055").erro)

    def test_lote_limita_as_secoes_e_o_restante_fica_para_depois(self):
        self.publicar_tres_sem_lote()
        r = self.lote(batch_size=2)
        self.assertEqual((r.bu_discovered, r.bu_persisted, r.restantes), (2, 2, 1))
        r = self.lote(batch_size=2)
        self.assertEqual((r.bu_discovered, r.bu_persisted, r.restantes), (1, 1, 0))
        with self.assertRaises(ValueError):
            BuScope(origem=SIMULADO, pleito="17801", uf="ap", batch_size=0)

    def publicar_tres_sem_lote(self):
        for secao in URNAS:
            self.publicar(secao, bu_da_secao(secao))

    def test_arquivo_original_opcional_usa_o_sha256_como_nome(self):
        pasta = Path(tempfile.mkdtemp(prefix="p360-bu-"))
        conteudo = bu_da_secao("0055")
        self.publicar("0055", conteudo)
        self.lote(archive_dir=pasta)
        sha = hashlib.sha256(conteudo).hexdigest()
        destino = pasta / "simulado" / f"{sha}.bu"
        self.assertEqual(destino.read_bytes(), conteudo)
        self.assertEqual([p.name for p in pasta.rglob("*") if p.is_file()], [f"{sha}.bu"])

    def test_situacao_esperado_disponivel_ingerido_pendente(self):
        self.publicar("0055", bu_da_secao("0055"))
        store = BuStore(self.session)
        antes = store.situacao(SIMULADO, "17801", "ap", municipios=("06050",))
        self.assertEqual((antes["disponiveis"], antes["ingeridos"], antes["pendentes"]), (3, 0, 3))
        self.lote()
        depois = store.situacao(SIMULADO, "17801", "ap", municipios=("06050",))
        self.assertEqual((depois["ingeridos"], depois["pendentes"], depois["aguardando"],
                          depois["erro"]), (1, 0, 2, 0))
        self.assertGreater(depois["esperados"], depois["disponiveis"])


class IngestaoOficialTest(BuMixin, _TseFixture):
    """Fluxo completo com arquivos OFICIAIS: EA11/EA12/EA14/EA15/EA16/EA20 + EA18 + BU reais."""

    BASE_OFICIAL = "https://resultados.tse.jus.br"
    AJUSTES = TseSettings(base_url=BASE_OFICIAL, ambiente="oficial", requests_per_second=10)

    def setUp(self):
        super().setUp()
        raiz = f"{self.BASE_OFICIAL}/oficial/ele2026"
        ea16 = fixture("ea16_ap.json")
        abr = next(a for a in ea16["abr"] if a["cd"] == "ap")
        zon = next(z for m in abr["mu"] if m["cd"] == "06050" for z in m["zon"] if z["cd"] == "0002")
        next(s for s in zon["sec"] if s["ns"] == "0069").update({"da": "04/10/2026", "ha": "17:43:34"})
        self.config = discovery.parse_ea11(fixture("ea11.json"), base_url=self.BASE_OFICIAL,
                                           ambiente="oficial", pleito="3220")
        ea18_doc = json.loads((BU_FIXTURES / "ea18_0069.json").read_text(encoding="utf-8"))
        self.tse = FakeTseBin({
            f"{self.BASE_OFICIAL}/oficial/comum/config/ele-c.json": fixture("ea11.json"),
            f"{raiz}/6259/config/mun-e006259-cm.json": fixture("ea12_ap.json"),
            f"{raiz}/6259/dados/br/br-e006259-ab.json": fixture("ea14.json"),
            f"{raiz}/6259/dados/ap/ap-e006259-ab.json": fixture("ea15_ap.json"),
            f"{raiz}/6259/dados/ap/ap-c0006-e006259-u.json": fixture("ea20_ap_c0006.json"),
            f"{raiz}/6259/dados/ap/ap06050-z0002-c0006-e006259-u.json":
                fixture("ea20_ap06050_z0002_c0006.json"),
            f"{raiz}/arquivo-urna/3220/config/ap/ap-p003220-cs.json": ea16,
            discovery.ea18_url(self.config, "ap", "06050", "0002", "0069"): ea18_doc,
            discovery.urna_file_url(self.config, "ap", "06050", "0002", "0069",
                                    ea18_doc["hashes"][0]["hash"], BU_0069): real(BU_0069),
        })
        self.ingerir(IngestScope(pleito="3220", uf="ap", cargos=("0006",), municipios=(),
                                 zonas_de=("06050",), origem=OFICIAL), self.AJUSTES)

    def test_bu_oficial_real_e_ingerido_e_ligado_aos_candidatos(self):
        with TseClient(settings=self.AJUSTES, transport=httpx.MockTransport(self.tse),
                       sleep=lambda _s: None) as client:
            r = BuIngestion(self.session, client, self.AJUSTES).run(
                BuScope(origem=OFICIAL, pleito="3220", uf="ap", municipios=("06050",)))
        self.assertEqual((r.bu_discovered, r.bu_downloaded, r.bu_persisted, r.bu_failed),
                         (1, 1, 1, 0))
        boletim = self.session.scalars(select(TseBoletimUrna)).one()
        self.assertEqual((boletim.sha256, boletim.origem, boletim.pleito, boletim.comparecimento,
                          boletim.local_votacao), (SHA_0069, OFICIAL, "3220", 133, 2313))
        self.assertEqual(boletim.emitido_local.isoformat(), "2026-10-04T17:10:35")
        # Os cinco cargos do boletim, cada um na sua eleicao (6257 e 6259).
        cargos = {c.cargo.codigo: c for c in self.session.scalars(select(TseBuCargo))}
        self.assertEqual(sorted(cargos), ["0001", "0003", "0005", "0006", "0007"])
        federal = cargos["0006"]
        self.assertEqual((federal.votos_nominais, federal.votos_legenda, federal.votos_brancos,
                          federal.votos_nulos, federal.eleitores_aptos, federal.comparecimento),
                         (124, 6, 2, 1, 159, 133))
        # Deputado Federal tem cadastro (EA20 oficial da UF): todo voto nominal fica ligado.
        nominais = list(self.session.scalars(select(TseBuVoto).where(
            TseBuVoto.bu_cargo_id == federal.id, TseBuVoto.tipo == "NOMINAL")))
        self.assertEqual(sum(v.votos for v in nominais), 124)
        self.assertTrue(all(v.candidato_id for v in nominais))
        # Senador nao foi ingerido pelo EA20 nesta base: votos gravados, vinculo pendente.
        senador = list(self.session.scalars(select(TseBuVoto).where(
            TseBuVoto.bu_cargo_id == cargos["0005"].id)))
        self.assertEqual(sum(v.votos for v in senador), 253)
        self.assertTrue(all(v.candidato_id is None for v in senador))


# ----------------------------------------------------------------------- API
class _SecaoApiFixture(BuMixin, _ApiFixture):
    def setUp(self):
        super().setUp()
        self.preparar_urnas()
        self.r = {"uf": "ap", "municipio": "06050", "zona": "0002"}

    def get(self, rota, **params):
        return self.client.get(f"/apuracao/tse/eleicoes/{self.eleicao_id}/{rota}",
                               params={**self.r, **params})

    def ok(self, rota, **params):
        resposta = self.get(rota, **params)
        self.assertEqual(resposta.status_code, 200, resposta.text)
        return resposta.json()


class ApiSecaoTest(_SecaoApiFixture):
    def setUp(self):
        super().setUp()
        self.publicar("0055", bu_da_secao("0055", votos_padrao(fora=4, sub_judice=9)))
        self.publicar("0056", bu_da_secao("0056", votos_padrao(n5801=20, n5808=60)))
        self.lote()                                           # 0057 fica aguardando

    def test_resumo_da_secao_vem_do_bu(self):
        corpo_ = self.ok("resumo", secao="0055")
        self.assertEqual(corpo_["fonte"], "BU")
        self.assertEqual(corpo_["abrangencia"], {
            "tipo": "SECAO", "uf": "ap", "municipio_codigo": "06050", "municipio_nome": "MACAPÁ",
            "zona": "0002", "secao": "0055"})
        self.assertEqual([c["codigo"] for c in corpo_["cargos"]], ["0006"])
        tot = corpo_["cargos"][0]["totalizacao"]
        self.assertEqual((tot["eleitores"], tot["comparecimento"], tot["abstencoes"]), (300, 250, 50))
        self.assertEqual((tot["votos_nominais"], tot["votos_legenda"], tot["votos_validos"],
                          tot["votos_brancos"], tot["votos_nulos"]), (153, 10, 163, 3, 2))
        # Um boletim nao e "1 de 1 secoes": nao ha contagem nem percentual de secoes.
        self.assertEqual((tot["secoes_total"], tot["secoes_totalizadas"], tot["percentual_secoes"],
                          tot["andamento"]), (None, None, None, None))
        secao = corpo_["secao"]
        self.assertEqual((secao["status"], secao["section_exists"], secao["result_available"]),
                         ("BU_DISPONIVEL", True, True))
        self.assertEqual(len(secao["bu"]["sha256"]), 64)
        # A zona continua vindo do EA20, com contagem de secoes.
        zona = self.ok("resumo")
        self.assertEqual(zona["abrangencia"]["tipo"], "ZONA")
        self.assertNotIn("fonte", zona)
        self.assertEqual(zona["cargos"][0]["totalizacao"]["secoes_total"], 395)

    def test_cargo_por_secao_candidatos_percentual_e_ranking(self):
        corpo_ = self.ok("cargos/0006", secao="0055")
        linhas = corpo_["candidatos"]
        self.assertEqual([(l["posicao"], l["numero"], l["votos"]) for l in linhas],
                         [(1, "5801", 100), (2, "5808", 40), (3, "8606", 9), (4, "9999", 4)])
        self.assertEqual(linhas[0]["sqcand"], SQ_5801)
        # Percentual sobre os validos DO BOLETIM (nominais + legenda = 163).
        self.assertEqual(linhas[0]["percentual"], round(100 * 100 / 163, 2))
        self.assertEqual(corpo_["total_candidatos"], 4)
        # Fora da lista de candidatos: aparece pelo numero, sem identidade inventada.
        fora = linhas[3]
        self.assertEqual((fora["sqcand"], fora["nome"]), (None, None))
        self.assertIn("fora da lista", fora["nome_urna"])
        # O boletim nao traz situacao nem eleito.
        self.assertTrue(all(l["situacao"] is None and l["eleito"] is None for l in linhas))
        self.assertEqual(len(self.ok("cargos/0006", secao="0055", limite=2)["candidatos"]), 2)
        outra = self.ok("cargos/0006", secao="0056")["candidatos"]
        self.assertEqual([(l["numero"], l["votos"]) for l in outra[:2]], [("5808", 60), ("5801", 20)])

    def test_nominatas_por_secao_usam_a_legenda_oficial_do_boletim(self):
        corpo_ = self.ok("cargos/0006/nominatas", secao="0055")
        por_sigla = {n["sigla"]: n for n in corpo_["nominatas"]}
        p58 = por_sigla["P 9974"]
        self.assertEqual((p58["votos_nominais"], p58["votos_legenda"], p58["total"]), (140, 7, 147))
        self.assertEqual([c["posicao"] for c in p58["candidatos"]], [1, 2])
        # Partido 69 so tem legenda neste boletim e pertence a federacao 100.
        fed = por_sigla["F 9996"]
        self.assertEqual((fed["tipo"], fed["votos_nominais"], fed["votos_legenda"], fed["total"],
                          fed["candidatos"]), ("FEDERACAO", 0, 3, 3, []))
        self.assertEqual(sum(n["total"] for n in corpo_["nominatas"]),
                         corpo_["totalizacao"]["votos_validos"])
        self.assertEqual(sum(n["votos_legenda"] for n in corpo_["nominatas"]),
                         corpo_["totalizacao"]["votos_legenda"])
        self.assertEqual([n["total"] for n in corpo_["nominatas"]],
                         sorted((n["total"] for n in corpo_["nominatas"]), reverse=True))

    def test_candidato_por_secao(self):
        url = f"/apuracao/tse/candidatos/{SQ_5801}"
        base = {"eleicao_id": self.eleicao_id, "municipio": "06050", "zona": "0002"}
        com = self.client.get(url, params={**base, "secao": "0055"}).json()
        self.assertEqual((com["consolidado"]["votos"], com["consolidado"]["posicao"], com["fonte"]),
                         (100, 1, "BU"))
        # BU ingerido sem o candidato: zero voto (dado), e nao ausencia.
        outro = self.repo.find_candidato(self.eleicao_id, numero="5805", cargo="0006", uf="ap")
        zero = self.client.get(f"/apuracao/tse/candidatos/{outro.sqcand}",
                               params={**base, "secao": "0055"}).json()
        self.assertEqual((zero["consolidado"]["votos"], zero["consolidado"]["posicao"]), (0, None))
        # Sem BU: nao ha consolidado -- nunca zero.
        sem = self.client.get(url, params={**base, "secao": "0057"}).json()
        self.assertEqual((sem["consolidado"], sem["totalizacao"], sem["secao"]["status"]),
                         (None, None, "AGUARDANDO_BU"))

    def test_secao_existente_sem_bu_responde_200_sem_resultado_e_nunca_zero(self):
        for rota in ("resumo", "cargos/0006", "cargos/0006/nominatas"):
            corpo_ = self.ok(rota, secao="0057")
            secao = corpo_["secao"]
            self.assertEqual((secao["section_exists"], secao["result_available"], secao["status"],
                              secao["bu"]), (True, False, "AGUARDANDO_BU", None), rota)
            self.assertEqual(corpo_["abrangencia"]["secao"], "0057")
        self.assertEqual(self.ok("resumo", secao="0057")["cargos"], [])
        cargo_ = self.ok("cargos/0006", secao="0057")
        self.assertEqual((cargo_["totalizacao"], cargo_["candidatos"]), (None, []))
        self.assertNotIn('"votos": 0', self.get("cargos/0006", secao="0057").text)

    def test_secao_com_erro_de_processamento_tem_status_proprio(self):
        self.publicar("0057", bu_da_secao("0057")[:-5])
        self.carimbar("0057", "16:00:00")
        self.lote()
        secao = self.ok("cargos/0006", secao="0057")["secao"]
        self.assertEqual((secao["status"], secao["result_available"]), ("ERRO_PROCESSAMENTO", False))

    def test_secao_inexistente_e_hierarquia_invalida(self):
        for rota in ("resumo", "cargos/0006", "cargos/0006/nominatas"):
            self.assertEqual(self.get(rota, secao="9999").status_code, 404, rota)
        self.assertEqual(self.get("cargos/0006/distribuicao", secao="9999",
                                  candidato=SQ_5801).status_code, 404)
        # Secao de outra zona nao e encontrada nesta.
        self.assertEqual(self.client.get(
            f"/apuracao/tse/eleicoes/{self.eleicao_id}/cargos/0006",
            params={"uf": "ap", "municipio": "06050", "zona": "0010", "secao": "0055"}).status_code,
            404)
        base = f"/apuracao/tse/eleicoes/{self.eleicao_id}"
        for rota in ("resumo", "cargos/0006", "cargos/0006/nominatas"):
            for params in ({"uf": "ap", "secao": "0055"},
                           {"uf": "ap", "municipio": "06050", "secao": "0055"},
                           {"uf": "ap", "zona": "0002", "secao": "0055"}):
                self.assertEqual(self.client.get(f"{base}/{rota}", params=params).status_code,
                                 422, (rota, params))
        self.assertEqual(self.get("resumo", secao="55").status_code, 422)          # formato

    def test_secao_agregada_devolve_o_boletim_da_principal_sem_duplicar(self):
        agregada = self.ok("cargos/0006", secao="0058")
        principal = self.ok("cargos/0006", secao="0055")
        for corpo_, numero, eh_principal in ((agregada, "0058", False), (principal, "0055", True)):
            secao = corpo_["secao"]
            self.assertEqual((secao["numero"], secao["principal"], secao["resultado_agregado"]),
                             (numero, eh_principal, True))
            self.assertEqual(secao["secoes_do_grupo"], ["0055", *AGREGADAS_DA_0055])
        self.assertEqual(agregada["secao"]["secao_principal"], "0055")
        self.assertEqual(agregada["secao"]["bu"]["sha256"], principal["secao"]["bu"]["sha256"])
        self.assertEqual(agregada["candidatos"], principal["candidatos"])
        # Um unico boletim no banco para o grupo inteiro.
        self.assertEqual(self.contar(TseBoletimUrna), 2)
        self.assertEqual(self.ok("cargos/0006", secao="0056")["secao"]["resultado_agregado"], False)

    def test_opcoes_de_secao_trazem_a_situacao_do_bu(self):
        corpo_ = self.ok("territorio")
        self.assertEqual((corpo_["nivel"], corpo_["votos_por_secao_disponiveis"]), ("secoes", True))
        itens = {i["secao"]: i for i in corpo_["itens"]}
        self.assertEqual(len(itens), 10)
        self.assertEqual((itens["0055"]["bu_status"], itens["0055"]["resultado_disponivel"],
                          itens["0055"]["agregadas"], itens["0055"]["resultado_agregado"]),
                         ("BU_DISPONIVEL", True, list(AGREGADAS_DA_0055), True))
        self.assertEqual((itens["0058"]["principal"], itens["0058"]["secao_principal"],
                          itens["0058"]["bu_status"], itens["0058"]["resultado_agregado"]),
                         (False, "0055", "BU_DISPONIVEL", True))
        self.assertEqual((itens["0057"]["bu_status"], itens["0057"]["resultado_disponivel"]),
                         ("AGUARDANDO_BU", False))
        self.assertEqual(itens["0056"]["resultado_agregado"], False)

    def test_leitura_por_secao_nao_grava_nada(self):
        antes = self.contagens()
        for rota, extra in (("resumo", {}), ("cargos/0006", {}), ("cargos/0006/nominatas", {}),
                            ("cargos/0006/distribuicao", {"candidato": SQ_5801}),
                            ("cargos/0006/conferencia-bu", {})):
            params = extra if rota.endswith("conferencia-bu") else {**extra, "secao": "0055"}
            self.assertEqual(self.get(rota, **params).status_code, 200, rota)
        self.assertEqual(self.contagens(), antes)


class DistribuicaoPorSecaoTest(_SecaoApiFixture):
    def setUp(self):
        super().setUp()
        self.publicar("0055", bu_da_secao("0055", votos_padrao(n5801=100, n5808=40)))
        self.publicar("0056", bu_da_secao("0056", votos_padrao(n5801=20, n5808=60, legenda69=5)))
        self.lote()

    def test_zona_abre_por_secao_uma_parte_por_urna(self):
        corpo_ = self.ok("cargos/0006/distribuicao", candidato=SQ_5801)
        self.assertEqual((corpo_["nivel"], corpo_["abrangencia"]["tipo"], corpo_["secao_disponivel"]),
                         ("secoes", "ZONA", True))
        self.assertEqual(corpo_["cobertura"], {"partes": 3, "com_resultado": 2})
        partes = {p["codigo"]: p for p in corpo_["partes"]}
        self.assertEqual(list(partes), list(URNAS))            # so urnas; agregadas no grupo
        self.assertEqual((partes["0055"]["nome"], partes["0055"]["resultado_agregado"],
                          partes["0055"]["secoes_do_grupo"]),
                         ("Seções " + " + ".join(["0055", *AGREGADAS_DA_0055]), True,
                          ["0055", *AGREGADAS_DA_0055]))
        self.assertEqual((partes["0056"]["nome"], partes["0056"]["bu_status"]),
                         ("Seção 0056", "BU_DISPONIVEL"))
        self.assertEqual((partes["0057"]["bu_status"], partes["0057"]["totalizacao"]),
                         ("AGUARDANDO_BU", None))
        item = corpo_["itens"][0]
        linhas = {p["codigo"]: p for p in item["partes"]}
        self.assertEqual((linhas["0055"]["votos"], linhas["0056"]["votos"]), (100, 20))
        # Sem BU: ausencia de dado (null), nunca zero.
        self.assertEqual(linhas["0057"], {"codigo": "0057", "votos": None, "percentual_item": None,
                                          "percentual_parte": None})
        # O total do recorte e o da ZONA (EA20): a soma dos BUs e so "soma das partes".
        zona = self.ok("cargos/0006")
        oficial = next(c["votos"] for c in zona["candidatos"] if c["sqcand"] == SQ_5801)
        self.assertEqual((item["total_votos"], item["soma_das_partes"]), (oficial, 120))
        self.assertEqual(linhas["0055"]["percentual_item"], round(100 * 100 / oficial, 2))
        self.assertEqual(linhas["0055"]["percentual_parte"], round(100 * 100 / 150, 2))
        self.assertEqual(corpo_["totalizacao"]["secoes_total"], 395)       # do EA20 da zona

    def test_nominata_por_secao_soma_nominais_e_legenda_oficial(self):
        corpo_ = self.ok("cargos/0006/distribuicao", partido="58", federacao="100")
        por_id = {i["id"]: {p["codigo"]: p["votos"] for p in i["partes"]} for i in corpo_["itens"]}
        self.assertEqual(por_id["PARTIDO:58"], {"0055": 147, "0056": 87, "0057": None})
        self.assertEqual(por_id["FEDERACAO:100"], {"0055": 3, "0056": 5, "0057": None})

    def test_candidato_sem_voto_numa_urna_com_bu_e_zero(self):
        outro = self.repo.find_candidato(self.eleicao_id, numero="5805", cargo="0006", uf="ap")
        corpo_ = self.ok("cargos/0006/distribuicao", candidato=outro.sqcand)
        self.assertEqual({p["codigo"]: p["votos"] for p in corpo_["itens"][0]["partes"]},
                         {"0055": 0, "0056": 0, "0057": None})

    def test_recorte_de_secao_e_o_nivel_minimo(self):
        corpo_ = self.ok("cargos/0006/distribuicao", secao="0055", candidato=SQ_5801, partido="58")
        self.assertEqual((corpo_["nivel"], corpo_["partes"], corpo_["fonte"],
                          corpo_["abrangencia"]["tipo"]), ("secao", [], "BU", "SECAO"))
        candidato, partido = corpo_["itens"]
        self.assertEqual((candidato["total_votos"], candidato["posicao"], candidato["partes"]),
                         (100, 1, []))
        self.assertEqual(partido["total_votos"], 147)
        sem = self.ok("cargos/0006/distribuicao", secao="0057", candidato=SQ_5801)
        self.assertEqual((sem["itens"][0]["total_votos"], sem["secao"]["status"], sem["totalizacao"]),
                         (None, "AGUARDANDO_BU", None))

    def test_numero_de_consultas_nao_cresce_com_itens_nem_secoes(self):
        consultas = []
        escuta = lambda *_a, **_k: consultas.append(1)
        event.listen(self.engine, "before_cursor_execute", escuta)
        try:
            self.ok("cargos/0006/distribuicao", candidato=SQ_5801, partido="58")   # aquece a sessao
            consultas.clear()
            self.ok("cargos/0006/distribuicao", candidato=SQ_5801, partido="58")
            com_poucos = len(consultas)
            consultas.clear()
            varios = [SQ_5801, "41609381", "41609378", "41609507", "41609505"]
            self.ok("cargos/0006/distribuicao", candidato=varios, partido=["58", "78", "59"],
                    federacao="100")
            com_varios = len(consultas)
            consultas.clear()
            self.publicar("0057", bu_da_secao("0057"))
            self.carimbar("0057", "16:00:00")
            self.assertEqual(self.lote().bu_persisted, 1)
            # O commit do lote expira a sessao: aquece de novo antes de contar.
            self.ok("cargos/0006/distribuicao", candidato=SQ_5801, partido="58")
            consultas.clear()
            self.ok("cargos/0006/distribuicao", candidato=SQ_5801, partido="58")
            com_mais_bus = len(consultas)
        finally:
            event.remove(self.engine, "before_cursor_execute", escuta)
        self.assertEqual(com_poucos, com_varios)
        self.assertEqual(com_poucos, com_mais_bus)
        self.assertLess(com_poucos, 25)


# ---------------------------------------------------------------- conferencia
class ConferenciaTest(_SecaoApiFixture):
    """A = 100, B = 200, C = 300; EA20 da zona = 600."""

    def ea20_da_zona(self, votos_5801=600, legenda58=21, legenda69=9, brancos=9, nulos=6,
                     comparecimento=750, secoes=(3, 3), sub_judice=0):
        doc = copy.deepcopy(fixture("SIMULADO_ea20_ap06050_z0002_c0006.json"))
        for agr in doc["carg"][0]["agr"]:
            for par in agr["par"]:
                par["tvtl"] = {"58": str(legenda58), "69": str(legenda69)}.get(par["n"], "0")
                for cand in par["cand"]:
                    cand["vap"] = {"5801": str(votos_5801), "5808": "120",
                                   "8606": str(sub_judice)}.get(cand["n"], "0")
        doc["s"].update({"ts": str(secoes[0]), "st": str(secoes[1])})
        doc["e"]["c"] = str(comparecimento)
        doc["v"].update({"vb": str(brancos), "tvn": str(nulos), "vl": str(legenda58 + legenda69)})
        doc["idg"] = f"{votos_5801}{nulos}{secoes[1]}{sub_judice}"
        self.tse.arquivos[URL_ZONA_2] = doc
        self.ingerir(type(ESCOPO)(**{**ESCOPO.__dict__, "force": True}))

    def conferir(self):
        return self.ok("cargos/0006/conferencia-bu")["itens"][0]

    def test_match_partial_divergent_e_nada_e_alterado(self):
        self.ea20_da_zona()
        self.publicar("0055", bu_da_secao("0055", votos_padrao(n5801=100)))
        self.publicar("0056", bu_da_secao("0056", votos_padrao(n5801=200)))
        self.lote()
        parcial = self.conferir()                                  # C ausente
        self.assertEqual((parcial["status"], parcial["motivo"]), ("PARTIAL", "BU_FALTANDO"))
        self.assertEqual(parcial["cobertura"], {
            "secoes_principais": 3, "com_bu": 2, "ea20_secoes_total": 3,
            "ea20_secoes_totalizadas": 3})
        self.assertEqual(parcial["campos"], [])                    # sem comparar valores

        self.publicar("0057", bu_da_secao("0057", votos_padrao(n5801=300)))
        self.carimbar("0057", "16:00:00")
        self.lote()
        antes = self.contagens()
        igual = self.conferir()                                    # 100 + 200 + 300 = 600
        self.assertEqual((igual["status"], igual["total_divergencias"]), ("MATCH", 0))
        campos = {c["campo"]: (c["ea20"], c["bu"], c["diferenca"]) for c in igual["campos"]}
        self.assertEqual(campos["votos_dos_candidatos"], (720, 720, 0))
        self.assertEqual((campos["votos_legenda"], campos["votos_brancos"], campos["votos_nulos"],
                          campos["comparecimento"]),
                         ((30, 30, 0), (9, 9, 0), (6, 6, 0), (750, 750, 0)))

        self.ea20_da_zona(votos_5801=601)                          # mesma cobertura, 600 x 601
        diverge = self.conferir()
        self.assertEqual((diverge["status"], diverge["total_divergencias"]), ("DIVERGENT", 1))
        self.assertEqual(diverge["divergencias"], [{
            "tipo": "CANDIDATO", "numero": "5801", "nome": "CANDIDATO 9857", "ea20": 601, "bu": 600}])
        # A conferencia so le: nenhum boletim, voto ou totalizacao foi corrigido.
        depois = self.contagens()
        for tabela in ("tse_boletins_urna", "tse_bu_cargos", "tse_bu_votos", "tse_bu_controle"):
            self.assertEqual(depois[tabela], antes[tabela], tabela)
        self.assertEqual(self.session.scalar(select(func.sum(TseBuVoto.votos)).where(
            TseBuVoto.numero == "5801")), 600)
        total = self.session.scalars(select(TseTotalizacao).order_by(TseTotalizacao.id.desc())).first()
        self.assertEqual(total.snapshot.payload_json["carg"][0]["cd"], "6")
        self.assertTrue(self.ok("cargos/0006/conferencia-bu")["somente_leitura"])

    def test_janelas_diferentes_sao_partial_e_nao_divergencia(self):
        self.ea20_da_zona(votos_5801=450, secoes=(3, 2))           # EA20 ainda com 2 de 3
        self.publicar_tres()
        r = self.conferir()
        self.assertEqual((r["status"], r["motivo"]), ("PARTIAL", "JANELAS_DIFERENTES"))

    def test_sem_ea20_ou_sem_bu_nao_e_comparavel(self):
        self.ea20_da_zona()
        r = self.conferir()
        self.assertEqual((r["status"], r["motivo"]), ("NOT_COMPARABLE", "SEM_BU"))
        zonas = self.client.get(
            f"/apuracao/tse/eleicoes/{self.eleicao_id}/cargos/0006/conferencia-bu",
            params={"uf": "ap", "municipio": "06050"}).json()["itens"]
        self.assertEqual(sorted(z["zona"] for z in zonas), ["0002", "0010", "0014"])
        self.assertTrue(all(z["status"] == "NOT_COMPARABLE" for z in zonas))

    def test_fora_da_lista_entra_nos_nulos_e_sub_judice_fica_no_candidato(self):
        """As duas regras de totalizacao do TSE verificadas com os BUs oficiais do AP."""
        # Cada urna: 2 nulos + 4 votos em votavel fora da lista + 5 em candidato sub judice.
        self.ea20_da_zona(nulos=6 + 12, sub_judice=15, comparecimento=750)
        self.publicar_tres(fora=4, sub_judice=5)
        r = self.conferir()
        self.assertEqual((r["status"], r["total_divergencias"]), ("MATCH", 0), r)
        nulos = next(c for c in r["campos"] if c["campo"] == "votos_nulos")
        self.assertEqual((nulos["ea20"], nulos["bu"], nulos["bu_nulos"], nulos["bu_fora_da_lista"]),
                         (18, 18, 6, 12))
        candidatos = next(c for c in r["campos"] if c["campo"] == "votos_dos_candidatos")
        self.assertEqual((candidatos["ea20"], candidatos["bu"]), (735, 735))   # inclui os 15

    def test_contadores_de_conferencia_do_lote(self):
        self.ea20_da_zona()
        self.publicar_tres()
        contagem = conferencia.conferir_zonas(self.session, SIMULADO, "17801", "ap",
                                              [("06050", "0002")], ("0006", "0003"))
        self.assertEqual(contagem, {"bu_reconciled_match": 1, "bu_reconciled_partial": 0,
                                    "bu_reconciled_divergent": 0})
        self.assertEqual(sum(conferencia.conferir_zonas(
            self.session, SIMULADO, "17801", "ap", [], ("0006",)).values()), 0)


# -------------------------------------------------------------------- worker
class WorkerComBuTest(_BuFixture):
    def setUp(self):
        super().setUp()
        self.tmp = Path(tempfile.mkdtemp(prefix="p360-bu-worker-"))
        self.factory = sessionmaker(bind=self.engine)

    def worker(self, **extra) -> TseIngestorWorker:
        settings = TseIngestionSettings(**{
            "enabled": True, "origem": SIMULADO, "ufs": ("ap",), "cargos": ("0006",),
            "municipios": ("06050",), "zonas_de": ("06050",), "interval_seconds": 15,
            "heartbeat_file": self.tmp / "beat.json", **extra})
        w = TseIngestorWorker(settings, SETTINGS, self.engine, self.factory,
                              transport=httpx.MockTransport(self.tse))
        self.addCleanup(w.lock.release)
        return w

    def ciclo(self, w) -> dict:
        self.tse.chamadas.clear()
        self.assertTrue(w.lock.try_acquire())
        return w.run_cycle()

    def test_bu_desligado_por_padrao_mesmo_com_a_ingestao_ligada(self):
        padrao = load_ingestion_settings({"TSE_INGESTION_ENABLED": "1", "TSE_INGESTION_UFS": "ap"})
        self.assertEqual((padrao.enabled, padrao.bu_enabled, padrao.bu_batch_size,
                          padrao.bu_archive_dir), (True, False, 50, None))
        self.publicar("0055", bu_da_secao("0055"))
        registro = self.ciclo(self.worker())
        self.assertNotIn("bu", registro)
        self.assertFalse([u for u, _s in self.tse.chamadas if "-aux.json" in u or "-bu.dat" in u])
        self.assertEqual(self.contar(TseBoletimUrna), 0)

    def test_configuracao_do_bu_por_ambiente(self):
        s = load_ingestion_settings({
            "TSE_INGESTION_ENABLED": "1", "TSE_INGESTION_UFS": "ap",
            "TSE_INGESTION_BU_ENABLED": "true", "TSE_INGESTION_BU_BATCH_SIZE": "120",
            "TSE_INGESTION_BU_MUNICIPIOS": "6050", "TSE_INGESTION_BU_ARCHIVE_DIR": "/tmp/bu"})
        self.assertEqual((s.bu_enabled, s.bu_batch_size, s.bu_municipios, s.bu_archive_dir),
                         (True, 120, ("06050",), Path("/tmp/bu")))
        base = {"TSE_INGESTION_ENABLED": "1", "TSE_INGESTION_UFS": "ap"}
        for extra in ({"TSE_INGESTION_BU_BATCH_SIZE": "0"}, {"TSE_INGESTION_BU_BATCH_SIZE": "9999"},
                      {"TSE_INGESTION_BU_ENABLED": "talvez"},
                      {"TSE_INGESTION_BU_MUNICIPIOS": "macapa"}):
            with self.assertRaises(ValueError, msg=extra):
                load_ingestion_settings({**base, **extra})

    def test_ciclo_processa_em_lotes_e_registra_contadores(self):
        for secao in URNAS:
            self.publicar(secao, bu_da_secao(secao))
        w = self.worker(bu_enabled=True, bu_batch_size=2, bu_municipios=("06050",))
        with self.assertLogs("pesquisa360.tse.worker", level=logging.INFO) as logs:
            primeiro = self.ciclo(w)
        self.assertEqual(primeiro["status"], "ok")
        self.assertEqual({k: primeiro["bu"][k] for k in (
            "bu_discovered", "bu_downloaded", "bu_decoded", "bu_persisted", "bu_failed",
            "bu_pending", "bu_already_known", "restantes")},
            {"bu_discovered": 2, "bu_downloaded": 2, "bu_decoded": 2, "bu_persisted": 2,
             "bu_failed": 0, "bu_pending": 0, "bu_already_known": 0, "restantes": 1})
        for chave in ("bu_reconciled_match", "bu_reconciled_partial", "bu_reconciled_divergent"):
            self.assertIn(chave, primeiro["bu"])
        self.assertEqual(primeiro["bu"]["bu_reconciled_partial"], 1)       # zona incompleta
        linha = next(l for l in logs.output if "tse_ingest_cycle " in l)
        self.assertIn('"bu_persisted": 2', linha)
        self.assertLess(len(linha), 1500)                                  # sem binario no log
        segundo = self.ciclo(w)
        self.assertEqual((segundo["bu"]["bu_persisted"], segundo["bu"]["restantes"]), (1, 0))
        terceiro = self.ciclo(w)                                           # nada pendente
        self.assertEqual((terceiro["bu"]["bu_discovered"], terceiro["bu"]["bu_downloaded"]), (0, 0))
        self.assertFalse([u for u, _s in self.tse.chamadas if "-aux.json" in u or "-bu.dat" in u])
        self.assertEqual(self.contar(TseBoletimUrna), 3)

    def test_bu_invalido_nao_derruba_o_ciclo_nem_o_ea20(self):
        self.publicar("0055", b"\x30\x03\x02\x01\x05")
        self.publicar("0056", bu_da_secao("0056"))
        totalizacoes = self.contar(TseTotalizacao)
        registro = self.ciclo(self.worker(bu_enabled=True))
        self.assertEqual((registro["status"], registro["errors"]), ("ok", []))
        self.assertEqual((registro["bu"]["bu_failed"], registro["bu"]["bu_persisted"]), (1, 1))
        self.assertEqual(self.contar(TseTotalizacao), totalizacoes)


if __name__ == "__main__":
    unittest.main()
