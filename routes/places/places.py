import os, sys
import json
import logging
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), '../site-packages'))
from harvest import RequestDecorator
from harvest import Work
from harvest import ActionDeniedError
from harvest.utils.make_response_utils import make_response

DYNAMO_HOST = os.environ.get("DYNAMO_HOST")
DYNAMO_PORT = os.environ.get("DYNAMO_PORT")

def lambda_handler(event, context):
  formatter = logging.Formatter('[%(levelname)s]\t%(asctime)s.%(msecs)dZ\t%(aws_request_id)s\t[%(module)s#%(funcName)s %(lineno)d]\t%(message)s')

  logger = logging.getLogger()
  for handler in logger.handlers:
    handler.setFormatter(formatter)
    handler.setLevel(logging.DEBUG)

  logger = logging.getLogger(__name__)
  logger.setLevel(logging.DEBUG)

  try:
    work = Work(DYNAMO_HOST, DYNAMO_PORT)
    req = RequestDecorator(event)
  except Exception as e:
    raise e

  user_id = req.get_identity_id()
  username = req.get_username()
  if user_id and username:
    work.set_user_id(user_id)

  path_params = req.get_path_params()
  if "project_id" in path_params:
    project_id = path_params["project_id"]
    work.set_project_id(project_id)
    logger.info("requested project_id: {}".format(project_id))

  if "place_id" in path_params:
    place_id = path_params["place_id"]
    work.set_place_id(place_id)
    logger.info("requested place_id: {}".format(place_id))
  
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
    logger.info("processing successfully. return value: {}".format(ret))

  except ActionDeniedError as e:
    status_code = 403
    ret = e
    logger.error("permission denied.")
    logger.exception(e)
  except Exception as e:
    status_code = 400
    ret = e
    logger.error("invalid request?? processing failed...")
    logger.exception(e)

  return make_response(status_code=status_code, body=ret)
