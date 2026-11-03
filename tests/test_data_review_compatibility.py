"""Compatibility regressions found in the November data-path review."""

from unittest.mock import Mock

import pytest

from harvest.controllers.place_controller import PlaceController
from harvest.exception import PlaceHierarchyLimitExceededError, MaxHierarchyExceededError
from harvest.utils.csv_optimizer import CsvOptimizer


@pytest.mark.parametrize("prefix", ["project#", ""])
def test_ui_can_add_sixth_place_under_legacy_or_modern_fifth_parent(prefix):
    controller = PlaceController.__new__(PlaceController)
    controller.project_id, controller.user_id = "project", "actor"
    controller.show = Mock(return_value={"hierarchy": prefix + "one#two#three#four"})
    controller.client = Mock()
    controller.create("Sixth", "fifth")
    child = controller.client.transact_write_items.call_args.kwargs["TransactItems"][-1]["Put"][
        "Item"
    ]
    assert child["hierarchy"] == {"S": "project#one#two#three#four#fifth"}


@pytest.mark.parametrize("prefix", ["project#", ""])
def test_ui_rejects_seventh_place_for_both_stored_hierarchy_formats(prefix):
    controller = PlaceController.__new__(PlaceController)
    controller.project_id, controller.user_id = "project", "actor"
    controller.show = Mock(return_value={"hierarchy": prefix + "one#two#three#four#five"})
    controller.client = Mock()
    with pytest.raises(PlaceHierarchyLimitExceededError):
        controller.create("Seventh", "sixth")
    controller.client.transact_write_items.assert_not_called()


@pytest.mark.parametrize("depth", [5, 6])
def test_csv_keeps_its_distinct_five_place_limit(depth):
    rows = [
        {"parent": str(i - 1) if i else "", "type": "place", "name": str(i)} for i in range(depth)
    ]
    rows.append({"parent": str(depth - 1), "type": "target", "name": "Photo target"})
    optimizer = CsvOptimizer(items=rows)
    optimizer.set_project_id("project")
    if depth == 6:
        with pytest.raises(MaxHierarchyExceededError):
            optimizer.optimize()
    else:
        places, _ = optimizer.optimize()
        assert len(places) == 5
        assert all(place["photos_required_num"] == 1 for place in places)
