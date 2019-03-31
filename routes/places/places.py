import os, sys
import json
import logging

sys.path.append(os.path.join(os.path.dirname(__file__), '../site-packages'))
from harvest import RequestDecorator, PlaceController, TargetController, Auth
from decode_verify_jwt import decode_verify_jwt

from harvest.make_response_utils import make_response

#DYNAMO_HOST = "10.0.2.15"
#DYNAMO_PORT = "8000"
DYNAMO_HOST = None
DYNAMO_PORT = None

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
  if "Authorization" in req.get_headers():
    decoded = decode_verify_jwt(req.get_headers()["Authorization"])
    logger.info("decoded authorization header: {}".format(decoded))
    if decoded:
      user_id = decoded["cognito:username"]
      username = decoded["preferred_username"]
      logger.info("requested user id: {}".format(user_id))
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
  ret = ""

  try:
    auth = Auth(DYNAMO_HOST, DYNAMO_PORT, user_id, project_id)
    if req.get_method() == "GET":
      if auth.guard():
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
      else:
        status_code = 403

    elif req.get_method() == "POST":
      if auth.guard("worker"):
        name = req.get_body()["name"]
        # /projects/{project_id}/places/{place_id}
        if "place_id" in locals():
          ret = place.create(name, place_id)

        # /projects/{project_id}/places
        else:
          ret = place.create(name)
      else:
        status_code = 403

    # /projects/{project_id}/places/{place_id}
    elif req.get_method() == "PUT":
      if auth.guard("admin"):
        name = req.get_body()["name"]
        ret = place.update_name(name)
      else:
        status_code = 403

    # /projects/{project_id}/places/{place_id}
    elif req.get_method() == "DELETE":
      if auth.guard("admin"):
        ret = place.delete(project_id)
      else:
        status_code = 403

    elif req.get_method() == "OPTIONS":
      ret = []
  except Exception as e:
    print(e)
    status_code = 400

  return make_response(status_code=status_code, body=ret)
