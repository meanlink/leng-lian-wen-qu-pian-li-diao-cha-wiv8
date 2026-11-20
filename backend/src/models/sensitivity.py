from sqlalchemy import String, JSON, Double, ForeignKey, Boolean, Integer, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from src.config.database import Base
class SensitivityRun(Base):
    __tablename__ = 'sensitivity_runs'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    dataset_id: Mapped[int] = mapped_column(ForeignKey('datasets.id'))
    # 创建时锁定：批次指纹、上界起止值、步长与参数指纹，确认期间据此拒绝漂移
    dataset_checksum: Mapped[str] = mapped_column(String(64))
    lower: Mapped[float] = mapped_column(Double)
    upper: Mapped[float] = mapped_column(Double)
    step: Mapped[float] = mapped_column(Double)
    alpha: Mapped[float] = mapped_column(Double)
    params_fingerprint: Mapped[str] = mapped_column(String(64))
    # 创建时固化的不可变锁定快照；确认切换只与该快照比对，不依赖可变字段重算
    locked_params: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default='queued')
    error: Mapped[str] = mapped_column(String(1000), default='')
    version: Mapped[int] = mapped_column(Integer, default=1)
    switched: Mapped[bool] = mapped_column(Boolean, default=False)
    switched_at: Mapped[float | None] = mapped_column(Double, nullable=True)
    switched_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
    finished_at: Mapped[float | None] = mapped_column(Double, nullable=True)
class SensitivityTier(Base):
    __tablename__ = 'sensitivity_tiers'
    __table_args__ = (UniqueConstraint('run_id', 'upper', name='uq_tier_run_upper'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('sensitivity_runs.id'))
    upper: Mapped[float] = mapped_column(Double)
    ordinal: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default='pending')
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str] = mapped_column(String(1000), default='')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[float | None] = mapped_column(Double, nullable=True)
class SensitivityBrief(Base):
    __tablename__ = 'sensitivity_briefs'
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey('sensitivity_runs.id'))
    version: Mapped[int] = mapped_column(Integer)
    filename: Mapped[str] = mapped_column(String(160))
    checksum: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict] = mapped_column(JSON)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[float] = mapped_column(Double)
