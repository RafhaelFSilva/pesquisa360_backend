"""Resposta-alvo explicita na Gestao de Liderancas (caracterizacao).

`resultado_principal.respostas_alvo` e o numerador EXATO da taxa alvo:
entrevistas/coletas DISTINTAS da amostra efetivamente usada (`base_valida`,
entrevistas com resposta reportavel para a pergunta alvo) que apresentaram a
resposta alvo. `taxa_alvo = respostas_alvo / base_valida`. A unidade e
entrevista, nunca voto. O Web apenas exibe; nunca reconstroi pelo percentual.

Casos controlados do escopo: 12/30 (40%), 3/44 (6,818%), 0/38 (0%), coleta
com multiplas linhas contada uma unica vez.
"""

import os
from datetime import datetime, timezone

from fastapi import HTTPException

os.environ.setdefault("SECRET_KEY", "test-only-lideranca-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.db import models
from pesquisa360.services import lideranca as lideranca_service

from tests.test_lideranca_cenarios import _CenarioFixture


class _RespostaAlvoFixture(_CenarioFixture):
    def _principal(self, lideranca, no_setor=True):
        resultado = self._analisar_no_setor() if no_setor else self._analisar()
        return self._item(resultado, lideranca)["resultado_principal"]

    def _assert_relacao(self, principal, total, alvo):
        self.assertEqual(principal["total_entrevistas"], total)
        self.assertEqual(principal["base_valida"], total)
        self.assertEqual(principal["respostas_alvo"], alvo)
        self.assertLessEqual(principal["respostas_alvo"], principal["base_valida"])
        self.assertLessEqual(principal["base_valida"], principal["total_entrevistas"])
        self.assertAlmostEqual(principal["taxa_alvo"], alvo / total, places=9)


class CasosControladosTests(_RespostaAlvoFixture):
    def test_caso_a_12_de_30(self):
        lideranca = self._lideranca_no_setor(setor_id=30, cota=500)
        self._semear_coletas(total=30, alvo=12)
        principal = self._principal(lideranca)
        self._assert_relacao(principal, 30, 12)
        self.assertEqual(principal["taxa_alvo"], 0.4)

    def test_caso_b_3_de_44(self):
        lideranca = self._lideranca_no_setor(setor_id=30, cota=23)
        self._semear_coletas(total=44, alvo=3)
        principal = self._principal(lideranca)
        self._assert_relacao(principal, 44, 3)
        self.assertAlmostEqual(principal["taxa_alvo"], 0.0681818, places=6)

    def test_caso_c_0_de_38_e_zero_nao_indisponivel(self):
        lideranca = self._lideranca_no_setor(setor_id=30, cota=22)
        self._semear_coletas(total=38, alvo=0)
        item = self._item(self._analisar_no_setor(), lideranca)
        principal = item["resultado_principal"]
        self._assert_relacao(principal, 38, 0)
        self.assertEqual(principal["taxa_alvo"], 0.0)
        # Houve medicao: zero e resultado, nao ausencia.
        self.assertIsNone(item["indisponibilidade"])
        self.assertEqual(principal["votos_projetados_alvo"], 0)
        self.assertEqual(principal["status"], "GAP")

    def test_caso_d_coleta_com_multiplas_linhas_conta_uma_vez(self):
        lideranca = self._lideranca_no_setor(setor_id=30, cota=500)
        self._semear_coletas(total=44, alvo=3)
        coletas = (
            self.session.query(models.Coleta)
            .filter(models.Coleta.pesquisa_id == 10)
            .order_by(models.Coleta.id)
            .all()
        )
        # Linha extra com a resposta alvo em uma coleta JA alvo e em uma coleta
        # nao-alvo: a primeira nao pode dobrar; a segunda passa a contar (1 vez).
        for coleta in (coletas[0], coletas[10]):
            self.session.add(
                models.Resposta(pergunta_id=42, coleta_id=coleta.id, valor_resposta="Candidato X")
            )
        self.session.commit()
        principal = self._principal(lideranca)
        self.assertEqual(principal["base_valida"], 44)
        self.assertEqual(principal["respostas_alvo"], 4)
        self.assertAlmostEqual(principal["taxa_alvo"], 4 / 44, places=9)


class SemanticaTests(_RespostaAlvoFixture):
    def test_sem_amostra_mantem_semantica_atual_de_indisponibilidade(self):
        lideranca = self._lideranca_no_setor(setor_id=30, cota=500)
        # Nenhuma coleta: nao existe medicao; nunca "0 de 0 = 0%".
        item = self._item(self._analisar_no_setor(), lideranca)
        principal = item["resultado_principal"]
        self.assertEqual(principal["base_valida"], 0)
        self.assertEqual(principal["respostas_alvo"], 0)
        self.assertIsNone(principal["taxa_alvo"])
        self.assertEqual(item["indisponibilidade"], "SEM_RESPOSTAS_VALIDAS")

    def test_denominador_e_a_base_valida_nao_o_total(self):
        lideranca = self._lideranca_no_setor(setor_id=30, cota=500)
        self._semear_coletas(total=44, alvo=3)
        # Uma coleta sem resposta reportavel para a pergunta alvo: sai da base
        # valida, mas continua no total de entrevistas.
        ultima = (
            self.session.query(models.Coleta)
            .filter(models.Coleta.pesquisa_id == 10)
            .order_by(models.Coleta.id.desc())
            .first()
        )
        self.session.query(models.Resposta).filter(
            models.Resposta.coleta_id == ultima.id, models.Resposta.pergunta_id == 42
        ).delete(synchronize_session=False)
        self.session.commit()
        principal = self._principal(lideranca)
        self.assertEqual(principal["total_entrevistas"], 44)
        self.assertEqual(principal["base_valida"], 43)
        self.assertEqual(principal["respostas_alvo"], 3)
        self.assertAlmostEqual(principal["taxa_alvo"], 3 / 43, places=9)

    def test_filtros_diagnosticos_tem_numerador_proprio_sem_alterar_o_principal(self):
        lideranca = self._lideranca_no_setor(setor_id=30, cota=500)
        # 30 entrevistas, 12 alvo; sexo F em 10 (4 delas no alvo).
        self._semear_coletas(total=30, alvo=12, sexo_f=10, alvo_em_f=4)
        resultado = self._analisar_no_setor(
            filtros_respostas=[{"pergunta_id": 43, "valores": ["F"]}]
        )
        item = self._item(resultado, lideranca)
        self._assert_relacao(item["resultado_principal"], 30, 12)
        recorte = item["recorte_filtrado"]
        self.assertEqual(recorte["base_valida"], 10)
        self.assertEqual(recorte["respostas_alvo"], 4)
        self.assertAlmostEqual(recorte["taxa_alvo"], 0.4, places=9)

    def test_escopo_pesquisa_sem_setor_usa_toda_a_onda(self):
        lideranca = self._lideranca_no_setor(setor_id=None, cota=3000)
        self._semear_coletas(total=100, alvo=50)
        principal = self._principal(lideranca, no_setor=False)
        self.assertEqual(principal["escopo_amostral"], "PESQUISA")
        self._assert_relacao(principal, 100, 50)


class NaoAlteraCalculosTests(_RespostaAlvoFixture):
    def test_cenario_operacional_ativo_nao_altera_a_contagem(self):
        lideranca = self._lideranca_no_setor(setor_id=30, cota=500)
        self._semear_coletas(total=44, alvo=3)
        antes = self._principal(lideranca)
        self._cenario_ativo({30: 2000})
        depois = self._principal(lideranca)
        for campo in ("total_entrevistas", "base_valida", "respostas_alvo", "taxa_alvo"):
            self.assertEqual(antes[campo], depois[campo], campo)
        # Projecao muda de base (oficial -> operacional); a amostra nao.
        self.assertNotEqual(antes["votos_projetados_alvo"], depois["votos_projetados_alvo"])

    def test_daniele_e_jesse_no_mesmo_setor_compartilham_a_amostra(self):
        # Comportamento ATUAL do escopo Setor: mesmo setor, mesma amostra, mesmo
        # contador. A individualizacao NAO e tratada nesta rodada.
        daniele = self._lideranca_no_setor(setor_id=30, cota=23, nome="Daniele")
        jesse = self._lideranca_no_setor(setor_id=30, cota=100, nome="Jesse")
        self._semear_coletas(total=44, alvo=3)
        resultado = self._analisar_no_setor()
        for lideranca in (daniele, jesse):
            principal = self._item(resultado, lideranca)["resultado_principal"]
            self._assert_relacao(principal, 44, 3)

    def test_projecao_e_gap_nao_mudam_com_a_exposicao_do_numerador(self):
        # Baseline documentado da suite: 10.000 aptos -> 7.200 -> 50% -> 3.600 -> +600.
        lideranca = self._lideranca_no_setor(setor_id=None, cota=3000)
        self._semear_coletas(total=100, alvo=50)
        principal = self._principal(lideranca, no_setor=False)
        self.assertEqual(principal["respostas_alvo"], 50)
        self.assertEqual(principal["votos_projetados_alvo"], 3600)
        self.assertEqual(principal["gap_plus"], 600)
        self.assertEqual(principal["status"], "PLUS")


class ContratoTests(_RespostaAlvoFixture):
    def test_json_da_api_expoe_o_numerador(self):
        cliente = self._cliente(self.gerente_a)
        lideranca = self._lideranca_no_setor(setor_id=None, cota=3000)
        self._semear_coletas(total=30, alvo=12)
        resposta = cliente.post(
            "/projetos/100/liderancas/analise",
            json={"pesquisa_id": 10, "alvo": {"pergunta_id": 42, "valores": ["Candidato X"]}},
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        principal = next(
            i for i in resposta.json()["liderancas"] if i["id"] == lideranca.id
        )["resultado_principal"]
        self.assertEqual(principal["base_valida"], 30)
        self.assertEqual(principal["respostas_alvo"], 12)
        self.assertEqual(principal["taxa_alvo"], 0.4)
        self.assertIsInstance(principal["respostas_alvo"], int)

    def test_outro_tenant_nao_le_a_contagem(self):
        self._lideranca_no_setor(setor_id=None, cota=3000)
        self._semear_coletas(total=30, alvo=12)
        with self.assertRaises(HTTPException) as erro:
            self._analisar(current_user=self.gerente_b)
        self.assertEqual(erro.exception.status_code, 404)

    def test_contagem_nao_toca_o_banco(self):
        # Nada persistido: nenhuma coluna de contagem em modelo algum.
        for tabela in (models.LiderancaPolitica, models.LiderancaPesquisaConfig, models.Coleta):
            self.assertNotIn("respostas_alvo", tabela.__table__.columns.keys())
