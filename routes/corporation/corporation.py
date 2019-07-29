import os, sys
import json
import logging
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), '../site-packages'))
from harvest import RequestDecorator
from harvest import Corporation
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
    corp = Corporation()
    req = RequestDecorator(event)
  except Exception as e:
    raise e

  corporation_id = req.get_identity_id()
  #username = req.get_username()
  if corporation_id:
    corp.set_corporation_id(corporation_id)

  path_params = req.get_path_params()
  
  status_code = 200
  ret=""
  # /corps
  try:
    if req.get_method() == "GET":
      # /corporation
      if "users" in req.get_path():
        ret = corp.list_users()
      else:
        ret = corp.show()

    elif req.get_method() == "POST":
      body = req.get_body()
      if corporation_id:
        body.update({"corporation_id": corporation_id})
        ret = corp.create_user(**body)

    #elif req.get_method() == "PUT":
    #  name = req.get_body()["name"]
    #  ret = corp.update_corp(name, start_on, complete_on)

    #elif req.get_method() == "DELETE":
    #  ret = corp.delete_corp()

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
