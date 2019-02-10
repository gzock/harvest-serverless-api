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

  place.set_project_id("18873c7a-0fcb-4d85-b067-d9ed7dc184cc")
  #place.create("root_place")
  #print( place.list_children("9cb83d38-4950-4709-ad9d-7eeed2664ddc") )
  place.create("first_place_2", "7974dde1-ec16-4822-a499-ff73fd74d5cb")
  #print( place.list_children("8003532a-2122-44ff-95c2-5e5276b45453") )
  
  #place.set_place_id("f58d7b86-0b4a-442f-9ee1-79635b43b4bf")
  #place.update_name("first_place_renamed")
  #print( place.show("f58d7b86-0b4a-442f-9ee1-79635b43b4bf") )
  
  #place.set_place_id("7f883379-1291-49e5-8030-9827cce54a8f")
  #place.delete()

  #place.update_name("place_3")
  #place.update_users(["user1", "user2", "user3"])
  #place.delete()

  #print( place.list_places() ) 

except Exception as e:
    print(e)
    raise e

