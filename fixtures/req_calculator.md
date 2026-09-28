# Calculator requirement (blackbox E2E demo).
# One chunk per non-blank, non-# line. A line may be "<id>: <title>".
# This is the requirement the harness is asked to build end to end.

calc-core: Implement a calculator module with add, subtract, multiply and divide functions.
calc-errors: Raise ValueError on divide by zero and on a non-numeric operand.
calc-tests: Add pytest tests covering every operation and both error cases.
