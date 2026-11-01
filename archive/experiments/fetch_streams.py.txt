import boto3
import json

client = boto3.client(
    'dynamodbstreams',
    region_name='ap-northeast-1', 
    endpoint_url = 'http://127.0.0.1:8000'
)

ret = {"Records": []}
TABLES = ["Projects", "Places", "Targets", "Roles"]
#records = client.get_records()
for table in TABLES:
  records = client.list_streams(TableName=table)
  
  for stream in records["Streams"]:
    streams = client.describe_stream(StreamArn=stream["StreamArn"])
    for shard in streams["StreamDescription"]["Shards"]:
      shade_iterator = client.get_shard_iterator(StreamArn=streams["StreamDescription"]["StreamArn"], ShardId=shard["ShardId"], ShardIteratorType="TRIM_HORIZON")
      images = client.get_records(ShardIterator=shade_iterator["ShardIterator"])
      for image in images["Records"]:
        del image["dynamodb"]["ApproximateCreationDateTime"]
        #print( { k:list(v.values())[0] for k, v in image["dynamodb"]["NewImage"].items() } )
        image.update({"eventSourceARN": "arn:aws:dynamodb:us-east-1:123456789012:table/%s/stream/2015-06-27T00:48:05.899" % table})
        ret["Records"].append(image)
print(json.dumps(ret, ensure_ascii=False, indent=2, sort_keys=True, separators=(',', ': ')))
