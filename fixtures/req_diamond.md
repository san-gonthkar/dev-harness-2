# Diamond requirement (8.D step 2 validation, step 3 parallel execution).
# Wave 1 has a single chunk; wave 2 has three independent chunks (peak == 3);
# wave 3 has one join chunk.
c1: Base
c2: Left deps: c1
c3: Middle deps: c1
c4: Right deps: c1
c5: Join deps: c2, c3, c4