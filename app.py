"""Backwards-compatible alias for the function entry point.

The deployed function is ``main.py`` — ``deploy.sh`` runs
``gcloud functions deploy app --source=. --entry-point=app``, and the Python
runtime resolves that against ``main.py``. This module used to hold a second,
full copy of the router; the two drifted (``syncTasksForClient`` existed in one
and not the other), so it is now a single re-export.

Keep it: ``deploy.sh`` still checks that ``app.py`` exists before deploying, and
anything importing ``app:app`` keeps working.
"""

from main import app

__all__ = ["app"]
