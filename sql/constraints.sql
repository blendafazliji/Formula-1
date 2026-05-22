-- ============================================
-- PRIMARY KEYS
-- ============================================

ALTER TABLE circuits ADD PRIMARY KEY (circuitId);
ALTER TABLE drivers ADD PRIMARY KEY (driverId);
ALTER TABLE constructors ADD PRIMARY KEY (constructorId);
ALTER TABLE races ADD PRIMARY KEY (raceId);
ALTER TABLE results ADD PRIMARY KEY (resultId);

-- ============================================
-- FOREIGN KEYS
-- ============================================

ALTER TABLE races
ADD CONSTRAINT fk_races_circuits
FOREIGN KEY (circuitId) REFERENCES circuits(circuitId);

ALTER TABLE results
ADD CONSTRAINT fk_results_races
FOREIGN KEY (raceId) REFERENCES races(raceId);

ALTER TABLE results
ADD CONSTRAINT fk_results_drivers
FOREIGN KEY (driverId) REFERENCES drivers(driverId);

ALTER TABLE results
ADD CONSTRAINT fk_results_constructors
FOREIGN KEY (constructorId) REFERENCES constructors(constructorId);

ALTER TABLE lap_times
ADD CONSTRAINT fk_lap_race
FOREIGN KEY (raceId) REFERENCES races(raceId);

ALTER TABLE lap_times
ADD CONSTRAINT fk_lap_driver
FOREIGN KEY (driverId) REFERENCES drivers(driverId);

-- ============================================
-- CHECK CONSTRAINT (REQUIRED)
-- ============================================

ALTER TABLE results
ADD CONSTRAINT chk_points
CHECK (points >= 0);
