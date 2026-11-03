from harvest.commons.notifications.base_notification import BaseNotification


class TargetNotification(BaseNotification):
    def __init__(self, stream_record, host, port):
        super().set_record_type("撮影対象")
        super().__init__(stream_record, host, port)

    def select_message(self):
        if self.is_insert_event:
            self.message = self.create
        elif self.is_remove_event:
            self.message = self.delete
        elif self.is_modify_event and self.old_record.get("name") != self.record.get("name"):
            self.message = self.update
        return self.message

    def generate(self):
        self.needs_strings_dict.update({"name": self.record["name"]})
        if self.is_modify_event:
            self.needs_strings_dict.update({"old_name": self.old_record["name"]})
        return super().generate()
