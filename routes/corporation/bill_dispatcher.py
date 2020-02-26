import os, sys
import logging
import traceback

from harvest.commons.billing.billing import Billing
from harvest.controllers.corporation_controller import CorporationController

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

  billing = Billing(DYNAMO_HOST, DYNAMO_PORT)
  corp = CorporationController(DYNAMO_HOST, DYNAMO_PORT)
  logger.info("Billing calculation processing start for each month.")

  try:
    for corp_id in corp.list_corporations():
      logger.info("processing target corporation_id: %s" % str(corp_id))
      corp.set_corporation_id(corp_id)
      billing.set_users(
          corp.list_users()
      )
      ret = billing.create(corp_id)
      logger.info("creating record result: %s" % str(ret))
      if ret["ResponseMetadata"]["HTTPStatusCode"] != 200:
        raise Exception
      logger.info("bill dispatch successfully.")

  except Exception as e:
    logger.error("execution general error... processing failed.")
    logger.exception(e)
    raise Exception

  return "bill dispatch successfully."
