import os, sys
import json
import logging

sys.path.append(os.path.join(os.path.dirname(__file__), '../site-packages'))
from harvest import RequestDecorator, Generate

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
    logger.info("requested project_id: {}".format(project_id))

  if "type" in path_params:
    gen_type = path_params["type"]
    logger.info("requested generate type: {}".format(gen_type))

  logger.info("requested http method: {}".format(req.get_method()))
  logger.info("requested path: {}".format(req.get_path()))
  logger.info("requested pathParams: {}".format(req.get_path_params()))
  
  status_code = 200
  headers = {
      "Access-Control-Allow-Headers": "Content-Type,Authorization,X-Amz-Date,X-Api-Key,X-Amz-Security-Token",
      "Access-Control-Allow-Methods": "OPTIONS,GET,POST,PUT,DELETE",
      "Access-Control-Allow-Origin": "*"
  }

  ret = ""

  if req.get_method() == "GET":
    pass

  elif req.get_method() == "POST":
    # /projects/{project_id}/generate/{type}
    if gen_type  == "zip":
      ret = gen.gen_zip(project_id)

    elif gen_type  == "excel-doc":
      body = req.get_body()
      if "need_custom_template" in body:
        need_custom_template  = body["need_custom_template"]
      if "template" in body:
        template = req.get_body()["template"]

      ret = gen.gen_excel_doc(
          project_id=project_id, 
          need_custom_template=need_custom_template, 
          template=template, 
          need_download_link=True
      )

  return {
      "statusCode": status_code,
      "headers": headers,
      "body": json.dumps(ret)
  }

