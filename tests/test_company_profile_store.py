from pathlib import Path

import pytest

from app.intake.profile_store import seed_products


def test_deploy_preserves_approved_profile_and_refreshes_packaged_assets(tmp_path):
    source, runtime = tmp_path / "image", tmp_path / "runtime"
    (source / "servgo").mkdir(parents=True)
    canonical = source / "servgo/company-profile.json"
    canonical.write_text("seed")
    (source / "servgo/product.md").write_text("old")
    seed_products(source, runtime)
    approved = runtime / "servgo/company-profile.json"
    approved.write_text("approved by owner")
    canonical.write_text("new image seed")
    (source / "servgo/product.md").write_text("updated")
    seed_products(source, runtime)
    assert approved.read_text() == "approved by owner"
    assert (runtime / "servgo/product.md").read_text() == "updated"


def test_seed_rejects_runtime_symlink_without_modifying_outside(tmp_path):
    source, runtime, outside = [tmp_path / name for name in ("image", "runtime", "outside")]
    (source / "servgo").mkdir(parents=True)
    (source / "servgo/company-profile.json").write_text("seed")
    runtime.mkdir()
    outside.mkdir()
    (runtime / "servgo").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError):
        seed_products(source, runtime)
    assert list(outside.iterdir()) == []
