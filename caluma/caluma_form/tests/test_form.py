import pytest
from django.apps import apps
from django.db import connection
from django.utils import translation
from graphql_relay import to_global_id

from ...caluma_core.relay import extract_global_id
from ...caluma_core.tests import extract_serializer_input_fields
from ...caluma_snapshot.migration_utils import suffix_initial_snapshot_ids
from ...caluma_snapshot.models import Snapshot
from .. import models, validators
from ..api import copy_form
from ..serializers import SaveFormSerializer


@pytest.mark.parametrize(
    "form__description,form__name,question__type",
    [("First result", "1st", models.Question.TYPE_FLOAT)],
)
def test_query_all_forms(
    db,
    snapshot,
    form,
    form_factory,
    form_question,
    form_question_factory,
    question,
    schema_executor,
):
    form_factory(name="3rd", description="Second result")
    form_factory(name="2nd", description="Second result")
    form_question_factory(form=form)

    query = """
        query AllFormsQuery($name: String, $question: String, $order: [FormOrderSetType]) {
          allForms(filter: [{name: $name}], order: $order) {
            edges {
              node {
                id
                slug
                name
                description
                meta
                questions(filter: [{ search: $question }]) {
                  edges {
                    node {
                      id
                      slug
                      label
                    }
                  }
                }
              }
            }
          }
        }
    """

    result = schema_executor(
        query,
        variable_values={
            "question": str(question.label),
            "order": [
                {"attribute": "NAME", "direction": "ASC"},
                {"attribute": "CREATED_AT", "direction": "ASC"},
            ],
        },
    )

    assert not result.errors
    snapshot.assert_match(result.data)


def test_optional_filter(db, form_factory, question, schema_executor):
    form_factory(name="3rd", description="Second result")
    form_factory(name="2nd", description="Second result")

    query = """
        query Form($slugs: [String]) {
          allForms(filter: [{ slugs: $slugs }]) {
            totalCount
          }
        }
    """

    result = schema_executor(
        query,
    )

    assert not result.errors
    assert result.data["allForms"]["totalCount"] == 2


@pytest.mark.parametrize(
    "suffix_v1,expected_pks",
    [
        (False, ("test-form", "test-question", "test-form.test-question")),
        pytest.param(
            True,
            ("test-form:1", "test-question:1", "test-form.test-question:1"),
            marks=pytest.mark.snapshot_v1_suffix,
        ),
    ],
)
def test_snapshot_definition_primary_keys(
    db, settings, form_question_factory, question_factory, suffix_v1, expected_pks
):
    assert settings.CALUMA_SNAPSHOT_V1_SUFFIX is suffix_v1

    membership = form_question_factory(
        id="ignored-membership",
        form__id="ignored-form",
        form__slug="test-form",
        question__id="ignored-question",
        question__slug="test-question",
    )
    assert (membership.form_id, membership.question_id, membership.pk) == expected_pks
    assert membership.snapshot_id == 1

    membership.form.slug = "changed-form"
    with pytest.raises(ValueError, match="Snapshot identities"):
        membership.form.save()
    membership.form.refresh_from_db()

    Snapshot.objects.create(pk=3)
    other_form = models.Form.objects.create(slug="other-form", snapshot_id=3)
    with pytest.raises(ValueError, match="same snapshot"):
        models.Question.objects.create(
            slug="cross-snapshot-question",
            type=models.Question.TYPE_TABLE,
            row_form=other_form,
        )

    Snapshot.objects.create(pk=2)
    next_membership = form_question_factory(
        form__slug="test-form",
        form__snapshot_id=2,
        question__slug="test-question",
        question__snapshot_id=2,
    )
    assert (
        next_membership.form_id,
        next_membership.question_id,
        next_membership.pk,
    ) == ("test-form:2", "test-question:2", "test-form.test-question:2")
    assert next_membership.snapshot_id == 2

    question_factory(slug="v2-only", snapshot_id=2)
    validators.QuestionValidator().validate(
        {
            "type": models.Question.TYPE_CALCULATED_FLOAT,
            "calc_expression": "'v2-only'|answer",
            "snapshot_id": 2,
        }
    )
    with pytest.raises(KeyError, match="snapshot_id"):
        validators.QuestionValidator().validate(
            {
                "type": models.Question.TYPE_CALCULATED_FLOAT,
                "calc_expression": "'v2-only'|answer",
            }
        )

    if not suffix_v1:
        with connection.schema_editor() as editor:
            suffix_initial_snapshot_ids(apps, editor)
        assert models.FormQuestion.objects.get(pk=membership.pk).form_id == "test-form"

        settings.CALUMA_SNAPSHOT_V1_SUFFIX = True
        with connection.schema_editor() as editor:
            suffix_initial_snapshot_ids(apps, editor)
        migrated = models.FormQuestion.objects.get(pk="test-form.test-question:1")
        assert (migrated.form_id, migrated.question_id) == (
            "test-form:1",
            "test-question:1",
        )
        assert models.FormQuestion.objects.get(pk=next_membership.pk).snapshot_id == 2


@pytest.mark.parametrize("language_code", ("en", "de"))
@pytest.mark.parametrize("form__description", ("some description text", ""))
def test_save_form(db, snapshot, form, settings, schema_executor, language_code):
    query = """
        mutation SaveForm($input: SaveFormInput!) {
          saveForm(input: $input) {
            form {
              id
              slug
              name
              meta
            }
            clientMutationId
          }
        }
    """

    inp = {"input": extract_serializer_input_fields(SaveFormSerializer, form)}
    with translation.override(language_code):
        result = schema_executor(query, variable_values=inp)

    assert not result.errors
    snapshot.assert_match(result.data)


def test_save_form_created_as_admin_user(db, form, admin_schema_executor, admin_user):
    query = """
        mutation SaveForm($input: SaveFormInput!) {
          saveForm(input: $input) {
            form {
              createdByUser
            }
          }
        }
    """

    inp = {"input": extract_serializer_input_fields(SaveFormSerializer, form)}
    form.delete()  # test creation of form
    result = admin_schema_executor(query, variable_values=inp)

    assert not result.errors
    assert result.data["saveForm"]["form"]["createdByUser"] == admin_user.username


@pytest.mark.parametrize("form__meta", [{"meta": "set"}])
def test_copy_form(db, form, form_question_factory, schema_executor):
    form_question_factory.create_batch(5, form=form)
    query = """
        mutation CopyForm($input: CopyFormInput!) {
          copyForm(input: $input) {
            form {
              slug
            }
            clientMutationId
          }
        }
    """

    inp = {"input": {"source": form.pk, "slug": "new-form", "name": "Test Form"}}
    result = schema_executor(query, variable_values=inp)

    assert not result.errors

    form_slug = result.data["copyForm"]["form"]["slug"]
    assert form_slug == "new-form"
    new_form = models.Form.objects.get(slug=form_slug, snapshot_id=form.snapshot_id)
    assert new_form.name == "Test Form"
    assert new_form.meta == form.meta
    assert new_form.source == form
    assert set(
        models.FormQuestion.objects.filter(form=new_form).values_list(
            "question", flat=True
        )
    ) == set(
        models.FormQuestion.objects.filter(form=form).values_list("question", flat=True)
    )


@pytest.mark.parametrize("form__meta", [{"meta": "set"}])
def test_copy_form_api(db, form, form_question_factory, schema_executor):
    form_question_factory.create_batch(5, form=form)

    new_form = copy_form(source=form, slug="new-form", name="Test Form")

    assert new_form.slug == "new-form"
    assert new_form.pk == "new-form"
    assert new_form.name == "Test Form"
    assert new_form.meta == form.meta
    assert new_form.source == form
    assert set(
        models.FormQuestion.objects.filter(form=new_form).values_list(
            "question", flat=True
        )
    ) == set(
        models.FormQuestion.objects.filter(form=form).values_list("question", flat=True)
    )


def test_add_form_question(db, form, question, form_question_factory, schema_executor):
    form_questions = form_question_factory.create_batch(5, form=form)

    # initialize sorting keys
    for idx, form_question in enumerate(form_questions):
        form_question.sort = idx + 1
        form_question.save()

    query = """
        mutation AddFormQuestion($input: AddFormQuestionInput!) {
          addFormQuestion(input: $input) {
            form {
              questions {
                edges {
                  node {
                    slug
                  }
                }
              }
            }
            clientMutationId
          }
        }
    """

    result = schema_executor(
        query,
        variable_values={
            "input": {
                "form": to_global_id(type(form).__name__, form.pk),
                "question": to_global_id(type(question).__name__, question.pk),
            }
        },
    )

    assert not result.errors
    questions = result.data["addFormQuestion"]["form"]["questions"]["edges"]
    assert len(questions) == 6
    assert questions[-1]["node"]["slug"] == question.slug


def test_remove_form_question(
    db, form, form_question, question, snapshot, schema_executor
):
    query = """
        mutation RemoveFormQuestion($input: RemoveFormQuestionInput!) {
          removeFormQuestion(input: $input) {
            form {
              questions {
                edges {
                  node {
                    slug
                  }
                }
              }
            }
            clientMutationId
          }
        }
    """

    result = schema_executor(
        query,
        variable_values={
            "input": {
                "form": to_global_id(type(form).__name__, form.pk),
                "question": to_global_id(type(question).__name__, question.pk),
            }
        },
    )

    assert not result.errors
    snapshot.assert_match(result.data)


def test_reorder_form_questions(db, form, form_question_factory, schema_executor):
    form_question_factory.create_batch(2, form=form)

    query = """
        mutation ReorderFormQuestions($input: ReorderFormQuestionsInput!) {
          reorderFormQuestions(input: $input) {
            form {
              questions {
                edges {
                  node {
                    id
                  }
                }
              }
            }
            clientMutationId
          }
        }
    """

    question_ids = (
        form.questions.order_by("slug").reverse().values_list("pk", flat=True)
    )
    result = schema_executor(
        query,
        variable_values={
            "input": {
                "form": to_global_id(type(form).__name__, form.pk),
                "questions": [
                    to_global_id(models.Question.__name__, question_id)
                    for question_id in question_ids
                ],
            }
        },
    )

    assert not result.errors
    result_questions = [
        extract_global_id(question["node"]["id"])
        for question in result.data["reorderFormQuestions"]["form"]["questions"][
            "edges"
        ]
    ]

    assert result_questions == list(question_ids)


def test_reorder_form_questions_invalid_question(
    db, form, question_factory, schema_executor
):
    invalid_question = question_factory()

    query = """
        mutation ReorderFormQuestions($input: ReorderFormQuestionsInput!) {
          reorderFormQuestions(input: $input) {
            form {
              questions {
                edges {
                  node {
                    slug
                  }
                }
              }
            }
            clientMutationId
          }
        }
    """

    result = schema_executor(
        query,
        variable_values={
            "input": {
                "form": to_global_id(type(form).__name__, form.pk),
                "questions": [
                    to_global_id(models.Question.__name__, invalid_question.pk)
                ],
            }
        },
    )

    assert result.errors


def test_reorder_form_questions_duplicated_question(
    db, form, question, form_question, schema_executor
):
    query = """
        mutation ReorderFormQuestions($input: ReorderFormQuestionsInput!) {
          reorderFormQuestions(input: $input) {
            form {
              questions {
                edges {
                  node {
                    slug
                  }
                }
              }
            }
            clientMutationId
          }
        }
    """

    result = schema_executor(
        query,
        variable_values={
            "input": {
                "form": to_global_id(type(form).__name__, form.pk),
                "questions": [question.pk, question.pk],
            }
        },
    )
    assert result.errors
