# migrations/env.py
import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context
from alembic.operations import ops

# Adicione a importação da nossa Base de modelos
from pesquisa360.db.models import Base

# esta é a configuração do Alembic
config = context.config

# Interprete o arquivo de configuração para o logging do Python
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# adicione o seu MetaData aqui
target_metadata = Base.metadata

# Schemas a serem ignorados pelo autogenerate
EXCLUDED_SCHEMAS = ["tiger", "topology"]

def include_object(object, name, type_, reflected, compare_to):
    """
    Filtro para tabelas que o Alembic não deve tentar 'dropar'.
    """
    if (
        type_ == "table" and
        (
            object.schema in EXCLUDED_SCHEMAS or
            name == "spatial_ref_sys"
        )
    ):
        return False
    else:
        return True

def process_revision_directives(context, revision, directives):
    """
    Esta é a "rede de segurança". Ela inspeciona os comandos gerados
    antes de serem escritos no arquivo e remove os indesejados.
    """
    if directives[0].upgrade_ops is not None:
        upgrade_ops = directives[0].upgrade_ops.ops
        new_upgrade_ops = []
        for op in upgrade_ops:
            # A correção está aqui: usamos op.schema em vez de op.table.schema
            if not (
                isinstance(op, ops.DropTableOp) and
                op.schema in EXCLUDED_SCHEMAS
            ):
                new_upgrade_ops.append(op)
        directives[0].upgrade_ops.ops = new_upgrade_ops


def run_migrations_offline() -> None:
    """Roda migrações em modo 'offline'.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        process_revision_directives=process_revision_directives,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Roda migrações em modo 'online'.
    """
    config.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            process_revision_directives=process_revision_directives,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()