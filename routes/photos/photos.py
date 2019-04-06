import os, sys
import json
import logging
from base64 import b64encode, b64decode

sys.path.append(os.path.join(os.path.dirname(__file__), '../site-packages'))
from harvest import RequestDecorator
from harvest import Work
from harvest import ActionDeniedError

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

  if "target_id" in path_params:
    target_id = path_params["target_id"]
    work.set_target_id(target_id)
    logger.info("requested target_id: {}".format(target_id))

  if "photo_id" in path_params:
    photo_id = path_params["photo_id"]
    work.set_photo_id(photo_id)
    logger.info("requested photo_id: {}".format(photo_id))

  logger.info("requested http method: {}".format(req.get_method()))
  logger.info("requested path: {}".format(req.get_path()))
  logger.info("requested pathParams: {}".format(req.get_path_params()))
  logger.info("requested http headers: {}".format(str(req.get_headers())))
  
  status_code = 200
  ret = ""

  try:
    if req.get_method() == "GET" and target_id:
      # /projects/{project_id}/targets/{target_id}/photos/{photo_id}
      if "photo_id" in locals():
        ret = work.show_photo(photo_id, encode=True)
      else:
        # /projects/{project_id}/targets/{target_id}/photos
        ret = work.list_photo(target_id)

    elif req.get_method() == "POST" and target_id:
      type = req.get_body()["type"]
      enc_data = req.get_body()["data"]
      data = b64decode(enc_data)
      # /projects/{project_id}/targets/{target_id}/photos
      ret = work.create_photo(target_id, type, data)

    # /projects/{project_id}/targets/{target_id}/photos/{photo_id}
    elif req.get_method() == "PUT" and target_id:
      type = req.get_body()["type"]
      ret = work.update_photo(target_id, type, photo_id)

    # /projects/{project_id}/targets/{target_id}/photos/{photo_id}
    elif req.get_method() == "DELETE" and photo_id:
      ret = work.delete_photo(target_id, photo_id)

    elif req.get_method() == "OPTIONS":
      ret = []
    logger.info("processing successfully.".format(ret))

  except ActionDeniedError as e:
    status_code = 403
    ret = e
    logger.error("permission denied: {}".format(str(e)))
  except Exception as e:
    status_code = 400
    ret = e
    logger.error(traceback.format_exc())

  logger.info("response body: {}".format(ret))
  logger.info("response http status code: {}".format(status_code))

  return make_response(status_code=status_code, body=ret)
