import os, sys
import json
import logging
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), '../site-packages'))
from harvest import RequestDecorator, Generate, Auth
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
    gen = Generate(DYNAMO_HOST, DYNAMO_PORT)
    req = RequestDecorator(event)
  except Exception as e:
    raise e

  user_id = req.get_identity_id()
  username = req.get_username()
  if user_id and username:
    gen.set_user_id(user_id)

  path_params = req.get_path_params()
  if "project_id" in path_params:
    project_id = path_params["project_id"]
    gen.set_project_id(project_id)
    logger.info("requested project_id: {}".format(project_id))

  if "type" in path_params:
    gen_type = path_params["type"]
    logger.info("requested generate type: {}".format(gen_type))
  
  status_code = 200
  ret = ""

  try:
    auth = Auth(DYNAMO_HOST, DYNAMO_PORT, user_id, project_id)
    if req.get_method() == "GET":
      pass

    elif req.get_method() == "POST":
      # /projects/{project_id}/generate/{type}
      body = req.get_body()
      if gen_type  == "zip":
        by_name  = body["by_name"]
        ret = gen.gen_zip(
            project_id=project_id, 
            by_name=by_name,
            result_filename=project_id + ".zip",
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
            result_filename=project_id + ".xlsx",
            need_download_url=True
        )
        if isinstance(ret, str):
          ret = {"download_url": ret}
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
