ALTER TABLE projects
ADD COLUMN color_key TEXT NOT NULL DEFAULT 'blue'
CHECK (color_key IN ('blue', 'green', 'yellow', 'purple', 'orange', 'rose'));

UPDATE projects
SET color_key = CASE ((project_id - 1) % 6)
    WHEN 0 THEN 'blue'
    WHEN 1 THEN 'green'
    WHEN 2 THEN 'yellow'
    WHEN 3 THEN 'purple'
    WHEN 4 THEN 'orange'
    ELSE 'rose'
END;
