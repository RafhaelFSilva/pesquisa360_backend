# pesquisa360/db/session.py

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Pega a URL do banco de dados da variável de ambiente
DATABASE_URL = os.environ["DATABASE_URL"]

# Cria o "motor" de conexão do SQLAlchemy
engine = create_engine(DATABASE_URL)

# Cria uma classe SessionLocal que será nossa sessão de banco de dados
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)