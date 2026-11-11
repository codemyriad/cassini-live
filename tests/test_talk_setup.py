"""The HPB secret and URL override: deploy options first, then the settings page."""

import json
import stat
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from cassini_live.app import create_app
from cassini_live.config import Config
from cassini_live.pool import WorkerPool
from cassini_live.service import CaptionService
from cassini_live.talk.types import HPBSettings
from cassini_live.talk_setup import DEPLOY, DISCOVERED, SAVED, UNSET, SetupRefused, TalkSetup
from tests.test_settings_page import assert_secure, error, same_origin

TALK_SETTINGS = {"server": "https://cloud.test/signaling/", "stunservers": [], "turnservers": []}


def refused(setup, data, status, code):
    with pytest.raises(SetupRefused) as caught:
        setup.save(data)
    assert (caught.value.status, caught.value.code) == (status, code)


# Precedence and storage


def test_deploy_options_win_and_cannot_be_changed(tmp_path):
    setup = TalkSetup("deployed", "http://hpb.test:8080", tmp_path / "talk.json")
    assert setup.secret() == ("deployed", DEPLOY)
    assert setup.hpb_url() == ("http://hpb.test:8080", DEPLOY)
    refused(setup, {"secret": "other"}, 409, "deploy_options")
    refused(setup, {"hpbUrl": ""}, 409, "deploy_options")
    assert not (tmp_path / "talk.json").exists()


def test_saved_values_apply_without_deploy_options_and_persist(tmp_path):
    path = tmp_path / "state" / "talk.json"
    setup = TalkSetup("", "", path)
    assert setup.secret() == ("", UNSET)
    assert setup.hpb_url() == ("", DISCOVERED)
    assert setup.save({"secret": "  s3cret\n", "hpbUrl": " http://hpb.test:8080 "}) is True
    assert setup.secret() == ("s3cret", SAVED)
    assert setup.hpb_url() == ("http://hpb.test:8080", SAVED)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["talk.json"]  # no temporary left
    again = TalkSetup("", "", path)
    assert again.secret() == ("s3cret", SAVED)
    assert again.hpb_url() == ("http://hpb.test:8080", SAVED)


def test_a_deployed_secret_still_allows_saving_the_url(tmp_path):
    setup = TalkSetup("deployed", "", tmp_path / "talk.json")
    assert setup.save({"hpbUrl": "http://hpb.test:8080"}) is True
    assert setup.secret() == ("deployed", DEPLOY)
    assert setup.hpb_url() == ("http://hpb.test:8080", SAVED)


def test_empty_values_clear_and_unchanged_values_report_no_change(tmp_path):
    setup = TalkSetup("", "", tmp_path / "talk.json")
    setup.save({"secret": "s3cret", "hpbUrl": "http://hpb.test:8080"})
    assert setup.save({"secret": "s3cret"}) is False
    assert setup.save({"hpbUrl": ""}) is True
    assert setup.hpb_url() == ("", DISCOVERED)
    assert setup.save({"secret": ""}) is True
    assert setup.secret() == ("", UNSET)
    assert json.loads((tmp_path / "talk.json").read_text()) == {}


@pytest.mark.parametrize(
    ("data", "code"),
    [
        ({}, "bad_request"),
        ({"other": "x"}, "bad_request"),
        ({"secret": 7}, "bad_request"),
        ({"secret": "two words"}, "bad_secret"),
        ({"secret": "x" * 257}, "bad_secret"),
        ({"hpbUrl": "file:///etc/passwd"}, "bad_url"),
        ({"hpbUrl": "https://user:pw@hpb.test"}, "bad_url"),
        ({"hpbUrl": "https://hpb.test/" + "x" * 600}, "bad_url"),
    ],
)
def test_invalid_input_is_refused_and_nothing_is_written(tmp_path, data, code):
    setup = TalkSetup("", "", tmp_path / "talk.json")
    refused(setup, data, 400, code)
    assert not (tmp_path / "talk.json").exists()


@pytest.mark.parametrize("content", [b"{broken", b"[]", b'{"secret": 1}'])
def test_an_unreadable_file_is_reported_and_never_overwritten(tmp_path, content):
    path = tmp_path / "talk.json"
    path.write_bytes(content)
    setup = TalkSetup("", "", path)
    assert setup.secret() == ("", UNSET)
    assert setup.status()["unreadable"] is True
    refused(setup, {"secret": "s3cret"}, 409, "unreadable")
    assert path.read_bytes() == content


def test_without_storage_nothing_can_be_saved():
    refused(TalkSetup(), {"secret": "s3cret"}, 409, "storage")


def test_status_never_contains_the_secret(tmp_path):
    setup = TalkSetup("", "", tmp_path / "talk.json")
    setup.save({"secret": "s3cret-value"})
    status = setup.status()
    assert "s3cret-value" not in json.dumps(status)
    assert status["secretConfigured"] is True and status["secretSource"] == SAVED


# Discovery and the HPB check


async def test_discovery_uses_the_saved_secret_and_url(tmp_path):
    setup = TalkSetup("", "", tmp_path / "talk.json")
    setup.save({"secret": "s3cret", "hpbUrl": "http://hpb.test:8080"})
    service = CaptionService(Config(None, 1, "", ""), talk=setup)
    nc = AsyncMock()
    nc.ocs.return_value = dict(TALK_SETTINGS)
    await service.discover(nc)
    assert service.settings.server == "ws://hpb.test:8080/spreed"
    assert service.settings.secret == "s3cret"
    assert "s3cret" not in json.dumps(service.settings.model_dump())
    assert "s3cret" not in repr(service.settings)


async def test_discovery_without_a_secret_names_the_settings_page():
    service = CaptionService(Config(None, 1, "", ""))
    with pytest.raises(ValueError, match="settings page"):
        await service.discover(AsyncMock())


class FakeSocket:
    def __init__(self, replies):
        self.replies = [json.dumps(reply) for reply in replies]
        self.sent = []

    async def recv(self):
        return self.replies.pop(0)

    async def send(self, message):
        self.sent.append(json.loads(message))


class HelloClient:
    def __init__(self, *_args):
        self._server = None

    async def send_hello(self):
        await self._server.send(json.dumps({"type": "hello"}))


def connector_for(socket=None, error=None):
    @asynccontextmanager
    async def connect(url, **_kwargs):
        if error:
            raise error
        yield socket

    return connect


def checked_service(connector):
    service = CaptionService(Config(None, 1, "s3cret", ""), HelloClient, connector=connector)
    service.settings = HPBSettings(**TALK_SETTINGS, secret="s3cret")
    service.settings.server = "ws://hpb.test/spreed"
    return service


@pytest.mark.parametrize(
    ("replies", "state", "words"),
    [
        ([{"type": "welcome"}, {"type": "hello", "hello": {}}], "passed", "accepted"),
        ([{"type": "error", "error": {"code": "invalid_token"}}], "failed", "internalsecret"),
        ([{"type": "error", "error": {"code": "invalid_client_type"}}], "failed", "internal"),
    ],
)
async def test_hpb_check_outcomes(replies, state, words):
    socket = FakeSocket(replies)
    service = checked_service(connector_for(socket))
    check = await service.check_hpb()
    assert check.state == state and words in check.message
    assert service.talk.check is check
    if state == "passed":
        assert socket.sent[-1]["type"] == "bye"


async def test_hpb_check_reports_an_unreachable_server():
    service = checked_service(connector_for(error=OSError("refused")))
    check = await service.check_hpb()
    assert check.state == "unreachable" and "ws://hpb.test/spreed" in check.message


async def test_hpb_check_without_talk_settings():
    service = CaptionService(Config(None, 1, "s3cret", ""))
    assert (await service.check_hpb()).state == "unreachable"


# The settings page API


@pytest.fixture
def talk_page(headers, tmp_path):
    nc = AsyncMock()
    nc.ocs.return_value = dict(TALK_SETTINGS)
    service = CaptionService(Config(None, 2, "", ""), talk=TalkSetup("", "", tmp_path / "t.json"))
    service.request = AsyncMock()
    service.check_hpb = AsyncMock()
    app = create_app(
        Config(None, 2, "", ""), WorkerPool(2), service, nc_factory=lambda: nc, discovery_delay=60
    )
    app.state.manager.start_safety = AsyncMock()
    return app, service, TestClient(app), headers


def test_state_shows_the_talk_setup_without_the_secret(talk_page):
    _, _, client, headers = talk_page
    talk = client.get("/settings/api/state", headers=headers).json()["talk"]
    assert talk["secretConfigured"] is False and talk["secretSource"] == UNSET
    assert talk["hpbUrlSource"] == DISCOVERED and talk["storage"] is True


def test_saving_the_secret_reconnects_and_checks(talk_page):
    app, service, client, headers = talk_page
    response = client.post(
        "/settings/api/talk",
        headers=same_origin(headers),
        json={"secret": "s3cret", "hpbUrl": "http://hpb.test:8080"},
    )
    assert response.status_code == 200, response.text
    assert_secure(response)
    body = response.json()
    assert "s3cret" not in response.text
    assert body["talk"]["secretSource"] == SAVED and body["talk"]["hpbUrlSource"] == SAVED
    assert service.enabled and service.settings.server == "ws://hpb.test:8080/spreed"
    service.check_hpb.assert_awaited_once()
    # Started in the background: loading an engine must not hold the request.
    app.state.manager.start_safety.assert_called_once()


def test_an_unchanged_save_does_not_reconnect(talk_page):
    _, service, client, headers = talk_page
    client.post("/settings/api/talk", headers=same_origin(headers), json={"secret": "s3cret"})
    service.check_hpb.reset_mock()
    client.post("/settings/api/talk", headers=same_origin(headers), json={"secret": "s3cret"})
    service.check_hpb.assert_not_awaited()


def test_check_again(talk_page):
    _, service, client, headers = talk_page
    client.post("/settings/api/talk", headers=same_origin(headers), json={"secret": "s3cret"})
    service.check_hpb.reset_mock()
    response = client.post("/settings/api/talk/check", headers=same_origin(headers), json={})
    assert response.status_code == 200
    service.check_hpb.assert_awaited_once()


def test_talk_setup_refusals_use_the_envelope(talk_page):
    _, _, client, headers = talk_page
    error(client.post("/settings/api/talk", headers=same_origin(headers), json={}), 400,
          "bad_request")
    error(client.post("/settings/api/talk", headers=same_origin(headers),
                      json={"secret": "a b"}), 400, "bad_secret")


def test_talk_setup_needs_the_csrf_defense(talk_page):
    _, _, client, headers = talk_page
    error(client.post("/settings/api/talk", headers=headers, json={"secret": "s3cret"}), 403,
          "csrf")


def test_a_deployed_secret_cannot_be_replaced_from_the_page(headers, tmp_path):
    config = Config(None, 2, "deployed", "")
    service = CaptionService(config, talk=TalkSetup.from_config(config, tmp_path / "t.json"))
    service.check_hpb = AsyncMock()
    client = TestClient(create_app(config, WorkerPool(2), service))
    talk = client.get("/settings/api/state", headers=headers).json()["talk"]
    assert talk["secretSource"] == DEPLOY
    body = error(
        client.post("/settings/api/talk", headers=same_origin(headers), json={"secret": "x"}),
        409,
        "deploy_options",
    )
    assert "deployed" not in json.dumps(body)
