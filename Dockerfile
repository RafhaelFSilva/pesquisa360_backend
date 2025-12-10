# Dockerfile Simplificado e Robusto
FROM python:3.13-slim

# Define o diretório de trabalho
WORKDIR /app

# Desativa a criação de ambientes virtuais pelo Poetry.
# Ele vai instalar no ambiente principal do contêiner.
ENV POETRY_VIRTUALENVS_CREATE=false

# Instala o Poetry
RUN pip install poetry

# Copia os arquivos de dependência
COPY pyproject.toml poetry.lock ./

# Instala as dependências diretamente no sistema.
# O --no-root é importante para não instalar o próprio projeto, apenas as dependências.
RUN poetry install --no-root --no-interaction --no-ansi

# Copia o código da nossa aplicação
COPY ./pesquisa360 ./pesquisa360

# O python aqui é o do contêiner, que agora conhece as dependências
CMD ["python", "-m", "uvicorn", "pesquisa360.main:app", "--host", "0.0.0.0", "--port", "8000"]
