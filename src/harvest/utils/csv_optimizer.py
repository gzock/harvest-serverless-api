"""Validate the complete import graph before constructing any database writes."""

from uuid import uuid4
from collections import Counter

from harvest.drivers.csv_driver import CsvDriver
from harvest.exception import (
    DuplicatePlaceNameExistsError,
    MaxHierarchyExceededError,
    ParentPlaceNotFoundError,
)

MAX_HIERARCHY_NUM = 5


class CsvOptimizer:
    def __init__(self, filename=None, items=None):
        self.project_id = None
        if filename:
            self.items = CsvDriver().open_csv(filename)
        elif items is not None:
            self.items = [dict(item) for item in items]
        else:
            self.items = []

    def set_project_id(self, project_id):
        self.project_id = project_id

    def optimize(self):
        if not self.project_id or not self.items:
            raise ValueError("Project and CSV records are required")
        places = [dict(item) for item in self.items if item["type"] == "place"]
        targets = [dict(item) for item in self.items if item["type"] == "target"]
        duplicates = [
            name for name, count in Counter(item["name"] for item in places).items() if count > 1
        ]
        if duplicates:
            raise DuplicatePlaceNameExistsError(sorted(duplicates))
        by_name = {place["name"]: place for place in places}
        for place in places:
            place.update(project_id=self.project_id, place_id=str(uuid4()), photos_required_num=0)
        for target in targets:
            target.update(project_id=self.project_id, target_id=str(uuid4()))
        for item in places + targets:
            if item["parent"] and item["parent"] not in by_name:
                raise ParentPlaceNotFoundError()
            item["parent_place_id"] = (
                by_name[item["parent"]]["place_id"] if item["parent"] else self.project_id
            )
        ancestors: dict[str, list[str]] = {}

        def visit(name: str, visiting: set[str]) -> list[str]:
            if name in visiting:
                raise ValueError("CSV place hierarchy contains a cycle")
            if name in ancestors:
                return ancestors[name]
            place = by_name[name]
            chain = (
                visit(place["parent"], visiting | {name}) + [place["parent"]]
                if place["parent"]
                else []
            )
            if len(chain) + 1 > MAX_HIERARCHY_NUM:
                raise MaxHierarchyExceededError()
            ancestors[name] = chain
            return chain

        for place in places:
            chain = visit(place["name"], set())
            place["hierarchy"] = "#".join(
                [self.project_id] + [by_name[name]["place_id"] for name in chain]
            )
        for target in targets:
            if target["parent"]:
                for name in ancestors[target["parent"]] + [target["parent"]]:
                    by_name[name]["photos_required_num"] += 1
        self.places, self.targets = places, targets
        return places, targets
