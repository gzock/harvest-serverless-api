#!/bin/bash

echo "`date`  start create table for harvest"

echo "`date`  create projects table"
echo "----------"
aws dynamodb create-table \
    --endpoint-url http://localhost:8000 \
    --table-name Projects \
    --attribute-definitions \
        AttributeName=project_id,AttributeType=S \
    --key-schema \
        AttributeName=project_id,KeyType=HASH \
    --provisioned-throughput ReadCapacityUnits=3,WriteCapacityUnits=3 \
    --stream-specification StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES

echo "----------"

echo "`date`  create roles table"
echo "----------"
aws dynamodb create-table \
    --endpoint-url http://localhost:8000 \
    --table-name Roles \
    --attribute-definitions \
        AttributeName=project_id,AttributeType=S \
        AttributeName=user_id,AttributeType=S \
    --key-schema \
        AttributeName=project_id,KeyType=HASH \
        AttributeName=user_id,KeyType=RANGE \
    --global-secondary-indexes IndexName=UserRolesIndex,KeySchema=["{AttributeName=user_id,KeyType=HASH}"],Projection="{ProjectionType=ALL}",ProvisionedThroughput="{ReadCapacityUnits=3,WriteCapacityUnits=3}" \
    --provisioned-throughput ReadCapacityUnits=3,WriteCapacityUnits=3 \
    --stream-specification StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES

echo "----------"

echo "`date`  create places table"
echo "----------"
aws dynamodb create-table \
    --endpoint-url http://localhost:8000 \
    --table-name Places \
    --attribute-definitions \
        AttributeName=project_id,AttributeType=S \
        AttributeName=place_id,AttributeType=S \
        AttributeName=parent_place_id,AttributeType=S \
    --key-schema \
        AttributeName=project_id,KeyType=HASH \
        AttributeName=place_id,KeyType=RANGE \
    --local-secondary-indexes IndexName=ParentPlacesIndex,KeySchema=["{AttributeName=project_id,KeyType=HASH}","{AttributeName=parent_place_id,KeyType=RANGE}"],Projection="{ProjectionType=ALL}" \
    --provisioned-throughput ReadCapacityUnits=10,WriteCapacityUnits=12 \
    --stream-specification StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES
echo "----------"

echo "`date`  create targets table"
echo "----------"
aws dynamodb create-table \
    --endpoint-url http://localhost:8000 \
    --table-name Targets \
    --attribute-definitions \
        AttributeName=project_id,AttributeType=S \
        AttributeName=target_id,AttributeType=S \
        AttributeName=parent_place_id,AttributeType=S \
    --key-schema \
        AttributeName=project_id,KeyType=HASH \
        AttributeName=target_id,KeyType=RANGE \
    --local-secondary-indexes IndexName=ParentPlacesIndex,KeySchema=["{AttributeName=project_id,KeyType=HASH}","{AttributeName=parent_place_id,KeyType=RANGE}"],Projection="{ProjectionType=ALL}" \
    --provisioned-throughput ReadCapacityUnits=10,WriteCapacityUnits=8 \
    --stream-specification StreamEnabled=true,StreamViewType=NEW_AND_OLD_IMAGES
echo "----------"

echo "`date`  create notifications table"
echo "----------"
aws dynamodb create-table \
    --endpoint-url http://localhost:8000 \
    --table-name Notifications \
    --attribute-definitions \
        AttributeName=user_id,AttributeType=S \
        AttributeName=notification_id,AttributeType=S \
    --key-schema \
        AttributeName=user_id,KeyType=HASH \
        AttributeName=notification_id,KeyType=RANGE \
    --provisioned-throughput ReadCapacityUnits=3,WriteCapacityUnits=3
aws dynamodb update-time-to-live \
    --endpoint-url http://localhost:8000 \
    --table-name Notifications \
    --time-to-live-specification "Enabled=true, AttributeName=ttl"
echo "----------"

echo "`date`  create bills table"
echo "----------"
aws dynamodb create-table \
    --endpoint-url http://localhost:8000 \
    --table-name Bills \
    --attribute-definitions \
        AttributeName=user_id,AttributeType=S \
        AttributeName=context,AttributeType=S \
    --key-schema \
        AttributeName=user_id,KeyType=HASH \
        AttributeName=context,KeyType=RANGE \
    --provisioned-throughput ReadCapacityUnits=1,WriteCapacityUnits=1
echo "----------"
