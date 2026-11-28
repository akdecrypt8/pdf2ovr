from __future__ import annotations

import json
from pathlib import Path

import pytest

from pdf2ovr.cli import main
from pdf2ovr.eant_reader import EANTDocument
from tests.fixture_utils import classic_fixture


def test_inspect_json_includes_cloneable_annotation_offsets(capsys):
    path = classic_fixture()
    with pytest.raises(SystemExit) as result:
        main(["inspect", str(path), "--json"])
    assert result.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["hfa"]["entry_count"] == 4
    assert len(payload["annotations"]) == 1
    assert payload["annotations"][0]["entry_offset"] == 338


def test_clone_cli_writes_and_reopens_output(tmp_path: Path, capsys):
    path = classic_fixture()
    source = EANTDocument(path)
    item = source.annotations[0]
    output = tmp_path / "clone.ovr"
    with pytest.raises(SystemExit) as result:
        main([
            "clone", str(path), "--entry-offset", str(item.entry_offset),
            "--name", "CLI regression clone", "--output", str(output),
        ])
    assert result.value.code == 0
    assert output.is_file()
    cloned = EANTDocument(output)
    assert cloned.summary()["recursive_annotation_count"] == 2
    assert any(a.name == "CLI regression clone" for a in cloned.iter_annotations())
    assert cloned.decode_errors == []
    assert "Created:" in capsys.readouterr().out


def test_clone_cli_does_not_overwrite_destination_without_force(tmp_path: Path, capsys):
    path = classic_fixture()
    item = EANTDocument(path).annotations[0]
    output = tmp_path / "existing.ovr"
    sentinel = b"keep-existing-file"
    output.write_bytes(sentinel)
    with pytest.raises(SystemExit) as result:
        main([
            "clone", str(path), "--entry-offset", str(item.entry_offset),
            "--name", "Must not overwrite", "--output", str(output),
        ])
    assert result.value.code == 2
    assert output.read_bytes() == sentinel
    assert "Destination already exists" in capsys.readouterr().err
