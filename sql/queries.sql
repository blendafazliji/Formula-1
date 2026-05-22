#Top drivers by wins
SELECT d.forename, COUNT(*) AS wins
FROM results r
JOIN drivers d ON r.driverId = d.driverId
WHERE r.position = 1
GROUP BY d.forename
ORDER BY wins DESC;

# Top constructors
SELECT c.name, COUNT(*) AS wins
FROM results r
JOIN constructors c ON r.constructorId = c.constructorId
WHERE r.position = 1
GROUP BY c.name
ORDER BY wins DESC;

# Most races participated
SELECT d.forename, COUNT(*) AS races
FROM results r
JOIN drivers d ON r.driverId = d.driverId
GROUP BY d.forename
ORDER BY races DESC;

# Circuit with most races
SELECT ci.name, COUNT(*) AS races
FROM races ra
JOIN circuits ci ON ra.circuitId = ci.circuitId
GROUP BY ci.name
ORDER BY races DESC;

# Average finishing position per driver
SELECT d.forename, d.surname, AVG(r.position) AS avg_finish
FROM results r
JOIN drivers d ON r.driverId = d.driverId
WHERE r.position IS NOT NULL
GROUP BY d.forename, d.surname
ORDER BY avg_finish ASC
LIMIT 10;

# Total points per constructor
SELECT c.name, SUM(r.points) AS total_points
FROM results r
JOIN constructors c ON r.constructorId = c.constructorId
GROUP BY c.name
ORDER BY total_points DESC;

# Number of races per year
SELECT year, COUNT(*) AS total_races
FROM races
GROUP BY year
ORDER BY year;
