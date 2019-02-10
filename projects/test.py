#!/usr/bin/env python3

import os, sys
import json

sys.path.append(os.path.join(os.path.dirname(__file__), 'site-packages'))
import boto3
from boto3.dynamodb.conditions import Key, Attr

from harvest import ProjectController
from harvest import RoleController

#DYNAMO_HOST = "10.0.2.15"
DYNAMO_HOST = "127.0.0.1"
DYNAMO_PORT = "8000"

try:

  project = ProjectController(DYNAMO_HOST, DYNAMO_PORT)
  role = RoleController(DYNAMO_HOST, DYNAMO_PORT)
  role.set_user_id("bbb")
  role.set_project_id("ea974534-c545-48f2-ac87-e296724298e7")
  #print( role.list_users() )
  #print( role.list_projects() )
  #print( role.show() )
  #role.create("worker")
  #role.update("guest")
  #role.delete()

  project.set_user_id("aaa")
  #project.create("project_3")
  project.set_project_id("ea974534-c545-48f2-ac87-e296724298e7")
  #project.update_name("project_3")
  #project.update_users(["user1", "user2", "user3"])
  #project.delete()

  print( project.list_projects() ) 
  print( project.list_users() ) 

except Exception as e:
    print(e)
    raise e

