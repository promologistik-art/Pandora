import uuid
from datetime import datetime, date
from sqlalchemy import (
    Column, Integer, BigInteger, String, Boolean, Date, DateTime,
    ForeignKey, UniqueConstraint, DECIMAL, Text
)
from sqlalchemy.orm import relationship
from database.engine import Base


def generate_uuid():
    return str(uuid.uuid4())


class Client(Base):
    __tablename__ = "clients"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_id = Column(BigInteger, unique=True, nullable=False)
    username = Column(String(255))
    first_name = Column(String(255))
    phone = Column(String(20))
    source = Column(String(100))
    referrer_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    status = Column(String(20), default="active")  # active, banned
    created_at = Column(DateTime, default=datetime.utcnow)

    subscriptions = relationship("Subscription", back_populates="client", lazy="selectin")
    payments = relationship("Payment", back_populates="client", lazy="selectin")
    traffic = relationship("TrafficLog", back_populates="client", lazy="selectin")
    events = relationship("EventLog", back_populates="client", lazy="selectin")
    routers = relationship("Router", back_populates="client", lazy="selectin")
    referred_clients = relationship("Client", backref="referrer", remote_side=[id], lazy="selectin")


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    started_at = Column(Date, nullable=False, default=date.today)
    expires_at = Column(Date, nullable=False)
    status = Column(String(20), default="active")  # active, expired, cancelled, banned, cleaned
    plan = Column(String(20), default="1month")
    is_trial = Column(Boolean, default=False)
    xray_uuid = Column(String(64), default=generate_uuid)
    sub_link = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="subscriptions")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    amount = Column(DECIMAL(10, 2), nullable=False)
    method = Column(String(20), default="sbp")
    status = Column(String(20), default="pending")  # pending, confirmed, rejected
    phone_last4 = Column(String(4))
    confirmed_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="payments")


class TrafficLog(Base):
    __tablename__ = "traffic_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=False)
    date = Column(Date, nullable=False)
    upload_bytes = Column(BigInteger, default=0)
    download_bytes = Column(BigInteger, default=0)
    __table_args__ = (UniqueConstraint("client_id", "date"),)

    client = relationship("Client", back_populates="traffic")


class EventLog(Base):
    __tablename__ = "event_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    event_type = Column(String(50))
    description = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="events")


class GeoList(Base):
    __tablename__ = "geo_lists"

    id = Column(Integer, primary_key=True, autoincrement=True)
    telegram_file_id = Column(String(255), nullable=False)
    version = Column(Integer, nullable=False)
    size_bytes = Column(Integer)
    generated_at = Column(DateTime, default=datetime.utcnow)


class Router(Base):
    __tablename__ = "routers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    router_uid = Column(String(64), unique=True, nullable=False)
    client_id = Column(Integer, ForeignKey("clients.id"), nullable=True)
    agent_version = Column(String(20))
    xray_key_id = Column(Integer)
    last_heartbeat = Column(DateTime)
    status = Column(String(20), default="active")
    created_at = Column(DateTime, default=datetime.utcnow)

    client = relationship("Client", back_populates="routers")
    commands = relationship("RouterCommand", back_populates="router", lazy="selectin")


class RouterCommand(Base):
    __tablename__ = "router_commands"

    id = Column(Integer, primary_key=True, autoincrement=True)
    router_uid = Column(String(64), ForeignKey("routers.router_uid"), nullable=False)
    command = Column(String(50), nullable=False)
    status = Column(String(20), default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)

    router = relationship("Router", back_populates="commands")