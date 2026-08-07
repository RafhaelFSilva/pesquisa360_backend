import os
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("SECRET_KEY", "test-only-question-isolation-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import schemas
from pesquisa360.api.endpoints import projetos


class QuestionTenantIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:")

        @event.listens_for(cls.engine, "connect")
        def register_spatial_functions(connection, _):
            connection.create_function("AsEWKB", 1, lambda value: value)
            connection.create_function("ST_AsEWKB", 1, lambda value: value)

        cls.Session = sessionmaker(bind=cls.engine)
        with cls.engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE projetos (
                    id INTEGER PRIMARY KEY, nome TEXT NOT NULL, descricao TEXT,
                    status TEXT NOT NULL, data_inicio DATE, data_fim DATE,
                    coordenador_id INTEGER NOT NULL, company_id INTEGER NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE pesquisas (
                    id INTEGER PRIMARY KEY, titulo TEXT NOT NULL, tipo_pesquisa TEXT,
                    ativo BOOLEAN NOT NULL, projeto_id INTEGER NOT NULL,
                    cerca_eletronica BLOB, tolerancia_metros INTEGER
                )
            """))
            connection.execute(text("""
                CREATE TABLE perguntas (
                    id INTEGER PRIMARY KEY, texto_pergunta TEXT NOT NULL,
                    tipo_pergunta TEXT NOT NULL, ordem INTEGER NOT NULL,
                    eh_obrigatoria BOOLEAN NOT NULL, eh_resposta_espontanea BOOLEAN NOT NULL DEFAULT 0,
                    ativo BOOLEAN NOT NULL,
                    pesquisa_id INTEGER NOT NULL
                )
            """))
            connection.execute(text("""
                CREATE TABLE opcoes (
                    id INTEGER PRIMARY KEY, texto TEXT NOT NULL CHECK (texto <> '__FAIL__'), ordem INTEGER,
                    pergunta_id INTEGER, proxima_pergunta_id INTEGER
                )
            """))
            connection.execute(text("""
                CREATE TABLE respostas (
                    id INTEGER PRIMARY KEY, pergunta_id INTEGER NOT NULL,
                    coleta_id INTEGER NOT NULL, valor_resposta TEXT NOT NULL
                )
            """))

    def setUp(self):
        self.db = self.Session()
        for table in ("respostas", "opcoes", "perguntas", "pesquisas", "projetos"):
            self.db.execute(text(f"DELETE FROM {table}"))

        self.db.execute(text("""
            INSERT INTO projetos
                (id, nome, status, coordenador_id, company_id)
            VALUES (10, 'Projeto A', 'Ativo', 1, 1),
                   (20, 'Projeto B', 'Ativo', 2, 2)
        """))
        self.db.execute(text("""
            INSERT INTO pesquisas
                (id, titulo, ativo, projeto_id)
            VALUES (100, 'Pesquisa A1', 1, 10),
                   (101, 'Pesquisa A2', 1, 10),
                   (200, 'Pesquisa B1', 1, 20)
        """))
        self.db.execute(text("""
            INSERT INTO perguntas
                (id, texto_pergunta, tipo_pergunta, ordem, eh_obrigatoria, ativo, pesquisa_id)
            VALUES (1000, 'Pergunta A1', 'TEXTO', 1, 1, 1, 100),
                   (1002, 'Pergunta A1 destino', 'TEXTO', 2, 1, 1, 100),
                   (1010, 'Pergunta A2', 'TEXTO', 1, 1, 1, 101),
                   (2000, 'Pergunta B1', 'TEXTO', 1, 1, 1, 200)
        """))
        self.db.commit()
        self.user_a = SimpleNamespace(company_id=1)

    def tearDown(self):
        self.db.close()

    def assert_not_found(self, operation):
        with self.assertRaises(HTTPException) as context:
            operation()
        self.assertEqual(context.exception.status_code, 404)

    def scalar(self, statement):
        return self.db.execute(text(statement)).scalar_one()

    def create_question(self, options=None):
        return projetos.create_pergunta(
            pesquisa_id=100,
            pergunta=schemas.PerguntaCreate(
                texto_pergunta="Nova pergunta",
                tipo_pergunta="ESCOLHA_SIMPLES",
                opcoes=options,
            ),
            db=self.db,
            current_user=self.user_a,
        )

    def test_create_question_without_options_is_atomic(self):
        created = self.create_question()
        self.assertIsNotNone(created.id)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM perguntas"), 5)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM opcoes"), 0)

    def test_create_question_with_multiple_valid_options(self):
        created = self.create_question([
            {"texto": "Primeira", "ordem": 1},
            {"texto": "Segunda", "ordem": 2},
            {"texto": "Terceira", "ordem": 3},
        ])
        self.assertEqual(len(created.opcoes), 3)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM opcoes"), 3)

    def test_option_failure_at_any_position_rolls_back_question_and_all_options(self):
        for failure_position in (0, 1, 2):
            with self.subTest(failure_position=failure_position):
                options = [
                    {"texto": "Primeira", "ordem": 1},
                    {"texto": "Segunda", "ordem": 2},
                    {"texto": "Terceira", "ordem": 3},
                ]
                options[failure_position]["texto"] = "__FAIL__"
                with self.assertRaises(IntegrityError):
                    self.create_question(options)
                self.assertEqual(self.scalar("SELECT COUNT(*) FROM perguntas"), 4)
                self.assertEqual(self.scalar("SELECT COUNT(*) FROM opcoes"), 0)

    def test_valid_update_replaces_options(self):
        self.db.execute(text("""
            INSERT INTO opcoes (id, texto, ordem, pergunta_id)
            VALUES (1, 'Antiga A', 1, 1000), (2, 'Antiga B', 2, 1000)
        """))
        self.db.commit()

        updated = projetos.update_pergunta(
            pesquisa_id=100,
            pergunta_id=1000,
            pergunta_in=schemas.PerguntaUpdate(
                texto_pergunta="Pergunta nova",
                opcoes=[{"texto": "Nova", "ordem": 1}],
            ),
            db=self.db,
            current_user=self.user_a,
        )
        self.assertEqual(updated.texto_pergunta, "Pergunta nova")
        self.assertEqual([option.texto for option in updated.opcoes], ["Nova"])

    def test_failed_update_preserves_question_and_previous_options(self):
        self.db.execute(text("""
            INSERT INTO opcoes (id, texto, ordem, pergunta_id)
            VALUES (1, 'Original A', 1, 1000), (2, 'Original B', 2, 1000)
        """))
        self.db.commit()

        with self.assertRaises(IntegrityError):
            projetos.update_pergunta(
                pesquisa_id=100,
                pergunta_id=1000,
                pergunta_in=schemas.PerguntaUpdate(
                    texto_pergunta="Nao deve persistir",
                    opcoes=[
                        {"texto": "Nova valida", "ordem": 1},
                        {"texto": "__FAIL__", "ordem": 2},
                    ],
                ),
                db=self.db,
                current_user=self.user_a,
            )

        pergunta = self.db.execute(
            text("SELECT texto_pergunta FROM perguntas WHERE id = 1000")
        ).scalar_one()
        options = self.db.execute(
            text("SELECT texto FROM opcoes WHERE pergunta_id = 1000 ORDER BY id")
        ).scalars().all()
        self.assertEqual(pergunta, "Pergunta A1")
        self.assertEqual(options, ["Original A", "Original B"])

    def test_option_id_from_another_question_cannot_be_reused(self):
        self.db.execute(text("""
            INSERT INTO opcoes (id, texto, ordem, pergunta_id)
            VALUES (50, 'Opcao externa', 1, 1002)
        """))
        self.db.commit()

        self.assert_not_found(lambda: projetos.update_pergunta(
            pesquisa_id=100,
            pergunta_id=1000,
            pergunta_in=schemas.PerguntaUpdate(opcoes=[{
                "id": 50,
                "texto": "Reutilizada",
                "ordem": 1,
            }]),
            db=self.db,
            current_user=self.user_a,
        ))

    def test_semantically_duplicate_options_are_rejected(self):
        with self.assertRaises(HTTPException) as context:
            self.create_question([
                {"texto": "  Opcao   Um ", "ordem": 1},
                {"texto": "opcao um", "ordem": 2},
            ])
        self.assertEqual(context.exception.status_code, 422)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM perguntas"), 4)

    def test_empty_option_text_is_rejected_without_persistence(self):
        with self.assertRaises(HTTPException) as context:
            self.create_question([{"texto": "   ", "ordem": 1}])
        self.assertEqual(context.exception.status_code, 422)
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM perguntas"), 4)

    def test_same_tenant_same_survey_allows_question_and_options_update(self):
        updated = projetos.update_pergunta(
            pesquisa_id=100,
            pergunta_id=1000,
            pergunta_in=schemas.PerguntaUpdate(
                texto_pergunta="Pergunta atualizada",
                opcoes=[{
                    "texto": "Continuar",
                    "ordem": 1,
                    "proxima_pergunta_id": 1002,
                }],
            ),
            db=self.db,
            current_user=self.user_a,
        )

        self.assertEqual(updated.texto_pergunta, "Pergunta atualizada")
        self.assertEqual(len(updated.opcoes), 1)
        self.assertEqual(updated.opcoes[0].proxima_pergunta_id, 1002)

    def test_same_tenant_question_from_another_survey_returns_404(self):
        self.assert_not_found(lambda: projetos.update_pergunta(
            pesquisa_id=100,
            pergunta_id=1010,
            pergunta_in=schemas.PerguntaUpdate(texto_pergunta="Ataque"),
            db=self.db,
            current_user=self.user_a,
        ))

    def test_next_question_from_another_survey_returns_404(self):
        self.assert_not_found(lambda: projetos.update_pergunta(
            pesquisa_id=100,
            pergunta_id=1000,
            pergunta_in=schemas.PerguntaUpdate(opcoes=[{
                "texto": "Desvio",
                "ordem": 1,
                "proxima_pergunta_id": 1010,
            }]),
            db=self.db,
            current_user=self.user_a,
        ))

    def test_create_with_next_question_from_another_survey_returns_404(self):
        self.assert_not_found(lambda: projetos.create_pergunta(
            pesquisa_id=100,
            pergunta=schemas.PerguntaCreate(
                texto_pergunta="Nova pergunta",
                tipo_pergunta="ESCOLHA_SIMPLES",
                opcoes=[{
                    "texto": "Desvio",
                    "ordem": 1,
                    "proxima_pergunta_id": 1010,
                }],
            ),
            db=self.db,
            current_user=self.user_a,
        ))

    def test_reorder_with_question_from_another_survey_returns_404(self):
        self.assert_not_found(lambda: projetos.reordenar_perguntas(
            projeto_id=10,
            pesquisa_id=100,
            payload=schemas.PerguntasReordenarPayload(perguntas=[
                schemas.PerguntaReordenarItem(id=1010, ordem=1),
            ]),
            db=self.db,
            current_user=self.user_a,
        ))

    def test_other_tenant_question_update_returns_404(self):
        self.assert_not_found(lambda: projetos.update_pergunta(
            pesquisa_id=100,
            pergunta_id=2000,
            pergunta_in=schemas.PerguntaUpdate(texto_pergunta="Ataque"),
            db=self.db,
            current_user=self.user_a,
        ))

    def test_other_tenant_question_delete_returns_404(self):
        self.assert_not_found(lambda: projetos.delete_pergunta_endpoint(
            pesquisa_id=100,
            pergunta_id=2000,
            db=self.db,
            current_user=self.user_a,
        ))

    def test_other_tenant_question_options_update_returns_404(self):
        self.assert_not_found(lambda: projetos.update_pergunta(
            pesquisa_id=100,
            pergunta_id=2000,
            pergunta_in=schemas.PerguntaUpdate(opcoes=[{
                "texto": "Ataque",
                "ordem": 1,
                "proxima_pergunta_id": 1002,
            }]),
            db=self.db,
            current_user=self.user_a,
        ))


if __name__ == "__main__":
    unittest.main()
