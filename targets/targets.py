import os, sys
import json
from botocore.vendored import requests

sys.path.append(os.path.join(os.path.dirname(__file__), 'site-packages'))

import boto3
from boto3.dynamodb.conditions import Key, Attr
import logging

from harvest import RequestDecorator, ProjectDriver

DYNAMO_HOST = "10.0.2.15"
DYNAMO_PORT = "8000"

def lambda_handler(event, context):
  logger = logging.getLogger()
  logger.setLevel(logging.INFO)

  req = RequestDecorator(event)
  user_id = req.get_identity_id()
  logger.info("requested user id: {}".format(user_id))
  username = req.get_username()
  logger.info("requested user name: {}".format(username))
  project_id = req.get_path_param() #uuidの確認
  logger.info("requested project_id: {}".format(project_id))
  logger.info("requested http method: {}".format(req.get_method()))
  
  try:
    projects = ProjectDriver(DYNAMO_HOST, DYNAMO_PORT)
  except Exception as e:
    raise e

  if req.get_method() == "GET":
    if len(project_id):
     ret = projects.get(project_id)
    else:
     ret = projects.get_all(user_id)

  elif req.get_method() == "POST":
    #ret = projects.set(user_id) #set? add?
     ret = projects.get_all() #dummy

  elif req.get_method() == "PUT":
    ret = projects.update(project_id, body)

  elif req.get_method() == "DELETE":
    ret = projects.delete(project_id)

  return {
      "statusCode": 200,
      "body": json.dumps(
          {"message": str(ret)}
      )
  }
