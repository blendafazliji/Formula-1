CREATE TABLE circuits (
    circuitId INT PRIMARY KEY,
    circuitRef VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    country VARCHAR(50) NOT NULL,
    lat DOUBLE PRECISION,
    lng DOUBLE PRECISION
);

CREATE TABLE drivers (
    driverId INT PRIMARY KEY,
    driverRef VARCHAR(50) UNIQUE NOT NULL,
    forename VARCHAR(50) NOT NULL,
    surname VARCHAR(50) NOT NULL,
    nationality VARCHAR(50) NOT NULL,
    dob DATE
);

CREATE TABLE constructors (
    constructorId INT PRIMARY KEY,
    constructorRef VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(100) NOT NULL,
    nationality VARCHAR(50)
);

CREATE TABLE races (
    raceId INT PRIMARY KEY,
    year INT NOT NULL,
    round INT NOT NULL,
    circuitId INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    date VARCHAR(50),
    time VARCHAR(50),

    CONSTRAINT fk_races_circuits
        FOREIGN KEY (circuitId)
        REFERENCES circuits(circuitId)
        ON DELETE CASCADE
);

CREATE TABLE results (
    resultId SERIAL PRIMARY KEY,
    raceId INT NOT NULL,
    driverId INT NOT NULL,
    constructorId INT NOT NULL,

    grid INT,
    position INT,
    points FLOAT DEFAULT 0,

    CONSTRAINT fk_results_races
        FOREIGN KEY (raceId)
        REFERENCES races(raceId)
        ON DELETE CASCADE,

    CONSTRAINT fk_results_drivers
        FOREIGN KEY (driverId)
        REFERENCES drivers(driverId)
        ON DELETE CASCADE,

    CONSTRAINT fk_results_constructors
        FOREIGN KEY (constructorId)
        REFERENCES constructors(constructorId)
        ON DELETE CASCADE,

    CONSTRAINT chk_points
        CHECK (points >= 0)
);

CREATE TABLE lap_times (
    raceId INT NOT NULL,
    driverId INT NOT NULL,
    lap INT NOT NULL,
    position INT,
    milliseconds INT,

    PRIMARY KEY (raceId, driverId, lap),

    CONSTRAINT fk_lap_race
        FOREIGN KEY (raceId)
        REFERENCES races(raceId)
        ON DELETE CASCADE,

    CONSTRAINT fk_lap_driver
        FOREIGN KEY (driverId)
        REFERENCES drivers(driverId)
        ON DELETE CASCADE
);
