import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
from dotenv import load_dotenv
load_dotenv()
from database.db import engine
from database.models import Base

Base.metadata.create_all(engine)
print("Database tables created.")
