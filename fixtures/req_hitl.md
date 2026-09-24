# HITL requirement (8.D step 8: approval halt, checkpoint persisted, resume).
# The approval gate halts the graph before its approval node; the driver resumes
# it with one command and asserts exactly one node advanced.
c1: Awaiting approval