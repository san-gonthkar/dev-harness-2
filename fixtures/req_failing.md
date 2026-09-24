# Failing requirement (8.D step 7: bounded retry + HITL escalation).
# The driver's scripted client writes a permanently red suite, so the chunk can
# never pass and the retry loop must terminate in HITL (never spin forever).
c1: Broken