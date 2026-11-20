"""Container detail responses must never expose environment variable values.

DSM's `SYNO.Docker.Container get` returns `details.Config.Env` (NAME=value
strings) and `profile.env_variables` ({key, value} objects). Both carried
database passwords and API tokens into the model context before this test's
subject existed; names stay, values go.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from container.synology_container import SynologyContainer  # noqa: E402


def fixture_response():
    return {
        "success": True,
        "data": {
            "name": "web",
            "status": "running",
            "details": {
                "Config": {
                    "Image": "nginx:latest",
                    "Env": [
                        "DB_PASSWORD=supersecret",
                        "API_TOKEN=tok_live_123",
                        "TZ=Asia/Singapore",
                    ],
                }
            },
            "profile": {
                "env_variables": [
                    {"key": "MYSQL_ROOT_PASSWORD", "value": "rootsecret"},
                    {"key": "TZ", "value": "Asia/Singapore"},
                ]
            },
            "password": "profile-secret",
            "some": {"credential": "x", "nested": {"secret": "y", "keep": 1}},
        },
    }


class TestContainerGet:
    def make_client(self):
        return SynologyContainer("https://nas.example.com:5001", "SID")

    def test_values_are_dropped_and_names_kept(self, monkeypatch):
        client = self.make_client()
        monkeypatch.setattr(client, "_container_name_request", lambda method, name: fixture_response())
        result = client.get_container("web")

        blob = json.dumps(result)
        for secret in ("supersecret", "tok_live_123", "rootsecret", "profile-secret"):
            assert secret not in blob, secret

        assert result["data"]["details"]["Config"]["env_keys"] == [
            "DB_PASSWORD",
            "API_TOKEN",
            "TZ",
        ]
        assert result["data"]["profile"]["env_keys"] == ["MYSQL_ROOT_PASSWORD", "TZ"]

    def test_credential_keys_are_dropped_at_any_depth(self):
        clean = SynologyContainer._sanitize_details(fixture_response())
        data = clean["data"]
        assert "password" not in data
        assert "credential" not in data["some"]
        assert "secret" not in data["some"]["nested"]
        assert data["some"]["nested"]["keep"] == 1

    def test_failed_response_passes_through(self, monkeypatch):
        client = self.make_client()
        failure = {"success": False, "error": {"code": 1, "message": "no such container"}}
        monkeypatch.setattr(client, "_container_name_request", lambda method, name: failure)
        assert client.get_container("missing") == failure


class TestProjectSanitization:
    def test_env_variables_are_not_smuggled_through(self):
        fixture = {
            "success": True,
            "data": {"name": "stack", "profile": {"env_variables": [{"key": "TOKEN", "value": "tok-secret-value"}]}},
        }
        clean = SynologyContainer._sanitize_details(fixture)
        assert "tok-secret-value" not in json.dumps(clean)
        assert clean["data"]["profile"]["env_keys"] == ["TOKEN"]
