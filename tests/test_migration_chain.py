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
HEAD_REVISION = "78d895f396e9"
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
                pergunta_columns = {
                    row[1]: row for row in connection.execute("PRAGMA table_info(perguntas)")
                }
                pergunta_indexes = connection.execute(
                    "PRAGMA index_list(perguntas)"
                ).fetchall()
                setor_columns = {
                    row[1]: row for row in connection.execute("PRAGMA table_info(setores)")
                }
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
                    "categorias_resposta_espontanea", "mapeamentos_resposta_espontanea",
                } <= tables
            )
            self.assertTrue(any(index[2] for index in company_indexes))
            self.assertTrue(
                any(fk[2] == "companies" and fk[3] == "company_id" for fk in coleta_fks)
            )
            self.assertEqual(coleta_columns["company_id"][3], 1)
            self.assertEqual(coleta_columns["client_uuid"][3], 1)
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
