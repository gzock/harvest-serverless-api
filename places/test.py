#!/usr/bin/env python3

import os, sys
import json

sys.path.append(os.path.join(os.path.dirname(__file__), 'site-packages'))
import boto3
from boto3.dynamodb.conditions import Key, Attr

from harvest import PlaceController

#DYNAMO_HOST = "10.0.2.15"
DYNAMO_HOST = "127.0.0.1"
DYNAMO_PORT = "8000"

try:

  place = PlaceController(DYNAMO_HOST, DYNAMO_PORT)

  place.set_project_id("ea974534-c545-48f2-ac87-e296724298e7")
  place.set_place_id("aaa")
  #place.create("place_3")
  #place.update_name("place_3")
  #place.update_users(["user1", "user2", "user3"])
  #place.delete()

  #print( place.list_places() ) 

except Exception as e:
    print(e)
    raise e

