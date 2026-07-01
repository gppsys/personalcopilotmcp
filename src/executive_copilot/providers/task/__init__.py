"""Task provider interfaces."""

from executive_copilot.providers.task.asana import AsanaProvider
from executive_copilot.providers.task.interface import (
    NewTask,
    ProjectInfo,
    SectionInfo,
    TaskProvider,
    TaskQuery,
)

__all__ = [
    "AsanaProvider",
    "NewTask",
    "ProjectInfo",
    "SectionInfo",
    "TaskProvider",
    "TaskQuery",
]
