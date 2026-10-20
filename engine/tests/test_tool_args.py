"""Tool arguments: what models send instead of the schema, taken as they meant it."""

from __future__ import annotations

import pytest

from app.tools.base import ToolError, ToolSpec, arr, obj


async def _noop(args, ctx):  # pragma: no cover - never called
    return ""


SPEC = ToolSpec("t", "", obj({"rows": arr(arr({})), "opts": obj({"x": {"type": "integer"}})}),
                _noop)


@pytest.mark.parametrize(("sent", "meant"), [
    ('[[1, null], [2, "b"]]', [[1, None], [2, "b"]]),
    ('[1, null], [2, null]', [[1, None], [2, None]]),  # the outer brackets left off
])
def test_lists_sent_as_text(sent, meant):
    args = {"rows": sent}
    SPEC.validate(args)
    assert args["rows"] == meant


def test_text_that_isnt_a_list_still_fails():
    with pytest.raises(ToolError, match="invalid arguments at rows"):
        SPEC.validate({"rows": "[1, 2"})
    with pytest.raises(ToolError):
        SPEC.validate({"opts": '{"x": 1}, {"x": 2}'})
