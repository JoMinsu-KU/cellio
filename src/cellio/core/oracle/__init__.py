"""Finite-trace physical/compliance oracle for online and offline evidence."""
from .engine import Oracle, reduce_category, task_outcome
from .presets import preset

__all__ = ["Oracle","preset","reduce_category","task_outcome"]
