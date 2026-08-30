"""Tabelas da ACL (ADR-024) para fixtures de DDL manual.

O schema real passou a ter `usuario_empresa_acessos` e `usuario_projeto_acessos`,
e a expressao de autorizacao as consulta em toda leitura. Fixture que monta o
schema a mao precisa cria-las tambem.

Nenhuma LINHA e inserida de proposito: sem ACL configurada vale o fallback de
compatibilidade (empresa principal), que e exatamente o comportamento que esses
testes historicos exercitam.
"""
from pesquisa360.db import models


def criar_tabelas_acl(engine) -> None:
    """Idempotente: `checkfirst` deixa passar quem ja tem as tabelas."""
    models.UsuarioEmpresaAcesso.__table__.create(engine, checkfirst=True)
    models.UsuarioProjetoAcesso.__table__.create(engine, checkfirst=True)
