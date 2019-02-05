import os, sys
import json

sys.path.append(os.path.join(os.path.dirname(__file__), 'site-packages'))
import boto3
from boto3.dynamodb.conditions import Key, Attr

from harvest import ProjectDriver

#DYNAMO_HOST = "10.0.2.15"
DYNAMO_HOST = "127.0.0.1"
DYNAMO_PORT = "8000"

try:

    dynamodb = ProjectDriver(DYNAMO_HOST, DYNAMO_PORT)
    ret = dynamodb.get_all()
    print(ret)
    #dynamodb = boto3.resource(
    #    "dynamodb",
    #    region_name = "ap-northeast-1",
    #    endpoint_url = 'http://' + DYNAMO_HOST + ':' + DYNAMO_PORT
    #)
    #table = dynamodb.Table("projects")
    ##print(table.creation_date_time)
    #res = table.scan()
    #print(res)
except Exception as e:
    print(e)
    raise e

