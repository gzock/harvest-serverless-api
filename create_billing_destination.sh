#!/bin/bash

echo "create user billing destination"
read -p "user_id > " user_id
read -p "name > " name
read -p "email > " email
read -p "postal_code > " postal_code
read -p "address > " address
read -p "department > " department
read -p "tel > " tel
read -p "contact > " contact

now=`python3 -c "import datetime; print(datetime.datetime.now().isoformat())"`

json="{ 
      \"user_id\": { \"S\": \"${user_id}\" }, 
      \"context\": { \"S\": \"destination\" }, 
      \"destination\": { 
        \"M\": { 
          \"name\": { \"S\": \"${name}\" }, 
          \"email\": { \"S\": \"${email}\" }, 
          \"address\": { \"S\": \"${address}\" }, 
          \"postal_code\": { \"S\": \"${postal_code}\" }, 
          \"department\": { \"S\": \"${department}\" }, 
          \"tel\": { \"S\": \"${tel}\" }, 
          \"contact\": { \"S\": \"${contact}\"} 
        }
      },
      \"created_at\": { \"S\": \"${now}\" }, 
      \"updated_at\": { \"S\": \"${now}\" }
    }"


aws dynamodb put-item \
    --endpoint-url http://localhost:8000 \
    --table-name Bills \
    --item "${json}"
rc=$?

echo "---"
echo -n "result: "
[ ${rc} -eq 0 ] && echo "successfully" || echo "failed"
