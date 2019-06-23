import os, sys
import json
import logging
import traceback
from abc import ABCMeta, abstractmethod
from harvest.controllers.project_controller import ProjectController
from harvest.controllers.project_user_controller import ProjectUserController

sys.path.append(os.path.join(os.path.dirname(__file__), '../routes/site-packages'))
#from harvest import Project

DYNAMO_HOST = os.environ.get("DYNAMO_HOST")
DYNAMO_PORT = os.environ.get("DYNAMO_PORT")

def lambda_handler(event, context):
  formatter = logging.Formatter('[%(levelname)s]\t%(asctime)s.%(msecs)dZ\t%(aws_request_id)s\t[%(module)s#%(funcName)s %(lineno)d]\t%(message)s')

  logger = logging.getLogger()
  for handler in logger.handlers:
    handler.setFormatter(formatter)
    handler.setLevel(logging.DEBUG)

  logger = logging.getLogger(__name__)
  logger.setLevel(logging.DEBUG)

  # 入力されたrecordsをrecordに分割
  notify = NotificationFactory()
  for record in event["Records"]:
    notify.set_stream_record(record)
    message = notify.generate()
    print(message)

# それぞれの種類に分割してconveterにかましてループ
# 出力されたメッセージを保持
# メッセージから必要なユーザーをリストアップ
# 必要なユーザー用にメッセージを書き込み
# おわり


class NotificationConverter():
  __project_name = ""
  __user_name = ""
  factory = None

  def __init_(self, stream_record=None):
    if stream_record:
      self.factory = NotificationMessageFactory(stream_record)

  def set_project_name(self, project_name):
    self.__project_name = project_name

  def set_user_name(self, user_name):
    self.__user_name = user_name

  def input(self, stream_record):
    self.factory = NotificationMessageFactory(stream_record)

  def output(self):
    if self.factory:
      return self.factory.generate()


class NotificationMessageFactory():
  __stream_record = {}
  __created_at = ""
  __updated_at = ""

  def __init_(self, stream_record):
    self.set_stream_record(stream_record)

    self.project = ProjectController(host, port)
    self.user = ProjectUserController(host, port)

  def set_stream_record(self, stream_record):
    self.__stream_record = stream_record

  def __get_project_name(self, project_id):
    self.__project_name = self.project.show(project_id)

  def __get_user_name(self, user_id):
    self.__user_name = self.user.show(user_id)

  def __select_notification_type(self):
    new_record = old_record = {"photos": ""}
    if "NewImage" in self.__stream_record["dynamodb"]:
      new_record = self.__stream_record["dynamodb"]["NewImage"]
    if "OldImage" in self.__stream_record["dynamodb"]:
      old_record = self.__stream_record["dynamodb"]["OldImage"]

    arn = self.__stream_record['eventSourceARN']
    src_table = arn.split(':')[5].split('/')[1]
    print(src_table)

    notification = {}
    if src_table == "Projects":
      notification = ProjectNotification()

    elif src_table == "Places":
      notification = PlaceNotification()

    elif src_table == "Targets":
      
      if old_record["photos"] == new_record["photos"]:
        notification = PhotoNotification()
      else:
        notification = TargetNotification()
      
    elif src_table == "Roles":
      notification = ProjectUserNotification()

    notification.set_src_stream_record(self.__stream_record)
    return notification

  def __get_project_id(self, record)
    return record["project_id"]

  def __get_user_id(self, record)
    if "updated_by" in record:
      user_id = record["updated_by"]
    elif "user_id" in record:
      user_id = record["user_id"]
    else:
      raise AttributeError
    return user_id

  def generate(self):
    notification = self.__select_notification_type()
    message = notification.message
    if "project_name"in message:
      message.format_map(
        {
          "project_name": self.__get_project_name()
        }
      )
    if "user_name"in message:
      message.format_map(
        {
          "user_name": self.__get_user_name()
        }
      )
    return notification.message



#class Notification(metaclass=ABCMeta):
#  @abstractmethod
#  def set_src_stream_record(self, record):
#    pass

class Notification():
  __stream_record = {}
  create = "{project_name}: {user_name}さんによって{type}:{name}が追加されました"
  update = "{project_name}: {user_name}さんによって{type}:{old_name}が{name}に変更されました"
  delete = "{project_name}: {user_name}さんによって{type}:{name}が削除されました"
  message = ""
  needs_strings_dict = {"project_name": "", "user_name": "", "type": ""}
  is_insert_event = False
  is_modify_event = False
  is_remove_event = False
  record = {}
  old_record = None

  def __init_(self, stream_record, host=None, port=None):
    self.set_stream_record(stream_record)
    self.project = ProjectController(host, port)
    self.user = ProjectUserController(host, port)

    event_name = stream_record["eventName"]
    if event_name == "INSERT":
      self.is_insert_event = True
      self.record = stream_record["dynamodb"]["NewImage"]

    elif event_name == "MODIFY":
      self.is_modify_event = True
      self.record = stream_record["dynamodb"]["NewImage"]
      self.old_record = stream_record["dynamodb"]["OldImage"]

    elif event_name == "REMOVE":
      self.is_remove_event = True
      self.record = stream_record["dynamodb"]["OldImage"]

  def set_stream_record(self, record):
    self.record = record

  def __get_project_name(self):
    return self.project.show(
        self.record["project_id"]
    )

  def __get_user_name(self, user_id):
    return  self.user.show(
        self.record["updated_by"]
    )

  def select_message(self):
    event_name = record["eventName"]
    if self.is_insert_event:
      self.message = self.create

    elif self.is_modify_event:
      self.message = self.update

    elif self.is_remove_event:
      self.message = self.delete
    return self.message

  def mapping(self):
    self.message.format_map(
      self.needs_strings_dict
    )

  def set_record_type(self, record_type):
    self.need_strings_dict["type"] = record_type

  def generate(self):
    if not message():
      self.select_message()
    self.needs_strings_dict["project_name"] = self.__get_project_name()
    self.needs_strings_dict["user_name"] = self.__get_user_name()
    self.mapping()
    return self.message

class ProjectNotification(Notification):
  update = "{project_name}: {user_name}さんによってプロジェクト名が{old_name}から{name}に変更されました"
  delete = "{project_name}: {user_name}さんによって削除されました"

  def __init__(self, host, port):
    super().set_record_type("プロジェクト")
    super().__init_(host, port)

  def generate(self):
    self.needs_strings_dict.update(
      {
        "name": self.record["name"]
      }
    )
    if self.is_modify_event:
    self.needs_strings_dict.update(
      {
        "old_name": self.old_record["name"]
      }
    )
    return super().generate()


class PlaceNotification():

  def __init__(self, host, port):
    super().set_record_type("場所")
    super().__init_(host, port)

  def generate(self):
    self.needs_strings_dict.update(
      {
        "name": self.record["name"]
      }
    )
    if self.is_modify_event:
    self.needs_strings_dict.update(
      {
        "old_name": self.old_record["name"]
      }
    )
    return super().generate()

class TargetNotification():

  def __init__(self, host, port):
    super().set_record_type("撮影対象")
    super().__init_(host, port)

  def generate(self):
    self.needs_strings_dict.update(
      {
        "name": self.record["name"]
      }
    )
    if self.is_modify_event:
    self.needs_strings_dict.update(
      {
        "old_name": self.old_record["name"]
      }
    )
    return super().generate()

class PhotoNotification():
  create = "{project_name}: {user_name}さんによって{name}の写真が撮影されました"
  update = "{project_name}: {user_name}さんによって{name}の採用写真が変更されました"
  delete = "{project_name}: {user_name}さんによって{name}の写真が削除されました"

  def __init__(self, host, port):
    super().set_record_type("撮影対象")
    super().__init_(host, port)

  def generate(self):
    self.needs_strings_dict.update(
      {
        "name": self.record["name"]
      }
    )
    return super().generate()

class ProjectUserNotification():
  accept = "{project_name}: {user_name}さんが参加しました"
  request = "{project_name}: {user_name}さんが参加を希望しています"
  delete = "{project_name}: {user_name}さんが離脱しました"
  reject = "{project_name}: 参加を拒否されました"
  update_role = "{project_name}: ロールが{role}に変更されました"
  force_delete = "{project_name}: 強制的に離脱させられました"

  def __init__(self, host, port):
    super().set_record_type("ユーザー管理")
    super().__init_(host, port)

  def select_message(self):
    event_name = self.record["eventName"]
    if self.is_insert_event:
      if self.record["status"] == "request":
        self.message = self.request

    elif self.is_modify_event:
      if self.old_record["role"] != self.record["role"]:
        self.message = self.update_role
      elif (self.old_record["status"] == "reject" or self.old_record["status"] == "request") \
          and self.record["status"] == "accept":
        self.message = self.accept
      elif self.record["status"] == "reject":
        self.message = self.reject

    elif self.is_remove_event:
      self.message = self.delete
    return self.message

  def generate(self):
    self.needs_strings_dict.update(
      {
        "role": self.record["role"]
      }
    )
    return super().generate()

