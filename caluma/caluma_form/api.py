from typing import Any, Optional

from django.db import transaction

from caluma.caluma_form import domain_logic, models
from caluma.caluma_user.models import BaseUser


def _lock_family(document):
    """Lock the given document's family for the current transaction.

    Any update in the form structure must lock the document family, so
    no other request in parallel can update the same document. Even if
    only an answer is updated, it could trigger multiple calculated field
    updates, which could then cause race conditions.
    """
    _ = models.Document.objects.filter(pk=document.family_id).select_for_update().get()


@transaction.atomic
def save_answer(
    question: models.Question,
    document: Optional[models.Document] = None,
    user: Optional[BaseUser] = None,
    value: Optional[Any] = None,
    context: Optional[dict] = None,
    **kwargs,
) -> models.Answer:
    """
    Save an answer for given question, document.

    Similar to saveDocumentStringAnswer and the likes, it performes upsert.
    :param value: Must match the question type
    """

    data = {"question": question, "document": document, "value": value}
    data.update(kwargs)

    if document:
        _lock_family(document)
    answer = models.Answer.objects.filter(question=question, document=document).first()
    answer = domain_logic.SaveAnswerLogic.get_new_answer(
        data, user, answer, context=context
    )

    return answer


def save_default_answer(
    question: models.Question,
    user: Optional[BaseUser] = None,
    value: Optional[Any] = None,
    **kwargs,
) -> models.Answer:
    """
    Save default_answer for given question.

    Similar to saveDefaultStringAnswer and the likes, it performes upsert.
    :param value: Must match the question type
    """

    data = {"question": question, "value": value}
    data.update(kwargs)

    answer = question.default_answer
    answer = domain_logic.SaveDefaultAnswerLogic.get_new_answer(data, user, answer)

    return answer


@transaction.atomic
def save_document(
    form: models.Form,
    meta: Optional[dict] = None,
    document: Optional[models.Document] = None,
    user: Optional[BaseUser] = None,
) -> models.Document:
    """Save a document for a given form."""

    if meta is None:
        meta = {}

    if not document:
        return domain_logic.SaveDocumentLogic.create(
            {"form": form, "meta": meta}, user=user
        )
    _lock_family(document)

    domain_logic.SaveDocumentLogic.update(
        document, {"form": form, "meta": meta}, user=user
    )
    return document


@transaction.atomic
def remove_answer(
    answer: models.Answer, user: Optional[BaseUser] = None
) -> models.Answer:
    """Remove an answer from a document."""

    _lock_family(answer.document)
    domain_logic.RemoveAnswerLogic.delete(answer, user)
    return answer


@transaction.atomic
def remove_document(
    document: models.Document, user: Optional[BaseUser] = None
) -> models.Document:
    """Remove a document."""

    _lock_family(document)
    domain_logic.RemoveDocumentLogic.delete(document, user=user)
    return document


def copy_form(
    source: models.Form,
    slug: str,
    name: Optional[str] = None,
    description: Optional[str] = None,
    is_published: Optional[bool] = None,
    user: Optional[BaseUser] = None,
    snapshot_id: Optional[int] = None,
) -> models.Form:
    """Copy a form."""

    return domain_logic.CopyFormLogic.copy(
        {
            "source": source,
            "snapshot_id": source.snapshot_id if snapshot_id is None else snapshot_id,
            "slug": slug,
            "name": name,
            "description": description,
            "is_published": False if is_published is None else is_published,
        },
        user=user,
    )
