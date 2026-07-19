import factory

from h import models

from .annotation import Annotation
from .base import ModelFactory


class AnnotationNormalized(ModelFactory):
    class Meta:
        model = models.AnnotationNormalized

    annotation = factory.SubFactory(Annotation)
    normalized_quote = factory.Faker("sentence")
    method = "html"
    error = None
