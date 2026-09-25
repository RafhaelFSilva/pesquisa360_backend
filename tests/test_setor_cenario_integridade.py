"""Hardening P0 (ADR-075): Setor referenciado por cenario nao pode ser excluido.

O cenario e historico metodologico (snapshot oficial, operacional, observacao).
Se `lideranca_cenario_setores.setor_id` tivesse ON DELETE CASCADE, apagar o
Setor apagaria em silencio a linha historica de TODOS os cenarios -- RASCUNHO,
ATIVO e ARQUIVADO. A regra: qualquer referencia em cenario torna o Setor nao
removivel fisicamente (409); setor nunca usado continua excluivel.

Caso controlado: Universidade oficial 11.053 -> operacional 2.211.

Roda sobre o schema REAL (alembic upgrade head em SQLite com FK ligada), para
que a FK do banco -- e nao so a pre-checagem -- seja exercitada.
"""

import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import mock

from fastapi import HTTPException
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

os.environ.setdefault("SECRET_KEY", "test-only-lideranca-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import crud
from pesquisa360.api.endpoints import projetos as rotas_projetos
from pesquisa360.db import models
from pesquisa360.services import lideranca_cenario as cenarios

from tests.test_lideranca_cenarios import _CenarioFixture

OFICIAL = 11053
OPERACIONAL = 2211


class _SetorCenarioFixture(_CenarioFixture):
    def setUp(self):
        super().setUp()
        # Setor Norte (30) = bairro Universidade (11.053) na Base oficial.
        municipio = (
            self.session.query(models.TerritorioEleitoral)
            .filter(
                models.TerritorioEleitoral.base_eleitoral_id == self.base.id,
                models.TerritorioEleitoral.tipo == "MUNICIPIO",
            )
            .first()
        )
        self.universidade = models.TerritorioEleitoral(
            base_eleitoral_id=self.base.id,
            tipo="BAIRRO",
            nome="Universidade",
            nome_normalizado="universidade",
            parent_id=municipio.id,
            municipio_id=municipio.id,
            eleitorado_apto=OFICIAL,
        )
        self.session.add(self.universidade)
        self.session.commit()
        self._compor_setor(30, self.universidade)

    def _cenario_com_norte(self, nome="Campo Setembro", status="RASCUNHO"):
        cenario = self._criar(nome=nome)
        self._salvar(cenario, {30: OPERACIONAL})
        if status in ("ATIVO", "ARQUIVADO"):
            cenario = self._ativar(cenario)
        if status == "ARQUIVADO":
            cenario = cenarios.arquivar_cenario(self.session, 100, cenario.id, self.gerente_a)
        self.assertEqual(cenario.status, status)
        return cenario

    def _excluir(self, setor_id, projeto_id=100, pesquisa_id=10, usuario=None):
        return rotas_projetos.delete_setor_by_projeto_pesquisa(
            projeto_id=projeto_id,
            pesquisa_id=pesquisa_id,
            setor_id=setor_id,
            db=self.session,
            current_user=usuario or self.gerente_a,
        )

    def _setor_existe(self, setor_id):
        return self.session.execute(
            text(f"SELECT COUNT(*) FROM setores WHERE id = {setor_id}")
        ).scalar() == 1

    def _linha_bruta(self, cenario_id, setor_id):
        return self.session.execute(
            text(
                "SELECT eleitorado_oficial_referencia, eleitorado_operacional"
                f" FROM lideranca_cenario_setores WHERE cenario_id = {cenario_id}"
                f" AND setor_id = {setor_id}"
            )
        ).fetchone()

    def _assert_historico_intacto(self, cenario, status):
        self.assertTrue(self._setor_existe(30))
        self.session.expire_all()
        recarregado = self.session.get(models.LiderancaCenario, cenario.id)
        self.assertIsNotNone(recarregado)
        self.assertEqual(recarregado.status, status)
        linha = self._linha_bruta(cenario.id, 30)
        self.assertIsNotNone(linha, "linha cenario x setor desapareceu")
        self.assertEqual(tuple(linha), (OFICIAL, OPERACIONAL))


# =============================================================================
# Banco / modelagem
# =============================================================================


class FkTests(_SetorCenarioFixture):
    def test_model_nao_usa_cascade_destrutivo_em_setor_id(self):
        coluna = models.LiderancaCenarioSetor.__table__.c.setor_id
        fk = next(iter(coluna.foreign_keys))
        self.assertEqual(fk.column.table.name, "setores")
        self.assertNotEqual((fk.ondelete or "").upper(), "CASCADE")
        self.assertIn((fk.ondelete or "NO ACTION").upper(), ("RESTRICT", "NO ACTION"))

    def test_fk_de_cenario_id_permanece_cascade(self):
        # Escopo fechado: so setor_id muda. Remocao controlada do proprio
        # cenario continua levando as linhas junto.
        coluna = models.LiderancaCenarioSetor.__table__.c.cenario_id
        fk = next(iter(coluna.foreign_keys))
        self.assertEqual((fk.ondelete or "").upper(), "CASCADE")

    def test_schema_migrado_nao_tem_cascade_em_setor_id(self):
        fks = inspect(self.engine).get_foreign_keys("lideranca_cenario_setores")
        por_coluna = {tuple(fk["constrained_columns"]): fk for fk in fks}
        setor = por_coluna[("setor_id",)]
        self.assertNotEqual((setor["options"].get("ondelete") or "").upper(), "CASCADE")
        cenario = por_coluna[("cenario_id",)]
        self.assertEqual((cenario["options"].get("ondelete") or "").upper(), "CASCADE")

    def test_banco_rejeita_delete_direto_do_setor_referenciado(self):
        # Sem passar pela aplicacao: e a FK quem protege o historico.
        cenario = self._cenario_com_norte()
        with self.assertRaises(IntegrityError):
            self.session.execute(text("DELETE FROM setores WHERE id = 30"))
            self.session.commit()
        self.session.rollback()
        self._assert_historico_intacto(cenario, "RASCUNHO")

    def test_constraints_da_tabela_sobrevivem_a_migration(self):
        # UNIQUE(cenario, setor) e CHECK(operacional >= 0) continuam no schema
        # migrado (batch mode recria a tabela em SQLite).
        cenario = self._criar()
        self.session.add(
            models.LiderancaCenarioSetor(cenario_id=cenario.id, setor_id=31, eleitorado_operacional=1)
        )
        self.session.commit()
        with self.assertRaises(IntegrityError):
            self.session.add(
                models.LiderancaCenarioSetor(cenario_id=cenario.id, setor_id=31, eleitorado_operacional=2)
            )
            self.session.commit()
        self.session.rollback()
        with self.assertRaises(IntegrityError):
            self.session.add(
                models.LiderancaCenarioSetor(cenario_id=cenario.id, setor_id=30, eleitorado_operacional=-1)
            )
            self.session.commit()
        self.session.rollback()


# =============================================================================
# Regra de negocio via endpoint real
# =============================================================================


class ExclusaoBloqueadaTests(_SetorCenarioFixture):
    def _assert_bloqueado(self, status):
        cenario = self._cenario_com_norte(status=status)
        with self.assertRaises(HTTPException) as erro:
            self._excluir(30)
        self.assertEqual(erro.exception.status_code, 409)
        self.assertIn("cenários da Gestão de Lideranças", erro.exception.detail)
        self.assertNotIn("apague", erro.exception.detail.lower())
        self._assert_historico_intacto(cenario, status)

    def test_rascunho_bloqueia(self):
        self._assert_bloqueado("RASCUNHO")

    def test_ativo_bloqueia(self):
        self._assert_bloqueado("ATIVO")

    def test_arquivado_bloqueia(self):
        # ARQUIVADO e historico, nao lixo: continua protegendo o setor.
        self._assert_bloqueado("ARQUIVADO")

    def test_varios_cenarios_bloqueiam(self):
        setembro = self._cenario_com_norte(nome="Setembro", status="ATIVO")
        outubro = self._cenario_com_norte(nome="Outubro", status="ATIVO")  # arquiva Setembro
        self.session.expire_all()
        self.assertEqual(self.session.get(models.LiderancaCenario, setembro.id).status, "ARQUIVADO")
        with self.assertRaises(HTTPException) as erro:
            self._excluir(30)
        self.assertEqual(erro.exception.status_code, 409)
        self._assert_historico_intacto(setembro, "ARQUIVADO")
        self._assert_historico_intacto(outubro, "ATIVO")

    def test_snapshot_e_operacional_permanecem(self):
        cenario = self._cenario_com_norte(status="ATIVO")
        with self.assertRaises(HTTPException):
            self._excluir(30)
        linha = self._linha_bruta(cenario.id, 30)
        self.assertEqual(linha[0], OFICIAL)
        self.assertEqual(linha[1], OPERACIONAL)
        detalhe = self._detalhe(cenario)
        self.assertEqual(detalhe["total_eleitorado_operacional"], OPERACIONAL)
        norte = next(l for l in detalhe["setores"] if l["setor_id"] == 30)
        self.assertEqual(norte["eleitorado_oficial_referencia"], OFICIAL)

    def test_setor_livre_continua_excluivel(self):
        self._cenario_com_norte()  # referencia so o Norte (30)
        resposta = self._excluir(31)
        self.assertIn("sucesso", resposta["message"])
        self.assertFalse(self._setor_existe(31))
        self.assertTrue(self._setor_existe(30))

    def test_setor_de_cenario_arquivado_nao_e_liberado(self):
        cenario = self._cenario_com_norte(status="ARQUIVADO")
        self.assertTrue(crud.setor_possui_historico_cenario(self.session, setor_id=30))
        with self.assertRaises(HTTPException):
            self._excluir(30)
        self._assert_historico_intacto(cenario, "ARQUIVADO")

    def test_coletas_continuam_tendo_precedencia_de_mensagem(self):
        # Regra existente intacta: setor com coleta responde a mensagem de coletas.
        self.session.add(
            models.Coleta(
                pesquisa_id=10, agente_id=1, company_id=10, client_uuid="c-1",
                setor_id=30, status_sincronizacao="sincronizado",
                data_inicio_coleta=datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc),
            )
        )
        self.session.commit()
        with self.assertRaises(HTTPException) as erro:
            self._excluir(30)
        self.assertEqual(erro.exception.status_code, 409)
        self.assertIn("coletas vinculadas", erro.exception.detail)

    def test_sessao_continua_utilizavel_apos_o_bloqueio(self):
        self._cenario_com_norte()
        with self.assertRaises(HTTPException):
            self._excluir(30)
        self.assertEqual(self.session.query(models.Setor).count(), 4)
        self.assertIn("sucesso", self._excluir(31)["message"])


# =============================================================================
# Corrida, deteccao de constraint e multitenancy
# =============================================================================


class DefesaDoBancoTests(_SetorCenarioFixture):
    def test_referencia_concorrente_vira_409_pela_fk(self):
        # A pre-checagem nao ve nada (corrida); a FK barra; o rollback traduz.
        self._cenario_com_norte()
        with mock.patch.object(
            crud, "setor_possui_historico_cenario", side_effect=[False, True]
        ):
            with self.assertRaises(HTTPException) as erro:
                self._excluir(30)
        self.assertEqual(erro.exception.status_code, 409)
        self.assertIn("cenários", erro.exception.detail)
        self.assertTrue(self._setor_existe(30))

    def test_detector_reconhece_a_fk_do_cenario(self):
        def falha(constraint):
            orig = SimpleNamespace(diag=SimpleNamespace(constraint_name=constraint))
            return IntegrityError("stmt", {}, orig)

        self.assertTrue(rotas_projetos._conflito_cenario_do_setor(falha(rotas_projetos.FK_CENARIO_SETOR)))
        self.assertFalse(rotas_projetos._conflito_cenario_do_setor(falha(rotas_projetos.FK_COLETAS_SETOR)))
        self.assertFalse(rotas_projetos._conflito_coletas_do_setor(falha(rotas_projetos.FK_CENARIO_SETOR)))

    def test_integrityerror_desconhecido_sobe(self):
        outro = IntegrityError(
            "stmt", {}, SimpleNamespace(diag=SimpleNamespace(constraint_name="outra_coisa"))
        )
        with mock.patch.object(self.session, "commit", side_effect=outro):
            with self.assertRaises(IntegrityError):
                self._excluir(31)
        self.session.rollback()

    def test_tenant_cruzado_recebe_404_e_nao_409(self):
        cenario_b = cenarios.criar_cenario(
            self.session, 200, self.gerente_b, pesquisa_id=20, nome="Cenario B"
        )
        cenarios.salvar_setores(
            self.session, 200, cenario_b.id, self.gerente_b,
            setores=[{"setor_id": 40, "eleitorado_operacional": 5}],
        )
        with self.assertRaises(HTTPException) as erro:
            self._excluir(40, projeto_id=200, pesquisa_id=20, usuario=self.gerente_a)
        self.assertEqual(erro.exception.status_code, 404)
        self.assertNotIn("cenário", erro.exception.detail.lower())
        self.assertTrue(self._setor_existe(40))

    def test_helper_e_exists_sem_company_id(self):
        self.assertFalse(crud.setor_possui_historico_cenario(self.session, setor_id=30))
        self._cenario_com_norte()
        self.assertTrue(crud.setor_possui_historico_cenario(self.session, setor_id=30))
        self.assertFalse(crud.setor_possui_historico_cenario(self.session, setor_id=31))
