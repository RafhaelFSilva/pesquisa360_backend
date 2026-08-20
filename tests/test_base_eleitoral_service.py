"""Testes do servico de dominio da Base Eleitoral (Fase 3A).

Cobre visibilidade multitenant, vinculo Projeto x Base, maquina de estados,
resolucao humana de divergencia e o motor de absorcao do legado.
"""

import os
import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-base-eleitoral-service-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.db import models
from pesquisa360.services import base_eleitoral as service
from pesquisa360.services import base_eleitoral_import as core
from pesquisa360.services import base_eleitoral_legado as legado

from tests.test_base_eleitoral_import import run_alembic_upgrade


class _ServicoFixture(unittest.TestCase):
    """Dois tenants, tres bases (oficial, privada A, privada B)."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="pesquisa360-service-"))
        cls.db_path = cls.temp_dir / "service.db"
        run_alembic_upgrade(f"sqlite:///{cls.db_path.as_posix()}")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def setUp(self):
        self.engine = create_engine(f"sqlite:///{self.db_path.as_posix()}")

        @event.listens_for(self.engine, "connect")
        def _preparar(dbapi_connection, _record):
            dbapi_connection.execute("PRAGMA foreign_keys=ON")
            dbapi_connection.create_function("GeomFromEWKT", 1, lambda valor: valor)
            dbapi_connection.create_function("ST_GeomFromEWKT", 1, lambda valor: valor)
            dbapi_connection.create_function("AsEWKB", 1, lambda valor: valor)

        self.Session = sessionmaker(bind=self.engine)
        self.session = self.Session()
        self.addCleanup(self.engine.dispose)
        self.addCleanup(self.session.close)
        self._limpar()
        self._semear()

    def _limpar(self):
        for tabela in (
            "importacao_base_eleitoral",
            "projeto_base_eleitoral",
            "territorio_eleitoral",
            "base_eleitoral",
            "bairros",
            "locais_votacao",
            "projetos",
            "usuarios",
            "companies",
            "perfis",
        ):
            self.session.execute(text(f"DELETE FROM {tabela}"))
        self.session.commit()

    def _semear(self):
        self.session.execute(
            text(
                "INSERT INTO perfis (id, nome) VALUES"
                " (1, 'Superadmin'), (2, 'Gerente'), (3, 'Agente')"
            )
        )
        self.session.execute(
            text(
                "INSERT INTO companies (id, name, is_active) VALUES"
                " (10, 'Empresa A', 1), (20, 'Empresa B', 1)"
            )
        )
        self.session.execute(
            text(
                "INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id)"
                " VALUES"
                " (1, 'gerente.a@a', 'Gerente A', 'x', 1, 2, 10),"
                " (2, 'gerente.b@b', 'Gerente B', 'x', 1, 2, 20),"
                " (3, 'agente.a@a', 'Agente A', 'x', 1, 3, 10),"
                " (4, 'super@a', 'Super', 'x', 1, 1, 10)"
            )
        )
        self.session.execute(
            text(
                "INSERT INTO projetos (id, nome, status, data_inicio, coordenador_id, company_id)"
                " VALUES (100, 'Campanha A', 'Ativo', '2026-01-01', 1, 10),"
                " (200, 'Campanha B', 'Ativo', '2026-01-01', 2, 20)"
            )
        )
        self.session.commit()

        self.gerente_a = self.session.get(models.Usuario, 1)
        self.gerente_b = self.session.get(models.Usuario, 2)
        self.agente_a = self.session.get(models.Usuario, 3)
        self.superadmin = self.session.get(models.Usuario, 4)

        self.oficial = self._criar_base(company_id=None, nome="Oficial", versao="2026.1")
        self.privada_a = self._criar_base(company_id=10, nome="Privada A", versao="2026.1")
        self.privada_b = self._criar_base(company_id=20, nome="Privada B", versao="2026.1")

    def _criar_base(self, **overrides):
        dados = dict(
            nome="Base",
            ano=2026,
            uf="AP",
            fonte="FIXTURE",
            versao="2026.1",
            data_referencia=date(2026, 1, 1),
            criado_por_id=1,
        )
        dados.update(overrides)
        base = models.BaseEleitoral(**dados)
        self.session.add(base)
        self.session.commit()
        return base

    def _registros(self, detalhes=(400, 350, 250)):
        registros = [
            core.RegistroTerritorioImportacao(tipo="ESTADO", nome="Amapá", chave="e1"),
            core.RegistroTerritorioImportacao(
                tipo="MUNICIPIO",
                nome="Macapá",
                chave="m1",
                parent_chave="e1",
                codigo="6050",
                eleitorado_apto=1000,
            ),
        ]
        for indice, valor in enumerate(detalhes, start=1):
            registros.append(
                core.RegistroTerritorioImportacao(
                    tipo="BAIRRO",
                    nome="Bairro {}".format(indice),
                    chave="b{}".format(indice),
                    parent_chave="m1",
                    municipio_chave="m1",
                    eleitorado_apto=valor,
                )
            )
        return registros

    def _importar(self, base, detalhes=(400, 350, 250), conteudo=b"lote"):
        return core.importar_registros(
            self.session,
            base_eleitoral=base,
            registros=self._registros(detalhes),
            arquivo_origem="fixture.csv",
            executado_por_id=1,
            conteudo_arquivo=conteudo,
        )

    def _municipio(self, base):
        return (
            self.session.query(models.TerritorioEleitoral)
            .filter(
                models.TerritorioEleitoral.base_eleitoral_id == base.id,
                models.TerritorioEleitoral.tipo == "MUNICIPIO",
            )
            .one()
        )


class VisibilidadeTests(_ServicoFixture):
    def test_tenant_a_enxerga_oficial_e_privada_a(self):
        visiveis = {
            base.id for base in service.listar_bases_eleitorais_visiveis(self.session, self.gerente_a)
        }
        self.assertIn(self.oficial.id, visiveis)
        self.assertIn(self.privada_a.id, visiveis)
        self.assertNotIn(self.privada_b.id, visiveis)

    def test_tenant_b_enxerga_oficial_e_privada_b(self):
        visiveis = {
            base.id for base in service.listar_bases_eleitorais_visiveis(self.session, self.gerente_b)
        }
        self.assertIn(self.oficial.id, visiveis)
        self.assertIn(self.privada_b.id, visiveis)
        self.assertNotIn(self.privada_a.id, visiveis)

    def test_base_privada_de_outro_tenant_retorna_404(self):
        with self.assertRaises(HTTPException) as contexto:
            service.obter_base_eleitoral_visivel(self.session, self.privada_b.id, self.gerente_a)
        self.assertEqual(contexto.exception.status_code, 404)

    def test_base_inexistente_retorna_404(self):
        with self.assertRaises(HTTPException) as contexto:
            service.obter_base_eleitoral_visivel(self.session, 999999, self.gerente_a)
        self.assertEqual(contexto.exception.status_code, 404)

    def test_oficial_e_visivel_para_ambos(self):
        for usuario in (self.gerente_a, self.gerente_b):
            with self.subTest(usuario=usuario.email):
                base = service.obter_base_eleitoral_visivel(
                    self.session, self.oficial.id, usuario
                )
                self.assertIsNone(base.company_id)


class PermissaoTests(_ServicoFixture):
    def test_base_oficial_so_aceita_superadmin(self):
        with self.assertRaises(HTTPException) as contexto:
            service.assegurar_permissao_escrita_base(self.session, self.oficial, self.gerente_a)
        self.assertEqual(contexto.exception.status_code, 403)
        service.assegurar_permissao_escrita_base(self.session, self.oficial, self.superadmin)

    def test_base_privada_aceita_gerente_do_tenant(self):
        service.assegurar_permissao_escrita_base(self.session, self.privada_a, self.gerente_a)

    def test_agente_nao_pode_escrever_em_base_eleitoral(self):
        for base in (self.oficial, self.privada_a):
            with self.subTest(base=base.nome):
                with self.assertRaises(HTTPException) as contexto:
                    service.assegurar_permissao_escrita_base(
                        self.session, base, self.agente_a
                    )
                self.assertEqual(contexto.exception.status_code, 403)

    def test_agente_nao_pode_validar_base(self):
        with self.assertRaises(HTTPException) as contexto:
            service.validar_base_eleitoral(self.session, self.privada_a.id, self.agente_a)
        self.assertEqual(contexto.exception.status_code, 403)


class VinculoProjetoTests(_ServicoFixture):
    def test_projeto_a_vincula_base_oficial(self):
        vinculo = service.vincular_base_eleitoral_ao_projeto(
            self.session, 100, self.oficial.id, self.gerente_a
        )
        self.assertEqual(vinculo.projeto_id, 100)
        self.assertTrue(vinculo.principal)

    def test_projeto_a_vincula_base_privada_a(self):
        vinculo = service.vincular_base_eleitoral_ao_projeto(
            self.session, 100, self.privada_a.id, self.gerente_a
        )
        self.assertEqual(vinculo.base_eleitoral_id, self.privada_a.id)

    def test_projeto_a_nao_vincula_base_privada_b(self):
        with self.assertRaises(HTTPException) as contexto:
            service.vincular_base_eleitoral_ao_projeto(
                self.session, 100, self.privada_b.id, self.gerente_a
            )
        self.assertEqual(contexto.exception.status_code, 404)
        self.assertEqual(self.session.query(models.ProjetoBaseEleitoral).count(), 0)

    def test_projeto_de_outro_tenant_retorna_404(self):
        with self.assertRaises(HTTPException) as contexto:
            service.vincular_base_eleitoral_ao_projeto(
                self.session, 200, self.oficial.id, self.gerente_a
            )
        self.assertEqual(contexto.exception.status_code, 404)

    def test_troca_de_principal_preserva_historico(self):
        primeiro = service.vincular_base_eleitoral_ao_projeto(
            self.session, 100, self.privada_a.id, self.gerente_a, principal=True
        )
        segundo = service.vincular_base_eleitoral_ao_projeto(
            self.session, 100, self.oficial.id, self.gerente_a, principal=True
        )
        self.session.refresh(primeiro)
        self.assertFalse(primeiro.principal)
        self.assertTrue(segundo.principal)
        # Historico permanece: nenhum DELETE.
        self.assertEqual(self.session.query(models.ProjetoBaseEleitoral).count(), 2)

    def test_revincular_a_mesma_base_nao_duplica(self):
        service.vincular_base_eleitoral_ao_projeto(
            self.session, 100, self.oficial.id, self.gerente_a
        )
        service.vincular_base_eleitoral_ao_projeto(
            self.session, 100, self.oficial.id, self.gerente_a
        )
        self.assertEqual(self.session.query(models.ProjetoBaseEleitoral).count(), 1)


class StatusTests(_ServicoFixture):
    def _vincular_principal(self, base):
        return service.vincular_base_eleitoral_ao_projeto(
            self.session, 100, base.id, self.gerente_a, principal=True
        )

    def test_importada_sem_divergencia_nao_vira_validada_sozinha(self):
        self._importar(self.privada_a)
        self.session.refresh(self.privada_a)
        self.assertEqual(self.privada_a.status, "IMPORTADA")

    def test_calculo_rejeita_base_importada(self):
        self._importar(self.privada_a)
        self._vincular_principal(self.privada_a)
        with self.assertRaises(service.BaseEleitoralNaoValidadaError) as contexto:
            service.obter_base_principal_projeto_para_calculo(self.session, 100, self.gerente_a)
        self.assertEqual(contexto.exception.status_atual, "IMPORTADA")

    def test_calculo_rejeita_base_em_conferencia(self):
        self._importar(self.privada_a, detalhes=(400, 350, 200))
        self._vincular_principal(self.privada_a)
        with self.assertRaises(service.BaseEleitoralNaoValidadaError) as contexto:
            service.obter_base_principal_projeto_para_calculo(self.session, 100, self.gerente_a)
        self.assertEqual(contexto.exception.status_atual, "EM_CONFERENCIA")

    def test_conferencia_aceita_base_nao_validada(self):
        self._importar(self.privada_a, detalhes=(400, 350, 200))
        self._vincular_principal(self.privada_a)
        base = service.obter_base_principal_projeto_para_conferencia(
            self.session, 100, self.gerente_a
        )
        self.assertEqual(base.status, "EM_CONFERENCIA")

    def test_calculo_aceita_base_validada(self):
        self._importar(self.privada_a)
        self._vincular_principal(self.privada_a)
        service.validar_base_eleitoral(self.session, self.privada_a.id, self.gerente_a)
        base = service.obter_base_principal_projeto_para_calculo(self.session, 100, self.gerente_a)
        self.assertEqual(base.status, "VALIDADA")

    def test_calculo_rejeita_base_substituida(self):
        self._importar(self.privada_a)
        self._vincular_principal(self.privada_a)
        service.validar_base_eleitoral(self.session, self.privada_a.id, self.gerente_a)
        self.privada_a.status = "SUBSTITUIDA"
        self.session.add(self.privada_a)
        self.session.commit()
        with self.assertRaises(service.BaseEleitoralNaoValidadaError):
            service.obter_base_principal_projeto_para_calculo(self.session, 100, self.gerente_a)

    def test_leitura_historica_de_base_substituida_continua_possivel(self):
        # Leitura historica e outra operacao: nao passa pela porta de calculo.
        self.privada_a.status = "SUBSTITUIDA"
        self.session.add(self.privada_a)
        self.session.commit()
        base = service.obter_base_eleitoral_visivel(self.session, self.privada_a.id, self.gerente_a)
        self.assertEqual(base.status, "SUBSTITUIDA")

    def test_validar_rejeita_base_com_territorio_em_conferencia(self):
        self._importar(self.privada_a, detalhes=(400, 350, 200))
        with self.assertRaises(HTTPException) as contexto:
            service.validar_base_eleitoral(self.session, self.privada_a.id, self.gerente_a)
        self.assertEqual(contexto.exception.status_code, 422)
        self.session.refresh(self.privada_a)
        self.assertEqual(self.privada_a.status, "EM_CONFERENCIA")

    def test_validar_funciona_apos_resolver_todas_as_divergencias(self):
        self._importar(self.privada_a, detalhes=(400, 350, 200))
        municipio = self._municipio(self.privada_a)
        service.resolver_divergencia_territorio(
            self.session, municipio.id, 980, "Conferencia manual da pagina 47", self.gerente_a
        )
        base = service.validar_base_eleitoral(self.session, self.privada_a.id, self.gerente_a)
        self.assertEqual(base.status, "VALIDADA")

    def test_validar_base_substituida_e_rejeitado(self):
        self.privada_a.status = "SUBSTITUIDA"
        self.session.add(self.privada_a)
        self.session.commit()
        with self.assertRaises(HTTPException) as contexto:
            service.validar_base_eleitoral(self.session, self.privada_a.id, self.gerente_a)
        self.assertEqual(contexto.exception.status_code, 422)

    def test_validar_base_de_outro_tenant_retorna_404(self):
        with self.assertRaises(HTTPException) as contexto:
            service.validar_base_eleitoral(self.session, self.privada_b.id, self.gerente_a)
        self.assertEqual(contexto.exception.status_code, 404)


class ResolucaoDivergenciaTests(_ServicoFixture):
    def setUp(self):
        super().setUp()
        self.importacao = self._importar(self.privada_a, detalhes=(400, 350, 200))
        self.municipio = self._municipio(self.privada_a)
        self.importacao_id = self.importacao.id
        self.municipio_id = self.municipio.id

    def test_resolucao_atualiza_valor_e_preserva_origem(self):
        territorio = service.resolver_divergencia_territorio(
            self.session, self.municipio.id, 980, "Conferencia manual da pagina 47", self.gerente_a
        )
        self.assertEqual(territorio.eleitorado_apto, 980)
        self.assertEqual(territorio.eleitorado_apto_origem, 1000)
        self.assertFalse(territorio.eleitorado_apto_divergente)
        self.assertEqual(territorio.status_validacao, "IMPORTADA")

    def test_resolucao_registra_auditoria_nos_metadados(self):
        service.resolver_divergencia_territorio(
            self.session, self.municipio.id, 980, "Conferencia manual", self.gerente_a
        )
        self.session.expire_all()
        territorio = self.session.get(models.TerritorioEleitoral, self.municipio.id)
        resolucao = territorio.metadados["resolucao_divergencia"]
        self.assertEqual(resolucao["valor_anterior"], 1000)
        self.assertEqual(resolucao["valor_final"], 980)
        self.assertEqual(resolucao["justificativa"], "Conferencia manual")
        self.assertEqual(resolucao["usuario_id"], self.gerente_a.id)
        self.assertIn("resolvido_em", resolucao)
        # Valor original da fonte continua rastreavel.
        self.assertEqual(territorio.metadados["valor_original"], 1000)

    def test_divergencia_historica_e_marcada_resolvida_sem_ser_apagada(self):
        service.resolver_divergencia_territorio(
            self.session, self.municipio.id, 980, "Conferencia manual", self.gerente_a
        )
        self.session.expire_all()
        importacao = self.session.get(models.ImportacaoBaseEleitoral, self.importacao.id)
        self.assertEqual(len(importacao.divergencias), 1)
        divergencia = importacao.divergencias[0]
        self.assertTrue(divergencia["resolvida"])
        self.assertEqual(divergencia["resolvido_por_id"], self.gerente_a.id)
        # O registro original permanece intacto ao lado da resolucao.
        self.assertEqual(divergencia["valor_resumo"], 1000)
        self.assertEqual(divergencia["valor_detalhe"], 950)
        self.assertEqual(divergencia["diferenca"], 50)

    def test_alteracao_de_json_e_realmente_persistida(self):
        # Mutacao in-place em JSON nao seria detectada pelo SQLAlchemy.
        service.resolver_divergencia_territorio(
            self.session, self.municipio.id, 980, "Primeira", self.gerente_a
        )
        municipio_id, importacao_id = self.municipio_id, self.importacao_id
        self.session.close()
        nova_sessao = self.Session()
        self.addCleanup(nova_sessao.close)
        territorio = nova_sessao.get(models.TerritorioEleitoral, municipio_id)
        self.assertEqual(territorio.metadados["resolucao_divergencia"]["valor_final"], 980)
        importacao = nova_sessao.get(models.ImportacaoBaseEleitoral, importacao_id)
        self.assertTrue(importacao.divergencias[0]["resolvida"])

    def test_resolucoes_sucessivas_acumulam_historico(self):
        service.resolver_divergencia_territorio(
            self.session, self.municipio.id, 980, "Primeira", self.gerente_a
        )
        service.resolver_divergencia_territorio(
            self.session, self.municipio.id, 990, "Segunda", self.gerente_a
        )
        self.session.expire_all()
        territorio = self.session.get(models.TerritorioEleitoral, self.municipio.id)
        historico = territorio.metadados["resolucao_divergencia_historico"]
        self.assertEqual(len(historico), 2)
        self.assertEqual(historico[0]["valor_final"], 980)
        self.assertEqual(historico[1]["valor_final"], 990)
        self.assertEqual(territorio.eleitorado_apto_origem, 1000)

    def test_justificativa_vazia_e_rejeitada(self):
        for justificativa in ("", "   "):
            with self.subTest(justificativa=repr(justificativa)):
                with self.assertRaises(HTTPException) as contexto:
                    service.resolver_divergencia_territorio(
                        self.session, self.municipio.id, 980, justificativa, self.gerente_a
                    )
                self.assertEqual(contexto.exception.status_code, 422)

    def test_valor_negativo_e_rejeitado(self):
        with self.assertRaises(HTTPException) as contexto:
            service.resolver_divergencia_territorio(
                self.session, self.municipio.id, -1, "Justificativa", self.gerente_a
            )
        self.assertEqual(contexto.exception.status_code, 422)

    def test_resolver_territorio_de_outro_tenant_retorna_404(self):
        with self.assertRaises(HTTPException) as contexto:
            service.resolver_divergencia_territorio(
                self.session, self.municipio.id, 980, "Justificativa", self.gerente_b
            )
        self.assertEqual(contexto.exception.status_code, 404)

    def test_resolver_nao_valida_a_base_automaticamente(self):
        service.resolver_divergencia_territorio(
            self.session, self.municipio.id, 980, "Justificativa", self.gerente_a
        )
        self.session.refresh(self.privada_a)
        self.assertNotEqual(self.privada_a.status, "VALIDADA")

    def test_listar_divergencias_respeita_tenant(self):
        divergencias = service.listar_divergencias(self.session, self.privada_a.id, self.gerente_a)
        self.assertEqual(len(divergencias), 1)
        with self.assertRaises(HTTPException) as contexto:
            service.listar_divergencias(self.session, self.privada_b.id, self.gerente_a)
        self.assertEqual(contexto.exception.status_code, 404)


class AbsorcaoLegadoTests(_ServicoFixture):
    def _semear_legado(self):
        self.session.execute(
            text(
                "INSERT INTO locais_votacao (id, nome, zona, secoes, municipio, bairro, endereco, company_id)"
                " VALUES"
                " (1, 'Escola Central', 5, '[{\"num\": 12, \"votos\": 300}]', 'Macapá', 'Buritizal', 'Rua A', 10),"
                " (2, 'Escola Norte', 6, '[{\"secao\": 13, \"votos\": 250}]', 'Macapá', 'Trem', 'Rua B', 10),"
                " (3, 'Escola Sul', 7, '[{\"votos\": 100}]', 'Santana', 'Provedor', 'Rua C', 10)"
            )
        )
        self.session.execute(
            text(
                # bairros.geometria e NOT NULL no schema legado.
                "INSERT INTO bairros (id, nome, eleitores, geometria, company_id) VALUES"
                " (1, 'Buritizal', 30507, X'00', 10),"
                " (2, 'Bairro Fantasma', 100, X'00', 10),"
                " (3, 'Provedor', 500, X'00', 10)"
            )
        )
        # 'Provedor' aparece tambem em outro municipio: torna-se ambiguo.
        self.session.execute(
            text(
                "INSERT INTO locais_votacao (id, nome, zona, secoes, municipio, bairro, endereco, company_id)"
                " VALUES (4, 'Escola Dupla', 8, NULL, 'Macapá', 'Provedor', 'Rua D', 10)"
            )
        )
        self.session.commit()

    def test_preview_nao_persiste_nada(self):
        self._semear_legado()
        antes = self.session.query(models.TerritorioEleitoral).count()
        preview = legado.preview_absorcao_legado(self.session, company_id=10, uf="AP")
        self.assertGreater(preview.total_registros, 0)
        self.assertEqual(self.session.query(models.TerritorioEleitoral).count(), antes)
        self.assertEqual(self.session.query(models.BaseEleitoral).count(), 3)

    def test_preview_conta_por_tipo(self):
        self._semear_legado()
        preview = legado.preview_absorcao_legado(self.session, company_id=10, uf="AP")
        self.assertEqual(preview.contagem["ESTADO"], 1)
        self.assertEqual(preview.contagem["MUNICIPIO"], 2)  # Macapá e Santana
        self.assertEqual(preview.contagem["BAIRRO"], 1)  # somente Buritizal resolve
        self.assertEqual(preview.contagem["LOCAL_VOTACAO"], 4)
        self.assertEqual(preview.contagem["SECAO"], 2)  # 12 e 13; a terceira falha

    def test_bairro_com_municipio_unico_e_mapeado(self):
        self._semear_legado()
        preview = legado.preview_absorcao_legado(self.session, company_id=10, uf="AP")
        bairros = [r for r in preview.registros if r.tipo == "BAIRRO"]
        self.assertEqual(len(bairros), 1)
        self.assertEqual(bairros[0].nome, "Buritizal")
        self.assertEqual(bairros[0].eleitorado_apto, 30507)
        self.assertIsNotNone(bairros[0].parent_chave)

    def test_bairro_sem_correspondencia_vira_divergencia(self):
        self._semear_legado()
        preview = legado.preview_absorcao_legado(self.session, company_id=10, uf="AP")
        tipos = {
            d["tipo_divergencia"]: d
            for d in preview.divergencias
            if d.get("territorio") == "Bairro Fantasma"
        }
        self.assertIn(legado.DIVERGENCIA_MUNICIPIO_NAO_IDENTIFICADO, tipos)

    def test_bairro_ambiguo_nao_escolhe_municipio(self):
        self._semear_legado()
        preview = legado.preview_absorcao_legado(self.session, company_id=10, uf="AP")
        ambiguos = [
            d for d in preview.divergencias
            if d["tipo_divergencia"] == legado.DIVERGENCIA_MUNICIPIO_AMBIGUO
        ]
        self.assertEqual(len(ambiguos), 1)
        self.assertEqual(ambiguos[0]["territorio"], "Provedor")
        self.assertEqual(len(ambiguos[0]["municipios_observados"]), 2)
        # Nenhum BAIRRO 'Provedor' foi criado arbitrariamente.
        nomes = {r.nome for r in preview.registros if r.tipo == "BAIRRO"}
        self.assertNotIn("Provedor", nomes)

    def test_local_com_bairro_conhecido_fica_sob_o_bairro(self):
        self._semear_legado()
        preview = legado.preview_absorcao_legado(self.session, company_id=10, uf="AP")
        por_chave = {r.chave: r for r in preview.registros}
        local = next(r for r in preview.registros if r.nome == "Escola Central")
        self.assertEqual(por_chave[local.parent_chave].tipo, "BAIRRO")

    def test_local_sem_bairro_resolvido_fica_sob_o_municipio(self):
        self._semear_legado()
        preview = legado.preview_absorcao_legado(self.session, company_id=10, uf="AP")
        por_chave = {r.chave: r for r in preview.registros}
        local = next(r for r in preview.registros if r.nome == "Escola Norte")
        self.assertEqual(por_chave[local.parent_chave].tipo, "MUNICIPIO")

    def test_reconhece_secao_pela_chave_num(self):
        self._semear_legado()
        preview = legado.preview_absorcao_legado(self.session, company_id=10, uf="AP")
        numeros = {r.numero_secao for r in preview.registros if r.tipo == "SECAO"}
        self.assertIn(12, numeros)

    def test_reconhece_secao_pela_chave_secao(self):
        self._semear_legado()
        preview = legado.preview_absorcao_legado(self.session, company_id=10, uf="AP")
        numeros = {r.numero_secao for r in preview.registros if r.tipo == "SECAO"}
        self.assertIn(13, numeros)

    def test_secao_sem_numero_vira_divergencia(self):
        self._semear_legado()
        preview = legado.preview_absorcao_legado(self.session, company_id=10, uf="AP")
        tipos = {d["tipo_divergencia"] for d in preview.divergencias}
        self.assertIn(legado.DIVERGENCIA_SECAO_SEM_NUMERO, tipos)

    def test_votos_legado_nunca_vira_eleitorado_apto(self):
        self._semear_legado()
        preview = legado.preview_absorcao_legado(self.session, company_id=10, uf="AP")
        for registro in preview.registros:
            if registro.tipo != "SECAO":
                continue
            with self.subTest(secao=registro.numero_secao):
                self.assertIsNone(registro.eleitorado_apto)
                self.assertIn("votos_legado", registro.metadados)
        votos = {
            r.numero_secao: r.metadados["votos_legado"]
            for r in preview.registros
            if r.tipo == "SECAO"
        }
        self.assertEqual(votos, {12: 300, 13: 250})

    def test_absorcao_cria_base_privada_em_conferencia(self):
        self._semear_legado()
        importacao = legado.absorver_legado(
            self.session,
            company_id=10,
            uf="AP",
            nome="Legado Empresa A",
            versao="legado-1",
            data_referencia=date(2026, 1, 1),
            criado_por_id=self.gerente_a.id,
        )
        base = self.session.get(models.BaseEleitoral, importacao.base_eleitoral_id)
        self.assertEqual(base.company_id, 10)
        self.assertIsNotNone(base.company_id)  # nunca oficial
        self.assertEqual(base.fonte, legado.FONTE_MIGRACAO_LEGADO)
        self.assertEqual(base.status, "EM_CONFERENCIA")
        self.assertNotEqual(base.status, "VALIDADA")

    def test_absorcao_nao_altera_o_legado(self):
        self._semear_legado()
        bairros_antes = self.session.query(models.Bairro).count()
        locais_antes = self.session.query(models.LocalVotacao).count()
        eleitores_antes = [b.eleitores for b in self.session.query(models.Bairro).all()]
        legado.absorver_legado(
            self.session,
            company_id=10,
            uf="AP",
            nome="Legado",
            versao="legado-1",
            data_referencia=date(2026, 1, 1),
            criado_por_id=self.gerente_a.id,
        )
        self.assertEqual(self.session.query(models.Bairro).count(), bairros_antes)
        self.assertEqual(self.session.query(models.LocalVotacao).count(), locais_antes)
        self.assertEqual(
            [b.eleitores for b in self.session.query(models.Bairro).all()], eleitores_antes
        )

    def test_absorcao_registra_divergencias_do_legado(self):
        self._semear_legado()
        importacao = legado.absorver_legado(
            self.session,
            company_id=10,
            uf="AP",
            nome="Legado",
            versao="legado-1",
            data_referencia=date(2026, 1, 1),
            criado_por_id=self.gerente_a.id,
        )
        tipos = {d["tipo_divergencia"] for d in importacao.divergencias}
        self.assertIn(legado.DIVERGENCIA_MUNICIPIO_AMBIGUO, tipos)
        self.assertIn(legado.DIVERGENCIA_MUNICIPIO_NAO_IDENTIFICADO, tipos)
        self.assertIn(legado.DIVERGENCIA_SECAO_SEM_NUMERO, tipos)
        self.assertEqual(importacao.total_divergencias, len(importacao.divergencias))

    def test_absorcao_exige_tenant(self):
        with self.assertRaises(ValueError):
            legado.absorver_legado(
                self.session,
                company_id=None,
                uf="AP",
                nome="Legado",
                versao="legado-1",
                data_referencia=date(2026, 1, 1),
                criado_por_id=self.gerente_a.id,
            )

    def test_preview_de_outro_tenant_nao_vaza(self):
        self._semear_legado()
        preview = legado.preview_absorcao_legado(self.session, company_id=20, uf="AP")
        # Somente a raiz ESTADO: a empresa B nao tem legado.
        self.assertEqual(preview.contagem["MUNICIPIO"], 0)
        self.assertEqual(preview.contagem["BAIRRO"], 0)
        self.assertEqual(preview.contagem["LOCAL_VOTACAO"], 0)


if __name__ == "__main__":
    unittest.main()
