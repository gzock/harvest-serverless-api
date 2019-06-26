import os, sys
import json
import logging
import traceback
from datetime import datetime

from abc import ABCMeta, abstractmethod
from harvest.controllers.project_controller import ProjectController
from harvest.controllers.project_user_controller import ProjectUserController
from harvest.drivers.cognito_driver import CognitoDriver

sys.path.append(os.path.join(os.path.dirname(__file__), '../routes/site-packages'))

DYNAMO_HOST = os.environ.get("DYNAMO_HOST")
DYNAMO_PORT = os.environ.get("DYNAMO_PORT")
COGNITO_USER_POOL_ID="ap-northeast-1_KrEPljcrG"
ADMINS = ["owner", "admin"]

def lambda_handler(event, context):
  formatter = logging.Formatter('[%(levelname)s]\t%(asctime)s.%(msecs)dZ\t%(aws_request_id)s\t[%(module)s#%(funcName)s %(lineno)d]\t%(message)s')

  logger = logging.getLogger()
  for handler in logger.handlers:
    handler.setFormatter(formatter)
    handler.setLevel(logging.DEBUG)

  logger = logging.getLogger(__name__)
  logger.setLevel(logging.DEBUG)

  # 入力されたrecordsをrecordに分割
  users = ProjectUserController(DYNAMO_HOST, DYNAMO_PORT)
  notify = NotificationMessageFactory(DYNAMO_HOST, DYNAMO_PORT)
  for record in event["Records"]:
    notify.set_stream_record(record)
    messages = notify.generate()
    print("---")
    print(messages)
    print("---")

# 必要なユーザー用にメッセージを書き込み
# おわり
  return

class NotificationMessageFactory():
  __stream_record = {}
  __created_at = ""
  __updated_at = ""
  host = None
  port = None

  def __init__(self, host, port, stream_record=None):
    if stream_record:
      self.set_stream_record(stream_record)
    self.host = host
    self.port = port

  def serialize(self, stream_record):
    if "NewImage" in stream_record["dynamodb"]:
      stream_record["dynamodb"]["NewImage"] = { k:list(v.values())[0] for k, v in stream_record["dynamodb"]["NewImage"].items() }

    if "OldImage" in stream_record["dynamodb"]:
      stream_record["dynamodb"]["OldImage"] = { k:list(v.values())[0] for k, v in stream_record["dynamodb"]["OldImage"].items() }

    if "Keys" in stream_record["dynamodb"]:
      stream_record["dynamodb"]["Keys"] = { k:list(v.values())[0] for k, v in stream_record["dynamodb"]["Keys"].items() }
    return stream_record

  def set_stream_record(self, stream_record):
    self.__stream_record = self.serialize(stream_record)
  
  def __select_notification_type(self):
    new_record = old_record = {}
    if "NewImage" in self.__stream_record["dynamodb"]:
      new_record = self.__stream_record["dynamodb"]["NewImage"]
    if "OldImage" in self.__stream_record["dynamodb"]:
      old_record = self.__stream_record["dynamodb"]["OldImage"]

    arn = self.__stream_record['eventSourceARN']
    src_table = arn.split(':')[5].split('/')[1]

    notification = {}
    if src_table == "Projects":
      notification = ProjectNotification(self.__stream_record, self.host, self.port)

    elif src_table == "Places":
      notification = PlaceNotification(self.__stream_record, self.host, self.port)

    elif src_table == "Targets":
      
      if "photos" in old_record and old_record["photos"] != new_record["photos"]:
        notification = PhotoNotification(self.__stream_record, self.host, self.port)
      else:
        notification = TargetNotification(self.__stream_record, self.host, self.port)
      
    elif src_table == "Roles":
      notification = ProjectUserNotification(self.__stream_record, self.host, self.port)

    return notification

  def generate(self):
    notification = self.__select_notification_type()
    return notification.generate()

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
  notify_users = []

  def __init__(self, stream_record, host, port):
    self.project = ProjectController(host, port)
    self.users = ProjectUserController(host, port)
    self.cognito = CognitoDriver(COGNITO_USER_POOL_ID)
    self.set_stream_record(stream_record)

    print("processing stream_record: %s" % str(stream_record))
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

    self.project_id = self.record["project_id"]
    self.user_id = self.record["updated_by"]
    print("project_id: %s" % self.project_id)
    print("user_id: %s" % self.user_id)

  def set_stream_record(self, record):
    self.record = record
    self.users.set_project_id(record["dynamodb"]["Keys"]["project_id"])

  def __get_project_name(self):
    project = self.project.show(
        self.project_id
    )
    return project["name"]

  def __get_user_name(self):
    detail = self.cognito.show_user(self.user_id)[0]["Attributes"]
    detail = { item["Name"]: item["Value"] for item in detail }
    return detail["preferred_username"]

  def select_message(self):
    if self.is_insert_event:
      self.message = self.create

    elif self.is_modify_event:
      self.message = self.update

    elif self.is_remove_event:
      self.message = self.delete
    return self.message

  def mapping(self):
    self.message = self.message.format_map(
      self.needs_strings_dict
    )

  def set_record_type(self, record_type):
    self.needs_strings_dict["type"] = record_type

  def get_notify_users(self, target, user_id=None):
    if target not in ["all", "admins", "specify"]:
      # TODO: needs custom exception
      raise ValueError

    project_users = self.users.list()
    # TODO: needs delete myself user_id
    if target == "all":
      pass
    elif target == "admins":
      filterd_users = filter(lambda user: user["role"] in ADMINS, project_users)
      project_users = [user for user in filterd_users]
    elif target == "specify":
      if not user_id:
        # TODO: needs custom exception
        raise ValueError
      project_users = [ {"user_id": user_id} ]
    return project_users

  def generate(self):
    if not self.message:
      self.select_message()
    if not self.notify_users:
      self.notify_users = self.get_notify_users("all")
    self.needs_strings_dict["project_name"] = self.__get_project_name()
    self.needs_strings_dict["user_name"] = self.__get_user_name()
    self.mapping()
    ret = []
    if self.message:
      for user in self.notify_users:
        ret.append(
          {
            "user_id": user["user_id"],
            "project_id": self.project_id,
            "created_at": self.record["updated_at"],
            "message": self.message
          }
        )
    return ret

class ProjectNotification(Notification):
  update_name = "{project_name}: {user_name}さんによってプロジェクト名が{old_name}から{name}に変更されました"
  update_start_on = "{project_name}: {user_name}さんによってプロジェクトの開始日が{old_date}から{date}に変更されました"
  update_complete_on = "{project_name}: {user_name}さんによってプロジェクトの終了日が{old_date}から{date}に変更されました"
  delete = "{project_name}: {user_name}さんによって削除されました"

  def __init__(self, stream_record, host, port):
    super().set_record_type("プロジェクト")
    super().__init__(stream_record, host, port)

  def iso8601_to_simple(self, iso8601):
    dt = datetime.strptime(iso8601, '%Y-%m-%dT%H:%M:%S.%fZ')
    return dt.strftime("%Y/%m/%d")

  def select_message(self):
    if self.is_insert_event:
      pass

    elif self.is_modify_event:
      if self.old_record["name"] != self.record["name"]:
        self.message = self.update_name
        self.needs_strings_dict.update(
          {
            "old_name": self.old_record["name"]
          }
        )

      elif self.old_record["start_on"] != self.record["start_on"]:
        self.message = self.update_start_on
        self.needs_strings_dict.update(
          {
            "old_date": self.iso8601_to_simple(self.old_record["start_on"]),
            "date": self.iso8601_to_simple(self.record["start_on"])
          }
        )

      elif self.old_record["complete_on"] != self.record["complete_on"]:
        self.message = self.update_start_on
        self.needs_strings_dict.update(
          {
            "old_date": self.iso8601_to_simple(self.old_record["complete_on"]),
            "date": self.iso8601_to_simple(self.record["complete_on"])
          }
        )

    elif self.is_remove_event:
      self.message = self.delete
      self.needs_strings_dict.update(
        {
          "name": self.record["name"]
        }
      )
    self.notify_users = self.get_notify_users("all")

class PlaceNotification(Notification):

  def __init__(self, stream_record, host, port):
    super().set_record_type("場所")
    super().__init__(stream_record, host, port)

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

class TargetNotification(Notification):

  def __init__(self, stream_record, host, port):
    super().set_record_type("撮影対象")
    super().__init__(stream_record, host, port)

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

class PhotoNotification(Notification):
  create = "{project_name}: {user_name}さんによって{name}の写真が撮影されました"
  update = "{project_name}: {user_name}さんによって{name}の採用写真が変更されました"
  delete = "{project_name}: {user_name}さんによって{name}の写真が削除されました"

  def __init__(self, stream_record, host, port):
    super().set_record_type("撮影対象")
    super().__init__(stream_record, host, port)

  def generate(self):
    self.needs_strings_dict.update(
      {
        "name": self.record["name"]
      }
    )
    return super().generate()

class ProjectUserNotification(Notification):
  accept = "{project_name}: {user_name}さんが参加しました"
  request = "{project_name}: {user_name}さんが参加を希望しています"
  delete = "{project_name}: {user_name}さんが離脱しました"
  reject = "{project_name}: 参加を拒否されました"
  update_role = "{project_name}: ロールが{role}に変更されました"
  force_delete = "{project_name}: 強制的に離脱させられました"

  def __init__(self, stream_record, host, port):
    super().set_record_type("ユーザー管理")
    super().__init__(stream_record, host, port)

  def select_message(self):
    if self.is_insert_event:
      if self.record["status"] == "request":
        self.message = self.request
        self.notify_users = self.get_notify_users("admins")

    elif self.is_modify_event:
      if self.old_record["role"] != self.record["role"]:
        self.message = self.update_role
        self.notify_users = self.get_notify_users("specify", self.record["user_id"])

      elif (self.old_record["status"] == "reject" or self.old_record["status"] == "request") \
          and self.record["status"] == "accept":
        self.message = self.accept
        self.notify_users = self.get_notify_users("all")

      elif self.record["status"] == "reject":
        self.message = self.reject
        self.notify_users = self.get_notify_users("specify", self.record["user_id"])

    elif self.is_remove_event:
      self.message = self.delete
      self.notify_users = self.get_notify_users("all")

    return self.message

  def generate(self):
    self.needs_strings_dict.update(
      {
        "role": self.record["role"]
      }
    )
    return super().generate()

