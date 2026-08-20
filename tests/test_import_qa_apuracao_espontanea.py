import csv
import hashlib
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "import_qa_apuracao_espontanea.py"
SPEC = importlib.util.spec_from_file_location("import_qa_apuracao_espontanea", SCRIPT_PATH)
importer = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = importer
SPEC.loader.exec_module(importer)


class ImportQaApuracaoEspontaneaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        @event.listens_for(cls.engine, "connect")
        def register_spatial_functions(connection, _):
            connection.create_function("AsEWKB", 1, lambda value: value)
            connection.create_function("ST_AsEWKB", 1, lambda value: value)
            connection.create_function("GeomFromEWKT", 1, lambda value: value)

        cls.Session = sessionmaker(bind=cls.engine)
        with cls.engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE companies (
                    id INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    cnpj TEXT,
                    logo_url TEXT,
                    is_active BOOLEAN,
                    created_at DATETIME
                )
            """))
            connection.execute(text("""
                CREATE TABLE perfis (
                    id INTEGER PRIMARY KEY,
                    nome TEXT NOT NULL,
                    descricao TEXT
                )
            """))
            connection.execute(text("""
                CREATE TABLE usuarios (
                    id INTEGER PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    nome TEXT,
                    senha_hash TEXT NOT NULL,
                    ativo BOOLEAN NOT NULL,
                    perfil_id INTEGER NOT NULL,
                    company_id INTEGER NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE projetos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nome TEXT NOT NULL,
                    descricao TEXT,
                    status TEXT NOT NULL,
                    data_inicio DATE,
                    data_fim DATE,
                    coordenador_id INTEGER NOT NULL,
                    company_id INTEGER NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE pesquisas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    titulo TEXT NOT NULL,
                    tipo_pesquisa TEXT,
                    ativo BOOLEAN NOT NULL,
                    projeto_id INTEGER NOT NULL,
                    cerca_eletronica BLOB,
                    tolerancia_metros INTEGER
                )
            """))
            connection.execute(text("""
                CREATE TABLE perguntas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    texto_pergunta TEXT NOT NULL,
                    tipo_pergunta TEXT NOT NULL,
                    ordem INTEGER NOT NULL,
                    eh_obrigatoria BOOLEAN NOT NULL,
                    eh_resposta_espontanea BOOLEAN NOT NULL DEFAULT 0,
                    papel_analitico VARCHAR(50),
                    metadados_analiticos JSON NOT NULL DEFAULT '{}',
                    ativo BOOLEAN NOT NULL,
                    pesquisa_id INTEGER NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE opcoes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    texto TEXT NOT NULL,
                    ordem INTEGER,
                    pergunta_id INTEGER,
                    proxima_pergunta_id INTEGER
                )
            """))
            connection.execute(text("""
                CREATE TABLE coletas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pesquisa_id INTEGER NOT NULL,
                    agente_id INTEGER NOT NULL,
                    company_id INTEGER NOT NULL,
                    client_uuid TEXT NOT NULL,
                    foi_offline BOOLEAN,
                    endereco_estimado TEXT,
                    status_sincronizacao TEXT,
                    data_inicio_coleta DATETIME NOT NULL,
                    data_fim_coleta DATETIME,
                    localizacao_inicio BLOB,
                    localizacao_fim BLOB,
                    inconformidade_localizacao BOOLEAN NOT NULL DEFAULT 0
                )
            """))
            connection.execute(text("""
                CREATE TABLE respostas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pergunta_id INTEGER NOT NULL,
                    coleta_id INTEGER NOT NULL,
                    valor_resposta TEXT NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE categorias_resposta_espontanea (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pesquisa_id INTEGER NOT NULL,
                    nome TEXT NOT NULL,
                    nome_normalizado TEXT NOT NULL,
                    ativo BOOLEAN NOT NULL DEFAULT 1,
                    criado_por_id INTEGER NOT NULL,
                    atualizado_por_id INTEGER NOT NULL,
                    criado_em DATETIME,
                    atualizado_em DATETIME
                )
            """))
            connection.execute(text("""
                CREATE TABLE mapeamentos_resposta_espontanea (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pesquisa_id INTEGER NOT NULL,
                    categoria_id INTEGER NOT NULL,
                    chave_normalizada TEXT NOT NULL,
                    texto_referencia TEXT NOT NULL,
                    ativo BOOLEAN NOT NULL DEFAULT 1,
                    criado_por_id INTEGER NOT NULL,
                    atualizado_por_id INTEGER NOT NULL,
                    criado_em DATETIME,
                    atualizado_em DATETIME
                )
            """))

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        with self.engine.begin() as connection:
            for table in (
                "mapeamentos_resposta_espontanea",
                "categorias_resposta_espontanea",
                "respostas",
                "coletas",
                "opcoes",
                "perguntas",
                "pesquisas",
                "projetos",
                "usuarios",
                "perfis",
                "companies",
            ):
                connection.execute(text(f"DELETE FROM {table}"))
            connection.execute(text("""
                INSERT INTO companies (id, name, is_active)
                VALUES (1, 'Empresa QA', 1),
                       (2, 'Outra Empresa', 1)
            """))
            connection.execute(text("""
                INSERT INTO perfis (id, nome)
                VALUES (1, 'Gerente'),
                       (2, 'Agente'),
                       (3, 'Supervisor')
            """))
            connection.execute(text("""
                INSERT INTO usuarios (id, email, nome, senha_hash, ativo, perfil_id, company_id)
                VALUES (10, 'gerente@qa.local', 'Gerente QA', 'hash', 1, 1, 1),
                       (11, 'agente@qa.local', 'Agente QA', 'hash', 1, 2, 1),
                       (12, 'agente2@qa.local', 'Agente 2', 'hash', 1, 2, 2),
                       (13, 'supervisor@qa.local', 'Supervisor QA', 'hash', 1, 3, 1)
            """))

        self.profile = importer.CsvProfile(
            expected_hash="",
            headers=importer.EXPECTED_HEADERS,
            row_count=3,
            collection_count=2,
            per_question_counts={33: 2, 41: 1},
            spontaneous_question_ids={41},
        )
        self.rows = [
            {
                "coleta_qa_id": "1",
                "pergunta_origem_id": "33",
                "ordem": "4",
                "tipo_pergunta": "ESCOLHA_SIMPLES",
                "texto_pergunta": "Sexo",
                "valor_resposta": "Feminino",
            },
            {
                "coleta_qa_id": "1",
                "pergunta_origem_id": "41",
                "ordem": "12",
                "tipo_pergunta": "TEXTO",
                "texto_pergunta": "Quem é o seu candidato?",
                "valor_resposta": "Clécio ",
            },
            {
                "coleta_qa_id": "2",
                "pergunta_origem_id": "33",
                "ordem": "4",
                "tipo_pergunta": "ESCOLHA_SIMPLES",
                "texto_pergunta": "Sexo",
                "valor_resposta": "Masculino",
            },
        ]

    def tearDown(self):
        self.temp_dir.cleanup()

    def write_csv(self, rows=None, headers=None):
        csv_path = Path(self.temp_dir.name) / "dataset.csv"
        selected_rows = rows if rows is not None else self.rows
        selected_headers = headers if headers is not None else list(importer.EXPECTED_HEADERS)
        with csv_path.open("w", encoding="utf-8", newline="") as file_handle:
            if selected_headers == list(importer.EXPECTED_HEADERS):
                writer = csv.DictWriter(file_handle, fieldnames=selected_headers)
                writer.writeheader()
                for row in selected_rows:
                    writer.writerow(row)
            else:
                writer = csv.writer(file_handle)
                writer.writerow(selected_headers)
                for row in selected_rows:
                    writer.writerow([row.get(column, "") for column in selected_headers])
        file_hash = hashlib.sha256(csv_path.read_bytes()).hexdigest().upper()
        profile = importer.CsvProfile(
            expected_hash=file_hash,
            headers=tuple(importer.EXPECTED_HEADERS),
            row_count=self.profile.row_count,
            collection_count=self.profile.collection_count,
            per_question_counts=self.profile.per_question_counts,
            spontaneous_question_ids=self.profile.spontaneous_question_ids,
        )
        return csv_path, profile

    def execute(self, csv_path, profile, dry_run=False, apply=False, agent_id=11, company_id=1, coordinator_id=10):
        return importer.execute_import(
            session_factory=self.Session,
            csv_path=csv_path,
            company_id=company_id,
            coordinator_id=coordinator_id,
            agent_id=agent_id,
            dry_run=dry_run,
            apply=apply,
            project_name="QA - Import Teste",
            survey_title="QA - Pesquisa Import Teste",
            profile=profile,
        )

    def test_rejects_incorrect_hash(self):
        csv_path, profile = self.write_csv()
        wrong_profile = importer.CsvProfile(
            expected_hash="ABC",
            headers=profile.headers,
            row_count=profile.row_count,
            collection_count=profile.collection_count,
            per_question_counts=profile.per_question_counts,
            spontaneous_question_ids=profile.spontaneous_question_ids,
        )
        with self.assertRaisesRegex(ValueError, "Hash SHA-256 divergente"):
            importer.validate_and_load_csv(csv_path, wrong_profile)

    def test_rejects_incorrect_header(self):
        csv_path, profile = self.write_csv(headers=["a", "b", "c", "d", "e", "f"])
        with self.assertRaisesRegex(ValueError, "Cabecalho incorreto"):
            importer.validate_and_load_csv(csv_path, profile)

    def test_rejects_not_allowed_question(self):
        bad_rows = list(self.rows)
        bad_rows[0] = {**bad_rows[0], "pergunta_origem_id": "99"}
        csv_path, profile = self.write_csv(rows=bad_rows)
        with self.assertRaisesRegex(ValueError, "Conjunto de pergunta_origem_id divergente"):
            importer.validate_and_load_csv(csv_path, profile)

    def test_rejects_duplicate_collection_question_pair(self):
        duplicate_rows = [
            self.rows[0],
            {**self.rows[0]},
            {**self.rows[1], "coleta_qa_id": "2"},
        ]
        csv_path, profile = self.write_csv(rows=duplicate_rows)
        with self.assertRaisesRegex(ValueError, "Duplicidade detectada"):
            importer.validate_and_load_csv(csv_path, profile)

    def test_rejects_divergent_counts(self):
        fewer_rows = self.rows[:2]
        csv_path, profile = self.write_csv(rows=fewer_rows)
        with self.assertRaisesRegex(ValueError, "Quantidade de linhas divergente"):
            importer.validate_and_load_csv(csv_path, profile)

    def test_rejects_user_from_other_tenant(self):
        csv_path, profile = self.write_csv()
        with self.assertRaisesRegex(ValueError, "Coordenador invalido"):
            self.execute(csv_path, profile, dry_run=True, coordinator_id=10, company_id=2)

    def test_rejects_agent_from_other_company(self):
        csv_path, profile = self.write_csv()
        with self.assertRaisesRegex(ValueError, "Agente invalido"):
            self.execute(csv_path, profile, dry_run=True, agent_id=12)

    def test_dry_run_does_not_change_database(self):
        csv_path, profile = self.write_csv()
        result = self.execute(csv_path, profile, dry_run=True)
        self.assertEqual(result["planned_inserts"]["responses"], 3)
        with self.engine.begin() as connection:
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM projetos")).scalar_one(), 0)
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM pesquisas")).scalar_one(), 0)
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM respostas")).scalar_one(), 0)

    def test_error_during_apply_rolls_back_integrally(self):
        csv_path, profile = self.write_csv()
        with patch.object(importer, "_insert_respostas", side_effect=RuntimeError("falha forçada")):
            with self.assertRaisesRegex(RuntimeError, "falha forçada"):
                self.execute(csv_path, profile, apply=True)
        with self.engine.begin() as connection:
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM projetos")).scalar_one(), 0)
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM pesquisas")).scalar_one(), 0)
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM perguntas")).scalar_one(), 0)
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM coletas")).scalar_one(), 0)

    def test_duplicate_import_is_blocked(self):
        csv_path, profile = self.write_csv()
        first = self.execute(csv_path, profile, apply=True)
        self.assertGreater(first["survey_id"], 0)
        with self.assertRaisesRegex(ValueError, "Projeto QA ja existente"):
            self.execute(csv_path, profile, apply=True)

    def test_spontaneous_questions_are_marked_correctly(self):
        csv_path, profile = self.write_csv()
        result = self.execute(csv_path, profile, apply=True)
        with self.engine.begin() as connection:
            rows = connection.execute(text(
                "SELECT texto_pergunta, eh_resposta_espontanea FROM perguntas WHERE pesquisa_id = :pesquisa_id ORDER BY ordem"
            ), {"pesquisa_id": result["survey_id"]}).all()
        self.assertEqual(rows, [("Sexo", 0), ("Quem é o seu candidato?", 1)])

    def test_original_answer_is_preserved(self):
        csv_path, profile = self.write_csv()
        result = self.execute(csv_path, profile, apply=True)
        with self.engine.begin() as connection:
            stored = connection.execute(text(
                "SELECT r.valor_resposta FROM respostas r "
                "JOIN perguntas p ON p.id = r.pergunta_id "
                "WHERE p.pesquisa_id = :pesquisa_id AND p.texto_pergunta = 'Quem é o seu candidato?'"
            ), {"pesquisa_id": result["survey_id"]}).scalar_one()
        self.assertEqual(stored, "Clécio ")

    def test_categories_and_mappings_are_not_created(self):
        csv_path, profile = self.write_csv()
        self.execute(csv_path, profile, apply=True)
        with self.engine.begin() as connection:
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM categorias_resposta_espontanea")).scalar_one(), 0)
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM mapeamentos_resposta_espontanea")).scalar_one(), 0)


if __name__ == "__main__":
    unittest.main()
