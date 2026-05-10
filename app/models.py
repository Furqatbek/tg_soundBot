from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .db import Base


class Pack(Base):
    __tablename__ = "packs"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    slug = Column(String(50), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


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
    storage_path = Column(String(500))
    play_count = Column(Integer, nullable=False, default=0, server_default="0")
    pack_id = Column(Integer, ForeignKey("packs.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    pack = relationship("Pack", lazy="selectin")
