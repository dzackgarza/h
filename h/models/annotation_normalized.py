import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from h.db import Base, types
from h.db.mixins import Timestamps
from h.models import helpers


class AnnotationNormalized(Base, Timestamps):
    r"""A display-ready rendering of an annotation's selected text.

    The stored quote (in the annotation's selectors) is the raw text-layer / flattened-DOM
    capture, kept verbatim because anchoring depends on it. For an annotation over rendered
    mathematics that capture is unreadable (``M0 En`` for ``\\(\\mathcal{M}^0_{En}\\)``), so
    it must never be what a reader or agent sees. This table holds the reconstructed quote
    for that annotation — math recovered from the document source (HTML math markup, or OCR
    of a PDF region). It is a separate row keyed to the annotation, not a column on it: the
    annotation, and thus anchoring, is never mutated. Every view joins this and shows the
    normalized quote; the raw capture is used only for anchoring.
    """

    __tablename__ = "annotation_normalized"

    id: Mapped[int] = mapped_column(sa.Integer, autoincrement=True, primary_key=True)

    annotation_id: Mapped[types.URLSafeUUID] = mapped_column(
        types.URLSafeUUID,
        sa.ForeignKey("annotation.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    """FK to annotation.id -- one normalization per annotation."""
    annotation = sa.orm.relationship(
        "Annotation", back_populates="normalized", uselist=False
    )

    normalized_quote: Mapped[str] = mapped_column(sa.UnicodeText, nullable=False)
    """The selected text with rendered math recovered, in the document's LaTeX, as
    ``\\(..\\)`` / ``$$..$$`` for a markdown/KaTeX view. Equal to the raw quote when the
    selection spans no math."""

    method: Mapped[str] = mapped_column(sa.UnicodeText, nullable=False)
    """How it was produced: ``html`` (recovered from the page's math source) or ``ocr``
    (Mathpix on a rendered region). Never ``raw`` -- a genuine recovery failure raises and
    rolls the create back, so no row is ever written for it."""

    def __repr__(self) -> str:
        return helpers.repr_(self, ["id", "annotation_id", "method"])
