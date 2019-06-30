import os, sys
import logging
import traceback

from harvest.controllers.notification_controller import NotificationController
from harvest.factories.notification_factory import NotificationFactory

from harvest.commons.notifications.notification import Notificaton
from harvest.commons.notifications.photo_notification import PhotoNotification
from harvest.commons.notifications.place_notification import PlaceNotification
from harvest.commons.notifications.project_notification import ProjectNotification
from harvest.commons.notifications.project_user_notification import ProjectUserNotification
from harvest.commons.notifications.target_notification import TargetNotification

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

  notifications = []
  controller = NotificationController(DYNAMO_HOST, DYNAMO_PORT)
  factory = NotificationFactory(DYNAMO_HOST, DYNAMO_PORT)
  for record in event["Records"]:
    logger.info("processing target record: %s" % str(record))
    factory.set_stream_record(record)
    ret = factory.generate()
    logger.info("genereted notification body: %s" % str(ret))
    if ret:
      notifications.extend(ret)
  controller.batch_create(notifications)
  return
