import os, sys
import json
import logging
import traceback
import ABCmeta, abstractmethod

sys.path.append(os.path.join(os.path.dirname(__file__), '../routes/site-packages'))
from harvest import Project

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

  #try:
  #  project = Project(DYNAMO_HOST, DYNAMO_PORT)
  #  req = RequestDecorator(event)
  #except Exception as e:
  #  raise e

# 入力されたrecordsをrecordに分割
  for record in event["Records"]:
    print(record["eventName"])
    print(record["dynamodb"]["Keys"])

    if "NewImage" in record["dynamodb"]:
      print(record["dynamodb"]["NewImage"])

    if "OldImage" in record["dynamodb"]:
      print(record["dynamodb"]["OldImage"])

# それぞれの種類に分割してconveterにかましてループ
# 出力されたメッセージを保持
# メッセージから必要なユーザーをリストアップ
# 必要なユーザー用にメッセージを書き込み
# おわり
# 

def converter(record):

  event_name = record["eventName"]
  message = ""
  if event_name == "INSERT":
    message = "{username}さんによって{type}:{name}が追加されました"

  elif event_name == "MODIFY":

  elif event_name == "REMOVE":



class Notification(metaclass=ABCMeta):
  self.__stream_record = {}
  self.__project_name = ""
  self.__user_name = ""
  self.__created_at = ""
  self.__updated_at = ""

  @abstractmethod
  def set_stream_record(self):
    pass

  @abstractmethod
  def set_project_name(self):
    pass

  @abstractmethod
  def set_user_name(self):
    pass

  @abstractmethod
  def (self):
    pass

class NotificationFactory(metaclass=ABCMeta):
  self.__stream_record = {}
  self.__project_name = ""
  self.__user_name = ""
  self.__created_at = ""
  self.__updated_at = ""

  def __init_(self, project_name, user_name, stream_record):
    self.set_stream_record(stream_record)
    self.set_project_name(project_name)
    self.set_user_name(user_name)

  def set_stream_record(self, stream_record):
    self.__stream_record = stream_record

  def set_project_name(self, project_name):
    self.__project_name = project_name

  def set_user_name(self, user_name):
    self.__user_name = user_name

  def select_notification(self):
    new_record = self.__stream_record["dynamodb"]["NewImage"]
    old_record = {}
    if "OldImage" in self.__stream_record["dynamodb"]:
      old_record = self.__stream_record["dynamodb"]["OldImage"]

    arn = record['eventSourceARN']
    src_table = .split(':')[5].split('/')[1]

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

  def event_type(self)
    event_namae = self.__stream_record["eventName"]
    if event_name == "INSERT":
    elif event_name == "MODIFY":
    elif event_name == "REMOVE":

  def generate(self):
    notification = select_notification(self.__stream_record)
    message = notification.message

class Notification(metaclass=ABCMeta):
  @abstractmethod
  def set_src_stream_record(self, record):
    pass

class ProjectNotification(Notification):
  self.create = "{user_name}さんによって{type}:{name}が追加されました"
  self.update = "{user_name}さんによって{type}:{old_name}が{new_name}に追加されました"
  self.delete = "{user_name}さんによって{type}:{name}が削除されました"
  self.message = ""

  def set_src_stream_record(self, record):
    event_name = record["eventName"]
    if event_name == "INSERT":
      self.message = self.create
    elif event_name == "MODIFY":
      self.message = self.update
    elif event_name == "REMOVE":
      self.message = self.delete

class PlaceNotification():
  self.create = "{project_name}: {user_name}さんによって{type}:{name}が追加されました"
  self.update = "{project_name}: {user_name}さんによって{type}:{old_name}が{new_name}に追加されました"
  self.delete = "{project_name}: {user_name}さんによって{type}:{name}が削除されました"
  self.message = ""

  def set_src_stream_record(self, record):
    event_name = record["eventName"]
    if event_name == "INSERT":
      self.message = self.create
    elif event_name == "MODIFY":
      self.message = self.update
    elif event_name == "REMOVE":
      self.message = self.delete

class TargetNotification():
  self.create = "{project_name}: {user_name}さんによって{type}:{name}が追加されました"
  self.update = "{project_name}: {user_name}さんによって{type}:{old_name}が{new_name}に追加されました"
  self.delete = "{project_name}: {user_name}さんによって{type}:{name}が削除されました"
  self.message = ""

  def set_src_stream_record(self, record):
    event_name = record["eventName"]
    if event_name == "INSERT":
      self.message = self.create
    elif event_name == "MODIFY":
      self.message = self.update
    elif event_name == "REMOVE":
      self.message = self.delete

class PhotoNotification():
  self.create = "{project_name}: {user_name}さんによって{name}の写真が撮影されました"
  self.update = "{project_name}: {user_name}さんによって{name}の採用写真が変更されました"
  self.delete = "{project_name}: {user_name}さんによって{name}の写真が削除されました"
  self.message = ""

  def set_src_stream_record(self, record):
    event_name = record["eventName"]
    if event_name == "INSERT":
      self.message = self.create
    elif event_name == "MODIFY":
      self.message = self.update
    elif event_name == "REMOVE":
      self.message = self.delete

class ProjectUserNotification():
  self.accept = "{project_name}: {user_name}さんが参加しました"
  self.request = "{project_name}: {user_name}さんが参加を希望しています"
  self.delete = "{project_name}: {user_name}さんが離脱しました"
  self.reject = "{project_name}: {user_name}さんによって参加を拒否されました"
  self.update_role = "{project_name}: {user_name}さんによってロールが{role}に変更されました"
  self.force_delete = "{project_name}: {user_name}さんによって強制的に離脱させられました"
  self.message = ""

  def set_src_stream_record(self, record):
    event_name = record["eventName"]
    if event_name == "INSERT":
      if new_record["status"] == "request":
        self.message = self.request

    elif event_name == "MODIFY":
      new_record, old_record = split_record(record)
      if old_record["role"] != new_record["role"]:
        self.message = self.update_role
      if (old_record["status"] == "reject"] or old_record["status"] == "request") \
          and new_record["status"] == "accept":
        self.message = self.accept
      elif new_record["status"] == "reject":
        self.message = self.reject

    elif event_name == "REMOVE":
      self.message = self.delete

  def split_record(self, record):
    new_record = record["dynamodb"]["NewImage"]
    old_record = {}
    if "OldImage" in record["dynamodb"]:
      old_record = record["dynamodb"]["OldImage"]
    return new_record, old_record
