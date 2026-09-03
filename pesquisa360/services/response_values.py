"""Parser compartilhado de valores de resposta (unica semantica no projeto).

Extraido mecanicamente de `services/multidimensional_cross.py` (Prompt 03 do
Potencial de Crescimento) SEM mudanca de comportamento, para que o motor de
Potencial de Crescimento e o motor multidimensional usem exatamente o mesmo
parser de multipla escolha:

- resposta unica: escalar normalizado (strip; dict/list/bool/None viram
  ausencia);
- multipla escolha: string JSON list, lista ja desserializada ou escalar
  legado; deduplica valores dentro da mesma resposta e sinaliza a anomalia.
"""

from __future__ import annotations

import json


def normalize_scalar(value):
    if value is None or isinstance(value, (dict, list, bool)):
        return None
    normalized = str(value).strip()
    return normalized or None


def response_values(raw_value, multiple: bool):
    if not multiple:
        value = normalize_scalar(raw_value)
        return ([value] if value is not None else []), False

    parsed = raw_value
    if isinstance(raw_value, str):
        stripped = raw_value.strip()
        if not stripped:
            return [], False
        try:
            candidate = json.loads(stripped)
            parsed = candidate if isinstance(candidate, list) else stripped
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = stripped

    values = parsed if isinstance(parsed, list) else [parsed]
    result = []
    seen = set()
    duplicate_found = False
    for item in values:
        value = normalize_scalar(item)
        if value is None:
            continue
        if value in seen:
            duplicate_found = True
            continue
        seen.add(value)
        result.append(value)
    return result, duplicate_found
