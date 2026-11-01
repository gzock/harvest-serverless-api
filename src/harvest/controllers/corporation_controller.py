import logging
import os
from datetime import datetime

from botocore.exceptions import ClientError

from harvest.commons.billing.billing import Billing
from harvest.controllers.project_controller import ProjectController
from harvest.controllers.project_user_controller import ProjectUserController
from harvest.drivers.cognito_driver import CognitoDriver
from harvest.exception import ActionDeniedError

COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")


class CorporationController:
    logger = logging.getLogger(__name__)
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.DEBUG)

    def __init__(self, host, port):
        self.cognito = CognitoDriver(COGNITO_USER_POOL_ID)
        self.project = ProjectController(host, port)
        self.project_user = ProjectUserController(host, port)
        self.billing = Billing(host, port)

    def set_corporation_id(self, corporation_id):
        self.corporation_id = str(corporation_id)

    def require_corporation_admin(self):
        actor_id = getattr(self, "corporation_id", None)
        if not actor_id:
            raise ActionDeniedError()
        # A Cognito group provisioned by administrators is the authoritative
        # corporation boundary. A self-writable custom attribute alone is insufficient.
        try:
            user = self.cognito.cognito.admin_get_user(
                UserPoolId=COGNITO_USER_POOL_ID, Username=actor_id
            )
            group = self.cognito.show_group(actor_id)
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") in (
                "UserNotFoundException",
                "ResourceNotFoundException",
            ):
                raise ActionDeniedError() from None
            raise
        attrs = {item["Name"]: item["Value"] for item in user.get("UserAttributes", [])}
        if (
            not user.get("Enabled")
            or user.get("Username") != actor_id
            or attrs.get("custom:user_type") != "corporation"
            or group.get("GroupName") != actor_id
        ):
            raise ActionDeniedError()
        return actor_id

    def require_owned_employee(self, user_id):
        actor_id = self.require_corporation_admin()
        if not user_id or user_id == actor_id:
            raise ActionDeniedError()
        user = self.cognito.cognito.admin_get_user(
            UserPoolId=COGNITO_USER_POOL_ID, Username=user_id
        )
        attrs = {item["Name"]: item["Value"] for item in user.get("UserAttributes", [])}
        if attrs.get("custom:user_type") != "employee":
            raise ActionDeniedError()
        token = None
        seen = set()
        while True:
            kwargs = {"UserPoolId": COGNITO_USER_POOL_ID, "Username": user_id}
            if token:
                kwargs["NextToken"] = token
            response = self.cognito.cognito.admin_list_groups_for_user(**kwargs)
            if any(group.get("GroupName") == actor_id for group in response.get("Groups", [])):
                return user
            token = response.get("NextToken")
            if not token:
                raise ActionDeniedError()
            if token in seen:
                raise RuntimeError("Cognito pagination did not advance")
            seen.add(token)

    def show(self, corporation_id=None):
        if not corporation_id:
            corporation_id = self.corporation_id

        users = self.list_users()
        self.billing.set_users(users)
        billing = self.billing.show(corporation_id)
        return {"users": users, "billing": billing}

    def list_corporations(self):
        corporations = self.cognito.list_groups()
        return [corp["GroupName"] for corp in corporations["Groups"]]

    # その企業IDに結びついたユーザー一覧を取得
    def list_users(self):
        users = self.cognito.list_users_in_group(group_name=self.corporation_id)

        ret = []
        for user in users:
            # Group membership is authoritative. Optional Cognito profile fields
            # must not make one member break the entire list or monthly billing.
            user_id = user.get("Username")
            if not user.get("Enabled") or not isinstance(user_id, str) or not user_id:
                continue
            attributes = {attr["Name"]: attr["Value"] for attr in user.get("Attributes", [])}
            created_at = user.get("UserCreateDate")
            fallback_date = created_at.isoformat() if isinstance(created_at, datetime) else ""
            member = dict(attributes)
            member.update(
                {
                    "user_id": user_id,
                    # Never infer employee authority from absent profile attributes.
                    "user_type": member.pop("custom:user_type", ""),
                    "kana_username": member.pop("custom:kana_username", ""),
                    "created_at": member.pop("custom:created_at", fallback_date),
                }
            )
            for key in ("custom:pricing_plan", "email_verified", "sub"):
                member.pop(key, None)
            ret.append(member)

        return ret

    def create_user(self, corporation_id, username, kana_username, email, password):
        if corporation_id != self.require_corporation_admin():
            raise ActionDeniedError()
        custom_attr = {
            "custom:kana_username": kana_username,
            "custom:pricing_plan": "standard",
            "custom:user_type": "employee",
            "custom:created_at": datetime.now().isoformat(),
        }
        user = self.cognito.create_user(
            username=email,
            email=email,
            password=password,
            preferred_username=username,
            custom_attr=custom_attr,
            verify=True,
        )

        created_id = user["Username"]
        try:
            if not user.get("Enabled"):
                raise RuntimeError("Created employee is not enabled")
            if user.get("UserStatus") == "FORCE_CHANGE_PASSWORD":
                self.cognito.set_user_password(created_id, password)
            self.cognito.add_user_to_group(created_id, corporation_id)
            self.billing.update(event="create_user", user_id=corporation_id, member_id=created_id)
        except Exception:
            # This identifier came from this successful create operation. Do not
            # use the public delete flow: group assignment may itself have failed,
            # so its ownership guard cannot authorize cleanup of this new user.
            try:
                self.cognito.disable_user(created_id)
            except Exception as cleanup_error:
                self.logger.error(
                    "employee_provisioning_cleanup_failed error_type=%s",
                    type(cleanup_error).__name__,
                )
                raise RuntimeError(
                    "Employee provisioning incomplete; administrator recovery required"
                ) from None
            raise
        result = dict(user)
        result.pop("UserCreateDate", None)
        result.pop("UserLastModifiedDate", None)
        return result

    def delete_user(self, user_id):
        self.require_owned_employee(user_id)
        response = self.cognito.disable_user(user_id)
        if response.get("ResponseMetadata", {}).get("HTTPStatusCode") != 200:
            raise RuntimeError("Unable to disable corporation user")
        return {"message": "successfully"}
