from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from codemerge.jsui.path_policy import (
    OUTPUT_MARKER,
    PathConflict,
    PathDenied,
    PathGrant,
    PathGrantKind,
    PathGrantPurpose,
    PathInvalid,
    PathPolicy,
)


def test_input_file_accepts_relative_under_root(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    target = root / "src" / "app.py"
    target.parent.mkdir()
    target.write_text("print('ok')\n", encoding="utf-8")

    resolved = PathPolicy(root).resolve_input_file("src/app.py")

    assert resolved.path == target.resolve()
    assert resolved.inside_root is True
    assert resolved.external is False
    assert resolved.rel == "src/app.py"


def test_input_file_rejects_external_absolute_path(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")

    with pytest.raises(PathDenied):
        PathPolicy(root).resolve_input_file(str(outside))


def test_input_file_rejects_parent_traversal_after_resolution(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")

    with pytest.raises(PathDenied):
        PathPolicy(root).resolve_input_file("../outside.py")


def test_input_file_rejects_symlink_that_points_outside_root(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    link = root / "linked.py"
    link.symlink_to(outside)

    with pytest.raises(PathDenied):
        PathPolicy(root).resolve_input_file("linked.py")


def test_input_file_rejects_missing_file(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    with pytest.raises(PathInvalid):
        PathPolicy(root).resolve_input_file("missing.py")


def test_output_file_defaults_under_root(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    resolved = PathPolicy(root).resolve_output_file(None, default_name="merged.txt")

    assert resolved.path == (root / "merged.txt").resolve()
    assert resolved.inside_root is True
    assert resolved.existed_before is False


def test_output_file_rejects_external_by_default(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "out.txt"

    with pytest.raises(PathDenied):
        PathPolicy(root).resolve_output_file(str(outside), default_name="merged.txt")


def test_output_file_allows_external_when_explicit(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "out.txt"

    resolved = PathPolicy(root, allow_external_output=True).resolve_output_file(
        str(outside),
        default_name="merged.txt",
    )

    assert resolved.path == outside.resolve()
    assert resolved.inside_root is False
    assert resolved.external is True


def test_output_folder_overwrite_requires_generated_marker(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    folder = root / "chunks"
    folder.mkdir()
    policy = PathPolicy(root)
    resolved = policy.resolve_output_folder("chunks", default_name="chunks")

    with pytest.raises(PathConflict):
        policy.assert_safe_overwrite_folder(resolved)

    (folder / OUTPUT_MARKER).write_text("", encoding="utf-8")
    policy.assert_safe_overwrite_folder(policy.resolve_output_folder("chunks", default_name="chunks"))


def test_output_folder_overwrite_rejects_project_root(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    policy = PathPolicy(root)

    with pytest.raises(PathConflict):
        policy.assert_safe_overwrite_folder(policy.resolve_output_folder(str(root), default_name="chunks"))


def test_generated_folder_grant_does_not_grant_sibling_download(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    generated = root / "out"
    generated.mkdir()
    output = generated / "result.txt"
    output.write_text("result\n", encoding="utf-8")
    sibling = root / "sibling.txt"
    sibling.write_text("source\n", encoding="utf-8")
    policy = PathPolicy(root, generated_outputs=frozenset({generated}))

    assert policy.can_download(output) is True
    assert policy.can_open(output) is True
    assert policy.can_open(sibling) is False


def test_explicit_user_path_allows_open_but_not_external_download(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    selected = root / "src.py"
    selected.write_text("print('selected')\n", encoding="utf-8")
    policy = PathPolicy(root, explicit_user_paths=frozenset({selected}))

    assert policy.can_open(selected) is True
    assert policy.can_download(selected) is True


def test_structured_open_and_download_grants_are_distinct(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside_open = tmp_path / "outside-open.txt"
    outside_open.write_text("open", encoding="utf-8")
    outside_download = tmp_path / "outside-download.txt"
    outside_download.write_text("download", encoding="utf-8")
    policy = PathPolicy(
        root,
        grants=(
            PathGrant(
                path=outside_open,
                purpose=PathGrantPurpose.OPEN,
                kind=PathGrantKind.FILE,
                root=root,
            ),
            PathGrant(
                path=outside_download,
                purpose=PathGrantPurpose.DOWNLOAD,
                kind=PathGrantKind.FILE,
                root=root,
            ),
        ),
    )

    assert policy.can_open(outside_open) is True
    assert policy.can_download(outside_open) is False
    assert policy.can_download(outside_download) is True
    assert policy.can_open(outside_download) is False


def test_open_rejects_url_like_paths_before_canonicalization(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()

    assert PathPolicy(root).can_open("https://example.test/file.txt") is False
    assert PathPolicy(root).can_open("file:///tmp/file.txt") is False


def test_listing_is_root_restricted_by_default(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    assert PathPolicy(root).can_list(root) is True
    assert PathPolicy(root).can_list(outside) is False
    assert PathPolicy(root, allow_external_browse=True).can_list(outside, restrict_root=False) is True


def test_merge_endpoint_rejects_external_selected_path(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    res = client.post(
        "/api/merge",
        json={
            "path": str(root),
            "selected_paths": [str(outside)],
            "output_file": str(root / "merged.txt"),
        },
    )

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]


def test_merge_preview_rejects_external_selected_path(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    res = client.post(
        "/api/merge/preview",
        json={"path": str(root), "selected_paths": [str(outside)]},
    )

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]


def test_merge_endpoint_rejects_external_output_by_default(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    source = root / "a.py"
    source.write_text("print('a')\n", encoding="utf-8")
    outside_output = tmp_path / "merged.txt"

    client = TestClient(jsserver.app)
    scan = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert scan.status_code == 200
    res = client.post(
        "/api/merge",
        json={
            "path": str(root),
            "selected_paths": [str(source)],
            "output_file": str(outside_output),
        },
    )

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]
    assert not outside_output.exists()


def test_merge_endpoint_treats_string_false_external_output_as_false(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    source = root / "a.py"
    source.write_text("print('a')\n", encoding="utf-8")
    outside_output = tmp_path / "merged.txt"

    client = TestClient(jsserver.app)
    scan = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert scan.status_code == 200
    res = client.post(
        "/api/merge",
        json={
            "path": str(root),
            "selected_paths": [str(source)],
            "output_file": str(outside_output),
            "allow_external_output": "false",
        },
    )

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]
    assert not outside_output.exists()


def test_merge_endpoint_allows_external_output_when_explicit(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    source = root / "a.py"
    source.write_text("print('a')\n", encoding="utf-8")
    outside_output = tmp_path / "merged.txt"

    client = TestClient(jsserver.app)
    scan = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert scan.status_code == 200
    res = client.post(
        "/api/merge",
        json={
            "path": str(root),
            "selected_paths": [str(source)],
            "output_file": str(outside_output),
            "allow_external_output": True,
        },
    )

    assert res.status_code == 200
    assert outside_output.exists()
    missing_token = client.get("/download", params={"path": str(outside_output)})
    assert missing_token.status_code == 403
    token = client.get("/api/session").json()["csrf_token"]
    download = client.get("/download", params={"path": str(outside_output), "download_token": token})
    assert download.status_code == 200


def test_merge_job_rejects_external_output_before_job_creation(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    source = root / "a.py"
    source.write_text("print('a')\n", encoding="utf-8")
    outside_output = tmp_path / "merged.txt"

    client = TestClient(jsserver.app)
    res = client.post(
        "/api/merge_job",
        json={
            "path": str(root),
            "selected_paths": [str(source)],
            "output_file": str(outside_output),
        },
    )

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]
    assert "job_id" not in res.json()


def test_merge_multiple_rejects_external_output_folder_by_default(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    source = root / "a.py"
    source.write_text("print('a')\n", encoding="utf-8")
    outside_folder = tmp_path / "split"

    client = TestClient(jsserver.app)
    res = client.post(
        "/api/merge/multiple",
        json={
            "path": str(root),
            "selected_paths": [str(source)],
            "output_folder": str(outside_folder),
        },
    )

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]
    assert not outside_folder.exists()


def test_merge_multiple_job_rejects_external_output_folder_before_job_creation(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    source = root / "a.py"
    source.write_text("print('a')\n", encoding="utf-8")
    outside_folder = tmp_path / "split"

    client = TestClient(jsserver.app)
    res = client.post(
        "/api/merge/multiple_job",
        json={
            "path": str(root),
            "selected_paths": [str(source)],
            "output_folder": str(outside_folder),
        },
    )

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]
    assert "job_id" not in res.json()


def test_token_split_check_rejects_external_output_file_by_default(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    source = root / "a.py"
    source.write_text("print('a')\n", encoding="utf-8")
    outside_output = tmp_path / "merged.txt"

    client = TestClient(jsserver.app)
    res = client.post(
        "/api/merge/token_split/check",
        json={
            "path": str(root),
            "selected_paths": [str(source)],
            "output_file": str(outside_output),
            "token_limit": 1000,
        },
    )

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]


def test_token_split_check_preserves_external_selected_path_denial_status(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    res = client.post(
        "/api/merge/token_split/check",
        json={
            "path": str(root),
            "selected_paths": [str(outside)],
            "token_limit": 1000,
        },
    )

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]


def test_token_split_job_rejects_external_output_folder_before_job_creation(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    source = root / "a.py"
    source.write_text("print('a')\n", encoding="utf-8")
    outside_folder = tmp_path / "tok"

    client = TestClient(jsserver.app)
    res = client.post(
        "/api/merge/token_split_job",
        json={
            "path": str(root),
            "selected_paths": [str(source)],
            "output_folder": str(outside_folder),
            "token_limit": 1000,
        },
    )

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]
    assert "job_id" not in res.json()


def test_output_concat_allows_generated_folder_and_rejects_unmarked_folder(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    generated = root / "chunks"
    generated.mkdir()
    (generated / OUTPUT_MARKER).touch()
    (generated / "1.txt").write_text("one", encoding="utf-8")
    unmarked = root / "notes"
    unmarked.mkdir()
    (unmarked / "1.txt").write_text("secret", encoding="utf-8")

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})

    ok = client.post("/api/output/concat", json={"folder": str(generated)})
    assert ok.status_code == 200
    assert ok.json()["text"] == "one"

    denied = client.post("/api/output/concat", json={"folder": str(unmarked)})
    assert denied.status_code == 403
    assert "access denied" in denied.json()["error"]


def test_output_zip_allows_generated_folder_and_downloads_zip(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    generated = root / "chunks"
    generated.mkdir()
    (generated / OUTPUT_MARKER).touch()
    (generated / "1.txt").write_text("one", encoding="utf-8")

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    res = client.post("/api/output/zip", json={"folder": str(generated)})

    assert res.status_code == 200
    zip_path = res.json()["zip_path"]
    assert zip_path.endswith("chunks.zip")
    grants = jsserver._require_ctx().path_grants
    assert any(
        grant.path == PathPolicy._canonical(zip_path)
        and grant.purpose == PathGrantPurpose.GENERATED_OUTPUT
        for grant in grants
    )
    download = client.get(res.json()["download_url"])
    assert download.status_code == 200


def test_output_zip_refuses_non_generated_existing_zip_overwrite(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    generated = root / "chunks"
    generated.mkdir()
    (generated / OUTPUT_MARKER).touch()
    (generated / "1.txt").write_text("one", encoding="utf-8")
    zip_path = root / "chunks.zip"
    zip_path.write_text("user zip", encoding="utf-8")

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    res = client.post("/api/output/zip", json={"folder": str(generated), "overwrite": True})

    assert res.status_code == 409
    assert "non-generated zip" in res.json()["error"]
    assert zip_path.read_text(encoding="utf-8") == "user zip"


def test_open_rejects_source_file_but_allows_generated_output(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    opened: list[str] = []
    monkeypatch.setattr(jsserver, "open_file_in_default_app", lambda path: opened.append(path))

    root = tmp_path / "repo"
    root.mkdir()
    source = root / "a.py"
    source.write_text("print('a')\n", encoding="utf-8")
    generated = root / "chunks"
    generated.mkdir()
    (generated / OUTPUT_MARKER).touch()

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})

    denied = client.post("/api/open", json={"path": str(source)})
    assert denied.status_code == 403
    assert opened == []

    ok = client.post("/api/open", json={"path": str(generated)})
    assert ok.status_code == 200
    assert opened == [str(generated.resolve())]


def test_fs_list_defaults_to_root_restricted(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")

    client = TestClient(jsserver.app)
    scan = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert scan.status_code == 200
    res = client.post("/api/fs/list", json={"path": str(outside)})

    assert res.status_code == 403
    assert "access denied" in res.json()["error"]


def test_fs_list_external_browse_requires_explicit_flag(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "visible.txt").write_text("visible", encoding="utf-8")

    client = TestClient(jsserver.app)
    scan = client.post("/api/scan", json={"path": str(root), "include_all": True})
    assert scan.status_code == 200

    denied = client.post(
        "/api/fs/list",
        json={"path": str(outside), "restrict_root": False},
    )
    assert denied.status_code == 403

    ok = client.post(
        "/api/fs/list",
        json={"path": str(outside), "restrict_root": False, "allow_external_browse": True},
    )
    assert ok.status_code == 200
    assert [entry["name"] for entry in ok.json()["entries"]] == ["visible.txt"]


def test_allow_path_output_hint_does_not_grant_external_download_or_open(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    opened: list[str] = []
    monkeypatch.setattr(jsserver, "open_file_in_default_app", lambda path: opened.append(path))

    root = tmp_path / "repo"
    root.mkdir()
    external = tmp_path / "external.txt"
    external.write_text("external", encoding="utf-8")

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    allow = client.post("/api/allow_path", json={"path": str(external)})

    assert allow.status_code == 200
    assert allow.json()["granted"] is False
    assert client.get("/download", params={"path": str(external)}).status_code == 403
    assert client.post("/api/open", json={"path": str(external)}).status_code == 403
    assert opened == []


def test_path_exists_is_root_scoped(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    inside = root / "inside.txt"
    inside.write_text("inside", encoding="utf-8")
    external = tmp_path / "external.txt"
    external.write_text("external", encoding="utf-8")

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})

    ok = client.post("/api/path/exists", json={"path": str(inside)})
    assert ok.status_code == 200
    assert ok.json()["exists"] is True
    assert ok.json()["is_file"] is True

    denied = client.post("/api/path/exists", json={"path": str(external)})
    assert denied.status_code == 403
    assert "access denied" in denied.json()["error"]


def test_verify_tokens_rejects_external_selected_path(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    inside = root / "inside.py"
    inside.write_text("print('inside')\n", encoding="utf-8")
    external = tmp_path / "external.py"
    external.write_text("print('external')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    res = client.post(
        "/api/verify_tokens",
        json={"path": str(root), "selected_paths": [str(inside), str(external)], "ensure_summaries": False},
    )

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]


def test_summarize_rejects_external_selected_path_before_job(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    external = tmp_path / "external.py"
    external.write_text("print('external')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    srv = jsserver._require_ctx()
    jobs_before = set(srv.jobs)
    res = client.post("/api/summarize", json={"path": str(root), "selected_paths": [str(external)]})

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]
    assert set(srv.jobs) == jobs_before


def test_prune_rejects_external_selected_and_output_before_job(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    inside = root / "inside.py"
    inside.write_text("print('inside')\n", encoding="utf-8")
    external = tmp_path / "external.py"
    external.write_text("print('external')\n", encoding="utf-8")

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    srv = jsserver._require_ctx()

    selected = client.post(
        "/api/prune",
        json={"path": str(root), "goal": "goal", "selected_paths": [str(external)]},
    )
    assert selected.status_code == 403
    assert set(srv.jobs) == set()

    output = client.post(
        "/api/prune",
        json={
            "path": str(root),
            "goal": "goal",
            "selected_paths": [str(inside)],
            "output_path": str(tmp_path / "external-output.txt"),
        },
    )
    assert output.status_code == 403
    assert set(srv.jobs) == set()


def test_context_pack_rejects_external_output_before_build(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    called: list[bool] = []

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    srv = jsserver._require_ctx()
    monkeypatch.setattr(srv.processor, "build_pack", lambda *args, **kwargs: called.append(True))

    res = client.post(
        "/api/context_pack/build",
        json={"path": str(root), "subsystem": "core", "output_path": str(tmp_path / "pack.txt")},
    )

    assert res.status_code == 403
    assert called == []


def test_pair_codebases_rejects_traversal_selected_rel_before_write(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    src_a = root / "a"
    src_b = root / "b"
    dest = root / "paired"
    src_a.mkdir(parents=True)
    src_b.mkdir()
    called: list[bool] = []

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    srv = jsserver._require_ctx()
    monkeypatch.setattr(srv.processor, "pair_selected_files", lambda *args, **kwargs: called.append(True))

    res = client.post(
        "/api/pair_codebases",
        json={
            "src_a": str(src_a),
            "src_b": str(src_b),
            "dest_dir": str(dest),
            "pair_selected": True,
            "selected_rel_paths": ["../escape.py"],
        },
    )

    assert res.status_code == 403
    assert called == []


def test_pair_codebases_requires_source_grant_for_external_sources(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    external = tmp_path / "external"
    src_a = root / "a"
    src_b = external / "b"
    dest = root / "paired"
    src_a.mkdir(parents=True)
    src_b.mkdir(parents=True)
    called: list[tuple[str, str, str]] = []

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    srv = jsserver._require_ctx()
    monkeypatch.setattr(srv.processor, "pair_codebases", lambda a, b, d, **_kwargs: called.append((a, b, d)))

    denied = client.post(
        "/api/pair_codebases",
        json={"src_a": str(src_a), "src_b": str(src_b), "dest_dir": str(dest)},
    )
    granted = client.post(
        "/api/pair_codebases",
        json={
            "src_a": str(src_a),
            "src_b": str(src_b),
            "dest_dir": str(dest),
            "source_grants": [str(src_b)],
        },
    )

    assert denied.status_code == 403
    assert granted.status_code == 200
    assert called == [(str(src_a.resolve()), str(src_b.resolve()), str(dest.resolve()))]


def test_pair_ai_rejects_external_output_before_provider_call(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    called: list[bool] = []

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    srv = jsserver._require_ctx()
    monkeypatch.setattr(srv.processor, "compare_codebases_ai", lambda *args, **kwargs: called.append(True))

    res = client.post(
        "/api/pair_ai",
        json={"src_a": str(root), "src_b": str(root), "output_path": str(tmp_path / "pair-report.txt")},
    )

    assert res.status_code == 403
    assert called == []


def test_pair_ai_requires_source_grant_before_provider_call(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    external = tmp_path / "external"
    src_a = root / "a"
    src_b = external / "b"
    src_a.mkdir(parents=True)
    src_b.mkdir(parents=True)
    called: list[bool] = []

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    srv = jsserver._require_ctx()
    monkeypatch.setattr(srv.processor, "compare_codebases_ai", lambda *args, **kwargs: called.append(True) or {})

    preflight = client.post("/api/provider/preflight", json={"action": "pair_ai", "src_a": str(src_a), "src_b": str(src_b)}).json()
    denied = client.post(
        "/api/pair_ai",
        json={
            "src_a": str(src_a),
            "src_b": str(src_b),
            "provider_confirmed": True,
            "provider_confirmation_token": preflight["confirmation_token"],
        },
    )
    granted_preflight = client.post(
        "/api/provider/preflight",
        json={"action": "pair_ai", "src_a": str(src_a), "src_b": str(src_b), "source_grants": [str(src_b)]},
    ).json()
    granted = client.post(
        "/api/pair_ai",
        json={
            "src_a": str(src_a),
            "src_b": str(src_b),
            "source_grants": [str(src_b)],
            "provider_confirmed": True,
            "provider_confirmation_token": granted_preflight["confirmation_token"],
        },
    )

    assert denied.status_code == 403
    assert granted.status_code == 200
    assert called == [True]


def test_restore_rejects_external_dest_before_write(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    merged = root / "merged.txt"
    merged.write_text("======= START OF FILE: a.py =======\nprint('a')\n", encoding="utf-8")
    called: list[bool] = []

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    srv = jsserver._require_ctx()
    monkeypatch.setattr(srv.processor, "restore_from_merged", lambda *args, **kwargs: called.append(True))

    res = client.post(
        "/api/restore",
        json={"source": str(merged), "dest_dir": str(tmp_path / "external-restore")},
    )

    assert res.status_code == 403
    assert called == []


def test_restore_requires_source_grant_for_external_merged_file(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    merged = external / "merged.txt"
    merged.write_text("======= START OF FILE: a.py =======\nprint('a')\n", encoding="utf-8")
    dest = root / "restored"
    called: list[tuple[str, str]] = []

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    srv = jsserver._require_ctx()
    monkeypatch.setattr(srv.processor, "restore_from_merged", lambda source, out, **_kwargs: called.append((source, out)))

    denied = client.post("/api/restore", json={"source": str(merged), "dest_dir": str(dest)})
    granted = client.post(
        "/api/restore",
        json={"source": str(merged), "dest_dir": str(dest), "source_grants": [str(merged)]},
    )

    assert denied.status_code == 403
    assert granted.status_code == 200
    assert called == [(str(merged.resolve()), str(dest.resolve()))]


def test_watch_tick_rejects_external_selected_path_before_summary(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    external = tmp_path / "external.py"
    external.write_text("print('external')\n", encoding="utf-8")
    called: list[bool] = []

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    srv = jsserver._require_ctx()
    monkeypatch.setattr(srv.summarization_manager, "run", lambda *args, **kwargs: called.append(True))

    res = client.post("/api/watch/tick", json={"path": str(root), "selected_paths": [str(external)]})

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]
    assert called == []


def test_watch_tick_rejects_external_root_before_summary(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    external_file = external / "external.py"
    external_file.write_text("print('external')\n", encoding="utf-8")
    called: list[bool] = []

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    srv = jsserver._require_ctx()
    monkeypatch.setattr(srv.summarization_manager, "run", lambda *args, **kwargs: called.append(True))

    res = client.post("/api/watch/tick", json={"path": str(external), "selected_paths": [str(external_file)]})

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]
    assert called == []


def test_file_preview_rejects_external_file_after_scan(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    root.mkdir()
    (root / "inside.py").write_text("print('inside')\n", encoding="utf-8")
    external = tmp_path / "external.txt"
    external.write_text("external", encoding="utf-8")

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})

    res = client.post("/api/file/preview", json={"file_path": str(external)})

    assert res.status_code == 403
    assert "access denied" in res.json()["error"]


def test_xcode_targets_rejects_external_path_before_discovery(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    called: list[bool] = []

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    srv = jsserver._require_ctx()
    monkeypatch.setattr(srv.processor, "xcode_target_sources", lambda *_args, **_kwargs: called.append(True))

    res = client.get("/api/xcode_targets", params={"path": str(external)})

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]
    assert called == []


def test_watch_fingerprint_rejects_external_path_before_probe(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    called: list[bool] = []

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    monkeypatch.setattr(jsserver.watch_service, "compute_fingerprint", lambda *_args, **_kwargs: called.append(True))

    res = client.get("/api/watch/fingerprint", params={"path": str(external)})

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]
    assert called == []


def test_context_pack_subsystems_rejects_external_path_before_manifest_read(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    called: list[bool] = []

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    monkeypatch.setattr(jsserver, "list_manifest_subsystems", lambda *_args, **_kwargs: called.append(True))

    res = client.get("/api/context_pack/subsystems", params={"path": str(external)})

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]
    assert called == []


def test_context_pack_build_rejects_external_root_before_build(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    called: list[bool] = []

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    srv = jsserver._require_ctx()
    monkeypatch.setattr(srv.processor, "build_pack", lambda *args, **kwargs: called.append(True))

    res = client.post("/api/context_pack/build", json={"path": str(external), "subsystem": "core"})

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]
    assert called == []


def test_settings_reset_rejects_external_root_mutation(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})

    res = client.post("/api/settings/excluded/reset", json={"path": str(external)})

    assert res.status_code == 403
    assert "outside project root" in res.json()["error"]
    assert jsserver._require_ctx().config.get_config()["last_directory"] == str(root.resolve())


def test_settings_get_put_reject_external_root_mutation(tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})

    get_res = client.get("/api/settings", params={"path": str(external)})
    put_res = client.put("/api/settings", json={"path": str(external), "ui_font_size": 14})

    assert get_res.status_code == 403
    assert put_res.status_code == 403
    assert jsserver._require_ctx().config.get_config()["last_directory"] == str(root.resolve())


def test_diagnostics_and_why_reject_external_root_before_scan_or_read(monkeypatch, tmp_path):
    import codemerge.jsui.server as jsserver

    root = tmp_path / "repo"
    external = tmp_path / "external"
    root.mkdir()
    external.mkdir()
    external_file = external / "external.py"
    external_file.write_text("print('external')\n", encoding="utf-8")
    called: list[bool] = []

    client = TestClient(jsserver.app)
    client.post("/api/scan", json={"path": str(root), "include_all": True})
    monkeypatch.setattr(jsserver.diagnostics_service, "run_diagnostics", lambda *args, **kwargs: called.append(True))

    diag = client.post("/api/diagnostics", json={"path": str(external), "include_all": True})
    why = client.post("/api/why", json={"path": str(external), "file_path": str(external_file), "filters": {}})

    assert diag.status_code == 403
    assert why.status_code == 403
    assert called == []
    assert jsserver._require_ctx().config.get_config()["last_directory"] == str(root.resolve())


def test_pairing_helper_skips_traversal_rels_from_merged_inputs(tmp_path):
    from codemerge.filemerger.extras.pairing import pair_codebases

    class DummyProcessor:
        excluded_dirs = set()
        excluded_files = set()

        def is_virtual_env(self, path: str) -> bool:
            return False

    merged_a = tmp_path / "a.txt"
    merged_b = tmp_path / "b.txt"
    dest = tmp_path / "paired"
    escape = tmp_path / "escape.py"
    merged_a.write_text(
        "======= START OF FILE: ../escape.py =======\n"
        "print('a')\n"
        "======= END OF FILE: ../escape.py =======\n",
        encoding="utf-8",
    )
    merged_b.write_text(
        "======= START OF FILE: ../escape.py =======\n"
        "print('b')\n"
        "======= END OF FILE: ../escape.py =======\n",
        encoding="utf-8",
    )

    pair_codebases(DummyProcessor(), str(merged_a), str(merged_b), str(dest))

    assert not escape.exists()
    assert not any(dest.rglob("*")) if dest.exists() else True
