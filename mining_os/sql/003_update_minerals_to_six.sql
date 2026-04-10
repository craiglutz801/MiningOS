-- Sync minerals of interest to: Tungsten, Scandium, Beryllium, Uranium, Fluorspar, Germanium
TRUNCATE minerals_of_interest;

INSERT INTO minerals_of_interest (name, sort_order)
VALUES
  ('Tungsten', 1),
  ('Scandium', 2),
  ('Beryllium', 3),
  ('Uranium', 4),
  ('Fluorspar', 5),
  ('Germanium', 6);
