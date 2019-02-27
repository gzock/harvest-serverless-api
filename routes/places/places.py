import os, sys
import json
import decimal
import logging

sys.path.append(os.path.join(os.path.dirname(__file__), '../site-packages'))
from harvest import RequestDecorator, PlaceController, TargetController

DYNAMO_HOST = "10.0.2.15"
DYNAMO_PORT = "8000"

def lambda_handler(event, context):
  logger = logging.getLogger()
  logger.setLevel(logging.INFO)

  try:
    place = PlaceController(DYNAMO_HOST, DYNAMO_PORT)
    target = TargetController(DYNAMO_HOST, DYNAMO_PORT)
    req = RequestDecorator(event)
  except Exception as e:
    raise e

  user_id = req.get_identity_id()
  # prod
  # project.set_user_id(user_id)
  # dev
  #place.set_user_id("ryo_sasaki")
  logger.info("requested user id: {}".format(user_id))

  username = req.get_username()
  logger.info("requested user name: {}".format(username))

  path_params = req.get_path_params()
  if "project_id" in path_params:
    project_id = path_params["project_id"]
    place.set_project_id(project_id)
    target.set_project_id(project_id)
    logger.info("requested project_id: {}".format(project_id))

  if "place_id" in path_params:
    place_id = path_params["place_id"]
    place.set_place_id(place_id)
    logger.info("requested place_id: {}".format(place_id))

  logger.info("requested http method: {}".format(req.get_method()))
  logger.info("requested path: {}".format(req.get_path()))
  logger.info("requested pathParams: {}".format(req.get_path_params()))
  
  status_code = 200
  headers = {
      "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Amz-Date,X-Api-Key,X-Amz-Security-Token",
      "Access-Control-Allow-Methods": "OPTIONS,GET,POST,PUT,DELETE",
      "Access-Control-Allow-Origin": "*"
  }

  if req.get_method() == "GET":
    if "place_id" in locals():
      if "children" in req.get_path():
        # /projects/{project_id}/places/{place_id}/children
        places = place.list_children(place_id)
        targets = target.list_children(place_id)
        ret = {
          "places": places,
          "targets": targets
        }
      else:
        # /projects/{project_id}/places/{place_id}
        ret = place.show(place_id)

    # /projects/{project_id}/places
    else:
      places = place.list_children(project_id)
      targets = target.list_children(place_id)
      ret = {
        "places": places,
        "targets": targets
      }

  elif req.get_method() == "POST":
    name = req.get_body()["name"]
    # /projects/{project_id}/places/{place_id}
    if "place_id" in locals():
      ret = place.create(name, place_id)

    # /projects/{project_id}/places
    else:
      ret = place.create(name)

  # /projects/{project_id}/places/{place_id}
  elif req.get_method() == "PUT":
    name = req.get_body()["name"]
    ret = place.update(project_id, name)

  # /projects/{project_id}/places/{place_id}
  elif req.get_method() == "DELETE":
    ret = place.delete(project_id)

  elif req.get_method() == "OPTIONS":
    ret = []

  print(ret)

  return {
      "statusCode": status_code,
      "headers": headers,
      "body": json.dumps(ret, cls = DecimalEncoder)
  }

class DecimalEncoder(json.JSONEncoder):
  def default(self, o):
    if isinstance(o, decimal.Decimal):
      return int(o)
    return super(DecimalEncoder, self).default(o)
