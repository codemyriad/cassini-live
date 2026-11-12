"""Start the HaRP tunnel when requested, and reap both services on shutdown."""

import json
import os
import pwd
import resource
import signal
import subprocess
import sys
from pathlib import Path


def own_storage(path, uid, gid):
    """Give the app's persistent volume to the unprivileged account.

    AppAPI creates nc_app_<app id>_data owned by root, and the services drop to
    `cassini` below, so without this nothing could be saved: the HPB secret from
    the settings page, engine settings, downloaded models, the recovery journal.
    Only entries owned by someone else change; symlinks are not followed.
    """
    root = Path(path)
    if not root.is_dir():
        return
    for directory, dirs, files in os.walk(root, followlinks=False):
        for entry in [directory, *(os.path.join(directory, n) for n in dirs + files)]:
            stat = os.lstat(entry)
            if (stat.st_uid, stat.st_gid) != (uid, gid):
                os.lchown(entry, uid, gid)
    root.chmod(0o700)


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    if os.getuid() == 0:
        account = pwd.getpwnam("cassini")
        for name in ("ca.crt", "client.crt", "client.key"):
            cert = Path("/certs/frp") / name
            if cert.is_file():
                os.chown(cert, account.pw_uid, account.pw_gid)
                cert.chmod(0o600)
        if os.environ.get("APP_PERSISTENT_STORAGE"):
            own_storage(os.environ["APP_PERSISTENT_STORAGE"], account.pw_uid, account.pw_gid)
        os.setgroups([])
        os.setgid(account.pw_gid)
        os.setuid(account.pw_uid)
    children = []
    if os.environ.get("HP_SHARED_KEY"):
        # JSON string escaping is also valid for TOML basic strings.
        quote = json.dumps
        cfg = [
            f"serverAddr = {quote(os.environ['HP_FRP_ADDRESS'])}",
            f"serverPort = {int(os.environ['HP_FRP_PORT'])}",
            "loginFailExit = false",
            "transport.tls.enable = true",
            'transport.tls.serverName = "harp.nc"',
            f"metadatas.token = {quote(os.environ['HP_SHARED_KEY'])}",
        ]
        if Path("/certs/frp").is_dir():
            cfg += [
                'transport.tls.certFile = "/certs/frp/client.crt"',
                'transport.tls.keyFile = "/certs/frp/client.key"',
                'transport.tls.trustedCaFile = "/certs/frp/ca.crt"',
            ]
        cfg += [
            "[[proxies]]",
            f"name = {quote(os.environ['APP_ID'])}",
            'type = "tcp"',
            f"remotePort = {int(os.environ['APP_PORT'])}",
            "[proxies.plugin]",
            'type = "unix_domain_socket"',
            f"unixPath = {quote(os.environ.get('HP_EXAPP_SOCK', '/tmp/exapp.sock'))}",
        ]
        path = Path("/tmp/frpc.toml")
        path.touch(mode=0o600)
        path.write_text("\n".join(cfg) + "\n")
        children.append(subprocess.Popen(["frpc", "-c", str(path)]))
    children.append(subprocess.Popen([sys.executable, "-m", "cassini_live.app"]))

    def stop(_signal=None, _frame=None):
        for child in children:
            if child.poll() is None:
                child.terminate()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        # Also fail if frpc exits, so the deployment can restart a broken tunnel.
        while all(child.poll() is None for child in children):
            try:
                children[-1].wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
    finally:
        stop()
        for child in children:
            try:
                child.wait(timeout=20)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return children[-1].returncode or 1


if __name__ == "__main__":
    sys.exit(main())
