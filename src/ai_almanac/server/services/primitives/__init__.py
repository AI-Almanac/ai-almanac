"""Typed, provenance-stamped capabilities the assistant can invoke in a workspace.

``registry`` holds the ``@primitive`` chokepoint; each other module is one section of
primitives, offered to the assistant as its own toolset. Importing the package
registers every section, one import line per section.
"""

from . import acquire as acquire
from . import code_harness as code_harness
from . import inspection as inspection
from . import registry as registry
from . import runs as runs
from . import workspace as workspace
from .registry import toolset as toolset
