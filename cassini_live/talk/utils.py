import hashlib
import hmac
import ssl

from ..config import websocket_url


def hmac_sha256(key, message):
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def get_ssl_context(server_addr):
    return ssl.create_default_context() if server_addr.startswith(("https:", "wss:")) else None


sanitize_websocket_url = websocket_url
