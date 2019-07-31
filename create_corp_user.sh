#!/bin/bash

#aws cognito-idp admin-create-user \
#  --user-pool-id ap-northeast-1_KrEPljcrG \
#  --username xxxxx \
#  --temporary-password Hogehoge \
#  --user-attributes '[
#    {
#      "Name": "email",
#      "Value": "hogefuga@foobar.com
#    },
#    {
#      "Name": "email_verified",
#      "Value": "true"
#    },
#    {
#      "Name": "preferred_username",
#      "Value": "xxxxxxx"
#    },
#    {
#      "Name": "custom:user_type",
#      "Value": "corporation"
#    },
#    {
#      "Name": "custom:kana_username",
#      "Value": "xxxxxxx"
#    },
#    {
#      "Name": "custom:pricing_plan",
#      "Value": "standard"
#    },
#    {
#      "Name": "custom:created_at",
#      "Value": "2019-07-31T13:11:29.917Z"
#    }
#  ]'
#
#aws cognito-idp admin-set-user-password --user-pool-id=ap-northeast-1_KrEPljcrG --username=bcc38577-e3d6-4ce7-a7bc-734d9ac7c3b8 --password=Hogehoge


#aws cognito-idp admin-update-user-attributes --user-pool-id=ap-northeast-1_KrEPljcrG --username=8f425c18-60dd-4622-bfea-76fc0b97287b \
#  --user-attributes '[
#    {
#      "Name": "preferred_username",
#      "Value": "xxxxx"
#    },
#    {
#      "Name": "custom:user_type",
#      "Value": "employee"
#    },
#    {
#      "Name": "custom:kana_username",
#      "Value": "xxxxxx"
#    },
#    {
#      "Name": "custom:pricing_plan",
#      "Value": "standard"
#    },
#    {
#      "Name": "custom:created_at",
#      "Value": "2019-07-31T13:11:29.917Z"
#    }
#  ]'
