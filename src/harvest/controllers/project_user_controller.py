import logging
import os
from base64 import urlsafe_b64decode
from uuid import UUID

from harvest.controllers.role_controller import RoleController
from harvest.drivers.cognito_driver import CognitoDriver

COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")


class ProjectUserController:
    logger = logging.getLogger(__name__)
    logger.addHandler(logging.NullHandler())
    logger.setLevel(logging.DEBUG)

    def __init__(self, host, port):
        self.role = RoleController(host, port)

    def set_user_id(self, user_id):
        self.user_id = str(user_id)
        self.role.set_user_id(str(user_id))

    def set_project_id(self, project_id):
        if len(project_id) == 22:
            project_id = str(UUID(bytes=urlsafe_b64decode(project_id + "==")))
        self.project_id = str(project_id)
        self.role.set_project_id(str(project_id))

    def list(self):
        if not self.project_id:
            raise ValueError

        ret = []
        cognito = CognitoDriver(COGNITO_USER_POOL_ID)
        users = self.role.list_users()
        for user in users:
            detail = cognito.show_user(user["user_id"])[0]["Attributes"]

            detail = {item["Name"]: item["Value"] for item in detail}

            if not "status" in user:
                user.update({"status": "active"})
            ret.append(
                {
                    "user_id": user["user_id"],
                    "username": detail["preferred_username"],
                    "email": detail["email"],
                    "role": user["role"],
                    "status": user["status"],
                }
            )
        return ret

    def show(self, user_id):
        cognito = CognitoDriver(COGNITO_USER_POOL_ID)
        user = self.role.show(user_id)

        if not user:
            # TODO: カスタム例外なげる
            raise ValueError

        detail = cognito.show_user(user_id)[0]["Attributes"]
        detail = {item["Name"]: item["Value"] for item in detail}

        if not detail:
            # TODO: カスタム例外なげる
            raise ValueError

        ret = {
            "user_id": user_id,
            "username": detail["preferred_username"],
            "email": detail["email"],
            "role": user["role"],
        }
        return ret

    def join(self, user_id=None):
        if not self.project_id:
            raise ValueError
        if not user_id:
            user_id = self.user_id
        return self.role.create(
            "worker", user_id=user_id, project_id=self.project_id, status="request"
        )

    def accept(self, user_id=None):
        if not self.project_id:
            raise ValueError
        if not user_id:
            user_id = self.user_id
        return self.role.update_status(status="active", user_id=user_id)

    def reject(self, user_id=None):
        if not self.user_id or not self.project_id:
            raise ValueError
        if not user_id:
            user_id = self.user_id
        return self.role.update_status(status="reject", user_id=user_id)

    def delete(self, user_id=None):
        if not self.user_id or not self.project_id:
            raise ValueError
        if not user_id:
            user_id = self.user_id
        return self.role.delete(user_id)

    def update_role(self, role, user_id=None):
        if not self.project_id:
            raise ValueError
        if not user_id:
            user_id = self.user_id
        return self.role.update(role, user_id)
