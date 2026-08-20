import re
import unicodedata


CANONICAL_QUESTION_TYPES = (
    "TEXTO",
    "TEXTO_LONGO",
    "NUMERO",
    "ESCOLHA_SIMPLES",
    "MULTIPLA_ESCOLHA",
    "DATA",
    "IMAGEM",
    "ESCALA",
)

QUESTION_TYPE_ALIASES = {
    "TEXT": "TEXTO",
    "TEXTO_CURTO": "TEXTO",
    "TEXTAREA": "TEXTO_LONGO",
    "LONG_TEXT": "TEXTO_LONGO",
    "PARAGRAFO": "TEXTO_LONGO",
    "NUMBER": "NUMERO",
    "NUMERICO": "NUMERO",
    "INTEIRO": "NUMERO",
    "RADIO": "ESCOLHA_SIMPLES",
    "RADIO_BUTTON": "ESCOLHA_SIMPLES",
    "ESCOLHA_UNICA": "ESCOLHA_SIMPLES",
    "UNICA_ESCOLHA": "ESCOLHA_SIMPLES",
    "MULTIPLA_ESCOLHA_UNICA": "ESCOLHA_SIMPLES",
    "SINGLE_CHOICE": "ESCOLHA_SIMPLES",
    "UNICA": "ESCOLHA_SIMPLES",
    "CHECKBOX": "MULTIPLA_ESCOLHA",
    "CHECKBOXES": "MULTIPLA_ESCOLHA",
    "MULTIPLE_CHOICE": "MULTIPLA_ESCOLHA",
    "MULTIPLA": "MULTIPLA_ESCOLHA",
    "MULTIPLA_ESCOLHA_MULTIPLA": "MULTIPLA_ESCOLHA",
    "DATE": "DATA",
    "DATA_HORA": "DATA",
    "DATETIME": "DATA",
    "DATE_TIME": "DATA",
    "FOTO": "IMAGEM",
    "IMAGE": "IMAGEM",
    "PHOTO": "IMAGEM",
    "CAMERA": "IMAGEM",
    "SCALE": "ESCALA",
    "RATING": "ESCALA",
    "AVALIACAO": "ESCALA",
    "ESTRELAS": "ESCALA",
}

QUESTION_TYPE_MAP = {
    **{tipo: tipo for tipo in CANONICAL_QUESTION_TYPES},
    **QUESTION_TYPE_ALIASES,
}


def question_type_key(raw: str) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(raw or "").strip())
    value = value.upper().replace("-", "_")
    value = re.sub(r"\s+", "_", value)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"_+", "_", value)


def normalize_question_type(raw: str) -> str:
    key = question_type_key(raw)
    return QUESTION_TYPE_MAP.get(key, key)


CATEGORICAL_QUESTION_TYPES = frozenset({"ESCOLHA_SIMPLES", "MULTIPLA_ESCOLHA"})


def is_categorical_question_type(raw: str) -> bool:
    return normalize_question_type(raw) in CATEGORICAL_QUESTION_TYPES


def is_multiple_response_question_type(raw: str) -> bool:
    return normalize_question_type(raw) == "MULTIPLA_ESCOLHA"
