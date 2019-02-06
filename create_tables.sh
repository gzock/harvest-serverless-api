#!/bin/bash

echo "`date`  start create table for harvest"

echo "`date`  create projects table"
echo "----------"
aws dynamodb create-table \
    --endpoint-url http://localhost:8000 \
    --table-name Projects \
    --attribute-definitions \
        AttributeName=project_id,AttributeType=S \
        AttributeName=users,AttributeType=S \
    --key-schema \
        AttributeName=project_id,KeyType=HASH \
    --global-secondary-indexes IndexName=ProjectUsersIndex,KeySchema=["{AttributeName=users,KeyType=HASH}"],Projection="{ProjectionType=ALL}",ProvisionedThroughput="{ReadCapacityUnits=3,WriteCapacityUnits=3}" \
    --provisioned-throughput ReadCapacityUnits=3,WriteCapacityUnits=3
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
    --provisioned-throughput ReadCapacityUnits=3,WriteCapacityUnits=3
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
    --local-secondary-indexes IndexName=ParentPlaceIndex,KeySchema=["{AttributeName=project_id,KeyType=HASH}","{AttributeName=parent_place_id,KeyType=RANGE}"],Projection="{ProjectionType=ALL}" \
    --provisioned-throughput ReadCapacityUnits=3,WriteCapacityUnits=3 
echo "----------"
