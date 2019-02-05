import os, sys
import json
from botocore.vendored import requests

sys.path.append(os.path.join(os.path.dirname(__file__), 'site-packages'))

import boto3
from boto3.dynamodb.conditions import Key, Attr
import logging

from harvest import RequestDecorator, ProjectDriver

DYNAMO_HOST = "10.0.2.15"
DYNAMO_PORT = "8000"

def lambda_handler(event, context):
  logger = logging.getLogger()
  logger.setLevel(logging.INFO)

  req = RequestDecorator(event)
  user_id = req.get_identity_id()
  logger.info('requested user id: {}'.format(user_id))
  username = req.get_username()
  logger.info('requested user name: {}'.format(username))

  try:
    dynamodb = ProjectDriver(DYNAMO_HOST, DYNAMO_PORT)
    ret = dynamodb.get_all()
    print(ret)

  except Exception as e:
      print(e)
      raise e

  return {
      "statusCode": 200,
      "body": json.dumps(
          {"message": str(ret)}
      )
  }
