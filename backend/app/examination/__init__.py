"""V2.3 Examination Core: versioned Methods, a controlled evidence reader, and durable execution.

``contracts``     what a Method is and may do (the single Method concept)
``registry``      the one authoritative, code-owned list of executable Methods
``methods``       the executable Methods themselves
``runner``        executes one Method against a bounded, integrity-checked evidence reader
``coordinator``   the only owner of Analysis Run state changes in the database
``supervisor``    the in-process worker loop that claims and executes queued runs

Examination never creates Findings, Claims or Assessments and never writes evidence.
"""
