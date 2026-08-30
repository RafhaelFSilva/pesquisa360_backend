import os
import shutil
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

UPLOAD_TEST_DIRECTORY = Path(tempfile.mkdtemp(prefix="pesquisa360-upload-test-"))
os.environ.setdefault("SECRET_KEY", "test-only-secure-upload-key")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ["UPLOAD_DIRECTORY"] = str(UPLOAD_TEST_DIRECTORY)
os.environ["UPLOAD_MAX_SIZE_BYTES"] = "64"

from pesquisa360.core import security
from pesquisa360.core.dependencies import get_db
from pesquisa360.main import _load_upload_settings, app
from tests.acl_fixture import criar_tabelas_acl


class SecureUploadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        criar_tabelas_acl(cls.engine)
        cls.Session = sessionmaker(bind=cls.engine)
        with cls.engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE companies (
                    id INTEGER PRIMARY KEY, name TEXT NOT NULL, cnpj TEXT,
                    logo_url TEXT, is_active BOOLEAN, created_at DATETIME
                )
            """))
            connection.execute(text("""
                CREATE TABLE perfis (
                    id INTEGER PRIMARY KEY, nome TEXT NOT NULL UNIQUE, descricao TEXT
                )
            """))
            connection.execute(text("""
                CREATE TABLE usuarios (
                    id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE, nome TEXT,
                    senha_hash TEXT NOT NULL, ativo BOOLEAN NOT NULL,
                    perfil_id INTEGER NOT NULL, company_id INTEGER NOT NULL
                )
            """))

        def override_get_db():
            db = cls.Session()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_get_db
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        app.dependency_overrides.clear()
        cls.engine.dispose()
        shutil.rmtree(UPLOAD_TEST_DIRECTORY, ignore_errors=True)

    def setUp(self):
        for child in UPLOAD_TEST_DIRECTORY.iterdir():
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()

        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM usuarios"))
            connection.execute(text("DELETE FROM perfis"))
            connection.execute(text("DELETE FROM companies"))
            connection.execute(text("""
                INSERT INTO companies (id, name, is_active)
                VALUES (10, 'Tenant A', 1), (20, 'Tenant B', 1)
            """))
            connection.execute(text("INSERT INTO perfis (id, nome) VALUES (99, 'Agente')"))
            connection.execute(text("""
                INSERT INTO usuarios
                    (id, email, nome, senha_hash, ativo, perfil_id, company_id)
                VALUES (1, 'agent-a@example.com', 'Agent A', 'hash', 1, 99, 10),
                       (2, 'agent-b@example.com', 'Agent B', 'hash', 1, 99, 20)
            """))

    def token_for(self, email):
        return security.create_access_token(data={"sub": email})

    def upload(self, content, mime, filename="image.bin", email="agent-a@example.com"):
        token = self.token_for(email)
        return self.client.post(
            "/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (filename, content, mime)},
        )

    def uploaded_paths(self):
        return [path for path in UPLOAD_TEST_DIRECTORY.rglob("*") if path.is_file()]

    def test_upload_without_token_returns_401(self):
        response = self.client.post(
            "/upload",
            files={"file": ("image.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        self.assertEqual(response.status_code, 401)

    def test_missing_or_invalid_upload_configuration_fails_clearly(self):
        original_directory = os.environ.get("UPLOAD_DIRECTORY")
        original_max_size = os.environ.get("UPLOAD_MAX_SIZE_BYTES")
        try:
            invalid_settings = (
                (None, "64"),
                (str(UPLOAD_TEST_DIRECTORY), None),
                (str(UPLOAD_TEST_DIRECTORY), "invalid"),
                (str(UPLOAD_TEST_DIRECTORY), "0"),
            )
            for directory, max_size in invalid_settings:
                with self.subTest(directory=directory, max_size=max_size):
                    if directory is None:
                        os.environ.pop("UPLOAD_DIRECTORY", None)
                    else:
                        os.environ["UPLOAD_DIRECTORY"] = directory
                    if max_size is None:
                        os.environ.pop("UPLOAD_MAX_SIZE_BYTES", None)
                    else:
                        os.environ["UPLOAD_MAX_SIZE_BYTES"] = max_size
                    with self.assertRaises(RuntimeError):
                        _load_upload_settings()
        finally:
            os.environ["UPLOAD_DIRECTORY"] = original_directory
            os.environ["UPLOAD_MAX_SIZE_BYTES"] = original_max_size

    def test_inactive_user_returns_401(self):
        token = self.token_for("agent-a@example.com")
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE usuarios SET ativo = 0 WHERE id = 1"))
        response = self.client.post(
            "/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("image.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        self.assertEqual(response.status_code, 401)

    def test_inactive_company_returns_401(self):
        token = self.token_for("agent-a@example.com")
        with self.engine.begin() as connection:
            connection.execute(text("UPDATE companies SET is_active = 0 WHERE id = 10"))
        response = self.client.post(
            "/upload",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": ("image.png", b"\x89PNG\r\n\x1a\n", "image/png")},
        )
        self.assertEqual(response.status_code, 401)

    def test_supported_image_signatures_are_allowed(self):
        samples = (
            ("photo.jpeg", b"\xff\xd8\xffjpeg", "image/jpeg", ".jpg"),
            ("photo.png", b"\x89PNG\r\n\x1a\npng", "image/png", ".png"),
            ("photo.webp", b"RIFF\x04\x00\x00\x00WEBPdata", "image/webp", ".webp"),
        )
        for filename, content, mime, extension in samples:
            with self.subTest(mime=mime):
                response = self.upload(content, mime, filename)
                self.assertEqual(response.status_code, 200)
                self.assertTrue(response.json()["url"].endswith(extension))

    def test_valid_extension_with_fake_content_returns_415(self):
        response = self.upload(b"not-a-png", "image/png", "photo.png")
        self.assertEqual(response.status_code, 415)

    def test_allowed_mime_with_incompatible_signature_returns_415(self):
        response = self.upload(b"\xff\xd8\xffjpeg", "image/png", "photo.png")
        self.assertEqual(response.status_code, 415)

    def test_unsupported_format_returns_415(self):
        response = self.upload(b"GIF89a", "image/gif", "photo.gif")
        self.assertEqual(response.status_code, 415)

    def test_file_above_limit_returns_413_and_leaves_no_partial_file(self):
        response = self.upload(b"\xff\xd8\xff" + b"x" * 100, "image/jpeg", "large.jpg")
        self.assertEqual(response.status_code, 413)
        self.assertEqual(self.uploaded_paths(), [])

    def test_original_name_cannot_control_destination(self):
        response = self.upload(b"\x89PNG\r\n\x1a\n", "image/png", "../../escape.png")
        self.assertEqual(response.status_code, 200)
        stored = self.uploaded_paths()
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0].parent, UPLOAD_TEST_DIRECTORY / "10")
        self.assertNotIn("escape", stored[0].name)

    def test_same_original_name_does_not_overwrite(self):
        first = self.upload(b"\x89PNG\r\n\x1a\nfirst", "image/png", "same.png")
        second = self.upload(b"\x89PNG\r\n\x1a\nsecond", "image/png", "same.png")
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertNotEqual(first.json()["url"], second.json()["url"])
        self.assertEqual(len(self.uploaded_paths()), 2)

    def test_different_tenants_use_separate_directories(self):
        first = self.upload(b"\x89PNG\r\n\x1a\n", "image/png", email="agent-a@example.com")
        second = self.upload(b"\x89PNG\r\n\x1a\n", "image/png", email="agent-b@example.com")
        self.assertEqual((first.status_code, second.status_code), (200, 200))
        self.assertTrue((UPLOAD_TEST_DIRECTORY / "10").is_dir())
        self.assertTrue((UPLOAD_TEST_DIRECTORY / "20").is_dir())

    def test_response_does_not_reveal_physical_path(self):
        response = self.upload(b"\xff\xd8\xffjpeg", "image/jpeg", "photo.jpg")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(str(UPLOAD_TEST_DIRECTORY), response.text)
        self.assertTrue(response.json()["url"].startswith("/static/uploads/10/"))


if __name__ == "__main__":
    unittest.main()
