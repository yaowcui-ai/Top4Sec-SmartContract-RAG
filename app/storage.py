"""Separate SQLAlchemy tables; no access to the original DocTrace database."""
from datetime import datetime,timezone
from uuid import uuid4
from sqlalchemy import String,Text,JSON,DateTime,ForeignKey,UniqueConstraint,Integer,Boolean
from sqlalchemy.orm import DeclarativeBase,Mapped,mapped_column
from pwdlib import PasswordHash

password_hasher=PasswordHash.recommended()
def utcnow():return datetime.now(timezone.utc).replace(tzinfo=None)

class Base(DeclarativeBase):pass

class User(Base):
    __tablename__='paper_users'
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid4()))
    username:Mapped[str]=mapped_column(String(32),unique=True,nullable=False)
    password_hash:Mapped[str]=mapped_column(String(255),nullable=False)
    created_at:Mapped[datetime]=mapped_column(DateTime,default=utcnow)

class SessionToken(Base):
    __tablename__='paper_sessions'
    token_hash:Mapped[str]=mapped_column(String(64),primary_key=True)
    user_id:Mapped[str]=mapped_column(ForeignKey('paper_users.id'),index=True)
    expires_at:Mapped[datetime]=mapped_column(DateTime)

class QARecord(Base):
    __tablename__='paper_qa_records'
    __table_args__=(UniqueConstraint('user_id','idempotency_key',name='uq_paper_request_owner_key'),)
    id:Mapped[str]=mapped_column(String(36),primary_key=True,default=lambda:str(uuid4()))
    user_id:Mapped[str]=mapped_column(ForeignKey('paper_users.id'),index=True)
    idempotency_key:Mapped[str]=mapped_column(String(36),nullable=False)
    fingerprint:Mapped[str]=mapped_column(String(64),nullable=False)
    question:Mapped[str]=mapped_column(Text,nullable=False)
    mode:Mapped[str]=mapped_column(String(8),nullable=False)
    venue:Mapped[str|None]=mapped_column(String(12),nullable=True)
    year:Mapped[int|None]=mapped_column(Integer,nullable=True)
    status:Mapped[str]=mapped_column(String(16),default='processing')
    answer:Mapped[str|None]=mapped_column(Text,nullable=True)
    references:Mapped[list|None]=mapped_column(JSON,nullable=True)
    retrieval:Mapped[dict|None]=mapped_column(JSON,nullable=True)
    llm_generated:Mapped[bool|None]=mapped_column(Boolean,nullable=True)
    error_stage:Mapped[str|None]=mapped_column(String(32),nullable=True)
    error_kind:Mapped[str|None]=mapped_column(String(32),nullable=True)
    http_status:Mapped[int|None]=mapped_column(Integer,nullable=True)
    duration_ms:Mapped[int|None]=mapped_column(Integer,nullable=True)
    created_at:Mapped[datetime]=mapped_column(DateTime,default=utcnow,index=True)
    updated_at:Mapped[datetime]=mapped_column(DateTime,default=utcnow,onupdate=utcnow)

def serialize(record):
    return {'request_id':record.id,'question':record.question,'mode':record.mode,'venue':record.venue,'year':record.year,
        'status':record.status,'answer':record.answer,'references':record.references or [],'retrieval':record.retrieval,
        'llm_generated':record.llm_generated,'error_stage':record.error_stage,'error_kind':record.error_kind,
        'http_status':record.http_status,'duration_ms':record.duration_ms,'created_at':record.created_at.isoformat()+'Z'}
