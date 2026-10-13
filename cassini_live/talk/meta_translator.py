"""Inert adapter for the upstream transport's optional translation hooks.

Cassini Live never advertises translation and never sends text to an LLM.
"""

from .types import TranslateLangPairException


class MetaTranslator:
    def __init__(self, *_args):
        pass

    async def shutdown(self):
        pass

    async def is_translating(self):
        return False

    async def is_translation_target(self, _session):
        return False

    async def is_target_lang_supported(self, _language):
        return False

    async def remove_translator(self, _session):
        pass

    async def add_translator(self, *_args):
        raise TranslateLangPairException("Translation is not supported")
