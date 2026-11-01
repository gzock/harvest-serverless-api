from harvest.controllers.role_controller import RoleController


class Auth:
    ROLES = ("guest", "reporter", "worker", "admin", "owner")

    def __init__(self, host, port, user_id=None, project_id=None):
        self.__role = RoleController(host, port)
        self.__user_id = None
        self.__project_id = None
        self.my_role = None
        if user_id:
            self.set_user_id(user_id)
        if project_id:
            self.set_project_id(project_id)

    def set_user_id(self, user_id):
        self.__user_id = str(user_id) if user_id else None
        self.my_role = None
        self.__role.set_user_id(self.__user_id)

    def set_project_id(self, project_id):
        self.__project_id = str(project_id) if project_id else None
        self.my_role = None
        self.__role.set_project_id(self.__project_id)

    def get_role(self):
        self.my_role = None
        if self.__user_id and self.__project_id:
            record = self.__role.show()
            if record and self.is_active(record) and record.get("role") in self.ROLES:
                self.my_role = record["role"]
        return self.my_role

    def guard(self, role=None, allow_deleting=False, allow_importing=False):
        current = self.get_role()
        if current not in self.ROLES or (role is not None and role not in self.ROLES):
            return False
        permitted = role is None or self.ROLES.index(current) >= self.ROLES.index(role)
        options = {"allow_deleting": allow_deleting}
        if allow_importing:
            options["allow_importing"] = True
        return permitted and self.__role.project_is_available(**options)

    @staticmethod
    def is_active(role):
        # Legacy membership records predate the status field.
        return role.get("status", "active") == "active"
