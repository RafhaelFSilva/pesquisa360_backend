"""Cenarios de Base Eleitoral Operacional na Gestao de Liderancas (ADR-075).

Reaproveita a fixture controlada de test_lideranca_politica:
  Bairro A 6.000 + Bairro B 4.000 = 10.000 aptos (Base oficial)
  comparecimento 0,80 x validos 0,90

Cenario operacional do escopo: Setor Norte (30) = 2.000, Setor Sul (31) = 1.000.
  2.000 x 0,80 x 0,90 = 1.440 votos validos; taxa 50% -> 720 projetados.
"""

import os
from datetime import date
from unittest import mock

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

os.environ.setdefault("SECRET_KEY", "test-only-lideranca-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.db import models
from pesquisa360.services import lideranca as lideranca_service
from pesquisa360.services import lideranca_analytics as analytics
from pesquisa360.services import lideranca_cenario as cenarios
from pesquisa360.services import setor_territorio

from tests.test_lideranca_politica import _LiderancaFixture


class _CenarioFixture(_LiderancaFixture):
    def _limpar(self):
        # As novas tabelas referenciam pesquisas/setores/usuarios: saem antes.
        for tabela in ("lideranca_cenario_setores", "lideranca_cenarios"):
            self.session.execute(text(f"DELETE FROM {tabela}"))
        self.session.commit()
        super()._limpar()

    # --- helpers de dominio ---------------------------------------------------

    def _criar(self, nome="Campo Setembro 2026", pesquisa_id=10, usuario=None, **extras):
        return cenarios.criar_cenario(
            self.session, 100, usuario or self.gerente_a,
            pesquisa_id=pesquisa_id, nome=nome, **extras,
        )

    def _salvar(self, cenario, valores, usuario=None):
        """valores: {setor_id: eleitorado_operacional} ou lista de dicts."""
        if isinstance(valores, dict):
            setores = [
                {"setor_id": setor_id, "eleitorado_operacional": valor}
                for setor_id, valor in valores.items()
            ]
        else:
            setores = valores
        return cenarios.salvar_setores(
            self.session, 100, cenario.id, usuario or self.gerente_a, setores=setores
        )

    def _ativar(self, cenario, usuario=None):
        return cenarios.ativar_cenario(self.session, 100, cenario.id, usuario or self.gerente_a)

    def _cenario_ativo(self, valores=None, nome="Campo Setembro 2026"):
        cenario = self._criar(nome=nome)
        self._salvar(cenario, valores or {30: 2000, 31: 1000})
        return self._ativar(cenario)

    def _detalhe(self, cenario):
        recarregado = cenarios.obter_cenario(self.session, 100, cenario.id, self.gerente_a)
        return cenarios.detalhe(self.session, 100, recarregado, self.gerente_a)

    def _linha(self, cenario, setor_id):
        return next(l for l in self._detalhe(cenario)["setores"] if l["setor_id"] == setor_id)

    def _compor_setor(self, setor_id, *territorios):
        for territorio in territorios:
            self.session.add(
                models.SetorTerritorioEleitoral(
                    setor_id=setor_id, territorio_eleitoral_id=territorio.id
                )
            )
        self.session.commit()

    def _lideranca_no_setor(self, setor_id=30, cota=500, nome="Joao", territorios=True):
        lideranca = self._criar_lideranca(nome=nome)
        lideranca_service.definir_config_pesquisa(
            self.session, 100, lideranca.id, 10, self.gerente_a,
            setor_id=setor_id, cota_votos_validos=cota,
        )
        if territorios:
            lideranca_service.definir_territorios(
                self.session, 100, lideranca.id,
                [self.bairro_a.id, self.bairro_b.id], self.gerente_a,
            )
        return lideranca

    def _item(self, resultado, lideranca):
        return next(i for i in resultado["liderancas"] if i["id"] == lideranca.id)

    def _analisar_no_setor(self, **overrides):
        """Analise com TODAS as coletas da onda classificadas no setor 30.

        As coletas da fixture nao tem coordenada; a classificacao espacial e
        responsabilidade do PostGIS, fora do escopo. Aqui so precisamos que o
        universo amostral do setor exista para a taxa fechar em 50%.
        """
        coletas = [
            row[0]
            for row in self.session.query(models.Coleta.id)
            .filter(models.Coleta.pesquisa_id == 10)
            .all()
        ]
        classificacao = {"classificados": {coleta_id: 30 for coleta_id in coletas}}
        with mock.patch.object(
            analytics.crud, "classificar_coletas_por_setor", return_value=classificacao
        ):
            return self._analisar(**overrides)


# =============================================================================
# Modelagem
# =============================================================================


class ModelagemTests(_CenarioFixture):
    def test_setor_nao_ganhou_campo_global_de_eleitorado_operacional(self):
        colunas = set(models.Setor.__table__.columns.keys())
        for proibida in ("eleitores_manual", "eleitorado_operacional", "eleitorado_manual"):
            self.assertNotIn(proibida, colunas)

    def test_cenario_pertence_a_onda_sem_company_id(self):
        colunas = set(models.LiderancaCenario.__table__.columns.keys())
        self.assertIn("pesquisa_id", colunas)
        self.assertNotIn("company_id", colunas)
        self.assertNotIn("projeto_id", colunas)

    def test_cenario_x_setor_e_unico(self):
        cenario = self._criar()
        self.session.add(
            models.LiderancaCenarioSetor(cenario_id=cenario.id, setor_id=30, eleitorado_operacional=1)
        )
        self.session.commit()
        with self.assertRaises(IntegrityError):
            self.session.add(
                models.LiderancaCenarioSetor(
                    cenario_id=cenario.id, setor_id=30, eleitorado_operacional=2
                )
            )
            self.session.commit()
        self.session.rollback()

    def test_banco_rejeita_operacional_negativo(self):
        cenario = self._criar()
        with self.assertRaises(IntegrityError):
            self.session.add(
                models.LiderancaCenarioSetor(
                    cenario_id=cenario.id, setor_id=30, eleitorado_operacional=-1
                )
            )
            self.session.commit()
        self.session.rollback()

    def test_banco_garante_um_unico_ativo_por_onda(self):
        # Simula a ativacao concorrente: duas transacoes gravando ATIVO na
        # mesma onda. O indice parcial e a ultima barreira.
        primeiro = self._criar(nome="A")
        segundo = self._criar(nome="B")
        self.session.execute(
            text(f"UPDATE lideranca_cenarios SET status='ATIVO' WHERE id={primeiro.id}")
        )
        self.session.commit()
        with self.assertRaises(IntegrityError):
            self.session.execute(
                text(f"UPDATE lideranca_cenarios SET status='ATIVO' WHERE id={segundo.id}")
            )
            self.session.commit()
        self.session.rollback()
        ativos = (
            self.session.query(models.LiderancaCenario)
            .filter(models.LiderancaCenario.status == "ATIVO")
            .count()
        )
        self.assertEqual(ativos, 1)

    def test_ativo_em_ondas_diferentes_convive(self):
        primeiro = self._criar(nome="Onda 1", pesquisa_id=10)
        segundo = self._criar(nome="Onda 2", pesquisa_id=11)
        for cenario in (primeiro, segundo):
            self.session.execute(
                text(f"UPDATE lideranca_cenarios SET status='ATIVO' WHERE id={cenario.id}")
            )
        self.session.commit()
        self.assertEqual(
            self.session.query(models.LiderancaCenario)
            .filter(models.LiderancaCenario.status == "ATIVO")
            .count(),
            2,
        )


# =============================================================================
# Ciclo de vida
# =============================================================================


class CicloDeVidaTests(_CenarioFixture):
    def test_criar_rascunho(self):
        cenario = self._criar(
            metodologia="Universo operacional das areas de influencia",
            data_referencia=date(2026, 9, 16),
        )
        self.assertEqual(cenario.status, "RASCUNHO")
        self.assertEqual(cenario.pesquisa_id, 10)
        self.assertEqual(cenario.criado_por_id, self.gerente_a.id)
        self.assertIsNone(cenario.ativado_em)
        self.assertEqual(cenario.setores, [])

    def test_criar_em_pesquisa_de_outro_projeto_e_404(self):
        with self.assertRaises(HTTPException) as contexto:
            self._criar(pesquisa_id=20)
        self.assertEqual(contexto.exception.status_code, 404)

    def test_listar_por_projeto_e_por_onda(self):
        self._criar(nome="Onda 1 A", pesquisa_id=10)
        self._criar(nome="Onda 1 B", pesquisa_id=10)
        self._criar(nome="Onda 2", pesquisa_id=11)
        todos = cenarios.listar_cenarios(self.session, 100, self.gerente_a)
        self.assertEqual(len(todos), 3)
        onda1 = cenarios.listar_cenarios(self.session, 100, self.gerente_a, pesquisa_id=10)
        self.assertEqual({c.nome for c in onda1}, {"Onda 1 A", "Onda 1 B"})

    def test_detalhe_lista_todos_os_setores_da_onda(self):
        cenario = self._criar()
        self._salvar(cenario, {30: 2000})
        detalhe = self._detalhe(cenario)
        # Setores 30 e 31 sao da onda 10; 32 (onda 11) e 40 (tenant B) nao entram.
        self.assertEqual([l["setor_id"] for l in detalhe["setores"]], [30, 31])
        norte = detalhe["setores"][0]
        self.assertTrue(norte["configurado"])
        self.assertEqual(norte["eleitorado_operacional"], 2000)
        sul = detalhe["setores"][1]
        self.assertFalse(sul["configurado"])
        self.assertIsNone(sul["eleitorado_operacional"])
        self.assertIsNone(sul["peso_operacional"])

    def test_editar_rascunho(self):
        cenario = self._criar()
        editado = cenarios.atualizar_cenario(
            self.session, 100, cenario.id, self.gerente_a,
            campos={"nome": " Campo Outubro ", "metodologia": "Nova", "data_referencia": date(2026, 10, 1)},
        )
        self.assertEqual(editado.nome, "Campo Outubro")
        self.assertEqual(editado.metodologia, "Nova")
        self.assertEqual(editado.data_referencia, date(2026, 10, 1))

    def test_ativo_nao_aceita_edicao_territorial_nem_de_metadados(self):
        cenario = self._cenario_ativo()
        with self.assertRaises(HTTPException) as territorial:
            self._salvar(cenario, {30: 9999})
        self.assertEqual(territorial.exception.status_code, 409)
        with self.assertRaises(HTTPException) as metadados:
            cenarios.atualizar_cenario(
                self.session, 100, cenario.id, self.gerente_a, campos={"nome": "X"}
            )
        self.assertEqual(metadados.exception.status_code, 409)
        # Nada mudou em silencio.
        self.assertEqual(self._linha(cenario, 30)["eleitorado_operacional"], 2000)
        self.assertEqual(self._detalhe(cenario)["nome"], "Campo Setembro 2026")

    def test_arquivado_tambem_e_imutavel(self):
        cenario = self._criar()
        cenarios.arquivar_cenario(self.session, 100, cenario.id, self.gerente_a)
        with self.assertRaises(HTTPException) as contexto:
            self._salvar(cenario, {30: 1})
        self.assertEqual(contexto.exception.status_code, 409)

    def test_duplicar_cria_rascunho_independente(self):
        original = self._cenario_ativo()
        copia = cenarios.duplicar_cenario(self.session, 100, original.id, self.gerente_a)
        self.assertNotEqual(copia.id, original.id)
        self.assertEqual(copia.status, "RASCUNHO")
        self.assertEqual(copia.nome, "Cópia de Campo Setembro 2026")
        self.assertEqual(copia.metodologia, original.metodologia)
        self.assertEqual(
            {(s.setor_id, s.eleitorado_operacional, s.eleitorado_oficial_referencia) for s in copia.setores},
            {(s.setor_id, s.eleitorado_operacional, s.eleitorado_oficial_referencia) for s in original.setores},
        )
        # Editar a copia nao toca o original.
        self._salvar(copia, {30: 2600, 31: 1000})
        self.assertEqual(self._linha(copia, 30)["eleitorado_operacional"], 2600)
        self.assertEqual(self._linha(original, 30)["eleitorado_operacional"], 2000)
        self.assertEqual(self._detalhe(original)["status"], "ATIVO")

    def test_arquivar_rascunho_e_ativo_e_nao_arquivar_duas_vezes(self):
        rascunho = self._criar(nome="R")
        arquivado = cenarios.arquivar_cenario(self.session, 100, rascunho.id, self.gerente_a)
        self.assertEqual(arquivado.status, "ARQUIVADO")
        self.assertIsNotNone(arquivado.arquivado_em)
        with self.assertRaises(HTTPException) as contexto:
            cenarios.arquivar_cenario(self.session, 100, rascunho.id, self.gerente_a)
        self.assertEqual(contexto.exception.status_code, 409)

        ativo = self._cenario_ativo()
        cenarios.arquivar_cenario(self.session, 100, ativo.id, self.gerente_a)
        self.assertIsNone(cenarios.obter_cenario_ativo(self.session, 10))

    def test_arquivado_nao_pode_ser_ativado(self):
        cenario = self._criar()
        self._salvar(cenario, {30: 10})
        cenarios.arquivar_cenario(self.session, 100, cenario.id, self.gerente_a)
        with self.assertRaises(HTTPException) as contexto:
            self._ativar(cenario)
        self.assertEqual(contexto.exception.status_code, 409)

    def test_sem_hard_delete(self):
        self.assertFalse(hasattr(cenarios, "excluir_cenario"))
        self.assertFalse(hasattr(cenarios, "remover_cenario"))


# =============================================================================
# Setores
# =============================================================================


class SetoresTests(_CenarioFixture):
    def test_salva_varios_setores_em_lote(self):
        cenario = self._criar()
        salvo = self._salvar(cenario, [
            {"setor_id": 30, "eleitorado_operacional": 2211, "observacao": "Area de influencia"},
            {"setor_id": 31, "eleitorado_operacional": 1817, "observacao": None},
        ])
        self.assertEqual({s.setor_id: s.eleitorado_operacional for s in salvo.setores}, {30: 2211, 31: 1817})
        self.assertEqual(
            next(s for s in salvo.setores if s.setor_id == 30).observacao, "Area de influencia"
        )

    def test_lote_substitui_integralmente(self):
        cenario = self._criar()
        self._salvar(cenario, {30: 2211, 31: 1817})
        self._salvar(cenario, {30: 2300})
        self.assertEqual({s.setor_id for s in cenario.setores}, {30})

    def test_setor_repetido_no_lote_e_422(self):
        cenario = self._criar()
        with self.assertRaises(HTTPException) as contexto:
            self._salvar(cenario, [
                {"setor_id": 30, "eleitorado_operacional": 1},
                {"setor_id": 30, "eleitorado_operacional": 2},
            ])
        self.assertEqual(contexto.exception.status_code, 422)

    def test_negativo_e_rejeitado(self):
        cenario = self._criar()
        with self.assertRaises(HTTPException) as contexto:
            self._salvar(cenario, {30: -5})
        self.assertEqual(contexto.exception.status_code, 422)
        self.assertEqual(cenario.setores, [])

    def test_entrada_invalida_e_rejeitada(self):
        cenario = self._criar()
        for invalido in ("2211", 2211.5, True, None, float("nan")):
            with self.subTest(valor=invalido):
                with self.assertRaises(HTTPException) as contexto:
                    self._salvar(cenario, [{"setor_id": 30, "eleitorado_operacional": invalido}])
                self.assertEqual(contexto.exception.status_code, 422)

    def test_lote_invalido_faz_rollback_completo(self):
        cenario = self._criar()
        self._salvar(cenario, {30: 100})
        with self.assertRaises(HTTPException):
            # Setor 32 e da onda 11: derruba o lote inteiro.
            self._salvar(cenario, {30: 200, 32: 5})
        self.session.expire_all()
        self.assertEqual(self._linha(cenario, 30)["eleitorado_operacional"], 100)

    def test_setor_de_outra_onda_ou_tenant_e_404(self):
        cenario = self._criar()
        for setor_id in (32, 40, 99999):
            with self.subTest(setor_id=setor_id):
                with self.assertRaises(HTTPException) as contexto:
                    self._salvar(cenario, {setor_id: 1})
                self.assertEqual(contexto.exception.status_code, 404)

    def test_soma_e_peso_operacional(self):
        cenario = self._criar()
        self._salvar(cenario, {30: 2211, 31: 1817})
        detalhe = self._detalhe(cenario)
        self.assertEqual(detalhe["total_eleitorado_operacional"], 4028)
        norte = next(l for l in detalhe["setores"] if l["setor_id"] == 30)
        sul = next(l for l in detalhe["setores"] if l["setor_id"] == 31)
        self.assertEqual(norte["peso_operacional"], 54.89)
        self.assertEqual(sul["peso_operacional"], 45.11)
        # Pesos fecham em ~100 (duas casas, arredondamento half-up).
        self.assertAlmostEqual(norte["peso_operacional"] + sul["peso_operacional"], 100.0, places=1)

    def test_peso_nao_divide_por_zero(self):
        cenario = self._criar()
        self._salvar(cenario, {30: 0, 31: 0})
        detalhe = self._detalhe(cenario)
        self.assertEqual(detalhe["total_eleitorado_operacional"], 0)
        self.assertTrue(all(l["peso_operacional"] is None for l in detalhe["setores"]))

    def test_snapshot_da_referencia_oficial_e_capturado_quando_existe(self):
        # Norte = A (6.000) + B (4.000) = 10.000 na Base oficial; Sul sem composicao.
        self._compor_setor(30, self.bairro_a, self.bairro_b)
        cenario = self._criar()
        self._salvar(cenario, {30: 2000, 31: 1000})
        norte = self._linha(cenario, 30)
        self.assertEqual(norte["eleitorado_oficial_referencia"], 10000)
        self.assertEqual(norte["eleitorado_oficial_atual"], 10000)
        self.assertEqual(norte["status_oficial"], "DISPONIVEL")
        sul = self._linha(cenario, 31)
        # Sem referencia segura: ausencia explicita, jamais valor fabricado.
        self.assertIsNone(sul["eleitorado_oficial_referencia"])
        self.assertEqual(sul["status_oficial"], "SEM_COMPOSICAO_ELEITORAL")
        self.assertIsNone(self._detalhe(cenario)["total_eleitorado_oficial_referencia"])

    def test_operacional_maior_que_oficial_nao_e_bloqueado(self):
        # Sem supor que operacional <= oficial: a UI destaca, o backend aceita.
        self._compor_setor(30, self.bairro_a, self.bairro_b)
        cenario = self._criar()
        salvo = self._salvar(cenario, {30: 12000})
        self.assertEqual(salvo.setores[0].eleitorado_operacional, 12000)

    def test_agente_nao_escreve(self):
        cenario = self._criar()
        for acao in (
            lambda: self._criar(usuario=self.agente_a),
            lambda: self._salvar(cenario, {30: 1}, usuario=self.agente_a),
            lambda: cenarios.duplicar_cenario(self.session, 100, cenario.id, self.agente_a),
            lambda: cenarios.ativar_cenario(self.session, 100, cenario.id, self.agente_a),
            lambda: cenarios.arquivar_cenario(self.session, 100, cenario.id, self.agente_a),
        ):
            with self.assertRaises(HTTPException) as contexto:
                acao()
            self.assertEqual(contexto.exception.status_code, 403)


# =============================================================================
# Ativacao
# =============================================================================


class AtivacaoTests(_CenarioFixture):
    def test_ativar_cenario_valido(self):
        cenario = self._cenario_ativo()
        self.assertEqual(cenario.status, "ATIVO")
        self.assertIsNotNone(cenario.ativado_em)
        self.assertEqual(cenario.ativado_por_id, self.gerente_a.id)
        self.assertEqual(cenarios.obter_cenario_ativo(self.session, 10).id, cenario.id)

    def test_nao_ativa_sem_valores(self):
        cenario = self._criar()
        with self.assertRaises(HTTPException) as contexto:
            self._ativar(cenario)
        self.assertEqual(contexto.exception.status_code, 422)
        self.assertIn("ao menos um setor", " ".join(contexto.exception.detail["problemas"]))
        self.assertEqual(self._detalhe(cenario)["status"], "RASCUNHO")

    def test_nao_ativa_com_total_zero(self):
        cenario = self._criar()
        self._salvar(cenario, {30: 0, 31: 0})
        with self.assertRaises(HTTPException) as contexto:
            self._ativar(cenario)
        self.assertEqual(contexto.exception.status_code, 422)
        self.assertIn("maior que zero", " ".join(contexto.exception.detail["problemas"]))

    def test_nao_ativa_incompleto_com_lideranca_territorial_dependente(self):
        self._lideranca_no_setor(setor_id=31, nome="Maria")
        cenario = self._criar()
        self._salvar(cenario, {30: 2000})
        with self.assertRaises(HTTPException) as contexto:
            self._ativar(cenario)
        self.assertEqual(contexto.exception.status_code, 422)
        self.assertIn("Sul", " ".join(contexto.exception.detail["problemas"]))

    def test_lideranca_sem_setor_nao_impede_ativacao(self):
        lideranca = self._criar_lideranca(nome="Solta")
        lideranca_service.definir_config_pesquisa(
            self.session, 100, lideranca.id, 10, self.gerente_a, cota_votos_validos=100
        )
        cenario = self._criar()
        self._salvar(cenario, {30: 2000})
        self.assertEqual(self._ativar(cenario).status, "ATIVO")

    def test_lideranca_inativa_nao_bloqueia(self):
        lideranca = self._lideranca_no_setor(setor_id=31)
        lideranca_service.desativar_lideranca(self.session, 100, lideranca.id, self.gerente_a)
        cenario = self._criar()
        self._salvar(cenario, {30: 2000})
        self.assertEqual(self._ativar(cenario).status, "ATIVO")

    def test_ativar_b_substitui_a_de_forma_transacional(self):
        a = self._cenario_ativo(nome="A")
        b = self._criar(nome="B")
        self._salvar(b, {30: 3000})
        self._ativar(b)
        self.session.expire_all()
        a_recarregado = self.session.get(models.LiderancaCenario, a.id)
        b_recarregado = self.session.get(models.LiderancaCenario, b.id)
        self.assertEqual(a_recarregado.status, "ARQUIVADO")
        self.assertIsNotNone(a_recarregado.arquivado_em)
        self.assertEqual(b_recarregado.status, "ATIVO")
        ativos = (
            self.session.query(models.LiderancaCenario)
            .filter(models.LiderancaCenario.pesquisa_id == 10, models.LiderancaCenario.status == "ATIVO")
            .all()
        )
        self.assertEqual([c.id for c in ativos], [b.id])

    def test_ativar_ja_ativo_e_409(self):
        cenario = self._cenario_ativo()
        with self.assertRaises(HTTPException) as contexto:
            self._ativar(cenario)
        self.assertEqual(contexto.exception.status_code, 409)

    def test_ativacao_concorrente_antes_do_lock_deixa_um_unico_ativo(self):
        # Outra conexao ativa B enquanto A espera o lock da pesquisa. Ao seguir,
        # A enxerga B como ATIVO e o arquiva na mesma transacao: o ultimo vence
        # e nunca existem dois ativos.
        a = self._criar(nome="A")
        self._salvar(a, {30: 1})
        b = self._criar(nome="B")
        self._salvar(b, {30: 1})

        original = cenarios._bloquear_pesquisa

        def _concorrente(db, pesquisa_id):
            original(db, pesquisa_id)
            with self.engine.connect() as outra:
                outra.execute(
                    text(f"UPDATE lideranca_cenarios SET status='ATIVO' WHERE id={b.id}")
                )
                outra.commit()

        with mock.patch.object(cenarios, "_bloquear_pesquisa", _concorrente):
            self._ativar(a)
        self.session.expire_all()
        ativos = (
            self.session.query(models.LiderancaCenario)
            .filter(models.LiderancaCenario.status == "ATIVO")
            .all()
        )
        self.assertEqual([c.id for c in ativos], [a.id])
        self.assertEqual(self.session.get(models.LiderancaCenario, b.id).status, "ARQUIVADO")

    def test_ativacao_concorrente_apos_a_conferencia_e_409_sem_dois_ativos(self):
        # Outra conexao comete um ATIVO DEPOIS da conferencia e ANTES da
        # promocao: o indice parcial estoura, a transacao e desfeita e o
        # cliente recebe 409. Nada fica meio salvo.
        a = self._criar(nome="A")
        self._salvar(a, {30: 1})
        b = self._criar(nome="B")
        self._salvar(b, {30: 1})

        flush_original = self.session.flush
        injetado = {"feito": False}

        def _flush_com_concorrencia(*args, **kwargs):
            # Dispara no flush que promove A (status pendente = ATIVO), ou seja,
            # depois da conferencia de "anteriores" e antes do UPDATE chegar ao banco.
            # `__dict__` evita lazy load: acessar `a.status` expirado dispararia
            # autoflush -> este wrapper -> recursao.
            if not injetado["feito"] and a.__dict__.get("status") == "ATIVO":
                injetado["feito"] = True
                with self.engine.connect() as outra:
                    outra.execute(
                        text(f"UPDATE lideranca_cenarios SET status='ATIVO' WHERE id={b.id}")
                    )
                    outra.commit()
            flush_original(*args, **kwargs)

        self.session.flush = _flush_com_concorrencia
        try:
            with self.assertRaises(HTTPException) as contexto:
                self._ativar(a)
        finally:
            del self.session.flush
        self.assertEqual(contexto.exception.status_code, 409)
        self.session.expire_all()
        ativos = (
            self.session.query(models.LiderancaCenario)
            .filter(models.LiderancaCenario.status == "ATIVO")
            .all()
        )
        self.assertEqual([c.id for c in ativos], [b.id])
        self.assertEqual(self.session.get(models.LiderancaCenario, a.id).status, "RASCUNHO")

    def test_ativacao_validada_no_backend_nao_no_cliente(self):
        problemas = cenarios.problemas_para_ativar(self.session, self._criar())
        self.assertTrue(problemas)


# =============================================================================
# Historico / snapshot
# =============================================================================


class HistoricoTests(_CenarioFixture):
    def test_snapshot_nao_acompanha_atualizacao_da_base(self):
        self._compor_setor(30, self.bairro_a, self.bairro_b)
        cenario = self._cenario_ativo({30: 2000, 31: 1000})
        self.assertEqual(self._linha(cenario, 30)["eleitorado_oficial_referencia"], 10000)

        # A Base oficial muda depois (novo eleitorado no Bairro A).
        self.bairro_a.eleitorado_apto = 6400
        self.session.commit()

        linha = self._linha(cenario, 30)
        self.assertEqual(linha["eleitorado_oficial_referencia"], 10000)  # historico
        self.assertEqual(linha["eleitorado_oficial_atual"], 10400)  # hoje
        self.assertEqual(linha["eleitorado_operacional"], 2000)

    def test_duplicar_copia_snapshot_sem_recapturar(self):
        self._compor_setor(30, self.bairro_a, self.bairro_b)
        original = self._cenario_ativo({30: 2000})
        self.bairro_a.eleitorado_apto = 6400
        self.session.commit()
        copia = cenarios.duplicar_cenario(self.session, 100, original.id, self.gerente_a)
        self.assertEqual(self._linha(copia, 30)["eleitorado_oficial_referencia"], 10000)
        # Ao regravar o RASCUNHO, a referencia atual e capturada de novo.
        self._salvar(copia, {30: 2000})
        self.assertEqual(self._linha(copia, 30)["eleitorado_oficial_referencia"], 10400)
        self.assertEqual(self._linha(original, 30)["eleitorado_oficial_referencia"], 10000)

    def test_varios_cenarios_coexistem_com_valores_diferentes(self):
        setembro = self._cenario_ativo({30: 2211}, nome="Setembro")
        outubro = cenarios.duplicar_cenario(self.session, 100, setembro.id, self.gerente_a)
        cenarios.atualizar_cenario(
            self.session, 100, outubro.id, self.gerente_a, campos={"nome": "Outubro"}
        )
        self._salvar(outubro, {30: 2600})
        revisao = cenarios.duplicar_cenario(self.session, 100, setembro.id, self.gerente_a)
        cenarios.atualizar_cenario(
            self.session, 100, revisao.id, self.gerente_a, campos={"nome": "Revisao"}
        )
        self._salvar(revisao, {30: 2350})
        valores = {
            c.nome: c.setores[0].eleitorado_operacional
            for c in cenarios.listar_cenarios(self.session, 100, self.gerente_a)
        }
        self.assertEqual(valores["Setembro"], 2211)
        self.assertEqual(len(valores), 3)
        self.assertEqual(sorted(valores.values()), [2211, 2350, 2600])


# =============================================================================
# Compatibilidade / integracao com os calculos
# =============================================================================


class CalculosTests(_CenarioFixture):
    def test_sem_cenario_ativo_mantem_calculo_legado(self):
        lideranca = self._lideranca_no_setor(setor_id=None, cota=3000)
        self._semear_coletas(total=100, alvo=50)
        resultado = self._analisar()
        self.assertEqual(resultado["base_calculo"]["modo"], "PADRAO")
        self.assertIsNone(resultado["base_calculo"]["cenario_id"])
        item = self._item(resultado, lideranca)
        # 10.000 x 0,80 x 0,90 = 7.200; taxa 50% -> 3.600; cota 3.000 -> +600
        self.assertEqual(item["universo_eleitoral"]["eleitorado_apto"], 10000)
        self.assertEqual(item["universo_eleitoral"]["votos_validos_projetados"], 7200)
        self.assertEqual(item["universo_eleitoral"]["origem"], "BASE_OFICIAL")
        self.assertEqual(item["resultado_principal"]["gap_plus"], 600)
        self.assertEqual(item["resultado_principal"]["status"], "PLUS")

    def test_rascunho_nao_altera_os_calculos(self):
        lideranca = self._lideranca_no_setor(setor_id=None, cota=3000)
        self._semear_coletas(total=100, alvo=50)
        rascunho = self._criar()
        self._salvar(rascunho, {30: 1})
        resultado = self._analisar()
        self.assertEqual(resultado["base_calculo"]["modo"], "PADRAO")
        self.assertEqual(self._item(resultado, lideranca)["universo_eleitoral"]["eleitorado_apto"], 10000)

    def test_com_cenario_ativo_usa_o_operacional_do_setor(self):
        lideranca = self._lideranca_no_setor(setor_id=30, cota=500)
        self._semear_coletas(total=100, alvo=50)
        cenario = self._cenario_ativo({30: 2000, 31: 1000})
        resultado = self._analisar_no_setor()

        base = resultado["base_calculo"]
        self.assertEqual(base["modo"], "CENARIO_OPERACIONAL")
        self.assertEqual(base["cenario_id"], cenario.id)
        self.assertEqual(base["cenario_nome"], "Campo Setembro 2026")
        self.assertEqual(base["total_eleitorado_operacional"], 3000)

        item = self._item(resultado, lideranca)
        # 2.000 x 0,80 x 0,90 = 1.440; taxa 50% -> 720; cota 500 -> +220
        self.assertEqual(item["universo_eleitoral"]["eleitorado_apto"], 2000)
        self.assertEqual(item["universo_eleitoral"]["votos_validos_projetados"], 1440)
        self.assertEqual(item["universo_eleitoral"]["origem"], "CENARIO_OPERACIONAL")
        self.assertEqual(item["resultado_principal"]["escopo_amostral"], "SETOR")
        self.assertEqual(item["resultado_principal"]["taxa_alvo"], 0.5)
        self.assertEqual(item["resultado_principal"]["votos_projetados_alvo"], 720)
        self.assertEqual(item["resultado_principal"]["gap_plus"], 220)
        self.assertEqual(item["resultado_principal"]["status"], "PLUS")
        self.assertIsNone(item["indisponibilidade"])
        # Os bairros da lideranca continuam listados: ancora territorial intacta.
        self.assertEqual(len(item["territorios"]), 2)

    def test_setor_com_varias_liderancas_usa_o_mesmo_operacional(self):
        joao = self._lideranca_no_setor(setor_id=30, cota=500, nome="Joao")
        maria = self._lideranca_no_setor(setor_id=30, cota=900, nome="Maria")
        self._semear_coletas(total=100, alvo=50)
        self._cenario_ativo({30: 2000})
        resultado = self._analisar_no_setor()
        for lideranca, esperado in ((joao, 220), (maria, -180)):
            item = self._item(resultado, lideranca)
            self.assertEqual(item["universo_eleitoral"]["eleitorado_apto"], 2000)
            self.assertEqual(item["resultado_principal"]["gap_plus"], esperado)

    def test_lideranca_sem_setor_dentro_do_cenario_mantem_comportamento_atual(self):
        lideranca = self._lideranca_no_setor(setor_id=None, cota=3000)
        self._semear_coletas(total=100, alvo=50)
        self._cenario_ativo({30: 2000})
        resultado = self._analisar()
        self.assertEqual(resultado["base_calculo"]["modo"], "CENARIO_OPERACIONAL")
        item = self._item(resultado, lideranca)
        self.assertEqual(item["universo_eleitoral"]["eleitorado_apto"], 10000)
        self.assertEqual(item["universo_eleitoral"]["origem"], "BASE_OFICIAL")
        self.assertEqual(item["resultado_principal"]["gap_plus"], 600)
        # Metrica territorial de setor segue N/A, como hoje.
        self.assertEqual(item["cobertura_eleitoral"]["motivo_indisponibilidade"], "SEM_SETOR_REFERENCIA")

    def test_setor_sem_valor_em_cenario_ativo_nao_faz_fallback(self):
        lideranca = self._lideranca_no_setor(setor_id=30, cota=500)
        self._semear_coletas(total=100, alvo=50)
        self._cenario_ativo({30: 2000})
        # Depois da ativacao a lideranca migra para o Sul, que nao esta no cenario.
        lideranca_service.definir_config_pesquisa(
            self.session, 100, lideranca.id, 10, self.gerente_a, setor_id=31, cota_votos_validos=500
        )
        item = self._item(self._analisar_no_setor(), lideranca)
        self.assertEqual(item["indisponibilidade"], "CENARIO_SETOR_NAO_CONFIGURADO")
        self.assertIsNone(item["universo_eleitoral"]["eleitorado_apto"])
        self.assertIsNone(item["universo_eleitoral"]["votos_validos_projetados"])
        self.assertIsNone(item["universo_eleitoral"]["origem"])
        self.assertIsNone(item["resultado_principal"]["gap_plus"])

    def test_cenario_exige_parametros_da_base_como_o_modo_padrao(self):
        self.base.comparecimento_estimado = None
        self.session.commit()
        lideranca = self._lideranca_no_setor(setor_id=30, cota=500)
        self._semear_coletas(total=100, alvo=50)
        self._cenario_ativo({30: 2000})
        item = self._item(self._analisar_no_setor(), lideranca)
        self.assertEqual(item["indisponibilidade"], "PARAMETROS_ELEITORAIS_AUSENTES")
        self.assertEqual(item["universo_eleitoral"]["eleitorado_apto"], 2000)

    def test_arquivar_o_ativo_volta_ao_modo_padrao(self):
        lideranca = self._lideranca_no_setor(setor_id=30, cota=500)
        self._semear_coletas(total=100, alvo=50)
        cenario = self._cenario_ativo({30: 2000})
        self.assertEqual(self._analisar_no_setor()["base_calculo"]["modo"], "CENARIO_OPERACIONAL")
        cenarios.arquivar_cenario(self.session, 100, cenario.id, self.gerente_a)
        resultado = self._analisar_no_setor()
        self.assertEqual(resultado["base_calculo"]["modo"], "PADRAO")
        item = self._item(resultado, lideranca)
        self.assertEqual(item["universo_eleitoral"]["eleitorado_apto"], 10000)
        self.assertEqual(item["universo_eleitoral"]["origem"], "BASE_OFICIAL")

    def test_trocar_o_ativo_troca_a_base_de_calculo(self):
        lideranca = self._lideranca_no_setor(setor_id=30, cota=500)
        self._semear_coletas(total=100, alvo=50)
        self._cenario_ativo({30: 2000}, nome="Setembro")
        outubro = self._criar(nome="Outubro")
        self._salvar(outubro, {30: 4000})
        self._ativar(outubro)
        resultado = self._analisar_no_setor()
        self.assertEqual(resultado["base_calculo"]["cenario_nome"], "Outubro")
        item = self._item(resultado, lideranca)
        # 4.000 x 0,72 = 2.880; 50% -> 1.440; cota 500 -> +940
        self.assertEqual(item["universo_eleitoral"]["eleitorado_apto"], 4000)
        self.assertEqual(item["resultado_principal"]["gap_plus"], 940)

    def test_base_eleitoral_oficial_permanece_intacta(self):
        self._compor_setor(30, self.bairro_a, self.bairro_b)
        antes_a = self.bairro_a.eleitorado_apto
        antes_b = self.bairro_b.eleitorado_apto
        universo_antes = setor_territorio.obter_universo_eleitoral_setor(
            self.session, 100, 10, 30, self.gerente_a
        )
        self.assertEqual(universo_antes["eleitorado_apto"], 10000)

        lideranca = self._lideranca_no_setor(setor_id=30, cota=500)
        self._semear_coletas(total=100, alvo=50)
        cenario = self._cenario_ativo({30: 2000, 31: 1000})
        resultado = self._analisar_no_setor()
        self.assertEqual(self._item(resultado, lideranca)["universo_eleitoral"]["eleitorado_apto"], 2000)

        self.session.expire_all()
        self.assertEqual(self.session.get(models.TerritorioEleitoral, self.bairro_a.id).eleitorado_apto, antes_a)
        self.assertEqual(self.session.get(models.TerritorioEleitoral, self.bairro_b.id).eleitorado_apto, antes_b)
        universo_depois = setor_territorio.obter_universo_eleitoral_setor(
            self.session, 100, 10, 30, self.gerente_a
        )
        self.assertEqual(universo_depois, universo_antes)
        # A cobertura territorial (intersecao de bairros) segue na Base oficial.
        cobertura = self._item(resultado, lideranca)["cobertura_eleitoral"]
        self.assertEqual(cobertura["universo_eleitoral_setor"], 10000)
        self.assertEqual(cobertura["cobertura_percentual"], 100.0)
        self.assertEqual(self._linha(cenario, 30)["eleitorado_oficial_atual"], 10000)

    def test_cenario_ativo_de_outra_onda_nao_interfere(self):
        outro = self._criar(nome="Onda 2", pesquisa_id=11)
        self._salvar(outro, {32: 500})
        self._ativar(outro)
        lideranca = self._lideranca_no_setor(setor_id=None, cota=3000)
        self._semear_coletas(total=100, alvo=50)
        resultado = self._analisar()
        self.assertEqual(resultado["base_calculo"]["modo"], "PADRAO")
        self.assertEqual(self._item(resultado, lideranca)["universo_eleitoral"]["eleitorado_apto"], 10000)

    def test_resolvedor_e_a_unica_fonte(self):
        base = cenarios.resolver_base_calculo(self.session, 10)
        self.assertEqual(base.modo, "PADRAO")
        self.assertIsNone(base.eleitorado_operacional(30))
        self.assertIsNone(base.total_operacional)
        self._cenario_ativo({30: 2000, 31: 1000})
        base = cenarios.resolver_base_calculo(self.session, 10)
        self.assertEqual(base.modo, "CENARIO_OPERACIONAL")
        self.assertEqual(base.eleitorado_operacional(30), 2000)
        self.assertEqual(base.eleitorado_operacional(31), 1000)
        self.assertIsNone(base.eleitorado_operacional(32))
        self.assertIsNone(base.eleitorado_operacional(None))
        self.assertEqual(base.total_operacional, 3000)

    def test_universo_do_setor_em_lote_bate_com_a_leitura_unitaria(self):
        self._compor_setor(30, self.bairro_a, self.bairro_b)
        lote = setor_territorio.obter_universos_eleitorais_setores(
            self.session, 100, 10, [30, 31, 32, 40], self.gerente_a
        )
        self.assertEqual(set(lote), {30, 31})  # 32 e de outra onda; 40 de outro tenant
        for setor_id in (30, 31):
            unitario = setor_territorio.obter_universo_eleitoral_setor(
                self.session, 100, 10, setor_id, self.gerente_a
            )
            self.assertEqual(lote[setor_id], unitario)


# =============================================================================
# Multitenancy e API
# =============================================================================


class MultitenancyTests(_CenarioFixture):
    def setUp(self):
        super().setUp()
        self.cenario_a = self._criar(nome="Cenario A")
        self._salvar(self.cenario_a, {30: 2000})

    def test_a_ve_a_e_b_ve_b(self):
        cenario_b = cenarios.criar_cenario(
            self.session, 200, self.gerente_b, pesquisa_id=20, nome="Cenario B"
        )
        de_a = cenarios.listar_cenarios(self.session, 100, self.gerente_a)
        de_b = cenarios.listar_cenarios(self.session, 200, self.gerente_b)
        self.assertEqual([c.id for c in de_a], [self.cenario_a.id])
        self.assertEqual([c.id for c in de_b], [cenario_b.id])

    def test_b_nao_lista_nem_le_a(self):
        with self.assertRaises(HTTPException) as listagem:
            cenarios.listar_cenarios(self.session, 100, self.gerente_b)
        self.assertEqual(listagem.exception.status_code, 404)
        with self.assertRaises(HTTPException) as leitura:
            cenarios.obter_cenario(self.session, 100, self.cenario_a.id, self.gerente_b)
        self.assertEqual(leitura.exception.status_code, 404)
        self.assertNotEqual(leitura.exception.status_code, 403)

    def test_b_nao_altera_salva_duplica_ativa_nem_arquiva_a(self):
        acoes = {
            "patch": lambda: cenarios.atualizar_cenario(
                self.session, 100, self.cenario_a.id, self.gerente_b, campos={"nome": "X"}
            ),
            "setores": lambda: self._salvar(self.cenario_a, {30: 1}, usuario=self.gerente_b),
            "duplicar": lambda: cenarios.duplicar_cenario(
                self.session, 100, self.cenario_a.id, self.gerente_b
            ),
            "ativar": lambda: cenarios.ativar_cenario(
                self.session, 100, self.cenario_a.id, self.gerente_b
            ),
            "arquivar": lambda: cenarios.arquivar_cenario(
                self.session, 100, self.cenario_a.id, self.gerente_b
            ),
        }
        for nome, acao in acoes.items():
            with self.subTest(acao=nome):
                with self.assertRaises(HTTPException) as contexto:
                    acao()
                self.assertEqual(contexto.exception.status_code, 404)
        self.session.expire_all()
        recarregado = self.session.get(models.LiderancaCenario, self.cenario_a.id)
        self.assertEqual(recarregado.nome, "Cenario A")
        self.assertEqual(recarregado.status, "RASCUNHO")
        self.assertEqual(recarregado.setores[0].eleitorado_operacional, 2000)
        self.assertEqual(self.session.query(models.LiderancaCenario).count(), 1)

    def test_b_nao_alcanca_a_pelo_proprio_projeto(self):
        # Mesmo informando o projeto de B na URL, o cenario de A nao existe para B.
        with self.assertRaises(HTTPException) as contexto:
            cenarios.obter_cenario(self.session, 200, self.cenario_a.id, self.gerente_b)
        self.assertEqual(contexto.exception.status_code, 404)


class ApiTests(_CenarioFixture):
    def test_fluxo_completo_via_api(self):
        cliente = self._cliente(self.gerente_a)
        self._compor_setor(30, self.bairro_a, self.bairro_b)

        criado = cliente.post(
            "/projetos/100/liderancas/cenarios",
            json={
                "pesquisa_id": 10,
                "nome": "Campo Setembro 2026",
                "metodologia": "Universo operacional das areas de influencia",
                "data_referencia": "2026-09-16",
            },
        )
        self.assertEqual(criado.status_code, 201, criado.text)
        corpo = criado.json()
        cenario_id = corpo["id"]
        self.assertEqual(corpo["status"], "RASCUNHO")
        self.assertEqual(corpo["data_referencia"], "2026-09-16")
        self.assertEqual([l["setor_id"] for l in corpo["setores"]], [30, 31])
        self.assertEqual(corpo["setores"][0]["eleitorado_oficial_atual"], 10000)

        ativo = cliente.get("/projetos/100/liderancas/cenarios/ativo?pesquisa_id=10")
        self.assertEqual(ativo.status_code, 200, ativo.text)
        self.assertEqual(ativo.json()["base_calculo"]["modo"], "PADRAO")
        self.assertIsNone(ativo.json()["cenario"])

        salvo = cliente.put(
            f"/projetos/100/liderancas/cenarios/{cenario_id}/setores",
            json={
                "setores": [
                    {"setor_id": 30, "eleitorado_operacional": 2211, "observacao": "Influencia"},
                    {"setor_id": 31, "eleitorado_operacional": 1817, "observacao": None},
                ]
            },
        )
        self.assertEqual(salvo.status_code, 200, salvo.text)
        self.assertEqual(salvo.json()["total_eleitorado_operacional"], 4028)
        self.assertEqual(salvo.json()["setores"][0]["peso_operacional"], 54.89)
        self.assertEqual(salvo.json()["setores"][0]["eleitorado_oficial_referencia"], 10000)

        editado = cliente.patch(
            f"/projetos/100/liderancas/cenarios/{cenario_id}", json={"nome": "Setembro"}
        )
        self.assertEqual(editado.status_code, 200, editado.text)
        self.assertEqual(editado.json()["nome"], "Setembro")

        ativado = cliente.post(f"/projetos/100/liderancas/cenarios/{cenario_id}/ativar")
        self.assertEqual(ativado.status_code, 200, ativado.text)
        self.assertEqual(ativado.json()["status"], "ATIVO")

        ativo = cliente.get("/projetos/100/liderancas/cenarios/ativo?pesquisa_id=10").json()
        self.assertEqual(ativo["base_calculo"]["modo"], "CENARIO_OPERACIONAL")
        self.assertEqual(ativo["base_calculo"]["cenario_id"], cenario_id)
        self.assertEqual(ativo["base_calculo"]["total_eleitorado_operacional"], 4028)
        self.assertEqual(ativo["cenario"]["nome"], "Setembro")

        bloqueado = cliente.put(
            f"/projetos/100/liderancas/cenarios/{cenario_id}/setores",
            json={"setores": [{"setor_id": 30, "eleitorado_operacional": 1}]},
        )
        self.assertEqual(bloqueado.status_code, 409)

        copia = cliente.post(f"/projetos/100/liderancas/cenarios/{cenario_id}/duplicar")
        self.assertEqual(copia.status_code, 201, copia.text)
        self.assertEqual(copia.json()["status"], "RASCUNHO")
        self.assertEqual(copia.json()["nome"], "Cópia de Setembro")
        self.assertEqual(copia.json()["total_eleitorado_operacional"], 4028)

        lista = cliente.get("/projetos/100/liderancas/cenarios?pesquisa_id=10").json()
        self.assertEqual({c["status"] for c in lista}, {"ATIVO", "RASCUNHO"})

        arquivado = cliente.post(f"/projetos/100/liderancas/cenarios/{cenario_id}/arquivar")
        self.assertEqual(arquivado.status_code, 200, arquivado.text)
        self.assertEqual(arquivado.json()["status"], "ARQUIVADO")
        ativo = cliente.get("/projetos/100/liderancas/cenarios/ativo?pesquisa_id=10").json()
        self.assertEqual(ativo["base_calculo"]["modo"], "PADRAO")

    def test_ativacao_invalida_devolve_422_com_problemas(self):
        cliente = self._cliente(self.gerente_a)
        cenario_id = cliente.post(
            "/projetos/100/liderancas/cenarios", json={"pesquisa_id": 10, "nome": "Vazio"}
        ).json()["id"]
        resposta = cliente.post(f"/projetos/100/liderancas/cenarios/{cenario_id}/ativar")
        self.assertEqual(resposta.status_code, 422)
        self.assertIn("problemas", resposta.json()["detail"])

    def test_entradas_invalidas_sao_422(self):
        cliente = self._cliente(self.gerente_a)
        cenario_id = cliente.post(
            "/projetos/100/liderancas/cenarios", json={"pesquisa_id": 10, "nome": "X"}
        ).json()["id"]
        url = f"/projetos/100/liderancas/cenarios/{cenario_id}/setores"
        for valor in ("2211", 2211.5, -1, None, True):
            with self.subTest(valor=valor):
                resposta = cliente.put(
                    url, json={"setores": [{"setor_id": 30, "eleitorado_operacional": valor}]}
                )
                self.assertEqual(resposta.status_code, 422, resposta.text)
        duplicado = cliente.put(
            url,
            json={
                "setores": [
                    {"setor_id": 30, "eleitorado_operacional": 1},
                    {"setor_id": 30, "eleitorado_operacional": 2},
                ]
            },
        )
        self.assertEqual(duplicado.status_code, 422)
        sem_nome = cliente.post(
            "/projetos/100/liderancas/cenarios", json={"pesquisa_id": 10, "nome": "   "}
        )
        self.assertEqual(sem_nome.status_code, 422)

    def test_requests_rejeitam_company_id(self):
        cliente = self._cliente(self.gerente_a)
        cenario_id = cliente.post(
            "/projetos/100/liderancas/cenarios", json={"pesquisa_id": 10, "nome": "X"}
        ).json()["id"]
        respostas = [
            cliente.post(
                "/projetos/100/liderancas/cenarios",
                json={"pesquisa_id": 10, "nome": "Y", "company_id": 20},
            ),
            cliente.patch(
                f"/projetos/100/liderancas/cenarios/{cenario_id}",
                json={"nome": "Z", "company_id": 20},
            ),
            cliente.put(
                f"/projetos/100/liderancas/cenarios/{cenario_id}/setores",
                json={"setores": [], "company_id": 20},
            ),
        ]
        for resposta in respostas:
            with self.subTest(url=resposta.request.url):
                self.assertEqual(resposta.status_code, 422)

    def test_tenant_b_recebe_404_em_todas_as_rotas(self):
        cenario = self._criar()
        self._salvar(cenario, {30: 1})
        cliente_b = self._cliente(self.gerente_b)
        base = f"/projetos/100/liderancas/cenarios/{cenario.id}"
        respostas = [
            cliente_b.get("/projetos/100/liderancas/cenarios"),
            cliente_b.get("/projetos/100/liderancas/cenarios/ativo?pesquisa_id=10"),
            cliente_b.get(base),
            cliente_b.patch(base, json={"nome": "X"}),
            cliente_b.put(f"{base}/setores", json={"setores": []}),
            cliente_b.post(f"{base}/duplicar"),
            cliente_b.post(f"{base}/ativar"),
            cliente_b.post(f"{base}/arquivar"),
            cliente_b.post("/projetos/100/liderancas/cenarios", json={"pesquisa_id": 10, "nome": "X"}),
        ]
        for resposta in respostas:
            with self.subTest(url=str(resposta.request.url), metodo=resposta.request.method):
                self.assertEqual(resposta.status_code, 404, resposta.text)

    def test_analise_via_api_declara_a_base_de_calculo(self):
        cliente = self._cliente(self.gerente_a)
        lideranca = self._lideranca_no_setor(setor_id=None, cota=3000)
        self._semear_coletas(total=100, alvo=50)
        resposta = cliente.post(
            "/projetos/100/liderancas/analise",
            json={"pesquisa_id": 10, "alvo": {"pergunta_id": 42, "valores": ["Candidato X"]}},
        )
        self.assertEqual(resposta.status_code, 200, resposta.text)
        self.assertEqual(resposta.json()["base_calculo"]["modo"], "PADRAO")
        item = next(i for i in resposta.json()["liderancas"] if i["id"] == lideranca.id)
        self.assertEqual(item["universo_eleitoral"]["origem"], "BASE_OFICIAL")

        self._cenario_ativo({30: 2000})
        resposta = cliente.post(
            "/projetos/100/liderancas/analise",
            json={"pesquisa_id": 10, "alvo": {"pergunta_id": 42, "valores": ["Candidato X"]}},
        )
        self.assertEqual(resposta.json()["base_calculo"]["modo"], "CENARIO_OPERACIONAL")
        self.assertEqual(resposta.json()["base_calculo"]["cenario_nome"], "Campo Setembro 2026")
