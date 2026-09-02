import gc
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from time import sleep
from uuid import UUID

from alembic.config import Config
from alembic.script import ScriptDirectory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
HEAD_REVISION = "c6d7e8f9a0b1"
EXPECTED_LINEAGE = [
    "91fbe6db1f17",
    "3e4de16d893c",
    "a22720ccccb4",
    "28f012bafc15",
    "6de806306450",
    "b6f126cd7905",
    "c8e4b1a2d9f0",
    "d4e8a1b2c3f4",
    "e7f9a2b3c4d5",
    "b1c2d3e4f5a6",
    "f2a3b4c5d6e7",
    "a3b4c5d6e7f8",
    "c4d5e6f7a8b9",
    "8c2097e0a3de",
    "78d895f396e9",
    "a1b2c3d4e5f6",
    "b2c3d4e5f6a7",
    "c3d4e5f6a7b8",
    "d5e6f7a8b9c0",
    "e6f7a8b9c0d1",
    "f7a8b9c0d1e2",
    "a8b9c0d1e2f3",
    "b9c0d1e2f3a4",
    "c0d1e2f3a4b5",
    "d1e2f3a4b5c6",
    "e2f3a4b5c6d7",
    "f3a4b5c6d7e8",
    "a4b5c6d7e8f9",
    "b5c6d7e8f9a0",
]


class MigrationChainTests(unittest.TestCase):
    def alembic_url(self, db_path):
        return f"sqlite:///{db_path.as_posix()}"

    def run_alembic(self, *args, database_url):
        env = os.environ.copy()
        env["DATABASE_URL"] = database_url
        completed = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise AssertionError(
                "Alembic command failed: "
                + " ".join(args)
                + f"\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )
        return completed

    def combined_output(self, completed):
        return "\n".join(
            part for part in (completed.stdout, completed.stderr) if part
        ).strip()

    def current_revision(self, db_path):
        connection = sqlite3.connect(db_path)
        try:
            row = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
            return row[0] if row else None
        finally:
            connection.close()

    def remove_tree_with_retry(self, path):
        gc.collect()
        for attempt in range(5):
            try:
                shutil.rmtree(path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                sleep(0.2)

    @contextmanager
    def temporary_database(self, revision=None):
        temp_dir = Path(tempfile.mkdtemp(prefix="pesquisa360-migrations-"))
        try:
            db_path = temp_dir / "migration.db"
            database_url = self.alembic_url(db_path)
            if revision:
                self.run_alembic("upgrade", revision, database_url=database_url)
            yield db_path, database_url
        finally:
            self.remove_tree_with_retry(temp_dir)

    def seed_valid_legacy_data(self, db_path, collection_count=1):
        connection = sqlite3.connect(db_path)
        try:
            company_id = connection.execute(
                "SELECT id FROM companies ORDER BY id LIMIT 1"
            ).fetchone()[0]
            profile_id = connection.execute(
                "SELECT id FROM perfis WHERE lower(nome) = 'superadmin'"
            ).fetchone()[0]
            connection.execute(
                "UPDATE companies SET cnpj = '04.252.011/0001-10' WHERE id = ?",
                (company_id,),
            )
            connection.execute(
                """INSERT INTO usuarios
                   (id, email, nome, senha_hash, ativo, perfil_id, company_id)
                   VALUES (900, 'legacy@example.com', 'Legacy', 'hash', 1, ?, ?)""",
                (profile_id, company_id),
            )
            connection.execute(
                """INSERT INTO projetos
                   (id, nome, status, data_inicio, coordenador_id, company_id)
                   VALUES (901, 'Projeto legado', 'Ativo', '2026-01-01', 900, ?)""",
                (company_id,),
            )
            connection.execute(
                """INSERT INTO pesquisas (id, titulo, ativo, projeto_id)
                   VALUES (902, 'Pesquisa legada', 1, 901)"""
            )
            for index in range(collection_count):
                connection.execute(
                    """INSERT INTO coletas
                       (id, pesquisa_id, agente_id, data_inicio_coleta, foi_offline,
                        status_sincronizacao, inconformidade_localizacao)
                       VALUES (?, 902, 900, '2026-01-01 10:00:00', 0, 'sincronizado', 0)""",
                    (910 + index,),
                )
            connection.commit()
            return company_id
        finally:
            connection.close()

    def assert_current_output_contains(self, db_path, expected_revision):
        output = self.combined_output(
            self.run_alembic("current", database_url=self.alembic_url(db_path))
        )
        self.assertIn(expected_revision, output)

    def test_chain_has_exactly_one_head(self):
        self.assertEqual(
            ScriptDirectory.from_config(Config("alembic.ini")).get_heads(),
            [HEAD_REVISION],
        )

    def test_expected_revisions_are_in_single_lineage_to_head(self):
        script = ScriptDirectory.from_config(Config("alembic.ini"))
        revisions = {revision.revision: revision for revision in script.walk_revisions()}
        self.assertEqual(script.get_heads(), [HEAD_REVISION])

        lineage = []
        current = HEAD_REVISION
        while current is not None:
            lineage.append(current)
            down_revision = revisions[current].down_revision
            self.assertFalse(
                isinstance(down_revision, tuple),
                f"Unexpected branch before head at {current}: {down_revision}",
            )
            current = down_revision

        lineage_to_head = list(reversed(lineage))
        for revision in EXPECTED_LINEAGE:
            self.assertIn(revision, lineage_to_head)
        positions = [lineage_to_head.index(revision) for revision in EXPECTED_LINEAGE]
        self.assertEqual(positions, sorted(positions))

    def test_empty_database_upgrades_to_head_with_expected_constraints(self):
        with self.temporary_database() as (db_path, database_url):
            self.run_alembic("upgrade", "head", database_url=database_url)
            self.assertEqual(self.current_revision(db_path), HEAD_REVISION)
            self.assert_current_output_contains(db_path, HEAD_REVISION)
            connection = sqlite3.connect(db_path)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                company_indexes = connection.execute(
                    "PRAGMA index_list(companies)"
                ).fetchall()
                coleta_fks = connection.execute(
                    "PRAGMA foreign_key_list(coletas)"
                ).fetchall()
                coleta_columns = {
                    row[1]: row for row in connection.execute("PRAGMA table_info(coletas)")
                }
                coleta_indexes = connection.execute(
                    "PRAGMA index_list(coletas)"
                ).fetchall()
                pergunta_columns = {
                    row[1]: row for row in connection.execute("PRAGMA table_info(perguntas)")
                }
                pergunta_indexes = connection.execute(
                    "PRAGMA index_list(perguntas)"
                ).fetchall()
                setor_columns = {
                    row[1]: row for row in connection.execute("PRAGMA table_info(setores)")
                }
                setor_agentes_indexes = connection.execute(
                    "PRAGMA index_list(setor_agentes)"
                ).fetchall()
                categoria_indexes = connection.execute(
                    "PRAGMA index_list(categorias_resposta_espontanea)"
                ).fetchall()
                mapeamento_indexes = connection.execute(
                    "PRAGMA index_list(mapeamentos_resposta_espontanea)"
                ).fetchall()
            finally:
                connection.close()
            self.assertTrue(
                {
                    "companies", "usuarios", "projetos", "pesquisas", "coletas",
                    "setor_agentes",
                    "categorias_resposta_espontanea", "mapeamentos_resposta_espontanea",
                } <= tables
            )
            self.assertTrue(any(index[2] for index in company_indexes))
            self.assertTrue(
                any(fk[2] == "companies" and fk[3] == "company_id" for fk in coleta_fks)
            )
            self.assertEqual(coleta_columns["company_id"][3], 1)
            self.assertEqual(coleta_columns["client_uuid"][3], 1)
            self.assertEqual(coleta_columns["setor_id"][3], 0)
            self.assertIn(
                "ix_coletas_setor_id", {index[1] for index in coleta_indexes}
            )
            self.assertTrue(
                any(row[2] == "setores" and row[3] == "setor_id" and row[4] == "id"
                    for row in coleta_fks)
            )
            self.assertEqual(pergunta_columns["eh_resposta_espontanea"][3], 1)
            self.assertEqual(pergunta_columns["papel_analitico"][3], 0)
            self.assertEqual(pergunta_columns["metadados_analiticos"][3], 1)
            self.assertEqual(pergunta_columns["metadados_analiticos"][4], "'{}'")
            self.assertIn(
                "ix_perguntas_papel_analitico",
                {index[1] for index in pergunta_indexes},
            )
            self.assertEqual(setor_columns["finalidade"][3], 1)
            self.assertEqual(setor_columns["finalidade"][4], "'OPERACAO'")
            self.assertTrue(
                {"ix_setor_agentes_setor_id", "ix_setor_agentes_agente_id"}
                <= {index[1] for index in setor_agentes_indexes}
            )
            self.assertIn(
                "uq_categoria_resposta_espontanea_pesquisa_nome_ativo",
                {index[1] for index in categoria_indexes if index[2]},
            )
            self.assertIn(
                "uq_mapeamento_resposta_espontanea_pesquisa_chave_ativo",
                {index[1] for index in mapeamento_indexes if index[2]},
            )

    def test_valid_legacy_data_is_normalized_and_preserved(self):
        with self.temporary_database("c8e4b1a2d9f0") as (db_path, database_url):
            company_id = self.seed_valid_legacy_data(db_path, collection_count=2)
            self.run_alembic("upgrade", "head", database_url=database_url)
            connection = sqlite3.connect(db_path)
            try:
                cnpj = connection.execute(
                    "SELECT cnpj FROM companies WHERE id = ?", (company_id,)
                ).fetchone()[0]
                collections = connection.execute(
                    "SELECT company_id, client_uuid FROM coletas ORDER BY id"
                ).fetchall()
            finally:
                connection.close()
            self.assertEqual(cnpj, "04252011000110")
            self.assertEqual({row[0] for row in collections}, {company_id})
            uuids = [UUID(row[1]) for row in collections]
            self.assertEqual(len(set(uuids)), 2)

    def test_legacy_setores_receive_operacao_finalidade(self):
        with self.temporary_database("b1c2d3e4f5a6") as (db_path, database_url):
            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    """INSERT INTO setores
                       (id, nome, meta, geometria, pesquisa_id, agente_id, tolerancia)
                       VALUES (990, 'Setor legado', 10, NULL, 1, NULL, 50)"""
                )
                connection.commit()
            finally:
                connection.close()

            self.run_alembic("upgrade", "head", database_url=database_url)

            connection = sqlite3.connect(db_path)
            try:
                finalidade = connection.execute(
                    "SELECT finalidade FROM setores WHERE id = 990"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(finalidade, "OPERACAO")

    def test_setor_agentes_backfill_and_reversible_upgrade(self):
        previous = "b2c3d4e5f6a7"
        with self.temporary_database(previous) as (db_path, database_url):
            connection = sqlite3.connect(db_path)
            try:
                company_id = connection.execute(
                    "SELECT id FROM companies ORDER BY id LIMIT 1"
                ).fetchone()[0]
                profile_id = connection.execute(
                    "SELECT id FROM perfis ORDER BY id LIMIT 1"
                ).fetchone()[0]
                connection.execute(
                    """INSERT INTO usuarios
                       (id, email, nome, senha_hash, ativo, perfil_id, company_id)
                       VALUES (900, 'setor-backfill@example.com', 'Agente legado',
                               'hash', 1, ?, ?)""",
                    (profile_id, company_id),
                )
                connection.execute(
                    """INSERT INTO projetos
                       (id, nome, status, data_inicio, coordenador_id, company_id)
                       VALUES (901, 'Projeto backfill', 'Ativo', '2026-01-01',
                               900, ?)""",
                    (company_id,),
                )
                connection.execute(
                    """INSERT INTO pesquisas (id, titulo, ativo, projeto_id)
                       VALUES (902, 'Pesquisa backfill', 1, 901)"""
                )
                connection.execute(
                    """INSERT INTO setores
                       (id, nome, meta, geometria, pesquisa_id, agente_id,
                        tolerancia, finalidade)
                       VALUES (990, 'Setor legado', 10, NULL, 902, 900, 50,
                               'OPERACAO')"""
                )
                connection.commit()
            finally:
                connection.close()

            self.run_alembic("upgrade", "head", database_url=database_url)
            connection = sqlite3.connect(db_path)
            try:
                vinculos = connection.execute(
                    "SELECT setor_id, agente_id, ativo FROM setor_agentes"
                ).fetchall()
                legado = connection.execute(
                    "SELECT agente_id FROM setores WHERE id = 990"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(vinculos, [(990, 900, 1)])
            self.assertEqual(legado, 900)

            self.run_alembic("downgrade", previous, database_url=database_url)
            connection = sqlite3.connect(db_path)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                legado = connection.execute(
                    "SELECT agente_id FROM setores WHERE id = 990"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertNotIn("setor_agentes", tables)
            self.assertEqual(legado, 900)

            self.run_alembic("upgrade", "head", database_url=database_url)
            self.assertEqual(self.current_revision(db_path), HEAD_REVISION)

    def test_coleta_setor_upgrade_keeps_history_null_and_is_reversible(self):
        previous = "c3d4e5f6a7b8"
        with self.temporary_database(previous) as (db_path, database_url):
            connection = sqlite3.connect(db_path)
            try:
                company_id = connection.execute(
                    "SELECT id FROM companies ORDER BY id LIMIT 1"
                ).fetchone()[0]
                profile_id = connection.execute(
                    "SELECT id FROM perfis ORDER BY id LIMIT 1"
                ).fetchone()[0]
                connection.execute(
                    """INSERT INTO usuarios
                       (id, email, nome, senha_hash, ativo, perfil_id, company_id)
                       VALUES (950, 'coleta-setor@example.com', 'Agente setor',
                               'hash', 1, ?, ?)""",
                    (profile_id, company_id),
                )
                connection.execute(
                    """INSERT INTO projetos
                       (id, nome, status, data_inicio, coordenador_id, company_id)
                       VALUES (951, 'Projeto setor', 'Ativo', '2026-01-01', 950, ?)""",
                    (company_id,),
                )
                connection.execute(
                    "INSERT INTO pesquisas (id, titulo, ativo, projeto_id) VALUES (952, 'Pesquisa setor', 1, 951)"
                )
                connection.execute(
                    """INSERT INTO coletas
                       (id, pesquisa_id, agente_id, company_id, client_uuid,
                        data_inicio_coleta, foi_offline, status_sincronizacao,
                        inconformidade_localizacao)
                       VALUES (953, 952, 950, ?, '00000000-0000-4000-8000-000000000953',
                               '2026-01-01 10:00:00', 0, 'sincronizado', 0)""",
                    (company_id,),
                )
                connection.commit()
            finally:
                connection.close()

            self.run_alembic("upgrade", "head", database_url=database_url)
            connection = sqlite3.connect(db_path)
            try:
                columns = {
                    row[1]: row for row in connection.execute("PRAGMA table_info(coletas)")
                }
                indexes = {
                    row[1] for row in connection.execute("PRAGMA index_list(coletas)")
                }
                fks = connection.execute("PRAGMA foreign_key_list(coletas)").fetchall()
                setor_id = connection.execute(
                    "SELECT setor_id FROM coletas WHERE id = 953"
                ).fetchone()[0]
            finally:
                connection.close()
            self.assertEqual(columns["setor_id"][3], 0)
            self.assertIn("ix_coletas_setor_id", indexes)
            self.assertTrue(any(row[2] == "setores" and row[3] == "setor_id" for row in fks))
            self.assertIsNone(setor_id)

            self.run_alembic("downgrade", previous, database_url=database_url)
            connection = sqlite3.connect(db_path)
            try:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(coletas)")
                }
            finally:
                connection.close()
            self.assertNotIn("setor_id", columns)

    def test_pergunta_aplicabilidade_backfill_global_and_reversible(self):
        """FASE F: perguntas existentes viram GLOBAL; downgrade nao toca o resto."""
        previous = "d5e6f7a8b9c0"
        with self.temporary_database(previous) as (db_path, database_url):
            connection = sqlite3.connect(db_path)
            try:
                # Semente propria: em d5e6f7a8b9c0 `coletas.company_id` ja e
                # NOT NULL, entao o helper legado (sem company_id) nao serve.
                company_id = connection.execute(
                    "SELECT id FROM companies ORDER BY id LIMIT 1"
                ).fetchone()[0]
                profile_id = connection.execute(
                    "SELECT id FROM perfis ORDER BY id LIMIT 1"
                ).fetchone()[0]
                connection.execute(
                    """INSERT INTO usuarios
                       (id, email, nome, senha_hash, ativo, perfil_id, company_id)
                       VALUES (900, 'f@example.com', 'F', 'hash', 1, ?, ?)""",
                    (profile_id, company_id),
                )
                connection.execute(
                    """INSERT INTO projetos
                       (id, nome, status, data_inicio, coordenador_id, company_id)
                       VALUES (901, 'Projeto F', 'Ativo', '2026-01-01', 900, ?)""",
                    (company_id,),
                )
                connection.execute(
                    """INSERT INTO pesquisas (id, titulo, ativo, projeto_id)
                       VALUES (902, 'Pesquisa F', 1, 901)"""
                )
                connection.execute(
                    """INSERT INTO coletas
                       (id, pesquisa_id, agente_id, company_id, client_uuid,
                        data_inicio_coleta, foi_offline, status_sincronizacao,
                        inconformidade_localizacao)
                       VALUES (910, 902, 900, ?, 'f-uuid-910', '2026-01-01 10:00:00',
                               0, 'sincronizado', 0)""",
                    (company_id,),
                )
                for pergunta_id in (950, 951, 952):
                    connection.execute(
                        """INSERT INTO perguntas
                           (id, texto_pergunta, tipo_pergunta, ordem, eh_obrigatoria,
                            eh_resposta_espontanea, metadados_analiticos, ativo, pesquisa_id)
                           VALUES (?, 'Pergunta legada', 'TEXTO', ?, 1, 0, '{}', 1, 902)""",
                        (pergunta_id, pergunta_id - 949),
                    )
                connection.commit()
            finally:
                connection.close()

            self.run_alembic("upgrade", "head", database_url=database_url)

            connection = sqlite3.connect(db_path)
            try:
                aplicabilidades = connection.execute(
                    "SELECT aplicabilidade FROM perguntas WHERE id IN (950, 951, 952)"
                ).fetchall()
                self.assertEqual(aplicabilidades, [("GLOBAL",)] * 3)
                # Tabela associativa criada e vazia: nenhuma pergunta antiga
                # virou territorial.
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM pergunta_territorio_eleitoral"
                    ).fetchone()[0],
                    0,
                )
                coletas_antes = connection.execute("SELECT COUNT(*) FROM coletas").fetchone()[0]
            finally:
                connection.close()

            self.run_alembic("downgrade", previous, database_url=database_url)

            connection = sqlite3.connect(db_path)
            try:
                colunas = {
                    linha[1] for linha in connection.execute("PRAGMA table_info(perguntas)")
                }
                self.assertNotIn("aplicabilidade", colunas)
                tabelas = {
                    linha[0]
                    for linha in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                self.assertNotIn("pergunta_territorio_eleitoral", tabelas)
                # Downgrade nao mexe em coletas nem nas perguntas em si.
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM coletas").fetchone()[0],
                    coletas_antes,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM perguntas WHERE id IN (950, 951, 952)"
                    ).fetchone()[0],
                    3,
                )
            finally:
                connection.close()

    def test_invalid_legacy_cnpj_aborts_without_advancing_revision(self):
        with self.temporary_database("d4e8a1b2c3f4") as (db_path, database_url):
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("UPDATE companies SET cnpj = '11.111.111/1111-11'")
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(AssertionError, "CNPJ existente invalido"):
                self.run_alembic("upgrade", "head", database_url=database_url)
            self.assertEqual(self.current_revision(db_path), "d4e8a1b2c3f4")

    def test_duplicate_legacy_cnpj_aborts_without_selecting_winner(self):
        with self.temporary_database("d4e8a1b2c3f4") as (db_path, database_url):
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("UPDATE companies SET cnpj = '04.252.011/0001-10'")
                connection.execute(
                    "INSERT INTO companies (name, cnpj, is_active) VALUES ('Duplicada', '04252011000110', 1)"
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(AssertionError, "CNPJs duplicados"):
                self.run_alembic("upgrade", "head", database_url=database_url)
            connection = sqlite3.connect(db_path)
            try:
                values = [
                    row[0] for row in connection.execute(
                        "SELECT cnpj FROM companies ORDER BY id"
                    )
                ]
            finally:
                connection.close()
            self.assertEqual(values, ["04.252.011/0001-10", "04252011000110"])
            self.assertEqual(self.current_revision(db_path), "d4e8a1b2c3f4")

    def test_collection_without_tenant_aborts_before_schema_change(self):
        with self.temporary_database("c8e4b1a2d9f0") as (db_path, database_url):
            self.seed_valid_legacy_data(db_path)
            connection = sqlite3.connect(db_path)
            try:
                connection.execute("UPDATE coletas SET agente_id = 999999")
                connection.commit()
            finally:
                connection.close()
            with self.assertRaisesRegex(AssertionError, "Coleta existente sem tenant"):
                self.run_alembic("upgrade", "d4e8a1b2c3f4", database_url=database_url)
            connection = sqlite3.connect(db_path)
            try:
                columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(coletas)")
                }
            finally:
                connection.close()
            self.assertNotIn("client_uuid", columns)
            self.assertNotIn("company_id", columns)
            self.assertEqual(self.current_revision(db_path), "c8e4b1a2d9f0")

    def test_controlled_downgrades_can_be_upgraded_again(self):
        with self.temporary_database() as (db_path, database_url):
            self.run_alembic("upgrade", "head", database_url=database_url)
            self.run_alembic("downgrade", "d4e8a1b2c3f4", database_url=database_url)
            self.assertEqual(self.current_revision(db_path), "d4e8a1b2c3f4")
            self.run_alembic("upgrade", "head", database_url=database_url)
            self.run_alembic("downgrade", "c8e4b1a2d9f0", database_url=database_url)
            self.assertEqual(self.current_revision(db_path), "c8e4b1a2d9f0")
            self.run_alembic("upgrade", "head", database_url=database_url)
            self.assertEqual(self.current_revision(db_path), HEAD_REVISION)

    def test_postgresql_offline_sql_is_generated_and_contains_expected_tokens(self):
        with tempfile.TemporaryDirectory(prefix="pesquisa360-migrations-sql-") as temp_dir:
            sql_path = Path(temp_dir) / "upgrade.sql"
            database_url = "postgresql://user:pass@localhost/pesquisa360"
            completed = self.run_alembic(
                "upgrade",
                "head",
                "--sql",
                database_url=database_url,
            )
            sql_path.write_text(completed.stdout, encoding="utf-8")
            sql_text = sql_path.read_text(encoding="utf-8")
            self.assertIn("CREATE EXTENSION IF NOT EXISTS postgis", sql_text)
            self.assertRegex(sql_text, re.compile(r"\bJSONB\b", re.IGNORECASE))
            self.assertRegex(sql_text, re.compile(r"\bGeometry\b", re.IGNORECASE))
            self.assertIn("FOREIGN KEY", sql_text.upper())
            self.assertIn("NOT NULL", sql_text.upper())
            self.assertIn(
                "CREATE UNIQUE INDEX uq_categoria_resposta_espontanea_pesquisa_nome_ativo",
                sql_text,
            )
            self.assertIn(
                "CREATE UNIQUE INDEX uq_mapeamento_resposta_espontanea_pesquisa_chave_ativo",
                sql_text,
            )
            self.assertGreaterEqual(sql_text.upper().count("WHERE ATIVO IS TRUE"), 2)
            self.assertNotIn("PRAGMA", sql_text.upper())


if __name__ == "__main__":
    unittest.main()
