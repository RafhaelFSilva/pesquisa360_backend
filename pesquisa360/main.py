# pesquisa360/main.py

from fastapi import FastAPI, UploadFile, File, Depends
from fastapi.staticfiles import StaticFiles
import shutil
import os
import uuid
from fastapi.middleware.cors import CORSMiddleware

from .db import models
from .db.session import engine
from .api.endpoints import login, usuarios, projetos, coletas, relatorios, agente, locais

app = FastAPI(
    title="Pesquisa360 API",
    description="Backend da plataforma Pesquisa360 para gestão de pesquisas de campo.",
    version="0.1.0"
)

# Cria pasta de uploads se não existir
os.makedirs("static/uploads", exist_ok=True)
app.mount("/static", StaticFiles(directory="static"), name="static")

# --- CONFIGURAÇÃO DO CORS ---
origins = [
    "http://localhost:5173",          # React em desenvolvimento
    "http://127.0.0.1:5173",          # Alternativa para local
    # "https://360.rtecnologia.online", # Produção
    "*"                               # Em dev, pode deixar * para facilitar
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# --- FIM DA CONFIGURAÇÃO DO CORS ---

@app.post("/upload")
async def upload_file(file: UploadFile = File(...)):
    # Gera extensão e nome único
    ext = file.filename.split(".")[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    file_path = f"static/uploads/{filename}"
    
    # Salva no disco
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # Retorna a URL relativa
    return {"url": f"/static/uploads/{filename}"}

@app.get("/", tags=["Root"])
def read_root():
    """Endpoint raiz da API."""
    return {"message": "API Pesquisa360 no ar!"}

# --- INCLUSÃO DE ROTAS ---

# Login (prefixo /login + rota /token = /login/token)
app.include_router(login.router, prefix="/login", tags=["Login"])

# Usuários (prefixo /usuarios + rota / = /usuarios/)
app.include_router(usuarios.router, prefix="/usuarios", tags=["Usuarios"])

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