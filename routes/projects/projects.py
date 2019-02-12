import os, sys
import json
import logging

sys.path.append(os.path.join(os.path.dirname(__file__), '../site-packages'))
from harvest import RequestDecorator, ProjectController

DYNAMO_HOST = "10.0.2.15"
DYNAMO_PORT = "8000"

def lambda_handler(event, context):
  logger = logging.getLogger()
  logger.setLevel(logging.INFO)

  try:
    project = ProjectController(DYNAMO_HOST, DYNAMO_PORT)
    req = RequestDecorator(event)
  except Exception as e:
    raise e

  user_id = req.get_identity_id()
  # prod
  # project.set_user_id(user_id)
  # dev
  project.set_user_id("ryo_sasaki")
  logger.info("requested user id: {}".format(user_id))

  username = req.get_username()
  logger.info("requested user name: {}".format(username))

  path_params = req.get_path_params() #uuidの確認
  project_id = None
  if "project_id" in path_params:
    project_id = path_params["project_id"]
    project.set_project_id(project_id)
    logger.info("requested project_id: {}".format(project_id))
  logger.info("requested http method: {}".format(req.get_method()))
  logger.info("requested path: {}".format(req.get_path()))
  logger.info("requested pathParams: {}".format(req.get_path_params()))
  
  status_code = 200
  headers = {
      "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Amz-Date,X-Api-Key,X-Amz-Security-Token",
      "Access-Control-Allow-Methods": "OPTIONS,GET,POST,PUT.DELETE",
      "Access-Control-Allow-Origin": "*"
  }
  # /projects
  if req.get_method() == "GET":
    # /projects/xxxx-xxxx-xxxx-xxxx
    if project_id:
      ret = project.show()
    else:
      ret = project.list_projects()

  # /projects
  elif req.get_method() == "POST":
    name = req.get_body()["name"]
    ret = project.create(name)

  elif req.get_method() == "PUT":
    ret = project.update(project_id, body)

  elif req.get_method() == "DELETE":
    ret = project.delete(project_id)

  elif req.get_method() == "OPTIONS":
    ret = []

  return {
      "statusCode": status_code,
      "headers": headers,
      "body": json.dumps(ret)
  }

