"""Politica minima de senha do Pesquisa360.

Regra unica e centralizada -- o mesmo texto de erro vale para ativacao de conta
e, quando existir, para recuperacao de senha. Nao duplicar regex por endpoint:
duas copias divergem no primeiro ajuste.

    minimo 6 caracteres
    ao menos uma letra
    ao menos um numero

    abc123     -> valido
    pesquisa9  -> valido
    123456     -> invalido (sem letra)
    abcdef     -> invalido (sem numero)
    a12        -> invalido (curta)

As mensagens falam do requisito, nunca do mecanismo de hashing.
"""
from __future__ import annotations

COMPRIMENTO_MINIMO = 6

MENSAGEM_CURTA = f"A senha deve possuir no minimo {COMPRIMENTO_MINIMO} caracteres."
MENSAGEM_COMPOSICAO = "A senha deve conter pelo menos uma letra e um numero."


def erro_da_senha(senha: str | None) -> str | None:
    """Primeiro problema encontrado, ou None quando a senha e aceitavel.

    Devolve mensagem em vez de lancar: quem chama decide se vira 422 (schema)
    ou 400 (regra de negocio).
    """
    valor = senha or ""
    if len(valor) < COMPRIMENTO_MINIMO:
        return MENSAGEM_CURTA
    # `isalpha`/`isdigit` do Python sao unicode-aware: "senhá1" tem letra, e
    # digito arabico-indico conta como numero. E o comportamento desejado.
    if not any(c.isalpha() for c in valor) or not any(c.isdigit() for c in valor):
        return MENSAGEM_COMPOSICAO
    return None


def senha_valida(senha: str | None) -> bool:
    return erro_da_senha(senha) is None
