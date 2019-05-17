import os, sys
import json
import logging
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), '../site-packages'))
from harvest import RequestDecorator
from harvest import Project
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
    project = Project(DYNAMO_HOST, DYNAMO_PORT)
    req = RequestDecorator(event)
  except Exception as e:
    raise e

  user_id = req.get_identity_id()
  username = req.get_username()
  if user_id and username:
    project.set_user_id(user_id)

  path_params = req.get_path_params()
  project_id = None
  if "project_id" in path_params:
    project_id = path_params["project_id"]
    project.set_project_id(project_id)
    logger.info("requested project_id: {}".format(project_id))
  
  status_code = 200
  ret=""
  # /projects
  try:
    if req.get_method() == "GET":
      # /projects/xxxx-xxxx-xxxx-xxxx
      if project_id:
        if "users" in req.get_path():
          ret = project.list_users()
        elif "join" in req.get_path():
          ret = project.show_join_code()
        else:
          ret = project.show_project()
      else:
        ret = project.list_projects()

    # /projects
    elif req.get_method() == "POST":
      if "import" in req.get_path():
        csv = req.get_body()["csv"]
        ret = project.import_csv(csv, base64enc=True)

      elif "join" in req.get_path():
        ret = project.join_user()

      else:
        name = req.get_body()["name"]
        start_on = req.get_body()["start_on"]
        complete_on = req.get_body()["complete_on"]
        ret = project.create_project(name, start_on, complete_on)

    elif req.get_method() == "PUT":
      ret = project.update_project(project_id, body)

    elif req.get_method() == "DELETE":
      ret = project.delete_project()

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
