import boto3

client = boto3.client(
    'dynamodbstreams',
    region_name='ap-northeast-1', 
    endpoint_url = 'http://127.0.0.1:8000'
)

#records = client.get_records()
records = client.list_streams(TableName="Projects")
print(records)
print("--------------------")
records = client.describe_stream(StreamArn="arn:aws:dynamodb:ddblocal:000000000000:table/Projects/stream/2019-06-10T13:29:43.394")
print(records)
print("--------------------")
shade_iterator = client.get_shard_iterator(StreamArn="arn:aws:dynamodb:ddblocal:000000000000:table/Projects/stream/2019-06-10T13:29:43.394", ShardId="shardId-00000001560173383496-aaeca67b", ShardIteratorType="TRIM_HORIZON")

records = client.get_records(ShardIterator=shade_iterator["ShardIterator"])
print(records)
