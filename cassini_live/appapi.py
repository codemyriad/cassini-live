"""The app's AppAPI identity: its app id, and the proxy paths built from it.

The same code runs under two app ids, one per manifest: `cassini_live` from the App Store
(appstore/info.xml) and `live_transcription` registered by hand (appinfo/info.xml), the id
Talk's caption button calls. AppAPI hands the registered id to the container as APP_ID.
Entry points read it once with `app_id_from_env` and pass it down; nothing else reads it
or spells either id.
"""

import os
import re
from collections.abc import Mapping

# AppAPI app ids: lowercase letters, digits and underscores. The id also goes into URLs
# and, through the panel bundle, into JavaScript, so nothing else may pass.
APP_ID = re.compile(r"[a-z][a-z0-9_]{0,63}")
# Talk sends its caption button, server to server, only to an app with this id. It is
# Talk's contract, not ours: the one id the code names, to tell native mode apart.
TALK_CAPTION_PROVIDER = "live_transcription"


def app_id_from_env(environ: Mapping[str, str] = os.environ) -> str:
    app_id = environ.get("APP_ID", "")
    if not APP_ID.fullmatch(app_id):
        raise RuntimeError("APP_ID is missing or invalid; register Cassini Live through AppAPI")
    return app_id


def is_talk_provider(app_id: str) -> bool:
    """Whether Talk's caption button reaches this app (native mode)."""
    return app_id == TALK_CAPTION_PROVIDER


def proxy_path(app_id: str, path: str = "") -> str:
    """Root-relative AppAPI proxy path of one of this app's routes, without the web root."""
    if not APP_ID.fullmatch(app_id):
        raise ValueError("invalid app id")
    return f"/index.php/apps/app_api/proxy/{app_id}/{path.lstrip('/')}"
