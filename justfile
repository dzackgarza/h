# Native QC delegation justfile.
# h is a fork of upstream hypothesis/h and keeps upstream's native QC (tox: ruff,
# mypy, pytest). The ai-review-ci language gates do not apply to this fork; only the
# AI review workflows (review-general/slop/pr) run from .github/workflows/.

# tox<4 with the plugins tox.ini requires, run ephemerally (no global install).
tox := "uvx --python 3.11 --with tox-envfile --with tox-faster --with tox-run-command 'tox<4'"

# List available recipes.
default:
    @just --list

# Commit-tier QC: formatting, lint, and types via upstream's tox envs.
test-commit:
    {{tox}} -qe checkformatting,lint,typecheck

# Push-tier QC: commit tier plus the unit test suite.
test-push: test-commit
    {{tox}} -qe tests

# CI-tier QC: push tier plus the functional test suite.
test-ci: test-push
    {{tox}} -qe functests

# Re-record Mathpix's answers for the PDF annotation walkthrough. Calls the live API with
# the configured key and costs money, which is why the answers are committed; run it when
# the walkthrough's selections change or when a crop moves.
[private]
_record-pdf-math-fixtures:
    .tox/tests/bin/python tests/corpus/record_mathpix.py

# Re-record what a reader's drag captures on each fixture page. Drives a real browser
# (H_CHROMIUM_PATH) and lets the pages load their own renderers, so it needs the network;
# run it when a fixture page or one of the recorded drags changes.
[private]
_record-html-selections:
    node tests/corpus/record_html_selections.mjs

# Export what the recovery produces for every drag in the corpus, for the sidebar
# rendering suite in the client fork to render. No database or network needed: it runs the
# real extractor against the committed fixture pages.
[private]
_export-recovered-quotes:
    node tests/corpus/export_recovered_quotes.mjs

[private]
_test-pdf-annotations:
    {{tox}} -qe functests -- tests/functional/api/pdf_math_annotations_test.py

[private]
_test-html-annotations:
    {{tox}} -qe functests -- tests/functional/api/html_math_annotations_test.py

[private]
_test-page-note:
    {{tox}} -qe functests -- tests/functional/api/annotations_test.py::TestPostAnnotation::test_it_creates_a_page_note_without_normalization

[private]
_test-normalization:
    {{tox}} -qe tests -- tests/unit/h/services/normalization_test.py

[private]
_test-pdf-normalization:
    {{tox}} -qe tests -- tests/unit/h/services/pdf_math_test.py

[private]
_test-annotation-normalization-error:
    {{tox}} -qe functests -- tests/functional/api/annotations_test.py::TestPostAnnotation::test_a_failed_normalization_rolls_the_create_back

[private]
_test-annotation-json:
    {{tox}} -qe tests -- tests/unit/h/services/annotation_json_test.py

[private]
_test-normalize-annotations-cli:
    {{tox}} -qe tests -- tests/unit/h/cli/commands/normalize_annotations_test.py

[private]
_test-search-reindex:
    {{tox}} -qe tests -- tests/unit/h/cli/commands/search_test.py tests/unit/h/search/index_test.py

# Report how stored annotations were recovered: the path distribution, the share that
# took the paid OCR call, and any annotation holding a recovery this version would not
# produce. Reads the running dev web process's environment for the database.
[script(".tox/dev/bin/python")]
recovery-report:
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
                "recovery-report",
            ],
        )
    raise RuntimeError("running h development web process not found")

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

# Reconcile the live search index against the annotations actually in Postgres.
# Four ways the two can disagree, each reported and each a failure: a live row
# missing from the index (search cannot find it), a live row indexed as a
# tombstone (same), a deleted row still indexed as live (search returns what was
# deleted), and an indexed document whose row is gone entirely. The last is what
# old functest runs, the review tool's marker sessions, and database resets leave
# behind in a shared dev index. `just _reindex-existing-annotations` does not
# clear those: `search reindex` walks the Postgres rows and writes each one, so a
# document whose row is already gone is never visited. Removing them means
# deleting those documents, or building the index fresh.
[private]
[script(".tox/dev/bin/python")]
_reconcile-search-index:
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
    from h.models import Annotation

    request = bootstrap(None, dev=True)
    rows = dict(request.db.query(Annotation.id, Annotation.deleted))

    documents = {}
    page = request.es.conn.search(
        index=request.es.index,
        body={"query": {"match_all": {}}, "_source": ["deleted", "uri"]},
        size=500,
        scroll="2m",
    )
    while page["hits"]["hits"]:
        documents.update((hit["_id"], hit["_source"]) for hit in page["hits"]["hits"])
        page = request.es.conn.scroll(scroll_id=page["_scroll_id"], scroll="2m")

    def tombstoned(annotation_id):
        return documents[annotation_id].get("deleted") is True

    live = {id_ for id_, deleted in rows.items() if not deleted}
    faults = {
        "live rows missing from the index": sorted(live - documents.keys()),
        "live rows indexed as a tombstone": sorted(
            id_ for id_ in live & documents.keys() if tombstoned(id_)
        ),
        "deleted rows still indexed as live": sorted(
            id_
            for id_, deleted in rows.items()
            if deleted and id_ in documents and not tombstoned(id_)
        ),
        "indexed documents with no row in Postgres": sorted(
            id_ for id_ in documents.keys() - rows.keys() if not tombstoned(id_)
        ),
    }

    print(f"postgres: {len(rows)} rows ({len(live)} live)  index: {len(documents)} documents")
    for description, annotation_ids in faults.items():
        print(f"{len(annotation_ids):5d}  {description}")
        for annotation_id in annotation_ids[:10]:
            print(f"         {annotation_id}  {documents.get(annotation_id, {}).get('uri', '-')}")
        if len(annotation_ids) > 10:
            print(f"         ... and {len(annotation_ids) - 10} more")

    if any(faults.values()):
        raise SystemExit(1)

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
