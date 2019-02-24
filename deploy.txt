#!/bin/bash
sam validate
[ $? -ne 0 ] && exit

sam package --template-file template.yaml --s3-bucket product-harvest-workspace --output-template-file packaged.yaml
[ $? -ne 0 ] && exit

sam deploy --template-file packaged.yaml --stack-name harvest-stack --capabilities CAPABILITY_IAM
[ $? -ne 0 ] && exit
