"""Camada pura do dominio TSE: parsers, URLs, cliente HTTP e reconciliacao.

Sem banco e sem rede. As fixtures em tests/fixtures_tse/ sao recortes dos
arquivos oficiais do pleito 3220 (e do EA18 do pleito 452, de 2024).
"""

import copy
import json
import unittest
from datetime import datetime
from pathlib import Path

import httpx

from pesquisa360.services.tse import acompanhamento, bu, discovery, ea20, sections
from pesquisa360.services.tse import reconciliation as rec
from pesquisa360.services.tse.client import (
    TseClient, TseHttpError, TseTransientError,
)
from pesquisa360.services.tse.config import TseSettings, load_settings
from pesquisa360.services.tse.normalization import BRASILIA, to_datetime, to_int, to_pct

FIXTURES = Path(__file__).parent / "fixtures_tse"
BASE = "https://resultados.tse.jus.br"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def config_2026():
    return discovery.parse_ea11(fixture("ea11.json"), base_url=BASE, ambiente="oficial",
                                pleito="3220")


class NormalizacaoTest(unittest.TestCase):
    def test_conversoes(self):
        self.assertEqual(to_int("1.234"), 1234)
        self.assertIsNone(to_int(""))
        self.assertEqual(to_pct("12,34"), 12.34)
        self.assertEqual(
            to_datetime("04/10/2026", "17:05:09"),
            datetime(2026, 10, 4, 17, 5, 9, tzinfo=BRASILIA),
        )
        self.assertIsNone(to_datetime("", ""))


class DescobertaTest(unittest.TestCase):
    def test_t01_parse_ea11(self):
        config = config_2026()
        self.assertEqual((config.ciclo, config.pleito, config.data),
                         ("ele2026", "3220", "04/10/2026"))
        self.assertEqual([e.codigo for e in config.eleicoes], ["6257", "6259", "6261"])
        self.assertEqual(config.eleicoes[0].codigo_segundo_turno, "6258")
        self.assertIn("aux", config.templates)

    def test_ea11_pleito_inexistente(self):
        with self.assertRaises(LookupError):
            discovery.parse_ea11(fixture("ea11.json"), base_url=BASE, ambiente="oficial",
                                 pleito="9999")

    def test_t02_cargo_resolve_eleicao(self):
        config = config_2026()
        self.assertEqual(discovery.election_for_cargo(config, "0001").codigo, "6257")
        # Deputado Federal pertence a eleicao ESTADUAL (6259), nao a federal.
        self.assertEqual(discovery.election_for_cargo(config, "0006").codigo, "6259")
        self.assertEqual(discovery.election_for_cargo(config, "6").codigo, "6259")
        with self.assertRaises(LookupError):
            discovery.election_for_cargo(config, "0011")

    def test_urls_derivam_dos_templates(self):
        config = config_2026()
        dados = f"{BASE}/oficial/ele2026/6259/dados"
        self.assertEqual(discovery.ea12_url(config, "6259"),
                         f"{BASE}/oficial/ele2026/6259/config/mun-e006259-cm.json")
        self.assertEqual(discovery.ea14_url(config, "6259"), f"{dados}/br/br-e006259-ab.json")
        self.assertEqual(discovery.ea15_url(config, "6259", "ap"), f"{dados}/ap/ap-e006259-ab.json")
        self.assertEqual(discovery.ea20_url(config, "6259", "ap", "0006"),
                         f"{dados}/ap/ap-c0006-e006259-u.json")
        self.assertEqual(discovery.ea20_url(config, "6259", "ap", "0006", "06050"),
                         f"{dados}/ap/ap06050-c0006-e006259-u.json")
        self.assertEqual(discovery.ea20_url(config, "6259", "ap", "0006", "06050", "0002"),
                         f"{dados}/ap/ap06050-z0002-c0006-e006259-u.json")
        urna = f"{BASE}/oficial/ele2026/arquivo-urna/3220"
        self.assertEqual(discovery.ea16_url(config, "ap"), f"{urna}/config/ap/ap-p003220-cs.json")
        self.assertEqual(
            discovery.ea18_url(config, "ap", "06050", "0002", "0069"),
            f"{urna}/dados/ap/06050/0002/0069/p003220-ap-m06050-z0002-s0069-aux.json")
        self.assertEqual(
            discovery.urna_file_url(config, "ap", "06050", "0002", "0069", "abc", "x-bu.dat"),
            f"{urna}/dados/ap/06050/0002/0069/abc/x-bu.dat")
        with self.assertRaises(ValueError):
            discovery.ea20_url(config, "6259", "ap", "0006", zona="0002")

    def test_ea12_municipios_do_ap(self):
        municipios = discovery.parse_ea12(fixture("ea12_ap.json"), "ap")
        self.assertEqual(len(municipios), 16)
        macapa = discovery.find_municipio(municipios, nome="macapa")
        self.assertEqual((macapa.codigo, macapa.capital, macapa.zonas),
                         ("06050", True, ("0002", "0010", "0014")))
        self.assertEqual(discovery.find_municipio(municipios, codigo="6050"), macapa)
        with self.assertRaises(LookupError):
            discovery.find_municipio(municipios, nome="Inexistente")


class AcompanhamentoTest(unittest.TestCase):
    def test_t03_ea14_fingerprint(self):
        doc = fixture("ea14.json")
        atual = acompanhamento.parse_acompanhamento(doc)
        self.assertEqual(len(atual.linhas), 28)
        prints = atual.fingerprints()
        self.assertEqual(acompanhamento.changed_keys(None, prints), sorted(prints))
        self.assertEqual(acompanhamento.changed_keys(prints, prints), [])

        novo = copy.deepcopy(doc)
        ap = next(a for a in novo["abr"] if a["cdabr"] == "ap")
        ap.update({"and": "p", "dt": "04/10/2026", "ht": "17:20:00"})
        ap["s"]["st"] = "120"
        mudou = acompanhamento.changed_keys(
            prints, acompanhamento.parse_acompanhamento(novo).fingerprints())
        self.assertEqual(mudou, ["uf:ap"])

    def test_t04_ea15_fingerprint(self):
        doc = fixture("ea15_ap.json")
        atual = acompanhamento.parse_acompanhamento(doc)
        self.assertEqual(len([l for l in atual.linhas if l.tipo == "mun"]), 16)
        macapa = next(l for l in atual.linhas if l.codigo == "06050")
        self.assertEqual(macapa.secoes, 1027)

        novo = copy.deepcopy(doc)
        next(a for a in novo["abr"] if a["cdabr"] == "06050")["e"]["c"] = "5000"
        mudou = acompanhamento.changed_keys(
            atual.fingerprints(), acompanhamento.parse_acompanhamento(novo).fingerprints())
        self.assertEqual(mudou, ["mun:06050"])


class Ea20Test(unittest.TestCase):
    def test_t05_normalizacao_uf(self):
        resultado = ea20.parse_ea20(fixture("ea20_ap_c0006.json"))
        self.assertEqual((resultado.tipo_abrangencia, resultado.codigo_abrangencia), ("uf", "ap"))
        self.assertEqual((resultado.eleicao, resultado.cargo, resultado.vagas), ("6259", "0006", 8))
        self.assertEqual(resultado.secoes, 1914)
        self.assertEqual(resultado.eleitorado, 576359)
        self.assertEqual(len(resultado.candidatos), 84)
        self.assertEqual(len({c.sqcand for c in resultado.candidatos}), 84)
        self.assertEqual(len(resultado.federacoes), 3)
        uniao = next(p for p in resultado.partidos if p.numero == "44")
        self.assertEqual(uniao.federacao_numero, "104")
        self.assertIsNone(next(p for p in resultado.partidos if p.numero == "22").federacao_numero)
        self.assertIsNotNone(resultado.gerado_em.tzinfo)

    def test_zona_identifica_apenas_o_numero_da_zona(self):
        resultado = ea20.parse_ea20(fixture("ea20_ap06050_z0002_c0006.json"))
        self.assertEqual((resultado.tipo_abrangencia, resultado.codigo_abrangencia),
                         ("zona", "0002"))
        self.assertEqual(resultado.secoes, 451)

    def test_candidato_por_sqcand_e_numero(self):
        resultado = ea20.parse_ea20(fixture("ea20_ap_c0006.json"))
        alvo = resultado.candidatos[0]
        self.assertEqual(ea20.get_candidate(resultado, sqcand=alvo.sqcand), alvo)
        self.assertEqual(ea20.get_candidate(resultado, numero=alvo.numero), alvo)
        self.assertIsNone(ea20.get_candidate(resultado, sqcand="0"))
        with self.assertRaises(ValueError):
            ea20.get_candidate(resultado)

    def test_votos_com_separador_de_milhar(self):
        doc = fixture("ea20_ap_c0006.json")
        cand = doc["carg"][0]["agr"][0]["par"][0]["cand"][0]
        cand.update({"vap": "12.345", "pvap": "3,21"})
        resultado = ea20.parse_ea20(doc)
        self.assertEqual((resultado.candidatos[0].votos, resultado.candidatos[0].percentual),
                         (12345, 3.21))


class SecoesTest(unittest.TestCase):
    def setUp(self):
        self.config = sections.parse_ea16(fixture("ea16_ap.json"), "ap")
        self.macapa = next(m for m in self.config.municipios if m.codigo == "06050")

    def test_t12_secao_principal(self):
        principal = next(s for s in self.macapa.secoes if s.zona == "0002" and s.secao == "0095")
        self.assertTrue(principal.eh_principal)
        self.assertFalse(principal.eh_agregada)
        self.assertEqual(principal.agregadas, ("0096",))

    def test_t13_secao_agregada(self):
        agregada = next(s for s in self.macapa.secoes if s.zona == "0002" and s.secao == "0096")
        self.assertTrue(agregada.eh_agregada)
        self.assertEqual(agregada.secao_principal, "0095")
        # Toda agregada aponta para uma principal existente que a lista em `nsa`.
        por_chave = {(s.zona, s.secao): s for s in self.macapa.secoes}
        for secao in self.macapa.secoes:
            if secao.eh_agregada:
                principal = por_chave[(secao.zona, secao.secao_principal)]
                self.assertIn(secao.secao, principal.agregadas)

    def test_uf_ausente(self):
        with self.assertRaises(LookupError):
            sections.parse_ea16(fixture("ea16_ap.json"), "sp")

    def test_t14_ea18(self):
        auxiliar = sections.parse_ea18(fixture("ea18_2024.json"))
        self.assertEqual(auxiliar.situacao, "Totalizada")
        self.assertEqual(len(auxiliar.hashes), 1)
        self.assertEqual({a.tipo for a in auxiliar.hashes[0].arquivos},
                         {"bu", "rdv", "log", "vota"})
        hash_, arquivo = sections.current_bu(auxiliar)
        self.assertEqual(arquivo.nome, "o00452ap0605000020001-bu.dat")
        self.assertEqual(hash_.recebido_em, datetime(2024, 10, 6, 18, 4, 9, tzinfo=BRASILIA))

    def test_ea18_sem_hash_totalizado_nao_tem_bu_corrente(self):
        doc = fixture("ea18_2024.json")
        doc["hashes"][0]["st"] = "Rejeitado"
        self.assertIsNone(sections.current_bu(sections.parse_ea18(doc)))


class BuTest(unittest.TestCase):
    def test_sniff_identifica_sequencia_ber_sem_interpretar(self):
        conteudo = bytes([0x30, 0x82, 0x00, 0x05]) + b"\x00" * 5
        self.assertTrue(bu.sniff(conteudo)["asn1_ber_sequence"])
        self.assertFalse(bu.sniff(conteudo + b"\x00")["asn1_ber_sequence"])
        self.assertTrue(bu.sniff(b'{"a": 1}')["json"])


def ponto(ref, votos, hora="17:30:00", secoes=None):
    return rec.VotePoint(ref=ref, votes=votos, idg=f"idg-{ref}",
                         generated_at=to_datetime("04/10/2026", hora),
                         last_totalization=to_datetime("04/10/2026", hora),
                         sections_counted=secoes)


class ReconciliacaoTest(unittest.TestCase):
    def test_t16_consistente(self):
        r = rec.reconcile_candidate_votes(
            "sq1", "municipio", ponto("06050", 600, secoes=30),
            [ponto("0002", 100, secoes=10), ponto("0010", 200, secoes=10),
             ponto("0014", 300, secoes=10)], expected_parts=3)
        self.assertEqual((r["status"], r["difference"], r["derived"]["votes"]),
                         (rec.CONSISTENT, 0, 600))
        self.assertEqual(r["official"]["idg"], "idg-06050")

    def test_t17_defasagem_temporal_nao_e_inconsistencia(self):
        r = rec.reconcile_candidate_votes(
            "sq1", "municipio", ponto("06050", 644, hora="17:35:00", secoes=33),
            [ponto("0002", 100, secoes=10), ponto("0010", 200, secoes=10),
             ponto("0014", 300, secoes=10)], expected_parts=3)
        self.assertEqual((r["status"], r["difference"], r["same_window"]),
                         (rec.TEMPORAL_LAG, 44, False))

    def test_mesma_hora_com_secoes_diferentes_e_defasagem(self):
        r = rec.reconcile_candidate_votes(
            "sq1", "municipio", ponto("06050", 644, secoes=33),
            [ponto("0002", 100, secoes=10), ponto("0010", 500, secoes=20)], expected_parts=2)
        self.assertEqual(r["status"], rec.TEMPORAL_LAG)

    def test_diferenca_na_mesma_janela_e_inconsistente(self):
        r = rec.reconcile_candidate_votes(
            "sq1", "municipio", ponto("06050", 644, secoes=30),
            [ponto("0002", 100, secoes=10), ponto("0010", 500, secoes=20)], expected_parts=2)
        self.assertEqual((r["status"], r["difference"]), (rec.INCONSISTENT, 44))

    def test_partes_faltando_sao_dados_insuficientes(self):
        oficial = ponto("06050", 600)
        faltando_zona = rec.reconcile_candidate_votes(
            "sq1", "municipio", oficial, [ponto("0002", 100)], expected_parts=3)
        sem_candidato = rec.reconcile_candidate_votes(
            "sq1", "municipio", oficial, [ponto("0002", 100), ponto("0010", None)])
        for r in (faltando_zona, sem_candidato):
            self.assertEqual(r["status"], rec.INSUFFICIENT_DATA)
            self.assertIsNone(r["derived"]["votes"])
            self.assertIsNone(r["difference"])
        self.assertEqual(sem_candidato["missing_parts"], ["0010"])


class _Relogio:
    """Relogio falso: `sleep` avanca o tempo, sem esperar de verdade."""

    def __init__(self):
        self.agora, self.esperas = 0.0, []

    def clock(self):
        return self.agora

    def sleep(self, segundos):
        self.esperas.append(segundos)
        self.agora += segundos


def cliente(handler, **settings):
    relogio = _Relogio()
    client = TseClient(settings=TseSettings(**settings), transport=httpx.MockTransport(handler),
                       sleep=relogio.sleep, clock=relogio.clock)
    return client, relogio


URL = f"{BASE}/oficial/comum/config/ele-c.json"


class ClienteHttpTest(unittest.TestCase):
    def test_200_devolve_conteudo_e_validadores(self):
        client, _ = cliente(lambda req: httpx.Response(
            200, content=b"{}", headers={"ETag": '"abc"', "Last-Modified": "ontem"}))
        resp = client.get(URL)
        self.assertEqual((resp.status, resp.content, resp.etag, resp.last_modified),
                         (200, b"{}", '"abc"', "ontem"))

    def test_304_envia_validadores_e_nao_traz_conteudo(self):
        vistos = []

        def handler(req):
            vistos.append((req.headers.get("If-None-Match"), req.headers.get("If-Modified-Since")))
            return httpx.Response(304)

        client, _ = cliente(handler)
        resp = client.get(URL, etag='"abc"', last_modified="ontem")
        self.assertTrue(resp.not_modified)
        self.assertIsNone(resp.content)
        self.assertEqual(vistos, [('"abc"', "ontem")])

    def test_404_nao_tem_retry_nem_e_repetido(self):
        chamadas = []
        client, _ = cliente(lambda req: chamadas.append(1) or httpx.Response(404))
        self.assertTrue(client.get(URL).not_found)
        self.assertTrue(client.get(URL).not_found)
        self.assertEqual(len(chamadas), 1)

    def test_429_respeita_retry_after(self):
        respostas = [httpx.Response(429, headers={"Retry-After": "7"}),
                     httpx.Response(200, content=b"ok")]
        client, relogio = cliente(lambda req: respostas.pop(0))
        self.assertEqual(client.get(URL).content, b"ok")
        self.assertIn(7.0, relogio.esperas)

    def test_5xx_tem_backoff_exponencial_e_desiste(self):
        chamadas = []
        client, relogio = cliente(lambda req: chamadas.append(1) or httpx.Response(503),
                                  max_retries=3)
        with self.assertRaises(TseTransientError):
            client.get(URL)
        self.assertEqual(len(chamadas), 4)
        self.assertEqual([e for e in relogio.esperas if e >= 1.0], [1.0, 2.0, 4.0])

    def test_falha_de_transporte_e_transitoria(self):
        respostas = [httpx.ConnectError("sem rede"), httpx.Response(200, content=b"ok")]

        def handler(req):
            item = respostas.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        client, _ = cliente(handler)
        self.assertEqual(client.get(URL).content, b"ok")

    def test_4xx_inesperado_e_erro_definitivo(self):
        chamadas = []
        client, _ = cliente(lambda req: chamadas.append(1) or httpx.Response(403))
        with self.assertRaises(TseHttpError):
            client.get(URL)
        self.assertEqual(len(chamadas), 1)

    def test_limite_de_requisicoes_por_segundo(self):
        client, _ = cliente(lambda req: httpx.Response(200, content=b"x"), requests_per_second=2)
        for i in range(8):
            client.get(f"{URL}?n={i}")
        self.assertLessEqual(client.peak_requests_per_second(), 2)
        inicios = [t for t, _url, _status in client.requests]
        self.assertTrue(all(b - a >= 0.5 for a, b in zip(inicios, inicios[1:])))

    def test_recusa_host_nao_oficial(self):
        client, _ = cliente(lambda req: httpx.Response(200))
        for url in ("https://exemplo.com/x.json", "http://resultados.tse.jus.br/x.json"):
            with self.assertRaises(ValueError):
                client.get(url)


class ConfiguracaoTest(unittest.TestCase):
    def test_defaults_seguros(self):
        settings = load_settings({})
        self.assertEqual((settings.base_url, settings.requests_per_second, settings.max_retries),
                         (BASE, 2.0, 3))

    def test_ambiente_sobrescreve_e_valida(self):
        settings = load_settings({"TSE_REQUESTS_PER_SECOND": "4", "TSE_REQUEST_TIMEOUT": "5",
                                  "TSE_MAX_RETRIES": "1", "TSE_BASE_URL": BASE + "/"})
        self.assertEqual((settings.requests_per_second, settings.request_timeout,
                          settings.max_retries, settings.base_url), (4.0, 5.0, 1, BASE))
        with self.assertRaises(ValueError):
            load_settings({"TSE_REQUESTS_PER_SECOND": "100"})
        with self.assertRaises(ValueError):
            load_settings({"TSE_BASE_URL": "http://resultados.tse.jus.br"})


if __name__ == "__main__":
    unittest.main()
