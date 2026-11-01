import logging
import boto3

PAGINATION_LOOP_LIMIT = 1000


class CognitoDriver:
    logger = logging.getLogger(__name__)
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.DEBUG)

    def __init__(self, pool_id=None):
        self.cognito = boto3.client("cognito-idp")
        if pool_id:
            self.pool_id = pool_id

    def __list_users(self, attr, filter_attr="username", page_token=None):
        if page_token:
            return self.cognito.list_users(
                UserPoolId=self.pool_id,
                Filter='%s = "%s"' % (filter_attr, attr),
                PaginationToken=page_token,
            )
        else:
            return self.cognito.list_users(
                UserPoolId=self.pool_id, Filter='%s = "%s"' % (filter_attr, attr)
            )

    def list_users(self, attr, filter_attr="username"):
        if not self.pool_id:
            raise ValueError("User pool is required")
        users, token, seen = [], None, set()
        while True:
            page = self.__list_users(attr, filter_attr, token)
            users.extend(page.get("Users", []))
            token = page.get("PaginationToken")
            if not token:
                return users
            if token in seen:
                raise RuntimeError("Cognito repeated a pagination token")
            seen.add(token)

    def __list_users_in_group(self, group_name, next_token=None):
        if next_token:
            return self.cognito.list_users_in_group(
                UserPoolId=self.pool_id, GroupName=group_name, Limit=60, NextToken=next_token
            )
        else:
            return self.cognito.list_users_in_group(
                UserPoolId=self.pool_id, GroupName=group_name, Limit=60
            )

    def list_users_in_group(self, group_name):
        if not self.pool_id:
            raise ValueError("User pool is required")
        users, token, seen = [], None, set()
        while True:
            page = self.__list_users_in_group(group_name, token)
            users.extend(page.get("Users", []))
            token = page.get("NextToken")
            if not token:
                # Defend billing against overlapping pages while retaining order.
                return list({user["Username"]: user for user in users}.values())
            if token in seen:
                raise RuntimeError("Cognito repeated a pagination token")
            seen.add(token)

    def list_groups(self):
        groups, token, seen = [], None, set()
        while True:
            args = {"UserPoolId": self.pool_id, "Limit": 60}
            if token:
                args["NextToken"] = token
            page = self.cognito.list_groups(**args)
            groups.extend(page.get("Groups", []))
            token = page.get("NextToken")
            if not token:
                return {"Groups": groups}
            if token in seen:
                raise RuntimeError("Cognito repeated a pagination token")
            seen.add(token)

    def show_group(self, group_name):
        return self.cognito.get_group(UserPoolId=self.pool_id, GroupName=group_name)["Group"]

    def show_user(self, username):
        return self.cognito.list_users(
            UserPoolId=self.pool_id, Filter='username = "{}"'.format(username)
        )["Users"]

    def create_user(
        self, username, email, password, preferred_username=False, custom_attr=None, verify=False
    ):
        user_attr = [{"Name": "email", "Value": email}]

        if verify:
            user_attr.append({"Name": "email_verified", "Value": "true"})

        if preferred_username and isinstance(preferred_username, str):
            user_attr.append({"Name": "preferred_username", "Value": preferred_username})

        if custom_attr and isinstance(custom_attr, dict):
            custom_attr = [{"Name": k, "Value": v} for k, v in custom_attr.items()]
            user_attr.extend(custom_attr)

        return self.cognito.admin_create_user(
            UserPoolId=self.pool_id,
            Username=username,
            UserAttributes=user_attr,
            TemporaryPassword=password,
            ForceAliasCreation=False,
            MessageAction="SUPPRESS",
            DesiredDeliveryMediums=["EMAIL"],
        )["User"]

    def add_user_to_group(self, username, group_name):
        return self.cognito.admin_add_user_to_group(
            UserPoolId=self.pool_id, Username=username, GroupName=group_name
        )

    def set_user_password(self, username, password, permanent=True):
        return self.cognito.admin_set_user_password(
            UserPoolId=self.pool_id, Username=username, Password=password, Permanent=permanent
        )

    def delete_user(self, username):
        return self.cognito.admin_delete_user(UserPoolId=self.pool_id, Username=username)

    def disable_user(self, username):
        return self.cognito.admin_disable_user(UserPoolId=self.pool_id, Username=username)


# https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/cognito-idp.html#CognitoIdentityProvider.Client.list_users
