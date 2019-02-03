import os, sys
import json
from botocore.vendored import requests

sys.path.append(os.path.join(os.path.dirname(__file__), 'site-packages'))
import boto3
from boto3.dynamodb.conditions import Key, Attr

from harvest import 

DYNAMO_HOST = "10.0.2.15"
DYNAMO_PORT = "8000"

def lambda_handler(event, context):

    #def __init__(self):
    try:
        dynamodb = boto3.resource(
            "dynamodb",
            region_name = "ap-northeast-1",
            endpoint_url = 'http://' + DYNAMO_HOST + ':' + DYNAMO_PORT
        )
        table = dynamodb.Table("projects")
        print(table.creation_date_time)
        res = table.scan()
        print(res)
    except Exception as e:
        print(e)
        raise e

    #try:
    #    ip = requests.get("http://checkip.amazonaws.com/")
    #except requests.RequestException as e:
    #    # Send some context about this error to Lambda Logs
    #    print(e)

    #    raise e

    return {
        "statusCode": 200,
        "body": json.dumps(
            {"message": str(res)}
            #{"message": "hello world", "location": ip.text.replace("\n", "")}
        ),
    }
