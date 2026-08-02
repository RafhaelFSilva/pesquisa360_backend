# pesquisa360/main.py

from fastapi import FastAPI, UploadFile, File, Depends
from fastapi import HTTPException, status
from fastapi.staticfiles import StaticFiles
import os
import uuid
from pathlib import Path
from fastapi.middleware.cors import CORSMiddleware

from .db import models
from .db.session import engine
from .api.endpoints import login, usuarios, projetos, coletas, relatorios, agente, locais, empresas
from .core.dependencies import get_current_user


ALLOWED_UPLOAD_SIGNATURES = {
    "image/jpeg": ("jpg", lambda data: data.startswith(b"\xff\xd8\xff")),
    "image/png": ("png", lambda data: data.startswith(b"\x89PNG\r\n\x1a\n")),
    "image/webp": (
        "webp",
        lambda data: len(data) >= 12
        and data.startswith(b"RIFF")
        and data[8:12] == b"WEBP",
    ),
}


def _load_upload_settings() -> tuple[Path, int]:
    directory_value = os.environ.get("UPLOAD_DIRECTORY")
    if not directory_value or not directory_value.strip():
        raise RuntimeError("UPLOAD_DIRECTORY environment variable is required")

    max_size_value = os.environ.get("UPLOAD_MAX_SIZE_BYTES")
    if not max_size_value or not max_size_value.strip():
        raise RuntimeError("UPLOAD_MAX_SIZE_BYTES environment variable is required")
    try:
        max_size = int(max_size_value)
    except ValueError as exc:
        raise RuntimeError("UPLOAD_MAX_SIZE_BYTES must be a positive integer") from exc
    if max_size <= 0:
        raise RuntimeError("UPLOAD_MAX_SIZE_BYTES must be a positive integer")

    directory = Path(directory_value).expanduser().resolve()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RuntimeError("UPLOAD_DIRECTORY could not be initialized") from exc
    if not directory.is_dir():
        raise RuntimeError("UPLOAD_DIRECTORY must be a directory")
    return directory, max_size


UPLOAD_DIRECTORY, UPLOAD_MAX_SIZE_BYTES = _load_upload_settings()

app = FastAPI(
    title="Pesquisa360 API",
    description="Backend da plataforma Pesquisa360 para gestão de pesquisas de campo.",
    version="0.1.0"
)

app.mount("/static/uploads", StaticFiles(directory=UPLOAD_DIRECTORY), name="uploads")
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- CONFIGURAÇÃO DO CORS ---
cors_allowed_origins = os.environ.get("CORS_ALLOWED_ORIGINS")
origins = [
    origin.strip()
    for origin in cors_allowed_origins.split(",")
    if origin.strip()
] if cors_allowed_origins is not None else []

if not origins:
    raise RuntimeError("CORS_ALLOWED_ORIGINS must contain at least one valid origin")

if "*" in origins:
    raise RuntimeError("CORS_ALLOWED_ORIGINS cannot contain a wildcard when credentials are enabled")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# --- FIM DA CONFIGURAÇÃO DO CORS ---

@app.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    current_user: models.Usuario = Depends(get_current_user),
):
    company_id = getattr(current_user, "company_id", None)
    if company_id is None:
        raise HTTPException(status_code=422, detail="Usuario sem tenant para upload.")

    tenant_directory = UPLOAD_DIRECTORY / str(company_id)
    tenant_directory.mkdir(parents=True, exist_ok=True)

    detected_mime = None
    extension = None
    target_path = None
    output = None
    file_created = False
    try:
        first_chunk = await file.read(min(64 * 1024, UPLOAD_MAX_SIZE_BYTES + 1))
        for mime_type, (candidate_extension, signature_check) in ALLOWED_UPLOAD_SIGNATURES.items():
            if signature_check(first_chunk):
                detected_mime = mime_type
                extension = candidate_extension
                break

        if detected_mime is None or file.content_type != detected_mime:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Formato de imagem nao permitido ou conteudo incompatível.",
            )

        for _ in range(5):
            filename = f"{uuid.uuid4().hex}.{extension}"
            target_path = tenant_directory / filename
            try:
                output = target_path.open("xb")
                file_created = True
                break
            except FileExistsError:
                continue
        else:
            raise RuntimeError("Nao foi possivel gerar um nome de upload exclusivo")

        total_size = len(first_chunk)
        if total_size > UPLOAD_MAX_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="Arquivo excede o tamanho maximo permitido.",
            )
        output.write(first_chunk)

        while chunk := await file.read(64 * 1024):
            total_size += len(chunk)
            if total_size > UPLOAD_MAX_SIZE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                    detail="Arquivo excede o tamanho maximo permitido.",
                )
            output.write(chunk)

        output.close()
        output = None
        return {"url": f"/static/uploads/{company_id}/{filename}"}
    except Exception:
        if output is not None:
            output.close()
        if file_created and target_path is not None:
            target_path.unlink(missing_ok=True)
        raise

@app.get("/", tags=["Root"])
def read_root():
    """Endpoint raiz da API."""
    return {"message": "API Pesquisa360 no ar!"}

# --- INCLUSÃO DE ROTAS ---

# Login (prefixo /login + rota /token = /login/token)
app.include_router(login.router, prefix="/login", tags=["Login"])

# Usuários (prefixo /usuarios + rota / = /usuarios/)
app.include_router(usuarios.router, prefix="/usuarios", tags=["Usuarios"])
app.include_router(usuarios.admin_router)
app.include_router(usuarios.profiles_router)

# Empresas / tenants
app.include_router(empresas.router, prefix="/empresas", tags=["Empresas"])

# Projetos
# CORREÇÃO AQUI: Removemos o prefixo porque as rotas dentro de projetos.py já começam com /projetos
app.include_router(projetos.router, tags=["Projetos"]) 

# Coletas
app.include_router(coletas.router, tags=["Coletas"])

# Relatórios
app.include_router(relatorios.router, tags=["Relatorios"])

# Agente
app.include_router(agente.router, prefix="/agente", tags=["Agente"])

# Locais de Votação
app.include_router(locais.router, prefix="/locais", tags=["Locais de Votação"])
