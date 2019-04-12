import os, sys
from csv import DictReader
from uuid import uuid4

class BatchImporter():
  items =None
  places = None
  targets = None
  
  def __init__(self, filename=None):
    if filename:
      self.items = self.open_csv(filename)
      self.places, self.targets = self.split_types()

  def open_csv(self, filename):
    csv_file = open(filename, "r")
    lines = DictReader(csv_file, delimiter=",", doublequote=True, quotechar='"', skipinitialspace=True)
    return [ line for line in lines ]

  def get_places(self):
    return [ item for item in self.items if item["type"] == "place" ]
    
  def get_targets(self):
    return [ item for item in self.items if item["type"] == "target" ]
    
  def split_types(self):
    return self.get_places(), self.get_targets()

  def get_root_places(self):
    return [ place for place in self.places if not place["parent"] ]

  def get_child_places(self):
    return [ place for place in self.places if place["parent"] ]

  def parent_sort(self, places):
    # parentでソートした一覧を生成
    return sorted(places, key=lambda x: x["parent"])

  def batch_gen_place_id(self):
    return list(map(lambda x: self.append_id(x, "place"), self.places))

  def batch_gen_target_id(self):
    return list(map(lambda x: self.append_id(x, "target"), self.targets))

  def append_id(self, obj, type):
    obj.update({type + "_id": str(uuid4())})
    return obj

  def conv_list_to_dict(obj):
    return { item["name"]: dict(item) for item in obj }
  
  def batch_gen_parent_place_id(self, obj):
    rel = []
    for item in obj:
      parent = list(filter(lambda x: x["name"] == item["parent"], self.items))
      if parent and "place_id" in parent[0]:
        item.update({"parent_place_id": parent[0]["place_id"]})
      else:
        item.update({"parent_place_id": ""})
      rel.append(item)
    return rel

  def get_parent(self, place_id):
    for item in self.places:
      if place_id == item["place_id"]:
        return item
    
  def gen_hierarchy(self, place):
    hierarchy = []
    place_id = place["parent_place_id"]
    for i in range(5):
      parent = self.get_parent(place_id)
      #print("found place: " + str(parent))
      if parent:
        hierarchy.insert(0, parent["place_id"])
        place_id = parent["parent_place_id"]
      else:
        break
    return hierarchy
    
  def optimize(self):
    if not self.places or not self.targets:
      raise ValueError
    
    # parentでソートした一覧を生成
    sorted_places = self.parent_sort(self.places)
    
    # idを生成して足す
    self.places = self.batch_gen_place_id()
    self.targets = self.batch_gen_target_id()
    # parent_place_idを皆に付与
    self.places = self.batch_gen_parent_place_id(self.places)
    self.targets = self.batch_gen_parent_place_id(self.targets)
    
    optimized = []
    for place in self.places:
      #print(place)
      hierarchy = self.gen_hierarchy(place)
      if hierarchy:
        place.update( {"hierarchy": "#".join(hierarchy)} )
      else:
        place.update( {"hierarchy": ""} )
      optimized.append(place)
    return optimized, self.targets

