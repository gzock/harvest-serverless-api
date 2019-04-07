import os, sys
import json
import logging
import traceback
#from logging import getLogger, StreamHandler, Formatter, INFO, DEBUG

sys.path.append(os.path.join(os.path.dirname(__file__), '../site-packages'))
from harvest import RequestDecorator
from harvest import Project
from harvest import ActionDeniedError
#from decode_verify_jwt import decode_verify_jwt

from harvest.utils.make_response_utils import make_response

#DYNAMO_HOST = "10.0.2.15"
#DYNAMO_PORT = "8000"
DYNAMO_HOST = None
DYNAMO_PORT = None

def lambda_handler(event, context):
  formatter = '[%(levelname)s] %(asctime)s [%(module)s#%(funcName)s %(lineno)d] %(message)s'
   
  handler = logging.StreamHandler()
  handler.setLevel(logging.DEBUG)
  handler.setFormatter(logging.Formatter(formatter))
   
  logger = logging.getLogger(__name__)
  logger.addHandler(handler)
  logger.setLevel(logging.DEBUG)
  logger.propagate = False
  logging.getLogger("harvest.utils.request_decorator").addHandler(handler)

  try:
    project = Project(DYNAMO_HOST, DYNAMO_PORT)
    req = RequestDecorator(event)
  except Exception as e:
    raise e

  user_id = req.get_identity_id()
  username = req.get_username()
  if user_id and username:
    project.set_user_id(user_id)
    logger.info("requested user id: {}".format(user_id))
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
  logger.info("requested http headers: {}".format(str(req.get_headers())))
  
  status_code = 200
  ret=""
  # /projects
  try:
    if req.get_method() == "GET":
      # /projects/xxxx-xxxx-xxxx-xxxx
      if project_id:
        ret = project.show_project()
      else:
        ret = project.list_projects()

    # /projects
    elif req.get_method() == "POST":
      name = req.get_body()["name"]
      start_on = req.get_body()["start_on"]
      complete_on = req.get_body()["complete_on"]
      ret = project.create_project(name, start_on, complete_on)

    elif req.get_method() == "PUT":
      ret = project.update_project(project_id, body)

    elif req.get_method() == "DELETE":
      ret = project.delete_project(project_id)

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
