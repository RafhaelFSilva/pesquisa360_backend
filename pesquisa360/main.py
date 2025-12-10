# pesquisa360/main.py

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware # <- NOVA IMPORTAÇÃO

from .db import models
from .db.session import engine
from .api.endpoints import login, usuarios, projetos, coletas, relatorios, agente # <- Adicionamos agente

app = FastAPI(
    title="Pesquisa360 API",
    description="Backend da plataforma Pesquisa360 para gestão de pesquisas de campo.",
    version="0.1.0"
)

# --- CONFIGURAÇÃO DO CORS ---
# Lista de "endereços" que têm permissão para acessar nossa API
origins = [
    "http://localhost:5173",               # React em desenvolvimento
    "https://360.rtecnologia.online",      # React em produção (HostGator)
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins, # Permite as origens da lista
    allow_credentials=True,
    allow_methods=["*"], # Permite todos os métodos (GET, POST, etc)
    allow_headers=["*"], # Permite todos os cabeçalhos
)
# --- FIM DA CONFIGURAÇÃO DO CORS ---

@app.get("/", tags=["Root"])
def read_root():
    """Endpoint raiz da API."""
    return {"message": "API Pesquisa360 no ar!"}

# Inclui os roteadores no aplicativo principal
app.include_router(login.router, prefix="/login", tags=["Login"])
app.include_router(usuarios.router, prefix="/usuarios", tags=["Usuários"])
app.include_router(projetos.router, prefix="/projetos", tags=["Projetos"])
app.include_router(coletas.router, tags=["Coletas"])
app.include_router(relatorios.router, prefix="/relatorios", tags=["Relatórios"])
app.include_router(agente.router, prefix="/agente", tags=["Agente"]) # <- Adicionamos esta linha