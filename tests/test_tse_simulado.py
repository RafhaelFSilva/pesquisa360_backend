"""Contrato dos arquivos EA com votos, sobre fixtures do ambiente de SIMULACAO.

As fixtures `SIMULADO_*` vem do ambiente oficial de simulacao do TSE (pleito
17801, fase "s"): partidos e candidatos sao ficticios ("CANDIDATO 9741").
Servem para validar parser e reconciliacao com votos > 0 -- NAO sao resultado
oficial, e os testes abaixo fixam justamente essa separacao.
"""

import json
import unittest
from pathlib import Path

from pesquisa360.services.tse import acompanhamento, discovery, ea20, sections
from pesquisa360.services.tse import reconciliation as rec
from pesquisa360.services.tse.normalization import OFICIAL, SIMULADO, origem_from_fase

FIXTURES = Path(__file__).parent / "fixtures_tse"
BASE_SIMULADO = "https://resultados-sim.tse.jus.br/simulado"
CANDIDATO = "41609530"      # mais votado do AP no simulado, "Eleito por média"
ANULADO_SUB_JUDICE = "41609564"
MACAPA, ZONAS = "06050", ("0002", "0010", "0014")


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def config_simulado():
    return discovery.parse_ea11(fixture("SIMULADO_ea11.json"), base_url=BASE_SIMULADO,
                                ambiente="simulado2026", pleito="17801")


def ponto(resultado, ref, sqcand=CANDIDATO):
    cand = ea20.get_candidate(resultado, sqcand=sqcand)
    return rec.VotePoint(ref=ref, votes=cand.votos if cand else None, idg=resultado.idg,
                         generated_at=resultado.gerado_em,
                         last_totalization=resultado.ultima_totalizacao,
                         sections_counted=resultado.secoes_totalizadas)


class Ea11SimuladoTest(unittest.TestCase):
    def test_ea11_simulado(self):
        config = config_simulado()
        self.assertEqual((config.fase, config.origem, config.ciclo, config.pleito),
                         ("s", SIMULADO, "ele2026", "17801"))
        self.assertEqual(discovery.election_for_cargo(config, "0006").codigo, "21272")
        self.assertEqual(discovery.election_for_cargo(config, "0001").codigo, "21270")

    def test_urls_do_simulado_saem_dos_mesmos_templates(self):
        config = config_simulado()
        self.assertEqual(
            discovery.ea11_url(BASE_SIMULADO, "simulado2026"),
            f"{BASE_SIMULADO}/simulado2026/comum/config/ele-c.json")
        self.assertEqual(
            discovery.ea20_url(config, "21272", "ap", "0006", MACAPA, "0002"),
            f"{BASE_SIMULADO}/simulado2026/ele2026/21272/dados/ap/"
            "ap06050-z0002-c0006-e021272-u.json")
        self.assertEqual(
            discovery.ea18_url(config, "ap", MACAPA, "0002", "0055"),
            f"{BASE_SIMULADO}/simulado2026/ele2026/arquivo-urna/17801/dados/ap/06050/0002/0055/"
            "p017801-ap-m06050-z0002-s0055-aux.json")


class SeparacaoDeAmbienteTest(unittest.TestCase):
    def test_origem_vem_da_fase_do_payload(self):
        self.assertEqual(origem_from_fase("o"), OFICIAL)
        self.assertEqual(origem_from_fase("s"), SIMULADO)
        for invalida in (None, "", "x"):
            with self.assertRaises(ValueError):
                origem_from_fase(invalida)

    def test_arquivos_oficiais_e_simulados_se_declaram(self):
        self.assertEqual(ea20.parse_ea20(fixture("ea20_ap_c0006.json")).origem, OFICIAL)
        self.assertEqual(ea20.parse_ea20(fixture("SIMULADO_ea20_ap_c0006.json")).origem, SIMULADO)
        oficial = discovery.parse_ea11(fixture("ea11.json"), base_url="https://resultados.tse.jus.br",
                                       ambiente="oficial", pleito="3220")
        self.assertEqual((oficial.origem, config_simulado().origem), (OFICIAL, SIMULADO))

    def test_toda_fixture_simulada_tem_fase_s(self):
        simuladas = sorted(FIXTURES.glob("SIMULADO_*.json"))
        self.assertTrue(simuladas)
        for path in simuladas:
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["f"], "s", path.name)
        for path in sorted(FIXTURES.glob("ea*.json")):
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["f"].casefold(), "o",
                             path.name)


class AcompanhamentoComProgressoTest(unittest.TestCase):
    def test_ea14_com_progresso(self):
        ea14 = acompanhamento.parse_acompanhamento(fixture("SIMULADO_ea14.json"))
        ap = next(l for l in ea14.linhas if l.codigo == "ap")
        self.assertEqual((ap.andamento, ap.secoes, ap.secoes_totalizadas), ("f", 2177, 2177))
        self.assertEqual((ap.eleitorado, ap.comparecimento, ap.abstencao),
                         (628071, 534877, 93194))
        self.assertIsNotNone(ap.ultima_totalizacao)
        self.assertNotEqual(
            ap.fingerprint,
            next(l for l in acompanhamento.parse_acompanhamento(fixture("ea14.json")).linhas
                 if l.codigo == "ap").fingerprint)

    def test_cenario_secao_nao_instalada(self):
        ea14 = acompanhamento.parse_acompanhamento(fixture("SIMULADO_ea14.json"))
        com_nao_instalada = {l.codigo for l in ea14.linhas if l.secoes_nao_instaladas}
        self.assertEqual(com_nao_instalada, {"ba", "br"})

    def test_ea15_municipios_totalizados(self):
        ea15 = acompanhamento.parse_acompanhamento(fixture("SIMULADO_ea15_ap.json"))
        municipios = [l for l in ea15.linhas if l.tipo == "mun"]
        self.assertEqual(len(municipios), 16)
        self.assertTrue(all(l.secoes_totalizadas == l.secoes > 0 for l in municipios))
        self.assertEqual(sum(l.secoes for l in municipios), 2177)


class Ea20ComVotosTest(unittest.TestCase):
    def setUp(self):
        self.uf = ea20.parse_ea20(fixture("SIMULADO_ea20_ap_c0006.json"))
        self.municipio = ea20.parse_ea20(fixture("SIMULADO_ea20_ap06050_c0006.json"))
        self.zonas = {z: ea20.parse_ea20(fixture(f"SIMULADO_ea20_ap06050_z{z}_c0006.json"))
                      for z in ZONAS}

    def test_uf_com_votos(self):
        uf = self.uf
        self.assertEqual((uf.tipo_abrangencia, uf.andamento, uf.totalizacao_final),
                         ("uf", "f", "s"))
        self.assertEqual((uf.secoes_totalizadas, uf.secoes), (2177, 2177))
        self.assertEqual((uf.votos_total, uf.votos_validos, uf.votos_nominais, uf.votos_legenda),
                         (534877, 445241, 378662, 66579))
        self.assertEqual(uf.votos_nominais + uf.votos_legenda, uf.votos_validos)
        self.assertEqual((uf.vagas, uf.quociente_eleitoral), (8, 55655))
        self.assertIsNotNone(uf.ultima_totalizacao)

    def test_candidato_uf_municipio_zona(self):
        cand = ea20.get_candidate(self.uf, sqcand=CANDIDATO)
        self.assertEqual((cand.numero, cand.votos, cand.percentual, cand.situacao, cand.eleito),
                         ("6903", 3115, 0.59, "Eleito por média", "s"))
        self.assertTrue(cand.voto_valido)
        self.assertEqual(self.municipio.tipo_abrangencia, "mu")
        self.assertEqual(ea20.get_candidate(self.municipio, sqcand=CANDIDATO).votos, 1612)
        self.assertEqual({z: ea20.get_candidate(r, sqcand=CANDIDATO).votos
                          for z, r in self.zonas.items()},
                         {"0002": 676, "0010": 427, "0014": 509})

    def test_partido_e_federacao(self):
        cand = ea20.get_candidate(self.uf, sqcand=CANDIDATO)
        partido = next(p for p in self.uf.partidos if p.numero == cand.partido_numero)
        self.assertEqual((partido.federacao_numero, partido.destinacao_voto),
                         ("100", "Válido (legenda)"))
        federacao = next(f for f in self.uf.federacoes if f.numero == "100")
        self.assertIn(partido.numero, federacao.partidos)
        self.assertEqual(sum(p.votos_legenda for p in self.uf.partidos), self.uf.votos_legenda)

    def test_cenario_candidato_anulado_sub_judice(self):
        """Voto de candidato anulado sub judice aparece em `vap` mas nao e valido."""
        anulado = ea20.get_candidate(self.uf, sqcand=ANULADO_SUB_JUDICE)
        self.assertEqual((anulado.destinacao_voto, anulado.votos), ("Anulado sub judice", 2588))
        self.assertFalse(anulado.voto_valido)
        validos = sum(c.votos for c in self.uf.candidatos if c.voto_valido)
        sub_judice = sum(c.votos for c in self.uf.candidatos if not c.voto_valido)
        self.assertEqual(validos, self.uf.votos_nominais)
        self.assertEqual(sub_judice, self.uf.votos_anulados_sub_judice)
        self.assertEqual(sub_judice, 84351)

    def test_reconciliacao_zonas_x_municipio_com_votos(self):
        r = rec.reconcile_candidate_votes(
            CANDIDATO, "municipio", ponto(self.municipio, MACAPA),
            [ponto(r, z) for z, r in self.zonas.items()], expected_parts=len(ZONAS))
        self.assertEqual((r["status"], r["official"]["votes"], r["derived"]["votes"]),
                         (rec.CONSISTENT, 1612, 1612))
        self.assertTrue(r["same_window"])
        self.assertEqual(r["derived"]["sections_counted"], 1071)

    def test_reconciliacao_vale_para_todos_os_candidatos(self):
        for cand in self.municipio.candidatos:
            r = rec.reconcile_candidate_votes(
                cand.sqcand, "municipio", ponto(self.municipio, MACAPA, cand.sqcand),
                [ponto(r, z, cand.sqcand) for z, r in self.zonas.items()], expected_parts=3)
            self.assertEqual(r["status"], rec.CONSISTENT, cand.sqcand)


class SecoesSimuladoTest(unittest.TestCase):
    def setUp(self):
        self.config = sections.parse_ea16(fixture("SIMULADO_ea16_ap.json"), "ap")

    def test_secao_totalizada_traz_data_do_auxiliar(self):
        macapa = next(m for m in self.config.municipios if m.codigo == MACAPA)
        self.assertTrue(all(s.auxiliar_em is not None for s in macapa.secoes if s.eh_principal))
        # Antes da totalizacao (fixture oficial) o campo ainda nao existe.
        oficial = sections.parse_ea16(fixture("ea16_ap.json"), "ap")
        self.assertTrue(all(s.auxiliar_em is None for m in oficial.municipios for s in m.secoes))

    def test_principal_com_varias_agregadas(self):
        pracuuba = next(m for m in self.config.municipios if m.codigo == "06009")
        principal = next(s for s in pracuuba.secoes if s.secao == "0027")
        self.assertEqual(principal.agregadas, ("0090", "0091", "0092", "0093"))
        agregadas = [s for s in pracuuba.secoes if s.eh_agregada]
        self.assertEqual({s.secao_principal for s in agregadas}, {"0027"})
        self.assertTrue(all(s.auxiliar_em is None for s in agregadas))

    def test_ea18_totalizada_sem_arquivos(self):
        auxiliar = sections.parse_ea18(fixture("SIMULADO_ea18_sem_arquivos.json"))
        self.assertEqual((auxiliar.situacao, auxiliar.fase), ("Totalizada", "s"))
        self.assertEqual(len(auxiliar.hashes), 1)
        self.assertIsNone(auxiliar.hashes[0].hash)
        self.assertEqual(auxiliar.hashes[0].arquivos, ())
        self.assertIsNone(sections.current_bu(auxiliar))


if __name__ == "__main__":
    unittest.main()


class Ea18Oficial2026Test(unittest.TestCase):
    """Contrato real do EA18 oficial durante a apuracao (04/10/2026):
    fase em maiuscula ("O"), secao "Recebida", hash "Recebido" e o tipo `imgbu`."""

    def setUp(self):
        self.doc = fixture("ea18_2026_recebido.json")
        self.auxiliar = sections.parse_ea18(self.doc)

    def test_fase_em_maiuscula_e_oficial(self):
        self.assertEqual(self.doc["f"], "O")
        self.assertEqual((origem_from_fase("O"), origem_from_fase("o")), (OFICIAL, OFICIAL))
        self.assertEqual((origem_from_fase("S"), origem_from_fase("s")), (SIMULADO, SIMULADO))
        for invalida in (None, "", "x", "OS"):
            with self.assertRaises(ValueError):
                origem_from_fase(invalida)

    def test_secao_recebida_registra_arquivos_mas_nao_tem_bu_corrente(self):
        self.assertEqual((self.auxiliar.fase, self.auxiliar.situacao), ("O", "Recebida"))
        self.assertEqual(len(self.auxiliar.hashes), 1)
        hash_ = self.auxiliar.hashes[0]
        self.assertEqual(hash_.situacao, "Recebido")
        self.assertIsNotNone(hash_.recebido_em)
        self.assertEqual({a.tipo for a in hash_.arquivos}, {"bu", "log", "imgbu", "rdv", "vota"})
        self.assertEqual(next(a.nome for a in hash_.arquivos if a.tipo == "bu"),
                         "o03220ap0605000020069-bu.dat")
        # Recebido ainda nao e Totalizado: nenhum BU e tratado como corrente.
        self.assertIsNone(sections.current_bu(self.auxiliar))
        totalizado = fixture("ea18_2026_recebido.json")
        totalizado["hashes"][0]["st"] = "Totalizado"
        self.assertIsNotNone(sections.current_bu(sections.parse_ea18(totalizado)))
