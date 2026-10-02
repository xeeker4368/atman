"""Reflection: runs that read the record back and write about it.

Today this holds the journal (``journal.py``) and nothing else. Phase 6's scheduler,
when it exists, calls the same functions; nothing here assumes one does, and
**nothing here is a scheduler** (``tests/test_no_person_present.py`` keeps that
checkable).
"""
