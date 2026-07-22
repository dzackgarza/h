r"""A reader annotating real mathematical web pages, driven through the API their browser calls.

Every test posts to ``/api/annotations`` exactly as the client does -- a
``TextQuoteSelector`` whose ``exact`` is what the client's ``renderedTextFromRange``
captured from the reader's drag -- and reads the result back. The app is the real one on
the real database, and the pages are served over HTTP from this module, so the recovery's
fetch, parse and reconstruction all run for real.

The pages are committed excerpts of real documents, each a different way mathematics
reaches a browser (``tests/corpus/pages``):

* ``ar5iv-enriques.html`` -- LaTeXML MathML with the authored TeX in an ``annotation``
  child, from ar5iv's rendering of arXiv:2312.03638 (the same paper the PDF walkthrough
  annotates).
* ``mathjax-category-theory.html`` -- Pandoc ``<span class="math">`` holding ``\(..\)``,
  typeset in the browser by MathJax v3, with the page's own macro definitions.
* ``katex-docusaurus.html`` -- KaTeX rendered server-side: visible spans beside hidden
  MathML carrying the TeX.
* ``plain-about.html`` -- prose, no mathematics anywhere.

The selections are recorded, not written: what a drag captures depends on what the page's
own JavaScript built and on nodes the reader cannot see, so ``tests/corpus/record_html_
selections.mjs`` records each drag from a real browser and commits the result to
``tests/corpus/html-selections.json``. Re-record with ``just _record-html-selections``.

What is asserted is what the reader gets: the annotation saves, and its quote carries the
mathematics as authored TeX rather than as the glyph soup the DOM handed the client. A
neighbouring word costs the reader nothing and is not asserted against; noise *inside* the
selection is, because it is what makes a quote unreadable.
"""

import http.server
import json
import pathlib
import threading

import pytest

from h.models import Annotation

CORPUS = pathlib.Path(__file__).parents[2] / "corpus"
PAGES = CORPUS / "pages"
SELECTIONS = json.loads((CORPUS / "html-selections.json").read_text())

#: The mathematics each drag covers, as the reader expects to read it: the TeX the page's
#: author wrote. Not the whole quote -- prose around it is carried through verbatim.
MATHEMATICS = {
    "ar5iv_prose_and_inline_math": [r"2K\sim 0", "q=0"],
    "ar5iv_paragraph_of_definitions": [r"F_{\rm En,2}", r"{\mathcal{L}}_{Z}"],
    "ar5iv_theorem_statement": [r"{\overline{F}}_{\rm En,2}"],
    "ar5iv_drag_through_a_commutative_diagram": [
        r"{\mathbb{P}}^{1}\times{\mathbb{P}}^{1}",
        r"\tau\colon(x,y)\to(-x,-y)",
    ],
    "mathjax_prose_only": [],
    "mathjax_inline_math": [r"V^* = n", r"V^* \cong R^n"],
    "mathjax_custom_macro": [r"V\dual"],
    "mathjax_display_math": ["0+1 &= 1+0 = 1"],
    "katex_inline_math": [r"f\colon[a,b] \to \R", "\n" + r"\int_{a}^{x} f(t)\,dt"],
    "katex_display_math": [r"\int_0^{2\pi} \sin(x)\,dx"],
    "katex_prose_only": [],
    "plain_prose": [],
}

#: Text the DOM handed the client that must not survive into the quote. Each is markup a
#: browser never shows a reader: LaTeXML's Content MathML operator names, and the glyph run
#: KaTeX and LaTeXML repeat beside the formula. A quote carrying these is unreadable.
INVISIBLE_TO_THE_READER = {
    "ar5iv_prose_and_inline_math": ["similar-to"],
    "ar5iv_paragraph_of_definitions": ["subscript"],
    "ar5iv_theorem_statement": ["subscript"],
    "ar5iv_drag_through_a_commutative_diagram": ["superscript"],
    "katex_inline_math": ["f:[a,b]→R"],
    "katex_display_math": ["sin(x)dx"],
}

pytestmark = pytest.mark.usefixtures("init_elasticsearch")


class TestAnnotatingAMathematicalWebPage:
    @pytest.mark.parametrize("drag", sorted(SELECTIONS))
    def test_the_reader_gets_back_the_mathematics_they_selected(
        self, app, token_auth_header, page_url, drag
    ):
        response = app.post_json(
            "/api/annotations",
            _annotation(page_url(drag), SELECTIONS[drag]["exact"]),
            headers=token_auth_header,
        )

        assert response.status_code == 200
        quote = response.json["normalized_quote"]
        for latex in MATHEMATICS[drag]:
            assert latex in quote

    @pytest.mark.parametrize("drag", sorted(INVISIBLE_TO_THE_READER))
    def test_the_quote_carries_nothing_the_reader_could_not_see(
        self, app, token_auth_header, page_url, drag
    ):
        # The DOM hands the client more than the page shows: LaTeXML repeats every formula
        # as Content MathML, KaTeX repeats it as hidden MathML, and both sit inside the
        # captured range. Recovering the formula means replacing that run, not appending
        # TeX in front of it.
        response = app.post_json(
            "/api/annotations",
            _annotation(page_url(drag), SELECTIONS[drag]["exact"]),
            headers=token_auth_header,
        )

        quote = response.json["normalized_quote"]
        for noise in INVISIBLE_TO_THE_READER[drag]:
            assert noise not in quote

    def test_a_page_with_no_mathematics_is_quoted_as_it_reads(
        self, app, token_auth_header, page_url
    ):
        exact = SELECTIONS["plain_prose"]["exact"]

        response = app.post_json(
            "/api/annotations",
            _annotation(page_url("plain_prose"), exact),
            headers=token_auth_header,
        )

        assert response.json["normalized_quote"] == exact

    def test_a_highlight_with_no_note_still_gets_its_quote(
        self, app, token_auth_header, page_url
    ):
        # A highlight is an annotation with no text: the reader marked a passage and wrote
        # nothing. It is the most common thing a reader does, and it still has to be
        # readable in the sidebar, so it is recovered like any other.
        drag = "ar5iv_prose_and_inline_math"
        payload = _annotation(page_url(drag), SELECTIONS[drag]["exact"])
        payload["text"] = ""

        response = app.post_json("/api/annotations", payload, headers=token_auth_header)

        assert response.status_code == 200
        assert r"2K\sim 0" in response.json["normalized_quote"]

    def test_editing_the_note_leaves_the_quote_alone(
        self, app, token_auth_header, page_url
    ):
        # The reader reopens the annotation and rewrites their note. The selection did not
        # change, so the recovered quote must not either -- and re-recovering it would
        # spend a fetch and a parse on an answer already stored.
        drag = "ar5iv_paragraph_of_definitions"
        created = app.post_json(
            "/api/annotations",
            _annotation(page_url(drag), SELECTIONS[drag]["exact"]),
            headers=token_auth_header,
        )

        edited = app.patch_json(
            f"/api/annotations/{created.json['id']}",
            {"text": "a second thought about the same passage"},
            headers=token_auth_header,
        )

        assert edited.json["text"] == "a second thought about the same passage"
        assert edited.json["normalized_quote"] == created.json["normalized_quote"]

    def test_deleting_the_annotation_takes_its_quote_with_it(
        self, app, token_auth_header, page_url, db_session
    ):
        drag = "katex_inline_math"
        created = app.post_json(
            "/api/annotations",
            _annotation(page_url(drag), SELECTIONS[drag]["exact"]),
            headers=token_auth_header,
        )

        deleted = app.delete(
            f"/api/annotations/{created.json['id']}", headers=token_auth_header
        )

        assert deleted.json["deleted"] is True
        annotation = db_session.get(Annotation, created.json["id"])
        assert annotation.deleted is True

    def test_a_selection_that_is_no_longer_on_the_page_saves_nothing(
        self, app, token_auth_header, page_url, db_session
    ):
        # The page changed under a stale selector, or the reader's browser handed us a
        # capture we cannot find: there is nothing to recover, and nothing may be stored.
        before = db_session.query(Annotation).count()

        response = app.post_json(
            "/api/annotations",
            _annotation(page_url("plain_prose"), "zxqv kumquat xylophone brillig"),
            headers=token_auth_header,
            expect_errors=True,
        )

        assert response.status_code == 500
        assert db_session.query(Annotation).count() == before


def _annotation(uri: str, exact: str) -> dict:
    """Build the payload the client posts for a drag a reader made over a web page."""
    return {
        "uri": uri,
        "text": "an annotation",
        "target": [
            {
                "source": uri,
                "selector": [{"type": "TextQuoteSelector", "exact": exact}],
            }
        ],
    }


@pytest.fixture(scope="module")
def served_pages():
    """Serve the fixture pages over HTTP, so the recovery fetches them as it always does."""

    class _Pages(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path = PAGES / pathlib.PurePosixPath(self.path).name
            if not path.is_file():
                self.send_error(404)
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            """Keep test output quiet; assertions cover the behavior."""

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.fixture
def page_url(served_pages):
    """Return the URL of the page a given drag was recorded on."""
    return lambda drag: f"{served_pages}/{SELECTIONS[drag]['page']}"
