"""INFRA-001 -- a imagem Docker precisa ser autocontida.

O compose de DEV monta o repositorio inteiro em /app (hot-reload), o que
escondia que o Dockerfile copiava SO o pacote `pesquisa360`: sem o bind mount
(staging/producao) a API morria no import com
`RuntimeError: Directory 'static' does not exist`, e `alembic`/`scripts/`
nao existiam dentro do container. Estes testes leem o Dockerfile de verdade.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8")
MAIN = (ROOT / "pesquisa360" / "main.py").read_text(encoding="utf-8")


def _copy_sources() -> set[str]:
    sources = set()
    for line in DOCKERFILE.splitlines():
        match = re.match(r"\s*COPY\s+(.+?)\s+\S+\s*$", line)
        if match:
            for source in match.group(1).split():
                sources.add(source.strip().lstrip("./").rstrip("/"))
    return sources


class DockerfilePackagingTests(unittest.TestCase):
    def test_diretorios_montados_por_staticfiles_existem_na_imagem(self):
        # Todo `StaticFiles(directory="<literal>")` do main.py precisa existir na
        # imagem (criado ou copiado); diretorios vindos de env ficam para o
        # volume/UPLOAD_DIRECTORY.
        literais = re.findall(r'StaticFiles\(directory="([^"]+)"\)', MAIN)
        self.assertIn("static", literais)
        for directory in literais:
            with self.subTest(directory=directory):
                garantido = (
                    re.search(rf"mkdir\s+-p\s+[^\n]*\b{re.escape(directory)}\b", DOCKERFILE)
                    or directory in _copy_sources()
                )
                self.assertTrue(garantido, f"Dockerfile nao garante o diretorio {directory!r}")

    def test_migrations_alembic_e_scripts_entram_na_imagem(self):
        sources = _copy_sources()
        for required in ("pesquisa360", "migrations", "alembic.ini", "scripts"):
            with self.subTest(required=required):
                self.assertIn(required, sources)

    def test_imagem_nao_depende_de_bind_mount_para_uploads_default(self):
        # UPLOAD_DIRECTORY default do .env.example e /app/uploads: o diretorio
        # precisa existir mesmo sem volume montado.
        self.assertRegex(DOCKERFILE, r"mkdir\s+-p\s+[^\n]*\buploads\b")


if __name__ == "__main__":
    unittest.main()
