import os, sys
import json
import logging
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), '../site-packages'))
from harvest import RequestDecorator
from harvest import ProjectUser
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
    project = ProjectUser(DYNAMO_HOST, DYNAMO_PORT)
    req = RequestDecorator(event)
  except Exception as e:
    raise e

  user_id = req.get_identity_id()
  username = req.get_username()
  if user_id and username:
    project.set_user_id(user_id)
    logger.info("requested user_id: {}".format(user_id))

  path_params = req.get_path_params()
  project_id = None
  if "project_id" in path_params:
    project_id = path_params["project_id"]
    project.set_project_id(project_id)
    logger.info("requested project_id: {}".format(project_id))
  
  specified_user_id = None
  if "user_id" in path_params:
    specified_user_id = path_params["user_id"]
    logger.info("requested specified user_id: {}".format(specified_user_id))
  
  status_code = 200
  ret=""
  # /projects
  try:
    if req.get_method() == "GET":
      # /projects/xxxx/users/yyyy
      if specified_user_id:
        # userの情報取得
        ret = project.show_user(specified_user_id)

      else:
        ret = project.list_users()

    # /projects
    elif req.get_method() == "POST":
      action = req.get_body()["action"]
      if action == "join":
        ret = project.join_user()

    elif req.get_method() == "PUT":
      action = req.get_body()["action"]
      if action == "accept":
        ret = project.accept_user(specified_user_id)

      elif action == "reject":
        ret = project.reject_user(specified_user_id)

      elif action == "update":
        role = req.get_body()["role"]
        ret = project.update_role(specified_user_id, role)

    elif req.get_method() == "DELETE":
      ret = project.delete_user(specified_user_id)

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
