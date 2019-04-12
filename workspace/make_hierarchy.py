import os, sys

class MakeHierarchy():
  places = None
  
  def __init__(self):
    pass

  def set_places(self, places):
    self.places = places

  def get_root_places(self):
    return [ place for place in self.places if not place["parent"] ]

  def get_leaf_places(self):
    return [ place for place in self.places if place["parent"] ]
