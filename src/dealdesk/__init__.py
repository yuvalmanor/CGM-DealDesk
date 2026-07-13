"""CGM DealDesk — daily inbox triage pipeline.

Phase 1 ships the read half only: service-account auth, the work-queue query,
and a read-only Source-by-volume discovery command. No writes, labels, or AI.
"""

__version__ = "0.1.0"
