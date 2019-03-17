import os, sys
import json
import logging
from base64 import b64encode, b64decode

sys.path.append(os.path.join(os.path.dirname(__file__), '../site-packages'))
from harvest import RequestDecorator, PhotoController

from harvest.make_response_utils import make_response

#DYNAMO_HOST = "10.0.2.15"
#DYNAMO_PORT = "8000"
DYNAMO_HOST = None
DYNAMO_PORT = None

def lambda_handler(event, context):
  logger = logging.getLogger()
  logger.setLevel(logging.INFO)

  try:
    photo = PhotoController(DYNAMO_HOST, DYNAMO_PORT)
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
    photo.set_project_id(project_id)
    logger.info("requested project_id: {}".format(project_id))

  if "target_id" in path_params:
    target_id = path_params["target_id"]
    photo.set_target_id(target_id)
    logger.info("requested target_id: {}".format(target_id))

  if "photo_id" in path_params:
    photo_id = path_params["photo_id"]
    logger.info("requested photo_id: {}".format(photo_id))

  logger.info("requested http method: {}".format(req.get_method()))
  logger.info("requested path: {}".format(req.get_path()))
  logger.info("requested pathParams: {}".format(req.get_path_params()))
  
  status_code = 200

  if req.get_method() == "GET" and target_id:
    # /projects/{project_id}/targets/{target_id}/photos/{photo_id}
    if "photo_id" in locals():
      ret = photo.show(photo_id, encode=True)
    else:
      # /projects/{project_id}/targets/{target_id}/photos
      ret = photo.list(target_id)

  elif req.get_method() == "POST" and target_id:
    type = req.get_body()["type"]
    enc_data = req.get_body()["data"]
    data = b64decode(enc_data)
    # /projects/{project_id}/targets/{target_id}/photos
    ret = photo.create(target_id, type, data)

  # /projects/{project_id}/targets/{target_id}/photos/{photo_id}
  elif req.get_method() == "PUT" and target_id:
    type = req.get_body()["type"]
    ret = photo.update_adopt(target_id, type, photo_id)

  # /projects/{project_id}/targets/{target_id}/photos/{photo_id}
  elif req.get_method() == "DELETE" and photo_id:
    ret = photo.delete(target_id, photo_id)

  elif req.get_method() == "OPTIONS":
    ret = []

  return make_response(status_code=status_code, body=ret)
