import boto3
import json

client = boto3.client(
    'dynamodbstreams',
    region_name='ap-northeast-1', 
    endpoint_url = 'http://127.0.0.1:8000'
)

#records = client.get_records()
records = client.list_streams(TableName="Projects")
#print(records)
#print("--------------------")
#records = client.describe_stream(StreamArn=records["Streams"][0]["StreamArn"])
#print(records)
#print("--------------------")

for stream in records["Streams"]:
  streams = client.describe_stream(StreamArn=stream["StreamArn"])
  for shard in streams["StreamDescription"]["Shards"]:
    shade_iterator = client.get_shard_iterator(StreamArn=streams["StreamDescription"]["StreamArn"], ShardId=shard["ShardId"], ShardIteratorType="TRIM_HORIZON")
    images = client.get_records(ShardIterator=shade_iterator["ShardIterator"])
    #print(images)
    for image in images["Records"]:
      #print(image["dynamodb"]["NewImage"])
      print( { k:list(v.values())[0] for k, v in image["dynamodb"]["NewImage"].items() } )
      print("--------")
