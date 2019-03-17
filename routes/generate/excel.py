import os, sys
import json
import logging

sys.path.append(os.path.join(os.path.dirname(__file__), '../site-packages'))
from harvest import Generate

#DYNAMO_HOST = "10.0.2.15"
#DYNAMO_PORT = "8000"
DYNAMO_HOST = None
DYNAMO_PORT = None

logger = logging.getLogger()
logger.setLevel(logging.INFO)

gen = Generate(DYNAMO_HOST, DYNAMO_PORT)

print( gen.create_download_link("f8dab64a-e650-4401-a0ec-5dbd551a0852", gen_type="zip") )

