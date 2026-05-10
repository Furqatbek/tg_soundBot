from sqlalchemy import Column, DateTime, Integer, String
from sqlalchemy.sql import func

from .db import Base


class Sound(Base):
    __tablename__ = "sounds"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False, index=True)
    tags = Column(String(500), nullable=False, default="")
    file_id = Column(String(255), nullable=False)
    file_unique_id = Column(String(255))
    kind = Column(String(20), nullable=False)  # voice | audio | document
    mime_type = Column(String(120))
    duration = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
