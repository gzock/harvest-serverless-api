import os, sys
import json
import logging
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), '../site-packages'))
from harvest import RequestDecorator, Generate, Auth
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
    gen = Generate(DYNAMO_HOST, DYNAMO_PORT)
    req = RequestDecorator(event)
  except Exception as e:
    raise e

  user_id = req.get_identity_id()
  username = req.get_username()
  if user_id and username:
    logger.info("requested user id: {}".format(user_id))
    logger.info("requested user name: {}".format(username))

  path_params = req.get_path_params()
  if "project_id" in path_params:
    project_id = path_params["project_id"]
    logger.info("requested project_id: {}".format(project_id))

  if "type" in path_params:
    gen_type = path_params["type"]
    logger.info("requested generate type: {}".format(gen_type))
  logger.info("requested http method: {}".format(req.get_method()))
  logger.info("requested path: {}".format(req.get_path()))
  logger.info("requested pathParams: {}".format(req.get_path_params()))
  logger.info("requested http headers: {}".format(str(req.get_headers())))
  
  status_code = 200
  ret = ""

  try:
    auth = Auth(DYNAMO_HOST, DYNAMO_PORT, user_id, project_id)
    if req.get_method() == "GET":
      pass

    elif req.get_method() == "POST":
      if auth.guard("admin"):
        # /projects/{project_id}/generate/{type}
        body = req.get_body()
        if gen_type  == "zip":
          by_name  = body["by_name"]
          ret = gen.gen_zip(
              project_id=project_id, 
              by_name=by_name,
              need_download_url=True
          )
          if isinstance(ret, str):
            ret = {"download_url": ret}

        elif gen_type  == "excel-doc":
          has_hierarchy  = body["has_hierarchy"]
          template = req.get_body()["template"]

          ret = gen.gen_excel_doc(
              project_id=project_id, 
              has_hierarchy=has_hierarchy, 
              template=template, 
              need_download_url=True
          )
          if isinstance(ret, str):
            ret = {"download_url": ret}
      else:
        status_code = 403
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
