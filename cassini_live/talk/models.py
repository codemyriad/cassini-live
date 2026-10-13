from ..config import LANGUAGE_NAMES
from .types import LanguageModel

LANGUAGE_MAP = {code: LanguageModel(name=name) for code, name in LANGUAGE_NAMES.items()}
LANGUAGE_MAP["auto"] = LanguageModel(name="Automatic")
