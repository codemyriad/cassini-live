"""The app's AppAPI identity: its app id, and the proxy paths built from it.

The manifest (appinfo/info.xml) registers the app as `cassini_live`, and AppAPI hands the
registered id to the container as APP_ID. Entry points read it once with `app_id_from_env`
and pass it down; nothing else reads it or spells the id.
"""

import os
import re
from collections.abc import Mapping

# AppAPI app ids: lowercase letters, digits and underscores. The id also goes into URLs
# and, through the panel bundle, into JavaScript, so nothing else may pass.
APP_ID = re.compile(r"[a-z][a-z0-9_]{0,63}")


def app_id_from_env(environ: Mapping[str, str] = os.environ) -> str:
    app_id = environ.get("APP_ID", "")
    if not APP_ID.fullmatch(app_id):
        raise RuntimeError("APP_ID is missing or invalid; register Cassini Live through AppAPI")
    return app_id


def proxy_path(app_id: str, path: str = "") -> str:
    """Root-relative AppAPI proxy path of one of this app's routes, without the web root."""
    if not APP_ID.fullmatch(app_id):
        raise ValueError("invalid app id")
    return f"/index.php/apps/app_api/proxy/{app_id}/{path.lstrip('/')}"
