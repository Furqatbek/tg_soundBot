from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from .db import Base


class Pack(Base):
    __tablename__ = "packs"

    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False)
    slug = Column(String(50), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    telegram_id = Column(BigInteger, primary_key=True)
    username = Column(String(64))
    first_name = Column(String(120))
    last_seen = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class PlayEvent(Base):
    __tablename__ = "play_events"

    id = Column(Integer, primary_key=True)
    sound_id = Column(Integer, ForeignKey("sounds.id"), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class MissedSearch(Base):
    """Inline queries that returned zero results — content-gap log."""

    __tablename__ = "missed_searches"

    id = Column(Integer, primary_key=True)
    query = Column(String(255), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


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
