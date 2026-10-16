"""Plugin cards credit the upstream author: fm carries author, author_url,
homepage and repository, http(s) only, and never the author's email."""
from __future__ import annotations

import json
from types import SimpleNamespace

from index_builder import assemble
from index_builder.assemble import _attribution_fm


def test_object_author_emits_name_and_url():
    fm = _attribution_fm({
        "author": {"name": " Ada ", "email": "a@x.io", "url": "https://ada.dev"},
        "homepage": "https://h.dev",
        "repository": {"type": "git", "url": "https://github.com/a/b"},
    })
    assert fm == {"author": "Ada", "author_url": "https://ada.dev",
                  "homepage": "https://h.dev",
                  "repository": "https://github.com/a/b"}


def test_string_author_has_null_author_url():
    fm = _attribution_fm({"author": "Ada", "repository": "https://github.com/a/b"})
    assert fm["author"] == "Ada" and fm["author_url"] is None
    assert fm["repository"] == "https://github.com/a/b"


def test_non_http_urls_become_null():
    fm = _attribution_fm({
        "author": {"name": "Ada", "url": "javascript:alert(1)"},
        "homepage": "data:text/html,x",
        "repository": {"url": "git@github.com:a/b.git"},
    })
    assert fm["author_url"] is None and fm["homepage"] is None
    assert fm["repository"] is None


def test_absent_fields_are_null():
    assert _attribution_fm({}) == {"author": None, "author_url": None,
                                   "homepage": None, "repository": None}


def test_plugin_item_never_emits_email_and_theme_is_unchanged(monkeypatch):
    manifest = {"description": "d", "author": {"name": "Ada", "email": "a@x.io"}}
    monkeypatch.setattr(assemble, "_git", lambda *a, **k: json.dumps(manifest))
    monkeypatch.setattr(assemble, "_version_entry", lambda *a, **k: {})
    rel = SimpleNamespace(commit="c", version="1.0.0")
    plugin = assemble._manifest_item(None, assemble.KIND_PLUGIN, "h", "n", [rel], "r")
    assert plugin["fm"]["author"] == "Ada"
    assert "author_email" not in plugin["fm"] and "email" not in plugin["fm"]
    theme = assemble._manifest_item(None, assemble.KIND_THEME, "h", "n", [rel], "r")
    assert theme["fm"] == {"description": "d"}
