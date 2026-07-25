"""Compatibility import for scheduler process supervision."""

import subprocess

from .runtime.supervisor import StrategySupervisor

# ``subprocess`` remains available because older tests and integrations patch
# ``grid_server.supervisor.subprocess.Popen``.  Both modules reference Python's
# same subprocess module object, so the compatibility patch still reaches the
# canonical runtime implementation.
__all__ = ["StrategySupervisor", "subprocess"]
