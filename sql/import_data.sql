-- ============================================
-- Formula 1 Dataset Import
-- ============================================

-- Dataset Source:
-- https://www.kaggle.com/datasets/rohanrao/formula-1-world-championship-1950-2020

-- Import Tool:
-- DBeaver CSV Import Wizard

-- Important CSV Settings:
-- Delimiter: ,
-- Header: enabled
-- NULL value mark: \N
-- Encoding: UTF-8

-- Import Order:
-- 1. circuits
-- 2. constructors
-- 3. drivers
-- 4. races
-- 5. results
-- 6. lap_times

-- Notes:
-- Some CSV fields contain \N values which are imported as NULL.
-- Tables were imported before applying foreign key constraints
-- to avoid dependency issues during bulk insertion.
