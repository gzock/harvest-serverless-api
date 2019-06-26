import os, sys
import json
import logging
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), '../site-packages'))
from harvest import RequestDecorator
from harvest import Notification
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
    notification = Notification(DYNAMO_HOST, DYNAMO_PORT)
    req = RequestDecorator(event)
  except Exception as e:
    raise e

  path_params = req.get_path_params()
  user_id = req.get_identity_id()
  username = req.get_username()
  if req.get_method() != "OPTIONS":
    if path_params["user_id"] == user_id:
      notification.set_user_id(user_id)
    else:
      raise ActionDeniedError

  notification_id = None
  if "notification_id" in path_params:
    notification_id = path_params["notification_id"]
    notification.set_notification_id(notification_id)
    logger.info("requested notification_id: {}".format(notification_id))
  
  status_code = 200
  ret=""
  try:
    if req.get_method() == "GET":
      # /users/{user_id}/notifications/{notification_id}
      if notification_id:
        ret = notification.show_notification()

      # /users/{user_id}/notifications
      else:
        ret = notification.list_notifications()

    elif req.get_method() == "PUT":
      read = req.get_body()["read"]
      ret = notification.update_notification(read)

    elif req.get_method() == "DELETE":
      ret = notification.delete_notification()

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
