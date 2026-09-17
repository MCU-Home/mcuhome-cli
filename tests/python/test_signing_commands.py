# SPDX-FileCopyrightText: 2026 The MCUHome Contributors
# SPDX-License-Identifier: Apache-2.0
"""``mcuhome signing``, in all three modes.

Real keys against real projects: drawing a P-256 key pair costs
milliseconds, and a mock of it would prove nothing about the one
property these two commands exist to keep — that the private half is in
no document and in no line either of them prints, whichever way it is
asked for.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from mcuhome.workbench import api

from mcuhome.cli.main import main


def _document(capsys: pytest.CaptureFixture[str]) -> dict:
    return json.loads(capsys.readouterr().out)


def _stream(capsys: pytest.CaptureFixture[str]) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


@pytest.fixture
def keyed(in_project: Path) -> Path:
    """A project whose signing key is drawn."""
    api.create_signing_key(env=dict(os.environ), project=api.read_project(in_project))
    return in_project


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    """A P-256 key outside any project, as a person's own file."""
    path = tmp_path / "own-key.pem"
    path.write_text(api.generate_key_pem(), encoding="utf-8")
    path.chmod(0o600)
    return path


def _private(project: Path) -> str:
    return (project / "secrets" / "signing" / "key.pem").read_text(encoding="utf-8")


class TestSigningPrintPublicKey:
    def test_it_answers_where_the_key_is_and_its_public_half(
        self, keyed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["signing", "print-public-key", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "path", "in_secrets", "created", "public_key"]
        assert document["ok"] is True
        assert document["path"] == str(keyed / "secrets" / "signing" / "key.pem")
        assert document["in_secrets"] is True
        # It never writes, so nothing it answers was created by it.
        assert document["created"] is False
        assert document["public_key"].startswith("-----BEGIN PUBLIC KEY-----")

    def test_a_person_reads_the_pem_alone(
        self, keyed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # So that `mcuhome signing print-public-key > key.pub` is a key file.
        assert main(["signing", "print-public-key"]) == 0
        printed = capsys.readouterr().out
        assert printed == api.public_key_pem(_private(keyed))

    def test_the_private_half_is_in_no_document_and_in_no_line(
        self, keyed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        private = _private(keyed)
        for mode in ("human", "json", "json-stream"):
            assert main(["signing", "print-public-key", "-o", mode]) == 0
            printed = capsys.readouterr()
            whole = printed.out + printed.err
            assert "PRIVATE KEY" not in whole
            assert private.splitlines()[1] not in whole

    def test_a_project_with_no_key_is_refused_naming_the_command_that_draws_one(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["signing", "print-public-key", "-o", "json"]) == 1
        document = _document(capsys)
        assert set(document) == {"ok", "errors"}
        assert "mcuhome signing create-key" in document["errors"][0]["hint"]
        # Reading is not the moment to draw one.
        assert not (in_project / "secrets" / "signing" / "key.pem").exists()

    def test_it_reads_the_key_the_flag_names(
        self, in_project: Path, key_file: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        code = main(["signing", "print-public-key", "--signing-key", str(key_file), "-o", "json"])
        assert code == 0
        document = _document(capsys)
        assert document["path"] == str(key_file)
        assert document["in_secrets"] is False
        assert document["public_key"] == api.public_key_pem(key_file.read_text(encoding="utf-8"))

    def test_the_stream_starts_and_ends(
        self, keyed: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["signing", "print-public-key", "-o", "json-stream"]) == 0
        messages = _stream(capsys)
        assert [message["verb"] for message in messages] == ["start", "result"]
        assert messages[0] == {"verb": "start", "task": "signing print-public-key"}


class TestSigningCreateKey:
    def test_it_draws_the_key_and_says_that_it_did(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["signing", "create-key", "-o", "json"]) == 0
        document = _document(capsys)
        assert list(document) == ["ok", "path", "in_secrets", "created", "public_key"]
        assert document["created"] is True
        assert document["in_secrets"] is True
        assert Path(document["path"]) == in_project / "secrets" / "signing" / "key.pem"
        assert Path(document["path"]).is_file()

    def test_asked_again_it_answers_the_key_that_is_there(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # Generating over existing key material is the one thing this
        # must never do: a device stops accepting images signed with the
        # key it does not carry.
        assert main(["signing", "create-key", "-o", "json"]) == 0
        first = _document(capsys)
        drawn = _private(in_project)
        assert main(["signing", "create-key", "-o", "json"]) == 0
        second = _document(capsys)
        assert second["created"] is False
        assert second["public_key"] == first["public_key"]
        assert _private(in_project) == drawn

    def test_the_private_half_is_in_no_document_and_in_no_line(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["signing", "create-key"]) == 0
        drawn = capsys.readouterr()
        private = _private(in_project)
        assert "PRIVATE KEY" not in drawn.out + drawn.err
        assert private.splitlines()[1] not in drawn.out + drawn.err
        for mode in ("json", "json-stream"):
            assert main(["signing", "create-key", "-o", mode]) == 0
            printed = capsys.readouterr()
            assert private.splitlines()[1] not in printed.out + printed.err

    def test_it_writes_where_the_flag_says(
        self, in_project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        target = tmp_path / "elsewhere" / "key.pem"
        assert main(["signing", "create-key", "--signing-key", str(target), "-o", "json"]) == 0
        document = _document(capsys)
        assert document["created"] is True
        assert document["in_secrets"] is False
        assert Path(document["path"]) == target
        assert target.is_file()
        # The project's own key is not drawn beside it.
        assert not (in_project / "secrets" / "signing" / "key.pem").exists()

    def test_a_person_reads_that_it_was_drawn_and_where(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["signing", "create-key"]) == 0
        printed = capsys.readouterr().out
        assert str(in_project / "secrets" / "signing" / "key.pem") in printed
        assert "mcuhome signing print-public-key" in printed

    def test_the_stream_ends_with_one_result(
        self, in_project: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["signing", "create-key", "-o", "json-stream"]) == 0
        messages = _stream(capsys)
        assert [message["verb"] for message in messages] == ["start", "result"]
        assert messages[-1]["document"]["created"] is True
