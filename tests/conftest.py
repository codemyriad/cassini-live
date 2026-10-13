import base64

import pytest


@pytest.fixture(autouse=True)
def app_environment(monkeypatch):
    for key, value in {
        "APP_ID": "live_transcription",
        "APP_VERSION": "0.1.0",
        "APP_SECRET": "test-exapp-secret",
        "NEXTCLOUD_URL": "http://cloud.test",
        "AA_VERSION": "32.0.0",
        "CASSINI_TALK_SIGNALING_INTERNAL_SECRET": "test-hpb",
        "CASSINI_CAPACITY_CHECK": "0",
    }.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def headers():
    return {
        "EX-APP-ID": "live_transcription",
        "EX-APP-VERSION": "0.1.0",
        "AUTHORIZATION-APP-API": base64.b64encode(b"alice:test-exapp-secret").decode(),
    }
