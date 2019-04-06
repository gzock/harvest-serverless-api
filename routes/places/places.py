import os, sys
import json
import logging
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), '../site-packages'))
from harvest import RequestDecorator
from harvest import Work
from harvest import ActionDeniedError
from decode_verify_jwt import decode_verify_jwt

from harvest.utils.make_response_utils import make_response

#DYNAMO_HOST = "10.0.2.15"
#DYNAMO_PORT = "8000"
DYNAMO_HOST = None
DYNAMO_PORT = None

def lambda_handler(event, context):
  logger = logging.getLogger()
  logger.setLevel(logging.INFO)

  try:
    work = Work(DYNAMO_HOST, DYNAMO_PORT)
    req = RequestDecorator(event)
  except Exception as e:
    raise e

  user_id = req.get_identity_id()
  username = req.get_username()
  if user_id and username:
    work.set_user_id(user_id)
    logger.info("requested user id: {}".format(user_id))
    logger.info("requested user name: {}".format(username))

  path_params = req.get_path_params()
  if "project_id" in path_params:
    project_id = path_params["project_id"]
    work.set_project_id(project_id)
    logger.info("requested project_id: {}".format(project_id))

  if "place_id" in path_params:
    place_id = path_params["place_id"]
    work.set_place_id(place_id)
    logger.info("requested place_id: {}".format(place_id))

  logger.info("requested http method: {}".format(req.get_method()))
  logger.info("requested path: {}".format(req.get_path()))
  logger.info("requested pathParams: {}".format(req.get_path_params()))
  
  status_code = 200
  ret = ""

  try:
    if req.get_method() == "GET":
      if "place_id" in locals():
        if "children" in req.get_path():
          # /projects/{project_id}/places/{place_id}/children
          ret = work.list_children(place_id)
        else:
          # /projects/{project_id}/places/{place_id}
          ret = work.show_place(place_id)
      # /projects/{project_id}/places
      else:
        ret = work.list_children(project_id)

    elif req.get_method() == "POST":
      name = req.get_body()["name"]
      # /projects/{project_id}/places/{place_id}
      if "place_id" in locals():
        ret = work.create_place(name, place_id)

      # /projects/{project_id}/places
      else:
        ret = work.create_place(name)

    # /projects/{project_id}/places/{place_id}
    elif req.get_method() == "PUT":
      name = req.get_body()["name"]
      ret = work.update_place(name)

    # /projects/{project_id}/places/{place_id}
    elif req.get_method() == "DELETE":
      ret = work.delete_place(place_id)

    elif req.get_method() == "OPTIONS":
      ret = []

  except ActionDeniedError as e:
    status_code = 403
    ret = e
  except Exception as e:
    status_code = 400
    ret = e
    logger.error(traceback.format_exc())

  return make_response(status_code=status_code, body=ret)
