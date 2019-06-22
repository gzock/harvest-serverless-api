import os, sys
import json
import logging
import traceback
from abc import ABCMeta, abstractmethod

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

  #try:
  #  project = Project(DYNAMO_HOST, DYNAMO_PORT)
  #  req = RequestDecorator(event)
  #except Exception as e:
  #  raise e

# 入力されたrecordsをrecordに分割
  notify = NotificationFactory()
  for record in event["Records"]:
    notify.set_stream_record(record)
    message = notify.generate()
    print(message)
    #print(record["eventName"])
    #print(record["dynamodb"]["Keys"])

    #if "NewImage" in record["dynamodb"]:
    #  print(record["dynamodb"]["NewImage"])

    #if "OldImage" in record["dynamodb"]:
    #  print(record["dynamodb"]["OldImage"])

# それぞれの種類に分割してconveterにかましてループ
# 出力されたメッセージを保持
# メッセージから必要なユーザーをリストアップ
# 必要なユーザー用にメッセージを書き込み
# おわり
# 

#def converter(record):
#
#  event_name = record["eventName"]
#  message = ""
#  if event_name == "INSERT":
#    message = "{username}さんによって{type}:{name}が追加されました"
#
#  elif event_name == "MODIFY":
#
#  elif event_name == "REMOVE":

class NotificationFactory():
  __stream_record = {}
  __project_name = ""
  __user_name = ""
  __created_at = ""
  __updated_at = ""

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

  def generate(self):
    notification = self.select_notification()
    message = notification.message
    return message



class Notification(metaclass=ABCMeta):
  @abstractmethod
  def set_src_stream_record(self, record):
    pass

class ProjectNotification(Notification):
  create = "{user_name}さんによって{type}:{name}が追加されました"
  update = "{user_name}さんによって{type}:{old_name}が{new_name}に変更されました"
  delete = "{user_name}さんによって{type}:{name}が削除されました"
  message = ""

  def set_src_stream_record(self, record):
    event_name = record["eventName"]
    if event_name == "INSERT":
      self.message = self.create
    elif event_name == "MODIFY":
      self.message = self.update
    elif event_name == "REMOVE":
      self.message = self.delete

class PlaceNotification():
  create = "{project_name}: {user_name}さんによって{type}:{name}が追加されました"
  update = "{project_name}: {user_name}さんによって{type}:{old_name}が{new_name}に変更されました"
  delete = "{project_name}: {user_name}さんによって{type}:{name}が削除されました"
  message = ""

  def set_src_stream_record(self, record):
    event_name = record["eventName"]
    if event_name == "INSERT":
      self.message = self.create
    elif event_name == "MODIFY":
      self.message = self.update
    elif event_name == "REMOVE":
      self.message = self.delete

class TargetNotification():
  create = "{project_name}: {user_name}さんによって{type}:{name}が追加されました"
  update = "{project_name}: {user_name}さんによって{type}:{old_name}が{new_name}に変更されました"
  delete = "{project_name}: {user_name}さんによって{type}:{name}が削除されました"
  message = ""

  def set_src_stream_record(self, record):
    event_name = record["eventName"]
    if event_name == "INSERT":
      self.message = self.create
    elif event_name == "MODIFY":
      self.message = self.update
    elif event_name == "REMOVE":
      self.message = self.delete

class PhotoNotification():
  create = "{project_name}: {user_name}さんによって{name}の写真が撮影されました"
  update = "{project_name}: {user_name}さんによって{name}の採用写真が変更されました"
  delete = "{project_name}: {user_name}さんによって{name}の写真が削除されました"
  message = ""

  def set_src_stream_record(self, record):
    event_name = record["eventName"]
    if event_name == "INSERT":
      self.message = self.create
    elif event_name == "MODIFY":
      self.message = self.update
    elif event_name == "REMOVE":
      self.message = self.delete

class ProjectUserNotification():
  accept = "{project_name}: {user_name}さんが参加しました"
  request = "{project_name}: {user_name}さんが参加を希望しています"
  delete = "{project_name}: {user_name}さんが離脱しました"
  reject = "{project_name}: {user_name}さんによって参加を拒否されました"
  update_role = "{project_name}: {user_name}さんによってロールが{role}に変更されました"
  force_delete = "{project_name}: {user_name}さんによって強制的に離脱させられました"
  message = ""

  def set_src_stream_record(self, record):
    event_name = record["eventName"]
    if event_name == "INSERT":
      if new_record["status"] == "request":
        self.message = self.request

    elif event_name == "MODIFY":
      new_record, old_record = split_record(record)
      if old_record["role"] != new_record["role"]:
        self.message = self.update_role
      if (old_record["status"] == "reject" or old_record["status"] == "request") \
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
