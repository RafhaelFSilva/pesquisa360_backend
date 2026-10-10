"""Metadados oficiais dos locais de votacao (ADR-092): leitura do CSV, conciliacao
com o BU pela chave oficial, importacao idempotente e resposta territorial.

Base: BUs sinteticos das fixtures SIMULADO_* (zona 0002 de Macapa; locais 0001
e 0002) e CSVs montados no formato do TSE. O trecho REAL do arquivo oficial
(`tests/fixtures_tse/locais/`) prova o contrato de colunas. Nada aqui escreve
em DEV ou QA.
"""

import hashlib
from pathlib import Path

from sqlalchemy import func, select

from pesquisa360.db import models
from pesquisa360.db.models_tse import (
    TseBoletimUrna, TseBuCargo, TseBuVoto, TseLocalVotacao, TseResultadoCandidato, TseTotalizacao,
)
from pesquisa360.services.tse import locais_import
from pesquisa360.services.tse.locais_import import (
    COLUNAS, ConciliacaoDivergente, FonteInvalida, importar, ler_fonte,
)
from pesquisa360.services.tse.normalization import SIMULADO

from tests.test_tse_bu import SQ_5801, _SecaoApiFixture, bu_da_secao, votos_padrao

TRECHO_OFICIAL = Path(__file__).parent / "fixtures_tse" / "locais" / \
    "eleitorado_local_votacao_2026_AP_trecho.csv"
URL = "https://exemplo.invalid/eleitorado_local_votacao.zip"


def linha(secao, local, nome, endereco="RUA A, 1", bairro="CENTRO", *, municipio="06050", zona="2",
          utilizado=None, uf="AP", data="29/09/2026") -> dict:
    """Uma linha (= uma secao) no formato do CSV do TSE. `utilizado` != `local` = secao realocada."""
    return {
        "DT_GERACAO": "30/09/2026", "HH_GERACAO": "06:00:00", "AA_ELEICAO": "2026",
        "DT_ELEICAO": data, "NR_TURNO": "1", "SG_UF": uf, "CD_MUNICIPIO": municipio,
        "NR_ZONA": zona, "NR_SECAO": str(secao), "NR_LOCAL_VOTACAO": str(utilizado or local),
        "NM_BAIRRO": bairro, "NR_LOCAL_VOTACAO_ORIGINAL": str(local),
        "NM_LOCAL_VOTACAO_ORIGINAL": nome, "DS_ENDERECO_LOCVT_ORIGINAL": endereco,
    }


def csv_tse(linhas: list[dict], colunas=COLUNAS) -> bytes:
    """Latin 1, campos entre aspas, separados por ponto e virgula -- como o leia-me descreve."""
    texto = ";".join(f'"{c}"' for c in colunas) + "\r\n"
    for item in linhas:
        texto += ";".join(f'"{item[c]}"' for c in colunas) + "\r\n"
    return texto.encode("latin-1")


class _LocaisFixture(_SecaoApiFixture):
    def setUp(self):
        super().setUp()
        for secao, votos, local in (("0055", 100, 1), ("0056", 20, 1), ("0057", 50, 2)):
            self.publicar(secao, bu_da_secao(secao, votos_padrao(n5801=votos), local=local))
        self.lote()
        self.data = self.eleicao().data_eleicao.strftime("%d/%m/%Y")

    def csv(self, *, nome1="ESCOLA ESTADUAL TIRADENTES", nome2="CENTRO COMUNITÁRIO SÃO JOSÉ",
            extras=(), **padrao) -> bytes:
        p = {"data": self.data, **padrao}
        return csv_tse([
            linha(55, 1, nome1, "AV FAB      1200", "CENTRO", **p),
            linha(56, 1, nome1, "AV FAB 1200", "CENTRO", **p),
            linha(58, 1, nome1, "AV FAB 1200", "CENTRO", **p),
            linha(57, 2, nome2, "RUA DAS FLORES, S/N", "#NULO", **p),
            *extras,
        ])

    def importar(self, conteudo=None, **extra):
        relatorio = importar(self.session, conteudo or self.csv(), origem=SIMULADO, pleito="17801",
                             uf="AP", fonte_url=URL, **extra)
        self.session.commit()
        return relatorio

    def locais(self, **params):
        return {l["codigo"]: l for l in self.ok("territorio", **params)["locais"]}

    def votos(self):
        return [self.session.scalar(select(func.count()).select_from(m))
                for m in (TseBoletimUrna, TseBuCargo, TseBuVoto, TseTotalizacao,
                          TseResultadoCandidato)] + [
            self.session.scalar(select(func.sum(TseBuVoto.votos)))]


class LeituraDaFonteTest(_LocaisFixture):
    def test_trecho_real_do_csv_oficial(self):
        """Colunas, codificacao e separador do arquivo do TSE; chave = local ORIGINAL."""
        bruto = TRECHO_OFICIAL.read_bytes()
        fonte = ler_fonte(bruto, "ap")
        self.assertEqual((fonte.uf, fonte.data_eleicao, fonte.turno, fonte.linhas, fonte.invalidas),
                         ("ap", "04/10/2026", "1", 11, 0))
        self.assertEqual(fonte.sha256, hashlib.sha256(bruto).hexdigest())
        self.assertEqual(fonte.gerada_em.isoformat(), "2026-10-05T06:29:34-03:00")
        vaz = fonte.locais[("06050", "0002", "2720")]
        self.assertEqual((vaz.nome, vaz.endereco, vaz.bairro, vaz.secoes, vaz.realocadas),
                         ("ESCOLA ESTADUAL DR.ALEXANDRE VAZ TAVARES", "AV FELICIANO COELHO SN",
                          "TREM", 3, 0))
        # Local original indisponivel: as secoes votaram em outro endereco. O nome e o
        # endereco sao os do ORIGINAL; o bairro (do local utilizado) nao e aproveitado.
        benigna = fonte.locais[("06050", "0002", "2046")]
        self.assertEqual((benigna.nome, benigna.realocadas, benigna.secoes, benigna.bairro),
                         ("ESCOLA ESTADUAL PROFª BENIGNA MOREIRA DE SOUZA", 3, 3, None))
        # O mesmo codigo em zonas/municipios diferentes sao locais diferentes.
        a, b = fonte.locais[("06050", "0014", "1074")], fonte.locais[("06017", "0001", "1074")]
        self.assertNotEqual(a.nome, b.nome)
        self.assertEqual(fonte.ambiguos, {})

    def test_arquivo_que_nao_e_a_fonte_e_recusado(self):
        for caso in (b"", b"a,b,c\r\n1,2,3\r\n", b'"X";"Y"\r\n"1";"2"\r\n',
                     csv_tse([linha(55, 1, "X")], colunas=COLUNAS[:-1])):
            with self.assertRaises(FonteInvalida):
                ler_fonte(caso, "ap")
        with self.assertRaises(FonteInvalida):            # so linhas de outra UF
            ler_fonte(csv_tse([linha(55, 1, "X", uf="PA")]), "ap")

    def test_linhas_invalidas_sao_contadas_e_ignoradas(self):
        fonte = ler_fonte(csv_tse([
            linha(55, 1, "ESCOLA A"), linha(56, "-1", "SEM CODIGO"), linha(57, 2, "#NULO"),
            linha(58, "abc", "CODIGO NAO NUMERICO"), linha(59, 3, "DE OUTRA UF", uf="PA"),
        ]), "ap")
        self.assertEqual((fonte.linhas, fonte.invalidas, sorted(fonte.locais)),
                         (5, 4, [("06050", "0002", "0001")]))

    def test_mesmo_codigo_com_dois_nomes_e_ambiguo_e_nao_e_escolhido(self):
        fonte = ler_fonte(csv_tse([linha(55, 1, "ESCOLA A"), linha(56, 1, "ESCOLA B"),
                                   linha(57, 2, "ESCOLA C")]), "ap")
        self.assertEqual(sorted(fonte.locais), [("06050", "0002", "0002")])
        self.assertEqual([n for n, _e in fonte.ambiguos[("06050", "0002", "0001")]],
                         ["ESCOLA A", "ESCOLA B"])


class ImportacaoTest(_LocaisFixture):
    def test_importa_nome_endereco_e_bairro_pela_chave_oficial(self):
        r = self.importar()
        self.assertEqual((r["linhas"], r["locais"], r["invalidos"], r["inseridos"], r["atualizados"],
                          r["inalterados"]), (4, 2, 0, 2, 0, 0))
        self.assertEqual({k: r["conciliacao"][k] for k in ("bu", "csv", "match")},
                         {"bu": 2, "csv": 2, "match": 2})
        self.assertEqual((r["conciliacao"]["bu_only"], r["conciliacao"]["csv_only"],
                          r["conciliacao"]["ambiguous"]), ([], [], []))
        um, dois = self.session.scalars(
            select(TseLocalVotacao).order_by(TseLocalVotacao.codigo_local)).all()
        self.assertEqual((um.origem, um.pleito, um.uf, um.municipio_codigo, um.zona, um.codigo_local),
                         (SIMULADO, "17801", "ap", "06050", "0002", "0001"))
        # Espacos repetidos do cadastro sao normalizados; nada mais e alterado.
        self.assertEqual((um.nome, um.endereco, um.bairro, um.secoes_cadastradas,
                          um.secoes_realocadas), ("ESCOLA ESTADUAL TIRADENTES", "AV FAB 1200",
                                                  "CENTRO", 3, 0))
        self.assertEqual((dois.nome, dois.endereco, dois.bairro),
                         ("CENTRO COMUNITÁRIO SÃO JOSÉ", "RUA DAS FLORES, S/N", None))   # #NULO -> null
        self.assertEqual((um.fonte, um.fonte_url, um.source_hash),
                         (locais_import.FONTE, URL, hashlib.sha256(self.csv()).hexdigest()))
        self.assertEqual(um.fonte_gerada_em.replace(tzinfo=None).isoformat(), "2026-09-30T06:00:00")
        self.assertNotIn("company_id", TseLocalVotacao.__table__.c)
        self.assertFalse(TseLocalVotacao.__table__.foreign_keys)

    def test_a_a_e_idempotente(self):
        self.importar()
        antes = {r.id: (r.nome, r.atualizado_em) for r in self.session.scalars(select(TseLocalVotacao))}
        r = self.importar()
        self.assertEqual((r["inseridos"], r["atualizados"], r["inalterados"]), (0, 0, 2))
        depois = {r.id: (r.nome, r.atualizado_em) for r in self.session.scalars(select(TseLocalVotacao))}
        self.assertEqual(antes, depois)
        self.assertEqual(self.contar(TseLocalVotacao), 2)

    def test_a_b_atualiza_o_mesmo_registro(self):
        self.importar()
        ids = sorted(self.session.scalars(select(TseLocalVotacao.id)))
        novo = self.csv(nome1="ESCOLA ESTADUAL TIRADENTES - ANEXO")
        r = self.importar(novo)
        self.assertEqual((r["inseridos"], r["atualizados"], r["inalterados"]), (0, 1, 1))
        self.assertEqual(sorted(self.session.scalars(select(TseLocalVotacao.id))), ids)
        um = self.session.scalars(select(TseLocalVotacao).where(
            TseLocalVotacao.codigo_local == "0001")).one()
        self.assertEqual((um.nome, um.source_hash),
                         ("ESCOLA ESTADUAL TIRADENTES - ANEXO", hashlib.sha256(novo).hexdigest()))

    def test_metadado_e_por_pleito_outra_eleicao_nao_sobrescreve(self):
        self.importar()
        self.session.add(TseLocalVotacao(
            origem=SIMULADO, pleito="99999", uf="ap", municipio_codigo="06050", zona="0002",
            codigo_local="0001", nome="NOME DE OUTRA ELEICAO", secoes_cadastradas=1,
            fonte="TESTE", source_hash="0" * 64))
        self.session.commit()
        self.importar(self.csv(nome1="ESCOLA RENOMEADA"))
        nomes = dict(self.session.execute(select(TseLocalVotacao.pleito, TseLocalVotacao.nome).where(
            TseLocalVotacao.codigo_local == "0001")).all())
        self.assertEqual(nomes, {"17801": "ESCOLA RENOMEADA", "99999": "NOME DE OUTRA ELEICAO"})

    def test_codigo_repetido_em_outra_zona_e_outro_local(self):
        extras = [linha(60, 1, "ESCOLA DA ZONA DEZ", zona="10", data=self.data),
                  linha(1, 1, "ESCOLA DE OUTRO MUNICIPIO", municipio="06157", zona="6", data=self.data)]
        r = self.importar(self.csv(extras=extras))
        self.assertEqual((r["inseridos"], r["conciliacao"]["match"], len(r["conciliacao"]["csv_only"])),
                         (4, 2, 2))
        nomes = {(l.municipio_codigo, l.zona): l.nome for l in self.session.scalars(
            select(TseLocalVotacao).where(TseLocalVotacao.codigo_local == "0001"))}
        self.assertEqual(nomes, {("06050", "0002"): "ESCOLA ESTADUAL TIRADENTES",
                                 ("06050", "0010"): "ESCOLA DA ZONA DEZ",
                                 ("06157", "0006"): "ESCOLA DE OUTRO MUNICIPIO"})
        # A zona 0002 so enxerga o SEU local 0001.
        self.assertEqual(self.locais()["0001"]["nome"], "ESCOLA ESTADUAL TIRADENTES")

    def test_local_do_bu_sem_metadado_ou_ambiguo_recusa_a_importacao(self):
        casos = {
            "BU_ONLY": csv_tse([linha(55, 1, "SO O LOCAL UM", data=self.data)]),
            "AMBIGUOUS": self.csv(extras=[linha(59, 2, "OUTRO NOME PARA O LOCAL DOIS", data=self.data)]),
        }
        for caso, conteudo in casos.items():
            with self.assertRaises(ConciliacaoDivergente, msg=caso):
                importar(self.session, conteudo, origem=SIMULADO, pleito="17801", uf="ap")
            self.session.rollback()
            self.assertEqual(self.contar(TseLocalVotacao), 0, caso)
        # A conciliacao nomeia o que divergiu, sem recorrer a nome nem a aproximacao.
        fonte = ler_fonte(casos["BU_ONLY"], "ap")
        c = locais_import.conciliar(self.session, fonte, SIMULADO, "17801")
        self.assertEqual((c["match"], c["bu_only"], c["ambiguous"]),
                         (1, [("06050", "0002", "0002")], []))

    def test_nome_nunca_e_usado_como_chave(self):
        """Nome identico ao de um local do BU, sob OUTRO codigo, nao casa com ele."""
        conteudo = csv_tse([linha(55, 1, "ESCOLA ESTADUAL TIRADENTES", data=self.data),
                            linha(57, 9, "CENTRO COMUNITÁRIO SÃO JOSÉ", data=self.data)])
        fonte = ler_fonte(conteudo, "ap")
        c = locais_import.conciliar(self.session, fonte, SIMULADO, "17801")
        self.assertEqual((c["bu_only"], c["csv_only"]),
                         ([("06050", "0002", "0002")], [("06050", "0002", "0009")]))

    def test_csv_de_outra_eleicao_ou_pleito_inexistente_e_recusado(self):
        with self.assertRaises(FonteInvalida):
            importar(self.session, self.csv(data="06/10/2024"), origem=SIMULADO, pleito="17801", uf="ap")
        with self.assertRaises(FonteInvalida):
            importar(self.session, self.csv(), origem=SIMULADO, pleito="424242", uf="ap")
        self.session.rollback()
        self.assertEqual(self.contar(TseLocalVotacao), 0)

    def test_dry_run_concilia_e_nao_grava(self):
        r = importar(self.session, self.csv(), origem=SIMULADO, pleito="17801", uf="ap", dry_run=True)
        self.assertEqual((r["dry_run"], r["inseridos"], r["conciliacao"]["match"]), (True, 0, 2))
        self.session.rollback()
        self.assertEqual(self.contar(TseLocalVotacao), 0)

    def test_secoes_realocadas_sao_contadas_e_o_bairro_alheio_nao_e_usado(self):
        conteudo = csv_tse([
            linha(55, 1, "ESCOLA INTERDITADA", "RUA X", "BAIRRO DO TEMPORARIO", utilizado=7, data=self.data),
            linha(56, 1, "ESCOLA INTERDITADA", "RUA X", "BAIRRO DO TEMPORARIO", utilizado=7, data=self.data),
            linha(57, 2, "ESCOLA NORMAL", "RUA Y", "CENTRO", data=self.data)])
        self.importar(conteudo)
        um = self.session.scalars(select(TseLocalVotacao).where(
            TseLocalVotacao.codigo_local == "0001")).one()
        self.assertEqual((um.nome, um.endereco, um.bairro, um.secoes_realocadas),
                         ("ESCOLA INTERDITADA", "RUA X", None, 2))
        self.assertEqual(self.locais()["0001"]["secoes_realocadas"], 2)

    def test_importacao_nao_toca_votos_bu_nem_ea20(self):
        antes = self.votos()
        candidato = self.ok("cargos/0006", local_votacao="0001")
        self.importar()
        self.importar(self.csv(nome1="OUTRO NOME"))
        self.assertEqual(self.votos(), antes)
        depois = self.ok("cargos/0006", local_votacao="0001")
        self.assertEqual(depois["candidatos"], candidato["candidatos"])
        self.assertEqual(depois["totalizacao"], candidato["totalizacao"])
        self.assertEqual(self.ok("cargos/0006")["candidatos"][0]["votos"],
                         self.ok("cargos/0006")["candidatos"][0]["votos"])

    def test_cobertura_apos_a_importacao(self):
        vazio = locais_import.cobertura(self.session, SIMULADO, "17801", "ap")
        self.assertEqual((vazio["locais_bu"], vazio["com_metadado"], len(vazio["sem_metadado"])),
                         (2, 0, 2))
        self.importar()
        c = locais_import.cobertura(self.session, SIMULADO, "17801", "ap")
        self.assertEqual((c["locais_bu"], c["com_metadado"], c["sem_metadado"], c["sem_nome"],
                          c["sem_endereco"], c["sem_bairro"]), (2, 2, [], 0, 0, 1))


class ApiComMetadadosTest(_LocaisFixture):
    def test_sem_metadado_o_rotulo_e_o_codigo(self):
        um = self.locais()["0001"]
        self.assertEqual((um["codigo"], um["nome"], um["endereco"], um["bairro"], um["rotulo"],
                          um["metadados"], um["secoes_realocadas"]),
                         ("0001", None, None, None, "Local 0001", None, None))
        cargo = self.ok("cargos/0006", local_votacao="0001")
        self.assertEqual((cargo["local"]["nome"], cargo["local"]["rotulo"]), (None, "Local 0001"))
        partes = self.ok("cargos/0006/distribuicao", nivel="locais_votacao", candidato=SQ_5801)["partes"]
        self.assertEqual([p["nome"] for p in partes], ["Local 0001", "Local 0002"])

    def test_territorio_enriquecido_mantem_o_codigo_e_as_contagens(self):
        antes = self.locais()
        self.importar()
        depois = self.locais()
        um = depois["0001"]
        self.assertEqual((um["codigo"], um["nome"], um["endereco"], um["bairro"], um["rotulo"]),
                         ("0001", "ESCOLA ESTADUAL TIRADENTES", "AV FAB 1200", "CENTRO",
                          "ESCOLA ESTADUAL TIRADENTES — Local 0001"))
        self.assertEqual((depois["0002"]["nome"], depois["0002"]["bairro"]),
                         ("CENTRO COMUNITÁRIO SÃO JOSÉ", None))
        self.assertEqual(um["metadados"]["fonte"], locais_import.FONTE)
        self.assertEqual((um["quantidade_secoes"], um["secoes_realocadas"]), (um["secoes"], 0))
        # So o metadado muda: codigo, secoes, principais, agregadas e BUs ficam iguais.
        for codigo in antes:
            for campo in ("codigo", "secoes", "principais", "agregadas", "bus"):
                self.assertEqual(depois[codigo][campo], antes[codigo][campo], (codigo, campo))
        # Com o filtro de local, a lista de locais da zona continua completa.
        self.assertEqual(sorted(self.locais(local_votacao="0002")), ["0001", "0002"])
        self.assertEqual(self.ok("territorio", local_votacao="0002")["local_votacao"], "0002")

    def test_local_no_resumo_cargo_nominatas_e_distribuicao(self):
        self.importar()
        for rota in ("resumo", "cargos/0006", "cargos/0006/nominatas"):
            local = self.ok(rota, local_votacao="0001")["local"]
            self.assertEqual((local["codigo"], local["nome"], local["endereco"], local["zona"]),
                             ("0001", "ESCOLA ESTADUAL TIRADENTES", "AV FAB 1200", "0002"), rota)
        zona = self.ok("cargos/0006/distribuicao", nivel="locais_votacao", candidato=SQ_5801)
        partes = {p["codigo"]: p for p in zona["partes"]}
        self.assertEqual((partes["0001"]["nome"], partes["0001"]["local"]["endereco"],
                          partes["0002"]["nome"]),
                         ("ESCOLA ESTADUAL TIRADENTES", "AV FAB 1200", "CENTRO COMUNITÁRIO SÃO JOSÉ"))
        self.assertEqual(zona["itens"][0]["soma_das_partes"], 170)          # votos inalterados
        detalhe = self.ok("cargos/0006/distribuicao", local_votacao="0001", candidato=SQ_5801)
        self.assertEqual((detalhe["local"]["nome"], detalhe["itens"][0]["total_votos"]),
                         ("ESCOLA ESTADUAL TIRADENTES", 120))

    def test_local_inexistente_continua_404_e_o_nome_nao_e_aceito_como_filtro(self):
        self.importar()
        self.assertEqual(self.get("cargos/0006", local_votacao="9999").status_code, 404)
        self.assertEqual(self.get("cargos/0006", local_votacao="ESCOLA").status_code, 422)
        self.assertEqual(self.get("territorio", local_votacao="0003").status_code, 404)

    def test_metadado_de_local_que_nao_esta_nos_bus_nao_cria_local(self):
        self.importar(self.csv(extras=[linha(70, 5, "LOCAL SO NO CADASTRO", data=self.data)]))
        self.assertEqual(self.contar(TseLocalVotacao), 3)
        self.assertEqual(sorted(self.locais()), ["0001", "0002"])
        self.assertEqual(self.get("cargos/0006", local_votacao="0005").status_code, 404)

    def test_enriquecimento_e_uma_consulta_por_zona_e_nao_grava(self):
        from sqlalchemy import event

        self.importar()
        self.ok("territorio")
        consultas = []
        escuta = lambda *_a, **_k: consultas.append(1)
        antes = self.contagens()
        event.listen(self.engine, "before_cursor_execute", escuta)
        try:
            self.ok("territorio")
            sem_filtro = len(consultas)
            consultas.clear()
            self.ok("territorio", local_votacao="0001")
            com_filtro = len(consultas)
        finally:
            event.remove(self.engine, "before_cursor_execute", escuta)
        self.assertEqual(sem_filtro, com_filtro)
        self.assertLess(sem_filtro, 12)
        self.assertEqual(self.contagens(), antes)

    def test_tabela_global_sem_tenant(self):
        tabela = models.Base.metadata.tables["tse_locais_votacao"]
        self.assertNotIn("company_id", tabela.c)
        unicas = [sorted(c.name for c in u.columns) for u in tabela.constraints
                  if u.__class__.__name__ == "UniqueConstraint"]
        self.assertIn(sorted(["origem", "pleito", "uf", "municipio_codigo", "zona", "codigo_local"]),
                      unicas)
