from harvest.controllers.corporation_controller import CorporationController


class Corporation:
    def __init__(self, host=None, port=None):
        self.corp = CorporationController(host, port)

    def set_corporation_id(self, corporation_id):
        self.corp.set_corporation_id(corporation_id)

    def list_users(self):
        self.corp.require_corporation_admin()
        return self.corp.list_users()

    def show(self):
        self.corp.require_corporation_admin()
        return self.corp.show()

    def create_user(self, corporation_id, username, kana_username, email, password):
        return self.corp.create_user(corporation_id, username, kana_username, email, password)

    def delete_user(self, user_id):
        return self.corp.delete_user(user_id)
