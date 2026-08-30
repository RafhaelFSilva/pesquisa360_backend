# pesquisa360/core/security.py

import os

# --- Configuração de Segurança ---
# Mantenha sua SECRET_KEY segura e configure-a exclusivamente pelo ambiente.
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY environment variable is required")

from datetime import datetime, timedelta, timezone
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))
# Refresh vive muito mais que o access: e ele que absorve a expiracao normal do
# access sem devolver o usuario para a tela de login.
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))

# --- Tipo de token ---------------------------------------------------------
# Access e refresh sao assinados com a MESMA chave, entao sem uma claim que os
# distinga um refresh valeria como credencial de API -- exatamente o que o
# refresh nao pode ser. A claim abaixo e o que separa os dois.
TOKEN_TYPE_CLAIM = "type"
ACCESS_TOKEN_TYPE = "access"
REFRESH_TOKEN_TYPE = "refresh"

# --- Lógica de Hashing de Senha (BCRYPT) ---
# Usamos 'bcrypt' que é o padrão seguro da indústria (muito melhor que SHA1)
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica se a senha em texto plano corresponde ao hash salvo."""
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    """Gera o hash seguro da senha."""
    return pwd_context.hash(password)


# --- Lógica de Token JWT ---
def _create_token(
    data: dict,
    token_type: str,
    default_delta: timedelta,
    expires_delta: Optional[timedelta] = None,
) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or default_delta)
    to_encode.update({"exp": expire, TOKEN_TYPE_CLAIM: token_type})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Cria um novo token de acesso JWT."""
    return _create_token(
        data,
        ACCESS_TOKEN_TYPE,
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
        expires_delta,
    )


def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None):
    """Cria o token de renovacao. Nunca serve para autenticar a API."""
    return _create_token(
        data,
        REFRESH_TOKEN_TYPE,
        timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        expires_delta,
    )


def access_token_expires_in() -> int:
    """TTL do access em segundos, para o cliente se antecipar se quiser."""
    return ACCESS_TOKEN_EXPIRE_MINUTES * 60


def token_type_of(payload: dict) -> str:
    """Tipo declarado no token.

    JANELA DE COMPATIBILIDADE: token emitido antes desta fase nao tem a claim.
    Ausencia e lida como `access`, para nao invalidar de uma vez todas as
    sessoes ja distribuidas. O inverso NAO vale -- ausencia jamais e aceita
    como refresh, senao qualquer access antigo viraria credencial de renovacao.
    """
    tipo = payload.get(TOKEN_TYPE_CLAIM)
    if tipo is None:
        return ACCESS_TOKEN_TYPE
    return str(tipo)
