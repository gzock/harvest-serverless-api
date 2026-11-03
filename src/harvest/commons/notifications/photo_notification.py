from harvest.commons.notifications.base_notification import BaseNotification


class PhotoNotification(BaseNotification):
    shot_before_photo = "{project_name}: {user_name}さんが{name}の施工前写真を撮影しました"
    shot_after_photo = "{project_name}: {user_name}さんが{name}の施工後写真を撮影しました"
    update_adopt_before_photo = (
        "{project_name}: {user_name}さんが{name}の施工前採用写真を変更しました"
    )
    update_adopt_after_photo = (
        "{project_name}: {user_name}さんが{name}の施工後採用写真を変更しました"
    )
    delete_before_photo = "{project_name}: {user_name}さんが{name}の施工前写真を削除しました"
    delete_after_photo = "{project_name}: {user_name}さんが{name}の施工後写真を削除しました"

    def __init__(self, stream_record, host, port):
        super().set_record_type("撮影対象")
        super().__init__(stream_record, host, port)

    def select_message(self):
        if self.is_insert_event:
            pass

        elif self.is_modify_event:
            if len(self.old_record["photos"]["before"]) < len(self.record["photos"]["before"]):
                self.message = self.shot_before_photo

            elif len(self.old_record["photos"]["after"]) < len(self.record["photos"]["after"]):
                self.message = self.shot_after_photo

            elif len(self.old_record["photos"]["before"]) > len(self.record["photos"]["before"]):
                self.message = self.delete_before_photo

            elif len(self.old_record["photos"]["after"]) > len(self.record["photos"]["after"]):
                self.message = self.delete_after_photo

            elif (
                self.old_record["photos"]["adopt"]["before"]
                != self.record["photos"]["adopt"]["before"]
            ):
                self.message = self.update_adopt_before_photo

            elif (
                self.old_record["photos"]["adopt"]["after"]
                != self.record["photos"]["adopt"]["after"]
            ):
                self.message = self.update_adopt_after_photo

        elif self.is_remove_event:
            pass

        return self.message

    def generate(self):
        self.needs_strings_dict.update({"name": self.record["name"]})
        return super().generate()
