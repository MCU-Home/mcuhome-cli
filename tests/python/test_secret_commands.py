# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome secret``, in all three modes.

Every test drives ``main([...])`` against a real project and lets the
workbench read and write the real files: a secrets file is a few lines
of YAML, and a mock of that would be a test of the mock. What is stubbed
is the one thing a person supplies and a test cannot — what standard
input carries.

Two of these tests are about what must **never** be printed: the mask a
listing shows instead of a value, and the firmware key, which no
document of this command line carries whichever way it is asked for.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest
from conftest import BOARD
from mcuhome.workbench import api

from mcuhome.cli.main import main


def _document(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


def _stream(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


@pytest.fixture
def secrets(in_project: Path) -> Path:
    """A project with one shared secret and one device that has its own."""
    project = api.read_project(in_project)
    api.create_device("kitchen", project=project, board=BOARD)
    api.set_secret(project, kind="main", key="wifi_password", value="hunter2")
    api.set_secret(project, kind="device", name="kitchen", key="token", value="device-token")
    return in_project


@pytest.fixture
def keyed(in_project: Path) -> Path:
    """A project whose signing key is drawn."""
    api.create_signing_key(env=dict(os.environ), project=api.read_project(in_project))
    return in_project


class TestSecretListScopes:
    def test_it_answers_every_scope_the_project_could_have(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["secret", "list-scopes", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "scopes"]
        assert document["ok"] is True
        assert [(scope["kind"], scope["name"]) for scope in document["scopes"]] == [
            ("main", ""),
            ("device", "kitchen"),
            ("signing", ""),
        ]
        for scope in document["scopes"]:
            assert list(scope) == ["kind", "name", "file", "exists"]
        assert document["scopes"][0]["exists"] is True
        # The device has a file, the signing key has not been drawn.
        assert document["scopes"][1]["exists"] is True
        assert document["scopes"][2]["exists"] is False

    def test_a_listing_is_positive_whatever_its_rows_say(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A project with no secrets at all still has the two scopes, both
        # with no file: the verdict is the listing's, not the rows'.
        assert main(["secret", "list-scopes", "-o", "json"]) == 0
        document = _document(capsys)
        assert document["ok"] is True
        assert [scope["exists"] for scope in document["scopes"]] == [False, False]

    def test_a_person_reads_the_file_of_every_scope(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["secret", "list-scopes"]) == 0
        printed = capsys.readouterr().out
        assert str(secrets / "secrets" / "main.yaml") in printed
        # The place is printed for a scope that has no file yet as well.
        assert str(secrets / "secrets" / "signing" / "key.yaml") in printed

    def test_the_stream_starts_and_ends(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["secret", "list-scopes", "-o", "json-stream"]) == 0
        messages = _stream(capsys)
        assert [message["verb"] for message in messages] == ["start", "result"]
        assert messages[0] == {"verb": "start", "task": "secret list-scopes"}
        assert messages[-1]["document"]["ok"] is True


class TestSecretList:
    def test_it_answers_the_scope_and_the_masked_keys(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["secret", "list", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "scope", "keys"]
        assert document["scope"]["kind"] == "main"
        assert [key["key"] for key in document["keys"]] == ["wifi_password"]
        assert list(document["keys"][0]) == ["key", "masked", "used_by"]
        assert "hunter2" not in json.dumps(document)

    def test_a_scope_with_no_file_answers_no_keys_rather_than_refusing(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # A client has to be able to open a scope it just listed.
        assert main(["secret", "list", "--kind", "signing", "-o", "json"]) == 0
        document = _document(capsys)
        assert document["scope"]["exists"] is False
        assert document["keys"] == []

    def test_the_default_kind_is_the_shared_file(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["secret", "list", "--kind", "device", "--name", "kitchen", "-o", "json"]) == 0
        named = _document(capsys)
        assert main(["secret", "list", "-o", "json"]) == 0
        assert _document(capsys)["scope"]["kind"] == "main"
        assert named["scope"]["kind"] == "device"
        assert [key["key"] for key in named["keys"]] == ["token"]

    def test_a_person_reads_the_mask_and_never_the_value(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["secret", "list"]) == 0
        printed = capsys.readouterr().out
        assert "wifi_password" in printed
        assert "hunter2" not in printed


class TestSecretReveal:
    def test_it_answers_the_value_under_one_key(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["secret", "reveal", "--key", "wifi_password", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "scope", "key", "value"]
        assert document["scope"]["kind"] == "main"
        assert document["key"] == "wifi_password"
        assert document["value"] == "hunter2"

    def test_a_person_reads_the_value_alone(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # So that `password="$(mcuhome secret reveal …)"` is the value.
        assert main(["secret", "reveal", "--key", "wifi_password"]) == 0
        assert capsys.readouterr().out == "hunter2\n"

    def test_it_reads_the_scope_it_was_asked_about(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert (
            main(
                [
                    "secret",
                    "reveal",
                    "--kind",
                    "device",
                    "--name",
                    "kitchen",
                    "--key",
                    "token",
                    "-o",
                    "json",
                ]
            )
            == 0
        )
        document = _document(capsys)
        assert document["scope"]["kind"] == "device"
        assert document["scope"]["name"] == "kitchen"
        assert document["value"] == "device-token"

    def test_the_signing_key_is_refused_rather_than_printed(
        self, keyed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The one thing this command must never do, whatever it is asked.
        code = main(["secret", "reveal", "--kind", "signing", "--key", "firmware_signing_key"])
        assert code == 1
        printed = capsys.readouterr()
        private = (keyed / "secrets" / "signing" / "key.pem").read_text(encoding="utf-8")
        assert "PRIVATE KEY" not in printed.out + printed.err
        assert private.splitlines()[1] not in printed.out + printed.err
        assert "does not print what is in it" in printed.err

    def test_a_key_the_file_does_not_hold_is_refused(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["secret", "reveal", "--key", "nothing", "-o", "json"]) == 1
        document = _document(capsys)
        assert set(document) == {"ok", "errors"}
        assert document["errors"][0]["kind"] == "ConfigError"


class TestSecretSet:
    def test_it_writes_the_key_and_answers_the_change(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["secret", "set", "--key", "api_token", "--value", "t0ken", "-o", "json"])
        assert code == 0
        document = _document(capsys)
        assert list(document) == ["ok", "scope", "key", "changed"]
        assert document["changed"] is True
        assert document["scope"]["file"] == str(secrets / "secrets" / "main.yaml")
        assert "api_token: t0ken" in (secrets / "secrets" / "main.yaml").read_text(encoding="utf-8")

    def test_the_value_is_in_no_document_and_in_no_line(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["secret", "set", "--key", "api_token", "--value", "t0ken"]) == 0
        printed = capsys.readouterr()
        assert "t0ken" not in printed.out + printed.err

    def test_a_value_that_is_already_there_changes_nothing(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["secret", "set", "--key", "wifi_password", "--value", "hunter2", "-o", "json"])
        assert code == 0
        assert _document(capsys)["changed"] is False

    def test_it_writes_the_scope_it_was_asked_about(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(
            [
                "secret",
                "set",
                "--kind",
                "device",
                "--name",
                "kitchen",
                "--key",
                "token",
                "--value",
                "other",
                "-o",
                "json",
            ]
        )
        assert code == 0
        document = _document(capsys)
        assert document["scope"]["kind"] == "device"
        assert document["changed"] is True
        assert (
            api.reveal_secret(api.read_project(secrets), kind="device", name="kitchen", key="token")
            == "other"
        )

    def test_the_value_is_read_from_standard_input(
        self, secrets: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # The reason the flag takes `-`: a secret in a shell's history is
        # a secret every later shell of that user carries.
        monkeypatch.setattr("sys.stdin", io.StringIO("piped-secret\n"))
        assert main(["secret", "set", "--key", "api_token", "--value", "-", "-o", "json"]) == 0
        assert _document(capsys)["changed"] is True
        assert (
            api.reveal_secret(api.read_project(secrets), kind="main", key="api_token")
            == "piped-secret"
        )

    def test_nothing_piped_in_is_a_wrong_invocation_rather_than_a_wait(
        self, secrets: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        class Terminal(io.StringIO):
            def isatty(self) -> bool:
                return True

        monkeypatch.setattr("sys.stdin", Terminal())
        assert main(["secret", "set", "--key", "api_token", "--value", "-", "-o", "json"]) == 2
        assert _document(capsys)["errors"][0]["kind"] == "UsageError"
        assert "api_token" not in (secrets / "secrets" / "main.yaml").read_text(encoding="utf-8")

    def test_an_empty_read_is_not_an_empty_secret(
        self, secrets: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr("sys.stdin", io.StringIO("\n"))
        assert main(["secret", "set", "--key", "api_token", "--value", "-", "-o", "json"]) == 2
        assert _document(capsys)["errors"][0]["kind"] == "UsageError"

    def test_the_signing_scope_is_refused_naming_the_two_ways_a_key_is_stated(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(
            ["secret", "set", "--kind", "signing", "--key", "firmware_signing_key", "--value", "x"]
        )
        assert code == 1
        printed = capsys.readouterr().err
        assert "mcuhome signing create-key" in printed
        assert "--signing-key" in printed


class TestSecretUnset:
    def test_it_removes_the_key(self, secrets: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["secret", "unset", "--key", "wifi_password", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "scope", "key", "changed"]
        assert document["changed"] is True
        assert api.read_secrets(api.read_project(secrets), kind="main").keys == ()

    def test_a_key_that_was_not_there_changed_nothing(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["secret", "unset", "--key", "nothing", "-o", "json"]) == 0
        assert _document(capsys)["changed"] is False

    def test_the_last_entry_leaves_the_file(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["secret", "unset", "--key", "wifi_password"]) == 0
        assert (secrets / "secrets" / "main.yaml").is_file()


class TestSecretDelete:
    def test_it_removes_a_whole_file(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["secret", "delete", "--kind", "device", "--name", "kitchen", "-o", "json"])
        assert code == 0
        document = _document(capsys)
        assert list(document) == ["ok", "scope", "key", "changed"]
        # The subject was the file, so there is no entry to name.
        assert document["key"] == ""
        assert document["changed"] is True
        assert document["scope"]["exists"] is False
        assert not (secrets / "secrets" / "device" / "kitchen.yaml").exists()

    def test_a_file_that_is_not_there_changed_nothing(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        api.delete_secret_file(api.read_project(secrets), kind="device", name="kitchen")
        code = main(["secret", "delete", "--kind", "device", "--name", "kitchen", "-o", "json"])
        assert code == 0
        assert _document(capsys)["changed"] is False

    def test_the_projects_own_files_are_refused(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["secret", "delete", "--kind", "main", "--name", "x", "-o", "json"]) == 1
        assert set(_document(capsys)) == {"ok", "errors"}
        assert (secrets / "secrets" / "main.yaml").is_file()

    def test_the_stream_ends_with_one_result(
        self, secrets: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(
            ["secret", "delete", "--kind", "device", "--name", "kitchen", "-o", "json-stream"]
        )
        assert code == 0
        messages = _stream(capsys)
        assert [message["verb"] for message in messages] == ["start", "result"]
        assert messages[-1]["document"]["changed"] is True
