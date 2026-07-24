"""A reader annotating a real PDF, driven through the API their browser calls.

Every test here posts to ``/api/annotations`` exactly as the client does -- a
``PageSelector`` and a ``TextQuoteSelector`` whose ``exact`` is the flattened text layer
the browser captured -- and reads the result back through the API. The app is the real
one, on the real database; the PDF is fetched over HTTP from a server this module starts,
so the recovery's fetch, render and locate steps all run for real.

The selections are real drags over pages 1 and 9 of arXiv:2312.03638
(``tests/corpus/enriques-moduli-p1-p9.pdf``), chosen to cover the shapes a reader of a
mathematics paper actually produces: prose, a selection ending in a formula, one starting
in one, one broken across a line in the middle of an expression, a figure caption, a long
passage carrying a symbol that dips into the line below.

One step cannot run here: OCR is a paid call to Mathpix. Its answers are replayed from
``tests/corpus/mathpix-recordings.json``, keyed by the bytes of the crop that produced them,
so a change that moves the crop misses the recording and fails loudly instead of passing
on a stale answer. Re-record with ``just _record-pdf-math-fixtures``.

What is asserted is what the reader gets: the annotation saves, and its quote carries the
mathematics they selected as LaTeX rather than the flattened glyphs. The crop is
deliberately generous at its edges -- a neighbouring word costs a reader nothing, losing
their selection costs them the annotation -- so nothing here asserts that the quote stops
exactly where the selection did.
"""

import hashlib
import http.server
import json
import pathlib
import threading

import pytest

from h.models import Annotation

CORPUS = pathlib.Path(__file__).parents[2] / "corpus"
PAPER = CORPUS / "enriques-moduli-p1-p9.pdf"
RECORDINGS = CORPUS / "mathpix-recordings.json"

INTRODUCTION = 0
CUSP_DIAGRAM = 1

#: name -> (page index, the text layer's rendering of the reader's drag)
SELECTIONS = {
    "prose_only": (
        INTRODUCTION,
        "Enriques   surfaces   are   quotients   of   K3   surfaces   by   basepoint   "
        "free   involutions.",
    ),
    "ends_in_math": (
        INTRODUCTION,
        "surfaces   ( Z,  M )   with   a   2-divisible   polarization   M  =  L ⊗ 2",
    ),
    "begins_in_math": (
        INTRODUCTION,
        "2 K ∼ 0 and q = 0 and occupy a place somewhere in between rational",
    ),
    "two_lines_inline_math": (
        INTRODUCTION,
        "In this paper we consider the moduli space  F En , 2   of pairs ( Z,  [ L Z ]), "
        "where  Z   is an Enriques surface with  ADE  singularities and [ L Z ]  ∈ Pic  "
        "Z/ Z 2  is an ample numerical",
    ),
    "spans_a_math_line_break": (
        INTRODUCTION,
        "surfaces   ( Z,  M )   with   a   2-divisible   polarization   M  =  L ⊗ 2 Z ∈ "
        "Pic  Z   of   degree   8.   The",
    ),
    "symbol_dips_into_the_line_below": (
        INTRODUCTION,
        "cover   ρ :   Z   → W   to   a   quartic   del   Pezzo   surface   with   4 A 1"
        "   or   A 3  + 2 A 1   singularities. The   ramification   divisor   R Z   ∈|M|"
        "   of   ρ   is   ample   and   the   pair   ( Z, ϵR Z )   is   log",
    ),
    "figure_caption": (
        CUSP_DIAGRAM,
        "Figure   3.   Cusp   diagram   of   F (10 , 10 , 0) ,   for   T En   =  U   ⊕ "
        "U (2)  ⊕ E 8 (2)",
    ),
    "prose_across_two_lines": (
        CUSP_DIAGRAM,
        "A   geometric   interpretation   of   these   cusps   is   as   follows.   Let  "
        " X   → ( C,  0)   be   a Kulikov   model   and   consider   the   completed   "
        "period   mapping",
    ),
}

#: The mathematics each selection covers, as the reader expects to see it rendered. Not
#: the whole quote: the crop may carry a neighbouring word, and that is not a defect.
MATHEMATICS = {
    "prose_only": [],
    "ends_in_math": [r"\mathcal{L}_{Z}^{\otimes 2}", r"\mathcal{M}"],
    "begins_in_math": [r"2 K \sim 0", "q=0"],
    "two_lines_inline_math": [r"\mathbb{Z}_{2}", r"F_{\mathrm{En}, 2}"],
    "spans_a_math_line_break": [
        r"\mathcal{L}_{Z}^{\otimes 2}",
        r"\operatorname{Pic} Z",
    ],
    "symbol_dips_into_the_line_below": [r"\in|\mathcal{M}|", r"A_{3}+2 A_{1}"],
    "figure_caption": [r"E_{8}(2)", r"T_{\mathrm{En}}"],
    "prose_across_two_lines": [r"\mathcal{X} \rightarrow(C, 0)"],
}

#: Every PDF region goes through OCR -- there is no text layer to recover authored TeX
#: from, only glyphs. A drag here recovering by any other path would mean the routing
#: broke, not that it got cheaper.
RECOVERY_PATH = dict.fromkeys(SELECTIONS, "ocr")

pytestmark = pytest.mark.usefixtures("init_elasticsearch", "replayed_ocr")


class TestAnnotatingAPdf:
    @pytest.mark.parametrize("selection", sorted(SELECTIONS))
    def test_the_reader_gets_back_the_mathematics_they_selected(
        self, app, token_auth_header, paper_url, selection, db_session
    ):
        page_index, exact = SELECTIONS[selection]

        response = app.post_json(
            "/api/annotations",
            _annotation(paper_url, page_index, exact),
            headers=token_auth_header,
        )

        assert response.status_code == 200
        annotation = db_session.get(Annotation, response.json["id"])
        assert annotation.normalized.method == RECOVERY_PATH[selection]
        quote = response.json["normalized_quote"]
        for latex in MATHEMATICS[selection]:
            assert latex in quote

    def test_the_quote_the_reader_reads_is_never_the_flattened_capture(
        self, app, token_auth_header, paper_url
    ):
        # The whole point of the recovery: the text layer stores "L ⊗ 2" where the page
        # shows a superscript, and that is what must not reach the reader.
        page_index, exact = SELECTIONS["ends_in_math"]

        response = app.post_json(
            "/api/annotations",
            _annotation(paper_url, page_index, exact),
            headers=token_auth_header,
        )

        assert "L ⊗ 2" not in response.json["normalized_quote"]

    def test_the_quote_survives_a_reload_of_the_page(
        self, app, token_auth_header, paper_url
    ):
        # The reader comes back to the document later and the sidebar fetches the
        # annotation again: the recovered quote is stored, not computed for the response.
        page_index, exact = SELECTIONS["spans_a_math_line_break"]
        created = app.post_json(
            "/api/annotations",
            _annotation(paper_url, page_index, exact),
            headers=token_auth_header,
        )

        reloaded = app.get(
            f"/api/annotations/{created.json['id']}", headers=token_auth_header
        )

        assert reloaded.json["normalized_quote"] == created.json["normalized_quote"]
        assert r"\operatorname{Pic} Z" in reloaded.json["normalized_quote"]

    def test_a_note_on_the_whole_pdf_needs_no_recovery(
        self, app, token_auth_header, paper_url
    ):
        # A page note carries no selection, so there is nothing to recover and nothing to
        # fail: the reader gets their note.
        response = app.post_json(
            "/api/annotations",
            {"uri": paper_url, "text": "a note about the whole paper"},
            headers=token_auth_header,
        )

        assert response.status_code == 200
        assert not response.json["normalized_quote"]

    def test_a_reply_needs_no_recovery(self, app, token_auth_header, paper_url):
        page_index, exact = SELECTIONS["prose_only"]
        parent = app.post_json(
            "/api/annotations",
            _annotation(paper_url, page_index, exact),
            headers=token_auth_header,
        )

        reply = app.post_json(
            "/api/annotations",
            {
                "uri": paper_url,
                "text": "I agree",
                "references": [parent.json["id"]],
            },
            headers=token_auth_header,
        )

        assert reply.status_code == 200
        assert not reply.json["normalized_quote"]

    def test_a_selection_that_is_not_on_the_page_saves_nothing(
        self, app, token_auth_header, paper_url, db_session
    ):
        # The failure a reader can actually hit: the document changed under a stale
        # selector. Nothing of the selection is on the page, so there is no region to
        # recover, and the create must leave no annotation behind rather than store a
        # quote of whatever the locator settled on.
        before = db_session.query(Annotation).count()

        response = app.post_json(
            "/api/annotations",
            _annotation(paper_url, INTRODUCTION, "zxqv kumquat xylophone brillig"),
            headers=token_auth_header,
            expect_errors=True,
        )

        assert response.status_code == 500
        assert db_session.query(Annotation).count() == before


def _annotation(uri: str, page_index: int, exact: str) -> dict:
    """Build the payload the client posts for a drag a reader made over a PDF."""
    return {
        "uri": uri,
        "text": "an annotation",
        "target": [
            {
                "source": uri,
                "selector": [
                    {"type": "PageSelector", "index": page_index, "label": "1"},
                    {"type": "TextQuoteSelector", "exact": exact},
                ],
            }
        ],
    }


@pytest.fixture(scope="module")
def paper_url():
    """Serve the fixture paper over HTTP, so the recovery fetches it the way it always does."""

    class _Paper(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            body = PAPER.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Paper)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/enriques-moduli.pdf"
    server.shutdown()
    thread.join()
    server.server_close()


@pytest.fixture
def replayed_ocr(monkeypatch):
    """Answer the recovery's Mathpix call from the recording made for that exact crop.

    The recovery renders the crop for real; only the paid third-party call is replayed.
    Keying on the crop's bytes is what keeps this honest: a change that moves the region
    fails here, with the command to re-record, instead of quietly reusing an answer that
    describes a region the code no longer produces.
    """
    import base64  # noqa: PLC0415 - local to the boundary being replaced

    import requests  # noqa: PLC0415

    recordings = json.loads(RECORDINGS.read_text())
    # The recovery refuses to run without a key, and rightly so; the replay stands in for
    # the account the recordings were made with.
    monkeypatch.setenv("MATHPIX_API_KEY", "replayed-from-tests/corpus")

    class _Replayed:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self):
            pass

        def json(self):
            return self._body

    def post(_url, **kwargs):
        png = base64.b64decode(kwargs["json"]["src"].split(",", 1)[1])
        digest = hashlib.sha256(png).hexdigest()
        recording = recordings.get(digest)
        if recording is None:
            pytest.fail(
                f"no Mathpix recording for this crop ({digest[:12]}). The rendered "
                f"region changed; re-record with `just _record-pdf-math-fixtures`."
            )
        return _Replayed(recording["body"])

    monkeypatch.setattr(requests, "post", post)
