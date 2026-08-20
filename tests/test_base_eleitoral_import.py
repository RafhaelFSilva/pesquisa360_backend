"""Testes do core de importacao da Base Eleitoral (Fase 3A).

Fixtures normalizadas em memoria: nao dependem de nenhum arquivo do TSE, que
ainda nao existe no repositorio.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-base-eleitoral-import-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360.db import models
from pesquisa360.services import base_eleitoral_import as core

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def run_alembic_upgrade(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "alembic upgrade head falhou:\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


class FuncoesPurasTests(unittest.TestCase):
    """Nada aqui toca banco: parsing, normalizacao, hash e conferencia."""

    def test_normalizacao_gera_chave_sem_acento_e_sem_ruido(self):
        self.assertEqual(core.normalizar_nome_territorio("  Perpétuo   Socorro "), "perpetuo socorro")
        self.assertEqual(core.normalizar_nome_territorio("SÃO JOSÉ"), "sao jose")
        self.assertEqual(core.normalizar_nome_territorio(None), "")

    def test_nome_original_nunca_e_substituido_pela_chave(self):
        registro = core.RegistroTerritorioImportacao(
            tipo="MUNICIPIO", nome="Macapá", chave="m1", parent_chave="e1"
        ).com_nome_normalizado()
        self.assertEqual(registro.nome, "Macapá")
        self.assertEqual(registro.nome_normalizado, "macapa")

    def test_parse_eleitorado_aceita_inteiro_e_separador_de_milhar(self):
        self.assertEqual(core.parse_eleitorado(30507), 30507)
        self.assertEqual(core.parse_eleitorado("30507"), 30507)
        self.assertEqual(core.parse_eleitorado("30.507"), 30507)
        self.assertEqual(core.parse_eleitorado("322.066"), 322066)
        self.assertIsNone(core.parse_eleitorado(None))
        self.assertIsNone(core.parse_eleitorado("   "))

    def test_parse_eleitorado_rejeita_valor_invalido_sem_virar_zero(self):
        for valor in ("30,507", "abc", "1.23", "-5", "12 345", True, -1, 3.5):
            with self.subTest(valor=valor):
                with self.assertRaises(core.RegistroInvalidoError):
                    core.parse_eleitorado(valor)

    def test_hash_sha256_estavel_e_sensivel_ao_conteudo(self):
        primeiro = core.calcular_hash_arquivo(b"conteudo")
        self.assertEqual(primeiro, core.calcular_hash_arquivo(b"conteudo"))
        self.assertNotEqual(primeiro, core.calcular_hash_arquivo(b"conteudo "))
        self.assertEqual(len(primeiro), 64)
        with self.assertRaises(core.RegistroInvalidoError):
            core.calcular_hash_arquivo("nao sao bytes")

    def _cenario(self, detalhes):
        registros = [
            core.RegistroTerritorioImportacao(tipo="ESTADO", nome="Amapa", chave="e1"),
            core.RegistroTerritorioImportacao(
                tipo="MUNICIPIO",
                nome="Macapa",
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

    def test_conferencia_sem_divergencia_quando_soma_bate(self):
        self.assertEqual(core.conferir_resumo_detalhe(self._cenario([400, 350, 250])), [])

    def test_conferencia_detecta_diferenca_sem_corrigir(self):
        divergencias = core.conferir_resumo_detalhe(self._cenario([400, 350, 200]))
        self.assertEqual(len(divergencias), 1)
        divergencia = divergencias[0]
        self.assertEqual(divergencia.tipo_divergencia, core.TIPO_DIVERGENCIA_RESUMO_DETALHE)
        self.assertEqual(divergencia.valor_resumo, 1000)
        self.assertEqual(divergencia.valor_detalhe, 950)
        self.assertEqual(divergencia.diferenca, 50)
        self.assertEqual(divergencia.territorio_codigo, "6050")

    def test_conferencia_ignora_detalhe_incompleto(self):
        # Um filho sem valor tornaria a soma parcial e criaria divergencia falsa.
        registros = self._cenario([400, 350])
        registros.append(
            core.RegistroTerritorioImportacao(
                tipo="BAIRRO", nome="Sem valor", chave="b9", parent_chave="m1"
            )
        )
        self.assertEqual(core.conferir_resumo_detalhe(registros), [])

    def test_conferencia_e_pura(self):
        registros = self._cenario([400, 350, 200])
        copia = list(registros)
        core.conferir_resumo_detalhe(registros)
        self.assertEqual(registros, copia)
        self.assertEqual(registros[1].eleitorado_apto, 1000)
        self.assertEqual([r.eleitorado_apto for r in registros[2:]], [400, 350, 200])

    def test_estrutura_invalida_e_rejeitada(self):
        casos = {
            "tipo": [core.RegistroTerritorioImportacao(tipo="REGIAO", nome="X", chave="a")],
            "raiz": [
                core.RegistroTerritorioImportacao(tipo="MUNICIPIO", nome="X", chave="a")
            ],
            "secao": [
                core.RegistroTerritorioImportacao(tipo="ESTADO", nome="E", chave="e"),
                core.RegistroTerritorioImportacao(
                    tipo="SECAO", nome="S", chave="s", parent_chave="e"
                ),
            ],
            "parent_inexistente": [
                core.RegistroTerritorioImportacao(tipo="ESTADO", nome="E", chave="e"),
                core.RegistroTerritorioImportacao(
                    tipo="MUNICIPIO", nome="M", chave="m", parent_chave="zzz"
                ),
            ],
            "chave_duplicada": [
                core.RegistroTerritorioImportacao(tipo="ESTADO", nome="E", chave="e"),
                core.RegistroTerritorioImportacao(tipo="ESTADO", nome="E2", chave="e"),
            ],
        }
        for nome, registros in casos.items():
            with self.subTest(caso=nome):
                with self.assertRaises(core.RegistroInvalidoError):
                    core.validar_estrutura(registros)

    def test_ciclo_na_arvore_e_rejeitado(self):
        registros = [
            core.RegistroTerritorioImportacao(
                tipo="MUNICIPIO", nome="A", chave="a", parent_chave="b"
            ),
            core.RegistroTerritorioImportacao(
                tipo="MUNICIPIO", nome="B", chave="b", parent_chave="a"
            ),
        ]
        with self.assertRaises(core.RegistroInvalidoError):
            core.validar_estrutura(registros)


class ImportacaoPersistenteTests(unittest.TestCase):
    """Persistencia real contra o schema produzido pela migration."""

    @classmethod
    def setUpClass(cls):
        cls.temp_dir = Path(tempfile.mkdtemp(prefix="pesquisa360-import-"))
        cls.db_path = cls.temp_dir / "import.db"
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
        for tabela in (
            "importacao_base_eleitoral",
            "projeto_base_eleitoral",
            "territorio_eleitoral",
            "base_eleitoral",
            "projetos",
            "usuarios",
            "companies",
            "perfis",
        ):
            self.session.execute(text(f"DELETE FROM {tabela}"))
        self.session.execute(
            text("INSERT INTO perfis (id, nome) VALUES (1, 'Superadmin'), (2, 'Gerente')")
        )
        self.session.execute(
            text("INSERT INTO companies (id, name, is_active) VALUES (10, 'A', 1)")
        )
        self.session.execute(
            text(
                "INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id)"
                " VALUES (1, 'a@a', 'A', 'x', 1, 2, 10)"
            )
        )
        self.session.commit()
        self.base = self._criar_base()

    def _criar_base(self, **overrides):
        dados = dict(
            nome="Base teste",
            ano=2026,
            uf="AP",
            fonte="FIXTURE",
            versao="2026.1",
            data_referencia=date(2026, 1, 1),
            company_id=10,
            criado_por_id=1,
        )
        dados.update(overrides)
        base = models.BaseEleitoral(**dados)
        self.session.add(base)
        self.session.commit()
        return base

    def _registros(self, detalhes=(400, 350, 250)):
        registros = [
            core.RegistroTerritorioImportacao(
                tipo="ESTADO", nome="Amapá", chave="e1", metadados={"linha_fonte": 1}
            ),
            core.RegistroTerritorioImportacao(
                tipo="MUNICIPIO",
                nome="Macapá",
                chave="m1",
                parent_chave="e1",
                codigo="6050",
                eleitorado_apto=1000,
                metadados={"linha_fonte": 2, "pagina_fonte": 47},
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
                    metadados={"linha_fonte": 2 + indice},
                )
            )
        return registros

    def _importar(self, registros=None, conteudo=b"lote-a", arquivo="fixture.csv", base=None):
        return core.importar_registros(
            self.session,
            base_eleitoral=base or self.base,
            registros=registros if registros is not None else self._registros(),
            arquivo_origem=arquivo,
            executado_por_id=1,
            conteudo_arquivo=conteudo,
        )

    def _territorio(self, chave):
        return (
            self.session.query(models.TerritorioEleitoral)
            .filter(models.TerritorioEleitoral.base_eleitoral_id == self.base.id)
            .all()
        ) and next(
            territorio
            for territorio in self.session.query(models.TerritorioEleitoral).filter(
                models.TerritorioEleitoral.base_eleitoral_id == self.base.id
            )
            if (territorio.metadados or {}).get("chave_importacao") == chave
        )

    # -- importacao feliz ------------------------------------------------------

    def test_importacao_sem_divergencia_mantem_status_importada(self):
        importacao = self._importar()
        self.session.refresh(self.base)
        self.assertEqual(importacao.total_linhas, 5)
        self.assertEqual(importacao.total_importadas, 5)
        self.assertEqual(importacao.total_divergencias, 0)
        self.assertEqual(importacao.divergencias, [])
        self.assertEqual(self.base.status, "IMPORTADA")
        # Zero divergencia NAO promove para VALIDADA.
        self.assertNotEqual(self.base.status, "VALIDADA")

    def test_arvore_e_persistida_com_hierarquia_correta(self):
        self._importar()
        municipio = self._territorio("m1")
        estado = self._territorio("e1")
        bairro = self._territorio("b1")
        self.assertEqual(municipio.parent_id, estado.id)
        self.assertEqual(bairro.parent_id, municipio.id)
        self.assertEqual(bairro.municipio_id, municipio.id)
        self.assertIsNone(estado.parent_id)

    def test_nome_original_preservado_e_normalizado_derivado(self):
        self._importar()
        municipio = self._territorio("m1")
        self.assertEqual(municipio.nome, "Macapá")
        self.assertEqual(municipio.nome_normalizado, "macapa")

    def test_metadados_preservam_rastreabilidade_por_registro(self):
        importacao = self._importar()
        municipio = self._territorio("m1")
        metadados = municipio.metadados
        self.assertEqual(metadados["arquivo"], "fixture.csv")
        self.assertEqual(metadados["hash_arquivo"], core.calcular_hash_arquivo(b"lote-a"))
        self.assertEqual(metadados["importacao_id"], importacao.id)
        self.assertEqual(metadados["linha_fonte"], 2)
        self.assertEqual(metadados["pagina_fonte"], 47)
        self.assertEqual(metadados["valor_original"], 1000)
        self.assertEqual(metadados["chave_importacao"], "m1")

    def test_pagina_fonte_nao_e_inventada_quando_ausente(self):
        self._importar()
        bairro = self._territorio("b1")
        self.assertNotIn("pagina_fonte", bairro.metadados)
        self.assertEqual(bairro.metadados["linha_fonte"], 3)

    def test_eleitorado_origem_preserva_valor_declarado(self):
        self._importar()
        municipio = self._territorio("m1")
        self.assertEqual(municipio.eleitorado_apto, 1000)
        self.assertEqual(municipio.eleitorado_apto_origem, 1000)
        self.assertFalse(municipio.eleitorado_apto_divergente)

    # -- divergencia -----------------------------------------------------------

    def test_importacao_com_divergencia_marca_em_conferencia(self):
        importacao = self._importar(self._registros((400, 350, 200)))
        self.session.refresh(self.base)
        self.assertEqual(self.base.status, "EM_CONFERENCIA")
        self.assertEqual(importacao.total_divergencias, 1)

        divergencia = importacao.divergencias[0]
        self.assertEqual(divergencia["valor_resumo"], 1000)
        self.assertEqual(divergencia["valor_detalhe"], 950)
        self.assertEqual(divergencia["diferenca"], 50)
        self.assertFalse(divergencia["resolvida"])

        municipio = self._territorio("m1")
        self.assertEqual(municipio.eleitorado_apto, 1000)
        self.assertEqual(municipio.eleitorado_apto_origem, 1000)
        self.assertTrue(municipio.eleitorado_apto_divergente)
        self.assertEqual(municipio.status_validacao, "EM_CONFERENCIA")

    def test_divergencia_nao_altera_nenhum_filho(self):
        self._importar(self._registros((400, 350, 200)))
        for chave, esperado in (("b1", 400), ("b2", 350), ("b3", 200)):
            with self.subTest(chave=chave):
                filho = self._territorio(chave)
                self.assertEqual(filho.eleitorado_apto, esperado)
                self.assertFalse(filho.eleitorado_apto_divergente)
                self.assertEqual(filho.status_validacao, "IMPORTADA")

    # -- hash e idempotencia ---------------------------------------------------

    def test_hash_do_arquivo_e_persistido(self):
        importacao = self._importar()
        self.assertEqual(importacao.hash_arquivo, core.calcular_hash_arquivo(b"lote-a"))

    def test_hash_duplicado_e_rejeitado_sem_sobrescrever(self):
        primeira = self._importar()
        territorios_antes = self.session.query(models.TerritorioEleitoral).count()
        with self.assertRaises(core.ImportacaoDuplicadaError) as contexto:
            self._importar(conteudo=b"lote-a", arquivo="outro-nome.csv")
        self.assertEqual(contexto.exception.importacao_anterior_id, primeira.id)
        self.assertEqual(
            self.session.query(models.TerritorioEleitoral).count(), territorios_antes
        )
        self.assertEqual(self.session.query(models.ImportacaoBaseEleitoral).count(), 1)

    def test_conteudo_novo_gera_nova_versao_sem_apagar_a_anterior(self):
        primeira = self._importar(conteudo=b"lote-a")
        outra_base = self._criar_base(versao="2026.2", nome="Base v2")
        segunda = core.importar_registros(
            self.session,
            base_eleitoral=outra_base,
            registros=self._registros(),
            arquivo_origem="fixture-v2.csv",
            executado_por_id=1,
            conteudo_arquivo=b"lote-b",
        )
        self.assertNotEqual(primeira.id, segunda.id)
        self.assertNotEqual(primeira.base_eleitoral_id, segunda.base_eleitoral_id)
        # Nada foi apagado: as duas versoes coexistem.
        self.assertEqual(self.session.query(models.BaseEleitoral).count(), 2)
        self.assertEqual(
            self.session.query(models.TerritorioEleitoral)
            .filter(models.TerritorioEleitoral.base_eleitoral_id == primeira.base_eleitoral_id)
            .count(),
            5,
        )

    # -- atomicidade -----------------------------------------------------------

    def test_erro_estrutural_faz_rollback_completo(self):
        registros = self._registros()
        registros.append(
            core.RegistroTerritorioImportacao(
                tipo="SECAO", nome="Secao sem numero", chave="s1", parent_chave="m1"
            )
        )
        with self.assertRaises(core.RegistroInvalidoError):
            self._importar(registros)
        self.assertEqual(self.session.query(models.TerritorioEleitoral).count(), 0)
        self.assertEqual(self.session.query(models.ImportacaoBaseEleitoral).count(), 0)
        self.session.refresh(self.base)
        self.assertEqual(self.base.status, "IMPORTADA")

    def test_lote_vazio_e_rejeitado(self):
        with self.assertRaises(core.RegistroInvalidoError):
            self._importar([])

    def test_base_substituida_nao_aceita_importacao(self):
        self.base.status = "SUBSTITUIDA"
        self.session.add(self.base)
        self.session.commit()
        with self.assertRaises(core.ImportacaoBaseEleitoralError):
            self._importar()

    def test_importacao_nunca_executa_delete_de_territorios(self):
        self._importar(conteudo=b"lote-a")
        ids_iniciais = {
            territorio.id
            for territorio in self.session.query(models.TerritorioEleitoral).all()
        }
        outra_base = self._criar_base(versao="2026.3", nome="Base v3")
        core.importar_registros(
            self.session,
            base_eleitoral=outra_base,
            registros=self._registros(),
            arquivo_origem="v3.csv",
            executado_por_id=1,
            conteudo_arquivo=b"lote-c",
        )
        ids_finais = {
            territorio.id
            for territorio in self.session.query(models.TerritorioEleitoral).all()
        }
        self.assertTrue(ids_iniciais.issubset(ids_finais))


if __name__ == "__main__":
    unittest.main()
