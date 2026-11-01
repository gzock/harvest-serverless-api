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

    @staticmethod
    def _profile(cognito, user_id):
        # An administrator must still be able to remove a membership whose
        # Cognito account was deleted. Missing optional profile fields are not
        # evidence that the project membership itself disappeared.
        matches = cognito.show_user(user_id)
        detail = (
            {item["Name"]: item["Value"] for item in matches[0].get("Attributes", [])}
            if matches
            else {}
        )
        return {
            "username": detail.get("preferred_username") or detail.get("email") or user_id,
            "email": detail.get("email", ""),
        }

    def list(self):
        if not self.project_id:
            raise ValueError
        cognito = CognitoDriver(COGNITO_USER_POOL_ID)
        return [
            {
                "user_id": user["user_id"],
                **self._profile(cognito, user["user_id"]),
                "role": user["role"],
                "status": user.get("status", "active"),
            }
            for user in self.role.list_users()
        ]

    def show(self, user_id):
        user = self.role.show(user_id)
        if not user:
            raise ValueError("Project membership not found")
        cognito = CognitoDriver(COGNITO_USER_POOL_ID)
        return {"user_id": user_id, **self._profile(cognito, user_id), "role": user["role"]}

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
