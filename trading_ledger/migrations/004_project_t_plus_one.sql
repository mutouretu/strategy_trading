-- Existing projects keep their unrestricted same-day trading behavior.
ALTER TABLE projects ADD COLUMN t_plus_one INTEGER NOT NULL DEFAULT 0
    CHECK (t_plus_one IN (0, 1));
