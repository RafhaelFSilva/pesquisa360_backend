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
    "SINGLE_CHOICE": "ESCOLHA_SIMPLES",
    "UNICA": "ESCOLHA_SIMPLES",
    "CHECKBOX": "MULTIPLA_ESCOLHA",
    "CHECKBOXES": "MULTIPLA_ESCOLHA",
    "MULTIPLE_CHOICE": "MULTIPLA_ESCOLHA",
    "MULTIPLA": "MULTIPLA_ESCOLHA",
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
    value = str(raw or "").strip().upper().replace("-", "_")
    value = re.sub(r"\s+", "_", value)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"_+", "_", value)


def normalize_question_type(raw: str) -> str:
    key = question_type_key(raw)
    return QUESTION_TYPE_MAP.get(key, key)
