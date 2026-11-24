import re
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, Column, Integer,String,Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

DB_PATH = DATA_DIR / "chatbot_memory.db"
DATABASE_URL = f"sqlite:///{DB_PATH.as_posix()}"

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(String,unique=True, index=True)
    title=Column(String,default="New Chat")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(String,index=True)
    role = Column(String)
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


class LongTermMemory(Base):
    __tablename__ = "long_term_memory"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(String,index=True)
    memory = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(bind=engine)




def create_or_update_conversation(thread_id: str, first_message: str | None = None):
    db = SessionLocal()
    try:
        conversation = (
        db.query(Conversation)
        .filter(Conversation.thread_id == thread_id)
        .first()
        )
        if not conversation:
            title = "New Chat"
            if first_message:
                title = first_message.strip()[:40]
                if len(first_message.strip()) > 40:
                    title += "..."

            conversation = Conversation(
                thread_id=thread_id,
                title=title,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.add(conversation)
        else:
            conversation.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()
        
def list_conversations():
    db = SessionLocal()
    try:
        return(
        db.query(Conversation)
        .order_by(Conversation.updated_at.desc())
        .all()
        )
    finally:
    
        db.close()


def save_chat_message(thread_id: str, role: str, content:str):
    db = SessionLocal()
    try:
        msg = ChatMessage(
            thread_id=thread_id,
            role=role,
            content=content,
            created_at=datetime.utcnow()
            )
        db.add(msg)
        conversation = (
            db.query(Conversation)
            .filter(Conversation.thread_id == thread_id)
            .first()
        )
        if conversation:
            conversation.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()


def get_chat_history(thread_id:str):
    db = SessionLocal()
    try:
        return(
            db.query(ChatMessage)
            .filter(ChatMessage.thread_id == thread_id)
            .order_by(ChatMessage.created_at.asc())
            .all()
        )
    finally:
        db.close()



def save_memory(thread_id: str,memory: str):

    db = SessionLocal()
    try:
        item = LongTermMemory(
        thread_id=thread_id,
        memory=memory,
        created_at=datetime.utcnow()
        )
        db.add(item)
        db.commit()
        return "Memory saved successfully."
    finally:
        db.close()


def search_memory(thread_id: str, query: str = ""):
    db = SessionLocal()
    try:
        memories = (
            db.query(LongTermMemory)
            .filter(LongTermMemory.thread_id == thread_id)
            .order_by(LongTermMemory.created_at.desc())
            .limit(50)
            .all()
        )
        if not memories:
            return "No saved memory found."

        keywords = [k for k in re.split(r"[\s,，。;；、]+", (query or "").strip()) if k]
        if keywords:
            scored = []
            for item in memories:
                text = (item.memory or "").lower()
                score = sum(1 for keyword in keywords if keyword.lower() in text)
                if score:
                    scored.append((score, item))
            if not scored:
                return "No relevant saved memory found."
            scored.sort(key=lambda pair: pair[0], reverse=True)
            memories = [item for _, item in scored[:10]]
        else:
            memories = memories[:10]

        return "\n".join([f"- {m.memory}" for m in memories])
    finally:
        db.close()


