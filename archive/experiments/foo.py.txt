from csv import DictReader
from uuid import uuid4

def append_place_id(place_obj, place_id):
  place_obj.update({"place_id": str(uuid4())})
  return place_obj

def conv_list_to_dict(obj):
  return { item["name"]: dict(item) for item in obj }
  
def make_relation(obj):
  rel = []
  for item in obj.values():
    #print(item["parent"])
    if item["parent"]:
      parent_place_id = obj[item["parent"]]["place_id"]
      #print(parent_place_id)
      item.update({"parent_place_id": parent_place_id})
    #else:
      # 本当はproject_idをparent_place_idに設定する必要がある
    rel.append(item)
  return rel

def make_hierarchy(item, array):
  if "parent_place_id" in i:
    for i in array:
      if "parent_place_id" in i:
        if item["place_id"] == i["parent_place_id"]:
          ret = make_hierarchy(i, array)
          if ret:
            return item["place_id"] + "#" + ret
          return item["place_id"]

#with open("./template.csv", "r") as csv_file:
csv_file = open("./template.csv", "r")
f = DictReader(csv_file, delimiter=",", doublequote=True, quotechar='"', skipinitialspace=True)

# parentでソートした一覧を生成
sorted_items = sorted(f, key=lambda x: x["parent"])

# parent_idを生成して一覧に足す
mapped_items = map(lambda x: append_place_id(x, str(uuid4())), sorted_items)
dict_items = conv_list_to_dict(mapped_items)

ret = make_relation(dict_items)

for item in ret:
  print(make_hierarchy(item, ret))

print("#################################")

for item in ret:
  print(item)

"""
def make_hierarchy(i, array)
   if i[name] in array:
     ret, h = make(array.i, array)
     ret.parent = i.placeid
     return ret, i.hierarchy + # + h
   else:
     return i

for item in array:
  ret = make(item, all)
"""

"""
for item in sorted(f, key=lambda x: x["parent"]):
  print(item["parent"])


{
  "PutRequest": {
    "Item": {
    }
  }
}

def make_obj(project_id, parent_place_id, hierarchy, name):
  new_id = str(uuid4())
  now = datetime.now().isoformat()
  return {
      "project_id": project_id,
      "place_id": new_id,
      "parent_place_id": parent_place_id,
      "hierarchy": hierarchy,
      "name": str(name),
      "photos": {
        "required": 0,
        "results": {
          "before": 0,
          "after": 0,
        }
      },
      "created_at": now,
      "updated_at": now
  }



if self.__projects_id:
  keys = [ {"project_id": item} for item in self.__projects_id ]
else:
  keys = [ {"project_id": item["project_id"]} for item in self.list_projects_id() ]

if not keys:
  return

# TODO: 10個以上のプロジェクトを持つ時の想定をする
return self.dynamodb.batch_write_item(
  RequestItems = {
    "Projects": {
      "Keys": keys,
      "ConsistentRead": False
    }
  }
)["Responses"]["Projects"]





def name_sort(obj, key, reverse=False):
  return sorted(
      obj,
      key=lambda item: datetime.strptime(item[key], '%Y-%m-%dT%H:%M:%S.%f'), 
      reverse=reverse
"""
