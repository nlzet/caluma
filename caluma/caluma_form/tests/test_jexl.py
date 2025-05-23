import gc
import itertools
from collections import Counter
from contextlib import nullcontext as does_not_raise

import pytest

from .. import models, structure, validators
from ..jexl import QuestionJexl, QuestionMissing
from ..models import Question


@pytest.mark.parametrize(
    "expression,num_errors",
    [
        # correct case
        ("\"question-slug\"|answer|mapby('key')", 0),
        # invalid subject type
        ("100|answer", 1),
        # two invalid subject types
        ('["test"]|answer || 1.0|answer', 2),
        # invalid operator
        ("'question-slug1'|answer &&& 'question-slug2'|answer", 1),
    ],
)
def test_question_jexl_validate(expression, num_errors):
    jexl = QuestionJexl(field=None)
    assert len(list(jexl.validate(expression))) == num_errors


@pytest.mark.parametrize(
    "expression,result",
    [
        ("[1,2] intersects [2,3]", True),
        ("[1,2] intersects [3,4]", False),
        ("[] intersects []", False),
        ("[1] intersects []", False),
        ("['foo'] intersects ['bar', 'bazz']", False),
        ("['foo'] intersects ['foo', 'foo']", True),
        ("[1] intersects [1] && [2] intersects [2]", True),
        ("[2] intersects [1] + [2]", True),
    ],
)
def test_intersects_operator(expression, result):
    assert QuestionJexl(field=None).evaluate(expression) == result


@pytest.mark.parametrize("form__slug", ["f-main-slug"])
def test_jexl_form(db, form):
    # TODO: this test is not really meaningful anymore with the new
    # form jexl classes
    answer_by_question = {
        "a1": {"value": "A1", "form": form},
        "b1": {"value": "B1", "form": form},
    }

    assert (
        QuestionJexl(
            field=None, context={"answers": answer_by_question, "form": form.slug}
        ).evaluate("form")
        == "f-main-slug"
    )


def test_all_deps_hidden(db, form, document_factory, form_question_factory):
    q1 = form_question_factory(form=form, question__is_hidden="true").question
    q2 = form_question_factory(
        form=form,
        question__is_hidden=f"'{q1.slug}'|answer=='blah'",
        question__is_required=f"'{q1.slug}'|answer=='blah'",
    ).question
    document = document_factory(form=form)

    struc = structure.FieldSet(document)
    field = struc.get_field(q2.slug)
    # Q2 is dependent on Q1 for it's hidden and required properties.
    # Since Q1 is hidden, Q2 can't really evaluate both expressions.
    # Therefore, is_hidden should evaluate to True, but
    # is_required should evaluate to False
    assert field.is_hidden()
    assert not field.is_required()


@pytest.mark.parametrize("fq_is_hidden", ["true", "false"])
def test_indirectly_hidden_dependency(
    db,
    form_question_factory,
    form_factory,
    question_factory,
    document_factory,
    answer_factory,
    info,
    fq_is_hidden,
):
    # Questions can not only be hidden by evaluating their is_hidden expression
    # but also if they're part of a subform, where the containing form question
    # is hidden.
    #
    # Showcase form to demonstrate this (f:form, q:question, d:document, a:answer):
    # f:topform
    #    q:formquestionbranch - hidden=false
    #      f:subformbranch
    #         q:depquestion2 - required = 'subquestion2'|answer=='bluh'
    #    q:formquestion - hidden=variable
    #      f:subform
    #         q:subquestion1 - hidden=false
    #         q:subquestion2 - hidden=false
    #   q:depquestion1 - required = 'subquestion1'|answer=='blah'
    #
    # d:topdoc f=topform
    #    a:subanswer1 q=subquestion1, value='blah'
    #    a:subanswer2 q=subquestion2, value='bluh'
    #    (depquestion1 and depquestion2 have no answers, as they're indirectly not required)
    #
    # Note: The above structure shows the actual "feature" case.
    # We also test with `subquestion` not hidden, to ensure both ways
    # work correctly

    # Build the form first...
    topform = form_factory(slug="top")
    subformbranch = form_factory(slug="subform")
    subform = form_factory(slug="subformbranch")

    formquestion = question_factory(
        type=Question.TYPE_FORM,
        is_hidden=fq_is_hidden,
        sub_form=subform,
        slug="topformquestion",
    )
    formquestionbranch = question_factory(
        type=Question.TYPE_FORM,
        is_hidden="false",
        sub_form=subformbranch,
        slug="topformquestionbranch",
    )
    subquestion1 = question_factory(
        type=Question.TYPE_INTEGER, is_hidden="false", slug="subquestion1"
    )
    subquestion2 = question_factory(
        type=Question.TYPE_INTEGER, is_hidden="false", slug="subquestion2"
    )
    depquestion1 = question_factory(
        type=Question.TYPE_INTEGER,
        is_required="'subquestion1'|answer=='blah'",
        slug="depquestion1",
    )
    depquestion2 = question_factory(
        type=Question.TYPE_INTEGER,
        is_required="'subquestion2'|answer=='bluh'",
        slug="depquestion2",
    )
    form_question_factory(form=topform, question=formquestion)
    form_question_factory(form=topform, question=formquestionbranch)
    form_question_factory(form=topform, question=depquestion1)

    form_question_factory(form=subformbranch, question=depquestion2)

    form_question_factory(form=subform, question=subquestion1)
    form_question_factory(form=subform, question=subquestion2)

    # ... then build the document
    topdoc = document_factory(form=topform)
    answer_factory(document=topdoc, question=subquestion1, value="blah")
    answer_factory(document=topdoc, question=subquestion2, value="bluh")

    validator = validators.DocumentValidator()

    if fq_is_hidden == "true":
        # parent is hidden, required question cannot be
        # shown, thus is implicitly hidden
        validator.validate(topdoc, info)
        assert True  # above did not fail
    else:
        with pytest.raises(validators.CustomValidationError):
            validator.validate(topdoc, info)


def test_reference_missing_question(
    db, form_question_factory, form_factory, question_factory, document_factory, info
):
    topform = form_factory(slug="top")

    depquestion = question_factory(
        type=Question.TYPE_INTEGER, is_hidden="'subquestion-missing'|answer=='blah'"
    )
    form_question_factory(form=topform, question=depquestion)

    # ... then build the document
    topdoc = document_factory(form=topform)
    validator = validators.DocumentValidator()

    with pytest.raises(QuestionMissing):
        validator.validate(topdoc, info)


@pytest.mark.parametrize(
    "question,expr,expectation,document_owner",
    [
        ("sub_question", "info.case.form == 'top_form'", True, "root_case"),
        ("sub_question", "info.case.form == 'child-case-form'", True, "child_case"),
        (
            "sub_question",
            "info.case.workflow == 'child-case-workflow'",
            True,
            "child_case",
        ),
        ("sub_question", "info.case.root.form == 'main-case-form'", True, "child_case"),
        (
            "sub_question",
            "info.case.root.workflow == 'main-case-workflow'",
            True,
            "child_case",
        ),
        ("sub_question", "info.form == 'sub_form'", True, "child_case"),
        ("sub_question", "info.formMeta.level == 1", True, "child_case"),
        ("sub_question", "info.formMeta['is-top-form']", False, "child_case"),
        (
            "sub_question",
            "info.formMeta['non-existent-key'] == null",
            True,
            "child_case",
        ),
        ("sub_question", "info.parent.form == 'top_form'", True, "child_case"),
        ("sub_question", "info.parent.formMeta.level == 0", True, "child_case"),
        ("sub_question", "info.parent.formMeta['is-top-form']", True, "child_case"),
        ("column", "info.case.form == 'top_form'", True, "root_case"),
        ("column", "info.parent.form == 'top_form'", True, "child_case"),
        ("column", "info.parent.formMeta.level == 0", True, "child_case"),
        ("column", "info.parent.formMeta['is-top-form']", True, "child_case"),
        ("column", "info.root.form == 'top_form'", True, "child_case"),
        ("column", "info.root.formMeta.level == 0", True, "child_case"),
        ("column", "info.root.formMeta['is-top-form']", True, "child_case"),
        ("column", "info.case.form == 'child-case-form'", True, "child_case"),
        ("column", "info.case == null", True, None),
    ],
)
def test_new_jexl_expressions(
    question,
    expr,
    expectation,
    document_owner,
    info,
    form_and_document,
    case_factory,
    work_item_factory,
):
    """Evaluate a JEXL expression in the context of a full document.

    The given JEXL expression is evaluated in the context of the given
    question within a structured document. The expression's value (boolean)
    is then returned. The document is generated with the following structure:

    * form: top_form
       * question: top_question
       * question: table
           * row_form: row_form
               * question: column
       * question: form_question
           * sub_form: sub_form
               * question: sub_question
    """

    form, document, questions, answers = form_and_document(
        use_table=True, use_subform=True
    )

    if document_owner == "child_case":
        root_case = case_factory(
            workflow__slug="main-case-workflow", document__form__slug="main-case-form"
        )
        child_case = case_factory(
            workflow__slug="child-case-workflow",
            document__form__slug="child-case-form",
            family=root_case,
        )
        work_item_factory(case=root_case, child_case=child_case)
        work_item_factory(case=child_case, document=document)
    elif document_owner == "root_case":
        root_case = case_factory(workflow__slug="main-case-workflow", document=document)

    document.refresh_from_db()

    # Expression test method: we delete an answer and set it's is_hidden
    # to an expression to be tested. If the expression evaluates to True,
    # the question is hidden and the missing answer is not a problem, thus
    # we don't get a validation error. If it evaluates to False, the missing answer
    # causes an exception, and we will know.
    answers[question].delete()
    questions[question].is_hidden = expr
    questions[question].save()

    def do_check():
        validator = validators.DocumentValidator()
        try:
            validator.validate(document, info)
            return True
        except validators.CustomValidationError:
            return False

    # The following few lines are just to generate a more useful assertion
    # message in case the expectations are not met. They're not influencing
    # the actual result
    context = []
    from caluma.caluma_form import structure

    fieldset = structure.FieldSet(document)
    structure.print_structure(
        fieldset,
        print_fn=lambda *x: context.append(" ".join([str(f) for f in x])),
    )
    ctx_str = "\n".join(context)
    result = do_check()
    relevant_field = fieldset.find_all_fields_by_slug(question)[0]
    assert result == expectation, (
        f"Expected JEXL({expr}) on question '{question}' to evaluate "
        f"to {expectation} but got {result}; context was: \n{ctx_str};\n"
        f"field-local `info` context: {relevant_field.get_local_info_context()}"
    )


def test_answer_transform_on_hidden_question(info, form_and_document):
    # Bug test: When a JEXL expression references two answers, and one of them
    # is hidden, the `answer` transform must return None for the hidden question
    # to ensure correct evaluation.

    form, document, questions, answers = form_and_document(
        use_table=True, use_subform=True
    )

    # the "sub_question" question is hidden (no magic). This means
    # it's "answer" transform should always return None
    questions["sub_question"].is_hidden = "true"
    questions["sub_question"].is_required = "true"
    questions["sub_question"].save()

    # sub_question and top_question are referenced in a JEXL expression later on
    answers["sub_question"].value = "hello"
    answers["sub_question"].save()
    answers["top_question"].value = "xyz"
    answers["top_question"].save()

    # the `column` question is used to evaluate the expression via `is_required`:
    # The required state depends on two questions, so we can check the expression's
    # result by checking whether the validator throws an exception.
    answers["column"].delete()

    # expression references two other questions, so it will still be evaluated
    # even if one question is hidden.
    # This specific expression evaluates to `True` only if the `answer` transform
    # works correctly
    questions[
        "column"
    ].is_required = "'sub_question'|answer == null && 'top_question'|answer=='xyz'"
    questions["column"].is_hidden = "false"
    questions["column"].save()

    # We're just validating the assumptions here for better understanding of
    # the test situation
    assert structure.list_document_structure(document, method=repr) == [
        " FieldSet(q=(root), f=top_form)",
        "    ValueField(q=top_question, v=xyz)",
        "    RowSet(q=table, f=row_form)",
        "       FieldSet(q=table, f=row_form)",
        "          ValueField(q=column, v=None)",  # this is our test field
        "    FieldSet(q=form, f=sub_form)",
        "       ValueField(q=sub_question, v=None)",
    ]

    validator = validators.DocumentValidator()
    with pytest.raises(validators.CustomValidationError):
        validator.validate(document, info)

    # Counter-Test to the `expect_fail` case: Ensure the answer transform still works
    questions["column"].is_required = "'sub_question'|answer == 'hello'"
    questions["column"].is_hidden = "false"
    questions["column"].save()

    assert validator.validate(document, info) is None


@pytest.mark.parametrize(
    "question_type,expected_value",
    [
        (Question.TYPE_MULTIPLE_CHOICE, []),
        (Question.TYPE_INTEGER, None),
        (Question.TYPE_FLOAT, None),
        (Question.TYPE_DATE, None),
        (Question.TYPE_CHOICE, None),
        (Question.TYPE_TEXTAREA, None),
        (Question.TYPE_TEXT, None),
        (Question.TYPE_TABLE, []),
        (Question.TYPE_FILES, None),
        (Question.TYPE_DYNAMIC_CHOICE, None),
        (Question.TYPE_DYNAMIC_MULTIPLE_CHOICE, []),
        # Those should not appear in a JEXL answer transform
        # (Question.TYPE_FORM,None),
        # (Question.TYPE_STATIC,None),
    ],
)
def test_answer_transform_on_hidden_question_types(
    info,
    form_and_document,
    document_factory,
    answer_factory,
    question_type,
    expected_value,
    form_factory,
):
    form, document, questions, answers = form_and_document(
        use_table=True, use_subform=True
    )
    table = questions["table"]
    row_form = table.row_form
    row_doc = document_factory(form=row_form, family=document)
    answer_factory(document=row_doc, question=questions["column"])
    answers["table"].documents.add(row_doc)

    questions["form"].is_hidden = (
        f"'top_question'|answer == {expected_value}"
        " && 'table'|answer|mapby('column')[0]"
        " && 'table'|answer|mapby('column')[1]"
    )
    questions["form"].save()

    questions["top_question"].is_hidden = "true"
    questions["top_question"].type = question_type
    questions["top_question"].row_form = (
        form_factory() if question_type == Question.TYPE_TABLE else None
    )
    questions["top_question"].save()

    struc = structure.FieldSet(document)
    field = struc.get_field("form")

    assert field.is_hidden()


@pytest.mark.parametrize(
    "jexl_field,expr",
    [
        ("is_required", "('column'|answer == 5)"),
        ("is_hidden", "('column'|answer > 10)"),
    ],
)
def test_answer_transform_in_tables(
    info,
    form_and_document,
    form_question_factory,
    jexl_field,
    expr,
    answer_document_factory,
):
    form, document, questions, answers = form_and_document(
        use_table=True, use_subform=False
    )
    column_a = answers["column"]
    column_a.value = 11
    column_a.save()

    table_question = questions["table"]

    col2_question = form_question_factory(
        **{
            "form": table_question.row_form,
            "question__slug": "column2",
            "question__is_hidden": "false",
            "question__is_required": "true",
            # this overrwrites above "default values"
            f"question__{jexl_field}": expr,
        }
    ).question
    assert getattr(col2_question, jexl_field) == expr

    table_answer = answers["table"]

    row2_doc = answer_document_factory(
        answer=table_answer, document__form=table_question.row_form, sort=10
    ).document

    # Second row has an answer that triggers the transform and implies a validation error.
    # This will wrongfully succeed if the answer value comes from the wrong row
    row2_doc.answers.create(question_id="column", value=5)

    validator = validators.DocumentValidator()
    # we expect this to fail, as in the second row, the 'column' answer is 5,
    # so 'column2' should be required
    with pytest.raises(validators.CustomValidationError):
        validator.validate(document, info)


def test_answer_transform_hidden_table_cell(
    info,
    form_and_document,
    form_question_factory,
):
    form, document, questions, answers = form_and_document(
        use_table=True, use_subform=False
    )
    column_a = answers["column"]
    column_a.value = 11.0
    column_a.save()

    table_question = questions["table"]

    # create column that is always hidden
    col2_question = form_question_factory(
        form=table_question.row_form,
        question__slug="column2",
        question__is_hidden="true",
        question__is_required="false",
    ).question

    # create question that depends on the value of the previously created hidden
    # column
    form_question_factory(
        form=form,
        question__is_hidden=f"!('yes' in '{table_question.slug}'|answer|mapby('{col2_question.slug}'))",
        question__is_required="true",
    )

    # answer the hidden column with the value that would make the dependant
    # question visible
    row = answers["table"].documents.first()
    row.answers.create(question_id=col2_question.slug, value="yes")

    # validation should suceed since the check question should be hidden even
    # though the dependant column has the proper value but is hidden
    validator = validators.DocumentValidator()
    validator.validate(document, info)

    assert True


def test_is_hidden_neighboring_table(
    info, form_question_factory, form_factory, form_and_document
):
    """
    Test a more complicated, nested setup.

    The structure looks like this:

    * form: top_form
       * question: top_question
       * question: form_question
           * sub_form: sub_form
               * question: sub_question
           * question: table
               * row_form: row_form
                   * question: column

       * question: neighbor_form_question
           * neighbor_form
               * question: neighbor_sub_question

    The is_hidden on neighbor_sub_question references the column question.
    """

    form, document, questions, answers = form_and_document(
        use_table=True, use_subform=True
    )

    # delete table answer documents
    answers["table"].documents.first().delete()
    assert not models.Document.objects.filter(form_id="row_form").exists()
    assert not models.Answer.objects.filter(question_id="column").exists()

    form_q = questions["form"]
    table_q = questions["table"]

    table_q.forms.remove(form)
    table_q.forms.add(form_q.sub_form)

    neighbor_form = form_factory()
    neighbor_sub_question = form_question_factory(
        form=neighbor_form,
        question__is_hidden="!('foo' in 'table'|answer|mapby('column'))",
        question__is_required=True,
    ).question

    form_question_factory(
        form=form, question__type=Question.TYPE_FORM, question__sub_form=neighbor_form
    )

    struc = structure.FieldSet(document)

    field = struc.get_field(neighbor_sub_question.slug)

    assert field.is_hidden()

    validator = validators.DocumentValidator()
    assert neighbor_sub_question not in validator.visible_questions(document)

    with pytest.raises(validators.CustomValidationError):
        validator.validate(document, info)


def test_optional_answer_transform(info, form_and_document):
    form, document, questions, answers = form_and_document(
        use_table=False, use_subform=False
    )

    questions["top_question"].is_hidden = "'nonexistent'|answer('default') == 'default'"
    questions["top_question"].save()

    validator = validators.DocumentValidator()
    assert validator.validate(document, info) is None

    questions["top_question"].is_hidden = "'nonexistent'|answer(null) == null"
    questions["top_question"].save()

    assert validator.validate(document, info) is None

    questions["top_question"].is_hidden = "'nonexistent'|answer == 'default'"
    questions["top_question"].save()

    with pytest.raises(QuestionMissing):
        validator.validate(document, info)


def _gc_object_counts_by_type():
    # We sort, so the insertion order into the counter is something
    # sorta-kinda useful. Otherwise, we'd get a random-ish insertion order
    # that makes looking at the counter difficult (for humans)
    o_types_sorted = sorted(
        ((type(o).__module__, type(o).__qualname__) for o in gc.get_objects())
    )
    return Counter(o_types_sorted)


def test_jexl2_memory_leaks(info, form_and_document):
    """Ensure our JEXL and form structures do not leak memory.

    We use quite a lot of forward and back references between objects, and
    those need to be cleaned up when the work is done, as otherwise we may
    use up a lot of memory over time.

    Measure the GC stats before and after a simple document validation call,
    and if anything is left over that wasn't there before, we consider it a
    bug.
    """
    form, document, questions, answers = form_and_document(
        use_table=True, use_subform=True
    )

    gc.collect()

    stats_before = _gc_object_counts_by_type()
    stats_after = Counter()  # so it's already there

    validator = validators.DocumentValidator()
    validator.validate(document, info)

    # Delete the validator, and with this, everything should be gone
    # To validate the test, comment-out this line, and the test should fail
    del validator

    gc.collect()
    stats_after = _gc_object_counts_by_type()

    # We are not concerned about django's or any other module's leaks,
    # just our own
    relevant_diffs = {}
    for mod, cls in sorted(
        set(itertools.chain(stats_before.keys(), stats_after.keys()))
    ):
        before = stats_before[mod, cls]
        after = stats_after[mod, cls]
        if mod.startswith("caluma"):
            # Only look at caluma data. Graphene and Python interna are
            # hard to keep track of (and not our responsibility to fix)
            relevant_diffs[mod, cls] = (before, after)

    # Should be empty
    for (mod, cls), (before, after) in relevant_diffs.items():
        leaked = after - before
        assert leaked == 0, (
            f"Leak detected: {mod}.{cls} was {before} before test run, "
            f"but {after} afterwards (leaked {leaked} objects)"
        )


@pytest.mark.parametrize(
    "do_raise, expectation",
    [
        (True, pytest.raises(RuntimeError)),
        (False, does_not_raise()),
    ],
)
def test_evaluate_error_no_raise(info, form_and_document, do_raise, expectation):
    """When passing raise_on_error=False, ensure it's honored."""
    form, document, questions, answers = form_and_document(
        use_table=False, use_subform=False
    )

    # Syntactically valid, but should cause a type error
    questions["top_question"].is_hidden = "1234 / 0 == 1"
    questions["top_question"].save()

    fieldset = structure.FieldSet(document)

    top_q_field = fieldset.get_field("top_question")

    with expectation:
        assert top_q_field.is_hidden(raise_on_error=do_raise) is None
