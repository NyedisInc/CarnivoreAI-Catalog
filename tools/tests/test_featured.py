"""catalog.yml ``featured``: validated, written to the index, refused when it names nothing."""

from __future__ import annotations

from pathlib import Path

import pytest

from index_builder.assemble import assemble
from index_builder.config import load_config
from index_builder.publishers import load_publishers
from index_builder.releases import ReleaseRefused, find_release_files, verify_release

from tests.conftest import requires_minisign
from tests.test_builder import CATALOG_YML, SLUG, catalog  # noqa: F401  (fixture)


def _build(root: Path, featured):
    """Assemble the repository's one release with the given featured list."""
    publishers = load_publishers(root)
    releases = [
        verify_release(root, p, publishers=publishers, repo_slug=SLUG)
        for p in find_release_files(root)
    ]
    return assemble(root, publishers=publishers, releases=releases, repo_slug=SLUG,
                    serial=1, generated_at="2026-09-15T00:00:00Z", featured=featured)


def _config(tmp_path: Path, extra: str):
    """load_config over CATALOG_YML plus ``extra`` lines."""
    (tmp_path / "catalog.yml").write_text(CATALOG_YML + extra, encoding="utf-8")
    return load_config(tmp_path)


def test_featured_is_optional_and_validated(tmp_path: Path) -> None:
    """Absent means empty; a list of unique strings loads; anything else raises."""
    assert _config(tmp_path, "").featured == ()
    assert _config(tmp_path, "featured:\n  - a/b\n  - c/d\n").featured == ("a/b", "c/d")
    for bad in ("featured: a/b\n", "featured:\n  - a/b\n  - a/b\n", "featured:\n  - 3\n"):
        with pytest.raises(ValueError, match="featured"):
            _config(tmp_path, bad)


@requires_minisign
def test_the_index_carries_featured_and_refuses_an_unknown_id(catalog: dict) -> None:  # noqa: F811
    """The document lists featured ids (empty when absent); an id with no item fails the build."""
    root = catalog["root"]
    assert _build(root, ()).document["featured"] == []
    assert _build(root, ("nyedis/work",)).document["featured"] == ["nyedis/work"]
    with pytest.raises(ReleaseRefused, match="nyedis/nope"):
        _build(root, ("nyedis/nope",))
