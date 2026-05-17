import math

import pytest

from pilotdb.pilot_engine.commons import (
    AGGREGATE, DIV_OPERATOR, FIRST_ELEMENT, MUL_OPERATOR, SECOND_ELEMENT, SUB_OPERATOR,
)
from pilotdb.pilot_engine.utils import aggregate_error_to_page_error, aggregate_error_uniform


def test_mul_error_mapping_matches_paper_table_2_for_block_and_uniform():
    required_error = 0.05
    expected = min(1 - math.sqrt(1 - required_error), math.sqrt(1 + required_error) - 1)
    mapping = [{AGGREGATE: MUL_OPERATOR, FIRST_ELEMENT: "a", SECOND_ELEMENT: "b"}]

    assert aggregate_error_to_page_error(mapping, required_error) == {"a": expected, "b": expected}
    assert aggregate_error_uniform(mapping, required_error) == {"a": expected, "b": expected}


def test_sub_error_mapping_matches_paper_table_2_for_block_and_uniform():
    mapping = [{AGGREGATE: SUB_OPERATOR, FIRST_ELEMENT: "a", SECOND_ELEMENT: "b"}]

    assert aggregate_error_to_page_error(mapping, 0.05) == {"a": 0.05, "b": 0.05}
    assert aggregate_error_uniform(mapping, 0.05) == {"a": 0.05, "b": 0.05}


def test_unsupported_error_mapping_raises_not_implemented_error():
    mapping = [{AGGREGATE: "median"}]

    with pytest.raises(NotImplementedError):
        aggregate_error_to_page_error(mapping, 0.05)
    with pytest.raises(NotImplementedError):
        aggregate_error_uniform(mapping, 0.05)
