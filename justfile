# ai-review-ci Bun + Python QC delegation justfile.
# The central implementation lives in ~/ai-review-ci/justfiles/.
# Public recipes delegate to both central gates while preserving this repo as the caller root.

# ai-review-ci contract variables consumed by doctor and workflow installers.
ai_review_ci_schema_version := "1"
ai_review_ci_profile := "bun-python"
ai_review_ci_ref := "main"
ai_review_ci_release_channel := "main"
ai_review_ci_workflow_template_version := "1"
ai_review_ci_local_delegation := "global-justfile"
ai_review_ci_default_branch := "main"
# List available recipes.
default:
    @just --list

# Run commit-tier Python and Bun QC through the central implementation.
test-commit:
    @just -f ~/ai-review-ci/justfiles/python.just -d . test-commit
    @just -f ~/ai-review-ci/justfiles/bun.just -d . test-commit

# Run the full Python and Bun test suites before pushing.
test-push:
    @just -f ~/ai-review-ci/justfiles/python.just -d . test-push
    @just -f ~/ai-review-ci/justfiles/bun.just -d . test-push

# Run CI acceptance QC through both central implementations.
test-ci:
    @just -f ~/ai-review-ci/justfiles/python.just -d . test-ci
    @just -f ~/ai-review-ci/justfiles/bun.just -d . test-ci

[private]
_test-page-note:
    pyenv exec tox -qe functests -- tests/functional/api/annotations_test.py::TestPostAnnotation::test_it_creates_a_page_note_without_normalization

[private]
_test-normalization:
    pyenv exec tox -qe tests -- tests/unit/h/services/normalization_test.py

[private]
_test-pdf-normalization:
    pyenv exec tox -qe tests -- tests/unit/h/services/pdf_math_test.py

[private]
_test-annotation-normalization-error:
    pyenv exec tox -qe functests -- tests/functional/api/annotations_test.py::TestPostAnnotation::test_a_failed_normalization_rolls_the_create_back

[private]
_test-annotation-json:
    pyenv exec tox -qe tests -- tests/unit/h/services/annotation_json_test.py

[private]
_test-normalize-annotations-cli:
    pyenv exec tox -qe tests -- tests/unit/h/cli/commands/normalize_annotations_test.py

[private]
_test-search-reindex:
    pyenv exec tox -qe tests -- tests/unit/h/cli/commands/search_test.py tests/unit/h/search/index_test.py

[private]
[script(".tox/dev/bin/python")]
_normalize-existing-annotations:
    import os
    from pathlib import Path

    for command_path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            command = command_path.read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if b"gunicorn\x00--paste\x00conf/development.ini" not in command:
            continue
        for entry in command_path.with_name("environ").read_bytes().split(b"\x00"):
            if entry:
                key, value = entry.split(b"=", 1)
                os.environ[key.decode()] = value.decode()
        os.execv(
            ".tox/dev/bin/python",
            [
                ".tox/dev/bin/python",
                "-m",
                "h",
                "--dev",
                "normalize-annotations",
            ],
        )
    raise RuntimeError("running h development web process not found")

[private]
[script(".tox/dev/bin/python")]
_reindex-existing-annotations:
    import os
    from pathlib import Path

    for command_path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            command = command_path.read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if b"gunicorn\x00--paste\x00conf/development.ini" not in command:
            continue
        for entry in command_path.with_name("environ").read_bytes().split(b"\x00"):
            if entry:
                key, value = entry.split(b"=", 1)
                os.environ[key.decode()] = value.decode()
        os.execv(
            ".tox/dev/bin/python",
            [
                ".tox/dev/bin/python",
                "-m",
                "h",
                "--dev",
                "search",
                "reindex",
            ],
        )
    raise RuntimeError("running h development web process not found")

[private]
[script(".tox/dev/bin/python")]
_inspect-search-index:
    import json
    import os
    import sys
    from pathlib import Path

    for command_path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            command = command_path.read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if b"gunicorn\x00--paste\x00conf/development.ini" not in command:
            continue
        for entry in command_path.with_name("environ").read_bytes().split(b"\x00"):
            if entry:
                key, value = entry.split(b"=", 1)
                os.environ[key.decode()] = value.decode()
        break
    else:
        raise RuntimeError("running h development web process not found")

    sys.path.insert(0, str(Path.cwd()))

    from h.cli import bootstrap

    request = bootstrap(None, dev=True)
    result = request.es.conn.search(
        index=request.es.index,
        body={"query": {"match_all": {}}, "size": 1000, "_source": ["group", "uri"]},
    )
    print(json.dumps({"total": result["hits"]["total"], "hits": result["hits"]["hits"]}))

[private]
[script(".tox/dev/bin/python")]
_inspect-page-note-proof:
    import json
    import os
    from pathlib import Path

    for command_path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            command = command_path.read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if b"gunicorn\x00--paste\x00conf/development.ini" not in command:
            continue
        for entry in command_path.with_name("environ").read_bytes().split(b"\x00"):
            if entry:
                key, value = entry.split(b"=", 1)
                os.environ[key.decode()] = value.decode()
        break
    else:
        raise RuntimeError("running h development web process not found")

    from sqlalchemy import create_engine, text

    with create_engine(os.environ["DATABASE_URL"]).connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, groupid, target_uri, target_selectors, deleted "
                "FROM annotation WHERE text = :text ORDER BY created DESC"
            ),
            {"text": "Page note integration proof."},
        )
        print(json.dumps([{"id": str(row.id), "group": row.groupid, "uri": row.target_uri, "selectors": row.target_selectors, "deleted": row.deleted} for row in rows]))

[private]
[script(".tox/dev/bin/python")]
_inspect-missing-normalization:
    import json
    import os
    from pathlib import Path

    for command_path in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            command = command_path.read_bytes()
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            continue
        if b"gunicorn\x00--paste\x00conf/development.ini" not in command:
            continue
        for entry in command_path.with_name("environ").read_bytes().split(b"\x00"):
            if entry:
                key, value = entry.split(b"=", 1)
                os.environ[key.decode()] = value.decode()
        break
    else:
        raise RuntimeError("running h development web process not found")

    from sqlalchemy import create_engine, text

    with create_engine(os.environ["DATABASE_URL"]).connect() as connection:
        rows = connection.execute(
            text(
                "SELECT a.id, a.groupid, a.deleted, a.target_uri, a.target_selectors, "
                "array_agg(du.uri ORDER BY du.updated DESC) FROM annotation a "
                "LEFT JOIN annotation_normalized n ON n.annotation_id = a.id "
                "LEFT JOIN document_uri du ON du.document_id = a.document_id "
                "WHERE n.id IS NULL AND a.target_selectors IS NOT NULL "
                "GROUP BY a.id ORDER BY a.created"
            )
        )
        for annotation_id, groupid, deleted, uri, selectors, siblings in rows:
            quotes = [
                selector.get("exact")
                for selector in selectors
                if selector.get("type") == "TextQuoteSelector"
            ]
            if quotes:
                print(
                    json.dumps(
                        {
                            "id": str(annotation_id),
                            "group": groupid,
                            "deleted": deleted,
                            "uri": uri,
                            "siblings": siblings,
                            "selectors": selectors,
                            "quotes": quotes,
                        }
                    )
                )

[private]
_add-html-normalize-browser:
    cd h/scripts/html-normalize && pnpm add playwright-core

[private]
_render-html-ocr-fixture output:
    node h/scripts/html-normalize/ocr.mjs 'http://127.0.0.1:7654/framework/Higher-Categories-and-Universes.html' 'Ordinary categories enter through the ordinary nerve' 30000 | base64 -d > {{output}}
