"""
models.py – SQLAlchemy ORM models for Smart EVM.

SECURITY NOTICE:
  The `Votes` table intentionally has NO voter_id column.
  This is the primary mechanism enforcing ballot anonymity.
  Under no circumstances should a voter_id or fingerprint_hash
  ever be added to the Votes table.
"""

import datetime

from sqlalchemy import (
    Column, Integer, String, Boolean, ForeignKey, create_engine, DateTime
)
from sqlalchemy.orm import relationship, declarative_base, sessionmaker

DATABASE_URL = "sqlite:///./evm.db"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ---------------------------------------------------------------------------
# WebUsers – Dashboard login accounts
# ---------------------------------------------------------------------------
class WebUser(Base):
    __tablename__ = "web_users"

    id            = Column(Integer, primary_key=True, index=True)
    username      = Column(String(64), unique=True, nullable=False, index=True)
    password_hash = Column(String(256), nullable=False)


# ---------------------------------------------------------------------------
# Constituencies – Geographical election areas
# ---------------------------------------------------------------------------
class Constituency(Base):
    __tablename__ = "constituencies"

    id    = Column(Integer, primary_key=True, index=True)
    name  = Column(String(128), unique=True, nullable=False)

    voters = relationship("Voter", back_populates="constituency")
    votes  = relationship("Vote",  back_populates="constituency")


# ---------------------------------------------------------------------------
# Admins – Hardware biometric administrators
# ---------------------------------------------------------------------------
class Admin(Base):
    __tablename__ = "admins"

    id               = Column(Integer, primary_key=True, index=True)
    fingerprint_hash = Column(String(256), unique=True, nullable=False, index=True)
    role             = Column(String(64), nullable=False, default="admin")


# ---------------------------------------------------------------------------
# Voters – Enrolled voters (identity kept separate from votes)
# ---------------------------------------------------------------------------
class Voter(Base):
    __tablename__ = "voters"

    id               = Column(Integer, primary_key=True, index=True)
    fingerprint_hash = Column(String(256), unique=True, nullable=False, index=True)
    constituency_id  = Column(Integer, ForeignKey("constituencies.id"), nullable=False)
    has_voted        = Column(Boolean, default=False, nullable=False)

    constituency = relationship("Constituency", back_populates="voters")


# ---------------------------------------------------------------------------
# Votes – Anonymous ballots (NO voter_id — ballot anonymity guaranteed)
# ---------------------------------------------------------------------------
class Vote(Base):
    __tablename__ = "votes"

    id              = Column(Integer, primary_key=True, index=True)
    constituency_id = Column(Integer, ForeignKey("constituencies.id"), nullable=False)
    candidate_id    = Column(Integer, nullable=False)

    constituency = relationship("Constituency", back_populates="votes")


# ---------------------------------------------------------------------------
# Archived Elections & Votes – Historical Record
# ---------------------------------------------------------------------------
class ArchivedElection(Base):
    __tablename__ = "archived_elections"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(128), unique=True, nullable=False)
    date_closed = Column(DateTime, default=datetime.datetime.utcnow)

    archived_votes = relationship("ArchivedVote", back_populates="election")

class ArchivedVote(Base):
    __tablename__ = "archived_votes"

    id              = Column(Integer, primary_key=True, index=True)
    election_id     = Column(Integer, ForeignKey("archived_elections.id"), nullable=False)
    constituency_id = Column(Integer, nullable=False)
    candidate_id    = Column(Integer, nullable=False)

    election = relationship("ArchivedElection", back_populates="archived_votes")

# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------
def create_tables():
    """Create all tables if they don't already exist."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency that yields a DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
