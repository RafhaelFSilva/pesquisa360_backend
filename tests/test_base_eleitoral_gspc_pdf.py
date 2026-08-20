"""Testes do adapter da fonte GSPC "Eleitorado Apto (Amapa 2026).pdf".

Duas camadas:

1. Fixture textual fiel ao arquivo real (linhas copiadas da fonte, incluindo o
   ruido de escolas/secoes e a ordem embaralhada da extracao). Roda sempre.
2. Teste golden contra o PDF real, habilitado por P360_GSPC_PDF. O PDF nao e
   versionado: tem 2,4 MB e e material de terceiro.
"""

import hashlib
import os
import unittest
from pathlib import Path

os.environ.setdefault("SECRET_KEY", "test-only-gspc-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.services.base_eleitoral_import import (
    RegistroInvalidoError,
    calcular_hash_arquivo,
    conferir_resumo_detalhe,
    validar_estrutura,
)
from pesquisa360.services.base_eleitoral_sources import gspc_amapa_2026_pdf as adapter

NOME_ARQUIVO = "Eleitorado Apto (Amapá 2026).pdf"

# Linhas reais da fonte. O cabecalho municipal aparece DEPOIS dos agregados em
# varias paginas: a extracao do pypdf nao respeita a ordem visual.
PAGINA_CAPA = """GSPC – CONSULTORIA LEGISLATIVA, ESTATÍSTICAS ELEITORAIS ANÁLISES & PESQUISA
Av. Francisca Praxédio de Mendonça – 513 - Jardim Equatorial: Cel (96) 98125-9666
ELEIÇÕES 2026
10.000 ELEITORES APTOS
NO ESTADO AMAPÁ
"""

# Tabela-resumo: as linhas nao usam travessao, entao nao viram registro.
PAGINA_RESUMO = """Nº BAIRRO /
LOCALIDADE
Nº DE ELEITORES
MACAPÁ Nº BAIRRO /
LOCALIDADE
Nº DE ELEITORES
SANTANA
1 BURITIZAL 30.507 1 CENTRO 26.569
2 SANTA RITA 19.828 2 FONTE NOVA 11.448
322.066 TOTAL 170.052
ELEITORADO AMAPAENSE APTO PARA AS ELEIÇÕES 2026
"""

PAGINA_AMAPA = """SEÇÃO 12 17 18 19 21 30 31 33 35 ELEITORES 2.478
END
SEÇÃO 84 90 ELEITORES 478
END
1 – SETE MANGUEIRAS – 2.956 ELEITORES
01 – E.E. VIDAL DE NEGREIROS - 09 SEÇÕES
Av. Desidério Antônio Coelho, 470 – Sete Mangueiras
2 – CENTRO – 1.220 ELEITORES
01 – E.P.G. MATÃO III - 01 SEÇÃO / 286 ELEITORES
Praça Barão do Rio Branco, s/nº – Centro
3 – PIQUIÁ – 347 ELEITORES
01 – ESCOLA ESTADUAL ROZENDO NASCIMENTO FILHO - 02 SEÇÕES
ELEITORES E LOCAIS DE VOTAÇÃO - 2026
MUNICÍPIO DE AMAPÁ - ZONA 1 - 4.523 ELEITORES
BAIRROS 03 - LOCAIS DE VOTAÇÃO – 10 / SEÇÕES – 29 / ELEITORADO – 4.523
"""

# Municipio com duas paginas e duas zonas declaradas.
PAGINA_MACAPA_1 = """MUNICÍPIO DE MACAPÁ - ZONAS 2 e 10 - 3.500 ELEITORES
1 – BURITIZAL – 3.000 ELEITORES
01 – E.M. FRANCISCO ALVES DE OLIVEIRA - 02 SEÇÕES
"""

PAGINA_MACAPA_2 = """Rua Marcos Botas, 476 - Vila São Joaquim do Pacuí, S/N
2 – ILHA MIRIM – 457 ELEITORES
BAIRROS 02 - LOCAIS DE VOTAÇÃO – 3 / SEÇÕES – 5 / ELEITORADO – 3.457
"""

# Santana traz o unico agregado com valor 1 e no singular, com hifen no lugar
# do travessao final.
PAGINA_SANTANA = """MUNICÍPIO DE SANTANA - ZONA 6 - 90 ELEITORES
1 – CENTRO – 89 ELEITORES
 2 – MAÇARANDUBA - 1 ELEITOR
01 – ASS. DOS PROD. RURAIS DE MAÇARANDUBA II - 01 SEÇÃO
BR 156. Ramal do Maçaranduba II KM 18 - Maçaranduba
BAIRROS 02 - LOCAIS DE VOTAÇÃO – 2 / SEÇÕES – 2 / ELEITORADO – 90
"""

FIXTURE = [
    PAGINA_CAPA,
    PAGINA_RESUMO,
    PAGINA_AMAPA,
    PAGINA_MACAPA_1,
    PAGINA_MACAPA_2,
    PAGINA_SANTANA,
]


class AdapterFixtureTextualTests(unittest.TestCase):
    """Estrutura do parser contra linhas reais, sem depender do PDF binario."""

    def setUp(self):
        self.registros = adapter.parse_paginas(FIXTURE, NOME_ARQUIVO)
        self.por_tipo = {}
        for registro in self.registros:
            self.por_tipo.setdefault(registro.tipo, []).append(registro)

    def _bairro(self, nome):
        return next(r for r in self.por_tipo["BAIRRO"] if r.nome == nome)

    # -- estrutura -------------------------------------------------------------

    def test_produz_apenas_estado_municipio_e_bairro(self):
        self.assertEqual(set(self.por_tipo), {"ESTADO", "MUNICIPIO", "BAIRRO"})

    def test_nenhum_local_de_votacao_secao_ou_localidade(self):
        for tipo in ("LOCAL_VOTACAO", "SECAO", "LOCALIDADE"):
            with self.subTest(tipo=tipo):
                self.assertNotIn(tipo, self.por_tipo)

    def test_contagens_da_fixture(self):
        self.assertEqual(len(self.por_tipo["ESTADO"]), 1)
        self.assertEqual(len(self.por_tipo["MUNICIPIO"]), 3)
        self.assertEqual(len(self.por_tipo["BAIRRO"]), 7)

    def test_arvore_valida_para_o_motor_da_fase_3a(self):
        validar_estrutura(self.registros)

    def test_chaves_sao_unicas(self):
        chaves = [r.chave for r in self.registros]
        self.assertEqual(len(chaves), len(set(chaves)))

    # -- cabecalho municipal ---------------------------------------------------

    def test_cabecalho_municipal_e_lido_mesmo_aparecendo_depois_dos_bairros(self):
        # Em PAGINA_AMAPA o cabecalho e a penultima linha, apos os tres agregados.
        amapa = next(r for r in self.por_tipo["MUNICIPIO"] if r.nome == "AMAPÁ")
        self.assertEqual(amapa.eleitorado_apto, 4523)
        self.assertEqual(amapa.metadados["pagina_fonte"], 3)

    def test_zona_unica_vira_atributo(self):
        amapa = next(r for r in self.por_tipo["MUNICIPIO"] if r.nome == "AMAPÁ")
        self.assertEqual(amapa.zona_eleitoral, 1)
        self.assertEqual(amapa.metadados["zona_original"], "1")

    def test_multiplas_zonas_nao_escolhem_uma(self):
        macapa = next(r for r in self.por_tipo["MUNICIPIO"] if r.nome == "MACAPÁ")
        self.assertIsNone(macapa.zona_eleitoral)
        self.assertEqual(macapa.metadados["zona_original"], "2 e 10")

    def test_municipio_com_multiplas_paginas_agrupa_os_bairros(self):
        macapa = next(r for r in self.por_tipo["MUNICIPIO"] if r.nome == "MACAPÁ")
        filhos = [r for r in self.por_tipo["BAIRRO"] if r.parent_chave == macapa.chave]
        self.assertEqual({r.nome for r in filhos}, {"BURITIZAL", "ILHA MIRIM"})

    def test_bairro_em_pagina_de_continuacao_herda_o_municipio(self):
        # ILHA MIRIM esta na pagina 5, que nao possui cabecalho municipal.
        ilha = self._bairro("ILHA MIRIM")
        self.assertEqual(ilha.metadados["municipio_original"], "MACAPÁ")
        self.assertEqual(ilha.metadados["pagina_fonte"], 5)

    # -- valores ---------------------------------------------------------------

    def test_numero_com_separador_de_milhar(self):
        self.assertEqual(self._bairro("SETE MANGUEIRAS").eleitorado_apto, 2956)
        self.assertEqual(self._bairro("BURITIZAL").eleitorado_apto, 3000)

    def test_eleitorado_igual_a_um_no_singular(self):
        # "2 – MAÇARANDUBA - 1 ELEITOR": exigir o plural perderia este registro.
        macaranduba = self._bairro("MAÇARANDUBA")
        self.assertEqual(macaranduba.eleitorado_apto, 1)

    def test_nome_com_acento_preservado_e_chave_normalizada(self):
        piquia = self._bairro("PIQUIÁ")
        self.assertEqual(piquia.nome, "PIQUIÁ")
        self.assertEqual(piquia.nome_normalizado, "piquia")
        macaranduba = self._bairro("MAÇARANDUBA")
        self.assertEqual(macaranduba.nome_normalizado, "macaranduba")

    # -- classificacao e metadados --------------------------------------------

    def test_todo_agregado_entra_como_bairro_com_classificacao_da_fonte(self):
        for registro in self.por_tipo["BAIRRO"]:
            with self.subTest(nome=registro.nome):
                self.assertEqual(registro.tipo, "BAIRRO")
                self.assertEqual(
                    registro.metadados["classificacao_fonte"], "BAIRRO_LOCALIDADE"
                )

    def test_metadados_de_rastreabilidade(self):
        sete = self._bairro("SETE MANGUEIRAS")
        metadados = sete.metadados
        self.assertEqual(metadados["origem"], NOME_ARQUIVO)
        self.assertEqual(metadados["pagina_fonte"], 3)
        self.assertEqual(metadados["numero_ordem_fonte"], 1)
        self.assertEqual(metadados["municipio_original"], "AMAPÁ")
        self.assertEqual(metadados["eleitorado_original"], "2.956")
        self.assertIn("SETE MANGUEIRAS", metadados["texto_original"])

    def test_numeracao_da_fonte_nao_vira_codigo_oficial(self):
        for registro in self.por_tipo["BAIRRO"]:
            with self.subTest(nome=registro.nome):
                self.assertIsNone(registro.codigo)
                self.assertIsInstance(registro.metadados["numero_ordem_fonte"], int)

    def test_estado_usa_valor_declarado_na_capa(self):
        estado = self.por_tipo["ESTADO"][0]
        self.assertEqual(estado.nome, "AMAPÁ")
        self.assertEqual(estado.eleitorado_apto, 10000)
        self.assertEqual(estado.metadados["pagina_fonte"], 1)

    # -- nao captura (secao 20) ------------------------------------------------

    def test_nao_captura_escola_secao_nem_endereco(self):
        nomes = {r.nome for r in self.por_tipo["BAIRRO"]}
        proibidos = (
            "E.E. VIDAL DE NEGREIROS",
            "E.P.G. MATÃO III",
            "ESCOLA ESTADUAL ROZENDO NASCIMENTO FILHO",
            "E.M. FRANCISCO ALVES DE OLIVEIRA",
            "ASS. DOS PROD. RURAIS DE MAÇARANDUBA II",
        )
        for proibido in proibidos:
            with self.subTest(nome=proibido):
                self.assertNotIn(proibido, nomes)
        for registro in self.por_tipo["BAIRRO"]:
            with self.subTest(nome=registro.nome):
                self.assertNotIn("SEÇÃO", registro.nome.upper())
                self.assertNotIn("ESCOLA", registro.nome.upper())

    def test_linha_de_escola_com_sufixo_eleitores_nao_e_capturada(self):
        # "01 – E.P.G. MATÃO III - 01 SEÇÃO / 286 ELEITORES" termina em ELEITORES
        # mas o valor nao e puramente numerico.
        linha = "01 – E.P.G. MATÃO III - 01 SEÇÃO / 286 ELEITORES"
        self.assertIsNone(adapter.RE_BAIRRO.match(linha))

    def test_linha_de_secao_nao_e_capturada(self):
        for linha in (
            "SEÇÃO 12 17 18 19 21 30 31 33 35 ELEITORES 2.478",
            "SEÇÃO 84 90 ELEITORES 478",
            "END",
        ):
            with self.subTest(linha=linha):
                self.assertIsNone(adapter.RE_BAIRRO.match(linha))

    def test_cabecalho_municipal_nao_vira_bairro(self):
        self.assertIsNone(
            adapter.RE_BAIRRO.match("MUNICÍPIO DE AMAPÁ - ZONA 1 - 4.523 ELEITORES")
        )

    def test_pagina_de_resumo_nao_gera_duplicata(self):
        # BURITIZAL e CENTRO aparecem no resumo e no detalhamento; devem existir
        # uma unica vez, vindos do detalhamento.
        nomes = [r.nome for r in self.por_tipo["BAIRRO"]]
        self.assertEqual(nomes.count("BURITIZAL"), 1)
        # CENTRO existe em AMAPÁ e em SANTANA: repeticao legitima entre municipios.
        pares = [(r.metadados["municipio_original"], r.nome) for r in self.por_tipo["BAIRRO"]]
        self.assertEqual(len(pares), len(set(pares)))
        buritizal = self._bairro("BURITIZAL")
        self.assertEqual(buritizal.metadados["pagina_fonte"], 4)
        self.assertNotEqual(buritizal.eleitorado_apto, 30507)

    # -- conferencia resumo x detalhe ------------------------------------------

    def test_conferencia_detecta_divergencia_sem_corrigir(self):
        divergencias = conferir_resumo_detalhe(self.registros)
        # Indexado por chave: "AMAPÁ" e nome de ESTADO e de MUNICIPIO.
        por_chave = {d.territorio_chave: d for d in divergencias}

        # MACAPÁ: 3.500 declarados contra 3.457 somados.
        macapa = por_chave["MUNICIPIO|macapa"]
        self.assertEqual(macapa.valor_resumo, 3500)
        self.assertEqual(macapa.valor_detalhe, 3457)
        self.assertEqual(macapa.diferenca, 43)

        # Nenhum filho foi alterado para fechar a conta.
        self.assertEqual(self._bairro("BURITIZAL").eleitorado_apto, 3000)
        self.assertEqual(self._bairro("ILHA MIRIM").eleitorado_apto, 457)

        # Municipios que fecham nao geram divergencia.
        self.assertNotIn("MUNICIPIO|amapa", por_chave)
        self.assertNotIn("MUNICIPIO|santana", por_chave)

    # -- validacao contra a propria fonte --------------------------------------

    def test_rodape_divergente_derruba_a_extracao(self):
        pagina = PAGINA_AMAPA.replace("BAIRROS 03", "BAIRROS 04")
        with self.assertRaises(adapter.FonteGspcError) as contexto:
            adapter.parse_paginas([PAGINA_CAPA, PAGINA_RESUMO, pagina], NOME_ARQUIVO)
        self.assertIn("rodape declara", str(contexto.exception))

    def test_numeracao_nao_sequencial_derruba_a_extracao(self):
        pagina = PAGINA_AMAPA.replace("3 – PIQUIÁ", "9 – PIQUIÁ")
        with self.assertRaises(adapter.FonteGspcError) as contexto:
            adapter.parse_paginas([PAGINA_CAPA, PAGINA_RESUMO, pagina], NOME_ARQUIVO)
        self.assertIn("sequencial", str(contexto.exception))

    def test_municipio_sem_rodape_derruba_a_extracao(self):
        pagina = "\n".join(
            l for l in PAGINA_AMAPA.splitlines() if not l.startswith("BAIRROS")
        )
        with self.assertRaises(adapter.FonteGspcError):
            adapter.parse_paginas([PAGINA_CAPA, PAGINA_RESUMO, pagina], NOME_ARQUIVO)

    def test_fonte_sem_cabecalho_municipal_e_rejeitada(self):
        with self.assertRaises(adapter.FonteGspcError):
            adapter.parse_paginas([PAGINA_CAPA, PAGINA_RESUMO], NOME_ARQUIVO)

    def test_fonte_sem_total_estadual_e_rejeitada(self):
        with self.assertRaises(adapter.FonteGspcError):
            adapter.parse_paginas([PAGINA_RESUMO, PAGINA_AMAPA], NOME_ARQUIVO)

    def test_eleitorado_em_formato_invalido_e_rejeitado(self):
        pagina = PAGINA_AMAPA.replace("2.956 ELEITORES", "2,956 ELEITORES")
        # A linha deixa de casar e o rodape passa a divergir: o lote e recusado.
        with self.assertRaises(adapter.FonteGspcError):
            adapter.parse_paginas([PAGINA_CAPA, PAGINA_RESUMO, pagina], NOME_ARQUIVO)

    def test_lista_de_paginas_vazia_e_rejeitada(self):
        with self.assertRaises(adapter.FonteGspcError):
            adapter.parse_paginas([], NOME_ARQUIVO)

    # -- resumo de preview -----------------------------------------------------

    def test_resumo_extracao_reporta_contagens_e_diferencas(self):
        resumo = adapter.resumo_extracao(self.registros)
        self.assertEqual(resumo["por_tipo"]["BAIRRO"], 7)
        self.assertEqual(resumo["por_tipo"]["MUNICIPIO"], 3)
        self.assertEqual(resumo["por_tipo"]["ESTADO"], 1)
        self.assertTrue(resumo["chaves_unicas"])
        macapa = next(m for m in resumo["municipios"] if m["municipio"] == "MACAPÁ")
        self.assertEqual(macapa["diferenca"], 43)


class ExtracaoBinariaTests(unittest.TestCase):
    def test_bytes_invalidos_sao_rejeitados(self):
        for conteudo in (b"", "texto", None):
            with self.subTest(conteudo=type(conteudo).__name__):
                with self.assertRaises(adapter.FonteGspcError):
                    adapter.extrair_texto_paginas(conteudo)

    def test_pdf_corrompido_e_rejeitado(self):
        with self.assertRaises(adapter.FonteGspcError):
            adapter.extrair_texto_paginas(b"%PDF-1.4 truncado")

    def test_hash_e_reproduzivel(self):
        conteudo = b"conteudo de teste"
        self.assertEqual(
            calcular_hash_arquivo(conteudo),
            hashlib.sha256(conteudo).hexdigest(),
        )


PDF_REAL = os.environ.get("P360_GSPC_PDF")


@unittest.skipUnless(
    PDF_REAL and Path(PDF_REAL).is_file(),
    "defina P360_GSPC_PDF apontando para o PDF real para rodar o golden",
)
class GoldenArquivoRealTests(unittest.TestCase):
    """Golden contra o arquivo real. O PDF nao e versionado (2,4 MB, terceiro)."""

    SHA256 = "7a363d763d9aaec1e791a606a32278e3bcff2499add98c784ff56bef233439fa"

    @classmethod
    def setUpClass(cls):
        cls.conteudo = Path(PDF_REAL).read_bytes()
        cls.paginas = adapter.extrair_texto_paginas(cls.conteudo)
        cls.registros = adapter.parse_paginas(cls.paginas, NOME_ARQUIVO)
        cls.por_tipo = {}
        for registro in cls.registros:
            cls.por_tipo.setdefault(registro.tipo, []).append(registro)

    def _bairro(self, municipio, nome):
        return next(
            r
            for r in self.por_tipo["BAIRRO"]
            if r.nome == nome and r.metadados["municipio_original"] == municipio
        )

    def test_hash_do_arquivo(self):
        self.assertEqual(calcular_hash_arquivo(self.conteudo), self.SHA256)

    def test_quarenta_paginas(self):
        self.assertEqual(len(self.paginas), 40)

    def test_contagens_de_aceite(self):
        self.assertEqual(len(self.por_tipo["BAIRRO"]), 198)
        self.assertEqual(len(self.por_tipo["MUNICIPIO"]), 16)
        self.assertEqual(len(self.por_tipo["ESTADO"]), 1)
        self.assertEqual(len(self.registros), 215)

    def test_nada_de_local_votacao_secao_ou_localidade(self):
        for tipo in ("LOCAL_VOTACAO", "SECAO", "LOCALIDADE"):
            with self.subTest(tipo=tipo):
                self.assertEqual(len(self.por_tipo.get(tipo, [])), 0)

    def test_os_dezesseis_municipios(self):
        esperados = {
            "AMAPÁ", "CALÇOENE", "CUTIAS", "FERREIRA GOMES", "ITAUBAL",
            "LARANJAL DO JARI", "MACAPÁ", "MAZAGÃO", "OIAPOQUE", "PEDRA BRANCA",
            "PORTO GRANDE", "PRACUÚBA", "SANTANA", "SERRA DO NAVIO",
            "TARTARUGALZINHO", "VITÓRIA DO JARI",
        }
        self.assertEqual({r.nome for r in self.por_tipo["MUNICIPIO"]}, esperados)

    def test_bairros_por_municipio(self):
        esperado = {
            "AMAPÁ": 8, "CALÇOENE": 7, "CUTIAS": 4, "FERREIRA GOMES": 4,
            "ITAUBAL": 9, "LARANJAL DO JARI": 16, "MACAPÁ": 42, "MAZAGÃO": 20,
            "OIAPOQUE": 15, "PEDRA BRANCA": 6, "PORTO GRANDE": 10, "PRACUÚBA": 5,
            "SANTANA": 23, "SERRA DO NAVIO": 3, "TARTARUGALZINHO": 19,
            "VITÓRIA DO JARI": 7,
        }
        obtido = {}
        for registro in self.por_tipo["BAIRRO"]:
            municipio = registro.metadados["municipio_original"]
            obtido[municipio] = obtido.get(municipio, 0) + 1
        self.assertEqual(obtido, esperado)
        self.assertEqual(sum(obtido.values()), 198)

    def test_sentinelas(self):
        sentinelas = {
            ("MACAPÁ", "BURITIZAL"): 30507,
            ("MACAPÁ", "SANTA RITA"): 19828,
            ("MACAPÁ", "ILHA MIRIM"): 457,
            ("SANTANA", "CENTRO"): 26569,
            ("SANTANA", "MAÇARANDUBA"): 1,
            ("AMAPÁ", "SETE MANGUEIRAS"): 2956,
            ("TARTARUGALZINHO", "ENTRE RIOS"): 251,
            ("VITÓRIA DO JARI", "COMUNIDADE DE TAPEREIRA"): 89,
        }
        for (municipio, nome), esperado in sentinelas.items():
            with self.subTest(municipio=municipio, bairro=nome):
                self.assertEqual(self._bairro(municipio, nome).eleitorado_apto, esperado)

    def test_estado_declarado_na_capa(self):
        estado = self.por_tipo["ESTADO"][0]
        self.assertEqual(estado.nome, "AMAPÁ")
        self.assertEqual(estado.eleitorado_apto, 578157)

    def test_arvore_valida_para_o_motor(self):
        validar_estrutura(self.registros)

    def test_divergencias_reais_da_fonte(self):
        divergencias = {d.territorio: d for d in conferir_resumo_detalhe(self.registros)}
        # A fonte nao fecha em tres pontos; nenhum deles e corrigido.
        self.assertEqual(set(divergencias), {"AMAPÁ", "MAZAGÃO", "PORTO GRANDE"})
        self.assertEqual(divergencias["MAZAGÃO"].diferenca, 40)
        self.assertEqual(divergencias["PORTO GRANDE"].diferenca, 100)
        self.assertEqual(divergencias["AMAPÁ"].valor_resumo, 578157)
        self.assertEqual(divergencias["AMAPÁ"].valor_detalhe, 578034)
        self.assertEqual(divergencias["AMAPÁ"].diferenca, 123)

    def test_nenhum_nome_de_escola_ou_secao_entre_os_bairros(self):
        import re

        proibido = re.compile(
            r"ESCOLA|E\.E\.|E\.M\.|E\.P\.G|SE[ÇC][ÃA]O|SE[ÇC][ÕO]ES|UNIFAP|IFAP|COL[ÉE]GIO",
            re.I,
        )
        for registro in self.por_tipo["BAIRRO"]:
            with self.subTest(nome=registro.nome):
                self.assertIsNone(proibido.search(registro.nome))

    def test_todo_bairro_tem_rastreabilidade(self):
        for registro in self.por_tipo["BAIRRO"]:
            with self.subTest(nome=registro.nome):
                self.assertEqual(registro.metadados["origem"], NOME_ARQUIVO)
                self.assertEqual(
                    registro.metadados["classificacao_fonte"], "BAIRRO_LOCALIDADE"
                )
                self.assertIsInstance(registro.metadados["pagina_fonte"], int)
                self.assertIsNone(registro.codigo)


if __name__ == "__main__":
    unittest.main()
