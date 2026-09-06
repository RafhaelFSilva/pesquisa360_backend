"""QA-BE-001 -- seed QA compativel com o schema real de `companies.cnpj`.

O seed gravava CNPJ com mascara (18 caracteres) numa coluna VARCHAR(14) e
falhava com `StringDataRightTruncation`. Estes testes prendem tres coisas:

1. os CNPJs do seed passam pelo MESMO validador da API e cabem na coluna
   declarada no modelo (limite lido do modelo, nao hardcoded);
2. a persistencia respeita o limite num banco que o impoe de verdade
   (SQLite com CHECK de comprimento -- o teste prova que o CHECK barra o valor
   antigo antes de confiar nele);
3. opcionalmente, o seed inteiro roda DUAS vezes num PostgreSQL/PostGIS real
   e descartavel (P360_QA_DATABASE_URL) sem truncamento nem duplicidade.
"""
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine, func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("SECRET_KEY", "test-only-seed-qa-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from pesquisa360 import schemas
from pesquisa360.db import models

ROOT_DIR = Path(__file__).resolve().parents[1]
SEED_PATH = ROOT_DIR / "scripts" / "seed_qa_dataset.py"
OLD_MASKED_VALUE = "11.111.111/0001-11"  # valor que disparava o defeito


def load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_qa_dataset", SEED_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed = load_seed_module()
CNPJ_LIMIT = models.Company.cnpj.type.length


class SeedCnpjTests(unittest.TestCase):
    def test_limite_da_coluna_e_o_do_modelo(self):
        # Se alguem alargar a coluna, este teste passa a documentar o novo limite.
        self.assertEqual(CNPJ_LIMIT, 14)

    def test_cnpjs_do_seed_passam_no_validador_da_api_e_cabem_na_coluna(self):
        for company in seed.COMPANIES:
            value = company["cnpj"]
            with self.subTest(company=company["name"]):
                self.assertEqual(schemas.normalize_cnpj(value), value)
                self.assertLessEqual(len(value), CNPJ_LIMIT)
                self.assertEqual(seed.seed_cnpj(value), value)
                # Mesmo caminho de entrada da API administrativa.
                created = schemas.CompanyCreate(name=company["name"], cnpj=value)
                self.assertEqual(created.cnpj, value)

    def test_cnpjs_do_seed_sao_distintos(self):
        values = [company["cnpj"] for company in seed.COMPANIES]
        self.assertEqual(len(values), len(set(values)))

    def test_valor_antigo_nao_cabe_nem_passa_no_validador(self):
        self.assertGreater(len(OLD_MASKED_VALUE), CNPJ_LIMIT)
        with self.assertRaises(ValueError):
            schemas.normalize_cnpj(OLD_MASKED_VALUE)
        with self.assertRaises(ValueError):
            seed.seed_cnpj(OLD_MASKED_VALUE)

    def test_seed_cnpj_rejeita_valor_que_excede_a_coluna(self):
        # Blindagem: mesmo que o validador devolvesse algo maior, o seed barra.
        with patch.object(schemas, "normalize_cnpj", return_value="1" * (CNPJ_LIMIT + 1)):
            with self.assertRaisesRegex(RuntimeError, "excede"):
                seed.seed_cnpj("qualquer")


class SeedCompanyPersistenceTests(unittest.TestCase):
    """Persistencia com limite REAL de comprimento (CHECK), nao mock."""

    def setUp(self):
        self.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        with self.engine.begin() as connection:
            connection.execute(text(f"""
                CREATE TABLE companies (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                    cnpj VARCHAR({CNPJ_LIMIT}) UNIQUE
                        CHECK (cnpj IS NULL OR length(cnpj) <= {CNPJ_LIMIT}),
                    logo_url VARCHAR(2048), is_active BOOLEAN, created_at DATETIME
                )
            """))
        self.Session = sessionmaker(bind=self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_limite_do_banco_barra_o_valor_antigo(self):
        with self.Session() as db:
            db.add(models.Company(name="Empresa Antiga", cnpj=OLD_MASKED_VALUE))
            with self.assertRaises(IntegrityError):
                db.flush()

    def test_upsert_company_duas_vezes_sem_truncamento_nem_duplicidade(self):
        with self.Session() as db:
            for _ in range(2):
                for data in seed.COMPANIES:
                    seed.upsert_company(db, data)
                db.commit()

            rows = db.query(models.Company).order_by(models.Company.id).all()
            self.assertEqual(len(rows), len(seed.COMPANIES))
            self.assertEqual(
                [(row.name, row.cnpj, row.is_active) for row in rows],
                [(data["name"], data["cnpj"], True) for data in seed.COMPANIES],
            )

    def test_upsert_company_recusa_cnpj_de_outra_empresa(self):
        with self.Session() as db:
            db.add(models.Company(name="Outra Empresa", cnpj=seed.COMPANIES[0]["cnpj"]))
            db.commit()
            with self.assertRaisesRegex(RuntimeError, "outra empresa"):
                seed.upsert_company(db, seed.COMPANIES[0])


class SeedGuardTests(unittest.TestCase):
    def test_senha_vem_obrigatoriamente_da_variavel_de_ambiente(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, seed.PASSWORD_ENV):
                seed.get_seed_password()
        with patch.dict(os.environ, {seed.PASSWORD_ENV: "  senha-temporaria  "}):
            self.assertEqual(seed.get_seed_password(), "senha-temporaria")

    def test_script_nao_embute_senha(self):
        source = SEED_PATH.read_text(encoding="utf-8")
        self.assertNotIn("Qa@123456", source)
        self.assertFalse(hasattr(seed, "DEFAULT_PASSWORD"))

    def test_confirmacao_explicita_e_guarda_de_producao(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                seed.require_confirmation()
        with patch.dict(os.environ, {"CONFIRM_QA_SEED": seed.CONFIRM_VALUE}):
            seed.require_confirmation()
        with patch.dict(os.environ, {"APP_ENV": "production"}):
            with self.assertRaisesRegex(RuntimeError, "producao"):
                seed.guard_environment()

    def test_dados_sinteticos_e_deterministicos(self):
        for user in seed.USERS:
            self.assertTrue(user["email"].endswith("@pesquisa360.com"))
            self.assertIn("QA", user["name"])
        keys = [item["key"] for item in seed.TENTATIVAS]
        self.assertEqual(len(keys), len(set(keys)))
        uuids = [seed.tentativa_client_uuid(key) for key in keys]
        self.assertEqual(uuids, [seed.tentativa_client_uuid(key) for key in keys])
        self.assertEqual(len(uuids), len(set(uuids)))
        for item in seed.TENTATIVAS:
            self.assertIn(item["resultado"], {r.value for r in schemas.TentativaResultado})


QA_DATABASE_URL = os.environ.get("P360_QA_DATABASE_URL")


@unittest.skipUnless(
    QA_DATABASE_URL,
    "Defina P360_QA_DATABASE_URL (PostgreSQL/PostGIS DESCARTAVEL, ja migrado) "
    "para rodar o seed real.",
)
class SeedRealPostgresTests(unittest.TestCase):
    """Seed completo, duas vezes, contra o schema real (alembic head)."""

    TABLES = (
        "companies", "perfis", "usuarios", "projetos", "pesquisas", "perguntas",
        "opcoes", "setores", "setor_agentes", "tentativas_campo",
    )

    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(QA_DATABASE_URL)
        cls.Session = sessionmaker(bind=cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()

    def snapshot(self, db):
        return {
            table: db.execute(text(f"SELECT count(*) FROM {table}")).scalar()
            for table in self.TABLES
        }

    def test_seed_roda_duas_vezes_sem_truncamento_nem_duplicidade(self):
        with self.Session() as db:
            first = seed.run_seed(db, "senha-qa-teste")
            after_first = self.snapshot(db)
            second = seed.run_seed(db, "senha-qa-teste")
            after_second = self.snapshot(db)

            self.assertEqual(after_first, after_second)
            self.assertEqual(first["project"].id, second["project"].id)
            self.assertEqual(first["survey"].id, second["survey"].id)

            for data in seed.COMPANIES:
                company = (
                    db.query(models.Company)
                    .filter(func.lower(models.Company.name) == data["name"].lower())
                    .one()
                )
                self.assertEqual(company.cnpj, data["cnpj"])

            for name in seed.PROFILES:
                self.assertIsNotNone(
                    db.query(models.Perfil)
                    .filter(func.lower(models.Perfil.nome) == name.lower())
                    .first()
                )

            survey_id = second["survey"].id
            self.assertEqual(
                db.query(func.count(models.Setor.id))
                .filter(models.Setor.pesquisa_id == survey_id)
                .scalar(),
                len(seed.SECTORS),
            )
            self.assertEqual(
                db.query(func.count(models.TentativaCampo.id))
                .filter(models.TentativaCampo.pesquisa_id == survey_id)
                .scalar(),
                len(seed.TENTATIVAS),
            )
            self.assertEqual(
                db.query(func.count(models.SetorAgente.id))
                .join(models.Setor, models.Setor.id == models.SetorAgente.setor_id)
                .filter(models.Setor.pesquisa_id == survey_id)
                .scalar(),
                len(seed.SECTORS),
            )


if __name__ == "__main__":
    unittest.main()
