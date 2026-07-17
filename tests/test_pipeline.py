from __future__ import annotations

from pathlib import Path

import pytest

from rpg_translator.core.pipeline import UnknownEngineError, detect_adapter


def test_detect_adapter_picks_mz(mz_project: Path):
    adapter = detect_adapter(mz_project)
    assert adapter.engine_name == "mz"


def test_detect_adapter_picks_mv(mv_project: Path):
    adapter = detect_adapter(mv_project)
    assert adapter.engine_name == "mv"


def test_detect_adapter_picks_vxace(vxace_project: Path):
    adapter = detect_adapter(vxace_project)
    assert adapter.engine_name == "vxace"


def test_detect_adapter_raises_on_unrecognized_dir(tmp_path: Path):
    with pytest.raises(UnknownEngineError):
        detect_adapter(tmp_path)
